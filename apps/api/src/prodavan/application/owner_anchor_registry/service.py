"""Fail-closed typed owner-anchor adapter."""

from __future__ import annotations

from .contracts import (
    LifecycleStatus,
    OwnerAnchorDecision,
    OwnerAnchorReason,
    OwnerKey,
    OwnerState,
    ScopeKind,
)
from .ports import TrustedOwnerAnchorPort


class OwnerAnchorRegistryService:
    """Resolve canonical owner keys using only server-side read evidence."""

    def __init__(self, port: TrustedOwnerAnchorPort) -> None:
        self._port = port

    async def resolve(self, key: OwnerKey) -> OwnerAnchorDecision:
        """Return a positive anchor only when identity, parents and RLS agree."""
        context = await self._port.get_server_context()
        if context is None:
            return _denied(OwnerAnchorReason.MISSING_RLS_CONTEXT)
        if not _context_matches(context, key):
            return _denied(OwnerAnchorReason.RLS_CONTEXT_MISMATCH)

        records = await self._port.find_by_key(key)
        if not records:
            return _denied(OwnerAnchorReason.OWNER_NOT_FOUND)
        if len(records) != 1:
            return _denied(OwnerAnchorReason.DUPLICATE_OWNER)
        record = records[0]
        if record.key != key:
            return _denied(OwnerAnchorReason.OWNER_KEY_MISMATCH)
        if record.owner_state is OwnerState.ARCHIVED:
            return _denied(OwnerAnchorReason.OWNER_ARCHIVED)
        if record.owner_state is OwnerState.QUARANTINED:
            return _denied(OwnerAnchorReason.OWNER_QUARANTINED)
        if record.lifecycle_status is not LifecycleStatus.ACTIVE:
            return _denied(_lifecycle_reason(record.lifecycle_status))

        proof = await self._port.prove(key)
        if not proof.valid_for(key):
            return _denied(_parent_reason(key.scope_kind))
        return OwnerAnchorDecision(True, OwnerAnchorReason.ALLOWED, record)


def _context_matches(context: object, key: OwnerKey) -> bool:
    if getattr(context, "tenant_id", None) != key.tenant_id:
        return False
    if key.scope_kind is ScopeKind.TENANT:
        return context.cabinet_id is None and context.project_id is None and context.plugin_instance_id is None
    if key.scope_kind is ScopeKind.CABINET:
        return context.cabinet_id == key.cabinet_id and context.project_id is None and context.plugin_instance_id is None
    if key.scope_kind is ScopeKind.PROJECT:
        return context.project_id == key.project_id and context.cabinet_id == key.cabinet_id and context.plugin_instance_id is None
    return (
        context.plugin_instance_id == key.plugin_instance_id
        and context.cabinet_id == key.cabinet_id
        and context.project_id == key.project_id
    )


def _lifecycle_reason(status: LifecycleStatus) -> OwnerAnchorReason:
    if status is LifecycleStatus.ARCHIVED:
        return OwnerAnchorReason.OWNER_ARCHIVED
    return OwnerAnchorReason.OWNER_QUARANTINED


def _parent_reason(kind: ScopeKind) -> OwnerAnchorReason:
    if kind is ScopeKind.PROJECT:
        return OwnerAnchorReason.PROJECT_NOT_COMPATIBLE
    if kind is ScopeKind.PLUGIN_INSTANCE:
        return OwnerAnchorReason.PLUGIN_INSTANCE_NOT_COMPATIBLE
    return OwnerAnchorReason.CABINET_NOT_COMPATIBLE


def _denied(reason: OwnerAnchorReason) -> OwnerAnchorDecision:
    return OwnerAnchorDecision(False, reason)
