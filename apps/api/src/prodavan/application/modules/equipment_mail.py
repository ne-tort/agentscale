"""WAVE16: почта модуля «Подбор техники» — отправка писем через профили SMTP.

mail_senders (профили: SMTP host/port/user/password-ref/TLS/default),
mail_contacts (адресная книга), mail_outbox (лог отправки: queued/sent/failed).

Отправка синхронная (SMTP по своей природе быстрый); статус и ошибка пишутся
в mail_outbox. Секрет пароля — secret_ref (secret store), в body только ссылка.

Два режима шифрования: implicit TLS (``smtp_ssl``, порт 465 — так работает
Яндекс.Почта) и STARTTLS (``smtp_tls``, порт 587). ``smtp_ssl`` главнее:
если он включён, STARTTLS не выполняется.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from prodavan.domain.errors import AppError

# Яндекс.Почта и большинство провайдеров режут письмо около 35 МБ; base64
# раздувает вложение примерно на треть, поэтому держим запас.
DEFAULT_MAX_ATTACHMENT_MB = 25


class MailSendError(Exception):
    pass


def resolve_secret(body_value: Any) -> str:
    """secret_ref → plaintext из secret store.

    Пусто — только когда ссылки нет вовсе (безавторизационный релей). Если
    ссылка есть, но секрет не читается — это ошибка конфигурации, и молча
    отправлять без пароля нельзя: SMTP ответит 535, а настоящая причина
    (недоступное хранилище секретов) потеряется.
    """
    if not isinstance(body_value, dict):
        return ""
    ref = str(body_value.get("secret_ref") or "").strip()
    if not ref:
        return ""
    try:
        from prodavan.infrastructure.secrets.store import get_secret_store

        secret = get_secret_store().get(ref)
    except Exception as exc:  # noqa: BLE001 — клиенту нужен понятный текст
        raise MailSendError(f"smtp password secret unavailable: {exc}") from exc
    if not secret.strip():
        raise MailSendError("smtp password secret is empty")
    return secret.strip()


def build_message(
    *,
    from_address: str,
    from_name: str = "",
    to_address: str,
    subject: str,
    body_text: str,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> EmailMessage:
    """Письмо с опциональными вложениями [(filename, mime, bytes)]."""
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_address)) if from_name else from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body_text)
    for filename, mime, data in attachments or []:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )
    return msg


def send_smtp(
    *,
    host: str,
    port: int = 587,
    user: str = "",
    password: str = "",
    starttls: bool = True,
    use_ssl: bool = False,
    msg: EmailMessage,
    timeout: float = 120.0,
) -> None:
    """Отправить письмо; MailSendError при сбое.

    ``use_ssl`` — implicit TLS (SMTPS, обычно 465). Иначе при ``starttls``
    выполняется апгрейд открытого соединения (587).
    """
    if not host.strip():
        raise MailSendError("smtp host is empty")
    port = int(port or (465 if use_ssl else 587))
    context = ssl.create_default_context()
    try:
        if use_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host.strip(), port, timeout=timeout, context=context
            )
        else:
            smtp = smtplib.SMTP(host.strip(), port, timeout=timeout)
        with smtp:
            smtp.ehlo()
            if not use_ssl and starttls:
                smtp.starttls(context=context)
                smtp.ehlo()
            if user.strip():
                smtp.login(user.strip(), password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailSendError(f"{type(exc).__name__}: {exc}") from exc


def resolve_sender(
    rows: list[dict[str, Any]],
    *,
    sender_id: str = "",
    sender_name: str = "",
) -> dict[str, Any]:
    """Профиль отправки: по row_id, затем по имени, затем ``is_default``.

    ``rows`` — строки mail_senders ({row_id, body}). Выключенные профили не
    участвуют: письмо из выключенного ящика — почти всегда ошибка настройки.
    """
    candidates: list[dict[str, Any]] = []
    for row in rows:
        body = row.get("body") if isinstance(row.get("body"), dict) else None
        if not isinstance(body, dict):
            continue
        if body.get("enabled") is False:
            continue
        candidates.append({**body, "_row_id": str(row.get("row_id") or "")})
    if not candidates:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="нет доступных профилей отправки (mail_senders)",
        )
    if sender_id:
        for body in candidates:
            if body["_row_id"] == sender_id:
                return body
        raise AppError(
            code="NOT_FOUND",
            title="Not Found",
            status=404,
            detail=f"профиль отправки {sender_id} не найден или выключен",
        )
    if sender_name:
        wanted = sender_name.strip().lower()
        for body in candidates:
            if str(body.get("name") or "").strip().lower() == wanted:
                return body
        raise AppError(
            code="NOT_FOUND",
            title="Not Found",
            status=404,
            detail=f"профиль отправки «{sender_name}» не найден или выключен",
        )
    for body in candidates:
        if body.get("is_default") is True:
            return body
    return candidates[0]


def resolve_recipient(
    rows: list[dict[str, Any]],
    *,
    contact_id: str = "",
    address: str = "",
) -> str:
    """Адрес получателя: профиль контакта по row_id, либо явный адрес."""
    contact_id = (contact_id or "").strip()
    if contact_id:
        for row in rows:
            if str(row.get("row_id") or "") != contact_id:
                continue
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            email = str(body.get("email") or "").strip()
            if email:
                return email
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="у выбранного контакта не заполнен email",
            )
        raise AppError(
            code="NOT_FOUND",
            title="Not Found",
            status=404,
            detail=f"контакт {contact_id} не найден",
        )
    address = (address or "").strip()
    if not address:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="не выбран получатель",
        )
    return address


def send_via_profile(
    profile: dict[str, Any],
    *,
    to_address: str,
    subject: str,
    body_text: str,
    attachments: list[tuple[str, str, bytes]] | None = None,
    timeout: float = 120.0,
) -> None:
    """Собрать и отправить письмо по уже разрешённому профилю отправки."""
    from_address = str(profile.get("from_address") or "").strip()
    if not from_address:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="у профиля отправки не задан адрес отправителя",
        )
    if not to_address.strip():
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="не выбран получатель",
        )
    msg = build_message(
        from_address=from_address,
        from_name=str(profile.get("from_name") or ""),
        to_address=to_address.strip(),
        subject=subject,
        body_text=body_text,
        attachments=attachments,
    )
    send_smtp(
        host=str(profile.get("smtp_host") or ""),
        port=int(profile.get("smtp_port") or 587),
        user=str(profile.get("smtp_user") or ""),
        password=resolve_secret(profile.get("smtp_password")),
        starttls=bool(profile.get("smtp_tls", True)),
        use_ssl=bool(profile.get("smtp_ssl", False)),
        msg=msg,
        timeout=timeout,
    )


async def list_sender_rows(
    session: Any,
    *,
    cabinet_id: str,
    principal: Any = None,
    employee: Any = None,
) -> list[dict[str, Any]]:
    """Строки mail_senders кабинетного контура (фолбэк, когда вызывающий
    не передал их сам)."""
    from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService

    return await CabinetModuleService(session).list_data_rows(
        cabinet_id=cabinet_id,
        module_id="mod_equipment",
        table_slug="mail_senders",
        principal=principal,
        employee=employee,
    )


async def send_mail_for_row(
    session: Any,
    *,
    cabinet_id: str,
    row_body: dict[str, Any],
    principal: Any = None,
    employee: Any = None,
    sender_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Отправить письмо по строке mail_outbox (queued → sent/failed).

    ``sender_rows`` передаёт вызывающий, когда уже прочитал mail_senders в
    своём контуре (проектный лист vs кабинет) — иначе список берётся из
    кабинетного инстанса.
    """
    rows = (
        sender_rows
        if sender_rows is not None
        else await list_sender_rows(
            session, cabinet_id=cabinet_id, principal=principal, employee=employee
        )
    )
    profile = resolve_sender(rows, sender_id=str(row_body.get("sender_id") or ""))
    send_via_profile(
        profile,
        to_address=str(row_body.get("to_address") or ""),
        subject=str(row_body.get("subject") or ""),
        body_text=str(row_body.get("body_text") or ""),
    )
    return {"ok": True}
