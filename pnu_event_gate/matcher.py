from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    suppressed: bool
    score: int
    action: str
    matched_reasons: dict[str, list[dict[str, Any]]]
    threshold: int


def match_event(profile: dict[str, Any], event: dict[str, Any]) -> MatchResult:
    fields = event_text_fields(event)
    reasons = {
        "phrases": [],
        "positive_terms": [],
        "negative_terms": [],
        "source_hints": [],
        "attachment_hints": [],
    }
    score = 0

    for phrase in profile.get("phrases", []):
        for field, weight in (("title", 6), ("snippet", 4), ("attachment_names", 5)):
            if contains(fields.get(field, ""), phrase):
                reasons["phrases"] = [
                    *reasons["phrases"],
                    {"term": phrase, "field": field, "score": weight},
                ]
                score += weight
                break

    for term in profile.get("positive_terms", []):
        for field, weight in (("title", 3), ("snippet", 2), ("attachment_names", 2)):
            if contains(fields.get(field, ""), term):
                reasons["positive_terms"] = [
                    *reasons["positive_terms"],
                    {"term": term, "field": field, "score": weight},
                ]
                score += weight
                break

    for term in profile.get("attachment_hints", []):
        if contains(fields.get("attachment_names", ""), term):
            reasons["attachment_hints"] = [
                *reasons["attachment_hints"],
                {"term": term, "field": "attachment_names", "score": 3},
            ]
            score += 3

    source_score, source_reasons = match_source_hints(profile, event)
    score += source_score
    reasons = {
        **reasons,
        "source_hints": source_reasons,
    }

    for term in profile.get("negative_terms", []):
        for field in ("title", "snippet", "attachment_names"):
            if contains(fields.get(field, ""), term):
                reasons["negative_terms"] = [
                    *reasons["negative_terms"],
                    {"term": term, "field": field, "score": -999},
                ]
                break

    suppressed = bool(reasons["negative_terms"])
    threshold = int((profile.get("thresholds") or {}).get("candidate", 1))
    matched = not suppressed and score >= threshold
    return MatchResult(
        matched=matched,
        suppressed=suppressed,
        score=score,
        action="invoke_agent" if matched else "skip",
        matched_reasons=reasons,
        threshold=threshold,
    )


def event_text_fields(event: dict[str, Any]) -> dict[str, str]:
    attachments = event.get("attachments") or []
    names = [
        str(attachment.get("name") or "")
        for attachment in attachments
        if isinstance(attachment, dict)
    ]
    return {
        "title": str(event.get("title") or ""),
        "snippet": str(event.get("snippet") or ""),
        "attachment_names": " ".join(names),
    }


def match_source_hints(
    profile: dict[str, Any],
    event: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    hints = profile.get("source_hints") or {}
    score = 0
    reasons: list[dict[str, Any]] = []
    checks = [
        ("source_ids", "source_id", 4, event.get("source_id")),
        ("source_categories", "source_category", 4, event.get("source_category")),
    ]
    for hint_key, field, weight, value in checks:
        if value and str(value) in {str(item) for item in hints.get(hint_key, [])}:
            reasons = [*reasons, {"value": str(value), "field": field, "score": weight}]
            score += weight

    event_topics = {str(topic) for topic in event.get("topics", [])}
    for topic in hints.get("topics", []):
        if str(topic) in event_topics:
            reasons = [*reasons, {"value": str(topic), "field": "topics", "score": 3}]
            score += 3

    event_tags = {str(tag) for tag in event.get("tags", [])}
    event_tags.update(str(tag) for tag in event.get("source_tags", []))
    for tag in hints.get("tags", []):
        if str(tag) in event_tags:
            reasons = [*reasons, {"value": str(tag), "field": "tags", "score": 2}]
            score += 2

    return score, reasons


def contains(value: str, needle: str) -> bool:
    normalized_value = normalize(value)
    normalized_needle = normalize(needle)
    compact_value = re.sub(r"\s+", "", normalized_value)
    compact_needle = re.sub(r"\s+", "", normalized_needle)
    return normalized_needle in normalized_value or compact_needle in compact_value


def normalize(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", value).casefold(),
    ).strip()
