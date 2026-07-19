from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    sender: str
    starttls: bool = True

    @classmethod
    def from_env(cls, prefix: str = "PNU_SMTP_") -> SMTPConfig:
        host = os.environ.get(f"{prefix}HOST", "").strip()
        sender = os.environ.get(f"{prefix}FROM", "").strip()
        if not host:
            raise ValueError(f"{prefix}HOST is not set")
        if not sender:
            raise ValueError(f"{prefix}FROM is not set")
        try:
            port = int(os.environ.get(f"{prefix}PORT", "587"))
        except ValueError as error:
            raise ValueError(f"{prefix}PORT must be an integer") from error
        username = os.environ.get(f"{prefix}USERNAME") or None
        password = os.environ.get(f"{prefix}PASSWORD") or None
        if bool(username) != bool(password):
            raise ValueError(f"{prefix}USERNAME and {prefix}PASSWORD must be set together")
        starttls = os.environ.get(f"{prefix}STARTTLS", "true").casefold() not in {
            "0",
            "false",
            "no",
        }
        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=sender,
            starttls=starttls,
        )


def send_email(
    *,
    config: SMTPConfig,
    recipient: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = recipient
    message["Subject"] = str(content["subject"])
    if content.get("message_id"):
        message["Message-ID"] = str(content["message_id"])
    message.set_content(str(content["body_text"]))

    with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
        smtp.ehlo()
        if config.starttls:
            smtp.starttls()
            smtp.ehlo()
        if config.username and config.password:
            smtp.login(config.username, config.password)
        refused = smtp.send_message(message)
    if refused:
        raise RuntimeError(f"SMTP refused recipients: {sorted(refused)}")
    return {
        "status": "sent",
        "channel": "email",
        "recipient": recipient,
        "subject": str(content["subject"]),
    }
