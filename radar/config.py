from dataclasses import dataclass
from pathlib import Path

import yaml

TIERS = {"core", "trial", "dormant"}
DIRECTIONS = {"brain", "data_sim"}
CHANNEL_FIELDS = {"rss": "url", "page": "url", "github": "org", "hf": "org"}


@dataclass
class Domain:
    name: str
    raw: dict
    entities: list[dict]
    queries: list[str]

    @property
    def search(self) -> dict:
        return self.raw.get("search", {})

    @property
    def page(self) -> dict:
        return self.raw.get("page", {})


def _read(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load(domain_dir: str | Path) -> Domain:
    """Read and validate `domains/<name>/`; raises ValueError naming the first broken field."""
    root = Path(domain_dir)
    raw = _read(root / "domain.yaml")
    entities = _read(root / "entities.yaml") or []
    queries = _read(root / "queries.yaml") or []

    seen = set()
    for e in entities:
        for key in ("id", "name", "direction", "tier", "channels"):
            if not e.get(key):
                raise ValueError(f"entity {e.get('id', e)!r} is missing {key}")
        if e["id"] in seen:
            raise ValueError(f"duplicate entity id {e['id']}")
        seen.add(e["id"])
        if e["tier"] not in TIERS:
            raise ValueError(f"entity {e['id']}: tier {e['tier']!r} not in {sorted(TIERS)}")
        if e["direction"] not in DIRECTIONS:
            raise ValueError(f"entity {e['id']}: direction {e['direction']!r} not in {sorted(DIRECTIONS)}")
        for ch in e["channels"]:
            field = CHANNEL_FIELDS.get(ch.get("type"))
            if field is None:
                raise ValueError(f"entity {e['id']}: channel type {ch.get('type')!r} not in {sorted(CHANNEL_FIELDS)}")
            if not ch.get(field):
                raise ValueError(f"entity {e['id']}: {ch['type']} channel needs {field}")
    if not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("queries.yaml must be a list of non-empty strings")
    return Domain(name=raw.get("name", root.name), raw=raw, entities=entities, queries=queries)
