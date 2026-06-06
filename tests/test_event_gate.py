from __future__ import annotations

import json
from pathlib import Path

from pnu_event_gate.cli import main


def test_first_run_sets_baseline_without_output(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    exit_code = main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
    ])

    captured = capsys.readouterr()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured.out == ""
    assert state["cursor"]["last_seen_event_id"] == "event-3"
    assert state["cursor"]["last_seen_at"] == "2026-06-05T12:02:00+09:00"


def test_include_baseline_outputs_archive_enriched_events(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    exit_code = main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--include-baseline",
        "--limit",
        "2",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["archive_enrichment_enabled"] is True
    assert payload["selected_event_count"] == 2
    assert payload["events"][0]["event_id"] == "event-1"
    assert payload["events"][0]["snippet"] == "pnu-main-notice:1 snippet"
    assert payload["events"][0]["content_access"]["requires_login"] is False
    assert payload["events"][0]["attachments"][0]["file_extension"] == "pdf"


def test_no_archive_outputs_event_without_detail_enrichment(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--include-baseline",
        "--limit",
        "1",
        "--no-archive",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_enrichment_enabled"] is False
    assert payload["events"][0]["snippet"] is None
    assert payload["events"][0]["content_access"] is None
    assert payload["events"][0]["attachments"] == []


def test_outputs_only_events_after_acked_cursor(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    assert main([
        "ack",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--event-id",
        "event-1",
        "--seen-at",
        "2026-06-05T12:00:00+09:00",
    ]) == 0

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [event["event_id"] for event in payload["events"]] == ["event-2", "event-3"]
    assert payload["events"][1]["source_name"] == "부산대 대학공지"
    assert payload["events"][1]["topics"] == ["academic"]
    assert payload["next_cursor"]["last_seen_event_id"] == "event-3"


def test_source_filter(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    exit_code = main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--include-baseline",
        "--source",
        "pnu-onestop-scholarship",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [event["event_id"] for event in payload["events"]] == ["event-2"]


def test_topic_filter(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    exit_code = main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--include-baseline",
        "--topic",
        "scholarship",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [event["event_id"] for event in payload["events"]] == ["event-2"]


def test_default_dedupe_collapses_same_notice_group_and_keeps_cursor_progress(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path, include_duplicate=True)
    state_path = tmp_path / "state.json"

    assert main([
        "ack",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--event-id",
        "event-1",
        "--seen-at",
        "2026-06-05T12:00:00+09:00",
    ]) == 0

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dedupe_enabled"] is True
    assert payload["filtered_event_count"] == 3
    assert payload["selected_event_count"] == 2
    assert payload["suppressed_duplicate_count"] == 1
    assert [event["event_id"] for event in payload["events"]] == ["event-2", "event-3"]
    assert payload["next_cursor"]["last_seen_event_id"] == "event-4"


def test_no_dedupe_outputs_all_matching_events(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path, include_duplicate=True)
    state_path = tmp_path / "state.json"

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--include-baseline",
        "--no-dedupe",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dedupe_enabled"] is False
    assert payload["suppressed_duplicate_count"] == 0
    assert [event["event_id"] for event in payload["events"]] == [
        "event-1",
        "event-2",
        "event-3",
        "event-4",
    ]


def test_advance_updates_cursor_after_output(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    state_path = tmp_path / "state.json"

    assert main([
        "ack",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--event-id",
        "event-1",
        "--seen-at",
        "2026-06-05T12:00:00+09:00",
    ]) == 0

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--advance",
    ]) == 0

    capsys.readouterr()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["cursor"]["last_seen_event_id"] == "event-3"


def test_archive_required_uses_monthly_archive_catchup(
    tmp_path: Path,
    capsys,
) -> None:
    archive_events = _events()
    feed_path = _write_feed(
        tmp_path,
        feed_events=[archive_events[-1]],
        archive_events=archive_events,
    )
    state_path = tmp_path / "state.json"

    assert main([
        "ack",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
        "--event-id",
        "missing-old-event",
        "--seen-at",
        "2026-06-05T12:00:30+09:00",
    ]) == 0

    assert main([
        "check",
        "--events-url",
        str(feed_path),
        "--state",
        str(state_path),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["cursor_status"] == "archive_seen_at"
    assert payload["warnings"] == []
    assert [event["event_id"] for event in payload["events"]] == ["event-2", "event-3"]


def _write_feed(
    tmp_path: Path,
    *,
    include_duplicate: bool = False,
    feed_events: list[dict] | None = None,
    archive_events: list[dict] | None = None,
) -> Path:
    events = feed_events or _events(include_duplicate=include_duplicate)
    archive_doc_events = archive_events or events
    feed = {
        "schema_version": "0.1",
        "event_stream_version": "0.3",
        "generated_at": "2026-06-05T12:04:00+09:00",
        "timezone": "Asia/Seoul",
        "event_count": len(events),
        "total_event_count": len(archive_doc_events),
        "event_limit": 1000,
        "latest_event_id": events[-1]["event_id"],
        "oldest_event_id": events[0]["event_id"],
        "oldest_seen_at": events[0]["seen_at"],
        "latest_seen_at": events[-1]["seen_at"],
        "is_truncated": len(events) < len(archive_doc_events),
        "index_url": "./index.json",
        "archive_url_pattern": "./archive/{YYYY-MM}.json",
        "events": events,
    }
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True)
    archive_doc = {
        "schema_version": "0.1",
        "archive_version": "0.3",
        "archive_type": "month",
        "archive_month": "2026-06",
        "last_modified_at": "2026-06-05T12:04:00+09:00",
        "timezone": "Asia/Seoul",
        "item_count": len(archive_doc_events),
        "event_count": len(archive_doc_events),
        "source_counts": {},
        "items": [_item_for_event(event) for event in archive_doc_events],
        "events": archive_doc_events,
    }
    (archive_dir / "2026-06.json").write_text(
        json.dumps(archive_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    index = {
        "schema_version": "0.1",
        "generated_at": "2026-06-05T12:04:00+09:00",
        "archives": {
            "months": [
                {
                    "month": "2026-06",
                    "url": "./archive/2026-06.json",
                    "notice_count": len(archive_doc_events),
                    "event_count": len(archive_doc_events),
                }
            ]
        },
    }
    (tmp_path / "index.json").write_text(
        json.dumps(index, ensure_ascii=False),
        encoding="utf-8",
    )
    path = tmp_path / "events.json"
    path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    return path


def _events(include_duplicate: bool = False) -> list[dict]:
    events = [
        _event("event-1", "pnu-main-notice", "2026-06-05T12:00:00+09:00"),
        _event(
            "event-2",
            "pnu-onestop-scholarship",
            "2026-06-05T12:01:00+09:00",
            topics=["scholarship"],
        ),
        _event("event-3", "pnu-main-notice", "2026-06-05T12:02:00+09:00"),
    ]
    if include_duplicate:
        return [
            *events[:2],
            _event(
                "event-3",
                "pnu-main-notice",
                "2026-06-05T12:02:00+09:00",
                group_id="same_notice:1",
                canonical_item_id="pnu-main-notice:1",
                is_canonical=True,
            ),
            _event(
                "event-4",
                "pnu-academic-dept",
                "2026-06-05T12:03:00+09:00",
                notice_id="pnu-academic-dept:1",
                group_id="same_notice:1",
                canonical_item_id="pnu-main-notice:1",
                is_canonical=False,
            ),
        ]
    return events


def _event(
    event_id: str,
    source_id: str,
    seen_at: str,
    *,
    topics: list[str] | None = None,
    notice_id: str | None = None,
    group_id: str | None = None,
    canonical_item_id: str | None = None,
    is_canonical: bool = True,
) -> dict:
    item_id = notice_id or f"{source_id}:1"
    return {
        "event_id": event_id,
        "event_type": "added",
        "notice_id": item_id,
        "source_id": source_id,
        "source_name": "부산대 대학공지" if source_id == "pnu-main-notice" else f"{source_id} name",
        "source_category": "scholarship_notice" if "scholarship" in source_id else "university_notice",
        "source_tags": ["pnu", "official"],
        "seen_at": seen_at,
        "published_at": "2026-06-05",
        "title": f"{source_id} title",
        "url": "https://example.com/notice",
        "topics": topics or ["academic"],
        "same_notice_group_id": group_id,
        "canonical_item_id": canonical_item_id or item_id,
        "is_canonical": is_canonical,
        "same_notice_source_ids": [source_id],
        "content_hash": event_id,
        "previous_content_hash": None,
        "archive_file": "./archive/2026-06.json",
        "archive_item_id": item_id,
    }


def _item_for_event(event: dict) -> dict:
    item_id = str(event["archive_item_id"])
    return {
        "id": item_id,
        "url": event["url"],
        "title": event["title"],
        "summary": f"{item_id} summary",
        "content_text": "This feed does not mirror the full notice body.",
        "date_published": "2026-06-05T00:00:00+09:00",
        "_pnu": {
            "source_id": event["source_id"],
            "source_name": event["source_name"],
            "source_category": event["source_category"],
            "source_tags": event["source_tags"],
            "published_at": event["published_at"],
            "fetched_at": event["seen_at"],
            "snippet": f"{item_id} snippet",
            "content_access": {
                "detail_url": event["url"],
                "requires_login": False,
                "content_mirrored": False,
                "attachments_mirrored": False,
            },
            "attachments": [
                {
                    "name": "notice.pdf",
                    "download_url": "https://example.com/notice.pdf",
                    "type": "pdf",
                    "media_type": "application/pdf",
                    "file_extension": "pdf",
                }
            ],
            "tags": ["pnu", "official"],
            "topics": event["topics"],
            "same_notice_group_id": event["same_notice_group_id"],
            "canonical_item_id": event["canonical_item_id"],
            "is_canonical": event["is_canonical"],
            "same_notice_source_ids": event["same_notice_source_ids"],
            "content_hash": event["content_hash"],
        },
    }
