"""Atomic application ports for the equipment decision and export workflow.

This module deliberately contains no database, object-store, legacy-row, or
supplier adapter.  A host must provide the two transaction ports before a
selection or procurement export can be reported as durable.  The port is the
only place allowed to claim that the mutation, immutable audit evidence and
idempotency record committed together.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .domain import (
    DomainScope,
    HumanSelection,
    ProcurementExport,
    _exact_product,
    _exact_sum,
    _stable_digest,
)


WIRING_BLOCKER = "equipment_domain_transaction_adapter_unwired"


def _is_sha256(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 71
            and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:]))


class EquipmentTransactionStatus(StrEnum):
    """Durable adapter outcome; an exception or unknown outcome is no success."""

    BLOCKED = "blocked"
    COMMITTED = "committed"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    COMMIT_UNKNOWN = "commit_unknown"


@dataclass(frozen=True, slots=True)
class SelectionTransaction:
    """One human selection attempt fenced to one source generation and digest."""

    scope: DomainScope
    selection: HumanSelection
    projection_digest: str
    source_revision: str
    source_generation: int
    expected_prior_selection_id: str | None

    def __post_init__(self) -> None:
        if (self.selection.tenant_id, self.selection.owner_id, self.selection.project_id) != (
            self.scope.tenant_id, self.scope.owner_id, self.scope.project_id
        ):
            raise ValueError("selection transaction crosses tenant, owner or project scope")
        if self.selection.projection_digest != self.projection_digest:
            raise ValueError("selection transaction digest does not match its projection fence")
        if not all((self.selection.actor_id, self.selection.selection_id,
                    self.selection.supplier_id, self.selection.source_id,
                    self.selection.source_record_id, self.selection.source_schema_version)):
            raise ValueError("selection transaction provenance is incomplete")
        if not self.source_revision.strip():
            raise ValueError("selection transaction source revision is required")
        if self.source_generation < 1:
            raise ValueError("selection transaction requires a positive source generation")
        if not _is_sha256(self.projection_digest):
            raise ValueError("selection transaction projection digest is invalid")

    @property
    def idempotency_key(self) -> str:
        return _stable_digest({
            "operation": "equipment.selection",
            "scope": self.scope,
            "selection_id": self.selection.selection_id,
        })

    @property
    def payload_digest(self) -> str:
        return _stable_digest({
            "operation": "equipment.selection",
            "scope": self.scope,
            "selection": self.selection,
            "projection_digest": self.projection_digest,
            "source_revision": self.source_revision,
            "source_generation": self.source_generation,
            "expected_prior_selection_id": self.expected_prior_selection_id,
        })


@dataclass(frozen=True, slots=True)
class ExportTransaction:
    """One prepared procurement export append, never a supplier submission."""

    scope: DomainScope
    export: ProcurementExport
    projection_digest: str
    source_revision: str
    source_generation: int
    selection_digest: str
    expected_selection_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.export.tenant_id, self.export.owner_id, self.export.project_id) != (
                self.scope.tenant_id, self.scope.owner_id, self.scope.project_id):
            raise ValueError("export transaction crosses tenant, owner or project scope")
        if self.export.projection_digest != self.projection_digest:
            raise ValueError("export transaction digest does not match its projection fence")
        if not _is_sha256(self.projection_digest) or not _is_sha256(self.selection_digest):
            raise ValueError("export transaction digest evidence is invalid")
        if self.export.selection_digest != self.selection_digest:
            raise ValueError("export transaction selection digest does not match its fence")
        if self.export.source_generation != self.source_generation:
            raise ValueError("export transaction generation does not match its fence")
        if self.export.source_revision != self.source_revision:
            raise ValueError("export transaction revision does not match its fence")
        if self.export.idempotency_key != _stable_digest({
                "tenant_id": self.scope.tenant_id,
                "owner_id": self.scope.owner_id,
                "project_id": self.scope.project_id,
                "export_id": self.export.export_id,
        }):
            raise ValueError("export transaction idempotency key does not match its scoped identity")
        if not self.source_revision.strip() or self.source_generation < 1:
            raise ValueError("export transaction requires source revision and positive generation")
        if not self.export.idempotency_key:
            raise ValueError("export transaction idempotency key is required")
        if tuple(sorted(self.expected_selection_ids)) != tuple(sorted(self.export.selection_ids)):
            raise ValueError("export transaction selection membership is not canonical")
        if (len(self.expected_selection_ids) != len(set(self.expected_selection_ids))
                or len(self.export.lines) != len(self.export.selection_ids)
                or len(self.export.lines) != len(self.export.tender_ids)):
            raise ValueError("export transaction contains duplicate or incomplete selected rows")
        if (tuple(sorted(row.selection_id for row in self.export.lines))
                != tuple(sorted(self.export.selection_ids))
                or tuple(sorted(row.tender_id for row in self.export.lines))
                != tuple(sorted(self.export.tender_ids))):
            raise ValueError("export transaction row membership does not match its selected IDs")
        if any(not all((row.selection_id, row.line_id, row.tender_id, row.candidate_group_id,
                        row.supplier_id, row.source_id, row.source_record_id,
                        row.source_schema_version))
               or row.source_revision != self.source_revision
               or row.source_generation != self.source_generation
               or row.total != _exact_product(row.quantity, row.unit_price)
               for row in self.export.lines):
            raise ValueError("export transaction line provenance or total is inconsistent")
        if len({row.line_id for row in self.export.lines}) != len(self.export.lines):
            raise ValueError("export transaction has more than one selection for a request line")
        totals: dict[str, list] = {}
        for row in self.export.lines:
            totals.setdefault(row.currency, []).append(row.total)
        expected_totals = tuple((currency, _exact_sum(amounts))
                                for currency, amounts in sorted(totals.items()))
        if expected_totals != self.export.currency_totals:
            raise ValueError("export transaction currency totals do not reconcile selected lines")

    @property
    def idempotency_key(self) -> str:
        return self.export.idempotency_key

    @property
    def payload_digest(self) -> str:
        return _stable_digest({
            "operation": "equipment.procurement_export",
            "scope": self.scope,
            "export": self.export,
            "projection_digest": self.projection_digest,
            "source_revision": self.source_revision,
            "source_generation": self.source_generation,
            "selection_digest": self.selection_digest,
            "expected_selection_ids": self.expected_selection_ids,
        })


@dataclass(frozen=True, slots=True)
class EquipmentTransactionResult:
    """Adapter receipt; ``durable`` is an assertion the adapter must earn."""

    status: EquipmentTransactionStatus
    operation_id: str
    idempotency_key: str
    payload_digest: str
    scope: DomainScope
    source_revision: str
    source_generation: int
    reason_code: str = ""
    durable: bool = False

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.idempotency_key.strip():
            raise ValueError("transaction result identity is required")
        if not _is_sha256(self.payload_digest) or not _is_sha256(self.idempotency_key):
            raise ValueError("transaction result payload digest is invalid")
        if not self.source_revision.strip() or self.source_generation < 0:
            raise ValueError("transaction result source fence is invalid")
        if self.status in {
                EquipmentTransactionStatus.COMMITTED,
                EquipmentTransactionStatus.REPLAYED,
        } and not self.durable:
            raise ValueError("committed transaction result must assert durable persistence")
        if self.status in {
                EquipmentTransactionStatus.BLOCKED,
                EquipmentTransactionStatus.CONFLICT,
                EquipmentTransactionStatus.COMMIT_UNKNOWN,
        } and self.durable:
            raise ValueError("blocked, conflicting or unknown transaction result cannot assert success")



class HumanSelectionTransactionPort(Protocol):
    """The exclusive durable selection boundary.

    The implementation must use one transaction to authenticate the scope,
    re-read the current source revision/generation and projection digest,
    validate line/tender/candidate-group membership and current availability,
    compare the active selection CAS, append immutable selection/audit evidence,
    and claim ``idempotency_key``.  Identical replay returns the original
    durable result.  Reuse with a changed payload digest, stale generation,
    stale source revision, duplicate active line choice, or supersession race
    returns ``CONFLICT``.  Any write failure rolls back every row; an uncertain
    post-commit observation returns ``COMMIT_UNKNOWN`` for reconciliation and
    must never be translated into success.
    """

    async def commit_selection(
        self, *, transaction: SelectionTransaction
    ) -> EquipmentTransactionResult:
        ...


class ProcurementExportTransactionPort(Protocol):
    """The exclusive durable prepared-export boundary.

    The implementation must atomically verify the human selection set,
    projection/source fence, line membership, supplier/source provenance,
    availability, quantity×unit-price totals and scoped idempotency record,
    then append the export snapshot and audit evidence.  It must not submit to
    a supplier or create an artifact receipt.  Exact replay returns the first
    durable result; changed digest, stale generation, changed selection set,
    duplicate export identity or a post-commit uncertainty fail closed.
    """

    async def commit_export(
        self, *, transaction: ExportTransaction
    ) -> EquipmentTransactionResult:
        ...


class EquipmentDomainTransactionPort(
    HumanSelectionTransactionPort, ProcurementExportTransactionPort, Protocol
):
    """Combined seam used by the application service when both writes are wired."""

    pass


def blocked_transaction_result(*, scope: DomainScope, operation: str,
                               idempotency_key: str, payload_digest: str,
                               source_revision: str, source_generation: int,
                               reason_code: str = WIRING_BLOCKER) -> EquipmentTransactionResult:
    """Return an explicit no-write outcome when no trusted adapter is supplied."""
    return EquipmentTransactionResult(
        status=EquipmentTransactionStatus.BLOCKED,
        operation_id=f"blocked:{operation}",
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        scope=scope,
        source_revision=source_revision,
        source_generation=source_generation,
        reason_code=reason_code,
        durable=False,
    )


def validate_transaction_result(*, result: EquipmentTransactionResult,
                                scope: DomainScope, idempotency_key: str,
                                payload_digest: str, source_revision: str,
                                source_generation: int) -> EquipmentTransactionResult:
    """Reject adapter receipts that do not prove the exact requested fence."""
    if (result.scope != scope or result.idempotency_key != idempotency_key
            or result.payload_digest != payload_digest
            or result.source_revision != source_revision
            or result.source_generation != source_generation):
        raise RuntimeError("equipment transaction adapter returned mismatched fence evidence")
    if result.status is EquipmentTransactionStatus.COMMIT_UNKNOWN:
        raise RuntimeError(
            "equipment transaction commit outcome is unknown; reconcile by idempotency key"
        )
    if result.status in {
            EquipmentTransactionStatus.COMMITTED,
            EquipmentTransactionStatus.REPLAYED,
    } and not result.durable:
        raise RuntimeError("equipment transaction adapter claimed success without durable evidence")
    return result


__all__ = [
    "EquipmentDomainTransactionPort",
    "EquipmentTransactionResult",
    "EquipmentTransactionStatus",
    "ExportTransaction",
    "HumanSelectionTransactionPort",
    "ProcurementExportTransactionPort",
    "SelectionTransaction",
    "WIRING_BLOCKER",
    "blocked_transaction_result",
    "validate_transaction_result",
]
