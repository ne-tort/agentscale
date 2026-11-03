"""MIG-EQ-02 domain acceptance checks; intentionally not run during freeze."""

from decimal import Decimal

import pytest

from .acceptance_fixtures import (
    CASES,
    EVIDENCE,
    GROUP,
    INCOMPLETE_EVIDENCE,
    LINE,
    SCOPE,
    REQUEST,
    TENDER_SAME_PN_A,
    TENDER_SAME_PN_B,
    STALE_TENDER,
    ON_ORDER_TENDER,
)
from prodavan.application.migration.equipment_domain.domain import (
    AssociationBackfill,
    EquipmentDomainStatus,
    build_projection,
    prepare_procurement_export,
    select_tender,
)


def test_acceptance_fixtures_cover_required_contracts() -> None:
    assert len(CASES) >= 16
    assert EVIDENCE.complete
    assert not INCOMPLETE_EVIDENCE.complete
    assert TENDER_SAME_PN_A.part_number == TENDER_SAME_PN_B.part_number
    assert (TENDER_SAME_PN_A.supplier_id, TENDER_SAME_PN_A.source_id) != (
        TENDER_SAME_PN_B.supplier_id, TENDER_SAME_PN_B.source_id
    )
    assert Decimal("2") == LINE.quantity


def test_ordered_jsonb_backfill_contract() -> None:
    result = AssociationBackfill.from_jsonb(
        [{"id": "line-a", "tenant_id": "tenant-a", "owner_id": "owner-a",
          "project_id": "project-a", "candidate_group_ids": ["g-2", "g-1", "g-2"]}],
        id_field="id", association_field="candidate_group_ids", kind="candidate_group", expected_scope=SCOPE,
    )
    assert result.ordered_edges() == (
        ("line-a", "candidate_group", 0, "g-2"),
        ("line-a", "candidate_group", 1, "g-1"),
        ("line-a", "candidate_group", 2, "g-2"),
    )


def test_deterministic_projection_keeps_supplier_source_identity() -> None:
    projection = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP],
        tenders=[TENDER_SAME_PN_B, TENDER_SAME_PN_A], expected_scope=SCOPE,
    )
    assert projection.status is EquipmentDomainStatus.READY
    assert [item.tender_id for item in projection.tenders] == ["tender-a", "tender-b"]
    replay = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP],
        tenders=[TENDER_SAME_PN_A, TENDER_SAME_PN_B], expected_scope=SCOPE,
    )
    assert projection.contract.input_digest == replay.contract.input_digest


def test_human_only_decision_and_export_contract() -> None:
    projection = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP], tenders=[TENDER_SAME_PN_A], expected_scope=SCOPE,
    )
    selected = select_tender(
        projection=projection, actor_id="employee-a", line_id="line-a",
        tender_id="tender-a", reason="Reviewed supplier terms", selection_id="selection-a",
    )
    export = prepare_procurement_export(
        projection=projection, selections=[selected], actor_id="employee-a", export_id="export-a",
    )
    assert export.tender_ids == ("tender-a",)
    assert export.status == "prepared"
    assert export.lines[0].supplier_id == "supplier-a"
    assert export.lines[0].source_record_id == "record-a"
    assert export.lines[0].quantity == LINE.quantity
    assert export.lines[0].total == Decimal("200")


def test_missing_owner_source_evidence_fails_closed() -> None:
    incomplete_line = LINE.__class__(
        LINE.line_id, LINE.request_id, INCOMPLETE_EVIDENCE, LINE.ordinal,
        LINE.description, LINE.quantity, LINE.unit, LINE.part_number,
    )
    projection = build_projection(
        requests=[REQUEST], lines=[incomplete_line], candidate_groups=[GROUP], tenders=[TENDER_SAME_PN_A],
    )
    assert projection.status is EquipmentDomainStatus.BLOCKED
    assert projection.lines == ()
    assert "line_scope_or_evidence_missing" in projection.blocked_reasons


def test_stale_or_on_order_tenders_cannot_be_selected_or_exported() -> None:
    projection = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP],
        tenders=[TENDER_SAME_PN_A, STALE_TENDER, ON_ORDER_TENDER], expected_scope=SCOPE,
    )
    assert projection.status is EquipmentDomainStatus.READY
    assert not next(row for row in projection.budgets if row.tender_id == STALE_TENDER.tender_id).eligible_for_procurement
    assert not next(row for row in projection.budgets if row.tender_id == ON_ORDER_TENDER.tender_id).eligible_for_procurement
    with pytest.raises(ValueError, match="already-on-order"):
        select_tender(projection=projection, actor_id="employee-a", line_id="line-a",
                      tender_id=ON_ORDER_TENDER.tender_id, reason="reviewed", selection_id="s-on-order")


def test_selection_requires_candidate_group_membership() -> None:
    invalid_group = GROUP.__class__("other-group", "other-line", GROUP.evidence,
                                    GROUP.ordinal, GROUP.match_kind, GROUP.part_number)
    projection = build_projection(requests=[REQUEST], lines=[LINE], candidate_groups=[invalid_group],
                                 tenders=[TENDER_SAME_PN_A], expected_scope=SCOPE)
    assert projection.status is EquipmentDomainStatus.BLOCKED
    assert "tender_association_missing_or_cross_line" in projection.blocked_reasons
