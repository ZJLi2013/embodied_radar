from datetime import date
from pathlib import Path

import pytest
import yaml

from radar import config
from radar.collect import classify_page_date, gold_window, match_gold

ROOT = Path(__file__).resolve().parent.parent
RUN = date(2026, 10, 14)


def test_embodied_domain_config_is_valid():
    domain = config.load(ROOT / "domains" / "embodied")
    assert len(domain.entities) == 22 and len(domain.queries) == 7
    assert domain.control["gold"] == "G1"


def test_rejects_duplicate_entity_ids(tmp_path):
    for name in ("domain.yaml", "queries.yaml", "gold.yaml"):
        (tmp_path / name).write_text((ROOT / "domains" / "embodied" / name).read_text(encoding="utf-8"), encoding="utf-8")
    dup = [{"id": "a", "name": "A", "tier": "core", "query": "a robot"}] * 2
    (tmp_path / "entities.yaml").write_text(yaml.safe_dump(dup), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate entity id"):
        config.load(tmp_path)


def test_gold_window_includes_both_ends():
    gold = [{"id": "edge", "date": date(2026, 10, 7)}, {"id": "today", "date": RUN},
            {"id": "old", "date": date(2026, 10, 6)}]
    assert [g["id"] for g in gold_window(gold, RUN)] == ["edge", "today"]


def test_page_date_classification():
    assert classify_page_date("2026-10-13", RUN) == "in_last_7d"
    assert classify_page_date("2026-09-01T10:00:00", RUN) == "older"
    assert classify_page_date(None, RUN) == "undated"
    assert classify_page_date("not a date", RUN) == "undated"


def test_gold_matches_any_normalized_alias():
    item = {"aliases": ["geeksongs.github.io/agentic_robotics", "arxiv.org/abs/2610.10498"]}
    assert match_gold(item, {"arxiv.org/abs/2610.10498", "x.com/a"}) == "arxiv.org/abs/2610.10498"
    assert match_gold(item, {"x.com/a"}) is None
