from __future__ import annotations

import json
from pathlib import Path

from pnu_event_gate.cli import main


def test_scan_enqueues_matching_candidate_and_advances_cursor(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    profile_path = _write_profile(tmp_path)

    assert main([
        "profile",
        "upsert",
        "--db",
        str(db_path),
        "--profile-json",
        str(profile_path),
    ]) == 0
    capsys.readouterr()

    assert main([
        "scan",
        "--events-url",
        str(feed_path),
        "--db",
        str(db_path),
        "--include-baseline",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    candidate = payload["candidates"][0]
    assert payload["type"] == "pnu_notice_candidates"
    assert payload["candidate_count"] == 1
    assert candidate["watch_id"] == "watch-scholarship-round-2"
    assert candidate["event_id"] == "event-scholarship"
    assert candidate["score"] >= 5
    assert candidate["matched"]["phrases"][0]["term"] == "2차 신청"

    assert main([
        "scan",
        "--events-url",
        str(feed_path),
        "--db",
        str(db_path),
    ]) == 0
    assert capsys.readouterr().out == ""

    assert main(["status", "--db", str(db_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["scan"]["last_seen_event_id"] == "event-academic"
    assert status["candidates"]["pending"] == 1
    assert status["runs"]["by_status"] == {"ok": 2}
    assert status["runs"]["latest"]["command"] == "scan"
    assert status["runs"]["latest"]["candidate_count"] == 0


def test_scan_with_no_candidates_is_quiet_but_records_status(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    profile_path = _write_profile(
        tmp_path,
        watch_id="watch-dormitory",
        phrases=["생활관 추가모집"],
        positive_terms=["생활관"],
        topics=["dormitory"],
        source_ids=[],
    )

    assert main([
        "profile",
        "upsert",
        "--db",
        str(db_path),
        "--profile-json",
        str(profile_path),
    ]) == 0
    capsys.readouterr()

    assert main([
        "scan",
        "--events-url",
        str(feed_path),
        "--db",
        str(db_path),
        "--include-baseline",
    ]) == 0
    assert capsys.readouterr().out == ""

    assert main(["status", "--db", str(db_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["scan"]["last_seen_event_id"] == "event-academic"
    assert status["candidates"] == {}
    assert status["runs"]["by_status"] == {"ok": 1}


def test_resolve_candidate_and_complete_candidate(
    tmp_path: Path,
    capsys,
) -> None:
    feed_path = _write_feed(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    profile_path = _write_profile(tmp_path)

    assert main(["profile", "upsert", "--db", str(db_path), "--profile-json", str(profile_path)]) == 0
    capsys.readouterr()
    assert main([
        "scan",
        "--events-url",
        str(feed_path),
        "--db",
        str(db_path),
        "--include-baseline",
    ]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["candidates"][0]["candidate_id"]

    assert main([
        "resolve",
        "--candidate-id",
        candidate_id,
        "--db",
        str(db_path),
        "--cache-dir",
        str(tmp_path / "cache"),
    ]) == 0
    materials = json.loads(capsys.readouterr().out)
    assert materials["notice"]["title"] == "국가장학금 2차 신청 안내"
    assert "국가장학금 신청 본문" in materials["detail"]["text_preview"]

    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps({"classification": "relevant", "message": "알림 전송 완료"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert main([
        "candidate",
        "complete",
        "--db",
        str(db_path),
        "--candidate-id",
        candidate_id,
        "--result-json",
        str(result_path),
    ]) == 0
    capsys.readouterr()

    assert main(["candidate", "show", "--db", str(db_path), "--candidate-id", candidate_id]) == 0
    candidate = json.loads(capsys.readouterr().out)
    assert candidate["status"] == "completed"
    assert candidate["result"]["classification"] == "relevant"


def test_match_explain_suppresses_negative_terms(tmp_path: Path, capsys) -> None:
    profile_path = _write_profile(tmp_path)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            _compact_event(
                event_id="event-work-scholarship",
                title="국가근로장학금 2차 신청 안내",
                topics=["scholarship"],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main([
        "match",
        "--profile-json",
        str(profile_path),
        "--event-json",
        str(event_path),
        "--explain",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["matched"] is False
    assert payload["suppressed"] is True
    assert payload["matched_reasons"]["negative_terms"][0]["term"] == "근로장학"


def test_match_accepts_inline_event_json(tmp_path: Path, capsys) -> None:
    profile_path = _write_profile(tmp_path)
    inline_event_json = json.dumps(
        _compact_event(
            event_id="event-inline-scholarship",
            title="국가장학금 2차 신청 안내",
            topics=["scholarship"],
        ),
        ensure_ascii=False,
    )

    assert main([
        "match",
        "--profile-json",
        str(profile_path),
        "--event-json",
        inline_event_json,
        "--explain",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["matched"] is True
    assert payload["score"] >= 5


def test_backtest_matches_archive_metadata(tmp_path: Path, capsys) -> None:
    feed_path = _write_feed(tmp_path)
    profile_path = _write_profile(tmp_path)
    archive_path = feed_path.parent / "archive" / "2026-06.json"

    assert main([
        "backtest",
        "--profile-json",
        str(profile_path),
        "--archive-json",
        str(archive_path),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "pnu_notice_backtest"
    assert payload["scanned_event_count"] == 2
    assert payload["match_count"] == 1
    assert payload["matches"][0]["event_id"] == "event-scholarship"


def _write_profile(
    tmp_path: Path,
    *,
    watch_id: str = "watch-scholarship-round-2",
    phrases: list[str] | None = None,
    positive_terms: list[str] | None = None,
    topics: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> Path:
    path = tmp_path / f"{watch_id}.json"
    profile = {
        "schema_version": "watch_profile.v1",
        "id": watch_id,
        "revision": "1",
        "enabled": True,
        "type": "recurring",
        "request": "국가장학금 2차 신청 공지가 뜨면 알려줘",
        "positive_terms": positive_terms or ["국가장학금", "한국장학재단"],
        "phrases": phrases or ["2차 신청"],
        "negative_terms": ["근로장학"],
        "attachment_hints": ["신청"],
        "source_hints": {
            "source_ids": ["pnu-onestop-scholarship"] if source_ids is None else source_ids,
            "topics": topics or ["scholarship"],
        },
        "thresholds": {"candidate": 5, "invoke_agent": 5},
    }
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return path


def _write_feed(tmp_path: Path) -> Path:
    detail_path = tmp_path / "scholarship.html"
    detail_path.write_text(
        "<html><body><h1>국가장학금 신청 본문</h1><p>한국장학재단 2차 신청 안내입니다.</p></body></html>",
        encoding="utf-8",
    )
    events = [
        _event(
            event_id="event-scholarship",
            title="국가장학금 2차 신청 안내",
            source_id="pnu-onestop-scholarship",
            seen_at="2026-06-05T12:00:00+09:00",
            detail_url=detail_path.as_uri(),
            topics=["scholarship"],
        ),
        _event(
            event_id="event-academic",
            title="수강정정 안내",
            source_id="pnu-main-notice",
            seen_at="2026-06-05T12:01:00+09:00",
            detail_url=detail_path.as_uri(),
            topics=["academic"],
        ),
    ]
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    archive_doc = {
        "items": [_item_for_event(event) for event in events],
        "events": events,
    }
    (archive_dir / "2026-06.json").write_text(
        json.dumps(archive_doc, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "archives": {
                    "months": [
                        {
                            "month": "2026-06",
                            "url": "./archive/2026-06.json",
                            "event_count": len(events),
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    feed = {
        "schema_version": "0.1",
        "generated_at": "2026-06-05T12:04:00+09:00",
        "latest_event_id": "event-academic",
        "latest_seen_at": "2026-06-05T12:01:00+09:00",
        "oldest_seen_at": "2026-06-05T12:00:00+09:00",
        "index_url": "./index.json",
        "events": events,
    }
    path = tmp_path / "events.json"
    path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    return path


def _event(
    *,
    event_id: str,
    title: str,
    source_id: str,
    seen_at: str,
    detail_url: str,
    topics: list[str],
) -> dict:
    return {
        **_compact_event(event_id=event_id, title=title, topics=topics),
        "source_id": source_id,
        "source_name": source_id,
        "source_category": "scholarship_notice" if "scholarship" in source_id else "university_notice",
        "seen_at": seen_at,
        "url": detail_url,
        "archive_file": "./archive/2026-06.json",
        "archive_item_id": f"{source_id}:1",
    }


def _compact_event(
    *,
    event_id: str,
    title: str,
    topics: list[str],
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "added",
        "notice_id": "notice:1",
        "source_id": "pnu-onestop-scholarship",
        "source_name": "장학공지",
        "source_category": "scholarship_notice",
        "source_tags": ["pnu"],
        "seen_at": "2026-06-05T12:00:00+09:00",
        "published_at": "2026-06-05",
        "title": title,
        "url": "https://example.test/notice",
        "topics": topics,
        "same_notice_group_id": None,
        "canonical_item_id": "notice:1",
        "is_canonical": True,
        "same_notice_source_ids": ["pnu-onestop-scholarship"],
        "archive_file": "./archive/2026-06.json",
        "archive_item_id": "notice:1",
        "snippet": "한국장학재단 2차 신청",
        "attachments": [{"name": "국가장학금 신청 매뉴얼.pdf", "file_extension": "pdf"}],
    }


def _item_for_event(event: dict) -> dict:
    is_scholarship = "scholarship" in str(event["source_id"])
    snippet = "한국장학재단 2차 신청" if is_scholarship else "수강정정 기간 안내"
    attachment_name = "국가장학금 신청 매뉴얼.pdf" if is_scholarship else "수강정정 안내.pdf"
    return {
        "id": event["archive_item_id"],
        "url": event["url"],
        "title": event["title"],
        "summary": snippet,
        "_pnu": {
            "source_id": event["source_id"],
            "source_name": event["source_name"],
            "source_category": event["source_category"],
            "source_tags": ["pnu"],
            "snippet": snippet,
            "topics": event["topics"],
            "content_access": {
                "detail_url": event["url"],
                "requires_login": False,
                "content_mirrored": False,
                "attachments_mirrored": False,
            },
            "attachments": [
                {
                    "name": attachment_name,
                    "download_url": "https://example.test/manual.pdf",
                    "file_extension": "pdf",
                    "media_type": "application/pdf",
                }
            ],
        },
    }
