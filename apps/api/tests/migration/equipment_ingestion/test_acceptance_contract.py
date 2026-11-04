"""Behavioral MIG-EQ-01 acceptance tests; intentionally not run during freeze."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from prodavan.application.migration.equipment_ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    EquipmentIngestionService,
    FreshnessState,
    IngestionRun,
    IngestionRunStatus,
    PageCommitResult,
    ProjectionRebuilder,
    RawArtifact,
    SourceIdentity,
    SourcePage,
    SupplierIdentity,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
SOURCE = SourceIdentity("source-a", "supplier-feed", "v1", "owner-a", "tenant-a")
SUPPLIER = SupplierIdentity("supplier-a", "feed-key", "Vendor", True)


def page(raw: bytes, *, cursor=None, next_cursor=None, records=()):
    import hashlib

    return SourcePage(cursor=cursor, next_cursor=next_cursor, records=records,
                      observed_at=NOW,
                      raw_digest="sha256:" + hashlib.sha256(raw).hexdigest())


def valid_offer(*, snapshot_id: str, artifact_id: str, key: str = "row") -> CatalogOffer:
    return CatalogOffer(
        offer_identity("source-a", "supplier-a", key), snapshot_id,
        "supplier-a", "source-a", key, NOW, "RUB", "1.00", "in_stock", {},
        offer_provenance_digest(source_id="source-a", supplier_id="supplier-a",
                                source_record_key=key, artifact_id=artifact_id,
                                observed_at=NOW), artifact_id,
    )


class Evidence:
    """In-memory fake of the atomic evidence boundary."""

    def __init__(self):
        self.run = None
        self.artifacts = {}
        self.snapshots = {}
        self.failures = []
        self.last_good = None
        self.concurrent_run = None
        self.force_page_result: PageCommitResult | None = None

    async def load_run(self, run_id):
        return self.run

    async def commit_page(self, *, artifact, payload, snapshot, run, expected_checkpoint):
        if self.force_page_result is not None:
            result, self.force_page_result = self.force_page_result, None
            return result
        current = self.run.checkpoint_cursor if self.run else None
        if artifact.artifact_id in self.artifacts:
            if (self.artifacts[artifact.artifact_id] == payload
                    and self.snapshots.get(snapshot.snapshot_id) == snapshot):
                return PageCommitResult.REPLAYED
            return PageCommitResult.CONFLICT
        if current != expected_checkpoint:
            return PageCommitResult.CHECKPOINT_CONFLICT
        # Simulate a single transaction after all comparisons pass.
        self.artifacts[artifact.artifact_id] = payload
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.run = run
        if snapshot.accepted:
            self.last_good = snapshot
        return PageCommitResult.COMMITTED

    async def mark_source_failure(self, source_id, *, at, reason):
        self.failures.append((source_id, reason))
        if self.concurrent_run is not None:
            self.run = self.concurrent_run
            self.concurrent_run = None

    async def record_run_failure(self, *, run, expected_checkpoint):
        current = self.run.checkpoint_cursor if self.run else None
        if current != expected_checkpoint:
            return False
        self.run = run
        return True

    async def read_last_good(self, source_id):
        return self.last_good if self.last_good and self.last_good.source_id == source_id else None


class Adapter:
    supplier_identity = SUPPLIER

    def __init__(self, source=SOURCE, *, raw=b"page", rows=({"key": "row"},),
                 next_cursor=None, broken=False):
        self.source = source
        self.raw = raw
        self.rows = rows
        self.next_cursor = next_cursor
        self.broken = broken
        self.cursors = []

    async def identity(self):
        return self.source

    async def fetch_page(self, *, cursor, limit):
        self.cursors.append(cursor)
        if self.broken:
            raise TimeoutError("source unavailable")
        return page(self.raw, cursor=cursor, next_cursor=self.next_cursor, records=self.rows)

    async def fetch_raw_artifact(self, *, cursor, limit):
        return self.raw


class Normalizer:
    def normalize(self, record, *, source, supplier, snapshot_id, observed_at, artifact_id):
        key = record["key"]
        return valid_offer(snapshot_id=snapshot_id, artifact_id=artifact_id, key=key)


@pytest.mark.asyncio
async def test_identity_is_required_before_positive_evidence():
    evidence = Evidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    adapter = Adapter(SourceIdentity(None, "feed", "v1", None, None))
    result = await service.ingest(adapter=adapter, run_id="run", now=NOW)
    assert result.status is IngestionRunStatus.BLOCKED
    assert evidence.artifacts == evidence.snapshots == {}


@pytest.mark.asyncio
async def test_supplier_identity_is_separate_and_verified():
    evidence = Evidence()
    adapter = Adapter()
    adapter.supplier_identity = SupplierIdentity("supplier-a", "feed-key", "Brand", False)
    result = await EquipmentIngestionService(evidence=evidence, normalizer=Normalizer()).ingest(
        adapter=adapter, run_id="run", now=NOW)
    assert result.status is IngestionRunStatus.BLOCKED
    assert result.error_code == "canonical_supplier_identity_incomplete"
    assert evidence.artifacts == evidence.snapshots == {}


@pytest.mark.asyncio
async def test_resume_uses_checkpoint_and_each_snapshot_has_exact_page_rows():
    evidence = Evidence()
    first_adapter = Adapter(raw=b"a", rows=({"key": "a"},), next_cursor="c1")
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer(),
                                        page_size=1, max_pages_per_call=1)
    partial = await service.ingest(adapter=first_adapter, run_id="run", now=NOW)
    assert partial.status is IngestionRunStatus.PARTIAL
    assert partial.checkpoint_cursor == "c1"
    assert len(evidence.snapshots[partial.snapshot_ids[0]].offers) == 1

    resumed = Adapter(raw=b"b", rows=({"key": "b"},))
    final = await service.ingest(adapter=resumed, run_id="run", now=NOW)
    assert final.status is IngestionRunStatus.SUCCEEDED
    assert resumed.cursors == ["c1"]
    assert [evidence.snapshots[item].offers[0].source_record_key
            for item in final.snapshot_ids] == ["a", "b"]


@pytest.mark.asyncio
async def test_page_commit_conflict_persists_nothing_and_keeps_checkpoint():
    evidence = Evidence()
    evidence.force_page_result = PageCommitResult.CHECKPOINT_CONFLICT
    adapter = Adapter()
    result = await EquipmentIngestionService(evidence=evidence, normalizer=Normalizer()).ingest(
        adapter=adapter, run_id="run", now=NOW)
    assert result.status is IngestionRunStatus.BLOCKED
    assert result.error_code == "checkpoint_cas_conflict"
    assert evidence.artifacts == evidence.snapshots == {}
    assert evidence.run is None


@pytest.mark.asyncio
async def test_source_failure_preserves_last_good_and_returns_stale():
    evidence = Evidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    good = await service.ingest(adapter=Adapter(), run_id="good", now=NOW)
    last_good, freshness = await service.record_failure(source_id="source-a", reason="timeout", at=NOW)
    assert good.status is IngestionRunStatus.SUCCEEDED
    assert last_good is not None and last_good.freshness is FreshnessState.STALE
    assert freshness is FreshnessState.STALE
    assert evidence.snapshots[good.snapshot_ids[0]].freshness is FreshnessState.FRESH

    no_history, unknown = await EquipmentIngestionService(
        evidence=Evidence(), normalizer=Normalizer()).record_failure(
            source_id="source-a", reason="timeout", at=NOW)
    assert no_history is None
    assert unknown is FreshnessState.UNKNOWN


def test_normalizer_rejects_bad_currency_availability_and_provenance():
    with pytest.raises(ValueError, match="currency"):
        replace(valid_offer(snapshot_id="snap", artifact_id="art"), currency="ZZZ")
    with pytest.raises(ValueError, match="availability"):
        replace(valid_offer(snapshot_id="snap", artifact_id="art"), availability="on_order")
    with pytest.raises(ValueError, match="provenance"):
        CatalogOffer("offer", "snap", "supplier-a", "source-a", "row", NOW,
                     "RUB", "1.00", "in_stock", {}, "bad", "art")
    # The rejection is about registry membership, not about a narrow allow-list
    # of the currencies this migration happens to handle today: an assigned code
    # the product never quotes must still be storable evidence.
    for code in ("USD", "EUR", "KZT", "CNY", "AED"):
        assert replace(valid_offer(snapshot_id="snap", artifact_id="art"),
                       currency=code).currency == code
    # ...and shape alone is not membership (``ZZZ`` is shaped like a code).
    for code in ("ZZZ", "rub", "Rub", "RUBB", "", None):
        with pytest.raises(ValueError, match="currency"):
            replace(valid_offer(snapshot_id="snap", artifact_id="art"), currency=code)


def test_offer_identity_separates_source_and_supplier():
    assert offer_identity("source-a", "supplier-a", "row") != offer_identity(
        "source-b", "supplier-a", "row")
    assert offer_identity("source-a", "supplier-a", "row") != offer_identity(
        "source-a", "supplier-b", "row")


@pytest.mark.asyncio
async def test_projection_rebuild_is_deterministic_versioned_and_fenced():
    accepted = CatalogSnapshot(
        "snapshot-a", "tenant-a", "owner-a", "source-a", "supplier-a", "run-a",
        "artifact-a", "v1", "revision-a", NOW, FreshnessState.FRESH,
        (valid_offer(snapshot_id="snapshot-a", artifact_id="artifact-a"),),
        evidence_digest("snapshot-a"), True,
    )

    class ProjectionEvidence:
        def __init__(self):
            self.snapshots = (accepted,)
            self.generation = 0
            self.published = None
            self.mutate_after_read = False

        async def accepted_snapshots(self, *, source_ids=None):
            return self.snapshots

        async def publish_projection(self, projection, *, expected_generation,
                                     expected_source_digest, source_ids=None):
            if self.generation != expected_generation:
                return False
            if self.mutate_after_read:
                self.snapshots = (*self.snapshots, replace(
                    accepted, snapshot_id="snapshot-b", content_digest=evidence_digest("new"),
                    offers=(valid_offer(snapshot_id="snapshot-b", artifact_id="artifact-a"),)))
                self.mutate_after_read = False
            current_digest = evidence_digest([
                {"snapshot_id": item.snapshot_id, "content_digest": item.content_digest}
                for item in sorted(self.snapshots, key=lambda item: item.snapshot_id)
            ])
            if current_digest != expected_source_digest:
                return False
            self.generation = projection.contract.generation
            self.published = projection
            return True

    evidence = ProjectionEvidence()
    rebuilder = ProjectionRebuilder(source=evidence, projection_name="catalog",
                                    projection_version="1")
    first = await rebuilder.rebuild(expected_generation=0, rebuilt_at=NOW)
    assert first.contract.generation == 1
    assert first.contract.deterministic
    assert first.contract.document_count == 1
    assert evidence.generation == 1

    evidence.mutate_after_read = True
    with pytest.raises(RuntimeError, match="projection_generation_conflict"):
        await rebuilder.rebuild(expected_generation=1, rebuilt_at=NOW)
    assert evidence.generation == 1
    assert evidence.published == first
