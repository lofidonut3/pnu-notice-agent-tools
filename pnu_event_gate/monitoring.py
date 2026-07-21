from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .events import now_iso
from .store import NoticeStore


OperatorSender = Callable[[str, dict[str, str]], dict[str, Any]]


def monitor_service(
    *,
    store: NoticeStore,
    operator_email: str | None,
    sender: OperatorSender | None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    now = _parse_time(checked_at or now_iso())
    issues, details = evaluate_health(store, now=now)
    checked_at_value = now.isoformat()
    pending_notifications = store.sync_operator_incidents(issues, now=checked_at_value)
    status = (
        "unhealthy"
        if any(issue["severity"] == "critical" for issue in issues)
        else "degraded" if issues else "healthy"
    )
    summary = (
        "공지 감시 서비스가 정상 작동 중입니다."
        if not issues
        else f"운영 확인이 필요한 항목이 {len(issues)}개 있습니다."
    )
    health = {
        "id": "runtime",
        "status": status,
        "checked_at": checked_at_value,
        "feed_generated_at": details.get("feed_generated_at"),
        "latest_cycle_at": details.get("latest_cycle_at"),
        "open_incident_count": len(issues),
        "summary": summary,
        "details": details,
    }
    store.upsert_service_health(health)
    store.commit()

    notified: list[str] = []
    if pending_notifications and sender and operator_email:
        sender(
            operator_email,
            render_operator_alert(pending_notifications, checked_at=checked_at_value),
        )
        notified = [item["id"] for item in pending_notifications]
        store.mark_incidents_notified(notified, now=now_iso())
        store.commit()

    return {
        "type": "pnu_notice_service_health",
        **health,
        "issues": issues,
        "new_incident_count": len(pending_notifications),
        "notified_incident_ids": notified,
    }


def evaluate_health(
    store: NoticeStore,
    *,
    now: datetime,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    summary = store.status_summary()
    scan = summary.get("scan") or {}
    runs = store.list_runs(limit=50)
    latest_cycle = next((run for run in runs if run["command"] == "run-watch-cycle"), None)
    issues: list[dict[str, str]] = []

    last_checked = _optional_time(scan.get("last_checked_at"))
    feed_generated = _optional_time(scan.get("last_feed_generated_at"))
    latest_cycle_at = _optional_time(
        (latest_cycle or {}).get("finished_at") or (latest_cycle or {}).get("started_at")
    )

    if last_checked is None or now - last_checked > timedelta(hours=2):
        issues.append(
            _issue("feed-scan-stale", "feed", "critical", "공지 feed 확인이 2시간 이상 실행되지 않았습니다.")
        )
    if feed_generated is None or now - feed_generated > timedelta(hours=4):
        issues.append(
            _issue("feed-publication-stale", "feed", "warning", "공지 feed 발행 시각이 4시간 이상 오래되었습니다.")
        )
    if latest_cycle is None or latest_cycle_at is None or now - latest_cycle_at > timedelta(hours=2):
        issues.append(
            _issue("watch-cycle-stale", "worker", "critical", "공지 판정 워커가 2시간 이상 완료되지 않았습니다.")
        )
    elif latest_cycle["status"] == "failed":
        issues.append(
            _issue("watch-cycle-failed", "worker", "critical", "최근 공지 판정 워커가 실패했습니다.")
        )
    elif latest_cycle["status"] == "degraded":
        issues.append(
            _issue("watch-cycle-degraded", "worker", "warning", "최근 공지 판정 워커에 일부 오류가 있습니다.")
        )

    needs_attention = int((summary.get("outbox") or {}).get("needs_attention") or 0)
    if needs_attention:
        issues.append(
            _issue(
                "email-needs-attention",
                "delivery",
                "critical",
                f"재시도 한도를 초과한 이메일이 {needs_attention}개 있습니다.",
            )
        )
    failed_requests = int((summary.get("watch_requests") or {}).get("failed") or 0)
    if failed_requests:
        issues.append(
            _issue(
                "watch-request-failed",
                "registration",
                "warning",
                f"구조화에 실패한 감시 요청이 {failed_requests}개 있습니다.",
            )
        )

    details = {
        "feed_generated_at": feed_generated.isoformat() if feed_generated else None,
        "last_scan_at": last_checked.isoformat() if last_checked else None,
        "latest_cycle_at": latest_cycle_at.isoformat() if latest_cycle_at else None,
        "latest_cycle_status": latest_cycle.get("status") if latest_cycle else None,
        "outbox_needs_attention": needs_attention,
        "failed_watch_requests": failed_requests,
    }
    return issues, details


def render_operator_alert(
    incidents: list[dict[str, Any]],
    *,
    checked_at: str,
) -> dict[str, str]:
    lines = [
        "PNU Watch 운영 점검에서 새 문제가 감지되었습니다.",
        "",
        f"확인 시각: {checked_at}",
        "",
    ]
    for incident in incidents:
        lines.append(
            f"- [{str(incident['severity']).upper()}] "
            f"{incident['component']}: {incident['message']}"
        )
    lines.extend(["", "문제가 해결되면 다음 점검에서 자동으로 종료 처리됩니다."])
    return {
        "subject": f"[PNU Watch 운영 알림] 새 문제 {len(incidents)}건",
        "body_text": "\n".join(lines),
    }


def _issue(fingerprint: str, component: str, severity: str, message: str) -> dict[str, str]:
    return {
        "fingerprint": fingerprint,
        "component": component,
        "severity": severity,
        "message": message,
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_time(str(value))
    except ValueError:
        return None
