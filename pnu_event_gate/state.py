from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Cursor:
    last_seen_event_id: str | None = None
    last_seen_at: str | None = None

    def is_empty(self) -> bool:
        return not self.last_seen_event_id and not self.last_seen_at

    def to_json(self) -> dict[str, str | None]:
        return {
            "last_seen_event_id": self.last_seen_event_id,
            "last_seen_at": self.last_seen_at,
        }


class EventGateState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    @property
    def cursor(self) -> Cursor:
        cursor = self.data.get("cursor") or {}
        return Cursor(
            last_seen_event_id=cursor.get("last_seen_event_id"),
            last_seen_at=cursor.get("last_seen_at"),
        )

    def update_fetch_metadata(
        self,
        *,
        etag: str | None,
        last_modified: str | None,
        checked_at: str,
    ) -> None:
        self.data = {
            **self.data,
            "last_checked_at": checked_at,
            "http_cache": {
                **(self.data.get("http_cache") or {}),
                "etag": etag,
                "last_modified": last_modified,
            },
        }

    def update_cursor(
        self,
        *,
        last_seen_event_id: str | None,
        last_seen_at: str | None,
        acked_at: str,
    ) -> None:
        self.data = {
            **self.data,
            "cursor": Cursor(last_seen_event_id, last_seen_at).to_json(),
            "last_acked_at": acked_at,
        }

    def http_headers(self) -> dict[str, str]:
        cache = self.data.get("http_cache") or {}
        headers: dict[str, str] = {}
        if cache.get("etag"):
            headers["If-None-Match"] = str(cache["etag"])
        if cache.get("last_modified"):
            headers["If-Modified-Since"] = str(cache["last_modified"])
        return headers

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1}
        return json.loads(self.path.read_text(encoding="utf-8"))

