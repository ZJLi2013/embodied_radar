import json
from datetime import date
from pathlib import Path

import pytest
import trafilatura
import yaml

from radar import collect, config
from radar.channels import github, hf, page, rss
from radar.channels.common import Context
from radar.collect import check_gold
from radar.urls import normalize
from radar.verify import check_page_states

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
RUN = date(2026, 10, 14)
G1 = "deepmind.google/blog/gemini-4-argon-for-robotics"
G1_DEV = "developers.googleblog.com/gemini-4-argon-for-robotics"


class Resp:
    def __init__(self, body: bytes, status: int = 200, url: str = ""):
        self.content, self.status_code, self.url = body, status, url
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


def ctx(tmp_path, routes: dict, dates: dict | None = None, run_date: date = RUN, budget: int = 150) -> Context:
    def get(url):
        body = routes.get(url)
        if body is None:
            return Resp(b"not found", 404, url)
        return Resp(body if isinstance(body, bytes) else (FIX / body).read_bytes(), 200, url)

    return Context(run_date, tmp_path, get, lambda url: (dates or {}).get(normalize(url)), budget)


def g1_article_date() -> str:
    html = (FIX / "gdev_g1_article.html").read_text(encoding="utf-8")
    return json.loads(trafilatura.extract(html, output_format="json", with_metadata=True))["date"]


# --- rss ---

def test_deepmind_rss_has_g1_as_new(tmp_path):
    url = "https://deepmind.google/blog/rss.xml"
    out = rss.collect({"url": url}, ctx(tmp_path, {url: "deepmind_rss.xml"}), "feeds/deepmind")
    assert out["status"] == "updated" and out["first_run"]
    assert G1 in {i["key"] for i in out["new"]}
    assert all(RUN.toordinal() - 7 <= date.fromisoformat(i["published"]).toordinal() <= RUN.toordinal() for i in out["new"])


def test_undated_feed_is_baseline_then_diffs_against_state(tmp_path):
    url = "https://developers.googleblog.com/feeds/posts/default"
    c = ctx(tmp_path, {url: "gdev_rss.xml"})
    first = rss.collect({"url": url}, c, "feeds/gdev")
    assert first["status"] == "no_update" and first["fetched"] == first["baseline"] == 20
    again = rss.collect({"url": url}, c, "feeds/gdev")
    assert again["status"] == "no_update" and not again["first_run"]


SRL_FEED = "https://research.nvidia.com/labs/srl/publication/index.xml"


def feed(entries) -> bytes:
    items = "".join(f"<item><title>{s}</title><link>//research.nvidia.com/labs/srl/publication/{s}/</link>"
                    f"<pubDate>{d}</pubDate></item>" for s, d in entries)
    return f"<rss version='2.0'><channel><title>x</title>{items}</channel></rss>".encode()


VENUE = "Sun, 01 Nov 2026 00:00:00 +0000"
FIRST = [("a", VENUE), ("b", "Mon, 12 Oct 2026 00:00:00 +0000"), ("c", VENUE)]


def test_ignore_dates_feed_first_run_is_baseline(tmp_path):
    out = rss.collect({"url": SRL_FEED, "ignore_dates": True}, ctx(tmp_path, {SRL_FEED: feed(FIRST)}), "feeds/srl")
    assert out["status"] == "no_update" and out["baseline"] == 3


def test_ignore_dates_feed_reports_only_unseen_entries(tmp_path):
    channel = {"url": SRL_FEED, "ignore_dates": True}
    rss.collect(channel, ctx(tmp_path, {SRL_FEED: feed(FIRST)}), "feeds/srl")
    out = rss.collect(channel, ctx(tmp_path, {SRL_FEED: feed(FIRST + [("d", VENUE)])}), "feeds/srl")
    assert [i["key"] for i in out["new"]] == ["research.nvidia.com/labs/srl/publication/d"]


def test_protocol_relative_feed_links_resolve_against_feed_url(tmp_path):
    out = rss.collect({"url": SRL_FEED}, ctx(tmp_path, {SRL_FEED: feed([("x", "Mon, 12 Oct 2026 00:00:00 +0000")])}), "feeds/srl")
    assert [(i["key"], i["url"]) for i in out["new"]] == [
        ("research.nvidia.com/labs/srl/publication/x", "https://research.nvidia.com/labs/srl/publication/x/")]


def test_empty_feed_and_404_fail(tmp_path):
    url = "https://example.com/feed"
    assert rss.collect({"url": url}, ctx(tmp_path, {url: b"<rss></rss>"}), "feeds/x")["status"] == "failed"
    out = rss.collect({"url": url}, ctx(tmp_path, {}), "feeds/y")
    assert out["status"] == "failed" and out["error"] == "http_404"


# --- page ---

SUNDAY = "https://www.sunday.ai/blog"
SUNDAY_LINKS = {f"sunday.ai/blog/{s}" for s in ("act-2-preview", "no-robot-data", "series-b")}
SUNDAY_DATES = {k: d for k, d in zip(sorted(SUNDAY_LINKS), ("2025-11-14", "2025-11-19", "2025-11-14"))}


NEXT = date(2026, 10, 21)


def read_state(out):
    return json.loads(Path(out["snapshot"]).read_text(encoding="utf-8"))


def write_state(tmp_path, name, seen, pending=None):
    path = tmp_path / "pages" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": sorted(seen), "pending": pending or {}}), encoding="utf-8")


def list_page(slugs) -> bytes:
    return "".join(f'<a href="/blog/{s}">{s}</a>' for s in slugs).encode()


def test_page_first_run_old_links_are_baseline(tmp_path):
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES), "pages/sunday")
    assert out["status"] == "no_update" and out["first_run"]
    assert out["fetched"] == out["baseline"] == 3 and out["pending"] == 0
    assert read_state(out) == {"seen": sorted(SUNDAY_LINKS), "pending": {}}


def test_page_later_run_reports_links_missing_from_snapshot(tmp_path):
    write_state(tmp_path, "sunday", sorted(SUNDAY_LINKS)[:2])
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES), "pages/sunday")
    assert out["status"] == "updated" and not out["first_run"]
    assert [i["key"] for i in out["new"]] == [sorted(SUNDAY_LINKS)[2]]


def test_page_over_budget_links_stay_pending(tmp_path):
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES, budget=2), "pages/sunday")
    state = read_state(out)
    assert len(state["seen"]) == 2 and out["pending"] == 1 and out["date_fetches"] == 2
    [(key, entry)] = state["pending"].items()
    assert key not in state["seen"] and entry["since"] == "2026-10-07"


@pytest.mark.parametrize("page_date, is_new", [("2026-10-10", True), ("2025-11-14", False), ("2026-10-22", False)])
def test_pending_link_is_judged_against_its_own_window(tmp_path, page_date, is_new):
    first = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES, budget=2), "pages/sunday")
    [key] = read_state(first)["pending"]
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, {key: page_date}, run_date=NEXT),
                       "pages/sunday")
    assert [(i["key"], i["late"]) for i in out["new"]] == ([(key, True)] if is_new else [])
    assert key in read_state(out)["seen"] and out["pending"] == 0


def test_pending_link_waits_while_budget_is_spent(tmp_path):
    page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES, budget=2), "pages/sunday")
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, run_date=NEXT, budget=0), "pages/sunday")
    assert out["pending"] == 1 and not out["new"]
    assert [e["since"] for e in read_state(out)["pending"].values()] == ["2026-10-07"]


def test_pending_link_that_left_the_page_is_still_checked(tmp_path):
    gone = "sunday.ai/blog/gone"
    write_state(tmp_path, "sunday", SUNDAY_LINKS, {gone: {"url": "https://www.sunday.ai/blog/gone", "since": "2026-10-07"}})
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, {gone: "2026-10-10"}, run_date=NEXT),
                       "pages/sunday")
    assert [(i["key"], i["late"]) for i in out["new"]] == [(gone, True)]
    assert gone in read_state(out)["seen"] and out["pending"] == 0


def test_seen_link_that_reappears_is_not_new(tmp_path):
    url, slugs = "https://example.com/blog", ["a", "b", "c"]
    write_state(tmp_path, "ex", {f"example.com/blog/{s}" for s in slugs})
    week2 = page.collect({"url": url}, ctx(tmp_path, {url: list_page(slugs[:2])}, run_date=NEXT), "pages/ex")
    week3 = page.collect({"url": url}, ctx(tmp_path, {url: list_page(slugs)}, run_date=date(2026, 10, 28)), "pages/ex")
    assert not week2["new"] and not week3["new"]
    assert len(read_state(week3)["seen"]) == 3


LONG = "https://example.com/blog"
SLUGS = [f"p{i:02d}" for i in range(1, 16)]
LONG_DATES = {f"example.com/blog/{s}": ("2026-10-12" if s == "p13" else "2025-11-14") for s in SLUGS}


def test_long_page_within_budget_checks_every_link(tmp_path):
    out = page.collect({"url": LONG}, ctx(tmp_path, {LONG: list_page(SLUGS)}, LONG_DATES), "pages/long")
    assert out["date_fetches"] == 15 and out["pending"] == 0
    assert [i["key"] for i in out["new"]] == ["example.com/blog/p13"]
    assert len(read_state(out)["seen"]) == 15


def test_long_page_over_budget_reports_p13_on_next_run(tmp_path):
    routes = {LONG: list_page(SLUGS)}
    first = page.collect({"url": LONG}, ctx(tmp_path, routes, LONG_DATES, budget=12), "pages/long")
    state = read_state(first)
    assert state["seen"] == [f"example.com/blog/{s}" for s in SLUGS[:12]]
    assert sorted(state["pending"]) == [f"example.com/blog/{s}" for s in SLUGS[12:]] and not first["new"]
    out = page.collect({"url": LONG}, ctx(tmp_path, routes, LONG_DATES, run_date=NEXT), "pages/long")
    assert [(i["key"], i["late"]) for i in out["new"]] == [("example.com/blog/p13", True)]


def test_date_budget_is_shared_across_page_channels(tmp_path):
    c = ctx(tmp_path, {SUNDAY: "sunday_blog.html", LONG: list_page(SLUGS)}, SUNDAY_DATES, budget=3)
    sunday = page.collect({"url": SUNDAY}, c, "pages/sunday")
    long = page.collect({"url": LONG}, c, "pages/long")
    assert sunday["date_fetches"] == 3 and long["date_fetches"] == 0 and long["pending"] == 15
    assert c.date_fetches == 3


def test_page_link_pattern_selects_cross_site_g1(tmp_path):
    list_url = "https://deepmind.google/blog/"
    routes = {list_url: "deepmind_blog_list.html"}
    c = ctx(tmp_path, routes, {G1_DEV: g1_article_date()})

    default = page.collect({"url": list_url}, c, "pages/default")
    assert G1_DEV not in {i["key"] for i in default["new"]}
    assert not any(k.startswith("developers.googleblog.com") for k in json.loads(Path(default["snapshot"]).read_text()))

    pattern = r"https://(deepmind\.google/blog/|developers\.googleblog\.com/)"
    out = page.collect({"url": list_url, "link_pattern": pattern}, c, "pages/cross")
    hit = [i for i in out["new"] if i["key"] == G1_DEV]
    assert out["status"] == "updated" and hit and hit[0]["page_date"].startswith("2026-10-13")
    assert "utm_source" in hit[0]["url"]
    gold = [{"id": "G1", "date": date(2026, 10, 13), "aliases": [G1_DEV, G1]}]
    items = {i["key"]: {**i, "sources": [{"entity": "deepmind-robotics", "type": "page"}]} for i in out["new"]}
    assert check_gold(gold, items, RUN)[0]["hit"]


def test_empty_page_and_404_fail(tmp_path):
    url = "https://example.com/blog"
    assert page.collect({"url": url}, ctx(tmp_path, {url: b"<html></html>"}), "pages/x")["status"] == "failed"
    assert page.collect({"url": url}, ctx(tmp_path, {}), "pages/y")["status"] == "failed"


# --- github ---

def gh_url(org):
    return f"https://api.github.com/orgs/{org}/repos?sort=created&direction=desc&per_page=30"


def test_github_old_repos_are_no_update(tmp_path):
    out = github.collect({"org": "nvidia-cosmos"}, ctx(tmp_path, {gh_url("nvidia-cosmos"): "github_nvidia_cosmos.json"}), "github/x")
    assert out["status"] == "no_update" and out["fetched"] > 0


def test_github_only_repos_created_in_window_are_new(tmp_path):
    out = github.collect({"org": "amazon-far"}, ctx(tmp_path, {gh_url("amazon-far"): "github_mixed.json"}), "github/x")
    assert out["status"] == "updated"
    assert [i["title"] for i in out["new"]] == ["imprint"]


def test_github_unknown_org_fails(tmp_path):
    assert github.collect({"org": "nope"}, ctx(tmp_path, {}), "github/x")["status"] == "failed"


# --- hf ---

def hf_url(kind, org):
    return f"https://huggingface.co/api/{kind}?author={org}&sort=createdAt&direction=-1&limit=30"


def test_hf_lerobot_new_models_only(tmp_path):
    routes = {hf_url("models", "lerobot"): "hf_lerobot_models.json", hf_url("datasets", "lerobot"): "hf_lerobot_datasets.json"}
    out = hf.collect({"org": "lerobot"}, ctx(tmp_path, routes), "hf/x")
    assert out["status"] == "updated"
    assert sorted(i["title"].split("/")[-1] for i in out["new"]) == ["lingbot_vla_v2_base", "lingbot_vla_v2_robotwin"]
    assert {i["kind"] for i in out["new"]} == {"models"}


def test_hf_lerobot_datasets_alone_are_no_update(tmp_path):
    routes = {hf_url("models", "lerobot"): b"[]", hf_url("datasets", "lerobot"): "hf_lerobot_datasets.json"}
    assert hf.collect({"org": "lerobot"}, ctx(tmp_path, routes), "hf/x")["status"] == "no_update"


def test_hf_nvidia_new_datasets(tmp_path):
    routes = {hf_url("models", "nvidia"): b"[]", hf_url("datasets", "nvidia"): "hf_datasets_mixed.json"}
    out = hf.collect({"org": "nvidia"}, ctx(tmp_path, routes), "hf/x")
    assert sorted(i["title"].split("/")[-1] for i in out["new"]) == sorted(
        ["agile_one_s_pick_up_ssd_54eps", "agile_one_s_pick_196_eps", "Agile_one_s_PNP_Dataset"])


def test_hf_404_fails(tmp_path):
    assert hf.collect({"org": "nope"}, ctx(tmp_path, {}), "hf/x")["status"] == "failed"


# --- config ---

def test_embodied_config_is_valid():
    domain = config.load(ROOT / "domains" / "embodied")
    assert len(domain.entities) == 21
    assert {c["type"] for e in domain.entities for c in e["channels"]} == {"rss", "page", "github", "hf"}


def test_srl_tracks_publication_feed_not_page():
    srl = next(e for e in config.load(ROOT / "domains" / "embodied").entities if e["id"] == "nvidia-srl")
    assert {"type": "rss", "url": SRL_FEED, "ignore_dates": True} in srl["channels"]
    assert not any(c["type"] == "page" for c in srl["channels"])


def test_pending_carries_over_between_collect_runs(tmp_path, monkeypatch):
    domain = tmp_path / "domain"
    domain.mkdir()
    (domain / "domain.yaml").write_text("name: t\npage:\n  date_fetch_budget: 10\n", encoding="utf-8")
    (domain / "queries.yaml").write_text("[]\n", encoding="utf-8")
    team = {"direction": "brain", "tier": "core"}
    (domain / "entities.yaml").write_text(yaml.safe_dump([
        {"id": "sunday", "name": "S", **team, "channels": [{"type": "page", "url": SUNDAY}]},
        {"id": "long", "name": "L", **team, "channels": [{"type": "page", "url": LONG}]},
    ]), encoding="utf-8")
    routes, dates = {SUNDAY: "sunday_blog.html", LONG: list_page(SLUGS)}, {**SUNDAY_DATES, **LONG_DATES}
    monkeypatch.setattr(collect, "make_context",
                        lambda run_date, state_dir, budget: ctx(state_dir, routes, dates, run_date, budget))
    state = tmp_path / "state"

    first = collect.run(domain, tmp_path / "out1", state, tmp_path / "none.yaml", run_date=RUN)
    long1 = first["entities"]["long"]["channels"][0]
    pending = set(read_state(long1)["pending"])
    assert first["page_date_fetches"]["used"] == 10 and len(pending) == 8 and not check_page_states(first)

    second = collect.run(domain, tmp_path / "out2", state, tmp_path / "none.yaml", run_date=NEXT)
    long2 = second["entities"]["long"]["channels"][0]
    assert long2["pending"] == 0 and pending <= set(read_state(long2)["seen"])
    assert [(i["key"], i["late"]) for i in long2["new"]] == [("example.com/blog/p13", True)]
    assert not check_page_states(second)


def test_gold_g1_expects_deepmind_rss():
    gold = yaml.safe_load((ROOT / "evals" / "embodied" / "gold.yaml").read_text(encoding="utf-8"))
    assert [g["id"] for g in gold] == ["G1"] and gold[0]["expect"] == {"entity": "deepmind-robotics", "type": "rss"}


def write_domain(tmp_path, entities):
    for name in ("domain.yaml", "queries.yaml"):
        (tmp_path / name).write_text((ROOT / "domains" / "embodied" / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "entities.yaml").write_text(yaml.safe_dump(entities), encoding="utf-8")


TEAM = {"id": "a", "name": "A", "direction": "brain", "tier": "core", "channels": [{"type": "rss", "url": "https://a/feed"}]}


@pytest.mark.parametrize("entities, message", [
    ([TEAM, TEAM], "duplicate entity id"),
    ([{**TEAM, "channels": [{"type": "twitter", "url": "x"}]}], "channel type"),
    ([{**TEAM, "channels": [{"type": "github", "url": "x"}]}], "needs org"),
    ([{**TEAM, "channels": [{"type": "page"}]}], "needs url"),
    ([{**TEAM, "direction": "hardware"}], "direction"),
    ([{k: v for k, v in TEAM.items() if k != "channels"}], "missing channels"),
])
def test_config_rejects(tmp_path, entities, message):
    write_domain(tmp_path, entities)
    with pytest.raises(ValueError, match=message):
        config.load(tmp_path)
