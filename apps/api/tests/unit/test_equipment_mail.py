"""WAVE16: почта — сборка письма, SMTP-отправка (заглушка), профили."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prodavan.application.modules.equipment_mail import (
    MailSendError,
    build_message,
    resolve_secret,
    send_smtp,
)


def test_build_message() -> None:
    msg = build_message(
        from_address="sales@example.com",
        from_name="Продажи",
        to_address="client@example.com",
        subject="КП по тендеру",
        body_text="Здравствуйте!",
    )
    assert msg["To"] == "client@example.com"
    assert msg["Subject"] == "КП по тендеру"
    assert "sales@example.com" in msg["From"]
    assert "Здравствуйте" in msg.get_content()


def test_send_smtp_requires_host() -> None:
    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    with pytest.raises(MailSendError, match="host"):
        send_smtp(host="", msg=msg)


def test_send_smtp_flow() -> None:
    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    with patch("smtplib.SMTP", return_value=fake):
        send_smtp(host="smtp.example.com", port=587, user="u", password="p", msg=msg)
    fake.starttls.assert_called_once()
    fake.login.assert_called_once_with("u", "p")
    fake.send_message.assert_called_once_with(msg)


def test_send_smtp_no_tls_no_auth() -> None:
    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    with patch("smtplib.SMTP", return_value=fake):
        send_smtp(host="relay.local", port=25, starttls=False, msg=msg)
    fake.starttls.assert_not_called()
    fake.login.assert_not_called()


def test_resolve_secret_empty() -> None:
    assert resolve_secret(None) == ""
    assert resolve_secret({}) == ""
    assert resolve_secret({"secret_ref": ""}) == ""
