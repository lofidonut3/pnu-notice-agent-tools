from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .state import Cursor


DEFAULT_EVENTS_URL = "https://lofidonut3.github.io/pnu-public-notice-feed/events.json"


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    feed: dict[str, Any] | None
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class CursorSelection:
    events: list[dict[str, Any]]
    status: str
    warnings: list[str]


@dataclass(frozen=True)
class ArchiveEnrichment:
    events: list[dict[str, Any]]
    warnings: list[str]


@dataclass(frozen=True)
class DedupeSelection:
    events: list[dict[str, Any]]
    suppressed_count: int


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def fetch_events_feed(events_url: str, headers: dict[str, str] | None = None) -> FetchResult:
    parsed = urllib.parse.urlparse(events_url)
    if parsed.scheme in ("http", "https"):
        return _fetch_http(events_url, headers or {})
    if parsed.scheme == "file":
        return _read_file(Path(urllib.request.url2pathname(parsed.path)))
    return _read_file(Path(events_url))


def fetch_json_resource(resource_url: str) -> dict[str, Any]:
    result = fetch_events_feed(resource_url)
    if result.feed is None:
        raise ValueError(f"resource returned no JSON body: {resource_url}")
    return result.feed


def _fetch_http(events_url: str, headers: dict[str, str]) -> FetchResult:
    request = urllib.request.Request(events_url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return FetchResult(
                status_code=response.status,
                feed=json.loads(body),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return FetchResult(status_code=304, feed=None)
        raise


def _read_file(path: Path) -> FetchResult:
    return FetchResult(
        status_code=200,
        feed=json.loads(path.read_text(encoding="utf-8")),
    )


def validate_feed(feed: dict[str, Any]) -> None:
    if not isinstance(feed.get("events"), list):
        raise ValueError("events feed must contain an events array")
    if "latest_event_id" not in feed:
        raise ValueError("events feed must contain latest_event_id")


def feed_latest_cursor(feed: dict[str, Any]) -> Cursor:
    return Cursor(
        last_seen_event_id=feed.get("latest_event_id"),
        last_seen_at=feed.get("latest_seen_at"),
    )


def event_cursor(event: dict[str, Any]) -> Cursor:
    return Cursor(
        last_seen_event_id=event.get("event_id"),
        last_seen_at=event.get("seen_at"),
    )


def select_new_events(feed: dict[str, Any], cursor: Cursor) -> CursorSelection:
    events = list(feed.get("events") or [])
    if cursor.is_empty():
        return CursorSelection(events=events, status="no_cursor", warnings=[])

    if cursor.last_seen_event_id:
        for index, event in enumerate(events):
            if event.get("event_id") == cursor.last_seen_event_id:
                return CursorSelection(events=events[index + 1 :], status="event_id", warnings=[])

    if cursor.last_seen_at:
        selected = [
            event
            for event in events
            if str(event.get("seen_at") or "") > cursor.last_seen_at
        ]
        oldest_seen_at = feed.get("oldest_seen_at")
        if oldest_seen_at and cursor.last_seen_at < oldest_seen_at:
            return CursorSelection(
                events=selected,
                status="archive_required",
                warnings=[
                    "local cursor is older than the events.json window; use monthly archive files to avoid missing events",
                ],
            )
        return CursorSelection(events=selected, status="seen_at", warnings=[])

    return CursorSelection(
        events=events,
        status="stale_cursor",
        warnings=[
            "last_seen_event_id was not found in events.json and last_seen_at is unavailable; output may include already handled events",
        ],
    )


def select_archive_events(
    feed: dict[str, Any],
    events_url: str,
    cursor: Cursor,
) -> CursorSelection:
    if cursor.is_empty():
        return CursorSelection(events=[], status="no_cursor", warnings=[])

    index_url = resolve_related_url(events_url, str(feed.get("index_url") or "./index.json"))
    index = fetch_json_resource(index_url)
    archive_entries = index.get("archives", {}).get("months", [])
    archive_events: list[dict[str, Any]] = []

    for entry in archive_entries:
        if int(entry.get("event_count") or 0) <= 0:
            continue
        archive_url = resolve_related_url(index_url, str(entry.get("url") or ""))
        archive_doc = fetch_json_resource(archive_url)
        archive_events = [
            *archive_events,
            *[compact_event(event) for event in archive_doc.get("events", [])],
        ]

    events = sorted(archive_events, key=event_sort_key)
    if cursor.last_seen_event_id:
        for index, event in enumerate(events):
            if event.get("event_id") == cursor.last_seen_event_id:
                return CursorSelection(
                    events=events[index + 1 :],
                    status="archive_event_id",
                    warnings=[],
                )

    if cursor.last_seen_at:
        return CursorSelection(
            events=[
                event
                for event in events
                if str(event.get("seen_at") or "") > cursor.last_seen_at
            ],
            status="archive_seen_at",
            warnings=[],
        )

    return CursorSelection(
        events=events,
        status="archive_stale_cursor",
        warnings=[
            "last_seen_event_id was not found in monthly archive files and last_seen_at is unavailable; output may include already handled events",
        ],
    )


def event_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return str(event.get("seen_at") or ""), str(event.get("event_id") or "")


def apply_filters(
    events: list[dict[str, Any]],
    *,
    source_ids: set[str] | None,
    source_categories: set[str] | None = None,
    topics: set[str] | None = None,
    event_types: set[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    filtered = [
        event
        for event in events
        if (not source_ids or event.get("source_id") in source_ids)
        and (
            not source_categories
            or event.get("source_category") in source_categories
        )
        and (
            not topics
            or topics.intersection({str(topic) for topic in event.get("topics", [])})
        )
        and (not event_types or event.get("event_type") in event_types)
    ]
    if limit is not None:
        return filtered[:limit]
    return filtered


def collapse_duplicate_events(events: list[dict[str, Any]]) -> DedupeSelection:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    passthrough: list[tuple[int, dict[str, Any]]] = []

    for index, event in enumerate(events):
        group_id = event.get("same_notice_group_id")
        if not group_id:
            passthrough = [*passthrough, (index, event)]
            continue
        key = str(group_id)
        grouped = {
            **grouped,
            key: [*grouped.get(key, []), (index, event)],
        }

    selected: list[tuple[int, dict[str, Any]]] = list(passthrough)
    suppressed_count = 0
    for group_events in grouped.values():
        if len(group_events) == 1:
            selected = [*selected, group_events[0]]
            continue

        canonical = sorted(group_events, key=dedupe_event_rank)[0]
        suppressed_count += len(group_events) - 1
        selected = [*selected, canonical]

    return DedupeSelection(
        events=[event for _index, event in sorted(selected, key=lambda pair: pair[0])],
        suppressed_count=suppressed_count,
    )


def dedupe_event_rank(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
    index, event = pair
    notice_id = str(event.get("notice_id") or "")
    canonical_item_id = str(event.get("canonical_item_id") or "")
    return (
        0 if event.get("is_canonical") is True else 1,
        0 if canonical_item_id and notice_id == canonical_item_id else 1,
        index,
    )


def enrich_events_from_archives(
    events: list[dict[str, Any]],
    events_url: str,
) -> ArchiveEnrichment:
    archive_cache: dict[str, dict[str, dict[str, Any]]] = {}
    warnings: list[str] = []
    enriched_events: list[dict[str, Any]] = []

    for event in events:
        archive_file = event.get("archive_file")
        archive_item_id = event.get("archive_item_id") or event.get("notice_id")
        if not archive_file or not archive_item_id:
            warnings = [
                *warnings,
                f"event has no archive lookup fields: {event.get('event_id')}",
            ]
            enriched_events = [*enriched_events, event]
            continue

        archive_url = resolve_related_url(events_url, str(archive_file))
        try:
            if archive_url not in archive_cache:
                archive_doc = fetch_json_resource(archive_url)
                archive_cache = {
                    **archive_cache,
                    archive_url: archive_items_by_id(archive_doc),
                }
            item = archive_cache[archive_url].get(str(archive_item_id))
        except Exception as error:  # noqa: BLE001 - archive lookup should not drop events.
            warnings = [
                *warnings,
                f"failed to fetch archive metadata for {event.get('event_id')}: {error}",
            ]
            enriched_events = [*enriched_events, event]
            continue

        if not item:
            warnings = [
                *warnings,
                f"archive item not found for {event.get('event_id')}: {archive_item_id}",
            ]
            enriched_events = [*enriched_events, event]
            continue

        enriched_events = [
            *enriched_events,
            {
                **event,
                "item": item,
            },
        ]

    return ArchiveEnrichment(events=enriched_events, warnings=warnings)


def archive_items_by_id(archive_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in archive_doc.get("items", [])
        if item.get("id")
    }


def resolve_related_url(base_url: str, related_url: str) -> str:
    if not related_url:
        return related_url

    related = urllib.parse.urlparse(related_url)
    if related.scheme:
        return related_url

    base = urllib.parse.urlparse(base_url)
    if base.scheme in ("http", "https"):
        return urllib.parse.urljoin(base_url, related_url)
    if base.scheme == "file":
        base_path = Path(urllib.request.url2pathname(base.path))
        return (base_path.parent / related_url).resolve().as_uri()

    return str((Path(base_url).parent / related_url).resolve())


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item") or {}
    pnu = item.get("_pnu") or {}
    source_id = event.get("source_id") or pnu.get("source_id")
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "notice_id": event.get("notice_id"),
        "source_id": source_id,
        "source_name": event.get("source_name") or pnu.get("source_name"),
        "source_category": event.get("source_category") or pnu.get("source_category"),
        "source_tags": event.get("source_tags") or pnu.get("source_tags") or pnu.get("tags") or [],
        "seen_at": event.get("seen_at"),
        "published_at": event.get("published_at"),
        "title": event.get("title"),
        "url": event.get("url"),
        "topics": event.get("topics") or pnu.get("topics") or [],
        "same_notice_group_id": (
            event.get("same_notice_group_id")
            if "same_notice_group_id" in event
            else pnu.get("same_notice_group_id")
        ),
        "canonical_item_id": (
            event.get("canonical_item_id")
            or pnu.get("canonical_item_id")
            or event.get("notice_id")
        ),
        "is_canonical": (
            event.get("is_canonical")
            if "is_canonical" in event
            else pnu.get("is_canonical", True)
        ),
        "same_notice_source_ids": (
            event.get("same_notice_source_ids")
            or pnu.get("same_notice_source_ids")
            or ([source_id] if source_id else [])
        ),
        "content_hash": event.get("content_hash"),
        "previous_content_hash": event.get("previous_content_hash"),
        "archive_file": event.get("archive_file"),
        "archive_item_id": event.get("archive_item_id"),
        "snippet": pnu.get("snippet") or item.get("summary"),
        "content_access": pnu.get("content_access"),
        "attachments": _compact_attachments(pnu.get("attachments") or []),
        "tags": pnu.get("tags") or [],
    }


def _compact_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for attachment in attachments:
        compacted.append(
            {
                "name": attachment.get("name"),
                "url": attachment.get("download_url") or attachment.get("url"),
                "type": attachment.get("type"),
                "media_type": attachment.get("media_type"),
                "file_extension": attachment.get("file_extension"),
            }
        )
    return compacted
