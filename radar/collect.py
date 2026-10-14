"""One collection run: search the last week, normalize, dedupe, fetch, and summarize."""

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from radar import config
from radar.fetch import fetch
from radar.search import search_with_retry
from radar.urls import normalize

WINDOW_DAYS = 7


def gold_window(gold: list[dict], run_date: date) -> list[dict]:
    """Gold items published in [run_date - 7, run_date], both ends included."""
    start = run_date - timedelta(days=WINDOW_DAYS)
    return [g for g in gold if start <= g["date"] <= run_date]


def classify_page_date(page_date: str | None, run_date: date) -> str:
    if not page_date:
        return "undated"
    try:
        parsed = date.fromisoformat(page_date[:10])
    except ValueError:
        return "undated"
    return "in_last_7d" if parsed >= run_date - timedelta(days=WINDOW_DAYS) else "older"


def match_gold(item: dict, keys: set[str]) -> str | None:
    return next((a for a in map(normalize, item["aliases"]) if a in keys), None)


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["key"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def run(domain_dir: str, out_dir: str, state_dir: str, backend: str | None = None) -> dict:
    domain = config.load(domain_dir)
    settings = domain.search
    backend = backend or settings.get("backend", "searxng")
    max_results = int(settings.get("max_results", 10))
    retries = int(settings.get("retries", 2))
    interval = float(settings.get("interval_seconds", 1.0))
    run_date = date.today()

    plan = [(e["id"], e["query"]) for e in domain.entities if e["tier"] != "dormant"]
    plan += [("open", q) for q in domain.queries]

    errors, per_query, rows = Counter(), [], []
    failed = empty = 0
    for source, query in plan:
        result = search_with_retry(query, backend, max_results, retries)
        if result["error"]:
            failed += 1
            errors[result["error"]] += 1
        elif not result["results"]:
            empty += 1
        per_query.append({"source": source, "query": query, "raw": len(result["results"]), "error": result["error"]})
        rows += [{**r, "source": source, "query": query} for r in result["results"]]
        time.sleep(interval)

    control_result = search_with_retry(domain.control["query"], backend, max_results, retries)
    control_keys = {normalize(r["url"]) for r in control_result["results"] if r["url"]}
    control_gold = next(g for g in domain.gold if g["id"] == domain.control["gold"])

    seen = _load_seen(Path(state_dir) / "seen.jsonl")
    normalized = [r | {"key": normalize(r["url"])} for r in rows if r["url"].startswith(("http://", "https://"))]
    candidates: dict[str, dict] = {}
    for r in normalized:
        if r["key"] in seen:
            continue
        entry = candidates.setdefault(r["key"], {"key": r["key"], "url": r["url"], "title": r["title"],
                                                  "snippet": r["snippet"], "sources": []})
        if r["source"] not in entry["sources"]:
            entry["sources"].append(r["source"])
    for q in per_query:
        q["unique"] = len({r["key"] for r in normalized if r["query"] == q["query"] and r["key"] in candidates})

    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, page in zip(candidates.values(), pool.map(fetch, [c["url"] for c in candidates.values()])):
            entry["fetch"] = page

    keys = set(candidates)
    gold = []
    for item in gold_window(domain.gold, run_date):
        hit = match_gold(item, keys)
        page = candidates[hit]["fetch"] if hit else {}
        gold.append({"id": item["id"], "hit": hit is not None, "url": hit,
                     "chars": page.get("chars", 0), "page_date": page.get("date")})

    fetched = [c["fetch"] for c in candidates.values()]
    summary = {
        "run_date": run_date.isoformat(),
        "backend": backend,
        "requests": {"total": len(plan), "failed": failed, "empty": empty, "errors": dict(errors)},
        "stages": {"raw": len(rows), "normalized": len(normalized), "unique": len(candidates)},
        "per_query": per_query,
        "fetch": {
            "attempted": len(fetched),
            "ok": sum(f["status"] == "ok" for f in fetched),
            "thin": sum(f["status"] == "thin" for f in fetched),
            "failed": sum(f["status"] not in ("ok", "thin") for f in fetched),
            "reasons": dict(Counter(f["status"] for f in fetched if f["status"] != "ok")),
        },
        "page_dates": dict(Counter(classify_page_date(f.get("date"), run_date) for f in fetched)),
        "control": {"returned": len(control_result["results"]), "error": control_result["error"],
                    "matched_g1": match_gold(control_gold, control_keys)},
        "gold": gold,
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "candidates.jsonl", "w", encoding="utf-8") as f:
        for entry in candidates.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect last week's candidates for a domain")
    parser.add_argument("--domain", default="embodied")
    parser.add_argument("--out", default=None, help="default: out/<domain>/latest")
    parser.add_argument("--state", default=None, help="default: state/<domain>")
    parser.add_argument("--backend", default=None)
    args = parser.parse_args()
    summary = run(f"domains/{args.domain}", args.out or f"out/{args.domain}/latest",
                  args.state or f"state/{args.domain}", args.backend)
    print(json.dumps({k: summary[k] for k in ("run_date", "requests", "stages", "fetch", "control", "gold")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
