"""Locked verifiers for radar tasks. Exit 0 iff the acceptance criteria in the plan hold."""

import hashlib
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

BOT = "github-actions[bot]"


def check_page_states(report: dict) -> list[str]:
    """Each page state file: seen and pending are disjoint, cover the current links, and match the summary."""
    problems = []
    for eid, e in report["entities"].items():
        for r in e["channels"]:
            if r["type"] != "page" or r["status"] == "failed":
                continue
            if not r.get("snapshot") or not Path(r["snapshot"]).exists():
                problems.append(f"{eid}: page {r['target']} wrote no snapshot ({r.get('snapshot')})")
                continue
            state = json.loads(Path(r["snapshot"]).read_text(encoding="utf-8"))
            seen, pending = set(state["seen"]), set(state["pending"])
            if seen & pending:
                problems.append(f"{eid}: {len(seen & pending)} links are both seen and pending")
            if missing := set(r["links"]) - seen - pending:
                problems.append(f"{eid}: {len(missing)} current links in neither seen nor pending")
            if len(pending) != r["pending"]:
                problems.append(f"{eid}: state has {len(pending)} pending, summary says {r['pending']}")
    return problems


def check_channels(report: dict, run_meta: dict | None, gold_path: str = "evals/embodied/gold.yaml") -> list[str]:
    problems = []
    run_date = date.fromisoformat(report["run_date"])
    start = run_date - timedelta(days=7)
    gold = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8"))
    if not any(start <= g["date"] <= run_date for g in gold):
        problems.append(f"no gold item falls in [{start}, {run_date}]")

    if not run_meta or run_meta.get("conclusion") != "success":
        problems.append(f"workflow (incl. pytest) did not succeed: {run_meta and run_meta.get('conclusion')}")

    rows = [(eid, r) for eid, e in report["entities"].items() for r in e["channels"]]
    bad = [f"{eid}:{r['type']}:{r['target']}:{r['status']}:{r['error']}" for eid, r in rows
           if r["status"] in ("failed", "broken")]
    if len(bad) > 0.1 * len(rows):
        problems.append(f"failed+broken {len(bad)}/{len(rows)} > 10%: {bad}")
    for eid, e in report["entities"].items():
        if not any(r["status"] in ("no_update", "updated") for r in e["channels"]):
            problems.append(f"{eid}: no channel returned data")

    fetches = report["page_date_fetches"]
    if fetches["used"] > fetches["budget"]:
        problems.append(f"page date fetches {fetches['used']} > budget {fetches['budget']}")

    problems += check_page_states(report)
    for eid, r in rows:
        if r["type"] == "page" and r["status"] != "failed" and r.get("first_run"):
            for item in r["new"]:
                d = item.get("page_date")
                if not d or not start <= date.fromisoformat(d[:10]) <= run_date:
                    problems.append(f"{eid}: first-run page item {item['url']} has page date {d} outside window")

    for g in report["gold"]:
        if not g["hit"]:
            problems.append(f"{g['id']}: not among new items")
        elif g.get("expect") and [g["expect"]["entity"], g["expect"]["type"]] not in [list(v) for v in g["via"]]:
            problems.append(f"{g['id']}: hit via {g['via']}, expected {g['expect']}")
    return problems


def _rows(report: dict) -> dict:
    return {(eid, r["type"], r["target"]): r for eid, e in report["entities"].items() for r in e["channels"]}


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def check_weekly(latest: dict, prev: dict, latest_meta: dict | None, prev_meta: dict | None,
                 prev_dir: Path, domain: str = "embodied") -> list[str]:
    """Second of two consecutive runs: state was committed by the first run and read back by the second."""
    problems = check_channels(latest, latest_meta)
    if not prev_meta or prev_meta.get("conclusion") != "success":
        return problems + [f"first run did not succeed: {prev_meta and prev_meta.get('conclusion')}"]
    if latest_meta["headSha"] == prev_meta["headSha"]:
        problems.append("both runs used the same commit; the state commit is missing")
    else:
        out = subprocess.run(["git", "show", "--name-only", "--format=%an", latest_meta["headSha"]],
                             capture_output=True, text=True, encoding="utf-8")
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        if out.returncode or not lines or lines[0] != BOT:
            problems.append(f"second run's head {latest_meta['headSha'][:8]} is not a {BOT} commit: {lines[:1]}")
        elif stray := [f for f in lines[1:] if not f.startswith((f"state/{domain}/", f"history/{domain}/"))]:
            problems.append(f"state commit touches other files: {stray}")

    history = Path(f"history/{domain}/{prev['run_date']}-{prev_meta['databaseId']}/items.jsonl")
    if not history.exists():
        problems.append(f"{history} missing")
    elif _lines(history) != _lines(prev_dir / "items.jsonl"):
        problems.append(f"{history} differs from the first run's artifact items.jsonl")

    now = _rows(latest)
    for key, r in _rows(prev).items():
        if r["type"] in ("rss", "page") and r["status"] != "failed" and now.get(key, {}).get("first_run", True):
            problems.append(f"{key}: still a first run in the second run (state not read back)")

    budget_left = latest["page_date_fetches"]["used"] < latest["page_date_fetches"]["budget"]
    for key, r in _rows(prev).items():
        if r["type"] != "page" or r["status"] == "failed":
            continue
        before = json.loads((prev_dir / "state" / Path(r["snapshot"]).relative_to(f"state/{domain}")).read_text(encoding="utf-8"))
        cur = now[key]
        after = json.loads(Path(cur["snapshot"]).read_text(encoding="utf-8"))
        seen, pending = set(after["seen"]), set(after["pending"])
        if lost := set(before["pending"]) - seen - pending:
            problems.append(f"{key}: {len(lost)} pending keys from the first run were dropped")
        if budget_left and (waiting := set(before["pending"]) & pending):
            problems.append(f"{key}: {len(waiting)} first-run pending keys not checked although budget was left")
        for item in cur["new"]:
            if not ((item.get("late") and item["key"] in before["pending"]) or item["key"] not in r["links"]):
                problems.append(f"{key}: {item['key']} reported new again")

    srl = [k for k in now if k[0] == "nvidia-srl" and k[1] == "rss"]
    for k in srl:
        if now[k]["status"] == "failed" or _rows(prev).get(k, {}).get("status", "failed") == "failed":
            problems.append(f"{k}: failed in one of the runs")
        elif now[k]["new"]:
            problems.append(f"{k}: {len(now[k]['new'])} new items on the same day")
    if not srl:
        problems.append("nvidia-srl has no rss channel")
    return problems


# Quotes are compared without markup the model may drop: HTML tags, markdown emphasis, link-annotation residue.
_MARKUP = re.compile(r"</?[A-Za-z][^<>\n]*>|\(opens in a new window\)|[*_`]")


def _plain(text: str) -> str:
    return " ".join(_MARKUP.sub("", text).split())


def check_judge(summary_path: Path, judge_dir: Path = Path("evals/embodied/judge"),
                domain_dir: Path = Path("domains/embodied")) -> tuple[list[str], dict]:
    """Every frozen item judged once, from exactly the designed request, matching its frozen label."""
    from radar import config
    from radar.judge import build_messages, slug

    problems = []
    out_dir = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    items_path = judge_dir / "items.jsonl"
    items = {json.loads(l)["key"]: json.loads(l) for l in items_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    labels = {json.loads(l)["key"]: json.loads(l) for l in (judge_dir / "labels.jsonl").read_text(encoding="utf-8").splitlines()}
    domain = config.load(domain_dir)

    if summary["inputs"]["items_sha256"] != hashlib.sha256(items_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest():
        problems.append("summary was produced from a different items.jsonl")
    keys = [r["key"] for r in summary["results"]]
    if sorted(keys) != sorted(items) or len(keys) != len(set(keys)):
        problems.append(f"results cover {len(set(keys))} keys ({len(keys)} rows), items have {len(items)}")

    per_set = {}
    for r in summary["results"]:
        key, label = r["key"], labels.get(r["key"])
        if r["error"]:
            problems.append(f"{key}: error {r['error']}")
            continue
        for f in ("request_file", "response_file"):
            if not r.get(f) or not (out_dir / r[f]).exists():
                problems.append(f"{key}: {f} missing")
        request = json.loads((out_dir / r["request_file"]).read_text(encoding="utf-8"))
        meta = json.loads((judge_dir / "evidence" / f"{slug(key)}.json").read_text(encoding="utf-8"))
        text = (judge_dir / "evidence" / meta["text_file"]).read_text(encoding="utf-8")
        if request["messages"] != build_messages(items[key], text, meta["error"], domain.raw["scope"], domain.entities):
            problems.append(f"{key}: request differs from the designed template on the frozen inputs")
        if r["decision"] != label["label"]:
            problems.append(f"{key}: decided {r['decision']}, label {label['label']} ({r['reason']})")
        quote = _plain(r["evidence_quote"] or "")
        if (quote or r["decision"] != "needs_review") and quote not in _plain(request["messages"][1]["content"]):
            problems.append(f"{key}: evidence_quote not found in the request: {r['evidence_quote']!r}")
        s = per_set.setdefault(label["set"], [0, 0])
        s[0] += r["decision"] == label["label"]
        s[1] += 1
    return problems, per_set


REPORT_PREFIXES = tuple(f"{d}/embodied/" for d in ("state", "history", "wiki", "reports"))
G1_KEY = "deepmind.google/blog/gemini-4-argon-for-robotics"


def check_report(summary: dict, run_meta: dict | None, domain: str = "embodied") -> list[str]:
    """One weekly run: judged rows reconcile with candidates, keeps reach the report and the wiki once, within budget."""
    problems = []
    if not run_meta or run_meta.get("conclusion") != "success":
        return [f"workflow did not succeed: {run_meta and run_meta.get('conclusion')}"]
    run_dir = Path(f"history/{domain}/{summary['run_date']}-{run_meta['databaseId']}")
    out = subprocess.run(["git", "log", "-1", "--format=%H%n%an", "--name-only", "--", run_dir.as_posix()],
                         capture_output=True, text=True, encoding="utf-8")
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    if len(lines) < 2 or lines[1] != BOT:
        return problems + [f"no {BOT} commit adds {run_dir}: {lines[:2]}"]
    if stray := [f for f in lines[2:] if not f.startswith(REPORT_PREFIXES)]:
        problems.append(f"bot commit {lines[0][:8]} touches other files: {stray}")

    rows = read_rows(run_dir / "judged.jsonl")
    keys = [r["key"] for r in rows]
    items = {r["key"] for r in read_rows(run_dir / "items.jsonl")}
    if len(keys) != len(set(keys)):
        problems.append("judged.jsonl has duplicate keys")
    if missing := items - set(keys):
        problems.append(f"{len(missing)} candidates have no judged row")
    tried = [r for r in rows if r["status"] in ("judged", "error")]
    if sum(r["status"] == "error" for r in rows) > 0.1 * max(len(tried), 1):
        problems.append(f"errors {sum(r['status'] == 'error' for r in rows)} > 10% of {len(tried)} tried")
    if bad := [r["key"] for r in rows if r["status"] == "deferred" and not r.get("reason")]:
        problems.append(f"deferred rows without a reason: {bad}")
    attempts = [a for r in rows for a in r.get("attempts", [])]
    if len(attempts) > 100 or sum(a["tokens"] for a in attempts) > 300_000:
        problems.append(f"budget exceeded: {len(attempts)} requests, {sum(a['tokens'] for a in attempts)} tokens")

    report = (Path(f"reports/{domain}") / f"{run_dir.name}.md").read_text(encoding="utf-8")
    entries = read_rows(Path(f"wiki/{domain}/entries.jsonl"))
    entry_keys = [e["key"] for e in entries]
    if len(entry_keys) != len(set(entry_keys)):
        problems.append("wiki entries.jsonl has duplicate keys")
    for r in rows:
        if r["status"] == "judged" and r["decision"] == "keep":
            if r["url"] not in report:
                problems.append(f"{r['key']}: keep missing from the report")
            if entry_keys.count(r["key"]) != 1:
                problems.append(f"{r['key']}: {entry_keys.count(r['key'])} wiki entries")
    g1 = next((r for r in rows if r["key"] == G1_KEY), None)
    brain = report.split("## 大脑", 1)[-1].split("\n## ", 1)[0]
    g1_entry = next((e for e in entries if e["key"] == G1_KEY), None)
    if not g1 or g1.get("decision") != "keep" or g1["url"] not in brain:
        problems.append(f"G1 not reported under 大脑: {g1 and (g1['status'], g1.get('decision'))}")
    elif not g1_entry or g1_entry["team"] != "deepmind-robotics":
        problems.append(f"G1 wiki team is {g1_entry and g1_entry['team']}")
    return problems


def read_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main(kind: str, path: str, prev_path: str | None = None) -> int:
    if kind == "judge":
        problems, per_set = check_judge(Path(path))
        s = json.loads(Path(path).read_text(encoding="utf-8"))
        print(f"models {s['models_reported']} usage {s['usage']} retries {s['http_retries_used']} errors {s['errors']}")
        print("correct / judged per set: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(per_set.items())))
        for line in problems:
            print(f"FAIL {line}")
        print("PASS" if not problems else "FAIL")
        return 1 if problems else 0
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = _load(Path(path).with_name("run.json"))
    if kind == "channels":
        problems = check_channels(report, meta)
    elif kind == "report":
        problems = check_report(report, meta)
    elif kind == "weekly":
        prev_dir = Path(prev_path).parent
        problems = check_weekly(report, json.loads(Path(prev_path).read_text(encoding="utf-8")), meta,
                                _load(prev_dir / "run.json"), prev_dir)
    else:
        raise SystemExit(f"unknown check {kind!r}")
    print(f"run {report['run_date']} totals {report['totals']} by_type {report['by_type']} gold {report['gold']}")
    for line in problems:
        print(f"FAIL {line}")
    print("PASS" if not problems else "FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
