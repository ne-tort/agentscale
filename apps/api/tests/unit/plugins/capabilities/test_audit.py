"""Unit-тесты redaction аудита плагинов (F2, acceptance №11).

docs/08-migration/audit-append-shape-decision.md:13-19: raw SQL, параметры,
payload/body, токены, секреты, PII НИКОГДА не попадают в аудит — только
канонические digest-ы. Успешная авторизация и успешное исполнение — РАЗНЫЕ
события, связанные correlation/linked id.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (верификация заморожена, план §0.2).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from prodavan.application.capability_gateway_service import (
    CapabilityDecision,
    DecisionCode,
)
from prodavan.application.plugins.capabilities.audit import (
    AuditDecision,
    AuditEventKind,
    FailingAudit,
    InMemoryAudit,
    PluginAuditEvent,
    REDACTION_VERSION,
    authorization_event,
    canonical_digest,
    invocation_event,
    redact,
)

DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def envelope() -> object:
    from prodavan.application.plugins.capabilities.contracts import CapabilityEnvelope

    return CapabilityEnvelope(
        request_id="req-1",
        correlation_id="corr-1",
        run_id="run-1",
        company_id="co_123",
        cabinet_id="cab_456",
        project_id="prj_1",
        subject_id="plugin:mod:co_123:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=DIGEST,
        grant_id="grant_1",
    )


# ---------------------------------------------------------------------------
# redaction: password / token / sql / body
# ---------------------------------------------------------------------------


def test_redact_removes_password_token_sql_and_body_values() -> None:
    """Кр. 11: ни одного raw значения; только ``<key>_digest`` и маркер."""
    result = redact(
        {
            "password": "hunter2",
            "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
            "sql": "SELECT * FROM users WHERE email='a@b.c'",
            "body": '{"secret": "plaintext"}',
            "request_id": "req-1",
            "capability": "cache",
        }
    )

    assert "password" not in result
    assert "token" not in result
    assert "sql" not in result
    assert "body" not in result
    assert result["password_digest"].startswith("sha256:")
    assert result["token_digest"].startswith("sha256:")
    assert result["sql_digest"].startswith("sha256:")
    assert result["body_digest"].startswith("sha256:")
    assert result["password_redacted"] is True
    assert result["request_id"] == "req-1"
    assert result["capability"] == "cache"

    # Нигде в сериализованной проекции нет исходных значений.
    import json

    serialized = json.dumps(result, ensure_ascii=False)
    for leaked in ("hunter2", "eyJhbGciOi", "SELECT * FROM users", "plaintext", "a@b.c"):
        assert leaked not in serialized


def test_redact_handles_nested_and_varied_key_names() -> None:
    """Вложенные структуры и ``db_password``/``Authorization``/``payload``."""
    result = redact(
        {
            "meta": {"db_password": "p", "apiKey": "k"},
            "Authorization": "Bearer abc.def.ghi",
            "payload": {"email": "user@example.com", "bind_params": ["1", "2"]},
            "query": "DROP TABLE equipment",
            "sql_digest": DIGEST,  # безопасный ключ остаётся как есть
        }
    )
    assert "db_password" not in result["meta"]
    assert "apiKey" not in result["meta"]
    assert result["meta"]["db_password_digest"].startswith("sha256:")
    assert "Authorization" not in result
    assert "payload" not in result
    assert result["sql_digest"] == DIGEST
    import json

    serialized = json.dumps(result)
    for leaked in ("DROP TABLE equipment", "user@example.com", "Bearer abc", '"p"', '"k"'):
        assert leaked not in serialized


def test_redact_replaces_secret_like_strings_even_under_safe_keys() -> None:
    """Даже под «безопасным» ключом Bearer/JWT/DSN заменяется digest'ом."""
    result = redact(
        {
            "reason": "postgres://user:pass@tenant-db:5432/prodavan",
            "note": "Bearer " + "x" * 120,
        }
    )
    assert result["reason"].startswith("sha256:")
    assert result["note"].startswith("sha256:")


def test_canonical_digest_is_stable_and_sha256() -> None:
    first = canonical_digest({"a": 1, "b": [2, 3]})
    second = canonical_digest({"b": [2, 3], "a": 1})
    assert first == second
    assert first.startswith("sha256:") and len(first) == 71


def test_audit_event_redacted_contains_no_raw_content() -> None:
    """Даже если resource/reason пытаются пронести данные — redact их убирает."""
    event = PluginAuditEvent(
        request_id="req-1", company_id="co_123", subject_id="sub-1",
        plugin_instance_id="inst_abc", artifact_digest=DIGEST, grant_id="grant_1",
        capability="cache", operation="cache.get",
        resource="SELECT * FROM secrets",
        decision=AuditDecision.ALLOW, code=DecisionCode.ALLOWED,
        reason="token=abc123", occurred_at=NOW,
    )
    row = event.redacted()
    assert row["resource_digest"].startswith("sha256:")
    assert "SELECT * FROM secrets" not in str(row)
    assert row["reason_digest"].startswith("sha256:")
    assert row["redaction_version"] == REDACTION_VERSION
    assert row["event_kind"] == AuditEventKind.AUTHORIZATION.value


def test_plugin_audit_event_has_full_plugin_identity() -> None:
    """§4.5: все обязательные поля идентичности присутствуют в redacted-проекции."""
    event = PluginAuditEvent(
        request_id="req-1", run_id="run-1", correlation_id="corr-1",
        company_id="co_123", cabinet_id="cab_456", project_id="prj_1",
        subject_id="sub-1", plugin_instance_id="inst_abc", artifact_digest=DIGEST,
        grant_id="grant_1", capability="objects", operation="objects.list",
        resource="bucket/prefix", decision=AuditDecision.ALLOW,
        code=DecisionCode.ALLOWED, occurred_at=NOW,
    )
    row = event.redacted()
    for name in (
        "request_id", "run_id", "correlation_id", "company_id", "cabinet_id",
        "project_id", "subject_id", "plugin_instance_id", "artifact_digest",
        "grant_id", "capability", "operation", "decision", "code", "occurred_at",
    ):
        assert name in row, name


def test_event_rejects_long_freeform_reason() -> None:
    """reason — канонический код, а не произвольный текст с данными."""
    import pytest

    with pytest.raises(ValueError):
        PluginAuditEvent(
            request_id="req-1", company_id="co_123", subject_id="sub-1",
            plugin_instance_id="inst_abc", artifact_digest=DIGEST,
            capability="cache", operation="cache.get", resource="key",
            reason="x" * 500, occurred_at=NOW,
        )


# ---------------------------------------------------------------------------
# success audit != authorization audit
# ---------------------------------------------------------------------------


def test_success_audit_is_a_separate_event_from_authorization() -> None:
    """Успех адаптера ≠ allow: два РАЗНЫХ события, связанные linked_event_id."""
    sink = InMemoryAudit()
    env = envelope()
    authorization = authorization_event(
        envelope=env, capability="cache", operation="cache.get",
        resource="key", decision=CapabilityDecision.ALLOW,
        code=DecisionCode.ALLOWED, grant_id="grant_1", reason=None, occurred_at=NOW,
    )
    sink.append(authorization)
    success = invocation_event(
        envelope=env, capability="cache", operation="cache.get",
        resource="key", decision=AuditDecision.SUCCESS, code=DecisionCode.ALLOWED,
        grant_id="grant_1", reason="adapter_invocation_succeeded",
        occurred_at=NOW, linked_event_id=authorization.event_id, result_digest=DIGEST,
    )
    sink.append(success)

    assert len(sink.events) == 2
    assert sink.events[0].event_kind is AuditEventKind.AUTHORIZATION
    assert sink.events[1].event_kind is AuditEventKind.INVOCATION
    assert sink.events[0].event_id != sink.events[1].event_id
    assert sink.events[1].linked_event_id == sink.events[0].event_id
    assert sink.events[0].decision is CapabilityDecision.ALLOW
    assert sink.events[1].decision is AuditDecision.SUCCESS
    # Одно событие НЕ подразумевает другое.
    assert len(sink.by_kind(AuditEventKind.AUTHORIZATION)) == 1
    assert len(sink.by_kind(AuditEventKind.INVOCATION)) == 1
    assert len(sink.rows) == 2


def test_append_failure_is_not_silent_allow() -> None:
    """Append failure позитивной мутации = deny/system failure (§4.5)."""
    import pytest

    sink = FailingAudit()
    event = PluginAuditEvent(
        request_id="req-1", company_id="co_123", subject_id="sub-1",
        plugin_instance_id="inst_abc", artifact_digest=DIGEST,
        capability="cache", operation="cache.get", resource="key", occurred_at=NOW,
    )
    with pytest.raises(RuntimeError):
