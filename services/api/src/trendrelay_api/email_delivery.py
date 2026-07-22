"""Minimal SMTP boundary for one-time workspace invitation delivery."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlencode, urlsplit

from trendrelay_api.config import Settings, get_settings


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal["sent", "failed"]
    detail: str | None = None


def invitation_url(token: str, settings: Settings) -> str:
    base = settings.public_web_url.rstrip("/")
    parsed = urlsplit(base)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        raise ValueError("PUBLIC_WEB_URL must be HTTPS or a loopback HTTP URL.")
    return f"{base}/invitations/accept?{urlencode({'token': token})}"


def send_invitation_email(
    *,
    recipient: str,
    workspace_name: str,
    role: str,
    token: str,
    expires_at: datetime,
    settings: Settings | None = None,
) -> DeliveryResult:
    config = settings or get_settings()
    if not config.smtp_host or not config.smtp_from_email:
        return DeliveryResult("failed", "SMTP delivery is not configured.")

    password = config.smtp_password.get_secret_value()
    if config.smtp_username and not password:
        return DeliveryResult("failed", "SMTP password is not configured.")

    try:
        message = EmailMessage()
        message["From"] = config.smtp_from_email
        message["To"] = recipient
        message["Subject"] = "You're invited to TrendRelay"
        message.set_content(
            "\n".join(
                [
                    f'You have been invited to the TrendRelay workspace "{workspace_name}".',
                    f"Role: {role}",
                    f"Expires: {expires_at.isoformat()}",
                    "",
                    invitation_url(token, config),
                    "",
                    "If you did not expect this invitation, ignore this message.",
                ]
            )
        )

        context = ssl.create_default_context()
        if config.smtp_security == "ssl":
            client_context = smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=10, context=context
            )
        else:
            client_context = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10)
        with client_context as client:
            client.ehlo()
            if config.smtp_security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if config.smtp_username:
                client.login(config.smtp_username, password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as error:
        return DeliveryResult("failed", f"SMTP delivery failed: {type(error).__name__}")
    return DeliveryResult("sent")
