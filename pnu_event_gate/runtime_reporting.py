"""Allowlisted operational summaries; never include profiles or message contents."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
from typing import Any


def cycle_summary(payload: dict[str, Any]) -> dict[str, Any]:
    processed = payload.get("processed") or []
    deliveries = payload.get("deliveries") or []
    failures = sum(bool(item.get("error")) for item in [*processed, *deliveries])
    return {
        "type": payload.get("type"),
        "completed_at": payload.get("completed_at"),
        "status": "degraded" if failures else "ok",
        "candidate_count": payload.get("candidate_count", 0),
        "processed_status_counts": dict(Counter(str(item.get("status")) for item in processed)),
        "delivery_status_counts": dict(Counter(str(item.get("status")) for item in deliveries)),
        "error_count": failures,
        "scan": {key: (payload.get("scan") or {}).get(key) for key in [
            "cursor_status", "input_event_count", "selected_event_count", "candidate_count"
        ]},
    }


def request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    processed = payload.get("processed") or []
    return {
        "type": payload.get("type"),
        "processed_count": payload.get("processed_count", 0),
        "error_count": sum(bool(item.get("error")) for item in processed),
    }


def health_summary(payload: dict[str, Any]) -> dict[str, Any]:
    # This smaller document is also safe to publish as a daily operations record.
    return {
        "schema_version": 1,
        "checked_at": payload.get("checked_at"),
        "status": payload.get("status"),
        "feed_generated_at": payload.get("feed_generated_at"),
        "latest_cycle_at": payload.get("latest_cycle_at"),
        "issues": [
            {key: issue.get(key) for key in ["fingerprint", "component", "severity"]}
            for issue in payload.get("issues", [])
        ],
    }


def write_health_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health_summary(payload), indent=2) + "\n", encoding="utf-8")
