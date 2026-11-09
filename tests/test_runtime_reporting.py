import json
from pathlib import Path

import pytest

from pnu_event_gate.cli import main
from pnu_event_gate.runtime_reporting import cycle_summary
from scripts.record_runtime_health import record
from scripts import run_hosted


def test_hosted_summary_excludes_message_and_private_error_contents():
    summary = cycle_summary({
        "processed": [{"status": "failed_retryable", "error": "private provider response"}],
        "deliveries": [{"status": "sent", "recipient": "private@example.test", "payload": "private message"}],
        "status": {"profiles": {"request": "private watch request"}},
    })
    assert summary["error_count"] == 1
    assert summary["status"] == "degraded"
    assert summary["delivery_status_counts"] == {"sent": 1}
    assert "private" not in json.dumps(summary)


def test_unhealthy_monitor_fails_and_writes_allowlisted_report(tmp_path, capsys):
    report = tmp_path / "health.json"
    assert main(["monitor-service", "--db", str(tmp_path / "state.sqlite3"),
                 "--no-send", "--summary-only", "--check", "--health-report", str(report)]) == 1
    payload = json.loads(report.read_text())
    assert payload["status"] == "unhealthy"
    assert {item["fingerprint"] for item in payload["issues"]} >= {"feed-scan-stale", "watch-cycle-stale"}
    assert "details" not in payload
    assert json.loads(capsys.readouterr().out)["status"] == "unhealthy"


def test_hosted_failure_does_not_log_raw_exception(tmp_path, monkeypatch, capsys):
    def fail(_args):
        raise RuntimeError("private connection or provider response")
    monkeypatch.setattr(run_hosted, "main", fail)
    report = tmp_path / "health.json"
    assert run_hosted.run(["monitor-service", "--health-report", str(report)]) == 1
    assert "private" not in capsys.readouterr().err.replace("private service state", "")
    assert json.loads(report.read_text())["status"] == "unhealthy"


def test_daily_record_keeps_real_observation_and_records_incident_changes(tmp_path):
    source, target = tmp_path / "incoming.json", tmp_path / "record.json"
    observation = {"checked_at": "2026-11-09T01:00:00+00:00", "status": "healthy", "issues": [], "private_field": "excluded"}
    source.write_text(json.dumps(observation))
    assert record(source, target)
    first = target.read_bytes()
    assert "private_field" not in target.read_text()
    observation["checked_at"] = "2026-11-09T02:00:00+00:00"
    source.write_text(json.dumps(observation))
    assert not record(source, target)
    assert target.read_bytes() == first
    observation["status"] = "unhealthy"
    source.write_text(json.dumps(observation))
    assert record(source, target)
    observation["checked_at"] = "2026-11-10T02:00:00+00:00"
    source.write_text(json.dumps(observation))
    assert record(source, target)


def test_incomplete_health_observation_is_not_recorded(tmp_path):
    source = tmp_path / "incoming.json"
    source.write_text('{}')
    with pytest.raises(ValueError):
        record(source, tmp_path / "record.json")
