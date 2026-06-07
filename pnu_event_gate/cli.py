from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .backtest import run_backtest
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
from .matcher import match_event
from .profiles import load_profile
from .scan import run_scan
from .state import Cursor, EventGateState
from .store import NoticeStore


DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / ".pnu-notice-state.json"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / ".pnu-notice-state.sqlite3"
COMMANDS = {
    "check",
    "ack",
    "resolve",
    "scan",
    "profile",
    "candidate",
    "status",
    "match",
    "backtest",
    "receipt",
    "-h",
    "--help",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or raw_args[0] not in COMMANDS:
        raw_args = ["check", *raw_args]
    args = parser.parse_args(raw_args)

    if args.command == "ack":
        return _ack(args)
    if args.command == "resolve":
        return _resolve(args)
    if args.command == "scan":
        return _scan(args)
    if args.command == "profile":
        return _profile(args)
    if args.command == "candidate":
        return _candidate(args)
    if args.command == "status":
        return _status(args)
    if args.command == "match":
        return _match(args)
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "receipt":
        return _receipt(args)
    return _check(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check and materialize PNU public notice feed events for local AI agents.",
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

    scan = subparsers.add_parser("scan", help="Scan feed events, match active profiles, and enqueue candidates.")
    scan.add_argument(
        "--events-url",
        default=DEFAULT_EVENTS_URL,
        help="events.json URL, file:// URL, or local path.",
    )
    _add_db_arg(scan)
    scan.add_argument(
        "--include-baseline",
        action="store_true",
        help="Scan current feed events on the first run instead of only setting a baseline.",
    )
    scan.add_argument("--limit", type=int, help="Maximum events to scan.")
    scan.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not fetch monthly archive files to enrich event metadata.",
    )
    scan.add_argument(
        "--no-archive-catchup",
        action="store_true",
        help="Do not use monthly archive files when the local cursor is older than events.json.",
    )
    scan.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not collapse same-notice duplicate groups.",
    )
    scan.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    ack = subparsers.add_parser("ack", help="Advance cursor after downstream handling succeeds.")
    _add_common_args(ack)
    ack.add_argument("--event-id", required=True, help="Last successfully handled event id.")
    ack.add_argument("--seen-at", help="seen_at timestamp for the acked event.")

    profile = subparsers.add_parser("profile", help="Manage compiled watch profiles.")
    profile_subparsers = profile.add_subparsers(dest="profile_command")
    profile_subparsers.required = True
    profile_upsert = profile_subparsers.add_parser("upsert", help="Insert or update a watch profile.")
    _add_db_arg(profile_upsert)
    profile_upsert.add_argument("--profile-json", required=True, help="Profile JSON path or '-' for stdin.")
    profile_upsert.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    profile_list = profile_subparsers.add_parser("list", help="List watch profiles.")
    _add_db_arg(profile_list)
    profile_list.add_argument("--include-disabled", action="store_true", help="Include disabled profiles.")
    profile_list.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    profile_disable = profile_subparsers.add_parser("disable", help="Disable an active watch profile.")
    _add_db_arg(profile_disable)
    profile_disable.add_argument("--watch-id", required=True, help="Watch profile id to disable.")
    profile_disable.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    profile_export = profile_subparsers.add_parser("export", help="Export a stored watch profile.")
    _add_db_arg(profile_export)
    profile_export.add_argument("--watch-id", required=True, help="Watch profile id to export.")
    profile_export.add_argument("--revision", help="Profile revision to export. Defaults to the active revision.")
    profile_export.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    match = subparsers.add_parser("match", help="Match one event against one compiled watch profile.")
    match.add_argument("--profile-json", required=True, help="Profile JSON path or '-' for stdin.")
    match.add_argument("--event-json", required=True, help="Event JSON path or '-' for stdin.")
    match.add_argument("--explain", action="store_true", help="Include deterministic match reasons.")
    match.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    backtest = subparsers.add_parser("backtest", help="Replay one profile against archive metadata.")
    backtest.add_argument("--profile-json", required=True, help="Profile JSON path or '-' for stdin.")
    backtest.add_argument(
        "--archive-json",
        action="append",
        required=True,
        help="Archive JSON path. Can be repeated.",
    )
    backtest.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    resolve = subparsers.add_parser(
        "resolve",
        help="Fetch official notice materials for a selected event or URL.",
    )
    resolve.add_argument(
        "--event-json",
        help="Path to one event JSON object, a pnu-notice check payload, or '-' for stdin.",
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
        "--candidate-id",
        help="Resolve a queued candidate by id from the SQLite state database.",
    )
    _add_db_arg(resolve)
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

    candidate = subparsers.add_parser("candidate", help="Inspect or update queued candidates.")
    candidate_subparsers = candidate.add_subparsers(dest="candidate_command")
    candidate_subparsers.required = True

    candidate_list = candidate_subparsers.add_parser("list", help="List queued candidates.")
    _add_db_arg(candidate_list)
    candidate_list.add_argument("--status", help="Filter by candidate status.")
    candidate_list.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    candidate_show = candidate_subparsers.add_parser("show", help="Show one queued candidate.")
    _add_db_arg(candidate_show)
    candidate_show.add_argument("--candidate-id", required=True, help="Candidate id to show.")
    candidate_show.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    candidate_complete = candidate_subparsers.add_parser("complete", help="Mark a candidate completed.")
    _add_db_arg(candidate_complete)
    candidate_complete.add_argument("--candidate-id", required=True, help="Candidate id to complete.")
    candidate_complete.add_argument("--result-json", help="Result JSON path, inline JSON, or '-' for stdin.")
    candidate_complete.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    candidate_fail = candidate_subparsers.add_parser("fail", help="Mark a candidate failed.")
    _add_db_arg(candidate_fail)
    candidate_fail.add_argument("--candidate-id", required=True, help="Candidate id to fail.")
    failure_kind = candidate_fail.add_mutually_exclusive_group(required=True)
    failure_kind.add_argument("--retryable", action="store_true", help="Mark as retryable failure.")
    failure_kind.add_argument("--terminal", action="store_true", help="Mark as terminal failure.")
    failure_kind.add_argument("--needs-attention", action="store_true", help="Mark as needing user attention.")
    candidate_fail.add_argument("--reason", required=True, help="Failure reason.")
    candidate_fail.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    status = subparsers.add_parser("status", help="Print local scan/profile/candidate status.")
    _add_db_arg(status)
    status.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    receipt = subparsers.add_parser("receipt", help="Record notification receipts.")
    receipt_subparsers = receipt.add_subparsers(dest="receipt_command")
    receipt_subparsers.required = True
    receipt_record = receipt_subparsers.add_parser("record", help="Record a sent notification receipt.")
    _add_db_arg(receipt_record)
    receipt_record.add_argument("--receipt-id", required=True)
    receipt_record.add_argument("--candidate-id", required=True)
    receipt_record.add_argument("--channel")
    receipt_record.add_argument("--payload-hash")
    receipt_record.add_argument("--status", default="sent")
    receipt_record.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
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
        help="Path to local pnu-notice state JSON.",
    )


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Path to local pnu-notice SQLite state database.",
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


def _scan(args: argparse.Namespace) -> int:
    with NoticeStore(Path(args.db)) as store:
        payload = run_scan(
            store=store,
            events_url=args.events_url,
            include_baseline=args.include_baseline,
            no_archive=args.no_archive,
            no_archive_catchup=args.no_archive_catchup,
            no_dedupe=args.no_dedupe,
            limit=args.limit,
        )
    if payload is None:
        return 0
    _print_json(payload, pretty=args.pretty)
    return 0


def _profile(args: argparse.Namespace) -> int:
    checked_at = now_iso()
    with NoticeStore(Path(args.db)) as store:
        if args.profile_command == "upsert":
            profile = load_profile(args.profile_json)
            payload = {
                "type": "pnu_notice_profile",
                **store.upsert_profile(profile, now=checked_at),
            }
            store.commit()
            _print_json(payload, pretty=args.pretty)
            return 0
        if args.profile_command == "list":
            payload = {
                "type": "pnu_notice_profiles",
                "profiles": store.list_profiles(include_disabled=args.include_disabled),
            }
            _print_json(payload, pretty=args.pretty)
            return 0
        if args.profile_command == "disable":
            disabled_count = store.disable_profile(args.watch_id, now=checked_at)
            store.commit()
            _print_json(
                {
                    "type": "pnu_notice_profile_disabled",
                    "watch_id": args.watch_id,
                    "disabled_count": disabled_count,
                },
                pretty=args.pretty,
            )
            return 0
        if args.profile_command == "export":
            profile = store.get_profile(args.watch_id, args.revision)
            _print_json(profile["profile"], pretty=args.pretty)
            return 0
    raise SystemExit(f"unknown profile command: {args.profile_command}")


def _candidate(args: argparse.Namespace) -> int:
    checked_at = now_iso()
    with NoticeStore(Path(args.db)) as store:
        if args.candidate_command == "list":
            payload = {
                "type": "pnu_notice_candidate_list",
                "candidates": store.list_candidates(status=args.status),
            }
            _print_json(payload, pretty=args.pretty)
            return 0
        if args.candidate_command == "show":
            _print_json(store.get_candidate(args.candidate_id), pretty=args.pretty)
            return 0
        if args.candidate_command == "complete":
            result = _load_optional_json(args.result_json) or {}
            payload = store.update_candidate(
                args.candidate_id,
                status="completed",
                now=checked_at,
                result=result,
            )
            store.commit()
            _print_json(payload, pretty=args.pretty)
            return 0
        if args.candidate_command == "fail":
            status = (
                "failed_retryable"
                if args.retryable
                else "failed_terminal"
                if args.terminal
                else "needs_attention"
            )
            payload = store.update_candidate(
                args.candidate_id,
                status=status,
                now=checked_at,
                error=args.reason,
                increment_attempts=True,
            )
            store.commit()
            _print_json(payload, pretty=args.pretty)
            return 0
    raise SystemExit(f"unknown candidate command: {args.candidate_command}")


def _status(args: argparse.Namespace) -> int:
    with NoticeStore(Path(args.db)) as store:
        _print_json(store.status_summary(), pretty=args.pretty)
    return 0


def _match(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile_json)
    event = _load_json(args.event_json)
    if isinstance(event, dict) and isinstance(event.get("events"), list):
        event = event["events"][0] if event["events"] else {}
    if not isinstance(event, dict):
        raise SystemExit("match requires an event JSON object")
    result = match_event(profile, event)
    payload = {
        "type": "pnu_notice_match_explain",
        "matched": result.matched,
        "suppressed": result.suppressed,
        "score": result.score,
        "threshold": result.threshold,
        "action": result.action,
        "matched_reasons": result.matched_reasons if args.explain else {},
    }
    _print_json(payload, pretty=args.pretty)
    return 0


def _backtest(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile_json)
    archives = [_load_json(path) for path in args.archive_json]
    if not all(isinstance(archive, dict) for archive in archives):
        raise SystemExit("backtest requires archive JSON objects")
    _print_json(run_backtest(profile, archives), pretty=args.pretty)
    return 0


def _receipt(args: argparse.Namespace) -> int:
    checked_at = now_iso()
    with NoticeStore(Path(args.db)) as store:
        if args.receipt_command == "record":
            candidate = store.get_candidate(args.candidate_id)
            receipt = {
                "receipt_id": args.receipt_id,
                "candidate_id": args.candidate_id,
                "watch_id": candidate["watch_id"],
                "event_id": candidate["event_id"],
                "channel": args.channel,
                "payload_hash": args.payload_hash,
                "status": args.status,
                "created_at": checked_at,
                "sent_at": checked_at if args.status == "sent" else None,
                "metadata": {},
            }
            inserted = store.record_receipt(receipt)
            store.commit()
            _print_json(
                {
                    "type": "pnu_notice_receipt",
                    "inserted": inserted,
                    **receipt,
                },
                pretty=args.pretty,
            )
            return 0
    raise SystemExit(f"unknown receipt command: {args.receipt_command}")


def _resolve(args: argparse.Namespace) -> int:
    if not args.event_json and not args.url and not args.candidate_id:
        raise SystemExit("resolve requires --event-json or --url")

    store: NoticeStore | None = None
    candidate: dict[str, Any] | None = None
    if args.candidate_id:
        store = NoticeStore(Path(args.db))
        candidate = store.get_candidate(args.candidate_id)
        notice = candidate["event"]
        store.update_candidate(args.candidate_id, status="resolving", now=now_iso())
        store.commit()
    else:
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
    if store is not None and candidate is not None:
        store.update_candidate(
            candidate["candidate_id"],
            status="resolved",
            now=now_iso(),
            materials=payload,
        )
        store.commit()
        store.close()
    _print_json(payload, pretty=args.pretty)
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


def _load_optional_json(value: str | None) -> Any:
    if not value:
        return None
    return _load_json(value)


def _load_json(value: str) -> Any:
    if value == "-":
        raw = sys.stdin.read()
        return json.loads(raw)

    candidate = value.strip()
    if candidate.startswith(("{", "[")):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    try:
        raw = (
            Path(value).read_text(encoding="utf-8")
            if Path(value).exists()
            else value
        )
    except OSError:
        raw = value
    return json.loads(raw)


def _print_json(payload: Any, *, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


if __name__ == "__main__":
    sys.exit(main())
