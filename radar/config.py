from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

TIERS = {"core", "trial", "dormant"}


@dataclass
class Domain:
    name: str
    raw: dict
    entities: list[dict]
    queries: list[str]
    gold: list[dict]

    @property
    def search(self) -> dict:
        return self.raw.get("search", {})

    @property
    def control(self) -> dict:
        return self.raw["control"]


def _read(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load(domain_dir: str | Path) -> Domain:
    """Read and validate `domains/<name>/`; raises ValueError naming the first broken field."""
    root = Path(domain_dir)
    raw = _read(root / "domain.yaml")
    entities = _read(root / "entities.yaml") or []
    queries = _read(root / "queries.yaml") or []
    gold = _read(root / "gold.yaml") or []

    seen = set()
    for e in entities:
        for key in ("id", "name", "tier", "query"):
            if not e.get(key):
                raise ValueError(f"entity {e!r} is missing {key}")
        if e["tier"] not in TIERS:
            raise ValueError(f"entity {e['id']}: tier {e['tier']!r} not in {sorted(TIERS)}")
        if e["id"] in seen:
            raise ValueError(f"duplicate entity id {e['id']}")
        seen.add(e["id"])
    if not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("queries.yaml must be a list of non-empty strings")
    gold_ids = set()
    for g in gold:
        if not g.get("id") or not g.get("aliases") or not isinstance(g.get("date"), date):
            raise ValueError(f"gold item {g!r} needs id, a YYYY-MM-DD date and aliases")
        gold_ids.add(g["id"])
    control = raw.get("control") or {}
    if not control.get("query") or control.get("gold") not in gold_ids:
        raise ValueError("domain.yaml control needs a query and a gold id present in gold.yaml")
    return Domain(name=raw.get("name", root.name), raw=raw, entities=entities, queries=queries, gold=gold)
