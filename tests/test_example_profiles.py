from pathlib import Path

from pnu_event_gate.matcher import match_event
from pnu_event_gate.profiles import load_profile


EXAMPLE = Path("examples/watches/summer-2026-database-001-cancelled.json")


def test_course_cancellation_example_is_a_complete_worker_profile() -> None:
    profile = load_profile(str(EXAMPLE))

    assert profile["id"] == "summer-2026-database-001-cancelled"
    assert profile["compiled_intent"]["event_type"] == "course_cancelled"
    assert profile["compiled_intent"]["entities"] == [
        {"type": "course", "value": "데이터베이스", "required": True},
        {"type": "section", "value": "001", "required": True},
        {
            "type": "academic_term",
            "value": "2026 여름계절수업",
            "required": True,
        },
    ]


def test_course_cancellation_example_gates_broad_notice_for_ai_review() -> None:
    profile = load_profile(str(EXAMPLE))
    result = match_event(
        profile,
        {
            "title": "2026학년도 여름계절수업 폐강강좌 안내",
            "snippet": "폐강 대상 교과목은 첨부파일을 확인하세요.",
            "attachments": [{"name": "2026 여름계절수업 폐강강좌.xlsx"}],
        },
    )

    assert result.matched is True
    assert result.action == "invoke_agent"


def test_course_cancellation_example_skips_unrelated_academic_notice() -> None:
    profile = load_profile(str(EXAMPLE))
    result = match_event(
        profile,
        {
            "title": "2026학년도 2학기 전과 시행계획 안내",
            "snippet": "지원 자격과 제출 서류를 확인하세요.",
            "attachments": [{"name": "전과 시행계획.pdf"}],
        },
    )

    assert result.matched is False
    assert result.action == "skip"
