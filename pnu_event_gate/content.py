from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .events import now_iso


DEFAULT_CACHE_DIR = Path(".pnu-notice-cache/materials")
DEFAULT_MAX_TEXT_CHARS = 12000
DEFAULT_MAX_FILE_BYTES = 10_000_000
DEFAULT_MAX_TOTAL_BYTES = 30_000_000
DEFAULT_MAX_ATTACHMENT_BYTES = DEFAULT_MAX_FILE_BYTES
DEFAULT_SMALL_ATTACHMENT_COUNT = 3

ATTACHMENT_PLAN_STOP_TERMS = {
    "공지",
    "관련",
    "첨부",
    "첨부파일",
    "파일",
    "알려줘",
    "알려주세요",
    "되면",
    "대한",
    "해당",
    "notice",
    "attachment",
    "file",
}

ATTACHMENT_EXTENSIONS = {
    "csv",
    "doc",
    "docx",
    "hwp",
    "hwpx",
    "pdf",
    "ppt",
    "pptx",
    "txt",
    "xls",
    "xlsx",
    "zip",
}
CUSTOM_MEDIA_TYPES = {
    "hwp": "application/x-hwp",
    "hwpx": "application/x-hwpx",
}


@dataclass(frozen=True)
class Resource:
    url: str
    status_code: int | None
    media_type: str
    charset: str | None
    body: bytes


class ResourceTooLargeError(Exception):
    def __init__(self, url: str, max_bytes: int) -> None:
        super().__init__(f"resource exceeds max bytes: {url}")
        self.url = url
        self.max_bytes = max_bytes


def load_notice_input(path: str, event_index: int) -> dict[str, Any]:
    raw = sys_stdin_text() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        events = data.get("events") or []
        if not events:
            raise ValueError("event payload contains no events")
        if event_index < 0 or event_index >= len(events):
            raise ValueError(f"event index out of range: {event_index}")
        selected = events[event_index]
        if not isinstance(selected, dict):
            raise ValueError(f"event at index {event_index} is not an object")
        return selected
    if not isinstance(data, dict):
        raise ValueError("event JSON must be an object")
    return data


def sys_stdin_text() -> str:
    import sys

    return sys.stdin.read()


def build_direct_notice(url: str) -> dict[str, Any]:
    return {
        "notice_id": None,
        "source_id": None,
        "source_name": None,
        "title": None,
        "url": url,
        "content_access": {
            "detail_url": url,
            "requires_login": False,
            "content_mirrored": False,
            "attachments_mirrored": False,
        },
        "attachments": [],
    }


def resolve_notice_materials(
    notice: dict[str, Any],
    *,
    override_url: str | None,
    download_attachments: bool,
    cache_dir: Path,
    max_text_chars: int,
    max_file_bytes: int,
    max_total_bytes: int,
    attachment_policy: str | None = None,
    watch_request: str | None = None,
    selected_attachment_indices: set[int] | None = None,
) -> dict[str, Any]:
    detail_url = notice_detail_url(notice, override_url)
    if not detail_url:
        raise ValueError("notice has no detail URL")

    material_dir = material_directory(cache_dir, notice, detail_url)
    detail, detail_html = materialize_detail(
        detail_url,
        material_dir=material_dir,
        max_text_chars=max_text_chars,
        max_file_bytes=max_file_bytes,
    )
    detail_bytes = detail.get("bytes") if isinstance(detail.get("bytes"), int) else 0
    remaining_total_bytes = max(0, max_total_bytes - detail_bytes)
    raw_attachments = notice_attachments(notice)
    attachments = raw_attachments or (
        extract_attachment_links(detail_html, detail_url) if detail_html else []
    )
    policy = attachment_policy or ("all" if download_attachments else "none")
    attachment_plan = plan_attachment_downloads(
        attachments,
        policy=policy,
        watch_request=watch_request,
        selected_indices=selected_attachment_indices,
    )
    selected_indices = set(attachment_plan["selected_indices"])
    attachment_payloads: list[dict[str, Any]] = []

    for index, attachment in enumerate(attachments):
        selected = index in selected_indices
        payload = materialize_attachment(
            index,
            attachment,
            material_dir=material_dir,
            download=selected,
            max_file_bytes=max_file_bytes,
            remaining_total_bytes=remaining_total_bytes,
            skip_status=(
                "not_requested" if policy == "none" else "not_selected"
            ),
        )
        attachment_payloads = [*attachment_payloads, payload]
        payload_bytes = payload.get("bytes") if isinstance(payload.get("bytes"), int) else 0
        remaining_total_bytes = max(0, remaining_total_bytes - payload_bytes)

    return {
        "type": "pnu_notice_materials",
        "resolved_at": now_iso(),
        "notice": compact_notice(notice, detail_url),
        "detail": detail,
        "attachments": attachment_payloads,
        "attachment_plan": attachment_plan,
        "limits": {
            "max_file_bytes": max_file_bytes,
            "max_total_bytes": max_total_bytes,
        },
        "warnings": [],
    }


def notice_detail_url(notice: dict[str, Any], override_url: str | None) -> str | None:
    content_access = notice.get("content_access") or {}
    item = notice.get("item") or {}
    pnu = item.get("_pnu") or {}
    item_access = pnu.get("content_access") or {}
    return (
        override_url
        or content_access.get("detail_url")
        or item_access.get("detail_url")
        or notice.get("url")
        or item.get("url")
    )


def notice_attachments(notice: dict[str, Any]) -> list[dict[str, Any]]:
    item = notice.get("item") or {}
    pnu = item.get("_pnu") or {}
    raw_attachments = notice.get("attachments") or pnu.get("attachments") or []
    return [
        attachment
        for attachment in raw_attachments
        if isinstance(attachment, dict)
    ]


def compact_notice(notice: dict[str, Any], detail_url: str) -> dict[str, Any]:
    item = notice.get("item") or {}
    pnu = item.get("_pnu") or {}
    return {
        "event_id": notice.get("event_id"),
        "notice_id": notice.get("notice_id") or item.get("id"),
        "source_id": notice.get("source_id") or pnu.get("source_id"),
        "source_name": notice.get("source_name") or pnu.get("source_name"),
        "title": notice.get("title") or item.get("title"),
        "url": notice.get("url") or item.get("url") or detail_url,
        "detail_url": detail_url,
    }


def material_directory(cache_dir: Path, notice: dict[str, Any], detail_url: str) -> Path:
    item = notice.get("item") or {}
    notice_id = notice.get("notice_id") or item.get("id")
    source = str(notice_id or detail_url)
    slug = safe_slug(source)
    if not notice_id:
        slug = f"url-{sha256_text(detail_url)[:12]}"
    return cache_dir / slug


def materialize_detail(
    url: str,
    *,
    material_dir: Path,
    max_text_chars: int,
    max_file_bytes: int,
) -> tuple[dict[str, Any], str | None]:
    try:
        resource = fetch_resource(url, max_bytes=max_file_bytes)
    except ResourceTooLargeError as error:
        return (
            {
                "url": url,
                "local_path": None,
                "media_type": guess_media_type(url),
                "bytes": None,
                "sha256": None,
                "fetch_status": "oversized",
                "max_file_bytes": error.max_bytes,
            },
            None,
        )
    except Exception as error:  # noqa: BLE001 - expose fetch failure in manifest.
        return (
            {
                "url": url,
                "local_path": None,
                "media_type": guess_media_type(url),
                "bytes": None,
                "sha256": None,
                "fetch_status": "failed",
                "error": str(error),
            },
            None,
        )

    path = material_dir / detail_filename(resource)
    write_bytes(path, resource.body)
    detail = {
        "url": url,
        "local_path": path_to_json(path),
        "status_code": resource.status_code,
        "media_type": resource.media_type,
        "charset": resource.charset,
        "bytes": len(resource.body),
        "sha256": sha256_bytes(resource.body),
        "fetch_status": "ok",
    }
    preview = extract_text_resource(resource, max_text_chars=max_text_chars)
    detail = {
        **detail,
        "text_preview": preview["text"],
        "text_preview_truncated": preview["truncated"],
    }
    detail_html = decode_body(resource) if normalized_media_type(resource.media_type) == "text/html" else None
    return detail, detail_html


def materialize_attachment(
    index: int,
    attachment: dict[str, Any],
    *,
    material_dir: Path,
    download: bool,
    max_file_bytes: int,
    remaining_total_bytes: int,
    skip_status: str = "not_requested",
) -> dict[str, Any]:
    base = attachment_base(index, attachment)
    if not download:
        return {
            **base,
            "local_path": None,
            "bytes": None,
            "sha256": None,
            "fetch_status": skip_status,
            "read_hints": read_hints(base.get("file_extension")),
        }

    url = base.get("url")
    if not url:
        return {
            **base,
            "local_path": None,
            "bytes": None,
            "sha256": None,
            "fetch_status": "missing_url",
            "read_hints": read_hints(base.get("file_extension")),
        }
    if remaining_total_bytes <= 0:
        return {
            **base,
            "local_path": None,
            "bytes": None,
            "sha256": None,
            "fetch_status": "total_limit_exceeded",
            "read_hints": read_hints(base.get("file_extension")),
        }

    effective_max_bytes = min(max_file_bytes, remaining_total_bytes)
    try:
        resource = fetch_resource(str(url), max_bytes=effective_max_bytes)
    except ResourceTooLargeError as error:
        fetch_status = (
            "total_limit_exceeded"
            if effective_max_bytes < max_file_bytes
            else "oversized"
        )
        return {
            **base,
            "local_path": None,
            "bytes": None,
            "sha256": None,
            "fetch_status": fetch_status,
            "max_file_bytes": error.max_bytes,
            "read_hints": read_hints(base.get("file_extension")),
        }
    except Exception as error:  # noqa: BLE001 - keep per-attachment failure explicit.
        return {
            **base,
            "local_path": None,
            "bytes": None,
            "sha256": None,
            "fetch_status": "failed",
            "error": str(error),
            "read_hints": read_hints(base.get("file_extension")),
        }

    extension = base.get("file_extension") or extension_from_media_type(resource.media_type)
    path = material_dir / "attachments" / attachment_filename(index, extension)
    write_bytes(path, resource.body)
    return {
        **base,
        "media_type": resource.media_type,
        "file_extension": extension,
        "local_path": path_to_json(path),
        "status_code": resource.status_code,
        "bytes": len(resource.body),
        "sha256": sha256_bytes(resource.body),
        "fetch_status": "ok",
        "read_hints": read_hints(extension),
    }


def plan_attachment_downloads(
    attachments: list[dict[str, Any]],
    *,
    policy: str,
    watch_request: str | None,
    selected_indices: set[int] | None,
    small_attachment_count: int = DEFAULT_SMALL_ATTACHMENT_COUNT,
) -> dict[str, Any]:
    if policy not in {"none", "all", "relevant", "selected"}:
        raise ValueError(f"unsupported attachment policy: {policy}")
    valid_indices = set(range(len(attachments)))
    if policy == "none":
        selected: set[int] = set()
        reasons: dict[str, list[str]] = {}
    elif policy == "all":
        selected = valid_indices
        reasons = {str(index): ["all policy"] for index in selected}
    elif policy == "selected":
        selected = set(selected_indices or set())
        invalid = selected - valid_indices
        if invalid:
            raise ValueError(
                "attachment index out of range: "
                + ", ".join(str(index) for index in sorted(invalid))
            )
        reasons = {str(index): ["explicit index"] for index in selected}
    else:
        if not watch_request or not watch_request.strip():
            raise ValueError("relevant attachment policy requires a watch request")
        request_terms = attachment_match_terms(watch_request)
        reasons = {}
        selected = set()
        if len(attachments) <= small_attachment_count:
            selected = valid_indices
            reasons = {
                str(index): ["small attachment set"]
                for index in selected
            }
        else:
            for index, attachment in enumerate(attachments):
                name = str(attachment.get("name") or "")
                matched_terms = sorted(request_terms.intersection(attachment_match_terms(name)))
                if matched_terms:
                    selected.add(index)
                    reasons[str(index)] = [
                        "filename terms: " + ", ".join(matched_terms)
                    ]
            if not selected:
                selected = valid_indices
                reasons = {
                    str(index): ["no reliable filename signal; conservative fallback"]
                    for index in selected
                }

    return {
        "policy": policy,
        "attachment_count": len(attachments),
        "selected_indices": sorted(selected),
        "reasons": reasons,
    }


def attachment_match_terms(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", " ", value.casefold())
    return {
        term
        for term in normalized.split()
        if len(term) >= 2 and term not in ATTACHMENT_PLAN_STOP_TERMS
    }


def attachment_base(index: int, attachment: dict[str, Any]) -> dict[str, Any]:
    url = attachment.get("url") or attachment.get("download_url")
    name = attachment.get("name") or inferred_name(str(url or "")) or f"attachment-{index}"
    extension = (
        normalize_extension(attachment.get("file_extension"))
        or infer_extension(str(name))
        or infer_extension(str(url or ""))
    )
    media_type = (
        attachment.get("media_type")
        or media_type_for_extension(extension)
        or guess_media_type(str(url or name))
    )
    return {
        "index": index,
        "name": name,
        "url": url,
        "type": attachment.get("type"),
        "media_type": media_type,
        "file_extension": extension,
    }


def extract_attachment_links(html: str, base_url: str) -> list[dict[str, Any]]:
    parser = LinkParser()
    parser.feed(html)
    parser.close()

    attachments: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        url = urllib.parse.urljoin(base_url, link["href"])
        name = normalize_text(link.get("text") or "") or inferred_name(url)
        extension = infer_extension(str(name)) or infer_extension(url)
        if not extension or extension not in ATTACHMENT_EXTENSIONS:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        attachments = [
            *attachments,
            {
                "name": name or f"attachment-{len(attachments)}.{extension}",
                "url": url,
                "file_extension": extension,
                "media_type": media_type_for_extension(extension) or guess_media_type(url),
            },
        ]
    return attachments


def fetch_resource(url: str, *, max_bytes: int | None = None) -> Resource:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        return fetch_http_resource(url, max_bytes=max_bytes)
    if parsed.scheme == "file":
        path = Path(urllib.request.url2pathname(parsed.path))
        return read_file_resource(path, url=url, max_bytes=max_bytes)
    return read_file_resource(Path(url), url=url, max_bytes=max_bytes)


def fetch_http_resource(url: str, *, max_bytes: int | None) -> Resource:
    with urllib.request.urlopen(url, timeout=20) as response:
        read_size = max_bytes + 1 if max_bytes is not None else -1
        body = response.read(read_size)
        if max_bytes is not None and len(body) > max_bytes:
            raise ResourceTooLargeError(url, max_bytes)
        media_type, charset = parse_content_type(response.headers.get("Content-Type"))
        return Resource(
            url=url,
            status_code=response.status,
            media_type=media_type or guess_media_type(url),
            charset=charset,
            body=body,
        )


def read_file_resource(path: Path, *, url: str, max_bytes: int | None) -> Resource:
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise ResourceTooLargeError(url, max_bytes)
    data = path.read_bytes()
    return Resource(
        url=url,
        status_code=None,
        media_type=guess_media_type(url),
        charset=None,
        body=data,
    )


def parse_content_type(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [part.strip() for part in value.split(";") if part.strip()]
    media_type = parts[0].lower() if parts else None
    charset_parts = [
        part.split("=", 1)[1].strip("\"'")
        for part in parts[1:]
        if part.lower().startswith("charset=") and "=" in part
    ]
    return media_type, charset_parts[0] if charset_parts else None


def guess_media_type(url: str) -> str:
    extension = infer_extension(url)
    custom = media_type_for_extension(extension)
    if custom:
        return custom
    parsed = urllib.parse.urlparse(url)
    media_type, _encoding = mimetypes.guess_type(parsed.path or url)
    return media_type or "application/octet-stream"


def media_type_for_extension(extension: str | None) -> str | None:
    normalized = normalize_extension(extension)
    return CUSTOM_MEDIA_TYPES.get(normalized or "")


def extension_from_media_type(media_type: str) -> str | None:
    if normalized_media_type(media_type) == "application/pdf":
        return "pdf"
    return None


def extract_text_resource(resource: Resource, *, max_text_chars: int) -> dict[str, Any]:
    if normalized_media_type(resource.media_type) == "text/html":
        text = extract_html_text(decode_body(resource))
        return truncate_text(text, max_text_chars)
    if is_text_compatible_media_type(resource.media_type):
        text = normalize_text(decode_body(resource))
        return truncate_text(text, max_text_chars)
    return {
        "text": None,
        "truncated": False,
    }


def decode_body(resource: Resource) -> str:
    encoding = resource.charset or "utf-8"
    return resource.body.decode(encoding, errors="replace")


def extract_html_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    parser.close()
    return normalize_text(" ".join(parser.parts))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def truncate_text(text: str, max_text_chars: int) -> dict[str, Any]:
    if max_text_chars >= 0 and len(text) > max_text_chars:
        return {
            "text": text[:max_text_chars],
            "truncated": True,
        }
    return {
        "text": text,
        "truncated": False,
    }


def is_text_compatible_media_type(media_type: str) -> bool:
    normalized = normalized_media_type(media_type)
    return (
        normalized.startswith("text/")
        or normalized in {
            "application/json",
            "application/xml",
            "application/xhtml+xml",
            "application/rss+xml",
            "application/atom+xml",
            "application/javascript",
        }
    )


def normalized_media_type(media_type: str | None) -> str:
    return str(media_type or "").lower().split(";", 1)[0].strip()


def infer_extension(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    candidates = [
        parsed.path,
        parsed.query,
        urllib.parse.unquote(value),
    ]
    for candidate in candidates:
        match = re.search(r"\.([A-Za-z0-9]{1,8})(?:$|[?&#\"'])", candidate)
        if match:
            return normalize_extension(match.group(1))
    return None


def normalize_extension(value: Any) -> str | None:
    if not value:
        return None
    return str(value).lower().strip().lstrip(".") or None


def inferred_name(url: str) -> str | None:
    path = urllib.parse.urlparse(url).path
    name = Path(urllib.parse.unquote(path)).name
    return name or None


def detail_filename(resource: Resource) -> str:
    media_type = normalized_media_type(resource.media_type)
    if media_type == "text/html":
        return "detail.html"
    if media_type.startswith("text/"):
        return "detail.txt"
    extension = infer_extension(resource.url)
    return f"detail.{extension}" if extension else "detail.bin"


def attachment_filename(index: int, extension: Any) -> str:
    normalized = normalize_extension(extension)
    suffix = f".{normalized}" if normalized else ".bin"
    return f"{index:02d}{suffix}"


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return slug[:80] or "notice"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def path_to_json(path: Path) -> str:
    return path.as_posix()


def read_hints(extension: Any) -> dict[str, str]:
    normalized = normalize_extension(extension)
    if normalized in {"hwp", "hwpx"}:
        return {
            "gemini": "direct_file_input_candidate",
            "chatgpt": "try_direct_or_convert",
            "claude": "convert_first",
            "local": "libreoffice_or_tika_or_pyhwp",
        }
    if normalized in {"pdf", "xlsx", "xls", "docx", "doc", "pptx", "ppt", "txt", "csv"}:
        return {
            "gemini": "direct_file_input_candidate",
            "chatgpt": "direct_file_input_candidate",
            "claude": "direct_file_input_candidate",
            "local": "direct_or_standard_parser",
        }
    return {
        "gemini": "unknown",
        "chatgpt": "unknown",
        "claude": "unknown",
        "local": "inspect_or_convert",
    }


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts = [*self.parts, " "]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts = [*self.parts, " "]

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        if data.strip():
            self.parts = [*self.parts, data]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.current_href: str | None = None
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        hrefs = [value for name, value in attrs if name.lower() == "href" and value]
        if hrefs:
            self.current_href = hrefs[0]
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self.current_href:
            return
        self.links = [
            *self.links,
            {
                "href": self.current_href,
                "text": normalize_text(" ".join(self.current_text)),
            },
        ]
        self.current_href = None
        self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href and data.strip():
            self.current_text = [*self.current_text, data]
