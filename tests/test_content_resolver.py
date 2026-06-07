from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pnu_event_gate.cli import main


def test_resolve_event_json_materializes_detail_page(tmp_path: Path, capsys) -> None:
    detail_path = tmp_path / "notice.html"
    detail_path.write_text(
        """
        <!doctype html>
        <html>
          <head><title>ignored</title><style>.x { color: red; }</style></head>
          <body>
            <h1>2026학년도 졸업요건 서류 제출 안내</h1>
            <script>ignored()</script>
            <p>제출 기한은 2026년 7월 10일입니다.</p>
            <p>졸업예정자는 어학성적과 비교과 이수 내역을 확인하세요.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    event_path = _write_event(
        tmp_path,
        detail_url=detail_path.as_uri(),
        attachments=[
            {
                "name": "졸업요건 안내.pdf",
                "url": "https://example.test/graduation.pdf",
                "media_type": "application/pdf",
                "file_extension": "pdf",
            }
        ],
    )

    exit_code = main([
        "resolve",
        "--event-json",
        str(event_path),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--max-text-chars",
        "2000",
    ])

    payload = json.loads(capsys.readouterr().out)
    detail_local_path = Path(payload["detail"]["local_path"])
    detail_bytes = detail_local_path.read_bytes()
    assert exit_code == 0
    assert payload["type"] == "pnu_notice_materials"
    assert payload["notice"]["title"] == "졸업요건 공지"
    assert payload["detail"]["url"] == detail_path.as_uri()
    assert payload["detail"]["fetch_status"] == "ok"
    assert payload["detail"]["bytes"] == len(detail_bytes)
    assert payload["detail"]["sha256"] == _sha256(detail_bytes)
    assert "제출 기한은 2026년 7월 10일입니다." in payload["detail"]["text_preview"]
    assert "ignored()" not in payload["detail"]["text_preview"]
    assert payload["attachments"][0]["name"] == "졸업요건 안내.pdf"
    assert payload["attachments"][0]["fetch_status"] == "not_requested"
    assert payload["attachments"][0]["local_path"] is None


def test_resolve_event_gate_payload_uses_selected_event_index(
    tmp_path: Path,
    capsys,
) -> None:
    first_detail = tmp_path / "first.html"
    second_detail = tmp_path / "second.html"
    first_detail.write_text("<html><body>첫 번째 공지</body></html>", encoding="utf-8")
    second_detail.write_text("<html><body>두 번째 공지 본문</body></html>", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "type": "pnu_feed_events",
                "events": [
                    _event(detail_url=first_detail.as_uri(), title="첫 번째"),
                    _event(detail_url=second_detail.as_uri(), title="두 번째"),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main([
        "resolve",
        "--event-json",
        str(payload_path),
        "--event-index",
        "1",
        "--cache-dir",
        str(tmp_path / "cache"),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["notice"]["title"] == "두 번째"
    assert "두 번째 공지 본문" in payload["detail"]["text_preview"]


def test_resolve_downloads_text_attachment_as_material(
    tmp_path: Path,
    capsys,
) -> None:
    detail_path = tmp_path / "notice.html"
    attachment_path = tmp_path / "guide.txt"
    detail_path.write_text("<html><body>본문</body></html>", encoding="utf-8")
    attachment_path.write_text("첨부파일 안내 텍스트", encoding="utf-8")
    event_path = _write_event(
        tmp_path,
        detail_url=detail_path.as_uri(),
        attachments=[
            {
                "name": "guide.txt",
                "url": attachment_path.as_uri(),
                "media_type": "text/plain",
                "file_extension": "txt",
            }
        ],
    )

    assert main([
        "resolve",
        "--event-json",
        str(event_path),
        "--download-attachments",
        "--cache-dir",
        str(tmp_path / "cache"),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    attachment = payload["attachments"][0]
    attachment_local_path = Path(attachment["local_path"])
    attachment_bytes = attachment_local_path.read_bytes()
    assert attachment["fetch_status"] == "ok"
    assert attachment["bytes"] == len(attachment_bytes)
    assert attachment["sha256"] == _sha256(attachment_bytes)
    assert attachment_bytes.decode("utf-8") == "첨부파일 안내 텍스트"
    assert "text" not in attachment


def test_resolve_downloads_binary_attachment_without_parsing(
    tmp_path: Path,
    capsys,
) -> None:
    detail_path = tmp_path / "notice.html"
    attachment_path = tmp_path / "guide.pdf"
    detail_path.write_text("<html><body>본문</body></html>", encoding="utf-8")
    attachment_path.write_bytes(b"%PDF-1.7 binary")
    event_path = _write_event(
        tmp_path,
        detail_url=detail_path.as_uri(),
        attachments=[
            {
                "name": "guide.pdf",
                "url": attachment_path.as_uri(),
                "media_type": "application/pdf",
                "file_extension": "pdf",
            }
        ],
    )

    assert main([
        "resolve",
        "--event-json",
        str(event_path),
        "--download-attachments",
        "--cache-dir",
        str(tmp_path / "cache"),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    attachment = payload["attachments"][0]
    assert attachment["fetch_status"] == "ok"
    assert Path(attachment["local_path"]).read_bytes() == b"%PDF-1.7 binary"
    assert attachment["read_hints"]["chatgpt"] == "direct_file_input_candidate"
    assert "extraction_status" not in attachment


def test_resolve_direct_url_derives_attachment_links_from_detail_page(
    tmp_path: Path,
    capsys,
) -> None:
    detail_path = tmp_path / "notice.html"
    attachment_path = tmp_path / "guide.pdf"
    attachment_path.write_bytes(b"%PDF-1.7 binary")
    detail_path.write_text(
        f"""
        <html>
          <body>
            <p>직접 URL 본문</p>
            <a href="{attachment_path.name}">지원서 PDF</a>
            <a href="/site-map">사이트맵</a>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    assert main([
        "resolve",
        "--url",
        detail_path.as_uri(),
        "--cache-dir",
        str(tmp_path / "cache"),
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["notice"]["url"] == detail_path.as_uri()
    assert "직접 URL 본문" in payload["detail"]["text_preview"]
    assert len(payload["attachments"]) == 1
    assert payload["attachments"][0]["name"] == "지원서 PDF"
    assert payload["attachments"][0]["url"] == attachment_path.as_uri()
    assert payload["attachments"][0]["file_extension"] == "pdf"
    assert payload["attachments"][0]["fetch_status"] == "not_requested"


def test_resolve_marks_oversized_attachment_without_writing_file(
    tmp_path: Path,
    capsys,
) -> None:
    detail_path = tmp_path / "notice.html"
    attachment_path = tmp_path / "guide.txt"
    detail_path.write_text("<html><body>본문</body></html>", encoding="utf-8")
    attachment_path.write_text("1234567890", encoding="utf-8")
    event_path = _write_event(
        tmp_path,
        detail_url=detail_path.as_uri(),
        attachments=[
            {
                "name": "guide.txt",
                "url": attachment_path.as_uri(),
                "media_type": "text/plain",
                "file_extension": "txt",
            }
        ],
    )

    assert main([
        "resolve",
        "--event-json",
        str(event_path),
        "--download-attachments",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--max-file-bytes",
        "5",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["detail"]["fetch_status"] == "oversized"
    assert payload["attachments"][0]["fetch_status"] == "oversized"
    assert payload["attachments"][0]["local_path"] is None


def _write_event(
    tmp_path: Path,
    *,
    detail_url: str,
    attachments: list[dict] | None = None,
) -> Path:
    path = tmp_path / "event.json"
    path.write_text(
        json.dumps(_event(detail_url=detail_url, attachments=attachments), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _event(
    *,
    detail_url: str,
    title: str = "졸업요건 공지",
    attachments: list[dict] | None = None,
) -> dict:
    return {
        "event_id": "event-1",
        "notice_id": "pnu-test:1",
        "source_id": "pnu-test",
        "source_name": "부산대 테스트",
        "title": title,
        "url": detail_url,
        "content_access": {
            "detail_url": detail_url,
            "requires_login": False,
            "content_mirrored": False,
            "attachments_mirrored": False,
        },
        "attachments": attachments or [],
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
