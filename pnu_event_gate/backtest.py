from __future__ import annotations

from typing import Any

from .events import compact_event
from .matcher import match_event


def run_backtest(profile: dict[str, Any], archives: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    scanned_count = 0
    for archive in archives:
        for event in archive_events_for_backtest(archive):
            scanned_count += 1
            result = match_event(profile, event)
            if not result.matched:
                continue
            matches = [
                *matches,
                {
                    "event_id": event.get("event_id"),
                    "notice_id": event.get("notice_id"),
                    "title": event.get("title"),
                    "url": event.get("url"),
                    "score": result.score,
                    "action": result.action,
                    "matched": result.matched_reasons,
                    "event": event,
                },
            ]
    return {
        "type": "pnu_notice_backtest",
        "watch_id": profile["id"],
        "profile_revision": profile["revision"],
        "scanned_event_count": scanned_count,
        "match_count": len(matches),
        "matches": matches,
    }


def archive_events_for_backtest(archive: dict[str, Any]) -> list[dict[str, Any]]:
    items = {
        str(item.get("id")): item
        for item in archive.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    if archive.get("events"):
        return [
            compact_event({
                **event,
                "item": items.get(str(event.get("archive_item_id") or event.get("notice_id")), {}),
            })
            for event in archive.get("events", [])
            if isinstance(event, dict)
        ]
    return [compact_event(event_from_archive_item(item)) for item in items.values()]


def event_from_archive_item(item: dict[str, Any]) -> dict[str, Any]:
    pnu = item.get("_pnu") or {}
    notice_id = str(item.get("id") or item.get("url") or "")
    return {
        "event_id": f"archive:{notice_id}",
        "event_type": "added",
        "notice_id": notice_id,
        "source_id": pnu.get("source_id"),
        "source_name": pnu.get("source_name"),
        "source_category": pnu.get("source_category"),
        "source_tags": pnu.get("source_tags") or pnu.get("tags") or [],
        "seen_at": pnu.get("fetched_at") or item.get("date_modified") or item.get("date_published"),
        "published_at": pnu.get("published_at") or item.get("date_published"),
        "title": item.get("title"),
        "url": item.get("url"),
        "topics": pnu.get("topics") or [],
        "same_notice_group_id": pnu.get("same_notice_group_id"),
        "canonical_item_id": pnu.get("canonical_item_id") or notice_id,
        "is_canonical": pnu.get("is_canonical", True),
        "same_notice_source_ids": pnu.get("same_notice_source_ids") or [],
        "archive_item_id": notice_id,
        "item": item,
    }
