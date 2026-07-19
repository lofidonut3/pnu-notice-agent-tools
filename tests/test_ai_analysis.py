from __future__ import annotations

from typing import Any

from pnu_event_gate.analysis import (
    hydrate_visual_evidence,
    intent_to_profile,
    run_ai_analysis,
    select_visual_evidence_ids,
    validate_decision,
)
from pnu_event_gate.evidence import VISUAL_PENDING_TEXT, EvidenceBundle, EvidenceChunk


class FakeClient:
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
        if self.chat_calls == 1:
            return {
                "event_type": "course_cancelled",
                "entities": [
                    {"type": "course", "value": "데이터베이스", "required": True},
                    {"type": "section", "value": "001", "required": True},
                ],
                "exact_terms": ["데이터베이스", "001분반"],
            }
        return {
            "classification": "matched",
            "confidence": 0.97,
            "summary": "데이터베이스 001분반이 폐강 목록에 있습니다.",
            "facts": [
                {"text": "데이터베이스 001분반 폐강", "evidence_ids": ["E002"]}
            ],
            "evidence_ids": ["E002"],
            "missing_information": [],
        }

    def embeddings(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str,
    ) -> list[list[float]]:
        if input_type == "query":
            return [[1.0, 0.0]]
        return [
            [1.0, 0.0] if "데이터베이스" in text else [0.0, 1.0]
            for text in texts
        ]


def test_run_ai_analysis_produces_grounded_email() -> None:
    evidence = EvidenceBundle(
        chunks=[
            EvidenceChunk(id="E001", source_name="본문", text="계절수업 폐강 안내"),
            EvidenceChunk(
                id="E002",
                source_name="폐강목록.xlsx / 강좌",
                kind="xlsx_row",
                row=14,
                text="교과목명=데이터베이스 | 분반=001 | 상태=폐강",
            ),
        ],
        warnings=[],
        redaction_counts={},
        source_count=2,
    )

    result = run_ai_analysis(
        client=FakeClient(),
        request="2026 여름계절수업 데이터베이스 001분반 폐강되면 알려줘",
        evidence=evidence,
        notice={"title": "2026학년도 여름계절수업 폐강 안내"},
        lexical_pool_size=10,
        top_k=4,
    )

    assert result["should_notify"] is True
    assert result["decision"]["evidence_ids"] == ["E002"]
    assert "데이터베이스 001분반 폐강" in result["email"]["body_text"]
    assert "[E002]" in result["email"]["body_text"]


def test_validate_decision_drops_unknown_citations_and_downgrades_match() -> None:
    decision = validate_decision(
        {
            "classification": "matched",
            "confidence": 0.99,
            "summary": "근거 없는 일치",
            "facts": [{"text": "존재하지 않는 근거", "evidence_ids": ["E999"]}],
            "evidence_ids": ["E999"],
        },
        valid_ids={"E001"},
    )

    assert decision["classification"] == "uncertain"
    assert decision["confidence"] <= 0.49
    assert decision["facts"] == []
    assert decision["evidence_ids"] == []


def test_hydrate_visual_evidence_transcribes_and_redacts(monkeypatch) -> None:
    class VisualClient:
        def chat_json(self, **_kwargs) -> dict[str, Any]:
            return {
                "pages": [
                    {
                        "id": "E001",
                        "text": "신청기간 20260719 문의 test@pusan.ac.kr",
                        "confidence": 0.91,
                        "warnings": [],
                    }
                ]
            }

    monkeypatch.setattr(
        "pnu_event_gate.analysis.visual_data_uri",
        lambda _chunk: "data:image/png;base64,dGVzdA==",
    )
    bundle = EvidenceBundle(
        chunks=[
            EvidenceChunk(
                id="E001",
                source_name="poster.png",
                kind="image_visual",
                text=VISUAL_PENDING_TEXT,
                local_path="poster.png",
            )
        ],
        warnings=[],
        redaction_counts={"email": 0, "phone": 0, "resident_id": 0, "student_id": 0},
        source_count=1,
    )

    hydrated, warnings = hydrate_visual_evidence(
        VisualClient(),
        evidence=bundle,
        model="vision-test",
        max_visual_pages=4,
    )

    assert warnings == []
    assert hydrated.chunks[0].kind == "image_visual_transcript"
    assert "20260719" in hydrated.chunks[0].text
    assert "[EMAIL_REDACTED]" in hydrated.chunks[0].text
    assert hydrated.redaction_counts["email"] == 1


def test_intent_to_profile_keeps_broad_gate_and_exact_attachment_hints() -> None:
    profile = intent_to_profile(
        {
            "request": "데이터베이스 001분반이 폐강되면 알려줘",
            "entities": [
                {"type": "course", "value": "데이터베이스"},
                {"type": "section", "value": "001"},
            ],
            "exact_terms": ["데이터베이스", "001분반"],
            "semantic_terms": ["계절수업", "폐강"],
            "negative_terms": [],
        },
        watch_id="watch-course",
    )

    assert profile["id"] == "watch-course"
    assert "폐강" in profile["positive_terms"]
    assert "데이터베이스" in profile["attachment_hints"]
    assert profile["thresholds"]["candidate"] == 2


def test_select_visual_evidence_prioritizes_request_related_source() -> None:
    bundle = EvidenceBundle(
        chunks=[
            EvidenceChunk(
                id="E001",
                source_name="campus-map.pdf",
                kind="pdf_visual_page",
                page=1,
                text=VISUAL_PENDING_TEXT,
            ),
            EvidenceChunk(
                id="E002",
                source_name="database-cancellation-list.pdf",
                kind="pdf_visual_page",
                page=2,
                text=VISUAL_PENDING_TEXT,
            ),
        ],
        warnings=[],
        redaction_counts={},
        source_count=2,
    )

    selected = select_visual_evidence_ids(
        request="database 001 cancellation",
        intent={"exact_terms": ["database", "001"], "entities": []},
        evidence=bundle,
        max_visual_pages=1,
    )

    assert selected == ["E002"]


def test_run_ai_analysis_reuses_compiled_intent_without_compiler_call() -> None:
    client = FakeClient()
    evidence = EvidenceBundle(
        chunks=[EvidenceChunk(id="E002", source_name="list", text="database 001 cancelled")],
        warnings=[],
        redaction_counts={},
        source_count=1,
    )

    result = run_ai_analysis(
        client=client,
        request="database 001 cancellation",
        evidence=evidence,
        use_embeddings=False,
        compiled_intent={
            "event_type": "course_cancelled",
            "entities": [
                {"type": "course", "value": "database", "required": True},
                {"type": "section", "value": "001", "required": True},
            ],
            "exact_terms": ["database", "001"],
        },
    )

    assert client.chat_calls == 1
    assert result["intent"]["event_type"] == "course_cancelled"
