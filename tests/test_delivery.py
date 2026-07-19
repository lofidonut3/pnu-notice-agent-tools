from __future__ import annotations

from pnu_event_gate.delivery import SMTPConfig, send_email


def test_smtp_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TEST_HOST", "smtp.example.test")
    monkeypatch.setenv("TEST_FROM", "notices@example.test")
    monkeypatch.setenv("TEST_PORT", "2525")
    monkeypatch.setenv("TEST_STARTTLS", "false")

    config = SMTPConfig.from_env("TEST_")

    assert config.host == "smtp.example.test"
    assert config.port == 2525
    assert config.starttls is False


def test_send_email_uses_plain_text_smtp(monkeypatch) -> None:
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(
                (
                    "send",
                    message["To"],
                    message["Subject"],
                    message.get_content(),
                    message["Message-ID"],
                )
            )
            return {}

    monkeypatch.setattr("pnu_event_gate.delivery.smtplib.SMTP", FakeSMTP)
    receipt = send_email(
        config=SMTPConfig(
            host="smtp.example.test",
            port=587,
            username="user",
            password="secret",
            sender="notices@example.test",
        ),
        recipient="student@example.test",
        content={
            "subject": "공지",
            "body_text": "근거 본문",
            "message_id": "<notif-1@pnu-notice-agent.local>",
        },
    )

    assert receipt["status"] == "sent"
    assert any(call[0] == "starttls" for call in calls)
    assert any(call[0] == "login" for call in calls)
    assert any(call[0] == "send" and call[1] == "student@example.test" for call in calls)
    assert any(call[0] == "send" and call[4] == "<notif-1@pnu-notice-agent.local>" for call in calls)
