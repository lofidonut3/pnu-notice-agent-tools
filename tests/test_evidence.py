from __future__ import annotations

import json
from pathlib import Path

from pnu_event_gate.cli import main
from pnu_event_gate.evidence import (
    VISUAL_PENDING_TEXT,
    EvidenceChunk,
    evidence_from_materials,
    lexical_rank,
    redact_sensitive_text,
)


def test_material_extraction_redacts_contact_data_but_preserves_dates(tmp_path: Path) -> None:
    attachment = tmp_path / "course.txt"
    attachment.write_text(
        "20260719 폐강 강좌 데이터베이스 001분반 문의 051-510-1234 abc@pusan.ac.kr",
        encoding="utf-8",
    )
    manifest = {
        "notice": {"title": "2026 여름계절수업 폐강 안내"},
        "detail": {"text_preview": "폐강 강좌는 첨부파일을 확인하세요."},
        "attachments": [
            {
                "name": "폐강목록.txt",
                "local_path": str(attachment),
                "fetch_status": "ok",
            }
        ],
    }

    bundle = evidence_from_materials(manifest)
    combined = "\n".join(chunk.text for chunk in bundle.chunks)

    assert "20260719" in combined
    assert "[PHONE_REDACTED]" in combined
    assert "[EMAIL_REDACTED]" in combined
    assert bundle.redaction_counts["student_id"] == 0


def test_lexical_rank_prioritizes_exact_course_and_section() -> None:
    chunks = [
        EvidenceChunk(id="E001", source_name="목록", text="자료구조 002분반 개설"),
        EvidenceChunk(id="E002", source_name="목록", text="데이터베이스 001분반 폐강"),
    ]

    ranked = lexical_rank("데이터베이스 001분반 폐강되면 알려줘", chunks, limit=2)

    assert ranked[0][0].id == "E002"
    assert ranked[0][1] > ranked[1][1]


def test_cli_analyze_dry_run_needs_no_api_key(tmp_path: Path, capsys) -> None:
    attachment = tmp_path / "notice.txt"
    attachment.write_text("데이터베이스 001분반 폐강", encoding="utf-8")
    manifest = tmp_path / "materials.json"
    manifest.write_text(
        json.dumps(
            {
                "notice": {"title": "계절수업 폐강 안내"},
                "detail": {"text_preview": "첨부 참조"},
                "attachments": [
                    {
                        "name": "폐강목록.txt",
                        "local_path": str(attachment),
                        "fetch_status": "ok",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "analyze",
                "--request",
                "데이터베이스 001분반 폐강되면 알려줘",
                "--materials-json",
                str(manifest),
                "--dry-run",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "pnu_notice_ai_evidence"
    assert payload["evidence"]["source_count"] == 2
    assert any("데이터베이스" in item["text"] for item in payload["evidence"]["chunks"])


def test_redaction_catches_student_id_without_matching_eight_digit_date() -> None:
    text, counts = redact_sensitive_text("학번 2026123456, 기준일 20260719")
    assert "[STUDENT_ID_REDACTED]" in text
    assert "20260719" in text
    assert counts["student_id"] == 1


def test_image_attachment_is_queued_for_multimodal_transcription(tmp_path: Path) -> None:
    image = tmp_path / "poster.png"
    image.write_bytes(b"not decoded during deterministic extraction")
    bundle = evidence_from_materials(
        {
            "detail": {},
            "attachments": [
                {
                    "name": "poster.png",
                    "local_path": str(image),
                    "fetch_status": "ok",
                }
            ],
        }
    )

    assert len(bundle.chunks) == 1
    assert bundle.chunks[0].kind == "image_visual"
    assert bundle.chunks[0].text == VISUAL_PENDING_TEXT


def test_image_attachment_uses_local_ocr_before_visual_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image = tmp_path / "cancelled-list.png"
    image.write_bytes(b"fake image")
    monkeypatch.setattr(
        "pnu_event_gate.evidence.try_local_ocr_image",
        lambda _path: ("database 001 cancelled", "local OCR used"),
    )

    bundle = evidence_from_materials(
        {
            "detail": {},
            "attachments": [
                {
                    "name": "cancelled-list.png",
                    "local_path": str(image),
                    "fetch_status": "ok",
                }
            ],
        }
    )

    assert bundle.chunks[0].kind == "image_ocr"
    assert bundle.chunks[0].text == "database 001 cancelled"
    assert "local OCR used" in bundle.warnings
