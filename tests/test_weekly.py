import json
import shutil
import time
from pathlib import Path

import pytest

from radar import judge, weekly
from radar.judge import PARAMS, slug
from radar.urls import normalize

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = ROOT / "domains" / "embodied"
RUN1 = ROOT / "history" / "embodied" / "2026-10-15-38665194391"
FROZEN = ROOT / "evals" / "embodied" / "judge"
G1 = "deepmind.google/blog/gemini-4-argon-for-robotics"
KEEPS = {G1, "github.com/facebookresearch/robo_jepa", "github.com/amazon-far/imprint", "pi.website/blog/api-beta",
         "huggingface.co/datasets/lerobot/FineART"}


def rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class Provider:
    """Answers by the candidate URL in the request; state is shared by the per-call copies."""

    def __init__(self, answers: dict, hang: float = 0, usage=None):
        self.answers, self.hang, self.usage, self.log, self.last_response = answers, hang, usage, [], None

    def chat(self, messages, model=None, **kwargs):
        self.log.append(1)
        if self.hang:
            time.sleep(self.hang)
        url = next(l[5:] for l in messages[1]["content"].splitlines() if l.startswith("url: "))
        a = self.answers[normalize(url)]
        content = json.dumps({"decision": a["decision"], "reason": a.get("reason", "r"),
                              "evidence_quote": a.get("quote", ""), "team": a.get("team")}, ensure_ascii=False)
        tokens = self.usage(messages) if self.usage else 100
        self.last_response = {"model": "fake", "usage": {"prompt_tokens": tokens - 10, "completion_tokens": 10}}
        return content


def frozen_fetch(item, evidence_dir):
    evidence_dir.mkdir(parents=True, exist_ok=True)
    name = slug(item["key"])
    src = FROZEN / "evidence"
    if (src / f"{name}.json").exists():
        for ext in ("json", "txt"):
            shutil.copy(src / f"{name}.{ext}", evidence_dir / f"{name}.{ext}")
        return json.loads((src / f"{name}.json").read_text(encoding="utf-8"))
    (evidence_dir / f"{name}.txt").write_text(f"page text for {item['key']}", encoding="utf-8")
    return {"text_file": f"{name}.txt", "error": None, "title": None}


def label_answers() -> dict:
    items = {r["key"]: r for r in rows(RUN1 / "items.jsonl")}
    out = {}
    for l in rows(FROZEN / "labels.jsonl"):
        team = items[l["key"]]["sources"][0]["entity"] if l["key"] in items and l["label"] == "keep" else None
        out[l["key"]] = {"decision": l["label"], "reason": l["clause"], "quote": l["quote"], "team": team}
    return out


class Week:
    """One weekly run in a scratch repo layout."""

    def __init__(self, base: Path):
        self.base = base
        self.state, self.wiki, self.reports = base / "state", base / "wiki", base / "reports"

    def run(self, run_id, items, summary, provider, fetch=frozen_fetch, clock=time.monotonic):
        out = self.base / f"out-{run_id}"
        out.mkdir(parents=True)
        (out / "items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        result = weekly.run(DOMAIN, out, self.state, self.wiki, self.reports, run_id, provider, "fake",
                            fetch=fetch, clock=clock)
        report = (self.reports / f"{summary['run_date']}-{run_id}.md").read_text(encoding="utf-8")
        return result, rows(out / "judged.jsonl"), report


def summary_for(run_date="2026-10-15", start="2026-10-08"):
    s = json.loads((RUN1 / "summary.json").read_text(encoding="utf-8"))
    return {"run_date": run_date, "window": {"start": start, "end": run_date}, "totals": s["totals"]}


@pytest.fixture
def week(tmp_path):
    return Week(tmp_path)


def test_real_week_keeps_exactly_the_five_and_renders_dates_and_fallback_title(week):
    provider = Provider(label_answers())
    result, judged, report = week.run("r1", rows(RUN1 / "items.jsonl"), summary_for(), provider)
    entries = rows(week.wiki / "entries.jsonl")
    assert {e["key"] for e in entries} == KEEPS and len(entries) == 5 and len(provider.log) == 28
    assert not any("sunday" in e["key"] for e in entries)
    pi = next(e for e in entries if e["key"] == "pi.website/blog/api-beta")
    assert (pi["date"], pi["date_source"], pi["title"]) == ("2026-10-13", "page_date", "api-beta")
    assert "候选 28 条：本次判定 28（保留 5 · 排除 23 · 待人工 0 · 失败 0），已判定过跳过 0，暂缓 0。采集渠道失败 0 / 35。" in report
    brain = report.split("## 大脑")[1].split("## 数据 / 仿真")[0]
    assert "2026-10-13 · [Gemini 4 Argon for Robotics](https://deepmind.google/blog/gemini-4-argon-for-robotics/)" in brain
    assert "lerobot/FineART" in report.split("## 数据 / 仿真")[1].split("## 待人工确认")[0]
    assert sorted(r["key"] for r in judged) == sorted(i["key"] for i in rows(RUN1 / "items.jsonl"))


def test_replaying_the_same_batch_calls_nothing_and_changes_no_wiki_bytes(week):
    items = rows(RUN1 / "items.jsonl")
    week.run("r1", items, summary_for(), Provider(label_answers()))
    before = {p: p.read_bytes() for p in week.wiki.rglob("*") if p.is_file()}
    provider = Provider(label_answers())
    _, judged, report = week.run("r2", items, summary_for(), provider)
    assert len(provider.log) == 0 and {r["status"] for r in judged} == {"skipped"}
    assert {p: p.read_bytes() for p in week.wiki.rglob("*") if p.is_file()} == before
    assert "已判定过跳过 28" in report and "## 大脑\n（无）" in report


def test_second_week_appends_only_new_keeps_and_keeps_history(week):
    items = rows(RUN1 / "items.jsonl")
    week.run("r1", items, summary_for(), Provider(label_answers()))
    entries_before = (week.wiki / "entries.jsonl").read_bytes()
    report1 = next(week.reports.glob("*r1.md")).read_bytes()
    overlap = [i for i in items if i["key"] in (G1, "openai.com/index/sentinelone", "github.com/facebookresearch/GenIA")]
    new = [{"url": "https://www.pi.website/blog/pi-1", "key": "pi.website/blog/pi-1", "title": "π1", "published": "2026-10-20",
            "sources": [{"entity": "physical-intelligence", "type": "page"}]},
           {"url": "https://openai.com/index/x", "key": "openai.com/index/x", "title": "X", "published": "2026-10-21",
            "sources": [{"entity": "openai", "type": "rss"}]}]
    answers = {**label_answers(), "pi.website/blog/pi-1": {"decision": "keep", "team": "physical-intelligence"},
               "openai.com/index/x": {"decision": "reject"}}
    provider = Provider(answers)
    _, judged, report = week.run("r2", overlap + new, summary_for("2026-10-22", "2026-10-15"), provider)
    assert len(provider.log) == 2
    after = (week.wiki / "entries.jsonl").read_bytes()
    assert after.startswith(entries_before) and len(rows(week.wiki / "entries.jsonl")) == 6
    assert "本次判定 2（保留 1 · 排除 1 · 待人工 0 · 失败 0），已判定过跳过 3" in report
    brain = report.split("## 大脑")[1].split("## 待人工确认")[0]
    assert "pi-1" in brain and G1 not in brain.replace("https://", "")
    assert next(week.reports.glob("*r1.md")).read_bytes() == report1
    team_page = (week.wiki / "teams" / "physical-intelligence.md").read_text(encoding="utf-8")
    assert "api-beta" in team_page and "π1" in team_page


def one(key="example.com/a", entity="sunday", **extra):
    return {"url": f"https://{key}", "key": key, "title": key, "published": "2026-10-14",
            "sources": [{"entity": entity, "type": "page"}], **extra}


def test_needs_review_is_remembered_and_listed_but_not_in_wiki(week):
    _, judged, report = week.run("r1", [one()], summary_for(), Provider({"example.com/a": {"decision": "needs_review"}}))
    assert json.loads((week.state / "judged.json").read_text())["example.com/a"]["decision"] == "needs_review"
    assert "example.com/a" in report.split("## 待人工确认")[1] and not rows(week.wiki / "entries.jsonl")


def test_unknown_team_falls_back_to_source_and_is_flagged(week):
    _, _, report = week.run("r1", [one(entity="skild")], summary_for(), Provider({"example.com/a": {"decision": "keep", "team": None}}))
    e = rows(week.wiki / "entries.jsonl")[0]
    assert (e["team"], e["team_pending"]) == ("skild", True) and "Skild AI（团队待核）" in report


def test_errors_and_deferred_wait_in_the_queue_and_are_judged_next_run(week, monkeypatch):
    monkeypatch.setattr(judge, "CALL_DEADLINE_SECONDS", 0.2)
    items = [one("example.com/a"), one("example.com/b")]
    _, judged, report = week.run("r1", items, summary_for(), Provider({}, hang=0.5))
    assert [r["status"] for r in judged] == ["error", "error"]
    assert [i["key"] for i in rows(week.state / "judge_queue.jsonl")] == ["example.com/a", "example.com/b"]
    assert "example.com/a" in report.split("## 判定失败（下次运行重试）")[1]
    time.sleep(0.6)
    provider = Provider({"example.com/a": {"decision": "keep", "team": "sunday"}, "example.com/b": {"decision": "reject"}})
    _, judged, _ = week.run("r2", [], summary_for(), provider)
    assert [r["status"] for r in judged] == ["judged", "judged"] and len(provider.log) == 2
    assert not rows(week.state / "judge_queue.jsonl")


REAL_GATE = weekly.Gate


def limit_gate(monkeypatch, **settings):
    gates = []

    def factory(_settings, clock):
        gates.append(REAL_GATE({"max_requests": 100, "max_tokens": 300_000, "max_seconds": 600, **settings}, clock))
        return gates[-1]

    monkeypatch.setattr(weekly, "Gate", factory)
    return gates


def test_request_budget_stops_calls_before_the_limit(week, monkeypatch):
    items = [one(f"example.com/{i}") for i in range(5)]
    provider = Provider({i["key"]: {"decision": "reject"} for i in items})
    limit_gate(monkeypatch, max_requests=3)
    _, judged, report = week.run("r1", items, summary_for(), provider)
    assert len(provider.log) == 3
    assert [r.get("reason") for r in judged[3:]] == ["max_requests", "max_requests"] and "暂缓 2" in report


def request_reserve(messages):
    return weekly.reserve_tokens({"model": "fake", **PARAMS, "messages": messages})


def test_token_budget_holds_even_when_usage_equals_the_reservation(week, monkeypatch):
    items = [one(f"example.com/{i}") for i in range(4)]
    # Same-length keys and texts, so every request reserves the same amount.
    reserve = request_reserve(judge.build_messages(items[0], f"page text for {items[0]['key']}", None,
                                                   weekly.config.load(DOMAIN).raw["scope"],
                                                   weekly.config.load(DOMAIN).entities))
    gates = limit_gate(monkeypatch, max_tokens=2 * reserve)
    provider = Provider({i["key"]: {"decision": "reject"} for i in items}, usage=request_reserve)
    _, judged, _ = week.run("r1", items, summary_for(), provider)
    assert len(provider.log) == 2 and [r.get("reason") for r in judged[2:]] == ["max_tokens", "max_tokens"]
    assert gates[0].tokens <= 2 * reserve


def test_reservation_counts_utf8_bytes_and_overhead():
    messages = [{"role": "user", "content": "机器人" * 1000}]
    request = {"model": "fake", **PARAMS, "messages": messages}
    assert weekly.reserve_tokens(request) == len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 500
    assert weekly.reserve_tokens(request) > 3 * 3000


def test_no_new_item_starts_when_too_little_time_is_left(week, monkeypatch):
    ticks = iter([0.0] + [500.0] * 50)
    fetched = []
    _, judged, _ = week.run("r1", [one()], summary_for(), Provider({}),
                            fetch=lambda i, d: fetched.append(i) or frozen_fetch(i, d), clock=lambda: next(ticks))
    assert judged[0] == {"key": "example.com/a", "url": "https://example.com/a", "status": "deferred", "reason": "max_seconds"}
    assert not fetched


def test_circuit_opens_after_three_failed_calls(week, monkeypatch):
    monkeypatch.setattr(judge, "CALL_DEADLINE_SECONDS", 0.2)
    items = [one(f"example.com/{i}") for i in range(4)]
    provider = Provider({}, hang=0.5)
    _, judged, _ = week.run("r1", items, summary_for(), provider)
    time.sleep(0.6)
    assert len(provider.log) == 3
    assert [r["status"] for r in judged] == ["error", "error", "deferred", "deferred"]
    assert {r["reason"] for r in judged[2:]} == {"circuit_open"}
    assert len(rows(week.state / "judge_queue.jsonl")) == 4


def test_hung_fetch_is_an_error_without_a_model_call(week, monkeypatch):
    monkeypatch.setattr(weekly, "FETCH_DEADLINE_SECONDS", 0.2)

    def fetch(item, d):
        if item["key"] == "example.com/a":
            time.sleep(0.5)
        return frozen_fetch(item, d)

    provider = Provider({"example.com/b": {"decision": "reject"}})
    _, judged, _ = week.run("r1", [one("example.com/a"), one("example.com/b")], summary_for(), provider, fetch=fetch)
    assert judged[0]["error"] == "fetch_timeout" and judged[1]["status"] == "judged" and len(provider.log) == 1
