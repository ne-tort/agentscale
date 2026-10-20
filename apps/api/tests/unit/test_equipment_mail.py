"""WAVE16: почта — сборка письма, SMTP-отправка (заглушка), профили."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prodavan.application.modules.equipment_mail import (
    MailSendError,
    build_message,
    resolve_recipient,
    resolve_secret,
    resolve_sender,
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


def test_build_message_with_attachment() -> None:
    msg = build_message(
        from_address="price@itvzlet.ru",
        to_address="client@example.com",
        subject="Мастер-прайс",
        body_text="Прайс во вложении",
        attachments=[("master-price.xlsx", "application/vnd.spreadsheet", b"PK\x03\x04")],
    )
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "master-price.xlsx"
    assert parts[0].get_content_type() == "application/vnd.spreadsheet"


def test_send_smtp_ssl_flow() -> None:
    """smtp_ssl=465: SMTP_SSL с первого байта, STARTTLS не выполняется."""
    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    with patch("smtplib.SMTP_SSL", return_value=fake) as ctor,             patch("smtplib.SMTP", return_value=fake):
        send_smtp(host="smtp.yandex.ru", port=465, user="u", password="p", use_ssl=True, msg=msg)
    fake.starttls.assert_not_called()
    fake.login.assert_called_once_with("u", "p")
    fake.send_message.assert_called_once_with(msg)
    # MagicMock мимикрирует под SMTP и обманывает isinstance у «with smtp:»
    # (реальный SMTP_SSL рисует get_conn), поэтому проверяем выбор класса
    # аргументами конструктора, а не типом экземпляра.
    assert ctor.call_args.args[0] == "smtp.yandex.ru"


def test_send_smtp_ssl_uses_context() -> None:
    """TLS-контекст передаётся (сертификат валидируется, а не отключается)."""
    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    fake = MagicMock()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    with patch("smtplib.SMTP_SSL", return_value=fake) as ctor:
        send_smtp(host="smtp.yandex.ru", port=465, use_ssl=True, msg=msg)
    assert ctor.call_args.kwargs.get("context") is not None


def test_send_smtp_error_includes_host_port_and_mode() -> None:
    """Сбой отправки несёт host:port + режим шифрования, а не голой SSLEOFError."""
    import ssl

    msg = build_message(from_address="a@b.c", to_address="d@e.f", subject="s", body_text="b")
    boom = ssl.SSLEOFError("EOF occurred in violation of protocol")
    with patch("smtplib.SMTP_SSL", side_effect=boom):
        with pytest.raises(MailSendError) as ei:
            send_smtp(host="smtp.yandex.ru", port=465, use_ssl=True, msg=msg)
    text = str(ei.value)
    assert "smtp.yandex.ru:465" in text
    assert "implicit TLS" in text
    assert "блокировка" in text  # hint про вероятный фильтр исходящего SMTP


def test_resolve_sender_by_name_price() -> None:
    rows = [
        {"row_id": "s1", "body": {"name": "sales", "from_address": "s@x.y", "enabled": True}},
        {
            "row_id": "s2",
            "body": {
                "name": "price",
                "from_address": "price@itvzlet.ru",
                "enabled": True,
                "smtp_host": "smtp.yandex.ru",
                "smtp_port": 465,
                "smtp_ssl": True,
            },
        },
    ]
    profile = resolve_sender(rows, sender_name="price")
    assert profile["_row_id"] == "s2"
    assert profile["from_address"] == "price@itvzlet.ru"
    assert profile["smtp_ssl"] is True


def test_resolve_sender_skips_disabled_and_missing_raises() -> None:
    rows = [
        {"row_id": "s1", "body": {"name": "price", "enabled": False}},
    ]
    with pytest.raises(Exception, match="профил"):
        resolve_sender(rows, sender_name="price")


def test_resolve_recipient_by_contact() -> None:
    rows = [{"row_id": "c1", "body": {"name": "Клиент", "email": "client@example.com"}}]
    assert resolve_recipient(rows, contact_id="c1") == "client@example.com"


def test_resolve_recipient_requires_selection() -> None:
    from prodavan.domain.errors import AppError

    with pytest.raises(AppError, match="получатель"):
        resolve_recipient([])
