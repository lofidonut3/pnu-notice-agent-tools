from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .state import Cursor


SCHEMA_VERSION = 3


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class NoticeStore:
    def __init__(self, target: Path | str) -> None:
        self.target = str(target)
        self.backend = (
            "postgres"
            if self.target.startswith(("postgres://", "postgresql://"))
            else "sqlite"
        )
        if self.backend == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError(
                    "Postgres state requires psycopg; install pnu-notice-agent-tools[worker]"
                ) from error
            self.path = None
            self.connection = psycopg.connect(self.target, row_factory=dict_row)
        else:
            self.path = Path(self.target)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.path))
            self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> NoticeStore:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self._executescript(
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

            create table if not exists watch_requests (
              id text primary key,
              user_id text not null,
              request text not null,
              delivery_email text not null,
              enabled integer not null default 1,
              status text not null default 'pending',
              revision integer not null default 1,
              watch_id text,
              profile_revision text,
              compiled_intent_json text,
              last_error text,
              created_at text not null,
              updated_at text not null,
              processed_at text
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

            create table if not exists notification_outbox (
              outbox_id text primary key,
              candidate_id text not null,
              watch_id text not null,
              event_id text not null,
              decision_hash text not null,
              channel text not null,
              recipient text not null,
              payload_json text not null,
              status text not null,
              attempts integer not null default 0,
              next_attempt_at text,
              last_error text,
              created_at text not null,
              updated_at text not null,
              sent_at text,
              unique (watch_id, event_id, decision_hash, channel, recipient)
            );

            create table if not exists user_notifications (
              id text primary key,
              outbox_id text unique,
              user_id text not null,
              watch_request_id text not null,
              watch_id text not null,
              candidate_id text not null unique,
              event_id text not null,
              notice_id text,
              classification text not null,
              delivery_status text not null,
              title text not null,
              summary text not null,
              notice_url text,
              facts_json text not null,
              evidence_json text not null,
              last_error text,
              read_at text,
              created_at text not null,
              updated_at text not null,
              sent_at text
            );

            create table if not exists service_health (
              id text primary key,
              status text not null,
              checked_at text not null,
              feed_generated_at text,
              latest_cycle_at text,
              open_incident_count integer not null default 0,
              summary text not null,
              details_json text not null
            );

            create table if not exists operator_incidents (
              id text primary key,
              fingerprint text not null unique,
              component text not null,
              severity text not null,
              status text not null,
              message text not null,
              first_seen_at text not null,
              last_seen_at text not null,
              notified_at text,
              resolved_at text
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
        cursor = self._execute(
            """
            insert into meta (key, value) values ('schema_version', ?)
            on conflict(key) do update set value = excluded.value
            """,
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def _execute(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.backend == "postgres":
            query = query.replace("?", "%s")
        return self.connection.execute(query, parameters)

    def _executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self.connection.executescript(script)
            return
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(statement)

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
        self._execute(
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
              last_feed_generated_at = coalesce(
                excluded.last_feed_generated_at, scan_state.last_feed_generated_at
              ),
              http_etag = coalesce(excluded.http_etag, scan_state.http_etag),
              http_last_modified = coalesce(
                excluded.http_last_modified, scan_state.http_last_modified
              ),
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
            self._execute(
                "update profiles set enabled = 0, updated_at = ? where watch_id = ? and revision != ?",
                (now, watch_id, revision),
            )
        existing = self._execute(
            "select created_at from profiles where watch_id = ? and revision = ?",
            (watch_id, revision),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self._execute(
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
        rows = self._execute(
            f"select * from profiles {where} order by watch_id, revision"
        ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_profile(self, watch_id: str, revision: str | None = None) -> dict[str, Any]:
        if revision is None:
            row = self._execute(
                """
                select * from profiles
                where watch_id = ? and enabled = 1
                order by updated_at desc
                limit 1
                """,
                (watch_id,),
            ).fetchone()
        else:
            row = self._execute(
                "select * from profiles where watch_id = ? and revision = ?",
                (watch_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError(f"profile not found: {watch_id}")
        return self._profile_from_row(row)

    def disable_profile(self, watch_id: str, *, now: str) -> int:
        cursor = self._execute(
            "update profiles set enabled = 0, updated_at = ? where watch_id = ? and enabled = 1",
            (now, watch_id),
        )
        return int(cursor.rowcount or 0)

    def list_watch_requests(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = "where status = ?" if status else ""
        parameters: tuple[Any, ...] = (status, limit) if status else (limit,)
        rows = self._execute(
            f"""
            select * from watch_requests
            {where}
            order by created_at, id
            limit ?
            """,
            parameters,
        ).fetchall()
        return [self._watch_request_from_row(row) for row in rows]

    def claim_watch_request(self, request_id: str, *, now: str) -> bool:
        cursor = self._execute(
            """
            update watch_requests
            set status = 'processing', updated_at = ?, last_error = null
            where id = ? and status = 'pending'
            """,
            (now, request_id),
        )
        return int(cursor.rowcount or 0) == 1

    def complete_watch_request(
        self,
        request_id: str,
        *,
        expected_revision: int,
        watch_id: str,
        profile_revision: str,
        compiled_intent: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        cursor = self._execute(
            """
            update watch_requests set
              status = 'active', watch_id = ?, profile_revision = ?,
              compiled_intent_json = ?, last_error = null,
              updated_at = ?, processed_at = ?
            where id = ? and revision = ? and status = 'processing'
            """,
            (
                watch_id,
                profile_revision,
                dumps_json(compiled_intent),
                now,
                now,
                request_id,
                expected_revision,
            ),
        )
        if int(cursor.rowcount or 0) != 1:
            raise RuntimeError(f"watch request changed while processing: {request_id}")
        return self.get_watch_request(request_id)

    def fail_watch_request(
        self,
        request_id: str,
        *,
        expected_revision: int,
        error: str,
        now: str,
    ) -> dict[str, Any]:
        self._execute(
            """
            update watch_requests set
              status = 'failed', last_error = ?, updated_at = ?
            where id = ? and revision = ? and status = 'processing'
            """,
            (error, now, request_id, expected_revision),
        )
        return self.get_watch_request(request_id)

    def get_watch_request(self, request_id: str) -> dict[str, Any]:
        row = self._execute(
            "select * from watch_requests where id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"watch request not found: {request_id}")
        return self._watch_request_from_row(row)

    def insert_candidate(self, candidate: dict[str, Any]) -> bool:
        cursor = self._execute(
            """
            insert into candidates (
              candidate_id, watch_id, profile_revision, event_id, notice_id,
              seen_at, source_id, same_notice_group_id, status, score, action,
              match_json, event_json, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do nothing
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
            rows = self._execute(
                "select * from candidates where status = ? order by created_at, candidate_id",
                (status,),
            ).fetchall()
        else:
            rows = self._execute(
                "select * from candidates order by created_at, candidate_id"
            ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self._execute(
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
        self._execute(
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
        cursor = self._execute(
            """
            insert into notification_receipts (
              receipt_id, candidate_id, watch_id, event_id, channel,
              payload_hash, status, created_at, sent_at, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(receipt_id) do nothing
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

    def enqueue_notification(self, notification: dict[str, Any]) -> bool:
        cursor = self._execute(
            """
            insert into notification_outbox (
              outbox_id, candidate_id, watch_id, event_id, decision_hash,
              channel, recipient, payload_json, status, attempts,
              next_attempt_at, last_error, created_at, updated_at, sent_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(outbox_id) do nothing
            """,
            (
                notification["outbox_id"],
                notification["candidate_id"],
                notification["watch_id"],
                notification["event_id"],
                notification["decision_hash"],
                notification.get("channel") or "email",
                notification["recipient"],
                dumps_json(notification["payload"]),
                notification.get("status") or "pending",
                int(notification.get("attempts") or 0),
                notification.get("next_attempt_at"),
                notification.get("last_error"),
                notification["created_at"],
                notification["updated_at"],
                notification.get("sent_at"),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    def list_due_notifications(self, *, now: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            select * from notification_outbox
            where status in ('pending', 'retry')
              and (next_attempt_at is null or next_attempt_at <= ?)
            order by created_at, outbox_id
            limit ?
            """,
            (now, limit),
        ).fetchall()
        return [self._outbox_from_row(row) for row in rows]

    def mark_notification_sent(self, outbox_id: str, *, now: str) -> dict[str, Any]:
        self._execute(
            """
            update notification_outbox set
              status = 'sent', attempts = attempts + 1, last_error = null,
              next_attempt_at = null, updated_at = ?, sent_at = ?
            where outbox_id = ?
            """,
            (now, now, outbox_id),
        )
        return self.get_notification(outbox_id)

    def mark_notification_failed(
        self,
        outbox_id: str,
        *,
        now: str,
        error: str,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        current = self.get_notification(outbox_id)
        attempts = int(current["attempts"]) + 1
        status = "needs_attention" if attempts >= max_attempts else "retry"
        next_attempt_at = None
        if status == "retry":
            delay_minutes = min(60, 2 ** max(0, attempts - 1))
            next_attempt_at = (
                datetime.fromisoformat(now) + timedelta(minutes=delay_minutes)
            ).isoformat()
        self._execute(
            """
            update notification_outbox set
              status = ?, attempts = ?, next_attempt_at = ?, last_error = ?,
              updated_at = ?
            where outbox_id = ?
            """,
            (status, attempts, next_attempt_at, error, now, outbox_id),
        )
        return self.get_notification(outbox_id)

    def get_notification(self, outbox_id: str) -> dict[str, Any]:
        row = self._execute(
            "select * from notification_outbox where outbox_id = ?",
            (outbox_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"notification not found: {outbox_id}")
        return self._outbox_from_row(row)

    def upsert_user_notification(self, notification: dict[str, Any]) -> dict[str, Any]:
        self._execute(
            """
            insert into user_notifications (
              id, outbox_id, user_id, watch_request_id, watch_id, candidate_id,
              event_id, notice_id, classification, delivery_status, title,
              summary, notice_url, facts_json, evidence_json, last_error,
              read_at, created_at, updated_at, sent_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              outbox_id = excluded.outbox_id,
              classification = excluded.classification,
              delivery_status = excluded.delivery_status,
              title = excluded.title,
              summary = excluded.summary,
              notice_url = excluded.notice_url,
              facts_json = excluded.facts_json,
              evidence_json = excluded.evidence_json,
              last_error = excluded.last_error,
              updated_at = excluded.updated_at,
              sent_at = excluded.sent_at
            """,
            (
                notification["id"],
                notification.get("outbox_id"),
                notification["user_id"],
                notification["watch_request_id"],
                notification["watch_id"],
                notification["candidate_id"],
                notification["event_id"],
                notification.get("notice_id"),
                notification["classification"],
                notification["delivery_status"],
                notification["title"],
                notification["summary"],
                notification.get("notice_url"),
                dumps_json(notification.get("facts") or []),
                dumps_json(notification.get("evidence") or []),
                notification.get("last_error"),
                notification.get("read_at"),
                notification["created_at"],
                notification["updated_at"],
                notification.get("sent_at"),
            ),
        )
        return self.get_user_notification(notification["id"])

    def update_user_notification_delivery(
        self,
        outbox_id: str,
        *,
        status: str,
        now: str,
        error: str | None = None,
        sent_at: str | None = None,
    ) -> None:
        self._execute(
            """
            update user_notifications set
              delivery_status = ?, last_error = ?, updated_at = ?,
              sent_at = coalesce(?, sent_at)
            where outbox_id = ?
            """,
            (status, error, now, sent_at, outbox_id),
        )

    def get_user_notification(self, notification_id: str) -> dict[str, Any]:
        row = self._execute(
            "select * from user_notifications where id = ?",
            (notification_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"user notification not found: {notification_id}")
        return self._user_notification_from_row(row)

    def list_user_notifications(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id:
            rows = self._execute(
                "select * from user_notifications where user_id = ? order by created_at desc",
                (user_id,),
            ).fetchall()
        else:
            rows = self._execute(
                "select * from user_notifications order by created_at desc"
            ).fetchall()
        return [self._user_notification_from_row(row) for row in rows]

    def sync_operator_incidents(
        self,
        issues: list[dict[str, str]],
        *,
        now: str,
    ) -> list[dict[str, Any]]:
        active_fingerprints = {issue["fingerprint"] for issue in issues}
        open_rows = self._execute(
            "select fingerprint from operator_incidents where status = 'open'"
        ).fetchall()
        for row in open_rows:
            fingerprint = str(row["fingerprint"])
            if fingerprint not in active_fingerprints:
                self._execute(
                    """
                    update operator_incidents
                    set status = 'resolved', resolved_at = ?, last_seen_at = ?
                    where fingerprint = ?
                    """,
                    (now, now, fingerprint),
                )

        for issue in issues:
            existing = self._execute(
                "select * from operator_incidents where fingerprint = ?",
                (issue["fingerprint"],),
            ).fetchone()
            if existing is None:
                incident_id = "incident_" + uuid4().hex[:24]
                self._execute(
                    """
                    insert into operator_incidents (
                      id, fingerprint, component, severity, status, message,
                      first_seen_at, last_seen_at, notified_at, resolved_at
                    ) values (?, ?, ?, ?, 'open', ?, ?, ?, null, null)
                    """,
                    (
                        incident_id,
                        issue["fingerprint"],
                        issue["component"],
                        issue["severity"],
                        issue["message"],
                        now,
                        now,
                    ),
                )
                continue
            reopening = str(existing["status"]) == "resolved"
            self._execute(
                """
                update operator_incidents set
                  component = ?, severity = ?, status = 'open', message = ?,
                  first_seen_at = ?, last_seen_at = ?, notified_at = ?, resolved_at = null
                where fingerprint = ?
                """,
                (
                    issue["component"],
                    issue["severity"],
                    issue["message"],
                    now if reopening else existing["first_seen_at"],
                    now,
                    None if reopening else existing["notified_at"],
                    issue["fingerprint"],
                ),
            )

        rows = self._execute(
            """
            select * from operator_incidents
            where status = 'open' and notified_at is null
            order by severity desc, first_seen_at
            """
        ).fetchall()
        return [self._incident_from_row(row) for row in rows]

    def mark_incidents_notified(self, incident_ids: list[str], *, now: str) -> None:
        for incident_id in incident_ids:
            self._execute(
                "update operator_incidents set notified_at = ? where id = ?",
                (now, incident_id),
            )

    def upsert_service_health(self, health: dict[str, Any]) -> None:
        self._execute(
            """
            insert into service_health (
              id, status, checked_at, feed_generated_at, latest_cycle_at,
              open_incident_count, summary, details_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              status = excluded.status,
              checked_at = excluded.checked_at,
              feed_generated_at = excluded.feed_generated_at,
              latest_cycle_at = excluded.latest_cycle_at,
              open_incident_count = excluded.open_incident_count,
              summary = excluded.summary,
              details_json = excluded.details_json
            """,
            (
                health.get("id") or "runtime",
                health["status"],
                health["checked_at"],
                health.get("feed_generated_at"),
                health.get("latest_cycle_at"),
                int(health.get("open_incident_count") or 0),
                health["summary"],
                dumps_json(health.get("details") or {}),
            ),
        )

    def start_run(
        self,
        *,
        command: str,
        started_at: str,
        run_id: str | None = None,
    ) -> str:
        resolved_run_id = run_id or (
            "run_"
            + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
            + f"_{uuid4().hex[:12]}"
        )
        self._execute(
            """
            insert into runs (
              run_id, command, started_at, status,
              input_event_count, candidate_count, warnings_json
            ) values (?, ?, ?, 'running', 0, 0, ?)
            """,
            (resolved_run_id, command, started_at, dumps_json([])),
        )
        return resolved_run_id

    def finish_run(
        self,
        run_id: str,
        *,
        finished_at: str,
        status: str,
        input_event_count: int = 0,
        candidate_count: int = 0,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        self._execute(
            """
            update runs set
              finished_at = ?, status = ?, input_event_count = ?,
              candidate_count = ?, warnings_json = ?
            where run_id = ?
            """,
            (
                finished_at,
                status,
                input_event_count,
                candidate_count,
                dumps_json(warnings or []),
                run_id,
            ),
        )
        row = self._execute(
            "select * from runs where run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return self._run_from_row(row)

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            select * from runs
            order by coalesce(finished_at, started_at) desc, run_id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def status_summary(self) -> dict[str, Any]:
        scan = self._scan_state_row()
        profile_rows = self._execute(
            "select enabled, count(*) as count from profiles group by enabled"
        ).fetchall()
        candidate_rows = self._execute(
            "select status, count(*) as count from candidates group by status"
        ).fetchall()
        outbox_rows = self._execute(
            "select status, count(*) as count from notification_outbox group by status"
        ).fetchall()
        run_rows = self._execute(
            "select status, count(*) as count from runs group by status"
        ).fetchall()
        watch_request_rows = self._execute(
            "select status, count(*) as count from watch_requests group by status"
        ).fetchall()
        user_notification_rows = self._execute(
            "select delivery_status, count(*) as count from user_notifications group by delivery_status"
        ).fetchall()
        health_row = self._execute(
            "select * from service_health where id = 'runtime'"
        ).fetchone()
        latest_run = self._execute(
            """
            select * from runs
            order by coalesce(finished_at, started_at) desc, run_id desc
            limit 1
            """
        ).fetchone()
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
            "watch_requests": {
                str(row["status"]): int(row["count"])
                for row in watch_request_rows
            },
            "outbox": {
                str(row["status"]): int(row["count"])
                for row in outbox_rows
            },
            "user_notifications": {
                str(row["delivery_status"]): int(row["count"])
                for row in user_notification_rows
            },
            "service_health": self._service_health_from_row(health_row) if health_row else None,
            "runs": {
                "by_status": {
                    str(row["status"]): int(row["count"])
                    for row in run_rows
                },
                "latest": self._run_from_row(latest_run) if latest_run else None,
            },
        }

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def _scan_state_row(self) -> sqlite3.Row | None:
        return self._execute(
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

    def _watch_request_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "request": row["request"],
            "delivery_email": row["delivery_email"],
            "enabled": bool(row["enabled"]),
            "status": row["status"],
            "revision": int(row["revision"]),
            "watch_id": row["watch_id"],
            "profile_revision": row["profile_revision"],
            "compiled_intent": loads_json(row["compiled_intent_json"], None),
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "processed_at": row["processed_at"],
        }

    def _outbox_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "outbox_id": row["outbox_id"],
            "candidate_id": row["candidate_id"],
            "watch_id": row["watch_id"],
            "event_id": row["event_id"],
            "decision_hash": row["decision_hash"],
            "channel": row["channel"],
            "recipient": row["recipient"],
            "payload": loads_json(row["payload_json"], {}),
            "status": row["status"],
            "attempts": row["attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "sent_at": row["sent_at"],
        }

    def _user_notification_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "outbox_id": row["outbox_id"],
            "user_id": str(row["user_id"]),
            "watch_request_id": str(row["watch_request_id"]),
            "watch_id": row["watch_id"],
            "candidate_id": row["candidate_id"],
            "event_id": row["event_id"],
            "notice_id": row["notice_id"],
            "classification": row["classification"],
            "delivery_status": row["delivery_status"],
            "title": row["title"],
            "summary": row["summary"],
            "notice_url": row["notice_url"],
            "facts": loads_json(row["facts_json"], []),
            "evidence": loads_json(row["evidence_json"], []),
            "last_error": row["last_error"],
            "read_at": row["read_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "sent_at": row["sent_at"],
        }

    def _incident_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "fingerprint": row["fingerprint"],
            "component": row["component"],
            "severity": row["severity"],
            "status": row["status"],
            "message": row["message"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "notified_at": row["notified_at"],
            "resolved_at": row["resolved_at"],
        }

    def _service_health_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "status": row["status"],
            "checked_at": row["checked_at"],
            "feed_generated_at": row["feed_generated_at"],
            "latest_cycle_at": row["latest_cycle_at"],
            "open_incident_count": int(row["open_incident_count"] or 0),
            "summary": row["summary"],
            "details": loads_json(row["details_json"], {}),
        }

    def _run_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "command": row["command"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "input_event_count": int(row["input_event_count"] or 0),
            "candidate_count": int(row["candidate_count"] or 0),
            "warnings": loads_json(row["warnings_json"], []),
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
