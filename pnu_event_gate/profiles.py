from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_profile(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    profile = json.loads(raw)
    return normalize_profile(profile)


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("watch profile must be a JSON object")
    watch_id = profile.get("id") or profile.get("watch_id")
    if not watch_id:
        raise ValueError("watch profile requires id")
    request = profile.get("request")
    if not request:
        raise ValueError("watch profile requires request")

    source_hints = profile.get("source_hints") or {}
    if isinstance(source_hints, list):
        source_hints = {"source_ids": source_hints}

    thresholds = profile.get("thresholds") or {}
    candidate_threshold = thresholds.get("candidate", profile.get("candidate_threshold", 1))
    invoke_threshold = thresholds.get("invoke_agent", profile.get("resolve_threshold", candidate_threshold))

    return {
        **profile,
        "schema_version": profile.get("schema_version") or "watch_profile.v1",
        "id": str(watch_id),
        "revision": str(profile.get("revision") or "1"),
        "enabled": bool(profile.get("enabled", True)),
        "type": str(profile.get("type") or "recurring"),
        "request": str(request),
        "positive_terms": _strings(profile.get("positive_terms")),
        "phrases": _strings(profile.get("phrases")),
        "negative_terms": _strings(profile.get("negative_terms")),
        "attachment_hints": _strings(profile.get("attachment_hints")),
        "source_hints": {
            "source_ids": _strings(source_hints.get("source_ids")),
            "source_categories": _strings(source_hints.get("source_categories")),
            "topics": _strings(source_hints.get("topics")),
            "tags": _strings(source_hints.get("tags")),
        },
        "thresholds": {
            "candidate": int(candidate_threshold),
            "invoke_agent": int(invoke_threshold),
        },
    }


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
