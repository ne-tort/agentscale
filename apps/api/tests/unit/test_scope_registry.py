"""M04 typed scope registry acceptance tests (intentionally not executed in migration freeze)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    RlsContext,
    ScopeAnchors,
    ScopeAssertion,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from prodavan.application.scope_registry.service import ScopeRegistryService


class FakeRegistry:
    def __init__(self, entry=None, proof=None):
        self.entry = entry
        self.proof = proof or ParentCompatibilityProof(tenant_exists=True, tenant_retained=True)
        self.quarantined = []
        self.contexts = []
        self.writes = []

    async def get_scope(self, anchors):
        return self.entry

    async def prove(self, anchors):
        return self.proof

    async def record(self, record):
        self.quarantined.append(record)

    async def set_transaction_context(self, context):
        self.contexts.append(context)

    async def clear_transaction_context(self):
        self.contexts.clear()

    async def put_scope(self, entry):
        self.writes.append(entry)
        return entry


def anchors(kind=ScopeKind.TENANT):
    values = {"scope_kind": kind, "scope_id": "t-1", "tenant_id": "t-1"}
    if kind is ScopeKind.CABINET:
        values.update(scope_id="c-1", cabinet_id="c-1")
    elif kind is ScopeKind.PROJECT:
        values.update(scope_id="p-1", cabinet_id="c-1", project_id="p-1")
    elif kind is ScopeKind.PLUGIN_INSTANCE:
        values.update(scope_id="i-1", cabinet_id="c-1", project_id="p-1", plugin_instance_id="i-1")
    return ScopeAnchors(**values)


def entry(scope):
    return ScopeRegistryEntry(
        anchors=scope,
        owner_state=OwnerState.ACTIVE,
        provenance=ScopeProvenance(
            source_kind="fixture", source_id=scope.scope_id, schema_version="m04.v1", payload_digest="abc"
        ),
        observed_at=datetime.now(UTC),
    )


def context(scope=None):
    return RlsContext(
        tenant_id="t-1",
        cabinet_id=scope.cabinet_id if scope else None,
        project_id=scope.project_id if scope else None,
        user_id="u-1",
        role="admin",
    )


def test_typed_shapes_reject_impossible_parent_combinations():
    with pytest.raises(ValueError):
        ScopeAnchors(scope_kind="tenant", scope_id="t-1", tenant_id="t-1", cabinet_id="c-1")
    with pytest.raises(ValueError):
        ScopeAnchors(scope_kind="project", scope_id="p-1", tenant_id="t-1", project_id="p-1")
    with pytest.raises(ValueError):
        ScopeAnchors(scope_kind="plugin_instance", scope_id="i-1", tenant_id="t-1", plugin_instance_id="i-2")
    with pytest.raises(ValueError):
        ScopeAnchors(
            scope_kind="plugin_instance",
            scope_id="i-1",
            tenant_id="t-1",
            plugin_instance_id="i-1",
            project_id="p-1",
        )


def test_typed_ids_are_bounded_to_string_40():
    identifier = "x" * 41
    with pytest.raises(ValueError):
        ScopeAnchors(scope_kind="tenant", scope_id=identifier, tenant_id=identifier)
    with pytest.raises(ValueError):
        RlsContext(tenant_id=identifier, user_id="u-1", role="admin")
    with pytest.raises(ValueError):
        ScopeAssertion(scope_id="")


@pytest.mark.asyncio
async def test_positive_tenant_write_requires_active_registry_and_rls():
    scope = anchors()
    adapter = FakeRegistry(entry(scope))
    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=context(scope))
    assert decision.allowed is True
    assert adapter.quarantined == []

    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=None)
    assert decision.reason_code == "missing_rls_context"
    assert adapter.quarantined[-1].reason_code == "missing_rls_context"


@pytest.mark.asyncio
async def test_archived_scope_is_denied_and_quarantined():
    scope = anchors()
    archived = entry(scope).model_copy(update={"owner_state": OwnerState.ARCHIVED})
    adapter = FakeRegistry(archived)
    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=context(scope))
    assert decision.allowed is False
    assert decision.reason_code == "scope_archived"
    assert adapter.quarantined[-1].scope_id == "t-1"


@pytest.mark.asyncio
async def test_registry_entry_anchor_mismatch_is_denied_and_quarantined():
    requested = anchors()
    adapter = FakeRegistry(entry(anchors(ScopeKind.CABINET)))
    decision = await ScopeRegistryService(adapter).authorize_positive_write(
        anchors=requested, context=context(requested)
    )
    assert decision.allowed is False
    assert decision.reason_code == "scope_registry_mismatch"
    assert adapter.quarantined[-1].reason_code == "scope_registry_mismatch"


@pytest.mark.asyncio
async def test_quarantined_scope_is_denied_and_quarantined():
    scope = anchors()
    quarantined = entry(scope).model_copy(update={"owner_state": OwnerState.QUARANTINED})
    adapter = FakeRegistry(quarantined)
    decision = await ScopeRegistryService(adapter).authorize_positive_write(
        anchors=scope, context=context(scope)
    )
    assert decision.allowed is False
    assert decision.reason_code == "scope_quarantined"
    assert adapter.quarantined[-1].reason_code == "scope_quarantined"


@pytest.mark.asyncio
async def test_project_requires_parent_compatibility_proof():
    scope = anchors(ScopeKind.PROJECT)
    adapter = FakeRegistry(
        entry(scope),
        ParentCompatibilityProof(
            tenant_exists=True,
            tenant_retained=True,
            cabinet_exists=True,
            cabinet_tenant_compatible=True,
            cabinet_membership_active=True,
            project_exists=True,
            project_tenant_compatible=True,
            project_cabinet_compatible=False,
            project_policy_eligible=True,
        ),
    )
    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=context(scope))
    assert decision.reason_code == "project_parent_invalid"


@pytest.mark.asyncio
async def test_client_assertions_are_comparison_only():
    scope = anchors(ScopeKind.CABINET)
    adapter = FakeRegistry(entry(scope), ParentCompatibilityProof(
        tenant_exists=True, tenant_retained=True, cabinet_exists=True,
        cabinet_tenant_compatible=True, cabinet_membership_active=True,
    ))
    assertion = ScopeAssertion(tenant_id="other-tenant")
    decision = await ScopeRegistryService(adapter).authorize_positive_write(
        anchors=scope, context=context(scope), assertion=assertion
    )
    assert decision.reason_code == "assertion_tenant_id_mismatch"


@pytest.mark.asyncio
async def test_plugin_instance_requires_exact_registry_anchor_until_m05_relation_exists():
    scope = anchors(ScopeKind.PLUGIN_INSTANCE)
    adapter = FakeRegistry()
    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=context(scope))
    assert decision.reason_code == "plugin_instance_deferred_m05"
    assert adapter.quarantined[-1].reason_code == "plugin_instance_deferred_m05"


@pytest.mark.asyncio
async def test_plugin_instance_exact_anchor_checks_parent_chain_and_allows_write():
    scope = anchors(ScopeKind.PLUGIN_INSTANCE)
    adapter = FakeRegistry(
        entry(scope),
        ParentCompatibilityProof(
            tenant_exists=True,
            tenant_retained=True,
            cabinet_exists=True,
            cabinet_tenant_compatible=True,
            cabinet_membership_active=True,
            project_exists=True,
            project_tenant_compatible=True,
            project_cabinet_compatible=True,
            project_policy_eligible=True,
        ),
    )
    decision = await ScopeRegistryService(adapter).authorize_positive_write(anchors=scope, context=context(scope))
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_plugin_instance_registration_requires_parent_proof():
    scope = anchors(ScopeKind.PLUGIN_INSTANCE)
    adapter = FakeRegistry(entry(scope))
    decision = await ScopeRegistryService(adapter).register_active_scope(
        entry=entry(scope), context=context(scope)
    )
    assert decision.allowed is False
    assert decision.reason_code == "parent_compatibility_invalid"
    assert adapter.writes == []
