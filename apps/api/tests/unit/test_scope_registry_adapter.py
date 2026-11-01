"""M04 scope-registry adapter source tests (not executed during migration freeze)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    RlsContext,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from prodavan.application.scope_registry_adapter import ScopeRegistryServiceAdapter


class TrustedRepository:
    def __init__(self, entry=None, proof=None):
        self.entry = entry
        self.proof = proof or ParentCompatibilityProof(tenant_exists=True, tenant_retained=True)
        self.contexts = []
        self.quarantine = []
        self.writes = []

    async def get_scope(self, anchors):
        return self.entry

    async def put_scope(self, entry):
        self.writes.append(entry)
        return entry

    async def prove(self, anchors):
        return self.proof

    async def record(self, record):
        self.quarantine.append(record)

    async def set_transaction_context(self, context):
        self.contexts.append(context)

    async def clear_transaction_context(self):
        self.contexts.clear()


def anchors(kind: ScopeKind) -> ScopeAnchors:
    values = {"scope_kind": kind, "scope_id": "co-1", "tenant_id": "co-1"}
    if kind is ScopeKind.CABINET:
        values.update(scope_id="cab-1", cabinet_id="cab-1")
    elif kind is ScopeKind.PROJECT:
        values.update(scope_id="prj-1", cabinet_id="cab-1", project_id="prj-1")
    elif kind is ScopeKind.PLUGIN_INSTANCE:
        values.update(scope_id="plugin-1", plugin_instance_id="plugin-1")
    return ScopeAnchors(**values)


def entry(scope: ScopeAnchors) -> ScopeRegistryEntry:
    return ScopeRegistryEntry(
        anchors=scope,
        owner_state=OwnerState.ACTIVE,
        provenance=ScopeProvenance(
            source_kind="fixture",
            source_id=scope.scope_id,
            schema_version="m04.v1",
            payload_digest="digest",
        ),
        observed_at=datetime.now(UTC),
    )


def proof(scope: ScopeAnchors) -> ParentCompatibilityProof:
    if scope.scope_kind is ScopeKind.TENANT:
        return ParentCompatibilityProof(tenant_exists=True, tenant_retained=True)
    if scope.scope_kind is ScopeKind.CABINET:
        return ParentCompatibilityProof(
            tenant_exists=True,
            tenant_retained=True,
            cabinet_exists=True,
            cabinet_tenant_compatible=True,
            cabinet_membership_active=True,
        )
    return ParentCompatibilityProof(
        tenant_exists=True,
        tenant_retained=True,
        cabinet_exists=True,
        cabinet_tenant_compatible=True,
        cabinet_membership_active=True,
        project_exists=True,
        project_tenant_compatible=True,
        project_cabinet_compatible=True,
        project_policy_eligible=True,
    )


def context(scope: ScopeAnchors) -> RlsContext:
    return RlsContext(
        tenant_id=scope.tenant_id,
        cabinet_id=scope.cabinet_id,
        project_id=scope.project_id,
        user_id="user-1",
        role="admin",
    )


@pytest.mark.asyncio
async def test_adapter_sets_and_clears_transaction_local_rls_context():
    repository = TrustedRepository()
    adapter = ScopeRegistryServiceAdapter(repository)
    value = RlsContext(tenant_id="co-1", user_id="user-1", role="admin")

    await adapter.set_rls_context(value)
    assert repository.contexts == [value]
    await adapter.clear_rls_context()
    assert repository.contexts == []


@pytest.mark.asyncio
async def test_adapter_rls_context_clears_on_exception():
    repository = TrustedRepository()
    adapter = ScopeRegistryServiceAdapter(repository)
    value = RlsContext(tenant_id="co-1", user_id="user-1", role="admin")

    with pytest.raises(RuntimeError, match="boom"):
        async with adapter.rls_context(value):
            assert repository.contexts == [value]
            raise RuntimeError("boom")

    assert repository.contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [OwnerState.ARCHIVED, OwnerState.QUARANTINED])
async def test_resolve_active_hides_non_active_registry_evidence(state):
    scope = anchors(ScopeKind.TENANT)
    inactive = entry(scope).model_copy(update={"owner_state": state})
    repository = TrustedRepository(inactive)

    assert await ScopeRegistryServiceAdapter(repository).resolve_active(scope) is None


@pytest.mark.asyncio
async def test_register_active_scope_persists_only_after_parent_proof():
    scope = anchors(ScopeKind.PROJECT)
    candidate = entry(scope)
    repository = TrustedRepository(proof=ParentCompatibilityProof(tenant_exists=True, tenant_retained=True))

    decision = await ScopeRegistryServiceAdapter(repository).register_active_scope(
        entry=candidate,
        context=context(scope),
    )

    assert decision.allowed is False
    assert decision.reason_code == "cabinet_membership_invalid"
    assert repository.writes == []
    assert repository.quarantine[-1].reason_code == "cabinet_membership_invalid"


@pytest.mark.asyncio
async def test_register_active_scope_writes_validated_entry():
    scope = anchors(ScopeKind.CABINET)
    candidate = entry(scope)
    repository = TrustedRepository(proof=proof(scope))

    decision = await ScopeRegistryServiceAdapter(repository).register_active_scope(
        entry=candidate,
        context=context(scope),
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    assert repository.writes == [candidate]


@pytest.mark.asyncio
async def test_cross_tenant_rls_context_is_quarantined_without_write():
    scope = anchors(ScopeKind.PROJECT)
    repository = TrustedRepository(entry(scope), proof(scope))

    decision = await ScopeRegistryServiceAdapter(repository).authorize_positive_write(
        anchors=scope,
        context=RlsContext(tenant_id="other", user_id="user-1", role="admin"),
    )

    assert decision.allowed is False
    assert decision.reason_code == "rls_tenant_mismatch"
    assert repository.quarantine[-1].reason_code == "rls_tenant_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ScopeKind.TENANT, ScopeKind.CABINET, ScopeKind.PROJECT])
async def test_company_cabinet_project_anchors_use_trusted_repository(kind):
    scope = anchors(kind)
    repository = TrustedRepository(entry(scope), proof(scope))
    decision = await ScopeRegistryServiceAdapter(repository).authorize_positive_write(
        anchors=scope,
        context=context(scope),
    )

    assert decision.allowed is True
    assert decision.entry == repository.entry
    assert repository.quarantine == []


@pytest.mark.asyncio
async def test_plugin_anchor_is_deferred_and_quarantined_without_write():
    scope = anchors(ScopeKind.PLUGIN_INSTANCE)
    repository = TrustedRepository(entry(scope))
    decision = await ScopeRegistryServiceAdapter(repository).register_active_scope(
        entry=repository.entry,
        context=context(scope),
    )

    assert decision.allowed is False
    assert decision.reason_code == "plugin_instance_deferred_m05"
    assert repository.writes == []
    assert repository.quarantine[-1].scope_kind is ScopeKind.PLUGIN_INSTANCE


@pytest.mark.asyncio
async def test_adapter_preserves_fail_closed_missing_rls_quarantine():
    scope = anchors(ScopeKind.PROJECT)
    repository = TrustedRepository(entry(scope), proof(scope))
    decision = await ScopeRegistryServiceAdapter(repository).authorize_positive_write(
        anchors=scope,
        context=None,
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_rls_context"
    assert repository.quarantine[-1].reason_code == "missing_rls_context"
