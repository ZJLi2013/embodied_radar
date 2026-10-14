"""Locked verifiers for radar tasks. Exit 0 iff the acceptance criteria in the plan hold."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml


def check_probe(report: dict) -> list[str]:
    problems = []
    for q in report["queries"]:
        if q["error"]:
            problems.append(f"{q['kind']} query raised: {q['error']}")
        elif q["kind"] != "control" and q["count"] < 1:
            problems.append(f"{q['kind']} query returned 0 results")
    if not report["control"]["matched_g1"]:
        problems.append("control query did not return any G1 alias")
    for item in report["gold"]:
        best = max((p["chars"] for p in item["pages"]), default=0)
        if best < 1000:
            problems.append(f"{item['id']}: no alias fetched with >= 1000 chars (best {best})")
    return problems


def check_collect(report: dict, gold_path: str = "domains/embodied/gold.yaml") -> list[str]:
    problems = []
    run_date = date.fromisoformat(report["run_date"])
    gold = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8"))
    expected = {g["id"] for g in gold if run_date - timedelta(days=7) <= g["date"] <= run_date}
    if not expected:
        problems.append(f"no gold item falls in [{run_date - timedelta(days=7)}, {run_date}]; run between 10-13 and 10-19")

    req = report["requests"]
    if req["failed"] > 0.2 * req["total"]:
        problems.append(f"failed requests {req['failed']}/{req['total']} > 20% ({req['errors']})")
    if report["stages"]["unique"] < 30:
        problems.append(f"unique candidates {report['stages']['unique']} < 30 (stages {report['stages']})")
    f = report["fetch"]
    if f["attempted"] == 0 or f["ok"] < 0.7 * f["attempted"]:
        problems.append(f"fetch ok {f['ok']}/{f['attempted']} < 70% ({f['reasons']})")

    found = {g["id"]: g for g in report["gold"]}
    for gid in sorted(expected):
        item = found.get(gid)
        if item is None:
            problems.append(f"{gid}: in the window but missing from summary.gold")
        elif not item["hit"]:
            problems.append(f"{gid}: not among candidates")
        elif item["chars"] < 1000:
            problems.append(f"{gid}: hit {item['url']} but body {item['chars']} chars < 1000")
    return problems


def main(kind: str, path: str) -> int:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = {"probe": check_probe, "collect": check_collect}[kind](report)
    if kind == "probe":
        for item in report.get("gold", []):
            dates = [p["date"] for p in item["pages"] if p["date"]]
            print(f"{item['id']}: gold date {item['date']}, page dates {dates or 'undated'}")
    else:
        print(f"run {report['run_date']} backend {report['backend']} stages {report['stages']} "
              f"page_dates {report['page_dates']} control {report['control']}")
    for line in problems:
        print(f"FAIL {line}")
    print("PASS" if not problems else "FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
