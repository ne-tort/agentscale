"""Fail-closed service for the M04 typed scope registry boundary."""

from __future__ import annotations

from .contracts import (
    OwnerState,
    ParentCompatibilityProof,
    PositiveWriteDecision,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeAssertion,
    ScopeKind,
    ScopeRegistryEntry,
)
from .ports import ScopeRegistryPort


class ScopeRegistryService:
    """Resolve and gate typed scopes without depending on existing ORM rows."""

    def __init__(self, port: ScopeRegistryPort) -> None:
        self._port = port

    async def set_rls_context(self, context: RlsContext) -> None:
        """Set server-resolved transaction-local context; never accept client authority."""
        await self._port.set_transaction_context(context)

    async def clear_rls_context(self) -> None:
        await self._port.clear_transaction_context()

    async def resolve_active(self, anchors: ScopeAnchors) -> ScopeRegistryEntry | None:
        """Return an active scope only; archived/quarantined rows are not auth evidence."""
        entry = await self._port.get_scope(anchors)
        if entry is None or entry.owner_state is not OwnerState.ACTIVE:
            return None
        return entry

    async def authorize_positive_write(
        self,
        *,
        anchors: ScopeAnchors,
        context: RlsContext | None,
        assertion: ScopeAssertion | None = None,
    ) -> PositiveWriteDecision:
        """Apply all M04 gates before a canonical tenant-owned positive write.

        Plugin-instance scopes remain deferred only while M05 has not exposed an
        exact installation registry anchor.  Once found, parent anchors are still
        checked server-side before allowing the write.
        """
        entry = await self._port.get_scope(anchors)
        reason: str | None = None
        proof: ParentCompatibilityProof | None = None

        if context is None:
            reason = "missing_rls_context"
        elif context.tenant_id != anchors.tenant_id:
            reason = "rls_tenant_mismatch"
        elif context.cabinet_id != anchors.cabinet_id:
            reason = "rls_cabinet_mismatch"
        elif context.project_id != anchors.project_id:
            reason = "rls_project_mismatch"
        elif assertion is not None:
            reason = self._assertion_mismatch(assertion, anchors)
        if reason is None and entry is None:
            reason = (
                "plugin_instance_deferred_m05"
                if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE
                else "scope_not_registered"
            )
        elif reason is None and entry is not None and entry.anchors != anchors:
            reason = "scope_registry_mismatch"
        elif reason is None and entry is not None and entry.owner_state is not OwnerState.ACTIVE:
            reason = f"scope_{entry.owner_state.value}"
        if reason is None:
            proof = await self._port.prove(anchors)
            valid = (
                proof.valid_for_plugin(anchors)
                if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE
                else proof.valid_for_scope(anchors.scope_kind)
            )
            if not valid:
                reason = self._parent_reason(anchors.scope_kind, proof)

        if reason is not None:
            quarantine = self._quarantine_for(anchors, reason, entry)
            await self._port.record(quarantine)
            return PositiveWriteDecision(
                allowed=False,
                reason_code=reason,
                entry=entry,
                quarantine_reason=reason,
                details={"parent_proof": proof.model_dump() if proof else None},
            )
        return PositiveWriteDecision(allowed=True, reason_code="allowed", entry=entry)

    async def register_active_scope(
        self,
        *,
        entry: ScopeRegistryEntry,
        context: RlsContext | None,
        assertion: ScopeAssertion | None = None,
    ) -> PositiveWriteDecision:
        """Validate and persist one active registry row through the trusted port."""
        if entry.owner_state is not OwnerState.ACTIVE:
            reason = f"scope_{entry.owner_state.value}"
            await self._port.record(self._quarantine_for(entry.anchors, reason, entry))
            return PositiveWriteDecision(
                allowed=False,
                reason_code=reason,
                entry=entry,
                quarantine_reason=reason,
            )
        if entry.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            # M04 never creates a plugin-instance registry row: the cyclic
            # registry<->instance relation is staged by M05, so a self-issued
            # plugin anchor would become an authorization anchor nobody proved.
            # RLS and parent evidence are evaluated first, so a broken context or
            # chain is reported precisely instead of being masked as deferral.
            deferred_reason: str | None = "plugin_instance_deferred_m05"
        else:
            deferred_reason = None
        if context is None:
            reason = "missing_rls_context"
        elif context.tenant_id != entry.anchors.tenant_id:
            reason = "rls_tenant_mismatch"
        elif context.cabinet_id != entry.anchors.cabinet_id:
            reason = "rls_cabinet_mismatch"
        elif context.project_id != entry.anchors.project_id:
            reason = "rls_project_mismatch"
        elif assertion is not None:
            reason = self._assertion_mismatch(assertion, entry.anchors)
        else:
            reason = None
        proof = await self._port.prove(entry.anchors) if reason is None else None
        if reason is None:
            valid = (
                proof.valid_for_plugin(entry.anchors)
                if entry.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE
                else proof.valid_for_scope(entry.anchors.scope_kind)
            )
            if not valid:
                reason = self._parent_reason(entry.anchors.scope_kind, proof)
        if reason is None:
            reason = deferred_reason
        if reason is not None:
            await self._port.record(self._quarantine_for(entry.anchors, reason, entry))
            return PositiveWriteDecision(
                allowed=False,
                reason_code=reason,
                entry=entry,
                quarantine_reason=reason,
                details={"parent_proof": proof.model_dump() if proof else None},
            )
        stored = await self._port.put_scope(entry)
        return PositiveWriteDecision(allowed=True, reason_code="allowed", entry=stored)

    @staticmethod
    def _assertion_mismatch(assertion: ScopeAssertion, anchors: ScopeAnchors) -> str | None:
        expected = {
            "tenant_id": anchors.tenant_id,
            "cabinet_id": anchors.cabinet_id,
            "project_id": anchors.project_id,
            "scope_kind": anchors.scope_kind,
            "scope_id": anchors.scope_id,
        }
        for key, value in assertion.model_dump().items():
            if value is not None and value != expected[key]:
                return f"assertion_{key}_mismatch"
        return None

    @staticmethod
    def _parent_reason(kind: ScopeKind, proof: ParentCompatibilityProof) -> str:
        if not proof.tenant_exists or not proof.tenant_retained:
            return "tenant_parent_invalid"
        if kind in (ScopeKind.CABINET, ScopeKind.PROJECT) and not proof.cabinet_membership_active:
            return "cabinet_membership_invalid"
        if kind is ScopeKind.PROJECT and not (
            proof.project_exists
            and proof.project_tenant_compatible
            and proof.project_cabinet_compatible
            and proof.project_policy_eligible
        ):
            # Every project gate (existence, tenant/cabinet compatibility, policy
            # eligibility) is project-parent evidence; a missing or incompatible
            # project parent must not degrade into a generic parent reason.
            return "project_parent_invalid"
        return "parent_compatibility_invalid"

    @staticmethod
    def _quarantine_for(
        anchors: ScopeAnchors, reason: str, entry: ScopeRegistryEntry | None
    ) -> QuarantineRecord:
        provenance = entry.provenance if entry else None
        return QuarantineRecord(
            source_kind=provenance.source_kind if provenance else "application",
            source_id=provenance.source_id if provenance else anchors.scope_id,
            schema_version=provenance.schema_version if provenance else "m04.v1",
            payload_digest=provenance.payload_digest if provenance else "unresolved",
            reason_code=reason,
            tenant_id=anchors.tenant_id,
            scope_kind=anchors.scope_kind,
            scope_id=anchors.scope_id,
        )
