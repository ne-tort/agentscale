"""WAVE16: почта модуля «Подбор техники» — отправка писем через профили SMTP.

mail_senders (профили: SMTP host/port/user/password-ref/TLS/default),
mail_contacts (адресная книга), mail_outbox (лог отправки: queued/sent/failed).

Отправка синхронная (SMTP по своей природе быстрый); статус и ошибка пишутся
в mail_outbox. Секрет пароля — secret_ref (secret store), в body только ссылка.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from prodavan.domain.errors import AppError


def resolve_secret(body_value: Any) -> str:
    """secret_ref → plaintext из secret store; пусто при отсутствии/сбое."""
    if not isinstance(body_value, dict):
        return ""
    ref = str(body_value.get("secret_ref") or "").strip()
    if not ref:
        return ""
    try:
        from prodavan.infrastructure.secrets.store import get_secret_store

        return get_secret_store().get(ref)
    except Exception:  # noqa: BLE001 — нет секрета = без пароля (безавторизационный релей)
        return ""


class MailSendError(Exception):
    pass


def build_message(
    *,
    from_address: str,
    from_name: str = "",
    to_address: str,
    subject: str,
    body_text: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_address)) if from_name else from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body_text)
    return msg


def send_smtp(
    *,
    host: str,
    port: int = 587,
    user: str = "",
    password: str = "",
    starttls: bool = True,
    msg: EmailMessage,
    timeout: float = 30.0,
) -> None:
    """Отправить письмо; MailSendError при сбое."""
    if not host.strip():
        raise MailSendError("smtp host is empty")
    try:
        with smtplib.SMTP(host.strip(), int(port or 587), timeout=timeout) as smtp:
            if starttls:
                smtp.starttls()
            if user.strip():
                smtp.login(user.strip(), password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailSendError(str(exc)) from exc


async def send_mail_for_row(
    session: Any,
    *,
    cabinet_id: str,
    row_body: dict[str, Any],
    principal: Any = None,
    employee: Any = None,
) -> dict[str, Any]:
    """Отправить письмо по строке mail_outbox (queued → sent/failed).

    Секрет SMTP-пароля резолвится из secret store (platform secret refs не
    читаются напрямую — нужен сервисный контур; пока secret_ref в body ящика
    хранит ссылку, а фактический пароль — в secret store под ней).
    """
    from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService

    svc = CabinetModuleService(session)
    rows = await svc.list_data_rows(
        cabinet_id=cabinet_id,
        module_id="mod_equipment",
        table_slug="mail_senders",
        principal=principal,
        employee=employee,
    )
    sender_id = str(row_body.get("sender_id") or "")
    sender = next((r for r in rows if str(r.get("row_id") or "") == sender_id), None)
    if sender is None:
        raise AppError(
            code="NOT_FOUND", title="Not Found", status=404, detail="mail sender not found"
        )
    sb = sender.get("body") or {}
    if sb.get("enabled") is False:
        raise AppError(
            code="VALIDATION_ERROR", title="Validation Error", status=422,
            detail="mail sender is disabled",
        )
    msg = build_message(
        from_address=str(sb.get("from_address") or ""),
        from_name=str(sb.get("from_name") or ""),
        to_address=str(row_body.get("to_address") or ""),
        subject=str(row_body.get("subject") or ""),
        body_text=str(row_body.get("body_text") or ""),
    )
    password = resolve_secret(sb.get("smtp_password"))
    send_smtp(
        host=str(sb.get("smtp_host") or ""),
        port=int(sb.get("smtp_port") or 587),
        user=str(sb.get("smtp_user") or ""),
        password=password,
        starttls=bool(sb.get("smtp_tls", True)),
        msg=msg,
    )
    return {"ok": True}
