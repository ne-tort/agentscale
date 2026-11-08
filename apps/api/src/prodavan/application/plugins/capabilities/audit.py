"""Аудит capability-запросов плагинов (F2, acceptance №11).

Расширение идеи ``AuditEvent`` из ``application/capability_gateway_service.py``:
сохраняем его поля (request_id, occurred_at, company_id, subject_id, capability,
operation, resource, decision, code, grant_id, reason) и добавляем обязательную
плагиновую идентичность (§4.5): run_id/correlation_id, cabinet_id, project_id,
plugin_instance_id, artifact_digest.

Источник правил redaction — docs/08-migration/audit-append-shape-decision.md:13-19:

* **НИКОГДА** не пишем raw SQL, параметры с данными, тела payload, токены,
  секреты, PII. Только канонические digest-ы (``sql_digest``, ``payload_digest``,
  ``artifact_digest`` и т.п.).
* Успешная авторизация и факт успешного исполнения — **РАЗНЫЕ** события,
  связанные ``correlation_id``/``request_id``. Успех одного не подразумевает
  успех другого (§4.5, decision §4 «Decision and result»).
* Append failure для позитивной мутации = deny/system failure, а не allow.
  Поэтому ``AuditPort.append`` не имеет «тихого» варианта: исключение
  пробрасывается сервису, который превращает его в отказ.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from prodavan.application.capability_gateway_service import (
    CapabilityDecision,
    DecisionCode,
)

# ---------------------------------------------------------------------------
# Классификация событий: авторизация ≠ исполнение (§4.5)
# ---------------------------------------------------------------------------


class AuditEventKind(StrEnum):
    """Тип факта. Два успеха — ДВА разных события, не один."""

    AUTHORIZATION = "plugin_authorization"
    INVOCATION = "plugin_invocation"
    QUOTA = "plugin_quota"
    REVOCATION = "plugin_revocation"
    ADAPTER_MISSING = "plugin_adapter_missing"


class AuditDecision(StrEnum):
    """Решение/результат. ``SUCCESS`` ≠ ``ALLOW``: allow — про политику,
    success — про факт возврата адаптера (decision §4, строки 109-117)."""

    ALLOW = "allow"
    DENY = "deny"
    ERROR = "error"
    SUCCESS = "success"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


#: Ключи, значения которых НИКОГДА не попадают в аудит. Сравнение по подстроке
#: в нижнем регистре: ``password`` ловит и ``db_password``, и ``PASSWORD_HASH``.
#: Сюда же входят raw SQL/params/payload/body/tokens/secrets/PII (§4.5).
_FORBIDDEN_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "sql", "query", "statement", "params", "parameters", "bind", "bindings",
        "payload", "body", "raw", "content", "text", "data", "value", "values",
        "password", "passwd", "pwd", "secret", "token", "access_token", "refresh_token",
        "authorization", "auth", "cookie", "set_cookie", "api_key", "apikey",
        "credential", "credentials", "private_key", "signature", "jwt", "bearer",
        "email", "phone", "address", "passport", "inn", "snils", "pii",
        "ssn", "name", "first_name", "last_name", "full_name", "birth",
        "dsn", "connection_string", "base64", "b64", "blob", "file", "bytes",
    }
)

#: Ключи, значения которых БЕЗОПАСНЫ: идентификаторы, digest-ы, коды, счётчики.
_ALLOWED_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "request_id", "run_id", "correlation_id", "event_id", "event_kind",
        "company_id", "cabinet_id", "project_id", "subject_id",
        "plugin_instance_id", "artifact_digest", "grant_id", "capability",
        "operation", "resource", "decision", "code", "reason", "occurred_at",
        "digest", "sql_digest", "payload_digest", "body_digest", "arguments_digest",
        "result_digest", "value_digest", "content_digest", "policy_digest",
        "input_schema_digest", "response_digest", "message_digest",
        "http_status", "status_code", "allowed", "ok", "found", "deleted",
        "limit", "used", "remaining", "amount", "retry_after_seconds",
        "bytes", "size_bytes", "stored_bytes", "total_bytes", "matched", "subscribers",
        "partition", "registered", "manual_commit", "ttl_seconds", "expires_at_epoch",
        "schema_version", "redaction_version", "linked_event_id",
    }
)

REDACTION_VERSION: str = "plugin-audit-redaction-v1"


def _is_forbidden(key: str) -> bool:
    lowered = key.lower()
    # Точное совпадение с разрешённым списком имеет приоритет: «payload_digest»
    # безопасно, хотя содержит подстроку «payload».
    if lowered in _ALLOWED_KEY_TOKENS:
        return False
    return any(token in lowered for token in _FORBIDDEN_KEY_TOKENS)


def canonical_digest(value: Any) -> str:
    """Канонический sha256 от произвольного значения — вместо raw значения.

    Используется для ``sql_digest`` / ``payload_digest`` / ``body_digest``:
    в аудит уходит ТОЛЬКО digest, исходное значение не сохраняется нигде.
    """
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def redact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Вернуть БЕЗОПАСНУЮ проекцию для аудита.

    Правила (docs/08-migration/audit-append-shape-decision.md:13-19):

    1. Ключи с ``password``/``token``/``sql``/``body``/``secret``/``payload``/
       ``credentials``/PII и т.п. не отбрасываются молча, а заменяются
       каноническим ``<key>_digest`` — факт наличия значения сохраняется,
       само значение — НЕТ.
    2. Вложенные mapping/sequence обрабатываются рекурсивно (иначе утечка
       через ``{"meta": {"password": ...}}``).
    3. Строковые значения, похожие на токен/DSN/``Bearer``/``sha256=`` HMAC,
       заменяются digest'ом даже под «безопасным» ключом.
    4. Никаких ``repr`` сырых значений в результате.

    Тесты проверяют, что ``password``/``token``/``sql``/``body`` убраны.
    """
    result: dict[str, Any] = {}
    for raw_key, value in payload.items():
        key = str(raw_key)
        if _is_forbidden(key):
            # Явно маркируем, что значение было изъято, и даём только digest.
            result[f"{key}_digest"] = canonical_digest(value)
            result[f"{key}_redacted"] = True
            continue
        # Даже под «безопасным» ключом (``resource``/``reason``/``capability``)
        # строка, похожая на SQL/токен/DSN/PII, заменяется digest'ом: иначе
        # утечка через канонические поля события.
        if isinstance(value, str) and (_looks_like_secret(value) or _looks_like_raw_data(value)):
            result[f"{key}_digest"] = canonical_digest(value)
            result[f"{key}_redacted"] = True
            continue
        result[key] = _redact_value(key, value)
    return result


def _redact_value(key: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str):
        return canonical_digest(value) if _looks_like_secret(value) else value
    if isinstance(value, (datetime,)):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Неизвестный объект: сохраняем только тип и digest, не repr/поля.
    return {"type": type(value).__name__, "digest": canonical_digest(value)}


_SUSPICIOUS_PREFIXES: tuple[str, ...] = (
    "bearer ", "basic ", "sha256=", "postgres://", "postgresql://",
    "mysql://", "mongodb://", "redis://", "amqp://",
)

#: Маркеры raw SQL / параметров / чувствительных выражений в строке значения.
_RAW_DATA_MARKERS: tuple[str, ...] = (
    "select ", "insert ", "update ", "delete ", "drop ", "create ",
    "alter ", "copy ", " where ", " from ",
    "password=", "token=", "secret=", "api_key=", "authorization:",
    "@",  # email / user@host — PII
)


def _looks_like_raw_data(value: str) -> bool:
    """Эвристика: raw SQL/params/PII под «безопасным» ключом → только digest."""
    lowered = value.lower()
    return any(marker in lowered for marker in _RAW_DATA_MARKERS)


def _looks_like_secret(value: str) -> bool:
    """Эвристика: даже под безопасным ключом не пишем подозрительный секрет."""
    lowered = value.lower()
    if any(lowered.startswith(prefix) for prefix in _SUSPICIOUS_PREFIXES):
        return True
    if lowered.startswith("eyj") and value.count(".") >= 2:  # JWT
        return True
    if lowered.startswith("sha256:") and len(value) == 71:  # это сам digest — ок
        return False
    if _looks_like_raw_data(lowered):
        return True
    # Длинная строка без пробелов — вероятно ключ/токен/base64.
    return len(value) > 96 and " " not in value


# ---------------------------------------------------------------------------
# Событие
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginAuditEvent:
    """Append-only факт: полная плагиновая идентичность, БЕЗ raw данных.

    Поля из §4.5 (расширение ``AuditEvent`` ядра). ``artifact_digest`` и
    ``resource``/``operation`` — канонические идентификаторы, а не содержимое.
    ``linked_event_id`` связывает авторизацию с исполнением вместе с
    ``request_id``/``correlation_id``; событие успеха НЕ заменяет событие allow.
    """

    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_kind: AuditEventKind = AuditEventKind.AUTHORIZATION
    request_id: str = ""
    run_id: str | None = None
    correlation_id: str | None = None
    company_id: str = ""
    cabinet_id: str | None = None
    project_id: str | None = None
    subject_id: str = ""
    plugin_instance_id: str = ""
    artifact_digest: str = ""
    grant_id: str | None = None
    capability: str = ""
    operation: str = ""
    resource: str = ""
    decision: AuditDecision | CapabilityDecision | str = AuditDecision.DENY
    code: DecisionCode | str = DecisionCode.INVALID_REQUEST
    reason: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    linked_event_id: str | None = None
    schema_version: str = REDACTION_VERSION
    digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("event_id", "request_id", "company_id", "subject_id", "plugin_instance_id", "capability", "operation", "resource"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.artifact_digest, str) or not self.artifact_digest.startswith("sha256:"):
            raise ValueError("artifact_digest must be a sha256 digest")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        # reason — только канонический код/короткая причина, не произвольный
        # текст с данными. Длинные свободные строки запрещены.
        if self.reason is not None and len(self.reason) > 200:
            raise ValueError("reason must be a short canonical code, not raw data")
        for key, value in self.digests.items():
            if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
                raise ValueError(f"digest {key!r} must be a sha256 digest")

    def redacted(self) -> dict[str, Any]:
        """Безопасная проекция: БЕЗ request content, БЕЗ resources с данными.

        Даже если в ``reason``/``resource`` кто-то попытался пронести SQL или
        токен, ``redact`` заменит значение на digest.
        """
        raw: dict[str, Any] = {
            "event_id": self.event_id,
            "event_kind": self.event_kind.value,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "company_id": self.company_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "subject_id": self.subject_id,
            "plugin_instance_id": self.plugin_instance_id,
            "artifact_digest": self.artifact_digest,
            "grant_id": self.grant_id,
            "capability": self.capability,
            "operation": self.operation,
            "resource": self.resource,
            "decision": self.decision.value if isinstance(self.decision, StrEnum) else str(self.decision),
            "code": self.code.value if isinstance(self.code, StrEnum) else str(self.code),
            "reason": self.reason,
            "occurred_at": self.occurred_at,
            "linked_event_id": self.linked_event_id,
            "schema_version": self.schema_version,
            "redaction_version": REDACTION_VERSION,
            **{key: value for key, value in self.digests.items()},
        }
        return redact(raw)

    def to_dict(self) -> dict[str, Any]:
        """Совместимый с ``AuditEvent.to_dict`` алиас; всегда redacted."""
        return self.redacted()


@runtime_checkable
class AuditPort(Protocol):
    """Append-only sink. Исключение = отказ мутации, а не «просто не записали»."""

    def append(self, event: PluginAuditEvent) -> None: ...


class InMemoryAudit:
    """Тестовый append-only sink; хранит уже ``redacted()`` проекции.

    Намеренно НЕ хранит сам ``PluginAuditEvent`` целиком: симулирует границу
    «в БД уходит только безопасная проекция». Список ``events`` содержит
    исходные объекты для проверки числа/типов событий, ``rows`` — то, что
    реально было бы записано.
    """

    def __init__(self) -> None:
        self.events: list[PluginAuditEvent] = []
        self.rows: list[dict[str, Any]] = []

    def append(self, event: PluginAuditEvent) -> None:
        row = event.redacted()
        self.events.append(event)
        self.rows.append(row)

    def by_kind(self, kind: AuditEventKind) -> list[PluginAuditEvent]:
        return [event for event in self.events if event.event_kind is kind]


class FailingAudit:
    """Для проверки fail-closed: append всегда бросает (audit failure = deny)."""

    def append(self, event: PluginAuditEvent) -> None:
        raise RuntimeError("audit append failed")


def authorization_event(
    *,
    envelope: Any,
    capability: str,
    operation: str,
    resource: str,
    decision: CapabilityDecision,
    code: DecisionCode,
    grant_id: str | None,
    reason: str | None,
    occurred_at: datetime,
    correlation_id: str | None = None,
) -> PluginAuditEvent:
    """Факт РЕШЕНИЯ об авторизации (НЕ факт исполнения)."""
    return PluginAuditEvent(
        event_kind=AuditEventKind.AUTHORIZATION,
        request_id=envelope.request_id,
        run_id=envelope.run_id,
        correlation_id=correlation_id or envelope.correlation_id,
        company_id=envelope.company_id,
        cabinet_id=envelope.cabinet_id,
        project_id=envelope.project_id,
        subject_id=envelope.subject_id,
        plugin_instance_id=envelope.plugin_instance_id,
        artifact_digest=envelope.artifact_digest,
        grant_id=grant_id,
        capability=capability,
        operation=operation,
        resource=resource,
        decision=decision,
        code=code,
        reason=reason,
        occurred_at=occurred_at,
    )


def invocation_event(
    *,
    envelope: Any,
    capability: str,
    operation: str,
    resource: str,
    decision: AuditDecision,
    code: DecisionCode,
    grant_id: str | None,
    reason: str | None,
    occurred_at: datetime,
    linked_event_id: str,
    result_digest: str | None = None,
) -> PluginAuditEvent:
    """Факт ИСПОЛНЕНИЯ адаптера; связь с allow через linked_event_id.

    Успех создаёт ОТДЕЛЬНОЕ событие ``AuditEventKind.INVOCATION`` с
    ``decision=SUCCESS``. Оно никогда не перезаписывает и не заменяет событие
    авторизации (acceptance «success audit ≠ authorization audit»).
    """
    digests: dict[str, str] = {}
    if result_digest is not None:
        digests["result_digest"] = result_digest
    return PluginAuditEvent(
        event_kind=AuditEventKind.INVOCATION,
        request_id=envelope.request_id,
        run_id=envelope.run_id,
        correlation_id=envelope.correlation_id,
        company_id=envelope.company_id,
        cabinet_id=envelope.cabinet_id,
        project_id=envelope.project_id,
        subject_id=envelope.subject_id,
        plugin_instance_id=envelope.plugin_instance_id,
        artifact_digest=envelope.artifact_digest,
        grant_id=grant_id,
        capability=capability,
        operation=operation,
        resource=resource,
        decision=decision,
        code=code,
        reason=reason,
        occurred_at=occurred_at,
        linked_event_id=linked_event_id,
        digests=digests,
    )


__all__ = [
    "AuditDecision",
    "AuditEventKind",
    "AuditPort",
    "FailingAudit",
    "InMemoryAudit",
    "PluginAuditEvent",
    "REDACTION_VERSION",
    "authorization_event",
    "canonical_digest",
    "invocation_event",
    "redact",
]
