"""Procurement export preview materialized from durable, scoped equipment state.

The module turns the deterministic projection plus the durable human-selection
history and durable budget allocations into one reviewable *bundle*.  It adds
nothing the database does not hold and it writes nothing:

* line order, currency totals and the bundle digest are derived only from
  already-ordered projection data, so two runs over the same committed rows
  produce byte-identical output;
* an empty selection set or an empty projection yields an explicit ``empty``
  bundle instead of an exception, because "nothing was selected yet" is a
  normal operational state that an operator must be able to see;
* an unselected request line yields a ``partial`` bundle that lists the exact
  missing line IDs; the durable export transaction refuses to commit such a
  bundle, so partial data can never be reported as a finished export;
* a candidate group without offers is reported by ID.  It is not an error: a
  group legitimately exists before its offers are materialized, and inventing
  a placeholder offer would be worse than showing the gap;
* an unfilled budget is reported as ``unfilled`` with explicit variance of
  ``None``.  A zero or absent allocation is never read as "the budget covers
  the export";
* every field is an identity, a digest, a Decimal amount or a normalized
  status token.  Free-form human justification, provider payloads and
  credentials never enter the bundle, so it is safe to log and to store as
  export evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from .domain import (
    BudgetAllocation,
    BudgetAllocationStatus,
    DomainScope,
    EquipmentDomainStatus,
    EquipmentProjection,
    HumanSelection,
    ProcurementExport,
    ProcurementExportLine,
    _exact_sum,
    _stable_digest,
    active_human_selections,
    prepare_procurement_export,
    selection_evidence_digest,
)


class DurableExportStatus(StrEnum):
    """Completeness of one prepared export bundle."""

    FULL = "full"
    PARTIAL = "partial"
    EMPTY = "empty"


class BudgetCoverageStatus(StrEnum):
    """Whether durable budget rows can be compared with the export at all."""

    FILLED = "filled"
    PARTIAL = "partial"
    UNFILLED = "unfilled"


@dataclass(frozen=True, slots=True)
class BudgetExportLine:
    """One durable budget allocation as it appears in export evidence."""

    request_id: str
    cost_center: str
    amount: Decimal
    currency: str
    status: str

    @property
    def counted(self) -> bool:
        return self.status != BudgetAllocationStatus.RELEASED.value


@dataclass(frozen=True, slots=True)
class BudgetCoverage:
    """Per-currency comparison of durable budget rows against selected totals."""

    currency: str
    allocated: Decimal
    selected: Decimal
    variance: Decimal
    allocation_count: int


@dataclass(frozen=True, slots=True)
class ExportDiagnosticNote:
    """Redacted, stable export diagnosis; never carries row payloads."""

    code: str
    subject_id: str = ""


@dataclass(frozen=True, slots=True)
class DurableExportBundle:
    export_id: str
    scope: DomainScope
    actor_id: str
    status: DurableExportStatus
    selection_digest: str
    projection_digest: str
    source_revision: str
    source_generation: int
    idempotency_key: str
    lines: tuple[ProcurementExportLine, ...] = ()
    prepared_export: ProcurementExport | None = None
    selection_ids: tuple[str, ...] = ()
    unselected_line_ids: tuple[str, ...] = ()
    empty_candidate_group_ids: tuple[str, ...] = ()
    budget_lines: tuple[BudgetExportLine, ...] = ()
    budget_coverage: tuple[BudgetCoverage, ...] = ()
    budget_status: BudgetCoverageStatus = BudgetCoverageStatus.UNFILLED
    currency_totals: tuple[tuple[str, Decimal], ...] = ()
    diagnostics: tuple[ExportDiagnosticNote, ...] = ()
    bundle_digest: str = ""

    @property
    def committable(self) -> bool:
        """A bundle may only be committed when it is complete and consistent."""
        return self.status is DurableExportStatus.FULL and self.prepared_export is not None

    @property
    def tender_ids(self) -> tuple[str, ...]:
        return tuple(row.tender_id for row in self.lines)

    @property
    def requires_human_review(self) -> bool:
        return True


def prepare_durable_export(
    *,
    projection: EquipmentProjection,
    selections: Sequence[HumanSelection],
    scope: DomainScope,
    actor_id: str,
    export_id: str,
    budget_allocations: Sequence[BudgetAllocation] = (),
    empty_candidate_group_ids: Sequence[str] = (),
) -> DurableExportBundle:
    """Build the export bundle for exactly the durable rows supplied.

    The caller is responsible for having read those rows through a scoped port;
    every row is re-checked against ``scope`` here so a port that leaks another
    tenant cannot produce a positive bundle.
    """
    if not isinstance(scope, DomainScope):
        raise ValueError("durable export requires an explicit tenant/owner/project scope")
    if not actor_id.strip() or not export_id.strip():
        raise ValueError("durable export requires a human actor and a stable export identity")

    if projection.status is not EquipmentDomainStatus.READY:
        return _bundle(
            projection=projection, scope=scope, actor_id=actor_id, export_id=export_id,
            status=DurableExportStatus.EMPTY,
            selections=(), budget_allocations=budget_allocations,
            empty_candidate_group_ids=empty_candidate_group_ids,
            diagnostics=(ExportDiagnosticNote("projection_blocked"),) + tuple(
                ExportDiagnosticNote("projection_blocked_reason", reason)
                for reason in projection.blocked_reasons
            ),
        )

    records = tuple(selections)
    if any((row.tenant_id, row.owner_id, row.project_id) != (
            scope.tenant_id, scope.owner_id, scope.project_id) for row in records):
        raise ValueError("durable export selection crosses tenant, owner or project scope")
    if any(row.actor_id != actor_id for row in records):
        raise ValueError("durable export selection belongs to another actor")
    if any(not row.selection_id.strip() or not row.reason.strip() for row in records):
        raise ValueError("durable export selection identity or recorded reason is missing")

    active = active_human_selections(records)
    if not active:
        return _bundle(
            projection=projection, scope=scope, actor_id=actor_id, export_id=export_id,
            status=DurableExportStatus.EMPTY, selections=(), budget_allocations=budget_allocations,
            empty_candidate_group_ids=empty_candidate_group_ids,
            diagnostics=(ExportDiagnosticNote("no_human_selection"),),
        )

    prepared = prepare_procurement_export(
        projection=projection, selections=active, actor_id=actor_id, export_id=export_id,
    )
    projected_line_ids = tuple(sorted(line.line_id for line in projection.lines))
    selected_line_ids = tuple(sorted(row.line_id for row in active))
    unselected = tuple(line_id for line_id in projected_line_ids if line_id not in set(selected_line_ids))
    diagnostics: list[ExportDiagnosticNote] = []
    for line_id in unselected:
        diagnostics.append(ExportDiagnosticNote("unselected_request_line", line_id))
    for group_id in sorted(empty_candidate_group_ids):
        diagnostics.append(ExportDiagnosticNote("candidate_group_without_offers", group_id))
    return _bundle(
        projection=projection, scope=scope, actor_id=actor_id, export_id=export_id,
        status=(DurableExportStatus.FULL if not unselected else DurableExportStatus.PARTIAL),
        selections=active, budget_allocations=budget_allocations,
        empty_candidate_group_ids=empty_candidate_group_ids,
        prepared=prepared, unselected_line_ids=unselected,
        diagnostics=tuple(diagnostics),
    )


def _bundle(
    *,
    projection: EquipmentProjection,
    scope: DomainScope,
    actor_id: str,
    export_id: str,
    status: DurableExportStatus,
    selections: Sequence[HumanSelection],
    budget_allocations: Sequence[BudgetAllocation],
    empty_candidate_group_ids: Sequence[str],
    prepared: ProcurementExport | None = None,
    unselected_line_ids: tuple[str, ...] = (),
    diagnostics: tuple[ExportDiagnosticNote, ...] = (),
) -> DurableExportBundle:
    lines = tuple(prepared.lines) if prepared is not None else ()
    _assert_deterministic_order(lines)
    selection_ids = tuple(row.selection_id for row in lines)
    currency_totals = _currency_totals(lines)
    budget_rows = tuple(
        sorted(
            (BudgetExportLine(row.request_id, row.cost_center, row.amount, row.currency, row.status)
             for row in budget_allocations),
            key=lambda row: (row.request_id, row.cost_center, row.currency, row.amount, row.status),
        )
    )
    coverage = _budget_coverage(budget_rows, currency_totals)
    budget_status = _budget_status(budget_rows, coverage)
    if not budget_rows:
        diagnostics = diagnostics + (ExportDiagnosticNote("budget_allocations_unfilled"),)
    selection_digest = selection_evidence_digest(selections) if selections else _stable_digest(())
    idempotency_key = (
        prepared.idempotency_key if prepared is not None
        else _scoped_export_key(scope=scope, export_id=export_id)
    )
    digest = _stable_digest({
        "contract": "equipment.v2.procurement_export_bundle/1",
        "export_id": export_id,
        "status": status.value,
        "scope": scope,
        "actor_id": actor_id,
        "selection_digest": selection_digest,
        "projection_digest": projection.contract.input_digest,
        "source_revision": projection.contract.source_revision,
        "source_generation": projection.contract.source_generation,
        "lines": lines,
        "unselected_line_ids": unselected_line_ids,
        "empty_candidate_group_ids": tuple(sorted(set(empty_candidate_group_ids))),
        "budget_lines": budget_rows,
        "budget_status": budget_status.value,
        "currency_totals": currency_totals,
        "diagnostics": diagnostics,
    })
    return DurableExportBundle(
        export_id=export_id,
        scope=scope,
        actor_id=actor_id,
        status=status,
        selection_digest=selection_digest,
        projection_digest=projection.contract.input_digest,
        source_revision=projection.contract.source_revision,
        source_generation=projection.contract.source_generation,
        idempotency_key=idempotency_key,
        lines=lines,
        prepared_export=prepared,
        selection_ids=selection_ids,
        unselected_line_ids=unselected_line_ids,
        empty_candidate_group_ids=tuple(sorted(set(empty_candidate_group_ids))),
        budget_lines=budget_rows,
        budget_coverage=coverage,
        budget_status=budget_status,
        currency_totals=currency_totals,
        diagnostics=diagnostics,
        bundle_digest=digest,
    )


def _assert_deterministic_order(lines: Sequence[ProcurementExportLine]) -> None:
    """The committed export order must be the projection order, not insert order."""
    keys = [(row.line_id, row.candidate_group_id, row.tender_id) for row in lines]
    if len(set(keys)) != len(keys):
        raise ValueError("durable export contains duplicated selected offers")
    if len({row.line_id for row in lines}) != len(lines):
        raise ValueError("durable export contains more than one offer for a request line")


def _currency_totals(
    lines: Sequence[ProcurementExportLine],
) -> tuple[tuple[str, Decimal], ...]:
    grouped: dict[str, list[Decimal]] = {}
    for row in lines:
        grouped.setdefault(row.currency, []).append(row.total)
    return tuple((currency, _exact_sum(amounts)) for currency, amounts in sorted(grouped.items()))


def _budget_coverage(
    rows: Sequence[BudgetExportLine],
    currency_totals: Sequence[tuple[str, Decimal]],
) -> tuple[BudgetCoverage, ...]:
    allocated: dict[str, list[Decimal]] = {}
    for row in rows:
        if row.counted:
            allocated.setdefault(row.currency, []).append(row.amount)
    coverage: list[BudgetCoverage] = []
    for currency in sorted(set(allocated) | {currency for currency, _ in currency_totals}):
        amounts = allocated.get(currency, [])
        selected = next((total for code, total in currency_totals if code == currency), Decimal("0"))
        allocated_total = _exact_sum(amounts)
        coverage.append(BudgetCoverage(
            currency=currency,
            allocated=allocated_total,
            selected=selected,
            variance=allocated_total - selected,
            allocation_count=len(amounts),
        ))
    return tuple(coverage)


def _budget_status(
    rows: Sequence[BudgetExportLine],
    coverage: Sequence[BudgetCoverage],
) -> BudgetCoverageStatus:
    if not rows or not any(row.counted for row in rows):
        # Every allocation is released (or none exists): the export has no budget
        # to be compared with, which is "unfilled", never "covered".
        return BudgetCoverageStatus.UNFILLED
    unfilled = any(item.allocation_count == 0 for item in coverage)
    return BudgetCoverageStatus.PARTIAL if unfilled else BudgetCoverageStatus.FILLED


def _scoped_export_key(*, scope: DomainScope, export_id: str) -> str:
    return _stable_digest({
        "tenant_id": scope.tenant_id,
        "owner_id": scope.owner_id,
        "project_id": scope.project_id,
        "export_id": export_id,
    })


__all__ = [
    "BudgetCoverage",
    "BudgetCoverageStatus",
    "BudgetExportLine",
    "DurableExportBundle",
    "DurableExportStatus",
    "ExportDiagnosticNote",
    "prepare_durable_export",
]
