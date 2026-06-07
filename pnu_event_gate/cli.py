from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .content import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_TEXT_CHARS,
    DEFAULT_MAX_TOTAL_BYTES,
    build_direct_notice,
    load_notice_input,
    resolve_notice_materials,
)
from .events import (
    DEFAULT_EVENTS_URL,
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
from .state import Cursor, EventGateState


DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / ".event-gate-state.json"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or raw_args[0] not in {"check", "ack", "resolve", "-h", "--help"}:
        raw_args = ["check", *raw_args]
    args = parser.parse_args(raw_args)

    if args.command == "ack":
        return _ack(args)
    if args.command == "resolve":
        return _resolve(args)
    return _check(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gate PNU public notice feed events for local AI agents.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = False

    check = subparsers.add_parser("check", help="Print new feed events after the local cursor.")
    _add_common_args(check)
    check.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        help="Source id to include. Can be repeated. Defaults to all sources.",
    )
    check.add_argument(
        "--source-category",
        action="append",
        dest="source_categories",
        help="Source category to include. Can be repeated. Defaults to all categories.",
    )
    check.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="Topic hint to include. Can be repeated. Defaults to all topics.",
    )
    check.add_argument(
        "--event-type",
        action="append",
        choices=["added", "updated"],
        dest="event_types",
        help="Event type to include. Can be repeated. Defaults to all types.",
    )
    check.add_argument("--limit", type=int, help="Maximum events to output.")
    check.add_argument(
        "--include-baseline",
        action="store_true",
        help="Output current feed events on the first run instead of only setting a baseline.",
    )
    check.add_argument(
        "--advance",
        action="store_true",
        help="Advance cursor immediately after printing events. Convenient but less safe.",
    )
    check.add_argument(
        "--full",
        action="store_true",
        help="Output full event objects instead of compact agent input.",
    )
    check.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not fetch monthly archive files to enrich event metadata.",
    )
    check.add_argument(
        "--no-archive-catchup",
        action="store_true",
        help="Do not use monthly archive files when the local cursor is older than events.json.",
    )
    check.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not collapse same-notice duplicate groups.",
    )
    check.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    ack = subparsers.add_parser("ack", help="Advance cursor after downstream handling succeeds.")
    _add_common_args(ack)
    ack.add_argument("--event-id", required=True, help="Last successfully handled event id.")
    ack.add_argument("--seen-at", help="seen_at timestamp for the acked event.")

    resolve = subparsers.add_parser(
        "resolve",
        help="Fetch official notice materials for a selected event or URL.",
    )
    resolve.add_argument(
        "--event-json",
        help="Path to one event JSON object, an event-gate payload, or '-' for stdin.",
    )
    resolve.add_argument(
        "--event-index",
        type=int,
        default=0,
        help="Event index to use when --event-json contains an events array.",
    )
    resolve.add_argument(
        "--url",
        help="Official detail URL to resolve directly or override the event detail URL.",
    )
    resolve.add_argument(
        "--download-attachments",
        dest="download_attachments",
        action="store_true",
        help="Download original attachment files into the local materials cache.",
    )
    resolve.add_argument(
        "--fetch-attachments",
        dest="download_attachments",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    resolve.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="Directory for resolved official materials.",
    )
    resolve.add_argument(
        "--max-text-chars",
        type=int,
        default=DEFAULT_MAX_TEXT_CHARS,
        help="Maximum visible text preview characters for the detail page.",
    )
    resolve.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="Maximum bytes to fetch for the detail page or one attachment.",
    )
    resolve.add_argument(
        "--max-attachment-bytes",
        type=int,
        dest="max_file_bytes",
        help=argparse.SUPPRESS,
    )
    resolve.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
        help="Maximum total bytes to fetch for one resolved notice.",
    )
    resolve.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--events-url",
        default=DEFAULT_EVENTS_URL,
        help="events.json URL, file:// URL, or local path.",
    )
    parser.add_argument(
        "--state",
        default=str(DEFAULT_STATE_PATH),
        help="Path to local event-gate state JSON.",
    )


def _check(args: argparse.Namespace) -> int:
    checked_at = now_iso()
    state = EventGateState(Path(args.state))
    result = fetch_events_feed(args.events_url, state.http_headers())
    if result.status_code == 304:
        return 0

    if result.feed is None:
        return 0

    validate_feed(result.feed)
    state.update_fetch_metadata(
        etag=result.etag,
        last_modified=result.last_modified,
        checked_at=checked_at,
    )

    previous_cursor = state.cursor
    if previous_cursor.is_empty() and not args.include_baseline:
        latest = feed_latest_cursor(result.feed)
        state.update_cursor(
            last_seen_event_id=latest.last_seen_event_id,
            last_seen_at=latest.last_seen_at,
            acked_at=checked_at,
        )
        state.save()
        return 0

    selection = select_new_events(result.feed, previous_cursor)
    warnings = list(selection.warnings)
    cursor_status = selection.status
    if selection.status == "archive_required" and not args.no_archive_catchup:
        try:
            selection = select_archive_events(result.feed, args.events_url, previous_cursor)
            warnings = list(selection.warnings)
            cursor_status = selection.status
        except Exception as error:  # noqa: BLE001 - fall back to current events window.
            warnings = [
                *warnings,
                f"archive catch-up failed: {error}",
            ]

    filtered_events = apply_filters(
        selection.events,
        source_ids=set(args.source_ids) if args.source_ids else None,
        source_categories=set(args.source_categories) if args.source_categories else None,
        topics=set(args.topics) if args.topics else None,
        event_types=set(args.event_types) if args.event_types else None,
        limit=args.limit,
    )
    dedupe_selection = (
        collapse_duplicate_events(filtered_events)
        if not args.no_dedupe
        else None
    )
    selected_events = (
        dedupe_selection.events
        if dedupe_selection is not None
        else filtered_events
    )
    suppressed_duplicate_count = (
        dedupe_selection.suppressed_count
        if dedupe_selection is not None
        else 0
    )
    if selected_events and not args.no_archive:
        archive_enrichment = enrich_events_from_archives(selected_events, args.events_url)
        selected_events = archive_enrichment.events
        warnings = [*warnings, *archive_enrichment.warnings]

    if not selected_events and not warnings:
        state.save()
        return 0

    next_cursor = event_cursor(filtered_events[-1]) if filtered_events else previous_cursor
    payload = _build_payload(
        args=args,
        feed=result.feed,
        checked_at=checked_at,
        previous_cursor=previous_cursor,
        next_cursor=next_cursor,
        cursor_status=cursor_status,
        warnings=warnings,
        new_event_count=len(selection.events),
        filtered_event_count=len(filtered_events),
        selected_events=selected_events,
        suppressed_duplicate_count=suppressed_duplicate_count,
    )

    if args.advance and filtered_events:
        state.update_cursor(
            last_seen_event_id=next_cursor.last_seen_event_id,
            last_seen_at=next_cursor.last_seen_at,
            acked_at=checked_at,
        )

    state.save()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _ack(args: argparse.Namespace) -> int:
    checked_at = now_iso()
    state = EventGateState(Path(args.state))
    state.update_cursor(
        last_seen_event_id=args.event_id,
        last_seen_at=args.seen_at,
        acked_at=checked_at,
    )
    state.save()
    return 0


def _resolve(args: argparse.Namespace) -> int:
    if not args.event_json and not args.url:
        raise SystemExit("resolve requires --event-json or --url")

    notice = (
        load_notice_input(args.event_json, args.event_index)
        if args.event_json
        else build_direct_notice(args.url)
    )
    payload = resolve_notice_materials(
        notice,
        override_url=args.url,
        download_attachments=args.download_attachments,
        cache_dir=Path(args.cache_dir),
        max_text_chars=args.max_text_chars,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _build_payload(
    *,
    args: argparse.Namespace,
    feed: dict[str, Any],
    checked_at: str,
    previous_cursor: Cursor,
    next_cursor: Cursor,
    cursor_status: str,
    warnings: list[str],
    new_event_count: int,
    filtered_event_count: int,
    selected_events: list[dict[str, Any]],
    suppressed_duplicate_count: int,
) -> dict[str, Any]:
    return {
        "type": "pnu_feed_events",
        "events_url": args.events_url,
        "checked_at": checked_at,
        "feed_generated_at": feed.get("generated_at"),
        "feed_latest_event_id": feed.get("latest_event_id"),
        "cursor_status": cursor_status,
        "warnings": warnings,
        "previous_cursor": previous_cursor.to_json(),
        "next_cursor": next_cursor.to_json(),
        "new_event_count": new_event_count,
        "filtered_event_count": filtered_event_count,
        "selected_event_count": len(selected_events),
        "dedupe_enabled": not args.no_dedupe,
        "suppressed_duplicate_count": suppressed_duplicate_count,
        "archive_enrichment_enabled": not args.no_archive,
        "filters": {
            "source_ids": args.source_ids or [],
            "source_categories": args.source_categories or [],
            "topics": args.topics or [],
            "event_types": args.event_types or [],
            "limit": args.limit,
        },
        "events": selected_events if args.full else [compact_event(event) for event in selected_events],
    }


if __name__ == "__main__":
    sys.exit(main())
