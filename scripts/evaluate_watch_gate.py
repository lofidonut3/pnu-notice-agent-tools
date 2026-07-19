from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pnu_event_gate.matcher import match_event  # noqa: E402
from pnu_event_gate.profiles import normalize_profile  # noqa: E402


DEFAULT_CASES = ROOT / "evaluation" / "watch_gate_cases.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate watch gating on a real-feed snapshot.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    for case in cases:
        profile = normalize_profile(
            {
                "id": case["id"],
                "request": case["request"],
                "phrases": case.get("phrases") or [],
                "positive_terms": case.get("positive_terms") or [],
                "thresholds": {"candidate": 2, "invoke_agent": 2},
            }
        )
        event = {
            "event_id": "snapshot:" + case["id"],
            "event_type": case.get("event_type") or "added",
            "source_id": case["source_id"],
            "title": case["title"],
            "snippet": "",
            "attachments": [],
            "topics": [],
            "tags": [],
            "source_tags": [],
        }
        match = match_event(profile, event)
        expected = bool(case["expected_match"])
        results.append(
            {
                "id": case["id"],
                "expected_match": expected,
                "actual_match": match.matched,
                "passed": match.matched == expected,
                "score": match.score,
                "threshold": match.threshold,
                "reasons": match.matched_reasons,
            }
        )

    positives = [item for item in results if item["expected_match"]]
    negatives = [item for item in results if not item["expected_match"]]
    true_positives = sum(item["actual_match"] for item in positives)
    false_positives = sum(item["actual_match"] for item in negatives)
    report = {
        "type": "pnu_notice_watch_gate_quality_report",
        "snapshot_date": "2026-07-19",
        "case_count": len(results),
        "passed_count": sum(item["passed"] for item in results),
        "recall": ratio(true_positives, len(positives)),
        "precision": ratio(true_positives, true_positives + false_positives),
        "results": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed_count"] == report["case_count"] else 1


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


if __name__ == "__main__":
    raise SystemExit(main())
