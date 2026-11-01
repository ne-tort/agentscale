"""Application orchestration for equipment source ingestion.

The service owns lifecycle transitions and provenance checks while repositories and
transport adapters remain replaceable infrastructure boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable, Mapping, Protocol

from .contracts import (
    AdapterContext,
    EquipmentIngestionAdapter,
    IngestionBatch,
    IngestionRun,
    IngestionRunState,
    NormalizedOfferSnapshot,
    RawArtifact,
    SourceConfig,
    SourceKind,
)


class EquipmentSourceRepository(Protocol):
    """Read source configuration without exposing persistence details."""

    async def get_source(self, source_id: str) -> SourceConfig | None: ...


class IngestionRunRepository(Protocol):
    """Persistence boundary for run state and the resulting ingestion batch."""

    async def start_run(self, source: SourceConfig, *, started_at: datetime) -> IngestionRun: ...

    async def claim_run(self, run_id: str) -> IngestionRun | None: ...

    async def complete_run(self, run_id: str, batch: IngestionBatch, *, finished_at: datetime) -> IngestionRun: ...

    async def fail_run(
        self,
        run_id: str,
        *,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> IngestionRun: ...


class IngestionContextFactory(Protocol):
    async def __call__(self, source: SourceConfig) -> AdapterContext: ...


class IngestionServiceError(RuntimeError):
    """Stable application error for orchestration failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EquipmentIngestionService:
    """Start, claim, execute, and finish one source ingestion run."""

    def __init__(
        self,
        *,
        sources: EquipmentSourceRepository,
        runs: IngestionRunRepository,
        adapters: Mapping[SourceKind, EquipmentIngestionAdapter],
        context_factory: IngestionContextFactory,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = sources
        self._runs = runs
        self._adapters = adapters
        self._context_factory = context_factory
        self._clock = clock

    async def start(self, source_id: str) -> IngestionRun:
        source = await self._sources.get_source(source_id)
        if source is None:
            raise IngestionServiceError("SOURCE_NOT_FOUND", f"source not found: {source_id}")
        if not source.enabled:
            raise IngestionServiceError("SOURCE_DISABLED", f"source is disabled: {source_id}")
        return await self._runs.start_run(source, started_at=self._now())

    async def claim(self, run_id: str) -> IngestionRun:
        run = await self._runs.claim_run(run_id)
        if run is None or run.state is not IngestionRunState.RUNNING:
            raise IngestionServiceError("RUN_NOT_CLAIMABLE", f"run is not claimable: {run_id}")
        return run

    async def complete(self, run: IngestionRun, batch: IngestionBatch) -> IngestionRun:
        self._require_running(run)
        self.validate_batch(run, batch)
        return await self._runs.complete_run(run.id, batch, finished_at=self._now())

    async def fail(
        self,
        run: IngestionRun,
        *,
        error_code: str,
        error_message: str,
    ) -> IngestionRun:
        self._require_running(run)
        if not error_code.strip() or not error_message.strip():
            raise ValueError("error_code and error_message are required")
        return await self._runs.fail_run(
            run.id,
            error_code=error_code,
            error_message=error_message,
            finished_at=self._now(),
        )

    async def ingest(self, source_id: str) -> IngestionRun:
        """Run one complete ingestion, persisting failure before returning it."""
        source = await self._sources.get_source(source_id)
        if source is None:
            raise IngestionServiceError("SOURCE_NOT_FOUND", f"source not found: {source_id}")
        if not source.enabled:
            raise IngestionServiceError("SOURCE_DISABLED", f"source is disabled: {source_id}")
        run = await self._runs.start_run(source, started_at=self._now())
        claimed = await self._runs.claim_run(run.id)
        if claimed is None:
            raise IngestionServiceError("RUN_NOT_CLAIMABLE", f"run is not claimable: {run.id}")
        adapter = self._adapters.get(source.kind)
        if adapter is None or adapter.kind is not source.kind:
            return await self.fail(
                claimed,
                error_code="ADAPTER_NOT_CONFIGURED",
                error_message=f"no compatible adapter for {source.kind.value}",
            )
        try:
            batch = await adapter.ingest(source, claimed, await self._context_factory(source))
            return await self.complete(claimed, batch)
        except Exception as exc:
            return await self.fail(claimed, error_code="INGESTION_FAILED", error_message=str(exc) or exc.__class__.__name__)

    @staticmethod
    def _require_running(run: IngestionRun) -> None:
        if run.state is not IngestionRunState.RUNNING:
            raise IngestionServiceError("RUN_NOT_RUNNING", f"run is not running: {run.id}")

    def validate_batch(self, run: IngestionRun, batch: IngestionBatch) -> None:
        """Validate adapter output against the claimed run's identity and provenance."""
        if batch.run.id != run.id:
            raise IngestionServiceError("RUN_MISMATCH", "batch run does not match claimed run")
        if batch.run.state is not IngestionRunState.RUNNING:
            raise IngestionServiceError("RUN_STATE_MISMATCH", "batch must retain the claimed running state")
        if batch.run.tenant_id != run.tenant_id:
            raise IngestionServiceError("TENANT_MISMATCH", "batch tenant does not match claimed run")
        if batch.run.source_id != run.source_id:
            raise IngestionServiceError("SOURCE_MISMATCH", "batch source does not match claimed run")
        artifact_ids = {artifact.id for artifact in batch.artifacts}
        if len(artifact_ids) != len(batch.artifacts):
            raise IngestionServiceError("DUPLICATE_ARTIFACT", "batch contains duplicate artifact ids")
        offer_ids = {offer.id for offer in batch.offers}
        if len(offer_ids) != len(batch.offers):
            raise IngestionServiceError("DUPLICATE_SNAPSHOT", "batch contains duplicate snapshot ids")
        self._validate_artifacts(run, batch.artifacts)
        self._validate_offers(run, batch.artifacts, batch.offers)

    @staticmethod
    def _validate_artifacts(run: IngestionRun, artifacts: tuple[RawArtifact, ...]) -> None:
        if set(run.artifact_ids) != {artifact.id for artifact in artifacts}:
            raise IngestionServiceError("ARTIFACT_PROVENANCE", "run artifact_ids must match batch artifact identity")
        for artifact in artifacts:
            if artifact.run_id != run.id or artifact.source_id != run.source_id:
                raise IngestionServiceError("ARTIFACT_PROVENANCE", "artifact run/source provenance mismatch")

    @staticmethod
    def _validate_offers(
        run: IngestionRun,
        artifacts: tuple[RawArtifact, ...],
        offers: tuple[NormalizedOfferSnapshot, ...],
    ) -> None:
        artifact_ids = {artifact.id for artifact in artifacts}
        if run.snapshot_count != len(offers):
            raise IngestionServiceError("SNAPSHOT_COUNT_MISMATCH", "run snapshot_count does not match normalized snapshots")
        for offer in offers:
            if offer.run_id != run.id or offer.source_id != run.source_id:
                raise IngestionServiceError("SNAPSHOT_PROVENANCE", "snapshot run/source provenance mismatch")
            if offer.artifact_id not in artifact_ids:
                raise IngestionServiceError("SNAPSHOT_PROVENANCE", "snapshot artifact_id does not reference this batch")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value


__all__ = [
    "EquipmentIngestionService",
    "EquipmentSourceRepository",
    "IngestionContextFactory",
    "IngestionRunRepository",
    "IngestionServiceError",
]
