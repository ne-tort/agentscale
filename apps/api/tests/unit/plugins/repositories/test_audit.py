"""``AppendOnlyAuditSink``: аудит НЕ содержит raw данных (критерий №11).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.contracts import (
    AdminAuditEvent,
    AdminDecision,
    AdminOperation,
)
from prodavan.infrastructure.persistence.repositories.plugins.audit import (
    AppendOnlyAuditSink,
    AuditAppendError,
    redact_details,
)

from .conftest import DIGEST, NOW, FakeSession, FakeSessionFactory


RAW_SQL = "SELECT * FROM users WHERE password='hunter2'"
RAW_TOKEN = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
RAW_PAYLOAD = '{"card_number": "4111111111111111"}'


def test_redact_details_never_leaks_raw_values() -> None:
    """Raw SQL / params / payload / token / PII → только sha256 digest."""
    details = {
        "sql": RAW_SQL,
        "params": ["hunter2"],
        "payload": RAW_PAYLOAD,
        "authorization": RAW_TOKEN,
        "password": "hunter2",
        "email": "user@example.com",
        "request_id": "req_01",
        "code": "deny",
        "payload_digest": DIGEST,
    }
    result = redact_details(details)
    AppendOnlyAuditSink.assert_no_raw_data(
        details, [RAW_SQL, RAW_TOKEN, RAW_PAYLOAD, "hunter2", "user@example.com"]
    )
    # Безопасные канонические поля остаются как есть.
    assert result["request_id"] == "req_01"
    assert result["code"] == "deny"
    assert result["payload_digest"] == DIGEST
    # Запрещённые — помечены и заменены digest'ом.
    assert result["sql_redacted"] is True
    assert result["sql_digest"].startswith("sha256:")
    assert result["payload_redacted"] is True


def test_redact_details_handles_nested_structures() -> None:
    """Вложенный mapping не протаскивает сырые данные (утечка через meta)."""
    result = redact_details({"meta": {"password": "hunter2", "token": RAW_TOKEN}})
    assert "hunter2" not in str(result)
    assert RAW_TOKEN not in str(result)
    assert result["meta_redacted"] is True


def test_redact_details_rejects_long_suspicious_strings() -> None:
    long_token = "a" * 120
    result = redact_details({"reason": long_token})
    assert long_token not in str(result)
    assert result["reason_redacted"] is True


def test_admin_audit_event_append_writes_only_canonical_columns() -> None:
    """``AdminAuditEvent.redacted()`` → РЕАЛЬНЫЕ колонки A10, без details/raw."""
    session = FakeSession()
    sink = AppendOnlyAuditSink(session_factory=FakeSessionFactory(session), clock=lambda: NOW)
    event = AdminAuditEvent(
        event_id="evt_01",
        occurred_at=NOW,
        company_id="co_123",
        plugin_id="mod_equipment",
        operation=AdminOperation.GRANT,
        decision=AdminDecision.ALLOW,
        grant_id="pcg_01",
        artifact_digest=DIGEST,
        code="ok",
        reason="granted",
    )
    # Собираем значения строки без обращения к БД (fail-closed маппинг).
    projection = sink._projection(event, event_type_hint="plugin_admin")
    values = sink._values(projection, sink._row())
    assert "details" not in values
    assert "payload" not in values
    assert values["company_id"] == "co_123"
    assert values["artifact_digest"] == DIGEST
    assert values["code"] == "ok"
    assert values["decision"] == "allow"
    # Ни одно запрещённое имя модели A10 не появилось.
    from prodavan.infrastructure.persistence.models.plugin_platform import (
        AUDIT_FORBIDDEN_RAW_COLUMNS,
    )

    assert not (set(values) & AUDIT_FORBIDDEN_RAW_COLUMNS)


def test_all_not_null_audit_columns_are_filled() -> None:
    """У A10 часть колонок NOT NULL — ни одна не остаётся NULL/пустой."""
    session = FakeSession()
    sink = AppendOnlyAuditSink(session_factory=FakeSessionFactory(session), clock=lambda: NOW)
    values = sink._values({}, sink._row())
    for required in (
        "request_id", "company_id", "subject_id", "plugin_instance_id",
        "artifact_digest", "capability", "operation", "resource", "decision",
        "code", "occurred_at",
    ):
        assert required in values, f"NOT NULL колонка {required} не заполнена"
        assert values[required] not in (None, "")


@pytest.mark.asyncio
async def test_append_failure_raises_not_false() -> None:
    """Append failure для позитивной мутации = исключение (deny), НЕ ``False``."""
    session = FakeSession()
    session.fail_on_commit = RuntimeError("audit table unavailable")
    sink = AppendOnlyAuditSink(session_factory=FakeSessionFactory(session), clock=lambda: NOW)
    event = AdminAuditEvent(
        event_id="evt_01", occurred_at=NOW, company_id="co_123",
        plugin_id="mod_equipment", operation=AdminOperation.GRANT,
        decision=AdminDecision.ALLOW,
    )
    with pytest.raises(AuditAppendError):
        await sink.append(event=event)


@pytest.mark.asyncio
async def test_event_audit_port_shape() -> None:
    """``events.ports.AuditPort``: ``append(event_type, *, details)``."""
    session = FakeSession()
    sink = AppendOnlyAuditSink(session_factory=FakeSessionFactory(session), clock=lambda: NOW)
    await sink.append("plugin_event_received", details={
        "company_id": "co_123",
        "event_id": "evt_01",
        "payload_digest": DIGEST,
        "attempts": 1,
    })
    assert session.commits == 1
    assert sink.rows_written == 1
    inserted = session.added[-1]
    assert inserted.payload_digest == DIGEST or inserted.operation == "plugin_event_received"


def test_audit_model_forbids_raw_columns() -> None:
    """Сама модель A10 явно запрещает колонки с raw-данными."""
    from prodavan.infrastructure.persistence.models.plugin_platform import (
        AUDIT_FORBIDDEN_RAW_COLUMNS,
        PluginAuditEventRow,
    )

    columns = set(PluginAuditEventRow.__table__.columns.keys())
    assert not (columns & AUDIT_FORBIDDEN_RAW_COLUMNS)
    assert "details" not in columns
