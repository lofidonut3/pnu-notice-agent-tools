from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .ai import AIClient
from .analysis import DEFAULT_MAX_VISUAL_PAGES, run_ai_analysis
from .gemini import DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL
from .content import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_TEXT_CHARS,
    DEFAULT_MAX_TOTAL_BYTES,
    resolve_notice_materials,
)
from .evidence import evidence_from_materials
from .events import now_iso
from .scan import run_scan
from .store import NoticeStore, dumps_json


AnalysisClientFactory = Callable[[], AIClient]
NotificationSender = Callable[[str, dict[str, Any]], dict[str, Any]]


def run_watch_cycle(
    *,
    store: NoticeStore,
    events_url: str,
    cache_dir: Path,
    client_factory: AnalysisClientFactory,
    notification_sender: NotificationSender | None,
    default_email_to: str | None = None,
    include_baseline: bool = False,
    process_limit: int = 20,
    send_limit: int = 20,
    max_attempts: int = 5,
    use_embeddings: bool = True,
    max_visual_pages: int = DEFAULT_MAX_VISUAL_PAGES,
    chat_model: str = DEFAULT_CHAT_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    if process_limit <= 0 or send_limit <= 0:
        raise ValueError("process and send limits must be greater than zero")

    scan_payload = run_scan(
        store=store,
        events_url=events_url,
        include_baseline=include_baseline,
    )
    candidates = _pending_candidates(store)[:process_limit]
    client: AIClient | None = None
    processed: list[dict[str, Any]] = []
    for candidate in candidates:
        if client is None:
            client = client_factory()
        processed.append(
            process_candidate(
                store=store,
                candidate=candidate,
                client=client,
                cache_dir=cache_dir,
                default_email_to=default_email_to,
                max_attempts=max_attempts,
                use_embeddings=use_embeddings,
                max_visual_pages=max_visual_pages,
                chat_model=chat_model,
                embedding_model=embedding_model,
            )
        )

    deliveries = deliver_due_notifications(
        store=store,
        sender=notification_sender,
        limit=send_limit,
        max_attempts=max_attempts,
    )
    return {
        "type": "pnu_notice_watch_cycle",
        "completed_at": now_iso(),
        "scan": scan_payload,
        "candidate_count": len(candidates),
        "processed": processed,
        "deliveries": deliveries,
        "status": store.status_summary(),
    }


def process_candidate(
    *,
    store: NoticeStore,
    candidate: dict[str, Any],
    client: AIClient,
    cache_dir: Path,
    default_email_to: str | None,
    max_attempts: int,
    use_embeddings: bool,
    max_visual_pages: int,
    chat_model: str = DEFAULT_CHAT_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    started_at = now_iso()
    store.update_candidate(candidate["candidate_id"], status="processing", now=started_at)
    store.commit()
    try:
        stored_profile = store.get_profile(
            candidate["watch_id"], candidate.get("profile_revision")
        )
        profile = stored_profile["profile"]
        request = str(profile.get("request") or "").strip()
        intent = profile.get("compiled_intent")
        if not request or not isinstance(intent, dict):
            raise ValueError("candidate watch profile requires request and compiled_intent")

        materials = resolve_notice_materials(
            candidate["event"],
            override_url=None,
            download_attachments=False,
            cache_dir=cache_dir,
            max_text_chars=DEFAULT_MAX_TEXT_CHARS,
            max_file_bytes=DEFAULT_MAX_FILE_BYTES,
            max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
            attachment_policy="relevant",
            watch_request=request,
        )
        store.update_candidate(
            candidate["candidate_id"],
            status="analyzing",
            now=now_iso(),
            materials=materials,
        )
        store.commit()

        evidence = evidence_from_materials(materials)
        analysis = run_ai_analysis(
            client=client,
            request=request,
            evidence=evidence,
            notice=materials.get("notice") or candidate["event"],
            use_embeddings=use_embeddings,
            max_visual_pages=max_visual_pages,
            compiled_intent=intent,
            chat_model=chat_model,
            embedding_model=embedding_model,
        )
        classification = analysis["decision"]["classification"]
        recipient = profile_email(profile) or default_email_to
        queued = False
        if classification == "matched":
            if not recipient:
                store.update_candidate(
                    candidate["candidate_id"],
                    status="needs_attention",
                    now=now_iso(),
                    result=analysis,
                    error="matched decision has no email recipient",
                )
            else:
                notification = build_notification(
                    candidate=candidate,
                    analysis=analysis,
                    recipient=recipient,
                    now=now_iso(),
                )
                queued = store.enqueue_notification(notification)
                store.update_candidate(
                    candidate["candidate_id"],
                    status="completed",
                    now=now_iso(),
                    result=analysis,
                )
        elif classification == "uncertain":
            store.update_candidate(
                candidate["candidate_id"],
                status="needs_attention",
                now=now_iso(),
                result=analysis,
                error="analysis remained uncertain",
            )
        else:
            store.update_candidate(
                candidate["candidate_id"],
                status="completed",
                now=now_iso(),
                result=analysis,
            )
        store.commit()
        return {
            "candidate_id": candidate["candidate_id"],
            "classification": classification,
            "notification_queued": queued,
            "status": store.get_candidate(candidate["candidate_id"])["status"],
        }
    except Exception as error:  # noqa: BLE001 - candidate remains durable for retry/review.
        attempts = int(candidate.get("attempts") or 0) + 1
        status = "needs_attention" if attempts >= max_attempts else "failed_retryable"
        updated = store.update_candidate(
            candidate["candidate_id"],
            status=status,
            now=now_iso(),
            error=f"{type(error).__name__}: {error}",
            increment_attempts=True,
        )
        store.commit()
        return {
            "candidate_id": candidate["candidate_id"],
            "classification": None,
            "notification_queued": False,
            "status": updated["status"],
            "error": updated["last_error"],
        }


def build_notification(
    *,
    candidate: dict[str, Any],
    analysis: dict[str, Any],
    recipient: str,
    now: str,
) -> dict[str, Any]:
    decision_hash = hashlib.sha256(
        dumps_json(
            {
                "decision": analysis["decision"],
                "email": analysis["email"],
            }
        ).encode("utf-8")
    ).hexdigest()
    identity = (
        f"{candidate['watch_id']}:{candidate['event_id']}:"
        f"{decision_hash}:email:{recipient}"
    )
    outbox_id = "notif_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    payload = {
        **analysis["email"],
        "message_id": f"<{outbox_id}@pnu-notice-agent.local>",
    }
    return {
        "outbox_id": outbox_id,
        "candidate_id": candidate["candidate_id"],
        "watch_id": candidate["watch_id"],
        "event_id": candidate["event_id"],
        "decision_hash": decision_hash,
        "channel": "email",
        "recipient": recipient,
        "payload": payload,
        "status": "pending",
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
    }


def deliver_due_notifications(
    *,
    store: NoticeStore,
    sender: NotificationSender | None,
    limit: int,
    max_attempts: int,
) -> list[dict[str, Any]]:
    due = store.list_due_notifications(now=now_iso(), limit=limit)
    if sender is None:
        return [
            {"outbox_id": item["outbox_id"], "status": "queued"}
            for item in due
        ]

    results = []
    for item in due:
        attempted_at = now_iso()
        try:
            metadata = sender(item["recipient"], item["payload"])
            sent = store.mark_notification_sent(item["outbox_id"], now=attempted_at)
            store.record_receipt(
                {
                    "receipt_id": "receipt_" + item["outbox_id"],
                    "candidate_id": item["candidate_id"],
                    "watch_id": item["watch_id"],
                    "event_id": item["event_id"],
                    "channel": item["channel"],
                    "payload_hash": item["decision_hash"],
                    "status": "sent",
                    "created_at": attempted_at,
                    "sent_at": attempted_at,
                    "metadata": metadata,
                }
            )
            store.commit()
            results.append({"outbox_id": item["outbox_id"], "status": sent["status"]})
        except Exception as error:  # noqa: BLE001 - outbox retry is the reliability boundary.
            failed = store.mark_notification_failed(
                item["outbox_id"],
                now=attempted_at,
                error=f"{type(error).__name__}: {error}",
                max_attempts=max_attempts,
            )
            store.commit()
            results.append(
                {
                    "outbox_id": item["outbox_id"],
                    "status": failed["status"],
                    "error": failed["last_error"],
                }
            )
    return results


def profile_email(profile: dict[str, Any]) -> str | None:
    delivery = profile.get("delivery") or {}
    if not isinstance(delivery, dict):
        return None
    value = str(delivery.get("email_to") or "").strip()
    return value or None


def _pending_candidates(store: NoticeStore) -> list[dict[str, Any]]:
    stale_before = datetime.fromisoformat(now_iso()) - timedelta(minutes=30)
    stale = []
    for status in ("processing", "analyzing", "resolving"):
        for candidate in store.list_candidates(status=status):
            try:
                updated_at = datetime.fromisoformat(candidate["updated_at"])
            except (TypeError, ValueError):
                updated_at = stale_before
            if updated_at <= stale_before:
                stale.append(candidate)
    candidates = [
        *store.list_candidates(status="pending"),
        *store.list_candidates(status="failed_retryable"),
        *stale,
    ]
    return sorted(candidates, key=lambda item: (item["created_at"], item["candidate_id"]))
