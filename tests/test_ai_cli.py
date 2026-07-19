import json
from pathlib import Path
from typing import Any

from pnu_event_gate.cli import main


class DecisionClient:
    def __init__(self) -> None:
        self.chat_calls = 0

    def chat_json(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        self.chat_calls += 1
        return {
            "classification": "matched",
            "confidence": 0.95,
            "summary": "The requested section is cancelled.",
            "facts": [
                {
                    "text": "Database section 001 is cancelled.",
                    "evidence_ids": ["E001"],
                }
            ],
            "evidence_ids": ["E001"],
            "missing_information": [],
        }


def test_analyze_loads_compiled_profile_from_sqlite(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    db_path = tmp_path / "state.sqlite3"
    profile_path = tmp_path / "profile.json"
    evidence_path = tmp_path / "evidence.json"
    profile_path.write_text(
        json.dumps(
            {
                "id": "summer-db-001",
                "revision": "1",
                "request": "database section 001 cancellation",
                "positive_terms": ["database", "cancellation"],
                "compiled_intent": {
                    "event_type": "course_cancelled",
                    "entities": [
                        {"type": "course", "value": "database"},
                        {"type": "section", "value": "001"},
                    ],
                    "exact_terms": ["database", "001"],
                },
            }
        ),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "id": "E001",
                        "source_name": "cancellation list",
                        "text": "course=database | section=001 | status=cancelled",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "profile",
            "upsert",
            "--db",
            str(db_path),
            "--profile-json",
            str(profile_path),
        ]
    ) == 0
    capsys.readouterr()

    client = DecisionClient()

    class ClientFactory:
        @staticmethod
        def from_env(_api_key_env: str) -> DecisionClient:
            return client

    monkeypatch.setattr("pnu_event_gate.cli.NvidiaClient", ClientFactory)

    assert main(
        [
            "analyze",
            "--watch-id",
            "summer-db-001",
            "--db",
            str(db_path),
            "--evidence-json",
            str(evidence_path),
            "--no-embeddings",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert client.chat_calls == 1
    assert payload["intent"]["event_type"] == "course_cancelled"
    assert payload["decision"]["classification"] == "matched"
