import json
from datetime import date
from pathlib import Path

import pytest
import trafilatura
import yaml

from radar import config
from radar.channels import github, hf, page, rss
from radar.channels.common import Context
from radar.collect import check_gold
from radar.urls import normalize

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


def ctx(tmp_path, routes: dict, dates: dict | None = None) -> Context:
    def get(url):
        body = routes.get(url)
        if body is None:
            return Resp(b"not found", 404, url)
        return Resp(body if isinstance(body, bytes) else (FIX / body).read_bytes(), 200, url)

    return Context(RUN, tmp_path, get, lambda url: (dates or {}).get(normalize(url)))


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


def test_empty_feed_and_404_fail(tmp_path):
    url = "https://example.com/feed"
    assert rss.collect({"url": url}, ctx(tmp_path, {url: b"<rss></rss>"}), "feeds/x")["status"] == "failed"
    out = rss.collect({"url": url}, ctx(tmp_path, {}), "feeds/y")
    assert out["status"] == "failed" and out["error"] == "http_404"


# --- page ---

SUNDAY = "https://www.sunday.ai/blog"
SUNDAY_LINKS = {f"sunday.ai/blog/{s}" for s in ("act-2-preview", "no-robot-data", "series-b")}
SUNDAY_DATES = {k: d for k, d in zip(sorted(SUNDAY_LINKS), ("2025-11-14", "2025-11-19", "2025-11-14"))}


def test_page_first_run_old_links_are_baseline(tmp_path):
    out = page.collect({"url": SUNDAY}, ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES), "pages/sunday")
    assert out["status"] == "no_update" and out["first_run"]
    assert out["fetched"] == out["baseline"] == 3
    assert json.loads(Path(out["snapshot"]).read_text(encoding="utf-8")) == sorted(SUNDAY_LINKS)


def test_page_later_run_reports_links_missing_from_snapshot(tmp_path):
    c = ctx(tmp_path, {SUNDAY: "sunday_blog.html"}, SUNDAY_DATES)
    snapshot = tmp_path / "pages" / "sunday.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(sorted(SUNDAY_LINKS)[:2]), encoding="utf-8")
    out = page.collect({"url": SUNDAY}, c, "pages/sunday")
    assert out["status"] == "updated" and not out["first_run"]
    assert [i["key"] for i in out["new"]] == [sorted(SUNDAY_LINKS)[2]]


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
