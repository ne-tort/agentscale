"""``AppendOnlyAuditSink`` — append-only аудит поверх РЕАЛЬНОЙ ``PluginAuditEventRow``.

Модель A10 ``plugin_audit_events`` намеренно НЕ имеет ``details`` JSONB,
``event_id`` или ``event_type`` (универсальный «карман» стал бы обходом запрета
raw-данных). Её колонки — типизированные канонические поля:

    request_id, run_id, company_id, cabinet_id, project_id, subject_id,
    plugin_instance_id, artifact_digest, grant_id, capability, operation,
    resource, decision, code, reason, sql_digest, payload_digest, occurred_at

Часть из них NOT NULL. Append-only дополнительно защищён ORM-listener'ом
``before_update`` / ``before_delete`` в модели.

Реализует ТРИ ``AuditPort`` (разная сигнатура ``append``) через один
диспетчер :meth:`AppendOnlyAuditSink.append`:

* ``capabilities.audit.AuditPort`` (задача 03, SYNC):
  ``append(event: PluginAuditEvent) -> None``;
* ``admin.ports.AuditPort`` (задача 12, ASYNC):
  ``await append(*, event: AdminAuditEvent) -> bool``;
* ``events.ports.AuditPort`` (задача 10, ASYNC):
  ``await append(event_type: str, *, details: Mapping) -> None``.

Диспетчер разбирает форму вызова и ДЛЯ SYNC-вызова выполняет вставку на
выделенном фоновом loop (``AsyncSession`` нельзя await'ить из sync-кода,
но «тихо не записать» недопустимо). Для ASYNC-вызова всё остаётся в текущем
loop и требует ``await``.

Защита criterion 11 (§4.5)
--------------------------
В БД уходит ТОЛЬКО безопасная проекция. НЕ пишем raw SQL, параметры,
payload/body, токены, секреты, PII. :func:`redact_details` пропускает любые
``details`` через строгий allowlist скаляров: запрещённые ключи и вложенные
структуры заменяются на ``sha256:...`` digest; длинные/подозрительные строки
— тоже. Поверх этого переиспользуются ``AdminAuditEvent.redacted()`` и
``PluginAuditEvent.redacted()`` (F2), которые уже дают каноническую проекцию.

Fail-closed для позитивной мутации: ЛЮБАЯ ошибка вставки (нет модели/колонки,
``IntegrityError``, недоступность БД) → :class:`AuditAppendError`, а не
``False``/«молча не записали». Вызывающий превращает это в deny/system failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
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
    column_names,
    load_row,
    text,
)

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

_MAX_VALUE_LENGTH = 200

_RAW_MARKERS: tuple[str, ...] = (
    "select ", "insert ", "update ", "delete ", "drop ", "create ", "alter ",
    " where ", " from ", "password=", "token=", "secret=", "authorization:", "@",
)

#: Значения NOT NULL колонок, которых может не быть во входном событии.
_NEUTRAL_DEFAULTS: Mapping[str, str] = {
    "subject_id": "system",
    "plugin_instance_id": "platform",
    "capability": "platform",
    "operation": "audit",
    "resource": "platform",
    "decision": "deny",
    "code": "audit_append",
}


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
    if lowered.startswith("eyj") and value.count(".") >= 2:
        return True
    if lowered.startswith("sha256:") and len(value) == 71:
        return False  # это сам digest — ок
    return len(value) > 96 and " " not in value


def redact_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Строгая проекция: только плоские безопасные скаляры (criterion 11).

    Запрещённый ключ / вложенная структура → ``<key>_digest`` +
    ``<key>_redacted=True``: факт наличия сохраняется, значение — НЕТ.
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
            if _suspicious(value) or len(value) > _MAX_VALUE_LENGTH:
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
        if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
            result[key] = str(value.value)
            continue
        result[key] = {"type": type(value).__name__, "digest": canonical_digest(value)}
    return result


def _safe_str(value: Any, *, default: str = "") -> str:
    """Каноническое строковое поле; подозрительное → digest (не raw)."""
    if value is None:
        return default
    result = (
        str(value.value)
        if hasattr(value, "value") and isinstance(getattr(value, "value"), str)
        else str(value)
    )
    if _suspicious(result):
        return canonical_digest(result)
    return result[:_MAX_VALUE_LENGTH]


def _parse_time(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = fallback
    else:
        parsed = fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class AppendOnlyAuditSink:
    """Append-only SQL-sink. Ошибка append = исключение (deny мутации)."""

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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def rows_written(self) -> int:
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
    # Событие → безопасная строка БД
    # ------------------------------------------------------------------

    def _projection(self, event: Any, *, event_type_hint: str) -> dict[str, Any]:
        """Извлечь каноническую проекцию из любого из трёх типов событий."""
        # 1) AdminAuditEvent / PluginAuditEvent с redacted().
        redacted = getattr(event, "redacted", None)
        if callable(redacted):
            raw = redacted()
            if isinstance(raw, Mapping):
                return redact_details(dict(raw))
        # 2) Произвольный объект события — берём ТОЛЬКО безопасные атрибуты.
        safe: dict[str, Any] = {}
        for name in (
            "event_id", "event_kind", "event_type", "request_id", "run_id",
            "correlation_id", "company_id", "cabinet_id", "project_id",
            "subject_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "grant_id", "capability", "operation", "resource", "decision",
            "code", "reason", "occurred_at", "linked_event_id", "schema_version",
            "sql_digest", "payload_digest",
        ):
            value = getattr(event, name, None)
            if value is not None:
                safe[name] = value
        safe.setdefault("event_type", event_type_hint)
        return redact_details(safe)

    def _details_projection(self, event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
        """``events.ports.AuditPort``: event_type + плоские details."""
        safe = redact_details(details)
        safe.setdefault("event_type", event_type)
        safe.setdefault("operation", event_type)
        return safe

    def _values(self, safe: dict[str, Any], row: type) -> dict[str, Any]:
        """Собрать значения РЕАЛЬНЫХ колонок A10 (все NOT NULL заполнены)."""
        c = self._col
        occurred = _parse_time(safe.get("occurred_at"), self._now())
        # digest-ы: только валидные sha256, иначе NULL.
        def _digest(key: str) -> str | None:
            value = safe.get(key)
            return value if isinstance(value, str) and value.startswith("sha256:") and len(value) == 71 else None

        artifact = _digest("artifact_digest") or "sha256:" + "0" * 64
        values: dict[str, Any] = {
            c("request_id"): _safe_str(safe.get("request_id") or safe.get("event_id"), default="audit")[:128],
            c("company_id"): _safe_str(safe.get("company_id"), default="platform")[:40],
            c("subject_id"): _safe_str(safe.get("subject_id"), default=_NEUTRAL_DEFAULTS["subject_id"])[:160],
            c("plugin_instance_id"): _safe_str(
                safe.get("plugin_instance_id"), default=_NEUTRAL_DEFAULTS["plugin_instance_id"]
            )[:80],
            c("artifact_digest"): artifact,
            c("capability"): _safe_str(safe.get("capability"), default=_NEUTRAL_DEFAULTS["capability"])[:64],
            c("operation"): _safe_str(safe.get("operation") or safe.get("event_type") or safe.get("event_kind"), default=_NEUTRAL_DEFAULTS["operation"])[:64],
            c("resource"): _safe_str(safe.get("resource"), default=_NEUTRAL_DEFAULTS["resource"])[:255],
            c("decision"): _safe_str(safe.get("decision"), default=_NEUTRAL_DEFAULTS["decision"])[:32],
            c("code"): _safe_str(safe.get("code"), default=_NEUTRAL_DEFAULTS["code"])[:64],
            c("occurred_at"): occurred,
            # Опциональные (nullable) канонические поля.
            "run_id": _safe_str(safe.get("run_id"), default="")[:128] or None,
            "cabinet_id": _safe_str(safe.get("cabinet_id"), default="")[:40] or None,
            "project_id": _safe_str(safe.get("project_id"), default="")[:40] or None,
            "grant_id": _safe_str(safe.get("grant_id"), default="")[:80] or None,
            "reason": _safe_str(safe.get("reason"), default="")[:255] or None,
            "sql_digest": _digest("sql_digest"),
            "payload_digest": _digest("payload_digest"),
        }
        existing = column_names(row)
        return {key: value for key, value in values.items() if key in existing}

    # ------------------------------------------------------------------
    # Единая точка INSERT
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
    # Диспетчер: одна точка входа для всех трёх AuditPort
    # ------------------------------------------------------------------

    def append(
        self,
        event_or_type: Any = None,
        *,
        event: Any = None,
        details: Mapping[str, Any] | None = None,
    ) -> Any:
        """Единый ``append`` для трёх форм вызова.

        Формы:

        * ``append(plugin_audit_event)`` — SYNC (задача 03) → блокирующе
          выполняет вставку на фоновом loop, возвращает ``None``;
        * ``await append(*, event=admin_audit_event)`` — ASYNC (задача 12) →
          возвращает ``True``;
        * ``await append("plugin_event_received", details={...})`` — ASYNC
          (задача 10) → возвращает ``None``.

        Возврат корутины для async-форм означает, что вызывающий ОБЯЗАН
        ``await``: «вызвали и забыли» не фиксирует аудит (fail-closed).
        """
        if event is not None:
            # admin.ports.AuditPort: async append(*, event) -> bool
            return self._append_admin(event)
        if isinstance(event_or_type, str):
            # events.ports.AuditPort: async append(event_type, *, details) -> None
            return self._append_event(event_or_type, details=details or {})
        if event_or_type is None:
            raise AuditAppendError("append: не передано событие аудита")
        # capabilities.audit.AuditPort: sync append(event) -> None
        projection = self._projection(event_or_type, event_type_hint="plugin_audit")
        values = self._values(projection, self._row())
        return self._run_sync(self._insert(values))

    async def _append_admin(self, event: Any) -> bool:
        """Подтверждённая запись → ``True``; ошибка → исключение, НЕ ``False``."""
        projection = self._projection(event, event_type_hint="plugin_admin")
        values = self._values(projection, self._row())
        await self._insert(values)
        return True

    async def _append_event(self, event_type: str, *, details: Mapping[str, Any]) -> None:
        """Стык с диспечером событий (F7): только канонические id/digest-ы."""
        text(event_type, field="event_type")
        projection = self._details_projection(event_type, details)
        values = self._values(projection, self._row())
        await self._insert(values)

    # Явные имена для читаемости/тестов (не альтернативные контракты).
    async def append_event(self, event_type: str, *, details: Mapping[str, Any]) -> None:
        await self._append_event(event_type, details=details)

    def append_sync(self, event: Any) -> None:
        projection = self._projection(event, event_type_hint="plugin_audit")
        values = self._values(projection, self._row())
        self._run_sync(self._insert(values))

    # ------------------------------------------------------------------
    # Мост sync → async
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_loop, args=(loop,), name="plugin-audit-sink", daemon=True
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            return loop

    def _run_sync(self, coro: Any) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            future = asyncio.run_coroutine_threadsafe(coro, self._get_loop())
            future.result()  # ДОЖИДАЕМСЯ подтверждения; ошибка → AuditAppendError
            return
        raise AuditAppendError(
            "append_sync вызван из работающего event loop; используйте await append(...)"
        )

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._loop_thread
            self._loop = None
            self._loop_thread = None
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Вспомогательное: проверка «нет raw данных» для тестов/ревью
    # ------------------------------------------------------------------

    @staticmethod
    def assert_no_raw_data(details: Mapping[str, Any], raw_values: Sequence[str]) -> None:
        """Проверить, что ни одно сырое значение не попало в проекцию (тесты)."""
        serialized = json.dumps(redact_details(details), ensure_ascii=False, sort_keys=True)
        for raw in raw_values:
            assert str(raw) not in serialized, f"raw данные {raw!r} попали в аудит"


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


class _BorrowedSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


__all__ = ["AppendOnlyAuditSink", "AuditAppendError", "canonical_digest", "redact_details"]
