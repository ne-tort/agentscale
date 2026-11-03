"""MIG-EQ-EXPORT-01 export review acceptance; intentionally not run during freeze."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

import pytest

from prodavan.application.migration.equipment_domain.domain import (
    EquipmentTender,
    HumanSelection,
    OfferFreshness,
    ProcurementExport,
    build_projection,
    prepare_procurement_export,
    select_tender,
)
from prodavan.application.migration.equipment_domain.export import (
    ApprovalActorEvidence,
    ApprovalActorKind,
    CurrencyRoundingPolicy,
    ExportReservation,
    FormatRenderPermit,
    LINE_TOTAL_ROUNDING_RULE,
    ProcurementDocumentFormat,
    ProcurementExportFence,
    approve_export_review,
    authorize_export_render,
    record_export_approval,
    reserve_approved_export,
    review_procurement_export,
)
from .acceptance_fixtures import EVIDENCE, GROUP, LINE, REQUEST, SCOPE, SNAPSHOT, TENDER_SAME_PN_A


RUB = CurrencyRoundingPolicy("RUB", 2, ROUND_HALF_UP)
XLSX = ProcurementDocumentFormat.XLSX
PDF = ProcurementDocumentFormat.PDF


def _review(*, projection=None, selections=None, export=None, policies=(RUB,), formats=(XLSX, PDF)):
    projection = projection or build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE, source_revision=SNAPSHOT.source_revision,
        source_generation=1,
    )
    selections = tuple(selections if selections is not None else (
        select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                      tender_id="tender-a", reason="Checked supplier quote", selection_id="selection-a"),
    ))
    export = export or prepare_procurement_export(
        projection=projection, selections=selections, actor_id="employee-a", export_id="export-a",
    )
    return review_procurement_export(
        projection=projection, prepared_export=export, selections=selections,
        scope=SCOPE, actor_id="employee-a", formats=formats, rounding_policies=policies,
    )


def test_review_snapshot_is_deterministic_and_preserves_currency_and_provenance() -> None:
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
        tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE, source_revision="revision-7",
        source_generation=1,
    )
    selection = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                              tender_id="tender-a", reason="Checked quote", selection_id="selection-a")
    export = prepare_procurement_export(projection=projection, selections=(selection,),
                                        actor_id="employee-a", export_id="export-a")
    first = _review(projection=projection, selections=(selection,), export=export)
    replay = _review(projection=projection, selections=(selection,), export=export)

    assert first.ready_for_human_review
    assert first.plan == replay.plan
    assert first.plan is not None
    assert first.plan.projection_digest == projection.contract.input_digest
    assert first.plan.source_revision == "revision-7"
    assert first.plan.source_schema_versions == ("v1",)
    assert first.plan.lines[0].currency == "RUB"
    assert first.plan.lines[0].supplier_id == "supplier-a"
    assert first.plan.lines[0].source_id == "source-a"
    assert first.plan.lines[0].source_record_id == "record-a"
    assert first.plan.lines[0].selection_id == "selection-a"
    assert first.plan.currency_totals[0].rounded_total == Decimal("200.00")
    assert first.plan.rounding_rule == LINE_TOTAL_ROUNDING_RULE
    assert first.plan.human_approval_required


def test_unselected_line_blocks_the_whole_document_plan() -> None:
    line_b = replace(LINE, line_id="line-b", ordinal=1, description="Second pump")
    group_b = replace(GROUP, group_id="group-b", line_id="line-b", ordinal=1)
    tender_b = replace(TENDER_SAME_PN_A, tender_id="tender-b", line_id="line-b",
                       group_id="group-b", source_record_id="record-b", ordinal=1)
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE, line_b), candidate_groups=(GROUP, group_b),
        tenders=(TENDER_SAME_PN_A, tender_b), expected_scope=SCOPE, source_revision="revision-7",
        source_generation=1,
    )
    selected = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                             tender_id="tender-a", reason="Reviewed", selection_id="selection-a")
    export = prepare_procurement_export(projection=projection, selections=(selected,),
                                        actor_id="employee-a", export_id="export-a")
    review = _review(projection=projection, selections=(selected,), export=export)

    assert review.plan is None
    assert any(item.code == "unselected_request_line" and item.subject_id == "line-b"
               for item in review.diagnostics)


@pytest.mark.parametrize(
    ("freshness", "on_order", "expected_code"),
    ((OfferFreshness.STALE, False, "stale_selected_offer"),
     (OfferFreshness.FRESH, True, "on_order_offer")),
)
def test_stale_or_on_order_selected_offer_blocks_plan(freshness, on_order, expected_code) -> None:
    tender = replace(TENDER_SAME_PN_A, freshness=freshness, on_order=on_order)
    projection = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                  tenders=(tender,), expected_scope=SCOPE, source_revision="revision-7", source_generation=1)
    selection = HumanSelection(
        "employee-a", "line-a", tender.tender_id, "Legacy or concurrent decision", "selection-a",
        projection.contract.input_digest, SCOPE.tenant_id, SCOPE.owner_id, SCOPE.project_id,
        supplier_id=tender.supplier_id, source_id=tender.source_id,
        source_record_id=tender.source_record_id, source_schema_version="v1",
    )
    prepared = ProcurementExport(
        "export-a", "employee-a", "sha256:" + "0" * 64, (tender.tender_id,),
        "prepared", (selection.selection_id,), (), (), SCOPE.tenant_id, SCOPE.owner_id,
        SCOPE.project_id, projection.contract.input_digest,
    )
    review = _review(projection=projection, selections=(selection,), export=prepared)

    assert review.plan is None
    assert any(item.code == expected_code for item in review.diagnostics)


def test_unknown_freshness_is_not_exportable_without_current_provenance() -> None:
    tender = replace(TENDER_SAME_PN_A, freshness=OfferFreshness.UNKNOWN)
    projection = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                  tenders=(tender,), expected_scope=SCOPE, source_revision="revision-7", source_generation=1)
    selection = HumanSelection(
        "employee-a", "line-a", tender.tender_id, "Legacy decision", "selection-a",
        projection.contract.input_digest, SCOPE.tenant_id, SCOPE.owner_id, SCOPE.project_id,
        supplier_id=tender.supplier_id, source_id=tender.source_id,
        source_record_id=tender.source_record_id, source_schema_version="v1",
    )
    prepared = ProcurementExport(
        "export-a", "employee-a", "sha256:" + "0" * 64, (tender.tender_id,),
        "prepared", (selection.selection_id,), (), (), SCOPE.tenant_id, SCOPE.owner_id,
        SCOPE.project_id, projection.contract.input_digest,
    )
    review = _review(projection=projection, selections=(selection,), export=prepared)

    assert review.plan is None
    assert any(item.code == "offer_freshness_unknown" for item in review.diagnostics)


def test_outdated_projection_and_cross_scope_selection_are_diagnostics() -> None:
    projection = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                  tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE,
                                  source_revision="revision-8", source_generation=2)
    selection = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                              tender_id="tender-a", reason="Reviewed", selection_id="selection-a")
    old_projection = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                      tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE,
                                      source_revision="revision-7", source_generation=1)
    old_selection = select_tender(projection=old_projection, actor_id="employee-a", line_id="line-a",
                                  tender_id="tender-a", reason="Reviewed", selection_id="old-selection")
    old_export = prepare_procurement_export(projection=old_projection, selections=(old_selection,),
                                            actor_id="employee-a", export_id="export-a")
    stale_review = _review(projection=projection, selections=(selection,), export=old_export)
    cross_scope = replace(selection, project_id="project-b")
    current_export = prepare_procurement_export(projection=projection, selections=(selection,),
                                                actor_id="employee-a", export_id="export-a")
    scope_review = _review(projection=projection, selections=(cross_scope,), export=current_export)

    assert stale_review.plan is None
    assert any(item.code == "stale_projection" for item in stale_review.diagnostics)
    assert scope_review.plan is None
    assert any(item.code == "selection_scope_mismatch" for item in scope_review.diagnostics)


def test_decimal_rounding_is_explicit_per_currency_and_sums_rounded_lines() -> None:
    line = replace(LINE, quantity=Decimal("1"))
    tender = replace(TENDER_SAME_PN_A, unit_price=Decimal("2.675"))
    projection = build_projection(requests=(REQUEST,), lines=(line,), candidate_groups=(GROUP,),
                                  tenders=(tender,), expected_scope=SCOPE, source_revision="revision-7", source_generation=1)
    selection = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                              tender_id="tender-a", reason="Reviewed", selection_id="selection-a")
    export = prepare_procurement_export(projection=projection, selections=(selection,),
                                        actor_id="employee-a", export_id="rounding-export")
    policy = CurrencyRoundingPolicy("RUB", 2, ROUND_HALF_UP)
    review = _review(projection=projection, selections=(selection,), export=export, policies=(policy,))

    assert review.plan is not None
    assert review.plan.lines[0].raw_total == Decimal("2.675")
    assert review.plan.lines[0].rounded_unit_price == Decimal("2.68")
    assert review.plan.lines[0].rounded_total == Decimal("2.68")
    assert review.plan.currency_totals[0].rounded_total == Decimal("2.68")
    assert review.plan.rounding_policies == (policy,)


def test_missing_or_ambiguous_currency_policy_fails_closed() -> None:
    missing = _review(policies=())
    duplicate = _review(policies=(RUB, RUB))

    assert missing.plan is None
    assert any(item.code == "currency_policy_missing" for item in missing.diagnostics)
    assert duplicate.plan is None
    assert any(item.code == "duplicate_rounding_policy" for item in duplicate.diagnostics)


def test_mixed_currency_snapshot_keeps_currency_totals_separate() -> None:
    line_b = replace(LINE, line_id="line-eur", ordinal=1, description="Euro pump")
    group_b = replace(GROUP, group_id="group-eur", line_id="line-eur", ordinal=1)
    evidence_eur = replace(EVIDENCE, supplier_id="supplier-eur")
    tender_b = EquipmentTender(
        "tender-eur", "line-eur", "group-eur", evidence_eur, 1, "supplier-eur",
        "source-a", "record-eur", "PN-EUR", "Euro pump", "EUR", Decimal("1.005"),
        Decimal("2"), OfferFreshness.FRESH,
    )
    projection = build_projection(
        requests=(REQUEST,), lines=(LINE, line_b), candidate_groups=(GROUP, group_b),
        tenders=(TENDER_SAME_PN_A, tender_b), expected_scope=SCOPE, source_revision="revision-7",
        source_generation=1,
    )
    selection_a = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                                tender_id="tender-a", reason="RUB reviewed", selection_id="selection-a")
    selection_b = select_tender(projection=projection, actor_id="employee-a", line_id="line-eur",
                                tender_id="tender-eur", reason="EUR reviewed", selection_id="selection-b")
    export = prepare_procurement_export(projection=projection,
                                        selections=(selection_b, selection_a),
                                        actor_id="employee-a", export_id="multi-currency")
    review = _review(projection=projection, selections=(selection_a, selection_b), export=export,
                     policies=(RUB, CurrencyRoundingPolicy("EUR", 2, ROUND_HALF_UP)))

    assert review.plan is not None
    assert tuple((total.currency, total.rounded_total) for total in review.plan.currency_totals) == (
        ("EUR", Decimal("2.01")), ("RUB", Decimal("200.00")),
    )
    assert tuple(line.currency for line in review.plan.lines) == ("RUB", "EUR")
    assert review.plan.lines[1].source_schema_version == "v1"


def test_tampered_prepared_export_line_total_is_rejected_as_inconsistent() -> None:
    review = _review()
    assert review.plan is not None
    projection = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                  tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE,
                                  source_revision=SNAPSHOT.source_revision, source_generation=1)
    selection = select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                              tender_id="tender-a", reason="Reviewed", selection_id="selection-a")
    prepared = prepare_procurement_export(projection=projection, selections=(selection,),
                                         actor_id="employee-a", export_id="export-a")
    tampered_line = replace(prepared.lines[0], total=Decimal("199.99"))
    tampered_export = replace(prepared, lines=(tampered_line,))
    tampered_review = _review(projection=projection, selections=(selection,), export=tampered_export)

    assert tampered_review.plan is None
    assert any(item.code == "line_snapshot_inconsistent" for item in tampered_review.diagnostics)


def test_human_approval_is_bound_to_exact_plan_and_renderer_boundary() -> None:
    review = _review(formats=(XLSX,))
    assert review.plan is not None
    with pytest.raises(PermissionError, match="human actor"):
        approve_export_review(review=review, approval_id="approval-a",
                              actor=ApprovalActorEvidence("agent-a", ApprovalActorKind.AGENT,
                                                         SCOPE, "auth-evidence-a"),
                              approved_at="2026-11-02T12:00:00Z", plan_digest=review.plan.plan_digest)
    with pytest.raises(ValueError, match="exact reviewed plan digest"):
        approve_export_review(review=review, approval_id="approval-a",
                              actor=ApprovalActorEvidence("employee-b", ApprovalActorKind.HUMAN,
                                                         SCOPE, "auth-evidence-b"),
                              approved_at="2026-11-02T12:00:00Z", plan_digest="sha256:" + "0" * 64)
    approval = approve_export_review(review=review, approval_id="approval-a",
                                     actor=ApprovalActorEvidence("employee-b", ApprovalActorKind.HUMAN,
                                                                SCOPE, "auth-evidence-b"),
                                     approved_at="2026-11-02T12:00:00Z",
                                     plan_digest=review.plan.plan_digest)
    fence = ProcurementExportFence(review.plan.idempotency_key, "worker-a", 3, "fence-token-a")
    permit = FormatRenderPermit("permit-xlsx", XLSX, fence.holder_id,
                                fence.epoch, fence.fencing_token)
    reservation = ExportReservation("reservation-a", review.plan.idempotency_key,
                                    review.plan.plan_digest, approval.approval_id, fence.holder_id,
                                    fence.epoch, fence.fencing_token,
                                    render_permits=(permit,))
    command = authorize_export_render(review=review, approval=approval,
                                      reservation=reservation, fence=fence,
                                      render_permit=permit, format=XLSX)
    assert command.plan == review.plan
    assert command.approval == approval
    assert command.fence == fence
    assert command.format is XLSX
    with pytest.raises(ValueError, match="another idempotent export resource"):
        authorize_export_render(review=review, approval=approval, reservation=reservation,
                                fence=replace(fence, resource_key="wrong-key"),
                                render_permit=permit, format=XLSX)
    with pytest.raises(ValueError, match="outside the reviewed export plan"):
        authorize_export_render(review=review, approval=approval, reservation=reservation,
                                fence=fence, render_permit=permit, format=PDF)


def test_idempotency_key_is_stable_for_scope_and_export_identity() -> None:
    first = _review()
    changed_source = build_projection(requests=(REQUEST,), lines=(LINE,), candidate_groups=(GROUP,),
                                      tenders=(TENDER_SAME_PN_A,), expected_scope=SCOPE,
                                      source_revision="revision-8", source_generation=2)
    changed_selection = select_tender(projection=changed_source, actor_id="employee-a", line_id="line-a",
                                      tender_id="tender-a", reason="Reviewed", selection_id="selection-a")
    changed_export = prepare_procurement_export(projection=changed_source,
                                                selections=(changed_selection,), actor_id="employee-a",
                                                export_id="export-a")
    second = _review(projection=changed_source, selections=(changed_selection,), export=changed_export)

    assert first.plan is not None and second.plan is not None
    assert first.plan.idempotency_key == second.plan.idempotency_key
    assert first.plan.plan_digest != second.plan.plan_digest


def test_no_artifact_is_created_by_review_or_approval_contract() -> None:
    review = _review()
    assert review.ready_for_human_review
    assert review.plan is not None
    assert not hasattr(review, "artifact")
    assert not hasattr(review.plan, "content")
    assert review.plan.formats == (XLSX, PDF)


class _ApprovalActors:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    async def resolve_actor(self, *, actor_id, scope):
        self.calls.append((actor_id, scope))
        return self.evidence


class _ApprovalStore:
    def __init__(self, accept=True):
        self.accept = accept
        self.approvals = []
        self.fences = []

    async def append_if_current(self, *, approval, source_revision, selection_digest):
        self.fences.append((source_revision, selection_digest))
        if not self.accept:
            return False
        self.approvals.append(approval)
        return True


class _ReservationStore:
    def __init__(self, reservation):
        self.reservation = reservation
        self.calls = []

    async def reserve(self, *, plan, approval, fence):
        self.calls.append((plan, approval, fence))
        return self.reservation

    async def commit_artifact(self, *, reservation, receipt, fence):
        raise AssertionError("review acceptance must not create a fake artifact")


@pytest.mark.asyncio
async def test_record_approval_requires_authenticated_same_scope_actor_and_revision_cas() -> None:
    review = _review()
    assert review.plan is not None
    bad_actors = _ApprovalActors(ApprovalActorEvidence(
        "employee-b", ApprovalActorKind.HUMAN,
        replace(SCOPE, project_id="project-b"), "auth-evidence-b",
    ))
    store = _ApprovalStore()
    with pytest.raises(PermissionError, match="identity or tenant/owner/project scope"):
        await record_export_approval(
            review=review, approval_id="approval-a", actor_id="employee-b",
            approved_at="2026-11-02T12:00:00Z", plan_digest=review.plan.plan_digest,
            actors=bad_actors, approvals=store,
        )
    assert store.approvals == []

    good_actors = _ApprovalActors(ApprovalActorEvidence(
        "employee-b", ApprovalActorKind.HUMAN, SCOPE, "auth-evidence-b",
    ))
    rejecting_store = _ApprovalStore(accept=False)
    with pytest.raises(RuntimeError, match="source or human selections changed"):
        await record_export_approval(
            review=review, approval_id="approval-a", actor_id="employee-b",
            approved_at="2026-11-02T12:00:00Z", plan_digest=review.plan.plan_digest,
            actors=good_actors, approvals=rejecting_store,
        )
    assert rejecting_store.approvals == []
    assert rejecting_store.fences == [(review.plan.source_revision, review.plan.selection_digest)]

    accepting_store = _ApprovalStore()
    approval = await record_export_approval(
        review=review, approval_id="approval-b", actor_id="employee-b",
        approved_at="2026-11-02T12:00:00Z", plan_digest=review.plan.plan_digest,
        actors=good_actors, approvals=accepting_store,
    )
    assert approval.actor_id == "employee-b"
    assert approval.actor_evidence_id == "auth-evidence-b"
    assert accepting_store.approvals == [approval]


@pytest.mark.asyncio
async def test_reservation_is_bound_to_plan_approval_scope_and_current_fence() -> None:
    review = _review()
    assert review.plan is not None
    approval = approve_export_review(
        review=review, approval_id="approval-a",
        actor=ApprovalActorEvidence("employee-b", ApprovalActorKind.HUMAN,
                                    SCOPE, "auth-evidence-b"),
        approved_at="2026-11-02T12:00:00Z", plan_digest=review.plan.plan_digest,
    )
    fence = ProcurementExportFence(review.plan.idempotency_key, "worker-a", 3, "fence-token-a")
    reservation = ExportReservation("reservation-a", review.plan.idempotency_key,
                                    review.plan.plan_digest, approval.approval_id, fence.holder_id,
                                    fence.epoch, fence.fencing_token)
    store = _ReservationStore(reservation)
    result = await reserve_approved_export(review=review, approval=approval,
                                           fence=fence, reservations=store)
    assert result == reservation
    assert store.calls == [(review.plan, approval, fence)]

    with pytest.raises(ValueError, match="another idempotent export resource"):
        await reserve_approved_export(review=review, approval=approval,
                                      fence=replace(fence, epoch=4, resource_key="wrong-key"),
                                      reservations=store)
    assert len(store.calls) == 1

    stale_store = _ReservationStore(replace(reservation, fencing_epoch=2))
    with pytest.raises(RuntimeError, match="different plan or fencing owner"):
        await reserve_approved_export(review=review, approval=approval,
                                      fence=fence, reservations=stale_store)
