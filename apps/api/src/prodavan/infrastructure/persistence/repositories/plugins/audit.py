"""``AppendOnlyAuditSink`` — append-only аудит поверх ``PluginAuditEventRow``.

Реализует ``AuditPort`` из трёх слоёв (структурно, через duck-typing):

* ``admin.ports.AuditPort``: ``async append(*, event: AdminAuditEvent) -> bool``;
* ``events.ports.AuditPort``: ``async append(event_type, *, details) -> None``;
* ``capabilities.audit.AuditPort``: ``sync append(event: PluginAuditEvent) -> None``.

Защита criterion 11 (§4.5)
--------------------------
В БД уходит ТОЛЬКО безопасная проекция. Мы НЕ доверяем ни одному полю
входного события и НЕ пишем:

* raw SQL, параметры, bodies/payload, tokens, secrets, PII;
* ``details`` произвольной формы — пропускаем через строгий allowlist скаляров
  + отдельную функцию :func:`redact_details`, которая:
  - разрешает ТОЛЬКО примитивы (str/int/float/bool/None) и короткие строки;
  - отбрасывает ключи с маркерами ``sql/params/payload/body/token/...``;
  - длинные/подозрительные строки заменяет на ``sha256:...`` digest;
  - вложенные dict/list НЕ разворачивает (заменяет digest'ом), чтобы не
    протащить сырые данные через ``{"meta": {...}}``.

Поверх этого переиспользуется ``capabilities.audit.redact`` (F2), если событие
— ``PluginAuditEvent``: его ``redacted()`` уже даёт каноническую проекцию.

Fail-closed для позитивной мутации: ЛЮБАЯ ошибка вставки (в т.ч. отсутствие
модели/колонки, ``IntegrityError``, недоступность БД) → ИСКЛЮЧЕНИЕ
``AuditAppendError``, а не ``False``/«молча не записали». Вызывающий
(``PluginAdminService``) превращает это в deny/system failure (§4.5).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ._schema import (
    AUDIT_COLUMNS,
    AUDIT_ROW,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    load_row,
    text,
)

#: Ключи, ЗАПРЕЩЁННЫЕ в ``details`` аудита (criterion 11).
_FORBIDDEN_DETAIL_TOKENS: frozenset[str] = frozenset(
    {
        "sql", "query", "statement", "params", "parameters", "bind", "bindings",
        "payload", "body", "raw", "content", "text", "data", "password",
        "passwd", "pwd", "secret", "token", "authorization", "auth", "cookie",
        "api_key", "credential", "private_key", "signature", "jwt", "bearer",
        "email", "phone", "address", "passport", "inn", "snils", "pii", "ssn",
        "dsn", "connection_string", "base64", "b64", "blob", "file", "bytes",
    }
)

#: Разрешённые канонические поля (идентификаторы, digest-ы, коды, счётчики).
_ALLOWED_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "event_id", "event_type", "event_kind", "request_id", "run_id",
        "correlation_id", "company_id", "cabinet_id", "project_id",
        "subject_id", "plugin_id", "plugin_instance_id", "artifact_digest",
        "grant_id", "capability", "operation", "resource", "decision", "code",
        "reason", "occurred_at", "linked_event_id", "schema_version",
        "redaction_version", "digest", "sql_digest", "payload_digest",
        "body_digest", "result_digest", "http_status", "status_code",
        "allowed", "limit", "used", "remaining", "amount", "attempts",
        "retry_after_seconds", "topic", "dlq_topic", "subscription_id",
        "source_event_id", "error_kind", "changed", "replayed", "ttl_seconds",
    }
)

_MAX_DETAIL_VALUE_LENGTH = 200
"""Длинные свободные строки в аудит не пишем (только digest)."""

_RAW_MARKERS: tuple[str, ...] = (
    "select ", "insert ", "update ", "delete ", "drop ", "create ", "alter ",
    " where ", " from ", "password=", "token=", "secret=", "authorization:", "@",
)


class AuditAppendError(PluginRepositoryError):
    """Запись аудита не подтверждена → deny позитивной мутации (§4.5)."""


def canonical_digest(value: Any) -> str:
    """``sha256:<64 hex>`` вместо значения; исходное NEVER сохраняется."""
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _suspicious(value: str) -> bool:
    lowered = value.lower()
    if any(marker in lowered for marker in _RAW_MARKERS):
        return True
    if lowered.startswith(("bearer ", "basic ", "postgres://", "postgresql://", "redis://")):
        return True
    if lowered.startswith("eyj") and value.count(".") >= 2:  # JWT
        return True
    if lowered.startswith("sha256:") and len(value) == 71:  # это сам digest — ок
        return False
    return len(value) > 96 and " " not in value


def redact_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Строгая проекция ``details``: только плоские безопасные скаляры.

    Возвращает НОВЫЙ dict. Любой запрещённый ключ/вложенная структура
    заменяется на ``<key>_digest`` + ``<key>_redacted=True`` — факт наличия
    сохраняется, значение — НЕТ (как в ``capabilities.audit.redact``).
    """
    if details is None:
        return {}
    if not isinstance(details, Mapping):
        return {"details_digest": canonical_digest(details), "details_redacted": True}
    result: dict[str, Any] = {}
    for raw_key, value in details.items():
        key = str(raw_key)
        lowered = key.lower()
        forbidden = lowered not in _ALLOWED_DETAIL_KEYS and any(
            token in lowered for token in _FORBIDDEN_DETAIL_TOKENS
        )
        if forbidden or isinstance(value, (Mapping, list, tuple, set, frozenset, bytes)):
            result[f"{key}_digest"] = canonical_digest(value)
            result[f"{key}_redacted"] = True
            continue
        if isinstance(value, str):
            if _suspicious(value) or len(value) > _MAX_DETAIL_VALUE_LENGTH:
                result[f"{key}_digest"] = canonical_digest(value)
                result[f"{key}_redacted"] = True
                continue
            result[key] = value
            continue
        if isinstance(value, bool) or value is None:
            result[key] = value
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
            continue
        if isinstance(value, float):
            result[key] = value
            continue
        if isinstance(value, datetime):
            result[key] = value.astimezone(UTC).isoformat()
            continue
        if hasattr(value, "value") and isinstance(getattr(value, "value"), str):  # StrEnum
            result[key] = str(value.value)
            continue
        # Неизвестный объект: тип + digest, без repr/полей.
        result[key] = {"type": type(value).__name__, "digest": canonical_digest(value)}
    return result


def _safe_str(value: Any, *, field: str, default: str = "") -> str:
    """Каноническое строковое поле; подозрительное → digest (не raw)."""
    if value is None:
        return default
    result = str(value.value) if hasattr(value, "value") and isinstance(getattr(value, "value"), str) else str(value)
    if _suspicious(result):
        return canonical_digest(result)
    return result[:_MAX_DETAIL_VALUE_LENGTH]


class AppendOnlyAuditSink:
    """Append-only SQL-sink. Ошибка append = исключение (deny мутации).

    ``session_factory`` обязателен. Ни один метод не обновляет и не удаляет
    строки: только ``INSERT`` (append-only по контракту §4.5).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "AppendOnlyAuditSink требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rows_written = 0

    @property
    def rows_written(self) -> int:
        """Счётчик подтверждённых вставок (диагностика/тесты)."""
        return self._rows_written

    def _row(self) -> type:
        row = load_row(AUDIT_ROW)
        self._columns.require(row, *AUDIT_COLUMNS)
        return row

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise AuditAppendError("часы аудита обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    # ------------------------------------------------------------------
    # Нормализация события → безопасная строка
    # ------------------------------------------------------------------

    def _values(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        company_id: str,
        plugin_id: str,
        details: Mapping[str, Any],
        row: type,
    ) -> dict[str, Any]:
        c = self._col
        safe = redact_details(details)
        values: dict[str, Any] = {
            c("event_id"): text(event_id, field="event_id"),
            c("event_type"): _safe_str(event_type, field="event_type"),
            c("occurred_at"): occurred_at,
            c("company_id"): _safe_str(company_id, field="company_id"),
            c("plugin_id"): _safe_str(plugin_id, field="plugin_id"),
            c("details"): safe,
        }
        # Необязательные канонические колонки заполняем ТОЛЬКО из безопасной
        # проекции — сырое событие никуда не передаётся.
        optional = {
            "grant_id": safe.get("grant_id"),
            "artifact_digest": safe.get("artifact_digest"),
            "operation": safe.get("operation"),
            "decision": safe.get("decision"),
            "code": safe.get("code"),
            "reason": safe.get("reason"),
        }
        existing = set(row.__table__.columns.keys())
        for name, value in optional.items():
            if name in existing and value is not None:
                values[name] = value
        return {key: value for key, value in values.items() if key in existing}

    @staticmethod
    def _admin_projection(event: Any) -> dict[str, Any]:
        """``AdminAuditEvent.redacted()`` — уже безопасная проекция задачи 12."""
        redacted = getattr(event, "redacted", None)
        if callable(redacted):
            raw = redacted()
            if isinstance(raw, Mapping):
                return dict(raw)
        raise AuditAppendError("AdminAuditEvent не предоставляет redacted()")

    @staticmethod
    def _capability_projection(event: Any) -> dict[str, Any]:
        """``PluginAuditEvent.redacted()`` (F2) — каноническая проекция."""
        redacted = getattr(event, "redacted", None)
        if callable(redacted):
            raw = redacted()
            if isinstance(raw, Mapping):
                return dict(raw)
        raise AuditAppendError("PluginAuditEvent не предоставляет redacted()")

    # ------------------------------------------------------------------
    # Единая точка вставки
    # ------------------------------------------------------------------

    async def _insert(self, values: dict[str, Any]) -> None:
        """INSERT + commit. Любая ошибка → :class:`AuditAppendError` (deny)."""
        try:
            row = self._row()
            async with self._session_ctx() as session:
                session.add(row(**values))
                if self._session is None:
                    await session.commit()
            self._rows_written += 1
        except AuditAppendError:
            raise
        except (SQLAlchemyError, PluginRepositoryError, PluginRepositorySchemaError) as exc:
            raise AuditAppendError(f"audit append failed: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed, ничего не маскируем
            raise AuditAppendError(f"audit append failed: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------
    # admin.ports.AuditPort: async append(*, event) -> bool
    # ------------------------------------------------------------------

    async def append(self, *, event: Any) -> bool:
        """Подтверждённая запись → ``True``; ошибка → исключение, НЕ ``False``.

        ``admin.ports.AuditPort`` объявляет ``-> bool`` и fire-and-forget
        ``False`` недопустим: для позитивной мутации это deny. Поэтому вместо
        ``False`` всегда бросается ``AuditAppendError``.
        """
        projection = self._admin_projection(event)
        occurred_raw = projection.get("occurred_at") or self._now().isoformat()
        occurred = (
            datetime.fromisoformat(occurred_raw)
            if isinstance(occurred_raw, str)
            else (occurred_raw if isinstance(occurred_raw, datetime) else self._now())
        )
        if occurred.tzinfo is None or occurred.utcoffset() is None:
            occurred = occurred.replace(tzinfo=UTC)
        values = self._values(
            event_id=str(projection.get("event_id") or ""),
            event_type=str(projection.get("operation") or "plugin_admin"),
            occurred_at=occurred.astimezone(UTC),
            company_id=str(projection.get("company_id") or ""),
            plugin_id=str(projection.get("plugin_id") or ""),
            details=projection,
            row=self._row(),
        )
        await self._insert(values)
        return True

    # ------------------------------------------------------------------
    # events.ports.AuditPort: async append(event_type, *, details) -> None
    # ------------------------------------------------------------------

    async def append_event(self, event_type: str, *, details: Mapping[str, Any]) -> None:
        """Стык с диспечером событий (F7): только канонические id/digest-ы."""
        safe = redact_details(details)
        occurred = self._now()
        raw_occurred = safe.get("occurred_at")
        if isinstance(raw_occurred, str):
            try:
                parsed = datetime.fromisoformat(raw_occurred)
                occurred = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                occurred = self._now()
        values = self._values(
            event_id=str(safe.get("event_id") or f"event:{canonical_digest(details)[:24]}"),
            event_type=text(event_type, field="event_type"),
            occurred_at=occurred.astimezone(UTC),
            company_id=str(safe.get("company_id") or ""),
            plugin_id=str(safe.get("plugin_id") or ""),
            details=safe,
            row=self._row(),
        )
        await self._insert(values)

    # ------------------------------------------------------------------
    # capabilities.audit.AuditPort: SYNC append(event) -> None
    # ------------------------------------------------------------------

    def append_sync(self, event: Any) -> None:
        """Синхронный append для F2 (``PluginAuditEvent``).

        ``AsyncSession`` нельзя await'ить из sync-кода: выполняем вставку на
        выделенном фоновом loop (как в ``artifacts.py``) и ДОЖДАТЬСЯ результата.
        Ошибка — исключение (deny), не «тихо не записали».
        """
        import asyncio
        import threading

        projection = self._capability_projection(event)
        occurred = self._now()
        raw_occurred = projection.get("occurred_at")
        if isinstance(raw_occurred, str):
            try:
                parsed = datetime.fromisoformat(raw_occurred)
                occurred = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                occurred = self._now()
        values = self._values(
            event_id=str(projection.get("event_id") or ""),
            event_type=str(projection.get("event_kind") or "plugin_authorization"),
            occurred_at=occurred.astimezone(UTC),
            company_id=str(projection.get("company_id") or ""),
            plugin_id=str(projection.get("plugin_id") or "capabilities"),
            details=projection,
            row=self._row(),
        )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=_run_loop, args=(loop,), daemon=True)
            thread.start()
            try:
                future = asyncio.run_coroutine_threadsafe(self._insert(values), loop)
                future.result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=2.0)
                loop.close()
            return
        raise AuditAppendError(
            "append_sync вызван из работающего event loop; используйте await append/append_event"
        )

    # ------------------------------------------------------------------
    # Вспомогательное: проверка «нет raw данных» для тестов/ревью
    # ------------------------------------------------------------------

    @staticmethod
    def assert_no_raw_data(details: Mapping[str, Any], raw_values: Sequence[str]) -> None:
        """Проверить, что ни одно сырое значение не попало в проекцию (тесты).

        Бросает ``AssertionError`` при обнаружении. Используется unit-тестами,
        НЕ является частью рабочего решения.
        """
        serialized = json.dumps(redact_details(details), ensure_ascii=False, sort_keys=True)
        for raw in raw_values:
            assert str(raw) not in serialized, f"raw данные {raw!r} попали в аудит"


def _run_loop(loop: Any) -> None:
    import asyncio

    asyncio.set_event_loop(loop)
    loop.run_forever()


class _BorrowedSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


__all__ = ["ALLOWED_DETAIL_KEYS", "AppendOnlyAuditSink", "AuditAppendError", "redact_details"]
