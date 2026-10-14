"""Locked verifiers for radar tasks. Exit 0 iff the acceptance criteria in the plan hold."""

import json
import sys
from pathlib import Path


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


def main(kind: str, path: str) -> int:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = {"probe": check_probe}[kind](report)
    for item in report.get("gold", []):
        dates = [p["date"] for p in item["pages"] if p["date"]]
        print(f"{item['id']}: gold date {item['date']}, page dates {dates or 'undated'}")
    for line in problems:
        print(f"FAIL {line}")
    print("PASS" if not problems else "FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
