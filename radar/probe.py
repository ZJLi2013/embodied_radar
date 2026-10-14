"""t1 probe: is the search backend usable from this machine, and can gold pages be fetched?"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import requests
import trafilatura
import yaml
from ddgs import DDGS

from radar.urls import normalize

QUERIES = [
    ("entity", '"Google DeepMind" robot'),
    ("open", "robot foundation model vision-language-action model new release"),
    ("control", "Gemini 4 Argon for Robotics"),
]
PROBE_GOLD = ("G1", "G2")
UA = {"User-Agent": "Mozilla/5.0 (compatible; embodied-radar/0.1)"}
BACKEND = os.environ.get("RADAR_BACKEND", "ddgs")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")


def _search_urls(query: str, max_results: int) -> list[str]:
    if BACKEND == "searxng":
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "time_range": "week", "language": "en"},
            timeout=60,
        )
        resp.raise_for_status()
        return [r.get("url", "") for r in resp.json().get("results", [])][:max_results]
    return [r.get("href", "") for r in DDGS().text(query, timelimit="w", max_results=max_results) or []]


def search(query: str, max_results: int) -> dict:
    try:
        urls = _search_urls(query, max_results)
        return {"count": len(urls), "error": None, "urls": urls}
    except Exception as exc:  # backend errors are the measurement here, not a crash
        return {"count": 0, "error": f"{type(exc).__name__}: {exc}"[:300], "urls": []}


def fetch(url: str) -> dict:
    try:
        resp = requests.get(f"https://{url}", headers=UA, timeout=30)
    except requests.RequestException as exc:
        return {"url": url, "status": None, "chars": 0, "date": None, "error": type(exc).__name__}
    doc = trafilatura.extract(resp.text, url=resp.url, output_format="json", with_metadata=True)
    data = json.loads(doc) if doc else {}
    return {
        "url": url,
        "status": resp.status_code,
        "chars": len(data.get("text") or ""),
        "date": data.get("date"),
        "error": None,
    }


def main(domain_dir: str, out_path: str) -> None:
    gold = {g["id"]: g for g in yaml.safe_load(Path(domain_dir, "gold.yaml").read_text(encoding="utf-8"))}
    g1_aliases = {normalize(a) for a in gold["G1"]["aliases"]}

    queries = []
    for kind, query in QUERIES:
        result = search(query, 10)
        retried = False
        if kind != "control" and result["error"] is None and result["count"] < 5:
            result = search(query, 20)
            retried = True
        queries.append({"kind": kind, "query": query, "retried": retried, **result})

    control = next(q for q in queries if q["kind"] == "control")
    control_matched = sorted({normalize(u) for u in control["urls"]} & g1_aliases)

    gold_pages = []
    for gid in PROBE_GOLD:
        item = gold[gid]
        pages = [fetch(alias) for alias in item["aliases"]]
        gold_pages.append({"id": gid, "date": str(item["date"]), "pages": pages})

    report = {
        "run_date": date.today().isoformat(),
        "backend": BACKEND,
        "queries": queries,
        "control": {"returned": control["count"], "matched_g1": control_matched},
        "gold": gold_pages,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("run_date", "control")}, ensure_ascii=False))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["domains/embodied", "out/probe/probe.json"]))
