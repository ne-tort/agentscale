"""MIG-EQ-01 equipment ingestion contracts; persistence wiring is out of scope."""

from .ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    EquipmentIngestionService,
    FreshnessState,
    IngestionRun,
    IngestionRunStatus,
    ProjectionContract,
    RawArtifact,
    SourceAdapter,
    SourceIdentity,
    SupplierIdentity,
)

__all__ = [
    "CatalogOffer",
    "CatalogSnapshot",
    "EquipmentIngestionService",
    "FreshnessState",
    "IngestionRun",
    "IngestionRunStatus",
    "ProjectionContract",
    "RawArtifact",
    "SourceAdapter",
    "SourceIdentity",
    "SupplierIdentity",
]
