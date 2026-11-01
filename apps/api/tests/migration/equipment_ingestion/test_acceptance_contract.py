"""MIG-EQ-01 acceptance contract declarations; not run during verification freeze."""

from .acceptance_fixtures import (
    CASES,
    COMPLETE_SOURCE,
    INCOMPLETE_SOURCE,
    UNVERIFIED_SUPPLIER,
    VERIFIED_SUPPLIER,
)
from prodavan.application.migration.equipment_ingestion.ingestion import (
    EquipmentIngestionService,
    FreshnessState,
    IngestionRunStatus,
)


def test_acceptance_fixtures_cover_required_contracts() -> None:
    assert len(CASES) >= 15
    assert COMPLETE_SOURCE.complete
    assert not INCOMPLETE_SOURCE.complete
    assert VERIFIED_SUPPLIER.complete
    assert not UNVERIFIED_SUPPLIER.complete
    assert IngestionRunStatus.BLOCKED.value == "blocked"
    assert FreshnessState.STALE.value == "stale"
    assert EquipmentIngestionService.__name__ == "EquipmentIngestionService"
