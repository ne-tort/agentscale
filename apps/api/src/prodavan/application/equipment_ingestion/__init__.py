"""Typed contracts for equipment ingestion adapters.

This package is deliberately infrastructure-free: adapters may use HTTP, IMAP, or
file transports, but the contracts do not depend on a database, filesystem, or
runtime configuration loader.
"""

from .service import (
    EquipmentIngestionService,
    EquipmentSourceRepository,
    IngestionContextFactory,
    IngestionRunRepository,
    IngestionServiceError,
)
from .contracts import (
    AdapterContext,
    EquipmentIngestionAdapter,
    IMAPIngestionAdapter,
    IngestionBatch,
    IngestionRun,
    IngestionRunState,
    NormalizedOfferSnapshot,
    RawArtifact,
    S4BPriceFileIngestionAdapter,
    S4BWebIngestionAdapter,
    SourceConfig,
    SourceKind,
)

__all__ = [
    "AdapterContext",
    "EquipmentIngestionService",
    "EquipmentSourceRepository",
    "IngestionContextFactory",
    "IngestionRunRepository",
    "IngestionServiceError",
    "EquipmentIngestionAdapter",
    "IMAPIngestionAdapter",
    "IngestionBatch",
    "IngestionRun",
    "IngestionRunState",
    "NormalizedOfferSnapshot",
    "RawArtifact",
    "S4BPriceFileIngestionAdapter",
    "S4BWebIngestionAdapter",
    "SourceConfig",
    "SourceKind",
]
