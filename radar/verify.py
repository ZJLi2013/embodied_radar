"""Locked verifiers for radar tasks. Exit 0 iff the acceptance criteria in the plan hold."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml


def check_channels(report: dict, run_meta: dict | None, gold_path: str = "evals/embodied/gold.yaml") -> list[str]:
    problems = []
    run_date = date.fromisoformat(report["run_date"])
    start = run_date - timedelta(days=7)
    gold = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8"))
    if not any(start <= g["date"] <= run_date for g in gold):
        problems.append(f"no gold item falls in [{start}, {run_date}]")

    if not run_meta or run_meta.get("conclusion") != "success":
        problems.append(f"workflow (incl. pytest) did not succeed: {run_meta and run_meta.get('conclusion')}")

    rows = [(eid, r) for eid, e in report["entities"].items() for r in e["channels"]]
    bad = [f"{eid}:{r['type']}:{r['target']}:{r['status']}:{r['error']}" for eid, r in rows
           if r["status"] in ("failed", "broken")]
    if len(bad) > 0.1 * len(rows):
        problems.append(f"failed+broken {len(bad)}/{len(rows)} > 10%: {bad}")
    for eid, e in report["entities"].items():
        if not any(r["status"] in ("no_update", "updated") for r in e["channels"]):
            problems.append(f"{eid}: no channel returned data")

    for eid, r in rows:
        if r["type"] != "page" or r["status"] == "failed":
            continue
        if not r.get("snapshot") or not Path(r["snapshot"]).exists():
            problems.append(f"{eid}: page {r['target']} wrote no snapshot ({r.get('snapshot')})")
        if r.get("first_run"):
            for item in r["new"]:
                d = item.get("page_date")
                if not d or not start <= date.fromisoformat(d[:10]) <= run_date:
                    problems.append(f"{eid}: first-run page item {item['url']} has page date {d} outside window")

    for g in report["gold"]:
        if not g["hit"]:
            problems.append(f"{g['id']}: not among new items")
        elif g.get("expect") and [g["expect"]["entity"], g["expect"]["type"]] not in [list(v) for v in g["via"]]:
            problems.append(f"{g['id']}: hit via {g['via']}, expected {g['expect']}")
    return problems


def main(kind: str, path: str) -> int:
    if kind != "channels":
        raise SystemExit(f"unknown check {kind!r}")
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    meta_path = Path(path).with_name("run.json")
    problems = check_channels(report, json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None)
    print(f"run {report['run_date']} totals {report['totals']} by_type {report['by_type']} gold {report['gold']}")
    for line in problems:
        print(f"FAIL {line}")
    print("PASS" if not problems else "FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
