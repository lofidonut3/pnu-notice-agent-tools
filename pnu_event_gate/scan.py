from __future__ import annotations

import hashlib
from typing import Any

from .events import (
    apply_filters,
    collapse_duplicate_events,
    compact_event,
    enrich_events_from_archives,
    event_cursor,
    feed_latest_cursor,
    fetch_events_feed,
    now_iso,
    select_archive_events,
    select_new_events,
    validate_feed,
)
from .matcher import MatchResult, match_event
from .state import Cursor
from .store import NoticeStore


def run_scan(
    *,
    store: NoticeStore,
    events_url: str,
    include_baseline: bool = False,
    no_archive: bool = False,
    no_archive_catchup: bool = False,
    no_dedupe: bool = False,
    limit: int | None = None,
) -> dict[str, Any] | None:
    scanned_at = now_iso()
    result = fetch_events_feed(events_url, store.http_headers())
    if result.status_code == 304:
        store.update_scan_state(
            cursor=store.scan_cursor(),
            checked_at=scanned_at,
            feed_generated_at=None,
            etag=result.etag,
            last_modified=result.last_modified,
            status="not_modified",
            warnings=[],
        )
        store.commit()
        return None

    if result.feed is None:
        return None

    validate_feed(result.feed)
    previous_cursor = store.scan_cursor()
    if previous_cursor.is_empty() and not include_baseline:
        latest = feed_latest_cursor(result.feed)
        store.update_scan_state(
            cursor=latest,
            checked_at=scanned_at,
            feed_generated_at=result.feed.get("generated_at"),
            etag=result.etag,
            last_modified=result.last_modified,
            status="baseline",
            warnings=[],
        )
        store.commit()
        return None

    selection = select_new_events(result.feed, previous_cursor)
    warnings = list(selection.warnings)
    cursor_status = selection.status
    if selection.status == "archive_required" and not no_archive_catchup:
        try:
            selection = select_archive_events(result.feed, events_url, previous_cursor)
            warnings = list(selection.warnings)
            cursor_status = selection.status
        except Exception as error:  # noqa: BLE001 - scan should preserve feed progress info.
            warnings = [*warnings, f"archive catch-up failed: {error}"]

    filtered_events = apply_filters(
        selection.events,
        source_ids=None,
        source_categories=None,
        topics=None,
        event_types=None,
        limit=limit,
    )
    dedupe_selection = (
        collapse_duplicate_events(filtered_events)
        if not no_dedupe
        else None
    )
    selected_events = dedupe_selection.events if dedupe_selection else filtered_events
    if selected_events and not no_archive:
        archive_enrichment = enrich_events_from_archives(selected_events, events_url)
        selected_events = archive_enrichment.events
        warnings = [*warnings, *archive_enrichment.warnings]

    compact_events = [compact_event(event) for event in selected_events]
    profiles = store.list_profiles()
    candidates = enqueue_candidates(
        store=store,
        profiles=[profile["profile"] for profile in profiles],
        events=compact_events,
        now=scanned_at,
    )

    next_cursor = (
        event_cursor(filtered_events[-1])
        if filtered_events
        else previous_cursor
    )
    store.update_scan_state(
        cursor=next_cursor,
        checked_at=scanned_at,
        feed_generated_at=result.feed.get("generated_at"),
        etag=result.etag,
        last_modified=result.last_modified,
        status="ok",
        warnings=warnings,
    )
    store.commit()

    if not candidates:
        return None

    return {
        "type": "pnu_notice_candidates",
        "events_url": events_url,
        "scanned_at": scanned_at,
        "feed_generated_at": result.feed.get("generated_at"),
        "cursor_status": cursor_status,
        "previous_cursor": previous_cursor.to_json(),
        "next_cursor": next_cursor.to_json(),
        "input_event_count": len(selection.events),
        "selected_event_count": len(selected_events),
        "candidate_count": len(candidates),
        "warnings": warnings,
        "candidates": candidates,
    }


def enqueue_candidates(
    *,
    store: NoticeStore,
    profiles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for event in events:
        for profile in profiles:
            result = match_event(profile, event)
            if not result.matched:
                continue
            candidate = build_candidate(profile=profile, event=event, result=result, now=now)
            inserted = store.insert_candidate(candidate)
            if inserted:
                candidates = [*candidates, candidate_payload(candidate)]
    return candidates


def build_candidate(
    *,
    profile: dict[str, Any],
    event: dict[str, Any],
    result: MatchResult,
    now: str,
) -> dict[str, Any]:
    watch_id = str(profile["id"])
    revision = str(profile.get("revision") or "1")
    event_id = str(event.get("event_id") or "")
    candidate_id = stable_candidate_id(watch_id, revision, event_id)
    return {
        "candidate_id": candidate_id,
        "watch_id": watch_id,
        "profile_revision": revision,
        "event_id": event_id,
        "notice_id": event.get("notice_id"),
        "seen_at": event.get("seen_at"),
        "source_id": event.get("source_id"),
        "same_notice_group_id": event.get("same_notice_group_id"),
        "status": "pending",
        "score": result.score,
        "action": result.action,
        "match": result.matched_reasons,
        "event": event,
        "created_at": now,
        "updated_at": now,
    }


def candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "watch_id": candidate["watch_id"],
        "profile_revision": candidate["profile_revision"],
        "event_id": candidate["event_id"],
        "notice_id": candidate.get("notice_id"),
        "seen_at": candidate.get("seen_at"),
        "source_id": candidate.get("source_id"),
        "same_notice_group_id": candidate.get("same_notice_group_id"),
        "status": candidate["status"],
        "score": candidate["score"],
        "action": candidate["action"],
        "matched": candidate["match"],
        "event": candidate["event"],
    }


def stable_candidate_id(watch_id: str, revision: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{watch_id}:{revision}:{event_id}".encode("utf-8")).hexdigest()
    return f"cand_{digest[:20]}"


def cursor_from_candidate_event(candidate: dict[str, Any]) -> Cursor:
    event = candidate.get("event") or {}
    return Cursor(
        last_seen_event_id=event.get("event_id"),
        last_seen_at=event.get("seen_at"),
    )
