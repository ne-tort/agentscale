"""Source tests for equipment ingestion application orchestration.

These tests use only in-memory doubles and the transport-neutral contracts.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from prodavan.application.equipment_ingestion import (
    EquipmentIngestionService,
    IngestionBatch,
    IngestionRun,
    IngestionRunState,
    IngestionServiceError,
    NormalizedOfferSnapshot,
    RawArtifact,
    SourceConfig,
    SourceKind,
)

NOW = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def source() -> SourceConfig:
    return SourceConfig(
        id="s4b-1",
        tenant_id="tenant-1",
        kind=SourceKind.S4B_WEB,
        display_name="S4B",
        endpoint="https://supplier.example",
    )


def run(*, state=IngestionRunState.RUNNING, artifact_ids=("artifact-1",), count=1) -> IngestionRun:
    return IngestionRun(
        id="run-1",
        tenant_id="tenant-1",
        source_id="s4b-1",
        started_at=NOW,
        finished_at=NOW if state is not IngestionRunState.RUNNING else None,
        state=state,
        artifact_ids=artifact_ids,
        snapshot_count=count,
        error_message="failed" if state is IngestionRunState.FAILED else None,
    )


def artifact() -> RawArtifact:
    return RawArtifact(
        id="artifact-1",
        run_id="run-1",
        source_id="s4b-1",
        captured_at=NOW,
        media_type="text/csv",
        byte_size=1,
        sha256="a" * 64,
    )


def offer() -> NormalizedOfferSnapshot:
    return NormalizedOfferSnapshot(
        id="offer-1",
        run_id="run-1",
        artifact_id="artifact-1",
        source_id="s4b-1",
        external_key="sku-1",
        supplier_name="Supplier",
        title="Rack server",
        unit_price=Decimal("10"),
        currency="RUB",
    )


class SourceRepo:
    async def get_source(self, source_id):
        return source() if source_id == "s4b-1" else None


class RunRepo:
    def __init__(self):
        self.calls = []

    async def start_run(self, source, *, started_at):
        self.calls.append(("start", source.id))
        return run()

    async def claim_run(self, run_id):
        self.calls.append(("claim", run_id))
        return run()

    async def complete_run(self, run_id, batch, *, finished_at):
        self.calls.append(("complete", run_id, batch))
        return batch.run.model_copy(update={"state": IngestionRunState.SUCCEEDED, "finished_at": finished_at})

    async def fail_run(self, run_id, *, error_code, error_message, finished_at):
        self.calls.append(("fail", run_id, error_code, error_message))
        return run(state=IngestionRunState.FAILED, artifact_ids=(), count=0).model_copy(
            update={"finished_at": finished_at, "error_code": error_code, "error_message": error_message}
        )


class Adapter:
    kind = SourceKind.S4B_WEB

    async def ingest(self, source, run, context):
        artifact_value = artifact()
        return IngestionBatch(run=run, artifacts=(artifact_value,), offers=(offer(),))


@pytest.fixture
def service():
    repo = RunRepo()
    service = EquipmentIngestionService(
        sources=SourceRepo(),
        runs=repo,
        adapters={SourceKind.S4B_WEB: Adapter()},
        context_factory=lambda _source: _context(),
        clock=lambda: NOW,
    )
    service.repo = repo
    return service


async def _context():
    return object()


@pytest.mark.asyncio
async def test_ingest_orchestrates_lifecycle_and_complete(service):
    result = await service.ingest("s4b-1")
    assert result.state is IngestionRunState.SUCCEEDED
    assert [call[0] for call in service.repo.calls] == ["start", "claim", "complete"]


def test_validate_batch_rejects_snapshot_without_artifact(service):
    invalid_offer = offer().model_copy(update={"artifact_id": "missing"})
    batch = IngestionBatch.model_construct(run=run(), artifacts=(artifact(),), offers=(invalid_offer,))
    with pytest.raises(IngestionServiceError, match="does not reference"):
        service.validate_batch(run(), batch)


def test_validate_batch_rejects_run_identity_mismatch(service):
    batch = IngestionBatch(run=run(), artifacts=(artifact(),), offers=(offer(),))
    with pytest.raises(IngestionServiceError, match="does not match"):
        service.validate_batch(run().model_copy(update={"id": "other-run"}), batch)


@pytest.mark.asyncio
async def test_ingest_persists_adapter_failure(service):
    class BrokenAdapter(Adapter):
        async def ingest(self, source, run, context):
            raise RuntimeError("upstream timeout")

    service._adapters[SourceKind.S4B_WEB] = BrokenAdapter()
    result = await service.ingest("s4b-1")
    assert result.state is IngestionRunState.FAILED
    assert service.repo.calls[-1][0:3] == ("fail", "run-1", "INGESTION_FAILED")
