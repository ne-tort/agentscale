"""Source tests for the T01 typed owner-anchor boundary.

These tests are authored during migration verification freeze and are not run here.
"""

from prodavan.application.owner_anchor_registry.contracts import (
    LifecycleStatus,
    OwnerAnchorRecord,
    OwnerAnchorReason,
    OwnerKey,
    OwnerState,
    ParentCompatibility,
    RlsContext,
    ScopeKind,
)
from prodavan.application.owner_anchor_registry.service import OwnerAnchorRegistryService


class FakePort:
    def __init__(self, context, records, proof):
        self.context = context
        self.records = records
        self.proof = proof

    async def get_server_context(self):
        return self.context

    async def find_by_key(self, key):
        return self.records

    async def prove(self, key):
        return self.proof


def _key(kind=ScopeKind.PROJECT):
    if kind is ScopeKind.TENANT:
        return OwnerKey(kind, "tenant-1", "tenant-1")
    if kind is ScopeKind.CABINET:
        return OwnerKey(kind, "cabinet-1", "tenant-1", cabinet_id="cabinet-1")
    if kind is ScopeKind.PLUGIN_INSTANCE:
        return OwnerKey(kind, "plugin-1", "tenant-1", plugin_instance_id="plugin-1")
    return OwnerKey(kind, "project-1", "tenant-1", cabinet_id="cabinet-1", project_id="project-1")


def _proof(*, project=True, cabinet=True):
    return ParentCompatibility(
        company_exists=True,
        company_status=LifecycleStatus.ACTIVE,
        company_deleted=False,
        company_id="tenant-1",
        cabinet_exists=cabinet,
        cabinet_status=LifecycleStatus.ACTIVE if cabinet else None,
        cabinet_id="cabinet-1" if cabinet else None,
        cabinet_tenant_matches=cabinet,
        cabinet_membership_active=cabinet,
        project_exists=project,
        project_status=LifecycleStatus.ACTIVE if project else None,
        project_id="project-1" if project else None,
        project_tenant_matches=project,
        project_cabinet_matches=project,
    )


def test_owner_key_rejects_overlength_without_truncation():
    try:
        OwnerKey(ScopeKind.CABINET, "x" * 41, "tenant-1")
    except ValueError as exc:
        assert "String(40)" in str(exc)
    else:
        raise AssertionError("overlength owner key must be rejected")


async def test_resolve_allows_exact_active_project_with_server_rls():
    key = _key()
    record = OwnerAnchorRecord(key, OwnerState.ACTIVE, LifecycleStatus.ACTIVE)
    service = OwnerAnchorRegistryService(FakePort(RlsContext("tenant-1", project_id="project-1", cabinet_id="cabinet-1"), (record,), _proof()))
    result = await service.resolve(key)
    assert result.allowed is True
    assert result.reason is OwnerAnchorReason.ALLOWED


async def test_duplicate_owner_key_is_quarantined_decision():
    key = _key(ScopeKind.CABINET)
    record = OwnerAnchorRecord(key, OwnerState.ACTIVE, LifecycleStatus.ACTIVE)
    service = OwnerAnchorRegistryService(FakePort(RlsContext("tenant-1", cabinet_id="cabinet-1"), (record, record), _proof(project=False)))
    result = await service.resolve(key)
    assert result.allowed is False
    assert result.reason is OwnerAnchorReason.DUPLICATE_OWNER


async def test_archived_and_quarantined_states_never_authorize():
    key = _key(ScopeKind.CABINET)
    for state, expected in ((OwnerState.ARCHIVED, OwnerAnchorReason.OWNER_ARCHIVED), (OwnerState.QUARANTINED, OwnerAnchorReason.OWNER_QUARANTINED)):
        record = OwnerAnchorRecord(key, state, LifecycleStatus.ACTIVE)
        service = OwnerAnchorRegistryService(FakePort(RlsContext("tenant-1", cabinet_id="cabinet-1"), (record,), _proof(project=False)))
        result = await service.resolve(key)
        assert result.reason is expected


async def test_project_parent_mismatch_is_denied():
    key = _key()
    record = OwnerAnchorRecord(key, OwnerState.ACTIVE, LifecycleStatus.ACTIVE)
    service = OwnerAnchorRegistryService(FakePort(RlsContext("tenant-1", project_id="project-1", cabinet_id="cabinet-1"), (record,), _proof(project=False)))
    result = await service.resolve(key)
    assert result.reason is OwnerAnchorReason.PROJECT_NOT_COMPATIBLE


def test_owner_key_rejects_impossible_typed_shapes():
    for kwargs in (
        {"scope_kind": ScopeKind.TENANT, "scope_id": "tenant-1", "tenant_id": "tenant-1", "cabinet_id": "cabinet-1"},
        {"scope_kind": ScopeKind.PROJECT, "scope_id": "project-1", "tenant_id": "tenant-1", "project_id": "other-project", "cabinet_id": "cabinet-1"},
        {"scope_kind": ScopeKind.PLUGIN_INSTANCE, "scope_id": "plugin-1", "tenant_id": "tenant-1", "plugin_instance_id": "other-plugin"},
    ):
        try:
            OwnerKey(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid typed owner shape must be rejected")


async def test_plugin_instance_requires_exact_server_scope_and_parent_proof():
    key = _key(ScopeKind.PLUGIN_INSTANCE)
    record = OwnerAnchorRecord(key, OwnerState.ACTIVE, LifecycleStatus.ACTIVE)
    proof = ParentCompatibility(
        company_exists=True,
        company_status=LifecycleStatus.ACTIVE,
        company_deleted=False,
        company_id="tenant-1",
        plugin_instance_exists=True,
        plugin_instance_id="plugin-1",
    )
    service = OwnerAnchorRegistryService(FakePort(RlsContext("tenant-1", plugin_instance_id="plugin-1"), (record,), proof))
    result = await service.resolve(key)
    assert result.allowed is True


async def test_missing_or_mismatched_server_rls_denies_before_lookup():
    key = _key(ScopeKind.CABINET)
    record = OwnerAnchorRecord(key, OwnerState.ACTIVE, LifecycleStatus.ACTIVE)
    for context, reason in ((None, OwnerAnchorReason.MISSING_RLS_CONTEXT), (RlsContext("other"), OwnerAnchorReason.RLS_CONTEXT_MISMATCH)):
        service = OwnerAnchorRegistryService(FakePort(context, (record,), _proof(project=False)))
        result = await service.resolve(key)
        assert result.reason is reason
