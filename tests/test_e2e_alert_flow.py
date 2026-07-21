from pathlib import Path
from typing import Any

from pnu_event_gate.store import NoticeStore
from pnu_event_gate.worker import run_watch_cycle


class DecisionClient:
    provider = "fixture"

    def chat_json(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "classification": "matched",
            "confidence": 0.98,
            "summary": "데이터베이스 001분반이 폐강 강좌 목록에 있습니다.",
            "facts": [
                {
                    "text": "데이터베이스 001분반 폐강",
                    "evidence_ids": ["E001"],
                }
            ],
            "evidence_ids": ["E001"],
            "missing_information": [],
        }


def test_registered_watch_reaches_grounded_email_and_user_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sent: list[tuple[str, str]] = []
    with NoticeStore(tmp_path / "state.sqlite3") as store:
        store.upsert_profile(
            {
                "id": "web-request-1",
                "revision": "1",
                "request": "데이터베이스 001분반이 폐강되면 알려줘",
                "compiled_intent": {
                    "event_type": "course_cancelled",
                    "entities": [
                        {"type": "course", "value": "데이터베이스", "required": True},
                        {"type": "section", "value": "001", "required": True},
                    ],
                    "exact_terms": ["데이터베이스", "001", "폐강"],
                    "semantic_terms": [],
                    "negative_terms": [],
                },
                "delivery": {"email_to": "student@example.test"},
                "owner": {
                    "type": "web_request",
                    "user_id": "user-1",
                    "request_id": "request-1",
                },
            },
            now="2026-07-21T10:00:00+09:00",
        )
        store.insert_candidate(
            {
                "candidate_id": "candidate-e2e-1",
                "watch_id": "web-request-1",
                "profile_revision": "1",
                "event_id": "event-e2e-1",
                "notice_id": "notice-e2e-1",
                "seen_at": "2026-07-21T10:01:00+09:00",
                "source_id": "academic",
                "same_notice_group_id": None,
                "status": "pending",
                "score": 10,
                "action": "invoke_agent",
                "match": {"terms": ["데이터베이스", "폐강"]},
                "event": {
                    "event_id": "event-e2e-1",
                    "notice_id": "notice-e2e-1",
                    "title": "2026 여름계절수업 폐강 강좌 안내",
                    "url": "https://example.test/notices/cancelled-courses",
                },
                "created_at": "2026-07-21T10:01:00+09:00",
                "updated_at": "2026-07-21T10:01:00+09:00",
            }
        )
        store.commit()

        monkeypatch.setattr("pnu_event_gate.worker.run_scan", lambda **_kwargs: None)
        monkeypatch.setattr(
            "pnu_event_gate.worker.resolve_notice_materials",
            lambda *_args, **_kwargs: {
                "notice": {
                    "title": "2026 여름계절수업 폐강 강좌 안내",
                    "url": "https://example.test/notices/cancelled-courses",
                },
                "detail": {
                    "url": "https://example.test/notices/cancelled-courses",
                    "text_preview": "폐강 강좌 목록\n데이터베이스 | 001 | 폐강",
                },
                "attachments": [],
            },
        )

        result = run_watch_cycle(
            store=store,
            events_url="fixture-events.json",
            cache_dir=tmp_path / "cache",
            client_factory=DecisionClient,
            notification_sender=lambda recipient, content: (
                sent.append((recipient, content["subject"])) or {"transport": "fixture"}
            ),
            use_embeddings=False,
        )

        alerts = store.list_user_notifications(user_id="user-1")
        assert result["processed"][0]["classification"] == "matched"
        assert result["deliveries"][0]["status"] == "sent"
        assert sent == [("student@example.test", "[부산대 공지 알림] 2026 여름계절수업 폐강 강좌 안내")]
        assert alerts[0]["delivery_status"] == "sent"
        assert alerts[0]["facts"][0]["evidence_ids"] == ["E001"]
        assert alerts[0]["evidence"][0]["source_name"] == "공지 본문"
