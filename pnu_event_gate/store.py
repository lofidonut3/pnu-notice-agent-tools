from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .state import Cursor


SCHEMA_VERSION = 1


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class NoticeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> NoticeStore:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            create table if not exists meta (
              key text primary key,
              value text not null
            );

            create table if not exists scan_state (
              id text primary key,
              last_seen_event_id text,
              last_seen_at text,
              last_checked_at text,
              last_feed_generated_at text,
              http_etag text,
              http_last_modified text,
              last_run_status text,
              warnings_json text
            );

            create table if not exists profiles (
              watch_id text not null,
              revision text not null,
              enabled integer not null,
              type text not null,
              request text not null,
              profile_json text not null,
              created_at text not null,
              updated_at text not null,
              active_from_seen_at text,
              primary key (watch_id, revision)
            );

            create table if not exists candidates (
              candidate_id text primary key,
              watch_id text not null,
              profile_revision text not null,
              event_id text not null,
              notice_id text,
              seen_at text,
              source_id text,
              same_notice_group_id text,
              status text not null,
              score integer not null,
              action text not null,
              match_json text not null,
              event_json text not null,
              materials_json text,
              result_json text,
              attempts integer not null default 0,
              last_error text,
              created_at text not null,
              updated_at text not null,
              unique (watch_id, profile_revision, event_id)
            );

            create table if not exists notification_receipts (
              receipt_id text primary key,
              candidate_id text not null,
              watch_id text not null,
              event_id text not null,
              channel text,
              payload_hash text,
              status text not null,
              created_at text not null,
              sent_at text,
              metadata_json text
            );

            create table if not exists runs (
              run_id text primary key,
              command text not null,
              started_at text not null,
              finished_at text,
              status text not null,
              input_event_count integer,
              candidate_count integer,
              warnings_json text
            );
            """
        )
        self.connection.execute(
            "insert or replace into meta (key, value) values ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def http_headers(self) -> dict[str, str]:
        row = self._scan_state_row()
        if row is None:
            return {}
        headers: dict[str, str] = {}
        if row["http_etag"]:
            headers["If-None-Match"] = str(row["http_etag"])
        if row["http_last_modified"]:
            headers["If-Modified-Since"] = str(row["http_last_modified"])
        return headers

    def scan_cursor(self) -> Cursor:
        row = self._scan_state_row()
        if row is None:
            return Cursor()
        return Cursor(
            last_seen_event_id=row["last_seen_event_id"],
            last_seen_at=row["last_seen_at"],
        )

    def update_scan_state(
        self,
        *,
        cursor: Cursor,
        checked_at: str,
        feed_generated_at: str | None,
        etag: str | None,
        last_modified: str | None,
        status: str,
        warnings: list[str],
    ) -> None:
        self.connection.execute(
            """
            insert into scan_state (
              id, last_seen_event_id, last_seen_at, last_checked_at,
              last_feed_generated_at, http_etag, http_last_modified,
              last_run_status, warnings_json
            )
            values ('default', ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              last_seen_event_id = excluded.last_seen_event_id,
              last_seen_at = excluded.last_seen_at,
              last_checked_at = excluded.last_checked_at,
              last_feed_generated_at = excluded.last_feed_generated_at,
              http_etag = excluded.http_etag,
              http_last_modified = excluded.http_last_modified,
              last_run_status = excluded.last_run_status,
              warnings_json = excluded.warnings_json
            """,
            (
                cursor.last_seen_event_id,
                cursor.last_seen_at,
                checked_at,
                feed_generated_at,
                etag,
                last_modified,
                status,
                dumps_json(warnings),
            ),
        )

    def upsert_profile(self, profile: dict[str, Any], *, now: str) -> dict[str, Any]:
        watch_id = str(profile["id"])
        revision = str(profile.get("revision") or "1")
        enabled = 1 if profile.get("enabled", True) else 0
        if enabled:
            self.connection.execute(
                "update profiles set enabled = 0, updated_at = ? where watch_id = ? and revision != ?",
                (now, watch_id, revision),
            )
        existing = self.connection.execute(
            "select created_at from profiles where watch_id = ? and revision = ?",
            (watch_id, revision),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self.connection.execute(
            """
            insert into profiles (
              watch_id, revision, enabled, type, request, profile_json,
              created_at, updated_at, active_from_seen_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(watch_id, revision) do update set
              enabled = excluded.enabled,
              type = excluded.type,
              request = excluded.request,
              profile_json = excluded.profile_json,
              updated_at = excluded.updated_at,
              active_from_seen_at = excluded.active_from_seen_at
            """,
            (
                watch_id,
                revision,
                enabled,
                str(profile.get("type") or "recurring"),
                str(profile.get("request") or ""),
                dumps_json(profile),
                created_at,
                now,
                (profile.get("baseline") or {}).get("from_seen_at"),
            ),
        )
        return {
            "watch_id": watch_id,
            "revision": revision,
            "enabled": bool(enabled),
            "created_at": created_at,
            "updated_at": now,
            "profile": profile,
        }

    def list_profiles(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        where = "" if include_disabled else "where enabled = 1"
        rows = self.connection.execute(
            f"select * from profiles {where} order by watch_id, revision"
        ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_profile(self, watch_id: str, revision: str | None = None) -> dict[str, Any]:
        if revision is None:
            row = self.connection.execute(
                """
                select * from profiles
                where watch_id = ? and enabled = 1
                order by updated_at desc
                limit 1
                """,
                (watch_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "select * from profiles where watch_id = ? and revision = ?",
                (watch_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError(f"profile not found: {watch_id}")
        return self._profile_from_row(row)

    def disable_profile(self, watch_id: str, *, now: str) -> int:
        cursor = self.connection.execute(
            "update profiles set enabled = 0, updated_at = ? where watch_id = ? and enabled = 1",
            (now, watch_id),
        )
        return int(cursor.rowcount or 0)

    def insert_candidate(self, candidate: dict[str, Any]) -> bool:
        cursor = self.connection.execute(
            """
            insert or ignore into candidates (
              candidate_id, watch_id, profile_revision, event_id, notice_id,
              seen_at, source_id, same_notice_group_id, status, score, action,
              match_json, event_json, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["candidate_id"],
                candidate["watch_id"],
                candidate["profile_revision"],
                candidate["event_id"],
                candidate.get("notice_id"),
                candidate.get("seen_at"),
                candidate.get("source_id"),
                candidate.get("same_notice_group_id"),
                candidate["status"],
                candidate["score"],
                candidate["action"],
                dumps_json(candidate["match"]),
                dumps_json(candidate["event"]),
                candidate["created_at"],
                candidate["updated_at"],
            ),
        )
        return int(cursor.rowcount or 0) > 0

    def list_candidates(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.connection.execute(
                "select * from candidates where status = ? order by created_at, candidate_id",
                (status,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "select * from candidates order by created_at, candidate_id"
            ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "select * from candidates where candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"candidate not found: {candidate_id}")
        return self._candidate_from_row(row)

    def update_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        now: str,
        materials: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> dict[str, Any]:
        current = self.get_candidate(candidate_id)
        attempts = int(current.get("attempts") or 0) + (1 if increment_attempts else 0)
        self.connection.execute(
            """
            update candidates set
              status = ?,
              materials_json = coalesce(?, materials_json),
              result_json = coalesce(?, result_json),
              attempts = ?,
              last_error = ?,
              updated_at = ?
            where candidate_id = ?
            """,
            (
                status,
                dumps_json(materials) if materials is not None else None,
                dumps_json(result) if result is not None else None,
                attempts,
                error,
                now,
                candidate_id,
            ),
        )
        return self.get_candidate(candidate_id)

    def record_receipt(self, receipt: dict[str, Any]) -> bool:
        cursor = self.connection.execute(
            """
            insert or ignore into notification_receipts (
              receipt_id, candidate_id, watch_id, event_id, channel,
              payload_hash, status, created_at, sent_at, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["receipt_id"],
                receipt["candidate_id"],
                receipt["watch_id"],
                receipt["event_id"],
                receipt.get("channel"),
                receipt.get("payload_hash"),
                receipt["status"],
                receipt["created_at"],
                receipt.get("sent_at"),
                dumps_json(receipt.get("metadata") or {}),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    def status_summary(self) -> dict[str, Any]:
        scan = self._scan_state_row()
        profile_rows = self.connection.execute(
            "select enabled, count(*) as count from profiles group by enabled"
        ).fetchall()
        candidate_rows = self.connection.execute(
            "select status, count(*) as count from candidates group by status"
        ).fetchall()
        return {
            "type": "pnu_notice_status",
            "scan": self._scan_summary(scan),
            "profiles": {
                "active_count": sum(int(row["count"]) for row in profile_rows if int(row["enabled"]) == 1),
                "disabled_count": sum(int(row["count"]) for row in profile_rows if int(row["enabled"]) == 0),
            },
            "candidates": {
                str(row["status"]): int(row["count"])
                for row in candidate_rows
            },
        }

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def _scan_state_row(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "select * from scan_state where id = 'default'"
        ).fetchone()

    def _profile_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "watch_id": row["watch_id"],
            "revision": row["revision"],
            "enabled": bool(row["enabled"]),
            "type": row["type"],
            "request": row["request"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "active_from_seen_at": row["active_from_seen_at"],
            "profile": loads_json(row["profile_json"], {}),
        }

    def _candidate_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "watch_id": row["watch_id"],
            "profile_revision": row["profile_revision"],
            "event_id": row["event_id"],
            "notice_id": row["notice_id"],
            "seen_at": row["seen_at"],
            "source_id": row["source_id"],
            "same_notice_group_id": row["same_notice_group_id"],
            "status": row["status"],
            "score": row["score"],
            "action": row["action"],
            "match": loads_json(row["match_json"], {}),
            "event": loads_json(row["event_json"], {}),
            "materials": loads_json(row["materials_json"], None),
            "result": loads_json(row["result_json"], None),
            "attempts": row["attempts"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _scan_summary(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {
                "last_seen_event_id": None,
                "last_seen_at": None,
                "last_checked_at": None,
                "last_feed_generated_at": None,
                "last_run_status": "never",
                "warnings": [],
            }
        return {
            "last_seen_event_id": row["last_seen_event_id"],
            "last_seen_at": row["last_seen_at"],
            "last_checked_at": row["last_checked_at"],
            "last_feed_generated_at": row["last_feed_generated_at"],
            "last_run_status": row["last_run_status"],
            "warnings": loads_json(row["warnings_json"], []),
        }
