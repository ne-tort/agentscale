"""MIG-EQ-EXPORT-01 review contract for procurement document plans.

This module prepares immutable, deterministic review data only. Durable claims,
human identity, workbook/PDF rendering and artifact storage belong to explicit
adapters; no document bytes or supplier-side effects are produced here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from decimal import (
    ROUND_05UP,
    ROUND_DOWN,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    DecimalException,
    localcontext,
)
from enum import StrEnum
from typing import Protocol, Sequence, cast

from .domain import (
    DomainScope,
    EquipmentDomainStatus,
    EquipmentProjection,
    HumanSelection,
    OfferFreshness,
    ProcurementExport,
    active_human_selections,
    prepare_procurement_export,
)


_ALLOWED_ROUNDING = frozenset({
    ROUND_05UP, ROUND_DOWN, ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP,
})
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
LINE_TOTAL_ROUNDING_RULE = (
    "rounded_unit_price is display-only; round(quantity * source_unit_price) "
    "per line using the currency policy; currency total is the exact sum of "
    "rounded line totals"
)


class ProcurementDocumentFormat(StrEnum):
    XLSX = "xlsx"
    PDF = "pdf"


class ApprovalActorKind(StrEnum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class CurrencyRoundingPolicy:
    """Explicit minor-unit precision and Decimal rounding mode for one currency."""

    currency: str
    exponent: int
    rounding: str = ROUND_HALF_UP

    def __post_init__(self) -> None:
        if (len(self.currency) != 3 or not self.currency.isalpha()
                or self.currency.upper() != self.currency):
            raise ValueError("currency policy requires a three-letter uppercase code")
        if not 0 <= self.exponent <= 9:
            raise ValueError("currency exponent must be between zero and nine")
        if self.rounding not in _ALLOWED_ROUNDING:
            raise ValueError("unsupported Decimal rounding mode")

    @property
    def quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.exponent)


@dataclass(frozen=True, slots=True)
class ExportDiagnostic:
    code: str
    message: str
    subject_id: str = ""


@dataclass(frozen=True, slots=True)
class ProcurementDocumentLine:
    selection_id: str
    request_id: str
    line_id: str
    tender_id: str
    candidate_group_id: str
    supplier_id: str
    source_id: str
    source_record_id: str
    source_schema_version: str
    part_number: str | None
    description: str
    quantity: Decimal
    unit: str
    currency: str
    unit_price: Decimal
    rounded_unit_price: Decimal
    raw_total: Decimal
    rounded_total: Decimal
    source_revision: str = ""
    source_generation: int = 0
    manual_override: bool = True


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    currency: str
    rounded_total: Decimal
    line_count: int


@dataclass(frozen=True, slots=True)
class ProcurementDocumentExportPlan:
    export_id: str
    idempotency_key: str
    plan_digest: str
    scope: DomainScope
    prepared_by: str
    projection_digest: str
    source_revision: str
    source_generation: int
    selection_digest: str
    selection_ids: tuple[str, ...]
    source_schema_versions: tuple[str, ...]
    lines: tuple[ProcurementDocumentLine, ...]
    currency_totals: tuple[CurrencyTotal, ...]
    rounding_policies: tuple[CurrencyRoundingPolicy, ...]
    rounding_rule: str
    formats: tuple[ProcurementDocumentFormat, ...]
    human_approval_required: bool = True


@dataclass(frozen=True, slots=True)
class ExportReview:
    plan: ProcurementDocumentExportPlan | None
    diagnostics: tuple[ExportDiagnostic, ...]
    human_approval_required: bool = True

    @property
    def ready_for_human_review(self) -> bool:
        return self.plan is not None and not self.diagnostics


@dataclass(frozen=True, slots=True)
class ProcurementExportApproval:
    approval_id: str
    actor_id: str
    actor_evidence_id: str
    actor_kind: ApprovalActorKind
    plan_digest: str
    approved_at: str
    scope: DomainScope

    def __post_init__(self) -> None:
        if not all((self.approval_id, self.actor_id, self.actor_evidence_id, self.plan_digest)):
            raise ValueError("approval identity, authenticated actor evidence and plan digest are required")
        if self.actor_kind is not ApprovalActorKind.HUMAN:
            raise ValueError("procurement export approval must be attributed to a human")
        if not _SHA256_PATTERN.fullmatch(self.plan_digest):
            raise ValueError("approval requires a lowercase sha256 plan digest")
        try:
            from datetime import datetime
            timestamp = datetime.fromisoformat(self.approved_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("approval time must be ISO-8601") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("approval time must include a timezone")


@dataclass(frozen=True, slots=True)
class ApprovalActorEvidence:
    """Trusted authentication result; populate only from the application auth port."""

    actor_id: str
    actor_kind: ApprovalActorKind
    scope: DomainScope
    evidence_id: str


@dataclass(frozen=True, slots=True)
class ProcurementExportFence:
    resource_key: str
    holder_id: str
    epoch: int
    fencing_token: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.resource_key, self.holder_id, self.fencing_token)):
            raise ValueError("export fencing lease identity and token are required")
        if self.epoch < 1:
            raise ValueError("export fencing epoch must be positive")


@dataclass(frozen=True, slots=True)
class FormatRenderPermit:
    permit_id: str
    format: ProcurementDocumentFormat
    holder_id: str
    fencing_epoch: int
    fencing_token: str

    def __post_init__(self) -> None:
        if not all((self.permit_id, self.holder_id, self.fencing_token)):
            raise ValueError("format render permit identity and fencing token are required")
        if not isinstance(self.format, ProcurementDocumentFormat):
            raise ValueError("format render permit format must be XLSX or PDF")
        if self.fencing_epoch < 1:
            raise ValueError("format render permit fencing epoch must be positive")


@dataclass(frozen=True, slots=True)
class AuthorizedExportRender:
    plan: ProcurementDocumentExportPlan
    approval: ProcurementExportApproval
    reservation: ExportReservation
    fence: ProcurementExportFence
    render_permit: FormatRenderPermit
    format: ProcurementDocumentFormat


@dataclass(frozen=True, slots=True)
class ExportReservation:
    reservation_id: str
    idempotency_key: str
    plan_digest: str
    approval_id: str
    holder_id: str
    fencing_epoch: int
    fencing_token: str
    artifact_receipts: tuple[ArtifactReceipt, ...] = ()
    render_permits: tuple[FormatRenderPermit, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.reservation_id, self.idempotency_key, self.plan_digest,
                    self.approval_id, self.holder_id, self.fencing_token)):
            raise ValueError("export reservation identity, digest and fence are required")
        if self.fencing_epoch < 1:
            raise ValueError("export reservation fencing epoch must be positive")


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    """Adapter assertion about a persisted artifact, never an artifact payload."""

    artifact_id: str
    format: ProcurementDocumentFormat
    content_digest: str
    byte_length: int
    plan_digest: str
    approval_id: str
    idempotency_key: str
    reservation_id: str
    render_permit_id: str
    holder_id: str
    fencing_token: str
    fencing_epoch: int

    def __post_init__(self) -> None:
        if not all((self.artifact_id, self.approval_id, self.idempotency_key,
                    self.reservation_id, self.render_permit_id, self.holder_id,
                    self.fencing_token, self.plan_digest)):
            raise ValueError("persisted artifact receipt identity is incomplete")
        if not _SHA256_PATTERN.fullmatch(self.content_digest):
            raise ValueError("artifact receipt requires a lowercase sha256 digest")
        if not isinstance(self.format, ProcurementDocumentFormat):
            raise ValueError("artifact receipt format must be XLSX or PDF")
        if self.byte_length <= 0 or self.fencing_epoch < 1:
            raise ValueError("artifact receipt requires nonempty bytes and a positive fence epoch")


def review_procurement_export(*, projection: EquipmentProjection,
                              prepared_export: ProcurementExport,
                              selections: Sequence[HumanSelection],
                              scope: DomainScope, actor_id: str,
                              formats: Sequence[ProcurementDocumentFormat],
                              rounding_policies: Sequence[CurrencyRoundingPolicy]) -> ExportReview:
    """Build a deterministic plan only for a complete, current, explicitly selected scope.

    Line totals are rounded independently to the configured currency quantum,
    using the policy's explicit Decimal mode; currency totals sum those rounded
    line totals. Unit prices and source amounts remain unrounded in the plan.
    """
    diagnostics: list[ExportDiagnostic] = []
    if projection.status is not EquipmentDomainStatus.READY:
        diagnostics.append(ExportDiagnostic("projection_blocked", "Source projection is not ready."))
    if not projection.contract.source_revision.strip():
        diagnostics.append(ExportDiagnostic("source_revision_missing", "Source revision provenance is required."))
    if projection.contract.source_generation < 1:
        diagnostics.append(ExportDiagnostic("source_generation_missing", "A positive source generation is required for export fencing."))
    if prepared_export.status != "prepared":
        diagnostics.append(ExportDiagnostic("export_not_prepared", "A prepared procurement snapshot is required."))
    if not actor_id.strip() or prepared_export.actor_id != actor_id:
        diagnostics.append(ExportDiagnostic("actor_mismatch", "Export actor does not match the requested human actor."))
    if not prepared_export.export_id.strip():
        diagnostics.append(ExportDiagnostic("export_identity_missing", "A stable export identity is required."))
    if not prepared_export.idempotency_key.startswith("sha256:"):
        diagnostics.append(ExportDiagnostic("export_idempotency_missing", "A scoped export idempotency key is required."))
    if (prepared_export.tenant_id, prepared_export.owner_id, prepared_export.project_id) != (
            scope.tenant_id, scope.owner_id, scope.project_id):
        diagnostics.append(ExportDiagnostic("export_scope_mismatch", "Prepared export is outside the requested tenant, owner or project."))
    if prepared_export.projection_digest != projection.contract.input_digest:
        diagnostics.append(ExportDiagnostic("stale_projection", "Prepared export was built from another source snapshot."))
    if prepared_export.source_generation != projection.contract.source_generation:
        diagnostics.append(ExportDiagnostic("stale_generation", "Prepared export was built from another source generation."))
    if prepared_export.source_revision != projection.contract.source_revision:
        diagnostics.append(ExportDiagnostic("stale_source_revision", "Prepared export was built from another source revision."))
    expected_idempotency_key = _digest({
        "tenant_id": scope.tenant_id,
        "owner_id": scope.owner_id,
        "project_id": scope.project_id,
        "export_id": prepared_export.export_id,
    })
    if prepared_export.idempotency_key != expected_idempotency_key:
        diagnostics.append(ExportDiagnostic(
            "export_idempotency_mismatch",
            "Prepared export key does not match its scoped export identity.",
        ))

    evidence_rows = (*projection.requests, *projection.lines,
                     *projection.candidate_groups, *projection.tenders)
    if any(row.evidence.scope != (scope.tenant_id, scope.owner_id, scope.project_id)
           for row in evidence_rows):
        diagnostics.append(ExportDiagnostic("domain_scope_mismatch", "Projection contains missing or cross-scope evidence."))
    if (any(not row.evidence.owner_source_complete for row in (
            *projection.requests, *projection.lines, *projection.candidate_groups))
            or any(not row.evidence.complete for row in projection.tenders)):
        diagnostics.append(ExportDiagnostic("domain_provenance_incomplete", "Projection source or supplier provenance is incomplete."))

    active: tuple[HumanSelection, ...] = ()
    try:
        active = active_human_selections(selections)
    except ValueError:
        diagnostics.append(ExportDiagnostic("selection_history_invalid", "Human selection history is missing, duplicated or ambiguous."))

    if any(item.actor_id != actor_id for item in active):
        diagnostics.append(ExportDiagnostic("selection_actor_mismatch", "Selections do not belong to the requested actor."))
    if any((item.tenant_id, item.owner_id, item.project_id) != (
            scope.tenant_id, scope.owner_id, scope.project_id) for item in active):
        diagnostics.append(ExportDiagnostic("selection_scope_mismatch", "A selection is outside the requested tenant, owner or project."))
    if any(item.projection_digest != projection.contract.input_digest for item in active):
        diagnostics.append(ExportDiagnostic("stale_human_selection", "A human selection was made against another source projection."))
    expected_line_ids = {line.line_id for line in projection.lines}
    selected_line_ids = {item.line_id for item in active}
    for line_id in sorted(expected_line_ids - selected_line_ids):
        diagnostics.append(ExportDiagnostic("unselected_request_line", "Every request line requires an explicit human selection.", line_id))
    for line_id in sorted(selected_line_ids - expected_line_ids):
        diagnostics.append(ExportDiagnostic("selection_line_missing", "Selection refers to a line outside the source projection.", line_id))

    canonical_export: ProcurementExport | None = None
    current_tenders = {tender.tender_id: tender for tender in projection.tenders}
    for selection in active:
        tender = current_tenders.get(selection.tender_id)
        if tender is not None and tender.freshness is OfferFreshness.STALE:
            diagnostics.append(ExportDiagnostic("stale_selected_offer", "A stale offer cannot be included in a procurement document.", tender.tender_id))
        elif tender is not None and tender.freshness is OfferFreshness.UNKNOWN:
            diagnostics.append(ExportDiagnostic("offer_freshness_unknown", "Offer freshness must be confirmed before document export.", tender.tender_id))
        if tender is not None and tender.on_order:
            diagnostics.append(ExportDiagnostic("on_order_offer", "An already-on-order offer cannot be included in a procurement document.", tender.tender_id))
        if tender is not None and tender.available_quantity is not None:
            line = next((row for row in projection.lines if row.line_id == tender.line_id), None)
            if line is not None and tender.available_quantity < line.quantity:
                diagnostics.append(ExportDiagnostic("insufficient_availability", "Selected offer availability is below the requested quantity.", tender.tender_id))
        elif tender is not None:
            diagnostics.append(ExportDiagnostic("availability_unknown", "Selected offer availability is not confirmed.", tender.tender_id))
    if projection.status is EquipmentDomainStatus.READY and active:
        try:
            canonical_export = prepare_procurement_export(
                projection=projection, selections=active, actor_id=actor_id,
                export_id=prepared_export.export_id,
            )
        except ValueError:
            diagnostics.append(ExportDiagnostic(
                "selection_invalid", "Selected offer, actor or provenance is not valid for this projection."
            ))

    if canonical_export is not None and canonical_export != prepared_export:
        diagnostics.append(ExportDiagnostic("export_snapshot_mismatch", "Prepared export does not match the current selections and projection."))
        expected_by_tender = {row.tender_id: row for row in canonical_export.lines}
        for row in prepared_export.lines:
            if expected_by_tender.get(row.tender_id) != row:
                diagnostics.append(ExportDiagnostic(
                    "line_snapshot_inconsistent",
                    "Prepared line amount or provenance differs from the canonical selection snapshot.",
                    row.line_id,
                ))

    selected_formats: set[ProcurementDocumentFormat] = set()
    for value in formats:
        if not isinstance(value, ProcurementDocumentFormat):
            diagnostics.append(ExportDiagnostic("unsupported_document_format", "Only workbook and PDF adapter formats are supported."))
        else:
            selected_formats.add(value)
    if not selected_formats:
        diagnostics.append(ExportDiagnostic("document_format_missing", "At least one supported output format must be selected."))

    policy_by_currency: dict[str, CurrencyRoundingPolicy] = {}
    for policy in rounding_policies:
        if not isinstance(policy, CurrencyRoundingPolicy):
            diagnostics.append(ExportDiagnostic("invalid_rounding_policy", "Every currency requires an explicit rounding policy."))
            continue
        if policy.currency in policy_by_currency:
            diagnostics.append(ExportDiagnostic("duplicate_rounding_policy", "Currency rounding policy must be unique.", policy.currency))
        policy_by_currency[policy.currency] = policy
    snapshot_currencies = {line.currency for line in prepared_export.lines}
    for currency in sorted(snapshot_currencies - policy_by_currency.keys()):
        diagnostics.append(ExportDiagnostic("currency_policy_missing", "No explicit minor-unit policy exists for this source currency.", currency))
    for currency in sorted(policy_by_currency.keys() - snapshot_currencies):
        diagnostics.append(ExportDiagnostic("currency_policy_unused", "A rounding policy was supplied for a currency absent from the snapshot.", currency))

    document_lines: list[ProcurementDocumentLine] = []
    totals_by_currency: dict[str, list[Decimal]] = {}
    if canonical_export is not None:
        line_by_id = {line.line_id: line for line in projection.lines}
        group_by_id = {group.group_id: group for group in projection.candidate_groups}
        selection_by_id = {item.selection_id: item for item in active}
        tender_by_id = {tender.tender_id: tender for tender in projection.tenders}
        rows = sorted(canonical_export.lines, key=lambda row: (
            line_by_id[row.line_id].request_id, line_by_id[row.line_id].ordinal,
            row.line_id, group_by_id[row.candidate_group_id].ordinal, row.tender_id,
        ))
        for row in rows:
            policy = policy_by_currency.get(row.currency)
            if policy is None:
                continue
            line = line_by_id[row.line_id]
            tender = tender_by_id[row.tender_id]
            selection = selection_by_id.get(row.selection_id)
            exact_total = _exact_product(row.quantity, row.unit_price)
            if (selection is None or row.total != exact_total or row.quantity != line.quantity
                    or row.currency != tender.currency or row.unit_price != tender.unit_price):
                diagnostics.append(ExportDiagnostic("line_snapshot_inconsistent", "Line amount or provenance differs from the source projection.", row.line_id))
                continue
            try:
                rounded_total = _round_amount(exact_total, policy)
                rounded_unit_price = _round_amount(row.unit_price, policy)
            except DecimalException:
                diagnostics.append(ExportDiagnostic("line_rounding_failed", "Source amount cannot be rounded under the explicit currency policy.", row.line_id))
                continue
            document_lines.append(ProcurementDocumentLine(
                row.selection_id, line.request_id, row.line_id, row.tender_id,
                row.candidate_group_id, row.supplier_id, row.source_id,
                row.source_record_id, tender.evidence.source_schema_version or "",
                row.part_number,
                row.description, row.quantity, row.unit, row.currency, row.unit_price,
                rounded_unit_price, exact_total, rounded_total,
                projection.contract.source_revision, row.source_generation, row.manual_override,
            ))
            totals_by_currency.setdefault(row.currency, []).append(rounded_total)

    diagnostics_tuple = tuple(sorted(set(diagnostics), key=lambda item: (item.code, item.subject_id, item.message)))
    if diagnostics_tuple or canonical_export is None:
        return ExportReview(None, diagnostics_tuple or (
            ExportDiagnostic("export_unavailable", "A canonical prepared export could not be established."),
        ))

    ordered_lines = tuple(document_lines)
    currency_totals = tuple(CurrencyTotal(currency, _exact_sum(amounts), len(amounts))
                            for currency, amounts in sorted(totals_by_currency.items()))
    policies = tuple(policy_by_currency[currency] for currency in sorted(policy_by_currency))
    ordered_formats = tuple(sorted(selected_formats, key=lambda item: 0 if item is ProcurementDocumentFormat.XLSX else 1))
    source_versions = tuple(sorted({row.evidence.source_schema_version
                                    for row in evidence_rows
                                    if row.evidence.source_schema_version}))
    expected_idempotency_key = _digest({
        "tenant_id": scope.tenant_id,
        "owner_id": scope.owner_id,
        "project_id": scope.project_id,
        "export_id": prepared_export.export_id,
    })
    idempotency_key = prepared_export.idempotency_key
    if idempotency_key != expected_idempotency_key:
        diagnostics.append(ExportDiagnostic(
            "export_idempotency_mismatch",
            "Prepared export key does not match its scoped export identity.",
        ))
    plan_content = {
        "export_id": prepared_export.export_id,
        "idempotency_key": idempotency_key,
        "scope": scope,
        "prepared_by": actor_id,
        "projection_digest": projection.contract.input_digest,
        "source_revision": projection.contract.source_revision,
        "source_generation": projection.contract.source_generation,
        "selection_digest": prepared_export.selection_digest,
        "selection_ids": tuple(row.selection_id for row in ordered_lines),
        "source_schema_versions": source_versions,
        "lines": ordered_lines,
        "currency_totals": currency_totals,
        "rounding_policies": policies,
        "rounding_rule": LINE_TOTAL_ROUNDING_RULE,
        "formats": ordered_formats,
        "human_approval_required": True,
    }
    plan = ProcurementDocumentExportPlan(
        prepared_export.export_id, idempotency_key, _digest(plan_content), scope,
        actor_id, projection.contract.input_digest, projection.contract.source_revision,
        projection.contract.source_generation,
        prepared_export.selection_digest, tuple(row.selection_id for row in ordered_lines),
        source_versions, ordered_lines, currency_totals, policies,
        LINE_TOTAL_ROUNDING_RULE, ordered_formats,
    )
    return ExportReview(plan, ())


def approve_export_review(*, review: ExportReview, approval_id: str,
                          actor: ApprovalActorEvidence, approved_at: str,
                          plan_digest: str) -> ProcurementExportApproval:
    """Bind an explicit human approval to exactly the reviewed plan digest."""
    plan = review.plan
    if not review.ready_for_human_review or plan is None:
        raise ValueError("blocked export review cannot be approved")
    if not approval_id.strip() or not actor.actor_id.strip() or not actor.evidence_id.strip():
        raise ValueError("approval identity and trusted actor evidence are required")
    if actor.actor_kind is not ApprovalActorKind.HUMAN:
        raise PermissionError("procurement document approval requires a human actor")
    if actor.scope != plan.scope:
        raise PermissionError("human approval actor is outside the reviewed export scope")
    if plan_digest != plan.plan_digest:
        raise ValueError("approval must reference the exact reviewed plan digest")
    try:
        from datetime import datetime
        timestamp = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("approval time must be ISO-8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("approval time must include a timezone")
    return ProcurementExportApproval(approval_id, actor.actor_id, actor.evidence_id,
                                    actor.actor_kind,
                                    plan.plan_digest, approved_at, plan.scope)


def authorize_export_render(*, review: ExportReview, approval: ProcurementExportApproval,
                            reservation: ExportReservation, fence: ProcurementExportFence,
                            render_permit: FormatRenderPermit,
                            format: ProcurementDocumentFormat) -> AuthorizedExportRender:
    """Create the only renderer command after review, approval and lease checks."""
    plan = review.plan
    if not review.ready_for_human_review or plan is None:
        raise ValueError("blocked export review cannot be rendered")
    if (approval.actor_kind is not ApprovalActorKind.HUMAN
            or approval.plan_digest != plan.plan_digest or approval.scope != plan.scope):
        raise PermissionError("matching human approval is required before rendering")
    if fence.resource_key != plan.idempotency_key:
        raise ValueError("fencing lease is for another idempotent export resource")
    if (reservation.idempotency_key != plan.idempotency_key
            or reservation.plan_digest != plan.plan_digest
            or reservation.approval_id != approval.approval_id
            or reservation.holder_id != fence.holder_id
            or reservation.fencing_epoch != fence.epoch
            or reservation.fencing_token != fence.fencing_token):
        raise ValueError("export reservation does not match the approved plan and current fencing lease")
    if any((receipt.plan_digest != plan.plan_digest
            or receipt.approval_id != approval.approval_id
            or receipt.idempotency_key != plan.idempotency_key
            or receipt.reservation_id != reservation.reservation_id
            or receipt.format not in plan.formats)
           for receipt in reservation.artifact_receipts):
        raise ValueError("reservation contains a receipt outside the approved plan")
    receipt_formats = [receipt.format for receipt in reservation.artifact_receipts]
    if len(receipt_formats) != len(set(receipt_formats)):
        raise ValueError("reservation contains duplicate artifact receipts for one format")
    if format not in plan.formats:
        raise ValueError("requested format is outside the reviewed export plan")
    if any(receipt.format is format for receipt in reservation.artifact_receipts):
        raise ValueError("this format already has a committed artifact; return its receipt instead of rendering again")
    if (render_permit not in reservation.render_permits
            or render_permit.format is not format
            or render_permit.holder_id != fence.holder_id
            or render_permit.fencing_epoch != fence.epoch
            or render_permit.fencing_token != fence.fencing_token):
        raise PermissionError("exclusive format render permit for the current fence is required")
    return AuthorizedExportRender(plan, approval, reservation, fence, render_permit, format)


def verify_artifact_receipt(*, command: AuthorizedExportRender,
                            receipt: ArtifactReceipt) -> None:
    """Check adapter evidence; adapters must hash bytes actually persisted."""
    expected = command
    if (receipt.format is not expected.format
            or receipt.plan_digest != expected.plan.plan_digest
            or receipt.approval_id != expected.approval.approval_id
            or receipt.idempotency_key != expected.plan.idempotency_key
            or receipt.reservation_id != expected.reservation.reservation_id
            or receipt.render_permit_id != expected.render_permit.permit_id
            or receipt.holder_id != expected.fence.holder_id
            or receipt.fencing_token != expected.fence.fencing_token
            or receipt.fencing_epoch != expected.fence.epoch):
        raise ValueError("artifact receipt is not bound to the approved, fenced export command")


class ProcurementExportReservationPort(Protocol):
    async def reserve(self, *, plan: ProcurementDocumentExportPlan,
                      approval: ProcurementExportApproval,
                      fence: ProcurementExportFence) -> ExportReservation:
        """Atomically reserve (scope, idempotency key); same digest replays the reservation.

        The adapter verifies persisted human approval, current fencing epoch/token,
        source revision and active selection digest in the same transaction as the
        claim. It grants exclusive per-format render permits atomically. Exact
        replay requires the same approval ID; changed plan digest or approval ID
        conflicts. A replay returns receipts plus only newly acquired permits;
        in-progress formats are not re-granted to another caller. Recovery after
        a lost worker requires a newer fence. Existing immutable receipts may
        carry an older fence epoch, but a receipted format must never be rendered again.
        """
        ...

    async def commit_artifact(self, *, reservation: ExportReservation,
                              receipt: ArtifactReceipt,
                              fence: ProcurementExportFence) -> bool:
        """Atomically persist or exactly replay one receipt under the current fence.

        Receipt uniqueness is (reservation_id, format). Same content digest
        replay is idempotent; a changed digest conflicts. Stale fences never
        append or overwrite receipts. Return false for a stale claim/fence or a
        conflicting receipt digest; never read-then-write.
        """
        ...


class WorkbookArtifactAdapter(Protocol):
    async def render_workbook(self, *, command: AuthorizedExportRender) -> ArtifactReceipt:
        """Render and durably store XLSX bytes; hash those bytes before returning."""
        ...


class PdfArtifactAdapter(Protocol):
    async def render_pdf(self, *, command: AuthorizedExportRender) -> ArtifactReceipt:
        """Render and durably store PDF bytes; hash those bytes before returning."""
        ...


class ProcurementExportApprovalPort(Protocol):
    async def append_if_current(self, *, approval: ProcurementExportApproval,
                                source_revision: str,
                                selection_digest: str) -> bool:
        """Atomically append, or exactly replay, the immutable approval.

        Return true for an identical approval-ID/payload replay. Reuse of the
        same approval ID with a changed actor, evidence, scope, timestamp or
        digest conflicts. Return false when source or active selection evidence
        is no longer current. Implementation must not use read-then-write.
        """
        ...


class ApprovalActorPort(Protocol):
    async def resolve_actor(self, *, actor_id: str,
                            scope: DomainScope) -> ApprovalActorEvidence | None:
        """Resolve trusted authenticated principal and its exact export scope."""
        ...


async def record_export_approval(*, review: ExportReview, approval_id: str,
                                 actor_id: str, approved_at: str,
                                 plan_digest: str,
                                 actors: ApprovalActorPort,
                                 approvals: ProcurementExportApprovalPort) -> ProcurementExportApproval:
    """Append the explicit human decision under current source/selection fencing."""
    plan = review.plan
    if plan is None:
        raise ValueError("blocked export review cannot be approved")
    actor = await actors.resolve_actor(actor_id=actor_id, scope=plan.scope)
    if actor is None or actor.actor_id != actor_id or actor.scope != plan.scope:
        raise PermissionError("approval actor identity or tenant/owner/project scope is not authenticated")
    approval = approve_export_review(
        review=review, approval_id=approval_id, actor=actor,
        approved_at=approved_at, plan_digest=plan_digest,
    )
    if not await approvals.append_if_current(
            approval=approval, source_revision=plan.source_revision,
            selection_digest=plan.selection_digest):
        raise RuntimeError("approval append conflict; source or human selections changed")
    return approval


async def reserve_approved_export(*, review: ExportReview,
                                  approval: ProcurementExportApproval,
                                  fence: ProcurementExportFence,
                                  reservations: ProcurementExportReservationPort) -> ExportReservation:
    """Acquire/replay a durable export claim only for the exact approval and fence."""
    plan = review.plan
    if not review.ready_for_human_review or plan is None:
        raise ValueError("blocked export review cannot be reserved")
    if (approval.actor_kind is not ApprovalActorKind.HUMAN
            or approval.plan_digest != plan.plan_digest or approval.scope != plan.scope):
        raise PermissionError("matching human approval is required before export reservation")
    if fence.resource_key != plan.idempotency_key:
        raise ValueError("fencing lease is for another idempotent export resource")
    reservation = await reservations.reserve(plan=plan, approval=approval, fence=fence)
    if (reservation.idempotency_key != plan.idempotency_key
            or reservation.plan_digest != plan.plan_digest
            or reservation.approval_id != approval.approval_id
            or reservation.holder_id != fence.holder_id
            or reservation.fencing_epoch != fence.epoch
            or reservation.fencing_token != fence.fencing_token):
        raise RuntimeError("reservation adapter returned a different plan or fencing owner")
    if any((receipt.plan_digest != plan.plan_digest
            or receipt.approval_id != approval.approval_id
            or receipt.idempotency_key != plan.idempotency_key
            or receipt.reservation_id != reservation.reservation_id
            or receipt.format not in plan.formats)
           for receipt in reservation.artifact_receipts):
        raise RuntimeError("reservation adapter returned a receipt outside the approved plan")
    receipt_formats = [receipt.format for receipt in reservation.artifact_receipts]
    if len(receipt_formats) != len(set(receipt_formats)):
        raise RuntimeError("reservation adapter returned duplicate receipts for an output format")
    permit_formats = [permit.format for permit in reservation.render_permits]
    if (len(permit_formats) != len(set(permit_formats))
            or any(permit.format not in plan.formats
                   or permit.holder_id != fence.holder_id
                   or permit.fencing_epoch != fence.epoch
                   or permit.fencing_token != fence.fencing_token
                   for permit in reservation.render_permits)
            or set(permit_formats) & set(receipt_formats)):
        raise RuntimeError("reservation adapter returned duplicate, stale or already-receipted render permits")
    return reservation


async def commit_export_artifact(*, command: AuthorizedExportRender,
                                 receipt: ArtifactReceipt,
                                 reservations: ProcurementExportReservationPort) -> None:
    """Commit only an adapter receipt bound to this exact approved, fenced command."""
    verify_artifact_receipt(command=command, receipt=receipt)
    committed = await reservations.commit_artifact(
        reservation=command.reservation, receipt=receipt, fence=command.fence,
    )
    if not committed:
        raise RuntimeError("artifact receipt commit conflict; reconcile export claim and fencing evidence")


def _round_amount(amount: Decimal, policy: CurrencyRoundingPolicy) -> Decimal:
    digits = len(amount.as_tuple().digits)
    with localcontext() as context:
        context.prec = max(28, digits + policy.exponent + 4,
                           amount.adjusted() + policy.exponent + 2)
        return amount.quantize(policy.quantum, rounding=policy.rounding)


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
        return left * right


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    max_adjusted = max(value.adjusted() for value in values)
    min_exponent = min(cast(int, value.as_tuple().exponent) for value in values)
    with localcontext() as context:
        context.prec = max(28, max_adjusted - min_exponent + 1
                           + len(str(len(values))) + 2)
        return sum(values, Decimal("0"))


def _digest(value: object) -> str:
    def encode(item: object) -> object:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, StrEnum):
            return item.value
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: getattr(item, field.name) for field in fields(item)}
        raise TypeError(f"cannot canonically encode {type(item).__name__}")

    body = json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


__all__ = [
    "ApprovalActorEvidence", "ApprovalActorKind", "ArtifactReceipt", "AuthorizedExportRender", "CurrencyRoundingPolicy",
    "CurrencyTotal", "ExportDiagnostic", "ExportReservation", "ExportReview",
    "ProcurementDocumentExportPlan", "ProcurementDocumentFormat", "ProcurementDocumentLine",
    "FormatRenderPermit",
    "ProcurementExportApproval", "ProcurementExportApprovalPort", "ProcurementExportFence",
    "ProcurementExportReservationPort", "ApprovalActorPort", "LINE_TOTAL_ROUNDING_RULE",
    "PdfArtifactAdapter", "WorkbookArtifactAdapter", "approve_export_review",
    "authorize_export_render", "commit_export_artifact", "record_export_approval",
    "reserve_approved_export", "review_procurement_export",
    "verify_artifact_receipt",
]
