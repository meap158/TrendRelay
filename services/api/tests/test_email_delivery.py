from datetime import UTC, datetime

from trendrelay_api.config import Settings
from trendrelay_api.email_delivery import invitation_url, send_invitation_email


def settings(**overrides) -> Settings:
    values = {
        "public_web_url": "https://app.example.test",
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_username": "mailer",
        "smtp_password": "secret",
        "smtp_from_email": "invites@example.test",
        "smtp_security": "starttls",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_invitation_url_encodes_the_one_time_token() -> None:
    url = invitation_url("token with+/symbols", settings())
    assert url == ("https://app.example.test/invitations/accept?token=token+with%2B%2Fsymbols")


def test_unconfigured_delivery_fails_without_opening_smtp(monkeypatch) -> None:
    def unexpected_smtp(*_args, **_kwargs):
        raise AssertionError("SMTP must not be opened when delivery is unconfigured")

    monkeypatch.setattr("trendrelay_api.email_delivery.smtplib.SMTP", unexpected_smtp)
    result = send_invitation_email(
        recipient="editor@example.test",
        workspace_name="Editorial",
        role="editor",
        token="one-time-token",
        expires_at=datetime.now(UTC),
        settings=settings(smtp_host=""),
    )
    assert result.status == "failed"
    assert result.detail == "SMTP delivery is not configured."


def test_smtp_delivery_uses_tls_login_and_plain_text_link(monkeypatch) -> None:
    events: list[object] = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            events.append("ehlo")

        def starttls(self, *, context):
            events.append(("starttls", context is not None))

        def login(self, username, password):
            events.append(("login", username, password))

        def send_message(self, message):
            events.append(("message", message))

    monkeypatch.setattr("trendrelay_api.email_delivery.smtplib.SMTP", FakeSmtp)
    result = send_invitation_email(
        recipient="editor@example.test",
        workspace_name="Editorial",
        role="editor",
        token="one-time-token",
        expires_at=datetime(2026, 7, 25, tzinfo=UTC),
        settings=settings(),
    )

    assert result.status == "sent"
    assert ("connect", "smtp.example.test", 587, 10) in events
    assert ("starttls", True) in events
    assert ("login", "mailer", "secret") in events
    message = next(
        event[1] for event in events if isinstance(event, tuple) and event[0] == "message"
    )
    assert message["To"] == "editor@example.test"
    assert (
        "https://app.example.test/invitations/accept?token=one-time-token" in message.get_content()
    )


def test_delivery_rejects_non_https_public_links_before_smtp(monkeypatch) -> None:
    def unexpected_smtp(*_args, **_kwargs):
        raise AssertionError("SMTP must not open for an unsafe invitation URL")

    monkeypatch.setattr("trendrelay_api.email_delivery.smtplib.SMTP", unexpected_smtp)
    result = send_invitation_email(
        recipient="editor@example.test",
        workspace_name="Editorial",
        role="editor",
        token="one-time-token",
        expires_at=datetime.now(UTC),
        settings=settings(public_web_url="http://app.example.test"),
    )
    assert result.status == "failed"
    assert result.detail == "SMTP delivery failed: ValueError"


def test_implicit_tls_uses_smtp_ssl_without_starttls(monkeypatch) -> None:
    events: list[str] = []

    class FakeSmtpSsl:
        def __init__(self, host, port, timeout, context):
            assert (host, port, timeout) == ("smtp.example.test", 465, 10)
            assert context is not None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            events.append("ehlo")

        def starttls(self, **_kwargs):
            raise AssertionError("Implicit TLS must not issue STARTTLS")

        def login(self, _username, _password):
            events.append("login")

        def send_message(self, _message):
            events.append("send")

    monkeypatch.setattr("trendrelay_api.email_delivery.smtplib.SMTP_SSL", FakeSmtpSsl)
    result = send_invitation_email(
        recipient="editor@example.test",
        workspace_name="Editorial",
        role="editor",
        token="one-time-token",
        expires_at=datetime.now(UTC),
        settings=settings(smtp_security="ssl", smtp_port=465),
    )
    assert result.status == "sent"
    assert events == ["ehlo", "login", "send"]
