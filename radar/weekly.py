"""Weekly step after collection: judge new candidates, keep a per-team wiki, write this week's report."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import requests

from radar import config, judge
from radar.judge import (PARAMS, build_messages, call_once, call_with_deadline, fetch_evidence, make_provider,
                         parse_output, slug)

FETCH_DEADLINE_SECONDS = 45
ITEM_RESERVE_SECONDS = 180  # fetch 45 + call 120 + bookkeeping 15
MESSAGE_OVERHEAD_TOKENS = 100
CIRCUIT_FAILURES = 3
DIRECTION_TITLE = {"brain": "大脑", "data_sim": "数据 / 仿真"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def reserve_tokens(request: dict) -> int:
    """Upper bound for one call: byte-level BPE gives at most one input token per UTF-8 byte."""
    return len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + MESSAGE_OVERHEAD_TOKENS + PARAMS["max_tokens"]


class Gate:
    """Hard limits checked before every fetch and every model call."""

    def __init__(self, settings: dict, clock):
        self.max_requests = int(settings.get("max_requests", 100))
        self.max_tokens = int(settings.get("max_tokens", 300_000))
        self.max_seconds = float(settings.get("max_seconds", 600))
        self.clock, self.start = clock, clock()
        self.requests = self.tokens = self.consecutive_failures = 0

    def remaining(self) -> float:
        return self.max_seconds - (self.clock() - self.start)

    def before_item(self) -> str | None:
        if self.consecutive_failures >= CIRCUIT_FAILURES:
            return "circuit_open"
        if self.requests >= self.max_requests:
            return "max_requests"
        if self.remaining() < ITEM_RESERVE_SECONDS:
            return "max_seconds"
        return None

    def before_call(self, reserve: int) -> str | None:
        if self.consecutive_failures >= CIRCUIT_FAILURES:
            return "circuit_open"
        if self.requests >= self.max_requests:
            return "max_requests"
        if self.tokens + reserve > self.max_tokens:
            return "max_tokens"
        if self.remaining() < judge.CALL_DEADLINE_SECONDS + 15:
            return "max_seconds"
        return None


def judge_item(item: dict, gate: Gate, ctx: dict) -> dict:
    """One candidate: fetch its text, then call the model (one retry on HTTP errors) inside the gate."""
    key, name = item["key"], slug(item["key"])
    row = {"key": key, "url": item["url"]}
    if reason := gate.before_item():
        return row | {"status": "deferred", "reason": reason}
    try:
        meta = call_with_deadline(lambda: ctx["fetch"](item, ctx["evidence_dir"]), FETCH_DEADLINE_SECONDS)
    except requests.Timeout:
        return row | {"status": "error", "error": "fetch_timeout", "attempts": []}
    text = (ctx["evidence_dir"] / meta["text_file"]).read_text(encoding="utf-8")
    row["page_title"] = meta.get("title")
    messages = build_messages(item, text, meta["error"], ctx["scope"], ctx["entities"])
    request = {"model": ctx["model"], **PARAMS, "messages": messages}
    body = json.dumps(request, ensure_ascii=False, indent=1)
    (ctx["calls_dir"] / f"{name}.request.json").write_text(body, encoding="utf-8")
    row["request_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    reserve = reserve_tokens(request)

    attempts, content, error = [], None, None
    while True:
        if reason := gate.before_call(reserve):
            if not attempts:
                return row | {"status": "deferred", "reason": reason}
            break
        gate.requests += 1
        content, raw, error = call_once(ctx["provider"], messages, ctx["model"])
        usage = (raw or {}).get("usage")
        tokens = usage["prompt_tokens"] + usage["completion_tokens"] if usage else reserve
        gate.tokens += tokens
        attempts.append({"tokens": tokens, "estimated": usage is None, "error": error,
                         "model": (raw or {}).get("model")})
        if raw is not None:
            (ctx["calls_dir"] / f"{name}.response.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        if error is None:
            gate.consecutive_failures = 0
            break
        gate.consecutive_failures += 1
        if not (error.startswith("http:") and len(attempts) < 2):
            break
    row["attempts"] = attempts
    if error is not None:
        return row | {"status": "error", "error": error}
    try:
        return row | {"status": "judged", **parse_output(content)}
    except (ValueError, json.JSONDecodeError) as exc:
        return row | {"status": "error", "error": f"parse:{exc}"}


def entry_for(item: dict, row: dict, entities: dict, run_id: str, run_date: str) -> dict:
    source = item.get("sources", [{}])[0]
    team, pending = row.get("team"), False
    if team not in entities:
        team, pending = source.get("entity"), True
    date, date_source = item.get("published"), "published"
    if not date:
        date, date_source = item.get("page_date"), "page_date"
    if not date:
        date_source = None
    title = item.get("title") or row.get("page_title") or item["url"].rstrip("/").rsplit("/", 1)[-1]
    return {"key": item["key"], "url": item["url"], "title": title, "date": date, "date_source": date_source,
            "team": team, "team_pending": pending, "direction": entities.get(team, {}).get("direction"),
            "channel": source.get("type"), "reason": row["reason"], "quote": row["evidence_quote"],
            "first_run": run_id, "run_date": run_date}


def entry_line(e: dict, entities: dict) -> str:
    team = entities.get(e["team"], {}).get("name", e["team"]) + ("（团队待核）" if e["team_pending"] else "")
    return f"- {e['date'] or '日期未知'} · [{e['title']}]({e['url']}) — {team} · {e['channel']}\n  > {e['reason']}"


def by_date(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (e["date"] or "", e["key"]), reverse=True)


def render_wiki(entries: list[dict], entities: dict, wiki_dir: Path, title: str) -> None:
    teams = {}
    for e in entries:
        teams.setdefault(e["team"], []).append(e)
    rows = []
    for team_id in sorted(teams):
        team = entities.get(team_id, {"name": team_id, "direction": None})
        page = [f"# {team['name']}", "", f"方向：{DIRECTION_TITLE.get(team['direction'], '未知')}", ""]
        for e in by_date(teams[team_id]):
            page.append(entry_line(e, entities) + f"\n  收录于 {e['run_date']}（运行 {e['first_run']}）")
        (wiki_dir / "teams").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "teams" / f"{team_id}.md").write_text("\n".join(page) + "\n", encoding="utf-8")
        latest = by_date(teams[team_id])[0]["date"] or "日期未知"
        rows.append(f"| [{team['name']}](teams/{team_id}.md) | {DIRECTION_TITLE.get(team['direction'], '未知')} "
                    f"| {len(teams[team_id])} | {latest} |")
    index = [f"# {title} · wiki", "", f"共 {len(entries)} 条。", "", "| 团队 | 方向 | 条目 | 最近 |", "|---|---|---|---|", *rows]
    (wiki_dir / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")


def render_report(rows: list[dict], new_entries: list[dict], items: dict, summary: dict, entities: dict,
                  title: str) -> str:
    judged = [r for r in rows if r["status"] == "judged"]
    count = lambda status=None, decision=None: sum(
        (status is None or r["status"] == status) and (decision is None or r.get("decision") == decision) for r in rows)
    totals = summary["totals"]
    lines = [f"# {title} · 本周变化（{summary['run_date']}，窗口 {summary['window']['start']} 至 {summary['window']['end']}）", "",
             f"候选 {len(rows)} 条：本次判定 {len(judged)}（保留 {count('judged', 'keep')} · 排除 {count('judged', 'reject')} "
             f"· 待人工 {count('judged', 'needs_review')} · 失败 {count('error')}），已判定过跳过 {count('skipped')}，"
             f"暂缓 {count('deferred')}。采集渠道失败 {totals['failed']} / {totals['channels']}。"]
    for direction, heading in DIRECTION_TITLE.items():
        lines += ["", f"## {heading}"]
        lines += [entry_line(e, entities) for e in by_date([e for e in new_entries if e["direction"] == direction])] or ["（无）"]
    sections = [("待人工确认", [r for r in judged if r["decision"] == "needs_review"], lambda r: r.get("reason", "")),
                ("判定失败（下次运行重试）", [r for r in rows if r["status"] == "error"], lambda r: r["error"]),
                ("暂缓（下次运行先判）", [r for r in rows if r["status"] == "deferred"], lambda r: r["reason"])]
    for heading, picked, why in sections:
        lines += ["", f"## {heading}"]
        lines += [f"- [{items[r['key']].get('title') or r['key']}]({r['url']}) — {why(r)}" for r in picked] or ["（无）"]
    return "\n".join(lines) + "\n"


def update_readme(readme: Path, domain: str, new_entries: list[dict], summary: dict, entities: dict,
                  report: Path, wiki_dir: Path) -> None:
    start, end = f"<!-- latest:{domain} -->", f"<!-- /latest:{domain} -->"
    head, found_start, rest = readme.read_text(encoding="utf-8").partition(start)
    _, found_end, tail = rest.partition(end)
    if not (found_start and found_end):
        raise ValueError(f"{readme} has no {start} ... {end} block")
    link = lambda p: os.path.relpath(p, readme.parent).replace(os.sep, "/")
    lines = [f"{summary['run_date']}（窗口 {summary['window']['start']} 至 {summary['window']['end']}）新增 "
             f"{len(new_entries)} 条 · [完整周报]({link(report)}) · [全部条目]({link(wiki_dir / 'index.md')})"]
    for direction, heading in DIRECTION_TITLE.items():
        lines += ["", f"### {heading}"]
        lines += [entry_line(e, entities) for e in by_date([e for e in new_entries if e["direction"] == direction])] or ["（无新增）"]
    readme.write_text(f"{head}{start}\n" + "\n".join(lines) + f"\n{end}{tail}", encoding="utf-8")


def run(domain_dir: Path, out_dir: Path, state_dir: Path, wiki_dir: Path, reports_dir: Path, run_id: str,
        provider, model: str, fetch=fetch_evidence, clock=time.monotonic, readme: Path | None = None) -> dict:
    domain = config.load(domain_dir)
    entities = {e["id"]: e for e in domain.entities}
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    queue_path, judged_path = state_dir / "judge_queue.jsonl", state_dir / "judged.json"
    judged = json.loads(judged_path.read_text(encoding="utf-8")) if judged_path.exists() else {}
    candidates = {}
    for item in read_jsonl(queue_path) + read_jsonl(out_dir / "items.jsonl"):
        candidates.setdefault(item["key"], item)

    ctx = {"fetch": fetch, "evidence_dir": out_dir / "evidence", "calls_dir": out_dir / "judge" / "calls",
           "scope": domain.raw["scope"], "entities": domain.entities, "provider": provider, "model": model}
    ctx["calls_dir"].mkdir(parents=True, exist_ok=True)
    gate = Gate(domain.raw.get("judge", {}), clock)
    rows = []
    for key, item in candidates.items():
        if key in judged:
            rows.append({"key": key, "url": item["url"], "status": "skipped", "decision": judged[key]["decision"]})
        else:
            rows.append(judge_item(item, gate, ctx))

    run_date = summary["run_date"]
    entries_path = wiki_dir / "entries.jsonl"
    entries = read_jsonl(entries_path)
    known = {e["key"] for e in entries}
    new_entries = []
    for r in rows:
        if r["status"] == "judged":
            judged[r["key"]] = {"decision": r["decision"], "run": run_id, "run_date": run_date}
            if r["decision"] == "keep" and r["key"] not in known:
                new_entries.append(entry_for(candidates[r["key"]], r, entities, run_id, run_date))
    write_jsonl(entries_path, entries + new_entries)
    render_wiki(entries + new_entries, entities, wiki_dir, domain.raw.get("title", domain.name))

    state_dir.mkdir(parents=True, exist_ok=True)
    judged_path.write_text(json.dumps(judged, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    write_jsonl(queue_path, [candidates[r["key"]] for r in rows if r["status"] in ("error", "deferred")])
    write_jsonl(out_dir / "judged.jsonl", rows)
    report = render_report(rows, new_entries, candidates, summary, entities, domain.raw.get("title", domain.name))
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{run_date}-{run_id}.md"
    report_path.write_text(report, encoding="utf-8")
    if readme:
        update_readme(readme, domain.name, new_entries, summary, entities, report_path, wiki_dir)
    return {"rows": rows, "requests": gate.requests, "tokens": gate.tokens, "new_entries": len(new_entries)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge this week's candidates, update the wiki, write the report")
    parser.add_argument("--domain", default="embodied")
    args = parser.parse_args()
    provider, model = make_provider()
    d = args.domain
    result = run(Path(f"domains/{d}"), Path(f"out/{d}/latest"), Path(f"state/{d}"), Path(f"wiki/{d}"),
                 Path(f"reports/{d}"), os.environ.get("GITHUB_RUN_ID", "local"), provider, model, readme=Path("README.md"))
    statuses = {}
    for r in result["rows"]:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    print(json.dumps({"statuses": statuses, "requests": result["requests"], "tokens": result["tokens"],
                      "new_entries": result["new_entries"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
