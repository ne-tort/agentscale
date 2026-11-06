"""Contract tests for the isolated equipment migration foundation.

These tests intentionally avoid a database and legacy mod_equipment imports.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from prodavan.domain.equipment import (
    CandidateGroup,
    CandidateGroupStatus,
    CatalogOffer,
    EquipmentRequest,
    EquipmentRequestLine,
    ProcurementTender,
)


def test_request_and_line_are_typed_and_immutable():
    line = EquipmentRequestLine(
        id="line_1",
        request_id="request_1",
        line_number=1,
        title="Rack server",
        quantity=Decimal("2"),
        unit="pcs",
    )
    request = EquipmentRequest(id="request_1", tenant_id="tenant_1", title="Infrastructure", lines=(line,))

    assert request.lines[0].quantity == Decimal("2")
    with pytest.raises(ValidationError):
        request.title = "changed"


def test_candidate_selection_must_reference_group_offer():
    with pytest.raises(ValidationError, match="selected_offer_id"):
        CandidateGroup(
            id="group_1",
            request_line_id="line_1",
            title="Server candidates",
            status=CandidateGroupStatus.SELECTED,
        )

    group = CandidateGroup(
        id="group_1",
        request_line_id="line_1",
        title="Server candidates",
        status=CandidateGroupStatus.SELECTED,
        offer_ids=("offer_1",),
        selected_offer_id="offer_1",
    )
    assert group.selected_offer_id == "offer_1"


def test_tender_rejects_invalid_closing_window():
    with pytest.raises(ValidationError, match="timezone-aware"):
        ProcurementTender(
            id="tender_1",
            tenant_id="tenant_1",
            request_id="request_1",
            title="Infrastructure tender",
            opens_at=datetime(2026, 1, 1),
        )

    opening = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="closes_at"):
        ProcurementTender(
            id="tender_1",
            tenant_id="tenant_1",
            request_id="request_1",
            title="Infrastructure tender",
            opens_at=opening,
            closes_at=opening - timedelta(minutes=1),
        )


def test_offer_requires_nonnegative_price_and_quantity():
    with pytest.raises(ValidationError):
        CatalogOffer(
            id="offer_1",
            snapshot_id="snapshot_1",
            supplier_key="supplier_1",
            external_key="server-1",
            title="Rack server",
            unit_price=Decimal("-1"),
            currency="RUB",
        )
