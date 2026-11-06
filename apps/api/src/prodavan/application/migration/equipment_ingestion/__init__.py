"""MIG-EQ-01 equipment ingestion contracts; persistence wiring is out of scope."""

from .ingestion import (
    CatalogOffer,
    CatalogProjection,
    CatalogSnapshot,
    EquipmentIngestionService,
    FreshnessState,
    IngestionEvidencePort,
    IngestionRun,
    IngestionRunStatus,
    PageCommitResult,
    ProjectionContract,
    ProjectionRebuildPort,
    ProjectionRebuilder,
    QuarantinedRow,
    RawArtifact,
    SourceAdapter,
    SourceIdentity,
    SourcePage,
    SupplierIdentity,
    build_catalog_projection,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)

__all__ = [
    "CatalogOffer", "CatalogProjection", "CatalogSnapshot", "EquipmentIngestionService",
    "FreshnessState", "IngestionEvidencePort", "IngestionRun", "IngestionRunStatus",
    "PageCommitResult", "ProjectionContract", "ProjectionRebuildPort", "ProjectionRebuilder",
    "QuarantinedRow", "RawArtifact", "SourceAdapter", "SourceIdentity", "SourcePage",
    "SupplierIdentity", "build_catalog_projection", "evidence_digest", "offer_identity",
    "offer_provenance_digest",
]
