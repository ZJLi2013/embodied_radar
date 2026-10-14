"""One collection run: every channel of every team, plus supplementary open search."""

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import requests
import trafilatura
import yaml

from radar import config
from radar.channels import COLLECTORS
from radar.channels.common import Context, finish, load_state, save_state
from radar.search import search_with_retry
from radar.urls import normalize

UA = {"User-Agent": "Mozilla/5.0 (compatible; embodied-radar/0.1)"}


def make_context(run_date: date, state_dir: Path, date_fetch_budget: int) -> Context:
    session = requests.Session()
    session.headers.update(UA)
    token = os.environ.get("GITHUB_TOKEN")

    def get(url: str):
        headers = {}
        if token and url.startswith("https://api.github.com"):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        return session.get(url, headers=headers, timeout=30)

    def page_date(url: str) -> str | None:
        try:
            resp = get(url)
            if resp.status_code >= 400:
                return None
            doc = trafilatura.extract(resp.text, url=resp.url, output_format="json", with_metadata=True)
        except Exception:  # trafilatura raises on some markup; treat as undated
            return None
        return json.loads(doc).get("date") if doc else None

    return Context(run_date, state_dir, get, page_date, date_fetch_budget)


STATE_KIND = {"rss": "feeds", "page": "pages"}


def channel_key(entity_id: str, channel: dict, index: int) -> str:
    """Stable state key; `index` counts earlier channels of the same type in this entity."""
    return f"{STATE_KIND.get(channel['type'], channel['type'])}/{entity_id}" + (f"-{index}" if index else "")


def run_channel(channel: dict, ctx: Context, key: str) -> dict:
    try:
        return COLLECTORS[channel["type"]](channel, ctx, key)
    except Exception as exc:  # one broken channel must not stop the run
        return {"status": "failed", "fetched": 0, "new": [], "baseline": 0, "error": type(exc).__name__}


def run_search(domain, ctx: Context) -> dict:
    """Open search finds teams outside the list; results have no dates, so the first run is a baseline."""
    settings = domain.search
    state = load_state(ctx, "search/queries")
    seen = None if state is None else set(state)
    keys, new, failures = [], [], 0
    for query in domain.queries:
        result = search_with_retry(query, settings.get("backend", "searxng"),
                                   int(settings.get("max_results", 10)), int(settings.get("retries", 2)))
        failures += result["error"] is not None
        for r in result["results"]:
            if not r["url"].startswith(("http://", "https://")):
                continue
            k = normalize(r["url"])
            keys.append(k)
            if seen is not None and k not in seen:
                new.append({"url": r["url"], "key": k, "title": r["title"], "query": query})
        time.sleep(float(settings.get("interval_seconds", 1.0)))
    save_state(ctx, "search/queries", sorted(set(keys)))
    out = finish(len(set(keys)), new, len(set(keys)) - len(new), first_run=seen is None)
    out["failed_queries"] = failures
    return out


def check_gold(gold: list[dict], items: dict, run_date: date) -> list[dict]:
    rows = []
    for g in gold:
        if not (run_date.toordinal() - 7 <= g["date"].toordinal() <= run_date.toordinal()):
            continue
        aliases = {normalize(a) for a in g["aliases"]}
        hits = [items[k] for k in aliases if k in items]
        rows.append({"id": g["id"], "hit": bool(hits), "expect": g.get("expect"),
                     "via": sorted({(s["entity"], s["type"]) for h in hits for s in h["sources"]})})
    return rows


def run(domain_dir: str, out_dir: str, state_dir: str, gold_path: str, run_date: date | None = None) -> dict:
    domain = config.load(domain_dir)
    run_date = run_date or date.today()
    ctx = make_context(run_date, Path(state_dir), int(domain.page.get("date_fetch_budget", 150)))

    entities, items = {}, {}
    for entity in domain.entities:
        if entity["tier"] == "dormant":
            continue
        rows, per_type = [], {}
        for channel in entity["channels"]:
            target = channel.get("url") or channel.get("org")
            index = per_type.get(channel["type"], 0)
            per_type[channel["type"]] = index + 1
            if channel.get("broken"):
                rows.append({"type": channel["type"], "target": target, "status": "broken", "error": channel["broken"],
                             "fetched": 0, "new": [], "baseline": 0})
                continue
            result = run_channel(channel, ctx, channel_key(entity["id"], channel, index))
            rows.append({"type": channel["type"], "target": target, **result})
            for item in result["new"]:
                merged = items.setdefault(item["key"], {**item, "sources": []})
                merged["sources"].append({"entity": entity["id"], "type": channel["type"]})
        entities[entity["id"]] = {"direction": entity["direction"], "channels": rows}

    search = run_search(domain, ctx) if domain.queries else None
    gold = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8")) if Path(gold_path).exists() else []

    all_rows = [r for e in entities.values() for r in e["channels"]]
    by_type = {}
    for r in all_rows:
        t = by_type.setdefault(r["type"], {"channels": 0, "failed": 0, "broken": 0, "no_update": 0, "updated": 0, "new": 0})
        t["channels"] += 1
        t[r["status"]] += 1
        t["new"] += len(r["new"])
    summary = {
        "run_date": run_date.isoformat(),
        "window": {"start": ctx.window_start.isoformat(), "end": run_date.isoformat()},
        "totals": {"channels": len(all_rows), "failed": sum(r["status"] == "failed" for r in all_rows),
                   "broken": sum(r["status"] == "broken" for r in all_rows), "new_items": len(items)},
        "by_type": by_type,
        "page_date_fetches": {"used": ctx.date_fetches, "budget": ctx.date_fetch_budget},
        "entities": entities,
        "search": search and {k: v for k, v in search.items() if k != "new"} | {"new": len(search["new"])},
        "gold": check_gold(gold, items, run_date),
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "items.jsonl", "w", encoding="utf-8") as f:
        for item in items.values():
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect this week's new items for a domain")
    parser.add_argument("--domain", default="embodied")
    parser.add_argument("--out", default=None, help="default: out/<domain>/latest")
    parser.add_argument("--state", default=None, help="default: state/<domain>")
    parser.add_argument("--gold", default=None, help="default: evals/<domain>/gold.yaml")
    args = parser.parse_args()
    summary = run(f"domains/{args.domain}", args.out or f"out/{args.domain}/latest",
                  args.state or f"state/{args.domain}", args.gold or f"evals/{args.domain}/gold.yaml")
    print(json.dumps({k: summary[k] for k in ("run_date", "totals", "by_type", "gold")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
