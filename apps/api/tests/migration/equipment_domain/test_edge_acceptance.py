"""MIG-EQ-02 edge acceptance cases; intentionally not run during freeze."""

from decimal import Decimal

import pytest

from prodavan.application.migration.equipment_domain.domain import (
    AssociationBackfill,
    HumanSelection,
    active_human_selections,
)
from .acceptance_fixtures import EVIDENCE, REQUEST, SCOPE, TENDER_SAME_PN_A


def test_jsonb_association_backfill_rejects_invalid_shape_and_cross_project() -> None:
    with pytest.raises(ValueError):
        AssociationBackfill.from_jsonb(
            [{"id": "line-a", "candidate_group_ids": {"not": "an array"}}],
            id_field="id", association_field="candidate_group_ids", kind="candidate_group",
        )
    with pytest.raises(ValueError, match="crosses tenant"):
        AssociationBackfill.from_jsonb(
            [{"id": "line-a", "tenant_id": "tenant-a", "owner_id": "owner-a",
              "project_id": "project-b", "candidate_group_ids": ["group-a"]}],
            id_field="id", association_field="candidate_group_ids", kind="candidate_group",
            expected_scope=SCOPE,
        )


def test_supersession_history_selects_one_active_human_decision() -> None:
    initial = HumanSelection("employee-a", "line-a", "tender-a", "initial", "selection-a",
                             "digest", "tenant-a", "owner-a", "project-a",
                             supplier_id="supplier-a", source_id="source-a",
                             source_record_id="record-a", source_schema_version="v1")
    replacement = HumanSelection("employee-a", "line-a", "tender-b", "replace", "selection-b",
                                 "digest", "tenant-a", "owner-a", "project-a", "selection-a",
                                 supplier_id="supplier-a", source_id="source-a",
                                 source_record_id="record-b", source_schema_version="v1")
    assert active_human_selections((replacement, initial)) == (replacement,)
    cross_actor = HumanSelection("employee-b", "line-a", "tender-b", "replace", "selection-c",
                                 "digest", "tenant-a", "owner-a", "project-a", "selection-a",
                                 supplier_id="supplier-a", source_id="source-a",
                                 source_record_id="record-b", source_schema_version="v1")
    with pytest.raises(ValueError, match="cross-actor"):
        active_human_selections((initial, cross_actor))


def test_request_and_offer_inputs_require_timezone_and_uppercase_currency() -> None:
    with pytest.raises(ValueError, match="timezone"):
        REQUEST.__class__("bad-request", EVIDENCE, "Pump", "RUB", "2026-11-01T14:00:00")
    with pytest.raises(ValueError, match="uppercase"):
        TENDER_SAME_PN_A.__class__(
            "bad-tender", "line-a", "group-a", EVIDENCE, 0, "supplier-a", "source-a",
            "record-bad", "PN-42", "Pump", "rub", Decimal("10"),
            freshness=TENDER_SAME_PN_A.freshness,
        )
