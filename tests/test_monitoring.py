from pathlib import Path

from pnu_event_gate.monitoring import monitor_service
from pnu_event_gate.state import Cursor
from pnu_event_gate.store import NoticeStore


def test_monitor_service_records_healthy_snapshot(tmp_path: Path) -> None:
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        store.update_scan_state(
            cursor=Cursor(last_seen_event_id="event-1", last_seen_at="2026-07-20T10:00:00+09:00"),
            checked_at="2026-07-20T11:30:00+09:00",
            feed_generated_at="2026-07-20T11:00:00+09:00",
            etag=None,
            last_modified=None,
            status="ok",
            warnings=[],
        )
        run_id = store.start_run(
            command="run-watch-cycle",
            started_at="2026-07-20T11:29:00+09:00",
        )
        store.finish_run(
            run_id,
            finished_at="2026-07-20T11:31:00+09:00",
            status="ok",
        )
        store.commit()

        result = monitor_service(
            store=store,
            operator_email=None,
            sender=None,
            checked_at="2026-07-20T12:00:00+09:00",
        )

        assert result["status"] == "healthy"
        assert result["issues"] == []
        assert store.status_summary()["service_health"]["status"] == "healthy"


def test_monitor_service_notifies_only_new_incidents(tmp_path: Path) -> None:
    sent = []

    def sender(recipient: str, content: dict[str, str]) -> dict[str, str]:
        sent.append((recipient, content["subject"]))
        return {"status": "sent"}

    with NoticeStore(tmp_path / "state.sqlite3") as store:
        first = monitor_service(
            store=store,
            operator_email="operator@example.test",
            sender=sender,
            checked_at="2026-07-20T12:00:00+09:00",
        )
        second = monitor_service(
            store=store,
            operator_email="operator@example.test",
            sender=sender,
            checked_at="2026-07-20T12:30:00+09:00",
        )

        assert first["status"] == "unhealthy"
        assert first["new_incident_count"] >= 2
        assert second["new_incident_count"] == 0
        assert len(sent) == 1
