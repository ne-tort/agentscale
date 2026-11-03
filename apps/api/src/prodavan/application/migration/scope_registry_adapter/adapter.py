"""Fail-closed M04 typed scope registry application adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from prodavan.application.migration.m04_scope.scope_migration import (
    MigrationEvidenceContext,
    ResolveScopePort,
    ScopeMigrationCoordinator,
    ScopeParentEvidence,
    ScopeResolution,
    ScopeResolver,
)
from prodavan.application.scope_registry.contracts import (
    ParentCompatibilityProof,
    QuarantineRecord,
    RlsContext,
    ScopeAnchors,
    ScopeKind,
    ScopeRegistryEntry,
)
from prodavan.application.scope_registry.service import ScopeRegistryService

from .ports import (
    ScopeRegistryEvidenceIds,
    ScopeRegistryGateEvidence,
    ScopeRegistryRead,
    ScopeRegistryTransactionPort,
    ScopeRegistryTransactionManagerPort,
    TransactionLocalRlsClearReceipt,
    TransactionLocalRlsReceipt,
)


class ScopeAdapterOutcome(StrEnum):
    ACTIVE = "active"
    DENY = "deny"
    QUARANTINED = "quarantined"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    SYSTEM_FAILURE = "system_failure"


@dataclass(frozen=True, slots=True)
class ScopeAdapterResult:
    outcome: ScopeAdapterOutcome
    reason_code: str
    correlation_id: str
    tenant_id: str | None
    transaction_id: str | None = None
    entry: ScopeRegistryEntry | None = None
    evidence_ids: ScopeRegistryEvidenceIds = field(
        default_factory=lambda: ScopeRegistryEvidenceIds(None, None, None, None, None, None, ())
    )
    gate_evidence_ids: tuple[tuple[str, str], ...] = ()
    canonical_positive_writes: bool = False
    source_snapshot_digest: str | None = None
    partition_canonical_digest: str | None = None
    checkpoint_id: str | None = None
    source_snapshot_id: str | None = None
    source_schema_version: str | None = None
    canonical_schema_version: str | None = None
    source_payload_digest: str | None = None
    resolved_scope_digest: str | None = None


class _AdapterBlocked(Exception):
    """An explicit missing/contradictory trusted adapter precondition."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ScopeRegistryApplicationAdapter:
    """Read a typed scope through M04-02 inside one transaction-local RLS context.

    The adapter never writes registry rows or enables downstream positive writes.
    It consumes explicit M04 gate evidence and delegates active resolution to the
    M04-02 scope service/resolver; missing runtime wiring remains a blocked result.
    """

    def __init__(
        self,
        transactions: ScopeRegistryTransactionManagerPort | None,
    ) -> None:
        self._transactions = transactions

    async def resolve_active(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> ScopeAdapterResult:
        correlation_id = context.correlation_id or "missing-correlation"
        if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            return self._result(
                ScopeAdapterOutcome.DEFERRED,
                "plugin_instance_deferred_m05",
                correlation_id,
                anchors.tenant_id,
            )
        if self._transactions is None:
            return self._result(
                ScopeAdapterOutcome.BLOCKED,
                "scope_registry_transaction_port_missing",
                correlation_id,
                anchors.tenant_id,
            )
        context_reason = self._context_reason(context, anchors)
        if context_reason is not None:
            return self._result(
                ScopeAdapterOutcome.BLOCKED,
                context_reason,
                correlation_id,
                anchors.tenant_id,
            )

        transaction_id = context.transaction_id or ""
        result = self._result(
            ScopeAdapterOutcome.BLOCKED,
            "scope_resolution_not_completed",
            correlation_id,
            anchors.tenant_id,
            transaction_id=transaction_id,
        )
        try:
            async with self._transactions.transaction(
                expected_transaction_id=transaction_id
            ) as transaction:
                if transaction.transaction_id != transaction_id:
                    return self._result(
                        ScopeAdapterOutcome.BLOCKED,
                        "transaction_identity_mismatch",
                        correlation_id,
                        anchors.tenant_id,
                        transaction_id=transaction.transaction_id,
                    )
                bridge = _TransactionBoundScopePort(transaction)
                service = ScopeRegistryService(bridge)
                context_attempted = False
                try:
                    context_attempted = True
                    await service.set_rls_context(context.rls_context)  # type: ignore[arg-type]
                    gate = await transaction.migration_gates.read_gate(
                        tenant_id=anchors.tenant_id
                    )
                    gate_reason = self._gate_reason(gate, anchors.tenant_id)
                    if gate_reason is not None:
                        result = self._result(
                            ScopeAdapterOutcome.BLOCKED,
                            gate_reason,
                            correlation_id,
                            anchors.tenant_id,
                            transaction_id=transaction.transaction_id,
                            gate_evidence_ids=self._gate_ids(gate),
                        )
                    else:
                        assert gate is not None
                        bridge.gate = gate
                        await bridge.load(anchors=anchors, transaction_id=transaction_id)
                        # Bind the existing MIG-M04-02 application service to the same
                        # transaction snapshot; it cannot write or clear arbitrary rows.
                        active_entry = await service.resolve_active(anchors)
                        if active_entry is None:
                            result = self._result(
                                ScopeAdapterOutcome.DENY,
                                "scope_missing_or_not_active",
                                correlation_id,
                                anchors.tenant_id,
                                transaction_id=transaction.transaction_id,
                                evidence_ids=bridge.evidence_ids,
                                gate_evidence_ids=self._gate_ids(gate),
                            )
                        else:
                            resolution = await ScopeResolver(bridge).resolve(
                                context=context,
                                anchors=anchors,
                            )
                            evidence_ids = bridge.evidence_ids
                            if resolution.outcome is ScopeResolution.ACTIVE:
                                missing_ids = self._missing_evidence_ids(
                                    anchors, evidence_ids
                                )
                                if missing_ids:
                                    result = self._result(
                                        ScopeAdapterOutcome.BLOCKED,
                                        "scope_evidence_ids_incomplete",
                                        correlation_id,
                                        anchors.tenant_id,
                                        transaction_id=transaction.transaction_id,
                                        evidence_ids=evidence_ids,
                                        gate_evidence_ids=self._gate_ids(gate),
                                    )
                                else:
                                    result = self._result(
                                        ScopeAdapterOutcome.ACTIVE,
                                        resolution.reason_code,
                                        correlation_id,
                                        anchors.tenant_id,
                                        transaction_id=transaction.transaction_id,
                                        entry=resolution.entry,
                                        evidence_ids=evidence_ids,
                                        gate_evidence_ids=self._gate_ids(gate),
                                        gate=gate,
                                        canonical_schema_version=bridge.canonical_schema_version,
                                    )
                            else:
                                result = self._result(
                                    self._map_outcome(resolution.outcome),
                                    resolution.reason_code,
                                    correlation_id,
                                    anchors.tenant_id,
                                    transaction_id=transaction.transaction_id,
                                    evidence_ids=evidence_ids,
                                    gate_evidence_ids=self._gate_ids(gate),
                                )
                except _AdapterBlocked as blocked:
                    result = self._result(
                        ScopeAdapterOutcome.BLOCKED,
                        blocked.reason_code,
                        correlation_id,
                        anchors.tenant_id,
                        transaction_id=transaction.transaction_id,
                        evidence_ids=bridge.evidence_ids,
                        gate_evidence_ids=self._gate_ids(bridge.gate),
                    )
                except Exception:
                    result = self._result(
                        ScopeAdapterOutcome.SYSTEM_FAILURE,
                        "scope_registry_read_failed",
                        correlation_id,
                        anchors.tenant_id,
                        transaction_id=transaction.transaction_id,
                        evidence_ids=bridge.evidence_ids,
                        gate_evidence_ids=self._gate_ids(bridge.gate),
                    )
                finally:
                    cleanup_failed = False
                    if context_attempted:
                        try:
                            await service.clear_rls_context()
                        except Exception:
                            cleanup_failed = True
                    if cleanup_failed:
                        result = self._result(
                            ScopeAdapterOutcome.SYSTEM_FAILURE,
                            "transaction_local_rls_context_clear_unproven",
                            correlation_id,
                            anchors.tenant_id,
                            transaction_id=transaction.transaction_id,
                            evidence_ids=bridge.evidence_ids,
                            gate_evidence_ids=self._gate_ids(bridge.gate),
                        )
        except Exception:
            return self._result(
                ScopeAdapterOutcome.SYSTEM_FAILURE,
                "scope_registry_transaction_failed",
                correlation_id,
                anchors.tenant_id,
                transaction_id=transaction_id,
            )
        return result

    @staticmethod
    def _context_reason(
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> str | None:
        rls = context.rls_context
        if not context.usable or rls is None:
            return "trusted_transaction_context_missing"
        if context.tenant_id != anchors.tenant_id or rls.tenant_id != anchors.tenant_id:
            return "tenant_context_mismatch"
        if rls.cabinet_id != anchors.cabinet_id:
            return "cabinet_context_mismatch"
        if rls.project_id != anchors.project_id:
            return "project_context_mismatch"
        return None

    @staticmethod
    def _gate_reason(
        gate: ScopeRegistryGateEvidence | None,
        tenant_id: str,
    ) -> str | None:
        if gate is None:
            return "m04_scope_gate_evidence_missing"
        if gate.tenant_id != tenant_id:
            return "m04_scope_gate_tenant_mismatch"
        if gate.m04_01_status != "accepted" or gate.m04_02_status != "accepted":
            return "m04_01_or_m04_02_gate_not_accepted"
        if gate.canonical_positive_writes:
            return "upstream_canonical_positive_writer_not_disabled"
        required_ids = (
            gate.m04_01_evidence_id,
            gate.m04_02_evidence_id,
            gate.checkpoint_id,
            gate.source_snapshot_id,
            gate.source_schema_version,
            gate.canonical_schema_version,
            gate.rls_review_evidence_id,
            gate.audit_review_evidence_id,
            gate.decision_record_id,
        )
        if any(not item or not item.strip() for item in required_ids):
            return "m04_scope_gate_evidence_ids_incomplete"
        for digest in (gate.source_snapshot_digest, gate.canonical_scope_digest):
            if digest is None or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                return "m04_scope_gate_digest_invalid"
        return None

    @staticmethod
    def _gate_ids(gate: ScopeRegistryGateEvidence | None) -> tuple[tuple[str, str], ...]:
        if gate is None:
            return ()
        values = (
            ("MIG-M04-01", gate.m04_01_evidence_id),
            ("MIG-M04-02", gate.m04_02_evidence_id),
            ("checkpoint", gate.checkpoint_id),
            ("rls_review", gate.rls_review_evidence_id),
            ("audit_review", gate.audit_review_evidence_id),
            ("decision_record", gate.decision_record_id),
        )
        return tuple((name, value) for name, value in values if value)

    @staticmethod
    def _missing_evidence_ids(
        anchors: ScopeAnchors,
        ids: ScopeRegistryEvidenceIds,
    ) -> bool:
        required = [ids.registry_id, ids.mapping_id, ids.audit_id, ids.tenant_anchor_id]
        if anchors.scope_kind in (ScopeKind.CABINET, ScopeKind.PROJECT):
            required.append(ids.cabinet_anchor_id)
            if len(ids.cabinet_membership_ids) != 1:
                return True
        if anchors.scope_kind is ScopeKind.PROJECT:
            required.append(ids.project_anchor_id)
        return any(not value or not value.strip() for value in required)

    @staticmethod
    def _map_outcome(outcome: ScopeResolution) -> ScopeAdapterOutcome:
        return {
            ScopeResolution.ACTIVE: ScopeAdapterOutcome.ACTIVE,
            ScopeResolution.DENY: ScopeAdapterOutcome.DENY,
            ScopeResolution.QUARANTINED: ScopeAdapterOutcome.QUARANTINED,
            ScopeResolution.DEFERRED: ScopeAdapterOutcome.DEFERRED,
        }[outcome]

    @staticmethod
    def _result(
        outcome: ScopeAdapterOutcome,
        reason_code: str,
        correlation_id: str,
        tenant_id: str | None,
        *,
        transaction_id: str | None = None,
        entry: ScopeRegistryEntry | None = None,
        evidence_ids: ScopeRegistryEvidenceIds | None = None,
        gate_evidence_ids: tuple[tuple[str, str], ...] = (),
        gate: ScopeRegistryGateEvidence | None = None,
        canonical_schema_version: str | None = None,
    ) -> ScopeAdapterResult:
        return ScopeAdapterResult(
            outcome=outcome,
            reason_code=reason_code,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            transaction_id=transaction_id,
            entry=entry,
            evidence_ids=evidence_ids or ScopeRegistryEvidenceIds(None, None, None, None, None, None, ()),
            gate_evidence_ids=gate_evidence_ids,
            canonical_positive_writes=False,
            source_snapshot_digest=(gate.source_snapshot_digest if gate else None),
            partition_canonical_digest=(gate.canonical_scope_digest if gate else None),
            checkpoint_id=(gate.checkpoint_id if gate else None),
            source_snapshot_id=(gate.source_snapshot_id if gate else None),
            source_schema_version=(gate.source_schema_version if gate else None),
            canonical_schema_version=(gate.canonical_schema_version if gate else None),
            source_payload_digest=(entry.provenance.payload_digest if entry else None),
            resolved_scope_digest=(
                ScopeMigrationCoordinator._canonical_digest(entry, canonical_schema_version)
                if entry is not None and canonical_schema_version is not None
                else None
            ),
        )


class _TransactionBoundScopePort(ResolveScopePort):
    """Read-only ports backed by one open transaction and a single evidence snapshot."""

    def __init__(self, transaction: ScopeRegistryTransactionPort) -> None:
        self._transaction = transaction
        self.transaction_id = transaction.transaction_id
        self.gate: ScopeRegistryGateEvidence | None = None
        self._read: ScopeRegistryRead | None = None
        self._rls_context: RlsContext | None = None
        self._rls_receipt: TransactionLocalRlsReceipt | None = None
        self._clear_receipt: TransactionLocalRlsClearReceipt | None = None
        self.canonical_schema_version: str | None = None
        self.evidence_ids = ScopeRegistryEvidenceIds(None, None, None, None, None, None, ())

    async def set_transaction_context(self, context: RlsContext) -> None:
        self._rls_context = context
        receipt = await self._transaction.set_transaction_local_rls_context(context)
        if (
            receipt.transaction_id != self.transaction_id
            or receipt.setting_scope != "transaction_local"
            or receipt.tenant_id != context.tenant_id
            or receipt.cabinet_id != context.cabinet_id
            or receipt.project_id != context.project_id
            or receipt.user_id != context.user_id
            or receipt.role != context.role
        ):
            raise _AdapterBlocked("transaction_local_rls_context_unproven")
        self._rls_receipt = receipt

    async def clear_transaction_context(self) -> None:
        receipt = await self._transaction.clear_transaction_local_rls_context()
        if (
            receipt.transaction_id != self.transaction_id
            or receipt.setting_scope != "transaction_local"
            or not receipt.cleared
        ):
            raise _AdapterBlocked("transaction_local_rls_context_clear_unproven")
        self._clear_receipt = receipt

    async def load(self, *, anchors: ScopeAnchors, transaction_id: str) -> None:
        if self._rls_receipt is None or self._clear_receipt is not None:
            raise _AdapterBlocked("scope_read_without_transaction_local_rls")
        if transaction_id != self.transaction_id:
            raise _AdapterBlocked("scope_read_transaction_mismatch")
        read = await self._transaction.owner_registry.read_scope(
            anchors=anchors,
            transaction_id=transaction_id,
        )
        if read.transaction_id != self.transaction_id:
            raise _AdapterBlocked("owner_registry_evidence_transaction_mismatch")
        if read.match_count != 1:
            if read.match_count > 1:
                raise _AdapterBlocked("duplicate_or_ambiguous_scope_registry_rows")
            self._read = read
            return
        if read.entry is None or read.parent_proof is None or read.parent_evidence is None:
            raise _AdapterBlocked("scope_parent_evidence_incomplete")
        gate = self.gate
        if gate is None:
            raise _AdapterBlocked("m04_scope_gate_evidence_missing")
        if not read.evidence_ids.registry_id or not read.evidence_ids.tenant_anchor_id:
            raise _AdapterBlocked("scope_owner_anchor_evidence_missing")
        if (
            read.mapping_source_kind != read.entry.provenance.source_kind
            or read.mapping_source_id != read.entry.provenance.source_id
            or read.mapping_schema_version != read.entry.provenance.schema_version
            or read.mapping_source_digest != read.entry.provenance.payload_digest
        ):
            raise _AdapterBlocked("scope_mapping_source_identity_mismatch")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", read.entry.provenance.payload_digest) is None:
            raise _AdapterBlocked("scope_provenance_digest_invalid")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", read.mapping_canonical_digest or "") is None:
            raise _AdapterBlocked("scope_mapping_canonical_digest_invalid")
        if read.mapping_checkpoint_id != gate.checkpoint_id:
            raise _AdapterBlocked("scope_mapping_checkpoint_mismatch")
        if read.source_snapshot_id != gate.source_snapshot_id:
            raise _AdapterBlocked("scope_snapshot_identity_mismatch")
        if read.source_snapshot_schema_version != gate.source_schema_version:
            raise _AdapterBlocked("scope_snapshot_schema_mismatch")
        if read.source_snapshot_digest != gate.source_snapshot_digest:
            raise _AdapterBlocked("scope_snapshot_digest_mismatch")
        if read.mapping_canonical_schema_version != gate.canonical_schema_version:
            raise _AdapterBlocked("scope_mapping_canonical_schema_mismatch")
        if read.partition_canonical_digest != gate.canonical_scope_digest:
            raise _AdapterBlocked("scope_partition_canonical_digest_mismatch")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", read.partition_canonical_digest or "") is None:
            raise _AdapterBlocked("scope_partition_digest_invalid")
        if not read.mapping_canonical_schema_version:
            raise _AdapterBlocked("scope_mapping_canonical_schema_missing")
        expected_scope_digest = ScopeMigrationCoordinator._canonical_digest(
            read.entry, read.mapping_canonical_schema_version
        )
        if read.mapping_canonical_digest != expected_scope_digest:
            raise _AdapterBlocked("scope_mapping_digest_mismatch")
        self.canonical_schema_version = read.mapping_canonical_schema_version
        self._read = read
        self.evidence_ids = read.evidence_ids

    async def get_scope(self, anchors: ScopeAnchors) -> ScopeRegistryEntry | None:
        if self._read is None:
            raise _AdapterBlocked("scope_registry_service_read_without_snapshot")
        if self._read.match_count != 1 or self._read.entry is None:
            return None
        if self._read.entry.anchors != anchors:
            return None
        return self._read.entry

    async def prove(self, anchors: ScopeAnchors) -> ParentCompatibilityProof:
        if self._read is None or self._read.parent_proof is None:
            raise _AdapterBlocked("scope_registry_service_proof_without_snapshot")
        if self._read.entry is None or self._read.entry.anchors != anchors:
            raise _AdapterBlocked("scope_registry_service_anchor_mismatch")
        return self._read.parent_proof

    async def resolve_active(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> tuple[ScopeRegistryEntry, ParentCompatibilityProof, ScopeParentEvidence] | None:
        if context.transaction_id != self.transaction_id or self._read is None:
            raise _AdapterBlocked("scope_resolver_transaction_mismatch")
        if self._read.match_count != 1:
            return None
        read = self._read
        if (
            read.entry is None
            or read.parent_proof is None
            or read.parent_evidence is None
            or read.entry.anchors != anchors
        ):
            return None
        return read.entry, read.parent_proof, read.parent_evidence

    async def record(self, record: QuarantineRecord) -> None:
        raise _AdapterBlocked("read_only_scope_adapter_cannot_write_quarantine")

    async def put_scope(self, entry: ScopeRegistryEntry) -> ScopeRegistryEntry:
        raise _AdapterBlocked("read_only_scope_adapter_cannot_write_registry")

    @staticmethod
    def _valid_rls_receipt(
        receipt: TransactionLocalRlsReceipt,
        context: RlsContext,
    ) -> bool:
        return bool(
            receipt.transaction_id
            and receipt.setting_scope == "transaction_local"
            and receipt.tenant_id == context.tenant_id
            and receipt.cabinet_id == context.cabinet_id
            and receipt.project_id == context.project_id
            and receipt.user_id == context.user_id
            and receipt.role == context.role
        )


__all__ = [
    "ScopeAdapterOutcome",
    "ScopeAdapterResult",
    "ScopeRegistryApplicationAdapter",
]
