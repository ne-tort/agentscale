"""Source contracts for dynamic-app persistence capability grants."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from prodavan.domain.dynamic_apps.grants import (
    GrantDecision,
    GrantDenyReason,
    GrantScope,
    GrantScopeKind,
    PersistenceCapabilityGrant,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_grant(**overrides: object) -> PersistenceCapabilityGrant:
    values: dict[str, object] = {
        "id": "dgrant-1",
        "app_id": "billing",
        "app_version_id": "version-1",
        "tenant_id": "tenant-1",
        "scope": GrantScope(kind=GrantScopeKind.PROJECT, id="project-1"),
        "operations": ("read", "write"),
        "resources": ("invoices",),
        "domains": ("storage",),
        "issued_at": NOW,
        "issued_by": "admin-1",
    }
    values.update(overrides)
    return PersistenceCapabilityGrant(**values)


def test_grant_allows_only_exact_app_version_scope_and_requested_tuple() -> None:
    grant = make_grant(expires_at=NOW + timedelta(hours=1))

    decision = grant.authorize(
        app_id="billing",
        app_version_id="version-1",
        tenant_id="tenant-1",
        scope=GrantScope(kind=GrantScopeKind.PROJECT, id="project-1"),
        operation="read",
        resource="invoices",
        domain="storage",
        now=NOW + timedelta(minutes=1),
    )

    assert decision.decision is GrantDecision.ALLOW
    assert decision.grant_id == "dgrant-1"


def test_grant_is_deny_by_default_for_mismatch_and_expiry() -> None:
    grant = make_grant(expires_at=NOW + timedelta(hours=1))
    base_request = {
        "app_id": "billing",
        "app_version_id": "version-1",
        "tenant_id": "tenant-1",
        "scope": GrantScope(kind=GrantScopeKind.PROJECT, id="project-1"),
        "operation": "read",
        "resource": "invoices",
        "domain": "storage",
        "now": NOW + timedelta(minutes=1),
    }

    assert grant.authorize(**(base_request | {"app_version_id": "version-2"})).reason is GrantDenyReason.VERSION_MISMATCH
    assert grant.authorize(**(base_request | {"operation": "delete"})).reason is GrantDenyReason.OPERATION_NOT_GRANTED
    assert grant.authorize(**(base_request | {"now": NOW + timedelta(hours=1)})).reason is GrantDenyReason.EXPIRED


def test_revoked_grant_retains_revoke_audit_metadata_and_denies() -> None:
    grant = make_grant(
        revoked_at=NOW + timedelta(minutes=5),
        revoked_by="admin-2",
        revoke_reason="incident-42",
        audit_metadata={"ticket": "SEC-42"},
    )

    decision = grant.authorize(
        app_id="billing",
        app_version_id="version-1",
        tenant_id="tenant-1",
        scope=GrantScope(kind=GrantScopeKind.PROJECT, id="project-1"),
        operation="read",
        resource="invoices",
        domain="storage",
        now=NOW + timedelta(minutes=5),
    )

    assert decision.reason is GrantDenyReason.REVOKED
    assert grant.audit_metadata["ticket"] == "SEC-42"


def test_revoke_metadata_requires_a_revocation_timestamp_and_actor() -> None:
    with pytest.raises(ValidationError, match="revoke metadata"):
        make_grant(revoke_reason="manual revoke")

    with pytest.raises(ValidationError, match="revoked_by"):
        make_grant(revoked_at=NOW + timedelta(minutes=1))
