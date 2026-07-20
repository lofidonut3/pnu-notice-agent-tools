from pathlib import Path

from pnu_event_gate.store import NoticeStore
from pnu_event_gate.watch_requests import process_watch_requests


class FakeClient:
    provider = "fake"

    def chat_json(self, **_kwargs):
        return {
            "schema_version": "watch_intent.v1",
            "request": "2026 summer database 001 cancelled",
            "event_type": "course_cancelled",
            "entities": [
                {"type": "course", "value": "database", "required": True},
                {"type": "section", "value": "001", "required": True},
            ],
            "exact_terms": ["database", "001"],
            "semantic_terms": ["summer term", "cancelled"],
            "negative_terms": [],
            "time_scope": "2026 summer",
            "ambiguities": [],
        }


def test_pending_web_request_becomes_active_profile(tmp_path: Path) -> None:
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        _insert_request(store)

        result = process_watch_requests(
            store=store,
            client_factory=FakeClient,
            chat_model="test-model",
        )

        request_row = store.get_watch_request("request-1")
        profile = store.get_profile(request_row["watch_id"])
        assert result["processed_count"] == 1
        assert request_row["status"] == "active"
        assert request_row["compiled_intent"]["event_type"] == "course_cancelled"
        assert profile["profile"]["delivery"]["email_to"] == "student@example.test"
        assert profile["profile"]["owner"]["request_id"] == "request-1"
        assert store.status_summary()["runs"]["by_status"] == {"ok": 1}


def test_disabled_web_request_disables_profile(tmp_path: Path) -> None:
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        _insert_request(store)
        process_watch_requests(
            store=store,
            client_factory=FakeClient,
            chat_model="test-model",
        )
        store._execute(
            "update watch_requests set enabled = 0 where id = ?",
            ("request-1",),
        )
        store.commit()

        result = process_watch_requests(
            store=store,
            client_factory=lambda: (_ for _ in ()).throw(AssertionError("unused")),
            chat_model="test-model",
        )

        request_row = store.get_watch_request("request-1")
        profile = store.get_profile(
            request_row["watch_id"],
            request_row["profile_revision"],
        )
        assert result["synced_count"] == 1
        assert result["processed_count"] == 0
        assert profile["enabled"] is False


def _insert_request(store: NoticeStore) -> None:
    store._execute(
        """
        insert into watch_requests (
          id, user_id, request, delivery_email, enabled, status, revision,
          created_at, updated_at
        ) values (?, ?, ?, ?, 1, 'pending', 1, ?, ?)
        """,
        (
            "request-1",
            "user-1",
            "2026 summer database 001 cancelled",
            "student@example.test",
            "2026-07-20T10:00:00+09:00",
            "2026-07-20T10:00:00+09:00",
        ),
    )
    store.commit()
