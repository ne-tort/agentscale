"""Executable MIG-EQ-01 acceptance tests; intentionally unexecuted during freeze.

All doubles are in-memory and never access Commerce, a database, filesystem, or
an environment file.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.migration.equipment_ingestion import (
    CatalogOffer,
    CatalogProjection,
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
    build_catalog_projection,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
SOURCE = SourceIdentity("source-a", "supplier-feed", "v1", "owner-a", "tenant-a")
SUPPLIER = SupplierIdentity("supplier-a", "feed-vendor-7", "Vendor", True)


def page(*, cursor: str | None, next_cursor: str | None,
         records: tuple[object, ...], raw: bytes,
         observed_at: datetime = NOW) -> SourcePage:
    return SourcePage(
        cursor=cursor,
        next_cursor=next_cursor,
        records=records,
        observed_at=observed_at,
        raw_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def normalized_offer(*, snapshot_id: str, artifact_id: str, key: str,
                     observed_at: datetime = NOW, currency: str = "RUB",
                     availability: str = "in_stock") -> CatalogOffer:
    return CatalogOffer(
        offer_id=offer_identity(SOURCE.source_id or "", SUPPLIER.supplier_id or "", key),
        snapshot_id=snapshot_id,
        supplier_id=SUPPLIER.supplier_id or "",
        source_id=SOURCE.source_id or "",
        source_record_key=key,
        observed_at=observed_at,
        currency=currency,
        price="125.00",
        availability=availability,
        attributes={"title": key},
        provenance_digest=offer_provenance_digest(
            source_id=SOURCE.source_id or "",
            supplier_id=SUPPLIER.supplier_id or "",
            source_record_key=key,
            artifact_id=artifact_id,
            observed_at=observed_at,
        ),
        artifact_id=artifact_id,
    )


class Adapter:
    supplier_identity = SUPPLIER

    def __init__(self, pages: dict[str | None, SourcePage], raw: dict[str | None, bytes]):
        self.pages = pages
        self.raw = raw
        self.calls: list[str | None] = []

    async def identity(self) -> SourceIdentity:
        return SOURCE

    async def fetch_page(self, *, cursor: str | None, limit: int) -> SourcePage:
        self.calls.append(cursor)
        assert limit >= len(self.pages[cursor].records)
        return self.pages[cursor]

    async def fetch_raw_artifact(self, *, cursor: str | None, limit: int) -> bytes:
        return self.raw[cursor]


class FailingAdapter(Adapter):
    def __init__(self, *, source: SourceIdentity = SOURCE, supplier: SupplierIdentity = SUPPLIER,
                 broken: bool = True):
        self._source = source
        self.supplier_identity = supplier
        self.broken = broken
        self.calls: list[str | None] = []

    async def identity(self) -> SourceIdentity:
        return self._source

    async def fetch_page(self, *, cursor: str | None, limit: int) -> SourcePage:
        self.calls.append(cursor)
        if self.broken:
            if len(self.calls) == 1:
                raise TimeoutError("source timeout")
            return page(raw=b"retry-page", cursor=cursor, next_cursor=None,
                       records=({"key": "retry"},))
        return page(raw=b"retry-page", cursor=cursor, next_cursor=None,
                    records=({"key": "retry"},))

    async def fetch_raw_artifact(self, *, cursor: str | None, limit: int) -> bytes:
        return b"retry-page"


class Normalizer:
    def normalize(self, record, *, source, supplier, snapshot_id, observed_at, artifact_id):
        return normalized_offer(
            snapshot_id=snapshot_id,
            artifact_id=artifact_id,
            key=record["key"],
            observed_at=observed_at,
            currency=record.get("currency", "RUB"),
            availability=record.get("availability", "in_stock"),
        )


class MixedNormalizer(Normalizer):
    def normalize(self, record, *, source, supplier, snapshot_id, observed_at, artifact_id):
        if record.get("malformed"):
            raise ValueError("unsupported source row")
        return super().normalize(record, source=source, supplier=supplier,
                                 snapshot_id=snapshot_id, observed_at=observed_at,
                                 artifact_id=artifact_id)


class WrongSourceNormalizer(Normalizer):
    def normalize(self, record, *, source, supplier, snapshot_id, observed_at, artifact_id):
        return replace(super().normalize(record, source=source, supplier=supplier,
                                         snapshot_id=snapshot_id, observed_at=observed_at,
                                         artifact_id=artifact_id), source_id="other-source")


class MemoryEvidence:
    def __init__(self):
        self.runs: dict[str, IngestionRun] = {}
        self.artifacts: dict[str, bytes] = {}
        self.snapshots: dict[str, CatalogSnapshot] = {}
        self.failure_calls: list[tuple[str, str]] = []
        self.concurrent_run: IngestionRun | None = None
        self.force_result: PageCommitResult | None = None

    async def load_run(self, run_id: str) -> IngestionRun | None:
        return self.runs.get(run_id)

    async def commit_page(self, *, artifact, payload, snapshot, run,
                          expected_checkpoint: str | None) -> PageCommitResult:
        if self.force_result is not None:
            result, self.force_result = self.force_result, None
            return result
        existing = self.artifacts.get(artifact.artifact_id)
        if existing is not None:
            old_snapshot = self.snapshots.get(snapshot.snapshot_id)
            old_run = self.runs.get(run.run_id)
            if (existing == payload and old_snapshot == snapshot and old_run is not None
                    and artifact.artifact_id in old_run.artifact_ids
                    and snapshot.snapshot_id in old_run.snapshot_ids
                    and old_run.source == run.source and old_run.supplier == run.supplier):
                return PageCommitResult.REPLAYED
            return PageCommitResult.CONFLICT
        current = self.runs.get(run.run_id)
        current_checkpoint = current.checkpoint_cursor if current else None
        if current_checkpoint != expected_checkpoint:
            return PageCommitResult.CHECKPOINT_CONFLICT
        if "sha256:" + hashlib.sha256(payload).hexdigest() != artifact.content_digest:
            return PageCommitResult.CONFLICT
        if (snapshot.artifact_id, snapshot.run_id, snapshot.source_id) != (
                artifact.artifact_id, artifact.run_id, artifact.source_id):
            return PageCommitResult.CONFLICT
        if artifact.byte_length != len(payload):
            return PageCommitResult.CONFLICT
        # The in-memory adapter models a transaction: state changes only after
        # all replay and checkpoint checks have passed.
        self.artifacts[artifact.artifact_id] = payload
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.runs[run.run_id] = run
        return PageCommitResult.COMMITTED

    async def record_run_failure(self, *, run: IngestionRun,
                                 expected_checkpoint: str | None) -> bool:
        current = self.runs.get(run.run_id)
        current_checkpoint = current.checkpoint_cursor if current else None
        if current_checkpoint != expected_checkpoint:
            return False
        if current is not None and (
                current.page_count, current.record_count, current.artifact_ids, current.snapshot_ids
        ) != (run.page_count, run.record_count, run.artifact_ids, run.snapshot_ids):
            return False
        self.runs[run.run_id] = run
        return True

    async def mark_source_failure(self, source_id: str, *, at: datetime, reason: str) -> None:
        self.failure_calls.append((source_id, reason))
        if self.concurrent_run is not None:
            self.runs[self.concurrent_run.run_id] = self.concurrent_run
            self.concurrent_run = None

    async def read_last_good(self, source_id: str) -> CatalogSnapshot | None:
        accepted = [snapshot for snapshot in self.snapshots.values()
                    if snapshot.source_id == source_id and snapshot.accepted]
        return max(accepted, key=lambda snapshot: (snapshot.observed_at, snapshot.snapshot_id)) if accepted else None


class MemoryProjection:
    def __init__(self, snapshots: tuple[CatalogSnapshot, ...]):
        self.snapshots = snapshots
        self.generation = 0
        self.projection: CatalogProjection | None = None
        self.reject_publish = False
        self.add_snapshot_after_read: CatalogSnapshot | None = None

    async def accepted_snapshots(self, *, source_ids: tuple[str, ...] | None = None
                                 ) -> tuple[CatalogSnapshot, ...]:
        if source_ids is None:
            return self.snapshots
        return tuple(snapshot for snapshot in self.snapshots if snapshot.source_id in source_ids)

    async def publish_projection(self, projection: CatalogProjection, *,
                                 expected_generation: int,
                                 expected_source_digest: str,
                                 source_ids: tuple[str, ...] | None = None) -> bool:
        if self.reject_publish or self.generation != expected_generation:
            return False
        if self.add_snapshot_after_read is not None:
            self.snapshots = (*self.snapshots, self.add_snapshot_after_read)
            self.add_snapshot_after_read = None
        current_snapshots = self.snapshots
        if source_ids is not None:
            current_snapshots = tuple(snapshot for snapshot in current_snapshots
                                      if snapshot.source_id in source_ids)
        current_digest = evidence_digest([
            {"snapshot_id": snapshot.snapshot_id, "content_digest": snapshot.content_digest}
            for snapshot in sorted(current_snapshots, key=lambda item: item.snapshot_id)
        ])
        if current_digest != expected_source_digest:
            return False
        self.projection = projection
        self.generation = projection.contract.generation
        return True


@pytest.mark.asyncio
async def test_pages_resume_from_atomic_checkpoint_and_keep_exact_page_offers():
    raw_a, raw_b = b"page-a", b"page-b"
    adapter = Adapter(
        {None: page(cursor=None, next_cursor="c1", records=({"key": "a"},), raw=raw_a),
         "c1": page(cursor="c1", next_cursor=None, records=({"key": "b"},), raw=raw_b)},
        {None: raw_a, "c1": raw_b},
    )
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer(),
                                        page_size=1, max_pages_per_call=1)

    partial = await service.ingest(adapter=adapter, run_id="run-1", now=NOW)
    assert partial.status is IngestionRunStatus.PARTIAL
    assert partial.checkpoint_cursor == "c1"
    assert evidence.runs["run-1"] == partial
    assert len(evidence.snapshots[partial.snapshot_ids[0]].offers) == 1

    result = await service.ingest(adapter=adapter, run_id="run-1", now=NOW)
    assert result.status is IngestionRunStatus.SUCCEEDED
    assert adapter.calls == [None, "c1"]
    assert [offer.source_record_key for offer in evidence.snapshots[result.snapshot_ids[0]].offers] == ["a"]
    assert [offer.source_record_key for offer in evidence.snapshots[result.snapshot_ids[1]].offers] == ["b"]
    assert len(evidence.snapshots[result.snapshot_ids[1]].offers) == 1


@pytest.mark.asyncio
async def test_malformed_rows_are_quarantined_without_losing_valid_rows():
    raw = b"mixed-page"
    adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "accepted"}, {"malformed": True}), raw=raw)},
                      {None: raw})
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=MixedNormalizer(), page_size=2,
    ).ingest(adapter=adapter, run_id="run-mixed", now=NOW)
    snapshot = evidence.snapshots[result.snapshot_ids[0]]

    assert result.status is IngestionRunStatus.SUCCEEDED
    assert result.observed_count == 2
    assert result.record_count == 1
    assert result.quarantined_count == 1
    assert snapshot.offers[0].currency == "RUB"
    assert snapshot.quarantined_rows[0].error_code == "normalization_invalid"
    assert snapshot.quarantined_rows[0].record_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_checkpoint_conflict_leaves_artifact_snapshot_and_run_unmodified():
    raw = b"conflict-page"
    adapter = Adapter({None: page(cursor=None, next_cursor=None, records=({"key": "a"},), raw=raw)},
                      {None: raw})
    evidence = MemoryEvidence()
    evidence.force_result = PageCommitResult.CHECKPOINT_CONFLICT
    result = await EquipmentIngestionService(evidence=evidence, normalizer=Normalizer()).ingest(
        adapter=adapter, run_id="run-conflict", now=NOW)

    assert result.status is IngestionRunStatus.BLOCKED
    assert result.error_code == "checkpoint_cas_conflict"
    assert evidence.runs == {}
    assert evidence.artifacts == {}
    assert evidence.snapshots == {}


@pytest.mark.asyncio
async def test_page_replay_is_idempotent_and_changed_bytes_conflict():
    original = b"replay-page"
    adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "a"},), raw=original)},
                      {None: original})
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    first = await service.ingest(adapter=adapter, run_id="run-replay", now=NOW)
    artifact_id = first.artifact_ids[0]
    snapshot_id = first.snapshot_ids[0]

    exact_replay = await service.ingest(adapter=adapter, run_id="run-replay", now=NOW)
    assert exact_replay == first
    assert len(evidence.artifacts) == len(evidence.snapshots) == 1

    original_snapshot_digest = evidence.snapshots[snapshot_id].content_digest
    changed = b"changed-page"
    changed_adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                          records=({"key": "a"},), raw=changed)},
                              {None: changed})
    collision = await service.ingest(adapter=changed_adapter, run_id="run-replay", now=NOW)
    assert collision.status is IngestionRunStatus.BLOCKED
    assert collision.error_code == "immutable_page_replay_conflict"
    assert evidence.artifacts[artifact_id] == original
    assert evidence.snapshots[snapshot_id].content_digest == original_snapshot_digest
    assert len(evidence.snapshots) == len(evidence.artifacts) == 1


@pytest.mark.asyncio
async def test_last_good_remains_available_and_freshness_becomes_stale_or_unknown():
    raw = b"good"
    adapter = Adapter({None: page(cursor=None, next_cursor=None, records=({"key": "a"},), raw=raw)},
                      {None: raw})
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    await service.ingest(adapter=adapter, run_id="run-good", now=NOW)
    last_good, state = await service.record_failure(
        source_id=SOURCE.source_id or "", reason="timeout", at=NOW + timedelta(hours=1))

    assert last_good is not None
    assert state is FreshnessState.STALE
    assert last_good.freshness is FreshnessState.STALE
    assert evidence.snapshots[last_good.snapshot_id].freshness is FreshnessState.FRESH

    empty_evidence = MemoryEvidence()
    unknown, unknown_state = await EquipmentIngestionService(
        evidence=empty_evidence, normalizer=Normalizer()).record_failure(
            source_id=SOURCE.source_id or "", reason="timeout", at=NOW)
    assert unknown is None
    assert unknown_state is FreshnessState.UNKNOWN


@pytest.mark.asyncio
async def test_source_failure_records_failed_run_without_replacing_last_good():
    good_raw = b"good"
    good_adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                       records=({"key": "good"},), raw=good_raw)},
                           {None: good_raw})
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    good = await service.ingest(adapter=good_adapter, run_id="run-good", now=NOW)
    failed = await service.ingest(adapter=FailingAdapter(), run_id="run-failed", now=NOW)

    assert failed.status is IngestionRunStatus.FAILED
    assert failed.error_code == "source_page_fetch_failed"
    assert evidence.runs["run-failed"] == failed
    last_good = await evidence.read_last_good(SOURCE.source_id or "")
    assert last_good is not None
    assert last_good.snapshot_id == good.snapshot_ids[0]
    assert evidence.failure_calls == [(SOURCE.source_id or "", "source_page_fetch_failed")]


@pytest.mark.asyncio
async def test_outage_after_checkpoint_retries_and_keeps_last_good_snapshot_immutable():
    raw_first = b"checkpoint-good"
    first_adapter = Adapter(
        {None: page(cursor=None, next_cursor="c1", records=({"key": "good"},), raw=raw_first)},
        {None: raw_first},
    )
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(), page_size=1, max_pages_per_call=1,
    )
    partial = await service.ingest(adapter=first_adapter, run_id="run-outage", now=NOW)
    assert partial.status is IngestionRunStatus.PARTIAL
    assert partial.checkpoint_cursor == "c1"
    accepted_before_outage = evidence.snapshots[partial.snapshot_ids[0]]

    retry_raw = b"checkpoint-retry"
    retry_page = page(cursor="c1", next_cursor=None,
                      records=({"key": "recovered"},), raw=retry_raw,
                      observed_at=NOW)

    class FlakyContinuationAdapter(Adapter):
        def __init__(self):
            super().__init__({"c1": retry_page}, {"c1": retry_raw})
            self.failed_once = False

        async def fetch_page(self, *, cursor, limit):
            self.calls.append(cursor)
            if not self.failed_once:
                self.failed_once = True
                raise TimeoutError("source unavailable after checkpoint")
            return self.pages[cursor]

    flaky = FlakyContinuationAdapter()
    failed = await service.ingest(adapter=flaky, run_id="run-outage", now=NOW)
    assert failed.status is IngestionRunStatus.FAILED
    assert failed.checkpoint_cursor == "c1"
    stale, freshness = await service.record_failure(
        source_id=SOURCE.source_id or "", reason="source_page_fetch_failed", at=NOW,
    )
    assert stale is not None and stale.snapshot_id == accepted_before_outage.snapshot_id
    assert stale.freshness is freshness is FreshnessState.STALE
    assert evidence.snapshots[accepted_before_outage.snapshot_id] == accepted_before_outage

    recovered = await service.ingest(adapter=flaky, run_id="run-outage", now=NOW)
    assert recovered.status is IngestionRunStatus.SUCCEEDED
    assert flaky.calls == ["c1", "c1"]
    assert len(recovered.snapshot_ids) == 2
    assert evidence.snapshots[recovered.snapshot_ids[0]] == accepted_before_outage
    assert evidence.snapshots[recovered.snapshot_ids[1]].offers[0].source_record_key == "recovered"


def test_source_and_supplier_dimensions_remain_distinct_in_offer_identity():
    first = offer_identity("source-a", "supplier-a", "record-1")
    different_supplier = offer_identity("source-a", "supplier-b", "record-1")
    different_source = offer_identity("source-b", "supplier-a", "record-1")
    assert len({first, different_supplier, different_source}) == 3


@pytest.mark.asyncio
async def test_ingested_row_provenance_binds_identity_and_exact_raw_artifact():
    raw = b"supplier-record-source-row"
    adapter = Adapter(
        {None: page(cursor=None, next_cursor=None,
                    records=({"key": "source-record-17"},), raw=raw)},
        {None: raw},
    )
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(),
    ).ingest(adapter=adapter, run_id="row-provenance-run", now=NOW)
    snapshot = evidence.snapshots[result.snapshot_ids[0]]
    offer = snapshot.offers[0]

    assert result.status is IngestionRunStatus.SUCCEEDED
    assert offer.source_id == snapshot.source_id == SOURCE.source_id
    assert offer.supplier_id == snapshot.supplier_id == SUPPLIER.supplier_id
    assert offer.source_record_key == "source-record-17"
    assert offer.artifact_id == snapshot.artifact_id == result.artifact_ids[0]
    assert offer.provenance_digest == offer_provenance_digest(
        source_id=SOURCE.source_id or "",
        supplier_id=SUPPLIER.supplier_id or "",
        source_record_key="source-record-17",
        artifact_id=result.artifact_ids[0],
        observed_at=NOW,
    )
    assert evidence.artifacts[result.artifact_ids[0]] == raw
    assert snapshot.content_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_normalizer_source_reference_mismatch_is_quarantined():
    raw = b"bad-reference"
    adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "bad"},), raw=raw)}, {None: raw})
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=WrongSourceNormalizer(),
    ).ingest(adapter=adapter, run_id="run-reference", now=NOW)
    snapshot = evidence.snapshots[result.snapshot_ids[0]]
    assert result.record_count == 0
    assert result.quarantined_count == 1
    assert snapshot.offers == ()
    assert snapshot.quarantined_rows[0].error_code == "source_supplier_reference_mismatch"
    assert not snapshot.accepted


def test_normalizer_contract_rejects_invalid_currency_availability_and_refs():
    with pytest.raises(ValueError, match="currency"):
        CatalogOffer("offer", "snapshot", "supplier-a", "source-a", "row", NOW,
                     "ruble", "1", "in_stock", {}, evidence_digest("p"), "artifact")
    with pytest.raises(ValueError, match="availability"):
        CatalogOffer("offer", "snapshot", "supplier-a", "source-a", "row", NOW,
                     "RUB", "1", "on_order", {}, evidence_digest("p"), "artifact")
    with pytest.raises(ValueError, match="source/artifact"):
        CatalogOffer("offer", "snapshot", "supplier-a", "source-a", "row", NOW,
                     "RUB", "1", "in_stock", {}, evidence_digest("p"), "")
    with pytest.raises(ValueError, match="provenance"):
        CatalogOffer("offer", "snapshot", "supplier-a", "source-a", "row", NOW,
                     "RUB", "1", "in_stock", {}, "bad", "artifact")


def _snapshot(snapshot_id: str, key: str, observed_at: datetime = NOW) -> CatalogSnapshot:
    artifact_id = f"artifact-{snapshot_id}"
    offer = normalized_offer(snapshot_id=snapshot_id, artifact_id=artifact_id,
                             key=key, observed_at=observed_at)
    return CatalogSnapshot(
        snapshot_id, "tenant-a", "owner-a", "source-a", "supplier-a", "run-a",
        artifact_id, "v1", "revision-a", observed_at, FreshnessState.FRESH,
        (offer,), evidence_digest(snapshot_id), True,
    )


def test_projection_rebuild_is_deterministic_and_versioned_from_accepted_pages():
    snapshots = (_snapshot("snapshot-b", "row-b", NOW + timedelta(minutes=1)),
                 _snapshot("snapshot-a", "row-a", NOW))
    first = build_catalog_projection(snapshots=snapshots, projection_name="equipment",
                                     projection_version="1", generation=1, rebuilt_at=NOW)
    replay = build_catalog_projection(snapshots=tuple(reversed(snapshots)),
                                      projection_name="equipment", projection_version="1",
                                      generation=1, rebuilt_at=NOW + timedelta(minutes=2))

    assert first.contract.source_digest == replay.contract.source_digest
    assert first.offers == replay.offers
    assert first.contract.document_count == 2
    assert first.contract.source_snapshot_ids == ("snapshot-a", "snapshot-b")
    assert first.contract.deterministic is True


@pytest.mark.asyncio
async def test_raw_digest_mismatch_stops_before_any_positive_append():
    raw = b"actual-page"
    bad_page = SourcePage(None, None, ({"key": "a"},), NOW,
                          raw_digest="sha256:" + hashlib.sha256(b"claimed-page").hexdigest())
    adapter = Adapter({None: bad_page}, {None: raw})
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(),
    ).ingest(adapter=adapter, run_id="run-digest-mismatch", now=NOW)

    assert result.status is IngestionRunStatus.FAILED
    assert result.error_code == "page_raw_digest_mismatch"
    assert evidence.artifacts == {}
    assert evidence.snapshots == {}


@pytest.mark.asyncio
async def test_cursor_cycle_and_page_limit_fail_without_checkpoint_advance():
    raw_first, raw_second = b"first", b"second"
    adapter = Adapter(
        {None: page(cursor=None, next_cursor="c1", records=({"key": "a"},), raw=raw_first),
         "c1": page(cursor="c1", next_cursor="c1", records=({"key": "b"},), raw=raw_second)},
        {None: raw_first, "c1": raw_second},
    )
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer(),
                                        page_size=1, max_pages_per_call=3)
    result = await service.ingest(adapter=adapter, run_id="run-cycle", now=NOW)

    assert result.status is IngestionRunStatus.FAILED
    assert result.error_code == "source_cursor_did_not_advance"
    assert result.checkpoint_cursor == "c1"
    assert result.page_count == 1
    assert len(evidence.artifacts) == len(evidence.snapshots) == 1


@pytest.mark.asyncio
async def test_failure_run_checkpoint_cas_does_not_overwrite_concurrent_run():
    raw = b"checkpoint-page"
    adapter = Adapter({None: page(cursor=None, next_cursor="c1",
                                  records=({"key": "a"},), raw=raw)}, {None: raw})
    evidence = MemoryEvidence()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer(),
                                        page_size=1, max_pages_per_call=1)
    run = await service.ingest(adapter=adapter, run_id="run-failure-cas", now=NOW)
    concurrent = replace(run, checkpoint_cursor="c2", status=IngestionRunStatus.RUNNING,
                         error_code=None)
    evidence.concurrent_run = concurrent

    failure = await service.ingest(adapter=FailingAdapter(), run_id="run-failure-cas", now=NOW)
    assert failure.status is IngestionRunStatus.BLOCKED
    assert failure.error_code == "checkpoint_cas_conflict"
    assert evidence.runs["run-failure-cas"] == concurrent


@pytest.mark.asyncio
async def test_invalid_currency_and_availability_are_quarantined_not_accepted():
    raw = b"invalid-normalization"
    adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "bad-currency", "currency": "rub"},
                                           {"key": "bad-availability", "availability": "on_order"}),
                                  raw=raw)}, {None: raw})
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(), page_size=2,
    ).ingest(adapter=adapter, run_id="run-invalid-values", now=NOW)
    snapshot = evidence.snapshots[result.snapshot_ids[0]]

    assert result.record_count == 0
    assert result.quarantined_count == 2
    assert snapshot.offers == ()
    assert {row.error_code for row in snapshot.quarantined_rows} == {"normalization_invalid"}
    assert snapshot.accepted is False


@pytest.mark.asyncio
async def test_failed_run_retries_from_last_committed_checkpoint():
    evidence = MemoryEvidence()
    adapter = FailingAdapter()
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer())
    failed = await service.ingest(adapter=adapter, run_id="retry-run", now=NOW)
    assert failed.status is IngestionRunStatus.FAILED
    assert failed.checkpoint_cursor is None
    resumed = await service.ingest(adapter=adapter, run_id="retry-run", now=NOW)
    assert resumed.status is IngestionRunStatus.SUCCEEDED
    assert adapter.calls == [None, None]
    assert resumed.record_count == 1
    assert len(evidence.artifacts) == len(evidence.snapshots) == 1


@pytest.mark.asyncio
async def test_changed_source_or_supplier_identity_cannot_reuse_run():
    evidence = MemoryEvidence()
    raw = b"first"
    adapter = Adapter({None: page(cursor=None, next_cursor="c1",
                                  records=({"key": "a"},), raw=raw)}, {None: raw})
    service = EquipmentIngestionService(evidence=evidence, normalizer=Normalizer(),
                                        page_size=1, max_pages_per_call=1)
    partial = await service.ingest(adapter=adapter, run_id="identity-run", now=NOW)
    assert partial.status is IngestionRunStatus.PARTIAL

    changed = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "b"},), raw=b"second")}, {None: b"second"})
    changed.supplier_identity = SupplierIdentity("supplier-b", "feed-key-b", "Other", True)
    result = await service.ingest(adapter=changed, run_id="identity-run", now=NOW)
    assert result.status is IngestionRunStatus.BLOCKED
    assert result.error_code == "run_identity_conflict"
    assert evidence.runs["identity-run"] == partial
    assert len(evidence.artifacts) == len(evidence.snapshots) == 1


@pytest.mark.asyncio
async def test_changed_source_identity_cannot_reuse_existing_run():
    evidence = MemoryEvidence()
    raw = b"first-source"
    adapter = Adapter(
        {None: page(cursor=None, next_cursor="c1", records=({"key": "a"},), raw=raw)},
        {None: raw},
    )
    service = EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(), page_size=1, max_pages_per_call=1,
    )
    partial = await service.ingest(adapter=adapter, run_id="source-identity-run", now=NOW)
    assert partial.status is IngestionRunStatus.PARTIAL

    changed_source = FailingAdapter(
        source=replace(SOURCE, source_id="source-b"), broken=False,
    )
    result = await service.ingest(
        adapter=changed_source, run_id="source-identity-run", now=NOW,
    )
    assert result.status is IngestionRunStatus.BLOCKED
    assert result.error_code == "run_identity_conflict"
    assert changed_source.calls == []
    assert evidence.runs["source-identity-run"] == partial
    assert len(evidence.artifacts) == len(evidence.snapshots) == 1


@pytest.mark.asyncio
async def test_raw_artifact_over_limit_is_not_stored():
    raw = b"too-large"
    adapter = Adapter({None: page(cursor=None, next_cursor=None,
                                  records=({"key": "a"},), raw=raw)}, {None: raw})
    evidence = MemoryEvidence()
    result = await EquipmentIngestionService(
        evidence=evidence, normalizer=Normalizer(), max_raw_bytes=4,
    ).ingest(adapter=adapter, run_id="run-raw-limit", now=NOW)

    assert result.status is IngestionRunStatus.FAILED
    assert result.error_code == "raw_artifact_limit_exceeded"
    assert evidence.artifacts == {}
    assert evidence.snapshots == {}
