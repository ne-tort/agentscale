"""MIG-EQ-02 acceptance declarations; intentionally not executed during freeze."""

from decimal import Decimal

from .acceptance_fixtures import (
    CASES,
    EVIDENCE,
    GROUP,
    INCOMPLETE_EVIDENCE,
    LINE,
    REQUEST,
    TENDER_SAME_PN_A,
    TENDER_SAME_PN_B,
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
        [{"id": "line-a", "candidate_group_ids": ["g-2", "g-1", "g-2"]}],
        id_field="id", association_field="candidate_group_ids", kind="candidate_group",
    )
    assert result.ordered_edges() == (
        ("line-a", "candidate_group", 0, "g-2"),
        ("line-a", "candidate_group", 1, "g-1"),
        ("line-a", "candidate_group", 2, "g-2"),
    )


def test_deterministic_projection_keeps_supplier_source_identity() -> None:
    projection = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP],
        tenders=[TENDER_SAME_PN_B, TENDER_SAME_PN_A],
    )
    assert projection.status is EquipmentDomainStatus.READY
    assert [item.tender_id for item in projection.tenders] == ["tender-a", "tender-b"]
    replay = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP],
        tenders=[TENDER_SAME_PN_A, TENDER_SAME_PN_B],
    )
    assert projection.contract.input_digest == replay.contract.input_digest


def test_human_only_decision_and_export_contract() -> None:
    projection = build_projection(
        requests=[REQUEST], lines=[LINE], candidate_groups=[GROUP], tenders=[TENDER_SAME_PN_A],
    )
    selected = select_tender(
        projection=projection, actor_id="employee-a", line_id="line-a",
        tender_id="tender-a", reason="Reviewed supplier terms",
    )
    export = prepare_procurement_export(
        projection=projection, selections=[selected], actor_id="employee-a", export_id="export-a",
    )
    assert export.tender_ids == ("tender-a",)
    assert export.status == "prepared"


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
