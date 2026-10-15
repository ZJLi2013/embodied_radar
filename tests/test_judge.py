import json
import time
from pathlib import Path

import pytest
import requests

from radar import config, judge
from radar.verify import _plain, check_judge

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = ROOT / "domains" / "embodied"
ITEMS = [
    {"url": "https://example.com/blog/robot", "key": "example.com/blog/robot", "title": "", "description": "",
     "sources": [{"entity": "sunday", "type": "page"}]},
    {"url": "https://example.com/blog/gone", "key": "example.com/blog/gone", "title": "Gone", "sources": []},
]
ARTICLE = b"<html><body><article><p>We release a robot manipulation policy trained on home data.</p>" \
          b"<p>It folds laundry and loads dishwashers.</p></article></body></html>"


class Resp:
    def __init__(self, body: bytes, status: int):
        self.content, self.status_code = body, status


class FakeProvider:
    """Replies are content strings, exceptions, or ("hang", seconds); consumed in order. State is shared by copies."""

    def __init__(self, replies):
        self.replies, self.log, self.last_response = list(replies), [], None

    @property
    def calls(self):
        return len(self.log)

    def chat(self, messages, **kwargs):
        self.log.append(1)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            time.sleep(reply[1])
            reply = answer()
        self.last_response = {"model": "fake-model", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                              "choices": [{"message": {"content": reply}}]}
        return reply


def answer(decision="keep", quote="We release a robot manipulation policy trained on home data.", **extra):
    return json.dumps({"decision": decision, "reason": "r", "evidence_quote": quote, "team": None, **extra})


@pytest.fixture
def frozen(tmp_path):
    jdir = tmp_path / "judge"
    jdir.mkdir()
    (jdir / "items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), encoding="utf-8")
    pages = {"https://example.com/blog/robot": Resp(ARTICLE, 200), "https://example.com/blog/gone": Resp(b"", 404)}
    for item in ITEMS:
        judge.fetch_evidence(item, jdir / "evidence", get=lambda u: pages[u])
    labels = [{"key": ITEMS[0]["key"], "label": "keep", "set": "a"}, {"key": ITEMS[1]["key"], "label": "needs_review", "set": "a"}]
    (jdir / "labels.jsonl").write_text("".join(json.dumps(l) + "\n" for l in labels), encoding="utf-8")
    return jdir


def run(jdir, tmp_path, replies):
    provider = FakeProvider(replies)
    summary = judge.run(jdir / "items.jsonl", jdir / "evidence", tmp_path / "out", DOMAIN, provider, "fake-model")
    return summary, provider


def test_failed_fetch_is_kept_as_evidence(frozen):
    meta = json.loads((frozen / "evidence" / "example_com_blog_gone.json").read_text(encoding="utf-8"))
    assert meta["status"] == 404 and meta["error"] == "http_404" and meta["attempts"] == 2 and meta["text_chars"] == 0


def test_messages_mark_empty_fields_missing_text_and_truncation():
    domain = config.load(DOMAIN)
    empty = judge.build_messages(ITEMS[0], "", "http_404", domain.raw["scope"], domain.entities)[1]["content"]
    assert "title: （空）" in empty and "description: （空）" in empty and "（正文抓取失败：http_404）" in empty
    long = judge.build_messages(ITEMS[1], "x" * 7000, None, domain.raw["scope"], domain.entities)[1]["content"]
    assert "已截断，原文 7000 字符" in long and "x" * 6001 not in long


def test_run_reconciles_every_input_with_one_result(frozen, tmp_path):
    summary, provider = run(frozen, tmp_path, [answer(), answer("needs_review", "")])
    assert [r["key"] for r in summary["results"]] == [i["key"] for i in ITEMS] and provider.calls == 2
    assert all((tmp_path / "out" / r[f]).exists() for r in summary["results"] for f in ("request_file", "response_file"))
    assert summary["usage"] == {"requests": 2, "prompt_tokens": 20, "completion_tokens": 10}
    assert summary["models_reported"] == ["fake-model"] and summary["errors"] == 0


@pytest.mark.parametrize("bad", ["not json", answer(extra_key=1), answer("maybe")])
def test_invalid_output_is_an_error_not_a_decision(frozen, tmp_path, bad):
    summary, _ = run(frozen, tmp_path, [bad, answer("needs_review", "")])
    first = summary["results"][0]
    assert first["error"].startswith("parse:") and "decision" not in first


def test_api_failures_use_the_shared_retry_budget_then_error(frozen, tmp_path):
    fail = requests.ConnectionError("down")
    summary, provider = run(frozen, tmp_path, [fail] * 4 + [answer("needs_review", "")])
    assert summary["results"][0]["error"] == "http:ConnectionError" and summary["http_retries_used"] == 3
    assert provider.calls == 5 and summary["results"][1]["error"] is None


def test_hung_call_times_out_and_its_late_answer_is_not_recorded(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "CALL_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(judge, "HTTP_RETRY_BUDGET", 0)
    summary, _ = run(frozen, tmp_path, [("hang", 0.5), answer("needs_review", "")])
    time.sleep(0.5)
    first, second = summary["results"]
    assert first["error"] == "http:Timeout" and first["response_file"] is None
    assert second["error"] is None and second["decision"] == "needs_review"


def test_verifier_accepts_matching_run_and_catches_label_mismatch(frozen, tmp_path):
    run(frozen, tmp_path, [answer(), answer("needs_review", "")])
    problems, per_set = check_judge(tmp_path / "out" / "summary.json", frozen, DOMAIN)
    assert problems == [] and per_set == {"a": [2, 2]}
    run(frozen, tmp_path, [answer("reject"), answer("needs_review", "")])
    problems, _ = check_judge(tmp_path / "out" / "summary.json", frozen, DOMAIN)
    assert any("decided reject, label keep" in p for p in problems)


def test_quote_match_ignores_markup_but_not_words():
    page = "Amp(opens in a new window) is building\n  an <b>agent</b> for **every** dev_ops team."
    assert _plain("Amp is building an agent for every devops team.") in _plain(page)
    assert _plain("Amp is building a robot for every devops team.") not in _plain(page)
    card = "size: 10K<n<100K\n---\nFineART is a **bimanual** dataset.\n<p>more</p>"
    assert _plain("FineART is a bimanual dataset.") in _plain(card)


def test_verifier_catches_tampered_request_and_invented_quote(frozen, tmp_path):
    run(frozen, tmp_path, [answer(quote="a sentence that is not in the page"), answer("needs_review", "")])
    req = tmp_path / "out" / "calls" / "example_com_blog_robot.request.json"
    data = json.loads(req.read_text(encoding="utf-8"))
    data["messages"][1]["content"] += "\nreference label: keep"
    req.write_text(json.dumps(data), encoding="utf-8")
    problems, _ = check_judge(tmp_path / "out" / "summary.json", frozen, DOMAIN)
    assert any("differs from the designed template" in p for p in problems)
    assert any("evidence_quote not found" in p for p in problems)
