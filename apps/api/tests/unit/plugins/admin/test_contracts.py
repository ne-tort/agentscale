"""Unit-тесты типизированных контрактов F3-admin (fail-closed валидация)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.plugins.admin.contracts import (
    ALLOWED_CAPABILITIES,
    PLUGIN_CLAIMS_TTL_SECONDS,
    QUOTA_KEYS,
    AdminAuditEvent,
    AdminDecision,
    AdminOperation,
    ArtifactPublicationStatus,
    ArtifactRecord,
    CapabilityGrantRecord,
    CapabilitySet,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def test_registry_matches_architecture() -> None:
    """Реестр capability из §4.4 + db.direct/db.migrations из §2.3."""

    assert "db.direct" in ALLOWED_CAPABILITIES
    assert "objects" in ALLOWED_CAPABILITIES
    assert set(QUOTA_KEYS) == {
        "ops_per_minute",
        "max_rows",
        "max_bytes",
        "max_keys",
        "max_object_bytes",
        "max_egress_bytes",
    }
    assert PLUGIN_CLAIMS_TTL_SECONDS == 300


def test_capability_set_rejects_unknown_and_duplicates() -> None:
    with pytest.raises(ValueError, match="неизвестные capability"):
        CapabilitySet(capabilities=("raw_sql",))
    with pytest.raises(ValueError, match="не должны повторяться"):
        CapabilitySet(capabilities=("cache", "cache"))
    with pytest.raises(ValueError, match="не должен быть пустым"):
        CapabilitySet(capabilities=())
    assert CapabilitySet(capabilities=("cache", "objects")).capabilities == ("cache", "objects")


def test_egress_policy_validation() -> None:
    assert EgressPolicy().mode is EgressMode.NONE
    with pytest.raises(ValueError, match="mode=none"):
        EgressPolicy(mode=EgressMode.NONE, allowed_hosts=("example.com",))
    with pytest.raises(ValueError, match="webhooks-only требует"):
        EgressPolicy(mode=EgressMode.WEBHOOKS_ONLY, allowed_hosts=("example.com",))
    with pytest.raises(ValueError, match="approval"):
        EgressPolicy(mode=EgressMode.FULL)
    policy = EgressPolicy(
        mode=EgressMode.WEBHOOKS_ONLY,
        allowed_hosts=("example.com",),
        allowed_paths=("/hook",),
    )
    assert policy.allowed_hosts == ("example.com",)
    assert EgressPolicy(mode=EgressMode.FULL, approved_for_full=True).mode is EgressMode.FULL


def test_quota_set_rejects_zero_and_bool() -> None:
    """0 может трактоваться адаптером как «без лимита» — запрещаем (fail-closed)."""

    with pytest.raises(ValueError, match="ops_per_minute"):
        QuotaSet(ops_per_minute=0)
    with pytest.raises(ValueError, match="max_rows"):
        QuotaSet(max_rows=True)  # bool является подклассом int
    assert QuotaSet().max_bytes > 0


def test_artifact_record_publication_gate() -> None:
    published = ArtifactRecord(
        plugin_id="mod_equipment",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    quarantined = ArtifactRecord(
        plugin_id="mod_equipment",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        status=ArtifactPublicationStatus.QUARANTINED,
    )
    assert published.is_published is True
    assert quarantined.is_published is False


def test_capability_grant_record_validates_digest_and_time() -> None:
    grant = CapabilityGrantRecord(
        grant_id="grant_1",
        company_id="co_a",
        plugin_id="mod_equipment",
        plugin_instance_id="inst_1",
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=GrantStatus.ACTIVE,
        created_at=NOW,
    )
    assert grant.is_active is True
    assert grant.egress_policy.mode is EgressMode.NONE
    assert grant.quotas.ops_per_minute > 0

    with pytest.raises(ValueError, match="sha256"):
        CapabilityGrantRecord(
            grant_id="grant_bad",
            company_id="co_a",
            plugin_id="mod_equipment",
            plugin_instance_id="inst_1",
            manifest_version="1.2.0",
            artifact_digest="not-a-digest",
            capabilities=("cache",),
            egress_policy=EgressPolicy(),
            quotas=QuotaSet(),
            status=GrantStatus.ACTIVE,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CapabilityGrantRecord(
            grant_id="grant_2",
            company_id="co_a",
            plugin_id="mod_equipment",
            plugin_instance_id="inst_1",
            manifest_version="1.2.0",
            artifact_digest=DIGEST,
            capabilities=("cache",),
            egress_policy=EgressPolicy(),
            quotas=QuotaSet(),
            status=GrantStatus.ACTIVE,
            created_at=datetime(2026, 11, 8, 9, 0),  # naive
        )


def test_audit_event_redaction_has_no_sensitive_fields() -> None:
    event = AdminAuditEvent(
        event_id="ev_1",
        occurred_at=NOW + timedelta(seconds=1),
        company_id="co_a",
        plugin_id="mod_equipment",
        operation=AdminOperation.GRANT,
        decision=AdminDecision.ALLOW,
        grant_id="grant_1",
        artifact_digest=DIGEST,
    )
    redacted = event.redacted()
    # Только канонические идентификаторы/коды; ни raw SQL, ни payload, ни токенов, ни PII.
    assert "token" not in redacted
    assert "sql" not in redacted
    assert "payload" not in redacted
    assert "password" not in redacted
    assert redacted["operation"] == "plugin_grant"
    assert redacted["decision"] == "allow"
    assert redacted["artifact_digest"] == DIGEST
