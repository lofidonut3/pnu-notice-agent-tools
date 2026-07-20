from pathlib import Path
from typing import Any

import pytest

from pnu_event_gate.store import NoticeStore
from pnu_event_gate.worker import _pending_candidates, deliver_due_notifications, run_watch_cycle


class UnusedClient:
    pass


def test_watch_cycle_queues_before_sending_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    sent = []
    with NoticeStore(db_path) as store:
        store.upsert_profile(
            {
                "id": "watch-1",
                "revision": "1",
                "request": "database 001 cancellation",
                "compiled_intent": {
                    "event_type": "course_cancelled",
                    "entities": [],
                    "exact_terms": ["database", "001"],
                },
                "delivery": {"email_to": "student@example.test"},
            },
            now="2026-07-19T10:00:00+09:00",
        )
        store.insert_candidate(_candidate())
        store.commit()

        monkeypatch.setattr("pnu_event_gate.worker.run_scan", lambda **_kwargs: None)
        monkeypatch.setattr(
            "pnu_event_gate.worker.resolve_notice_materials",
            lambda *_args, **_kwargs: {
                "notice": {"title": "Cancellation notice"},
                "detail": {"text_preview": "database 001 cancelled"},
                "attachments": [],
            },
        )
        monkeypatch.setattr(
            "pnu_event_gate.worker.run_ai_analysis",
            lambda **_kwargs: {
                "decision": {
                    "classification": "matched",
                    "confidence": 0.99,
                    "facts": [{"text": "cancelled", "evidence_ids": ["E001"]}],
                    "evidence_ids": ["E001"],
                },
                "email": {"subject": "Matched", "body_text": "Evidence"},
            },
        )

        def sender(recipient: str, content: dict[str, Any]) -> dict[str, Any]:
            outbox = store.list_due_notifications(now="9999-12-31T00:00:00+00:00")
            assert len(outbox) == 1
            assert outbox[0]["status"] == "pending"
            sent.append((recipient, content["subject"]))
            return {"transport": "fake"}

        first = run_watch_cycle(
            store=store,
            events_url="events.json",
            cache_dir=tmp_path / "cache",
            client_factory=lambda: UnusedClient(),
            notification_sender=sender,
        )
        second = run_watch_cycle(
            store=store,
            events_url="events.json",
            cache_dir=tmp_path / "cache",
            client_factory=lambda: UnusedClient(),
            notification_sender=sender,
        )

        assert first["processed"][0]["notification_queued"] is True
        assert first["deliveries"][0]["status"] == "sent"
        assert second["candidate_count"] == 0
        assert second["deliveries"] == []
        assert sent == [("student@example.test", "Matched")]
        assert store.status_summary()["outbox"] == {"sent": 1}
        assert store.status_summary()["runs"]["by_status"] == {"ok": 2}


def test_watch_cycle_records_uncaught_failure(tmp_path: Path, monkeypatch) -> None:
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        def fail_scan(**_kwargs) -> None:
            raise RuntimeError("feed unavailable")

        monkeypatch.setattr("pnu_event_gate.worker.run_scan", fail_scan)

        with pytest.raises(RuntimeError, match="feed unavailable"):
            run_watch_cycle(
                store=store,
                events_url="events.json",
                cache_dir=tmp_path / "cache",
                client_factory=lambda: UnusedClient(),
                notification_sender=None,
            )

        runs = store.list_runs()
        assert len(runs) == 1
        assert runs[0]["command"] == "run-watch-cycle"
        assert runs[0]["status"] == "failed"
        assert runs[0]["warnings"] == ["RuntimeError: feed unavailable"]


def test_outbox_failure_is_retried_then_needs_attention(tmp_path: Path) -> None:
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        store.enqueue_notification(
            {
                "outbox_id": "notif-1",
                "candidate_id": "candidate-1",
                "watch_id": "watch-1",
                "event_id": "event-1",
                "decision_hash": "hash-1",
                "recipient": "student@example.test",
                "payload": {"subject": "Notice", "body_text": "Body"},
                "created_at": "2026-07-19T10:00:00+09:00",
                "updated_at": "2026-07-19T10:00:00+09:00",
            }
        )
        store.commit()

        def failing_sender(_recipient: str, _content: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("SMTP unavailable")

        first = deliver_due_notifications(
            store=store,
            sender=failing_sender,
            limit=10,
            max_attempts=2,
        )
        assert first[0]["status"] == "retry"

        store._execute(
            "update notification_outbox set next_attempt_at = null where outbox_id = ?",
            ("notif-1",),
        )
        store.commit()
        second = deliver_due_notifications(
            store=store,
            sender=failing_sender,
            limit=10,
            max_attempts=2,
        )
        assert second[0]["status"] == "needs_attention"
        assert store.get_notification("notif-1")["attempts"] == 2


def test_stale_processing_candidate_is_recovered(tmp_path: Path) -> None:
    candidate = _candidate()
    candidate["status"] = "analyzing"
    candidate["updated_at"] = "2026-01-01T00:00:00+09:00"
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        store.insert_candidate(candidate)
        store.commit()

        recovered = _pending_candidates(store)

        assert [item["candidate_id"] for item in recovered] == ["candidate-1"]


def _candidate() -> dict[str, Any]:
    return {
        "candidate_id": "candidate-1",
        "watch_id": "watch-1",
        "profile_revision": "1",
        "event_id": "event-1",
        "notice_id": "notice-1",
        "seen_at": "2026-07-19T10:01:00+09:00",
        "source_id": "source-1",
        "same_notice_group_id": None,
        "status": "pending",
        "score": 10,
        "action": "invoke_agent",
        "match": {"terms": ["database"]},
        "event": {
            "event_id": "event-1",
            "notice_id": "notice-1",
            "title": "Cancellation notice",
            "url": "https://example.test/notice-1",
            "attachments": [],
        },
        "created_at": "2026-07-19T10:01:00+09:00",
        "updated_at": "2026-07-19T10:01:00+09:00",
    }
