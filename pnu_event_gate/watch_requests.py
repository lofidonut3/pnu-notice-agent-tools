from __future__ import annotations

from typing import Any, Callable

from .ai import AIClient
from .analysis import compile_watch_request, intent_to_profile
from .events import now_iso
from .profiles import normalize_profile
from .store import NoticeStore


ClientFactory = Callable[[], AIClient]


def process_watch_requests(
    *,
    store: NoticeStore,
    client_factory: ClientFactory,
    chat_model: str,
    limit: int = 20,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("watch request limit must be greater than zero")

    started_at = now_iso()
    run_id = store.start_run(command="process-watch-requests", started_at=started_at)
    store.commit()
    try:
        payload = _process_watch_requests(
            store=store,
            client_factory=client_factory,
            chat_model=chat_model,
            limit=limit,
        )
        warnings = [
            str(item["error"])
            for item in payload["processed"]
            if item.get("error")
        ]
        store.finish_run(
            run_id,
            finished_at=now_iso(),
            status="degraded" if warnings else "ok",
            candidate_count=payload["processed_count"],
            warnings=warnings,
        )
        store.commit()
        payload["status"] = store.status_summary()
        return payload
    except Exception as error:
        store.rollback()
        store.finish_run(
            run_id,
            finished_at=now_iso(),
            status="failed",
            warnings=[f"{type(error).__name__}: {error}"],
        )
        store.commit()
        raise


def _process_watch_requests(
    *,
    store: NoticeStore,
    client_factory: ClientFactory,
    chat_model: str,
    limit: int,
) -> dict[str, Any]:

    synced = sync_watch_request_profiles(store)
    pending = store.list_watch_requests(status="pending", limit=limit)
    client: AIClient | None = None
    processed: list[dict[str, Any]] = []
    for request_row in pending:
        claimed_at = now_iso()
        if not store.claim_watch_request(request_row["id"], now=claimed_at):
            continue
        store.commit()
        try:
            if client is None:
                client = client_factory()
            intent = compile_watch_request(
                client,
                request=request_row["request"],
                model=chat_model,
            )
            watch_id = request_row["watch_id"] or web_watch_id(request_row["id"])
            profile_revision = str(request_row["revision"])
            profile = normalize_profile(
                intent_to_profile(
                    intent,
                    watch_id=watch_id,
                    revision=profile_revision,
                )
            )
            profile["enabled"] = bool(request_row["enabled"])
            profile["delivery"] = {"email_to": request_row["delivery_email"]}
            profile["owner"] = {
                "type": "web_request",
                "request_id": request_row["id"],
                "user_id": request_row["user_id"],
            }
            completed_at = now_iso()
            completed = store.complete_watch_request(
                request_row["id"],
                expected_revision=request_row["revision"],
                watch_id=watch_id,
                profile_revision=profile_revision,
                compiled_intent=intent,
                now=completed_at,
            )
            store.upsert_profile(profile, now=completed_at)
            store.commit()
            processed.append(
                {
                    "request_id": request_row["id"],
                    "watch_id": watch_id,
                    "revision": profile_revision,
                    "status": completed["status"],
                }
            )
        except Exception as error:  # noqa: BLE001 - retain a user-visible failure state.
            store.rollback()
            failed = store.fail_watch_request(
                request_row["id"],
                expected_revision=request_row["revision"],
                error=f"{type(error).__name__}: {error}",
                now=now_iso(),
            )
            store.commit()
            processed.append(
                {
                    "request_id": request_row["id"],
                    "watch_id": request_row.get("watch_id"),
                    "revision": str(request_row["revision"]),
                    "status": failed["status"],
                    "error": failed["last_error"],
                }
            )

    return {
        "type": "pnu_notice_watch_request_processing",
        "completed_at": now_iso(),
        "pending_count": len(pending),
        "processed_count": len(processed),
        "synced_count": synced,
        "processed": processed,
    }


def sync_watch_request_profiles(store: NoticeStore) -> int:
    synced = 0
    for request_row in store.list_watch_requests(limit=10_000):
        watch_id = request_row.get("watch_id")
        profile_revision = request_row.get("profile_revision")
        if not watch_id or not profile_revision:
            continue
        try:
            stored = store.get_profile(watch_id, profile_revision)
        except KeyError:
            continue
        profile = stored["profile"]
        desired_enabled = bool(request_row["enabled"])
        if stored["enabled"] == desired_enabled:
            continue
        if desired_enabled:
            profile["enabled"] = True
            store.upsert_profile(profile, now=now_iso())
        else:
            store.disable_profile(watch_id, now=now_iso())
        store.commit()
        synced += 1
    return synced


def web_watch_id(request_id: str) -> str:
    return "web-" + request_id.replace("-", "").casefold()
