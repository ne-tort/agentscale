"""Acceptance contracts for durable equipment ingestion persistence (MIG-EQ-INGEST-04).

Authored during the migration verification freeze and **intentionally not
executed**: every check below is written to be runnable, but the freeze defers
execution to the explicit verification gate.

Coverage map (kept explicit so cross-review does not read it as double coverage):

``persistence.py``-owned behaviour (asserted here, for the first time)
    * typed ``(tenant_id, source_id, source_digest, window)`` run scope and its
      deterministic run key / window prefix lookup key;
    * idempotent ``open()``: created once, resumed afterwards, replayed after a
      completed run, conflicted for a changed digest in the same window, blocked
      for incomplete/foreign identity — with no second run and no rewrite;
    * ``resume()`` checkpoint view and its counter invariants;
    * the terminal discipline: ``succeeded`` only from a committed final page
      whose checkpoint is exhausted, with every offer of that page accounted for
      and zero conflicts; ``partial``/``failed``/``blocked`` otherwise;
    * refusal of a page appended after a completed run (replays stay allowed);
    * the guarded ``partial``/``blocked`` writer, which excludes ``succeeded``
      in its ``WHERE`` clause and introduces no new status value;
    * absence of any ``project_id``/``cabinet_id`` scope, and no DDL.

``durable.py``-owned behaviour (already asserted by the sibling task's own
acceptance work; only *reached through* ``persistence.py`` here)
    * the ``metadata_json`` page-evidence envelope, deterministic snapshot/offer
      row ids and the offer ``$ingestion`` attribute namespace;
    * the single page transaction, ``FOR UPDATE`` run lock and checkpoint CAS;
    * ``ON CONFLICT (snapshot_id, external_key) DO NOTHING`` offer inserts;
    * ``uq_equipment_v2_snapshot_hash`` handling for a repeated content digest.

This module never re-derives the storage encoding: the double below models the
``DurablePageStore`` protocol's *observable outcomes*, not its SQL, and the
source-level checks assert that ``persistence.py`` imports the run key and
window prefix from ``durable`` instead of redefining them.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prodavan.application.migration.equipment_ingestion.durable import (
    IngestionWindow,
    RunAdmission,
    RunAdmissionState,
    run_row_id,
    run_window_prefix,
)
from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogOffer,
    CatalogSnapshot,
    FreshnessState,
    IngestionRun,
    IngestionRunStatus,
    PageCommitOutcome,
    PageCommitResult,
    RawArtifact,
    SourceIdentity,
    SupplierIdentity,
    evidence_digest,
    offer_identity,
    offer_provenance_digest,
)
from prodavan.application.migration.equipment_ingestion.persistence import (
    EquipmentIngestionPersistence,
    IngestionRunScope,
    PageWrite,
    PersistenceError,
    RunCheckpoint,
    RunOpening,
    TERMINAL_RUN_STATUSES,
    decide_page_write,
    terminal_status_statement,
)

TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"
SOURCE_ID = "source-a"
OWNER_ID = "owner-a"
SUPPLIER_ID = "supplier-a"
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)

SOURCE = SourceIdentity(SOURCE_ID, "supplier-feed", "v1", OWNER_ID, TENANT)
SUPPLIER = SupplierIdentity(SUPPLIER_ID, "feed-vendor-7", "Vendor", True)
WINDOW = IngestionWindow(start=NOW - timedelta(days=1), end=NOW)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
PERSISTENCE_PATH = Path(
    "apps/api/src/prodavan/application/migration/equipment_ingestion/persistence.py"
)


# --------------------------------------------------------------------------
# double: the durable store contract, in memory
# --------------------------------------------------------------------------


class DurableRunStore:
    """In-memory model of the ``DurablePageStore`` protocol.

    Mirrors the observable semantics of ``durable.DurableEquipmentIngestionStore``:
    window-scoped admission, immutable page evidence, ``(snapshot_id,
    external_key)`` insert-or-skip, checkpoint CAS, and a run row written by the
    same call that writes the page.

    Nothing here reaches a database, a filesystem, or the network.
    """

    def __init__(self, *, tenant_id: str = TENANT) -> None:
        self.tenant_id = tenant_id
        self.runs: dict[str, IngestionRun] = {}
        self.snapshots: dict[str, CatalogSnapshot] = {}
        self.offers: dict[str, dict[str, tuple[str, ...]]] = {}
        self.admit_calls = 0
        self.commit_calls = 0
        self.succeeded_calls = 0
        self.failed_calls = 0
        self.page_writes = 0
        self.forced_outcome: PageCommitOutcome | None = None
        self.forced_offer_counts: tuple[int, int, int] | None = None

    # -- admission -------------------------------------------------------

    async def admit_run(self, *, source: SourceIdentity, supplier: SupplierIdentity,
                        window: IngestionWindow, source_digest: str,
                        now: datetime | None = None) -> RunAdmission:
        self.admit_calls += 1
        computed = run_row_id(tenant_id=source.tenant_id or "", source_id=source.source_id or "",
                              window=window, source_digest=source_digest)
        prefix = run_window_prefix(tenant_id=source.tenant_id or "",
                                   source_id=source.source_id or "", window=window)
        window_ids = tuple(sorted(run_id for run_id in self.runs if run_id.startswith(prefix)))
        if computed in window_ids:
            status = self._status(computed)
            if status is IngestionRunStatus.SUCCEEDED:
                return RunAdmission(RunAdmissionState.REPLAYS, computed)
            return RunAdmission(RunAdmissionState.RESUMED, computed)
        if window_ids:
            return RunAdmission(RunAdmissionState.CONFLICT, computed,
                                "run_window_digest_conflict")
        return RunAdmission(RunAdmissionState.CREATED, computed)

    async def load_run(self, run_id: str) -> IngestionRun | None:
        run = self.runs.get(run_id)
        if run is None or not run.snapshot_ids:
            # No committed page evidence means no resumable run yet.
            return None
        return run

    # -- page commit -----------------------------------------------------

    async def commit_page(self, *, artifact: RawArtifact, payload: bytes,
                          snapshot: CatalogSnapshot, run: IngestionRun,
                          expected_checkpoint: str | None) -> PageCommitOutcome:
        self.commit_calls += 1
        existing = self.snapshots.get(snapshot.snapshot_id)
        if existing is not None:
            return self._replay_or_conflict(existing=existing, payload=payload,
                                            artifact=artifact, snapshot=snapshot, run=run)
        if self.forced_outcome is not None:
            outcome, self.forced_outcome = self.forced_outcome, None
            if outcome.result is not PageCommitResult.COMMITTED:
                return outcome
        current = self.runs.get(run.run_id)
        stored_checkpoint = current.checkpoint_cursor if current is not None else None
        if stored_checkpoint != expected_checkpoint:
            return PageCommitOutcome(PageCommitResult.CHECKPOINT_CONFLICT, "checkpoint_stale",
                                     offers_conflicted=len(snapshot.offers))
        if run.status is IngestionRunStatus.SUCCEEDED and run.checkpoint_cursor is not None:
            return PageCommitOutcome(PageCommitResult.CONFLICT, "terminal_run_not_exhausted",
                                     offers_conflicted=len(snapshot.offers))
        written = self._insert_offers(snapshot)
        if written is None:
            # ON CONFLICT DO NOTHING skipped a key inside this page.
            return PageCommitOutcome(PageCommitResult.CONFLICT, "offer_external_key_conflict",
                                     offers_conflicted=len(snapshot.offers))
        # The page, its offers and the run row land together.
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.runs[run.run_id] = run
        self.page_writes += 1
        return PageCommitOutcome(PageCommitResult.COMMITTED, "page_committed",
                                 offers_written=written)

    async def mark_run_succeeded(self, *, run_id: str, at: datetime) -> bool:
        self.succeeded_calls += 1
        run = self.runs.get(run_id)
        if run is None or run.source.tenant_id != self.tenant_id:
            return False
        if run.status is IngestionRunStatus.SUCCEEDED:
            return True
        if not run.snapshot_ids or run.checkpoint_cursor is not None:
            return False
        self.runs[run_id] = _with_status(run, IngestionRunStatus.SUCCEEDED, None, at)
        return True

    async def mark_run_failed(self, *, run_id: str, error_code: str, at: datetime) -> bool:
        self.failed_calls += 1
        run = self.runs.get(run_id)
        if run is None or run.source.tenant_id != self.tenant_id:
            return False
        if run.status is IngestionRunStatus.SUCCEEDED:
            return False
        self.runs[run_id] = _with_status(run, IngestionRunStatus.FAILED, error_code, at)
        return True

    # -- inspection ------------------------------------------------------

    def status(self, run_id: str) -> IngestionRunStatus:
        return self.runs[run_id].status

    def window_run_ids(self, *, source_id: str = SOURCE_ID,
                       window: IngestionWindow = WINDOW) -> tuple[str, ...]:
        prefix = run_window_prefix(tenant_id=self.tenant_id, source_id=source_id,
                                   window=window)
        return tuple(sorted(run_id for run_id in self.runs if run_id.startswith(prefix)))

    # -- internals -------------------------------------------------------

    def _status(self, run_id: str) -> IngestionRunStatus:
        return self.runs[run_id].status

    def _fingerprint(self, offer: CatalogOffer) -> tuple[str, ...]:
        return (offer.supplier_id, offer.source_record_key, offer.price or "",
                offer.currency, offer.availability, offer.provenance_digest)

    def _insert_offers(self, snapshot: CatalogSnapshot) -> int | None:
        stored = self.offers.setdefault(snapshot.snapshot_id, {})
        written = 0
        for offer in snapshot.offers:
            if offer.source_record_key in stored:
                # ON CONFLICT DO NOTHING: not inserted, so the caller's count
                # shortfall rolls the page back in the real adapter.
                continue
            stored[offer.source_record_key] = self._fingerprint(offer)
            written += 1
        if written != len(snapshot.offers):
            return None
        return written

    def _replay_or_conflict(self, *, existing: CatalogSnapshot, payload: bytes,
                            artifact: RawArtifact, snapshot: CatalogSnapshot,
                            run: IngestionRun) -> PageCommitOutcome:
        stored_run = self.runs.get(run.run_id)
        identical = (
            existing.content_digest == snapshot.content_digest
            and existing.artifact_id == artifact.artifact_id
            and stored_run is not None
            and existing.snapshot_id in stored_run.snapshot_ids
            and self.offers.get(snapshot.snapshot_id, {}) == {
                offer.source_record_key: self._fingerprint(offer)
                for offer in snapshot.offers
            }
        )
        if identical:
            return PageCommitOutcome(PageCommitResult.REPLAYED, "page_replayed",
                                     offers_replayed=len(snapshot.offers))
        stored = self.offers.get(snapshot.snapshot_id, {})
        for offer in snapshot.offers:
            if (offer.source_record_key in stored
                    and stored[offer.source_record_key] != self._fingerprint(offer)):
                return PageCommitOutcome(PageCommitResult.CONFLICT,
                                         "offer_external_key_conflict",
                                         offers_conflicted=len(snapshot.offers))
        return PageCommitOutcome(PageCommitResult.CONFLICT,
                                 "immutable_page_replay_conflict",
                                 offers_conflicted=len(snapshot.offers))


def _with_status(run: IngestionRun, status: IngestionRunStatus,
                 error_code: str | None, at: datetime) -> IngestionRun:
    return IngestionRun(
        run_id=run.run_id, source=run.source, supplier=run.supplier, status=status,
        checkpoint_cursor=run.checkpoint_cursor, page_count=run.page_count,
        record_count=run.record_count, artifact_ids=run.artifact_ids,
        snapshot_ids=run.snapshot_ids, started_at=run.started_at, updated_at=at,
        error_code=error_code, observed_count=run.observed_count,
        quarantined_count=run.quarantined_count,
    )


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def scope(*, digest: str = DIGEST_A, window: IngestionWindow = WINDOW,
          tenant_id: str = TENANT, source_id: str = SOURCE_ID) -> IngestionRunScope:
    return IngestionRunScope(tenant_id=tenant_id, source_id=source_id,
                             source_digest=digest, window=window)


def offer(*, snapshot_id: str, artifact_id: str, key: str,
          price: str = "125.00", observed_at: datetime = NOW) -> CatalogOffer:
    return CatalogOffer(
        offer_id=offer_identity(SOURCE_ID, SUPPLIER_ID, key),
        snapshot_id=snapshot_id,
        supplier_id=SUPPLIER_ID,
        source_id=SOURCE_ID,
        source_record_key=key,
        observed_at=observed_at,
        currency="RUB",
        price=price,
        availability="in_stock",
        attributes={"title": key},
        provenance_digest=offer_provenance_digest(
            source_id=SOURCE_ID, supplier_id=SUPPLIER_ID, source_record_key=key,
            artifact_id=artifact_id, observed_at=observed_at,
        ),
        artifact_id=artifact_id,
    )


class Page:
    """One page's evidence, bound to a scope and to the run it belongs to."""

    def __init__(self, *, scope: IngestionRunScope, index: int, cursor: str | None,
                 next_cursor: str | None, keys: tuple[str, ...],
                 prior: IngestionRun | None = None,
                 prices: dict[str, str] | None = None,
                 observed_at: datetime = NOW) -> None:
        run_id = scope.run_id
        self.payload = f"page:{index}:{cursor}:{next_cursor}".encode()
        self.artifact = RawArtifact(
            artifact_id=f"{run_id}:artifact:{index}",
            source_id=SOURCE_ID,
            run_id=run_id,
            content_digest="sha256:" + __import__("hashlib").sha256(self.payload).hexdigest(),
            byte_length=len(self.payload),
            media_type="application/octet-stream",
            immutable=True,
            retained_reference=f"immutable://equipment-artifacts/{run_id}:{index}",
        )
        self.snapshot_id = f"{run_id}:snapshot:{index}"
        self.offers = tuple(
            offer(snapshot_id=self.snapshot_id, artifact_id=self.artifact.artifact_id, key=key,
                  price=(prices or {}).get(key, "125.00"), observed_at=observed_at)
            for key in keys
        )
        self.snapshot = CatalogSnapshot(
            snapshot_id=self.snapshot_id,
            tenant_id=scope.tenant_id,
            owner_id=OWNER_ID,
            source_id=SOURCE_ID,
            supplier_id=SUPPLIER_ID,
            run_id=run_id,
            artifact_id=self.artifact.artifact_id,
            schema_version="v1",
            source_revision=None,
            observed_at=observed_at,
            freshness=FreshnessState.FRESH,
            offers=self.offers,
            content_digest=evidence_digest({
                "artifact": self.artifact.content_digest,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "offers": [item.source_record_key for item in self.offers],
            }),
            accepted=bool(self.offers),
            project_id=None,
        )
        status = IngestionRunStatus.SUCCEEDED if next_cursor is None else IngestionRunStatus.RUNNING
        self.run = IngestionRun(
            run_id=run_id,
            source=SOURCE,
            supplier=SUPPLIER,
            status=status,
            checkpoint_cursor=next_cursor,
            page_count=(prior.page_count if prior is not None else 0) + 1,
            record_count=(prior.record_count if prior is not None else 0) + len(self.offers),
            artifact_ids=tuple(prior.artifact_ids if prior is not None else ())
            + (self.artifact.artifact_id,),
            snapshot_ids=tuple(prior.snapshot_ids if prior is not None else ())
            + (self.snapshot_id,),
            started_at=prior.started_at if prior is not None else observed_at,
            updated_at=observed_at,
            observed_count=(prior.observed_count if prior is not None else 0) + len(self.offers),
            quarantined_count=prior.quarantined_count if prior is not None else 0,
        )

    @property
    def expected_checkpoint(self) -> str | None:
        return None if self.run.page_count == 1 else self._prior_checkpoint

    # The caller always knows the cursor it asked the source for; tests set it
    # explicitly through ``expected`` when it differs from "previous page's next".
    _prior_checkpoint: str | None = None


def persistence(store: DurableRunStore, **kwargs) -> EquipmentIngestionPersistence:
    kwargs.setdefault("clock", lambda: NOW)
    return EquipmentIngestionPersistence(store=store, tenant_id=TENANT, **kwargs)


async def open_first_page(*, store: DurableRunStore | None = None,
                          run_scope: IngestionRunScope | None = None,
                          keys: tuple[str, ...] = ("sku-1", "sku-2")):
    """Admit the run and build page 0 for it."""
    store = store or DurableRunStore()
    run_scope = run_scope or scope()
    repository = persistence(store)
    opening = await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=keys)
    return store, repository, run_scope, opening, page


# --------------------------------------------------------------------------
# 1. happy path: one page, exhausted checkpoint, succeeded
# --------------------------------------------------------------------------


async def test_single_page_run_reaches_succeeded_and_persists_offers() -> None:
    run_scope = scope()
    store, repository, run_scope, opening, page = await open_first_page(
        run_scope=run_scope)

    assert opening.state is RunAdmissionState.CREATED
    assert opening.run_id == run_scope.run_id
    assert opening.checkpoint == RunCheckpoint.empty(run_scope.run_id)
    assert not opening.writes_nothing
    assert store.admit_calls == 1

    final = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                 keys=("sku-1", "sku-2"))
    write = await repository.commit_page(
        run_scope, artifact=final.artifact, payload=final.payload,
        snapshot=final.snapshot, run=final.run, expected_checkpoint=None,
    )

    assert write.result is PageCommitResult.COMMITTED
    assert write.durable and write.advanced
    assert write.run_status is IngestionRunStatus.SUCCEEDED
    assert write.terminal and not write.resumable
    assert write.offers_written == 2
    assert write.offers_conflicted == 0
    assert write.checkpoint is not None
    assert write.checkpoint.exhausted and write.checkpoint.terminal
    assert write.checkpoint.page_count == 1
    assert write.checkpoint.accepted == 2
    # The run row and the page were written by one call in the store.
    assert store.status(run_scope.run_id) is IngestionRunStatus.SUCCEEDED
    assert store.snapshots[final.snapshot_id].content_digest == final.snapshot.content_digest
    assert set(store.offers[final.snapshot_id]) == {"sku-1", "sku-2"}
    assert store.page_writes == 1


async def test_empty_final_page_still_succeeds_when_checkpoint_is_exhausted() -> None:
    # A source that ends with a page holding only quarantined rows: no offers,
    # so no accepted snapshot, but the checkpoint is exhausted and the run is done.
    run_scope = scope()
    repository = persistence(DurableRunStore())
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None, keys=())
    write = await repository.commit_page(
        run_scope, artifact=page.artifact, payload=page.payload, snapshot=page.snapshot,
        run=page.run, expected_checkpoint=None,
    )
    assert write.result is PageCommitResult.COMMITTED
    assert write.run_status is IngestionRunStatus.SUCCEEDED
    assert write.offers_written == 0
    assert write.checkpoint is not None and write.checkpoint.exhausted


# --------------------------------------------------------------------------
# 2. resume: the durable cursor is the resume point
# --------------------------------------------------------------------------


async def test_resume_reopens_the_same_run_from_the_durable_checkpoint() -> None:
    store, repository, run_scope, opening, first = await open_first_page()
    assert opening.state is RunAdmissionState.CREATED

    first_write = await repository.commit_page(
        run_scope, artifact=first.artifact, payload=first.payload,
        snapshot=first.snapshot, run=first.run, expected_checkpoint=None,
    )
    assert first_write.result is PageCommitResult.COMMITTED
    # A page that is not the end of the source is never a success.
    assert first_write.run_status is IngestionRunStatus.RUNNING
    assert not first_write.terminal
    assert first_write.checkpoint is not None
    assert first_write.checkpoint.cursor == "c1"
    assert first_write.resumable

    reopened = await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert reopened.state is RunAdmissionState.RESUMED
    assert reopened.checkpoint is not None
    assert reopened.checkpoint.cursor == "c1"
    assert reopened.checkpoint.page_count == 1
    assert not reopened.writes_nothing

    resumed = await repository.resume(run_scope)
    assert resumed == reopened.checkpoint
    assert resumed.resumable and not resumed.exhausted

    second = Page(scope=run_scope, index=1, cursor="c1", next_cursor=None,
                  keys=("sku-3",), prior=store.runs[run_scope.run_id])
    second_write = await repository.commit_page(
        run_scope, artifact=second.artifact, payload=second.payload,
        snapshot=second.snapshot, run=second.run, expected_checkpoint="c1",
    )
    assert second_write.result is PageCommitResult.COMMITTED
    assert second_write.run_status is IngestionRunStatus.SUCCEEDED
    assert second_write.checkpoint is not None
    assert second_write.checkpoint.page_count == 2
    assert second_write.checkpoint.accepted == 3
    assert second_write.checkpoint.exhausted
    # Two runs were never created for one digest.
    assert len(store.runs) == 1
    assert store.admit_calls == 2


async def test_stale_expected_checkpoint_is_a_typed_conflict_without_advance() -> None:
    store, repository, run_scope, _opening, first = await open_first_page()
    await repository.commit_page(run_scope, artifact=first.artifact, payload=first.payload,
                                 snapshot=first.snapshot, run=first.run,
                                 expected_checkpoint=None)

    stale = Page(scope=run_scope, index=1, cursor=None, next_cursor=None,
                 keys=("sku-9",), prior=store.runs[run_scope.run_id])
    write = await repository.commit_page(
        run_scope, artifact=stale.artifact, payload=stale.payload,
        snapshot=stale.snapshot, run=stale.run, expected_checkpoint=None,
    )
    assert write.result is PageCommitResult.CHECKPOINT_CONFLICT
    assert write.reason_code == "checkpoint_stale"
    assert not write.durable
    # The page did not land and the durable run still holds page 0's checkpoint.
    assert stale.snapshot_id not in store.snapshots
    assert store.status(run_scope.run_id) is IngestionRunStatus.RUNNING
    assert write.run_status is IngestionRunStatus.RUNNING
    assert write.checkpoint is not None and write.checkpoint.cursor == "c1"


# --------------------------------------------------------------------------
# 3. repeat digest: replays, writes nothing, no second run
# --------------------------------------------------------------------------


async def test_repeat_digest_replays_without_a_second_run_or_duplicate_offers() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()

    first_open = await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert first_open.state is RunAdmissionState.CREATED
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                keys=("sku-1", "sku-2"))
    write = await repository.commit_page(run_scope, artifact=page.artifact,
                                        payload=page.payload, snapshot=page.snapshot,
                                        run=page.run, expected_checkpoint=None)
    assert write.run_status is IngestionRunStatus.SUCCEEDED

    pages_after_success = store.page_writes
    offers_after_success = dict(store.offers[page.snapshot_id])

    # Same digest, same window: the run is replayed, never recreated.
    replay_open = await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert replay_open.state is RunAdmissionState.REPLAYS
    assert replay_open.run_id == run_scope.run_id
    assert replay_open.writes_nothing
    assert replay_open.checkpoint is not None
    assert replay_open.checkpoint.exhausted
    assert replay_open.checkpoint.terminal
    assert len(store.runs) == 1

    # Re-committing the identical page is idempotent, not a second write.
    replay = await repository.commit_page(run_scope, artifact=page.artifact,
                                         payload=page.payload, snapshot=page.snapshot,
                                         run=page.run, expected_checkpoint=None)
    assert replay.result is PageCommitResult.REPLAYED
    assert replay.offers_written == 0
    assert replay.offers_replayed == 2
    assert replay.run_status is IngestionRunStatus.SUCCEEDED
    assert store.page_writes == pages_after_success
    assert store.offers[page.snapshot_id] == offers_after_success


async def test_a_second_run_key_is_stable_across_repository_instances() -> None:
    store = DurableRunStore()
    first = persistence(store)
    second = persistence(store)
    run_scope = scope()

    opening = await first.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=("sku-1",))
    await first.commit_page(run_scope, artifact=page.artifact, payload=page.payload,
                            snapshot=page.snapshot, run=page.run, expected_checkpoint=None)

    # A different repository instance over the same store resolves the same run.
    reopened = await second.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert reopened.run_id == opening.run_id
    assert reopened.state is RunAdmissionState.RESUMED
    assert len(store.runs) == 1


# --------------------------------------------------------------------------
# 4. changed digest in the same window: conflict, no second run
# --------------------------------------------------------------------------


async def test_changed_digest_in_the_same_window_conflicts_instead_of_a_new_run() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope(digest=DIGEST_A)

    assert (await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER,
                                  now=NOW)).state is RunAdmissionState.CREATED
    changed = scope(digest=DIGEST_B)
    assert changed.window_prefix == run_scope.window_prefix
    assert changed.run_id != run_scope.run_id

    opening = await repository.open(changed, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert opening.state is RunAdmissionState.CONFLICT
    assert opening.reason_code == "run_window_digest_conflict"
    assert opening.writes_nothing
    assert opening.run_id == changed.run_id
    # Exactly one run exists for the window, and it is still the first digest.
    assert store.window_run_ids() == (run_scope.run_id,)
    assert len(store.runs) == 0  # neither run row was written: admission found one key


async def test_different_windows_never_share_a_run_key() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    early = scope(window=IngestionWindow(start=NOW - timedelta(days=2),
                                         end=NOW - timedelta(days=1)))
    late = scope(window=WINDOW)
    assert early.run_id != late.run_id
    assert early.window_prefix != late.window_prefix
    first = await repository.open(early, source=SOURCE, supplier=SUPPLIER, now=NOW)
    second = await repository.open(late, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert first.state is RunAdmissionState.CREATED
    assert second.state is RunAdmissionState.CREATED


# --------------------------------------------------------------------------
# 5. external_key conflict
# --------------------------------------------------------------------------


async def test_external_key_conflict_rolls_back_and_never_succeeds() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()

    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                keys=("sku-1", "sku-2"))
    committed = await repository.commit_page(run_scope, artifact=page.artifact,
                                            payload=page.payload, snapshot=page.snapshot,
                                            run=page.run, expected_checkpoint=None)
    assert committed.run_status is IngestionRunStatus.SUCCEEDED
    stored = dict(store.offers[page.snapshot_id])

    # Same snapshot identity, same external keys, different evidence: the unique
    # (snapshot_id, external_key) key cannot be overwritten.
    tampered = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                    keys=("sku-1", "sku-2"), prices={"sku-1": "999.00"})
    assert tampered.snapshot_id == page.snapshot_id
    conflict = await repository.commit_page(run_scope, artifact=tampered.artifact,
                                           payload=tampered.payload,
                                           snapshot=tampered.snapshot, run=tampered.run,
                                           expected_checkpoint=None)
    assert conflict.result is PageCommitResult.CONFLICT
    assert conflict.reason_code == "offer_external_key_conflict"
    assert conflict.durable is False
    assert conflict.offers_conflicted == 2
    assert conflict.run_status is IngestionRunStatus.SUCCEEDED  # stored, not rewritten
    # Stored offer evidence is byte-identical to the accepted page.
    assert store.offers[page.snapshot_id] == stored
    assert store.offers[page.snapshot_id]["sku-1"][2] == "125.00"


async def test_duplicate_external_key_inside_one_page_is_a_conflict() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)

    duplicate = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                     keys=("sku-1", "sku-1"))
    write = await repository.commit_page(run_scope, artifact=duplicate.artifact,
                                        payload=duplicate.payload,
                                        snapshot=duplicate.snapshot, run=duplicate.run,
                                        expected_checkpoint=None)
    assert write.result is PageCommitResult.CONFLICT
    assert write.reason_code == "offer_external_key_conflict"
    assert write.run_status is not IngestionRunStatus.SUCCEEDED
    assert store.page_writes == 0


# --------------------------------------------------------------------------
# 6. partial batch
# --------------------------------------------------------------------------


async def test_partially_committed_batch_is_partial_with_a_code_and_never_succeeds() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)

    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                keys=("sku-1", "sku-2", "sku-3"))
    store.forced_outcome = PageCommitOutcome(
        PageCommitResult.PARTIALLY_COMMITTED, "offer_batch_write_interrupted",
        offers_written=2, offers_conflicted=1,
    )
    write = await repository.commit_page(run_scope, artifact=page.artifact,
                                        payload=page.payload, snapshot=page.snapshot,
                                        run=page.run, expected_checkpoint=None)

    assert write.result is PageCommitResult.PARTIALLY_COMMITTED
    assert write.reason_code == "offer_batch_write_interrupted"
    assert write.run_status is IngestionRunStatus.PARTIAL
    assert write.terminal is False
    assert write.offers_written == 2 and write.offers_conflicted == 1
    # No checkpoint evidence exists yet, so the page must be retried as a whole.
    assert write.checkpoint is None or not write.checkpoint.exhausted
    assert store.page_writes == 0


async def test_committed_page_with_failed_post_commit_step_is_failed_not_succeeded() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)

    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1",
                keys=("sku-1",))
    store.forced_outcome = PageCommitOutcome(
        PageCommitResult.COMMITTED_INCOMPLETE, "search_projection_unavailable",
        offers_written=1,
    )
    write = await repository.commit_page(run_scope, artifact=page.artifact,
                                        payload=page.payload, snapshot=page.snapshot,
                                        run=page.run, expected_checkpoint=None)
    assert write.result is PageCommitResult.COMMITTED_INCOMPLETE
    assert write.durable
    assert write.run_status is IngestionRunStatus.FAILED
    assert write.reason_code == "search_projection_unavailable"


async def test_mark_partial_is_a_resumable_guarded_write() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=("sku-1",))
    await repository.commit_page(run_scope, artifact=page.artifact, payload=page.payload,
                                 snapshot=page.snapshot, run=page.run,
                                 expected_checkpoint=None)

    # No guarded writer without a session factory: refusal, not a silent no-op.
    with pytest.raises(PersistenceError) as unavailable:
        await repository.mark_partial(run_scope, reason_code="page_budget_exhausted", at=NOW)
    assert unavailable.value.reason_code == "guarded_run_writer_unavailable"

    deferred: list[str] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, statement):
            deferred.append(str(statement))
            return _Rowcount(1)

    class Factory:
        def __call__(self):
            return Session()

    writer = EquipmentIngestionPersistence(store=store, tenant_id=TENANT,
                                           session_factory=Factory(), clock=lambda: NOW)
    assert await writer.mark_partial(run_scope, reason_code="page_budget_exhausted",
                                     at=NOW) is True
    assert len(deferred) == 1
    assert "partial" in deferred[0]
    assert "succeeded" in deferred[0]
    # The checkpoint stays put, so the run is resumable rather than finished.
    checkpoint = await writer.resume(run_scope)
    assert checkpoint is not None and checkpoint.cursor == "c1"


class _Rowcount:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


# --------------------------------------------------------------------------
# 7. terminal state only on complete success
# --------------------------------------------------------------------------


async def test_close_succeeded_requires_an_exhausted_checkpoint() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=("sku-1",))
    await repository.commit_page(run_scope, artifact=page.artifact, payload=page.payload,
                                 snapshot=page.snapshot, run=page.run,
                                 expected_checkpoint=None)

    # The source was not consumed, so success is refused by the store.
    assert await repository.close(run_scope, status=IngestionRunStatus.SUCCEEDED,
                                  reason_code="source_exhausted", at=NOW) is False
    assert store.status(run_scope.run_id) is IngestionRunStatus.RUNNING
    assert store.succeeded_calls == 1


async def test_close_failed_cannot_overwrite_a_completed_run() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None, keys=("sku-1",))
    await repository.commit_page(run_scope, artifact=page.artifact, payload=page.payload,
                                 snapshot=page.snapshot, run=page.run,
                                 expected_checkpoint=None)
    assert store.status(run_scope.run_id) is IngestionRunStatus.SUCCEEDED

    assert await repository.close(run_scope, status=IngestionRunStatus.FAILED,
                                  reason_code="source_rejected", at=NOW) is False
    assert store.status(run_scope.run_id) is IngestionRunStatus.SUCCEEDED
    assert store.failed_calls == 1


async def test_a_page_appended_after_success_is_refused_before_any_write() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    first = Page(scope=run_scope, index=0, cursor=None, next_cursor=None, keys=("sku-1",))
    await repository.commit_page(run_scope, artifact=first.artifact, payload=first.payload,
                                 snapshot=first.snapshot, run=first.run,
                                 expected_checkpoint=None)
    commits = store.commit_calls

    # A second page would grow a completed run: refused, not a silent no-op.
    extra = Page(scope=run_scope, index=1, cursor="c1", next_cursor=None,
                 keys=("sku-2",), prior=store.runs[run_scope.run_id])
    with pytest.raises(PersistenceError) as refused:
        await repository.commit_page(run_scope, artifact=extra.artifact,
                                     payload=extra.payload, snapshot=extra.snapshot,
                                     run=extra.run, expected_checkpoint=None)
    assert refused.value.reason_code == "run_already_succeeded"
    assert store.commit_calls == commits
    assert extra.snapshot_id not in store.snapshots
    assert store.status(run_scope.run_id) is IngestionRunStatus.SUCCEEDED


async def test_success_with_a_short_final_batch_is_refused() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)

    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None,
                keys=("sku-1", "sku-2"))
    # A store that claims success while only one of two offers landed.
    store.forced_outcome = PageCommitOutcome(PageCommitResult.COMMITTED,
                                             "page_committed", offers_written=1)
    with pytest.raises(PersistenceError) as refused:
        await repository.commit_page(run_scope, artifact=page.artifact,
                                     payload=page.payload, snapshot=page.snapshot,
                                     run=page.run, expected_checkpoint=None)
    assert refused.value.reason_code == "final_batch_not_committed"


async def test_requested_success_without_an_exhausted_checkpoint_is_refused() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=("sku-1",))
    # Status succeeded while a further cursor is still pending.
    forged = IngestionRun(
        run_id=page.run.run_id, source=SOURCE, supplier=SUPPLIER,
        status=IngestionRunStatus.SUCCEEDED, checkpoint_cursor="c1", page_count=1,
        record_count=1, artifact_ids=page.run.artifact_ids,
        snapshot_ids=page.run.snapshot_ids, started_at=NOW, updated_at=NOW,
    )
    with pytest.raises(PersistenceError) as refused:
        await repository.commit_page(run_scope, artifact=page.artifact,
                                     payload=page.payload, snapshot=page.snapshot,
                                     run=forged, expected_checkpoint=None)
    assert refused.value.reason_code == "terminal_run_requires_exhausted_checkpoint"
    assert store.commit_calls == 0


async def test_terminal_statuses_are_exactly_the_contract_set() -> None:
    assert TERMINAL_RUN_STATUSES == frozenset({
        IngestionRunStatus.SUCCEEDED,
        IngestionRunStatus.FAILED,
        IngestionRunStatus.BLOCKED,
    })
    assert IngestionRunStatus.PARTIAL not in TERMINAL_RUN_STATUSES


# --------------------------------------------------------------------------
# 8. scope and identity fail-closed
# --------------------------------------------------------------------------


async def test_repository_rejects_a_foreign_tenant_scope_before_any_write() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    foreign = scope(tenant_id=OTHER_TENANT)
    with pytest.raises(PersistenceError) as refused:
        await repository.open(foreign, source=SOURCE, supplier=SUPPLIER, now=NOW)
    assert refused.value.reason_code == "tenant_scope_mismatch"
    assert store.admit_calls == 0


async def test_foreign_tenant_source_identity_is_blocked_with_a_redacted_code() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    foreign_source = SourceIdentity(SOURCE_ID, "supplier-feed", "v1", OWNER_ID, OTHER_TENANT)
    opening = await repository.open(scope(), source=foreign_source, supplier=SUPPLIER, now=NOW)
    assert opening.state is RunAdmissionState.BLOCKED
    assert opening.reason_code == "source_scope_mismatch"
    assert opening.writes_nothing
    assert store.admit_calls == 0


async def test_incomplete_identity_blocks_admission_before_the_store() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    unverified = SupplierIdentity(SUPPLIER_ID, "feed-vendor-7", "Vendor", verified=False)
    blocked = await repository.open(scope(), source=SOURCE, supplier=unverified, now=NOW)
    assert blocked.state is RunAdmissionState.BLOCKED
    assert blocked.reason_code == "canonical_supplier_identity_incomplete"

    incomplete = SourceIdentity(SOURCE_ID, "supplier-feed", "v1", None, TENANT)
    blocked_source = await repository.open(scope(), source=incomplete, supplier=SUPPLIER,
                                           now=NOW)
    assert blocked_source.reason_code == "canonical_source_owner_identity_incomplete"
    assert store.admit_calls == 0


async def test_another_tenants_run_evidence_is_invisible() -> None:
    store = DurableRunStore()
    run_scope = scope()
    repository = persistence(store)
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor="c1", keys=("sku-1",))
    await repository.commit_page(run_scope, artifact=page.artifact, payload=page.payload,
                                 snapshot=page.snapshot, run=page.run,
                                 expected_checkpoint=None)

    # Same run id, but the stored row belongs to another tenant: no checkpoint.
    store.runs[run_scope.run_id] = IngestionRun(
        run_id=run_scope.run_id, source=SourceIdentity(SOURCE_ID, "supplier-feed", "v1",
                                                       OWNER_ID, OTHER_TENANT),
        supplier=SUPPLIER, status=IngestionRunStatus.RUNNING, checkpoint_cursor="c1",
        page_count=1, record_count=1,
        artifact_ids=page.run.artifact_ids, snapshot_ids=page.run.snapshot_ids,
        started_at=NOW, updated_at=NOW,
    )
    assert await repository.resume(run_scope) is None


async def test_page_evidence_must_be_bound_to_the_scope() -> None:
    store = DurableRunStore()
    repository = persistence(store)
    run_scope = scope()
    await repository.open(run_scope, source=SOURCE, supplier=SUPPLIER, now=NOW)
    page = Page(scope=run_scope, index=0, cursor=None, next_cursor=None, keys=("sku-1",))

    # A snapshot from another run cannot be committed under this scope.
    other = Page(scope=scope(digest=DIGEST_C), index=0, cursor=None, next_cursor=None,
                 keys=("sku-1",))
    with pytest.raises(PersistenceError) as refused:
        await repository.commit_page(run_scope, artifact=other.artifact,
                                     payload=other.payload, snapshot=other.snapshot,
                                     run=page.run, expected_checkpoint=None)
    assert refused.value.reason_code == "page_run_mismatch"
    assert store.commit_calls == 0


async def test_repository_requires_a_bounded_tenant() -> None:
    for invalid in ("", "   ", "t" * 65):
        with pytest.raises(PersistenceError):
            EquipmentIngestionPersistence(store=DurableRunStore(), tenant_id=invalid)


# --------------------------------------------------------------------------
# 9. pure decision table
# --------------------------------------------------------------------------


def _run(*, status: IngestionRunStatus, cursor: str | None,
         pages: int = 1) -> IngestionRun:
    run_scope = scope()
    return IngestionRun(
        run_id=run_scope.run_id, source=SOURCE, supplier=SUPPLIER, status=status,
        checkpoint_cursor=cursor, page_count=pages, record_count=1,
        artifact_ids=tuple(f"{run_scope.run_id}:artifact:{index}" for index in range(pages)),
        snapshot_ids=tuple(f"{run_scope.run_id}:snapshot:{index}" for index in range(pages)),
        started_at=NOW, updated_at=NOW,
    )


def test_decide_page_write_matrix() -> None:
    run_scope = scope()
    requested_done = _run(status=IngestionRunStatus.SUCCEEDED, cursor=None)
    requested_more = _run(status=IngestionRunStatus.RUNNING, cursor="c1")
    stored_running = RunCheckpoint(run_id=run_scope.run_id,
                                   status=IngestionRunStatus.RUNNING, cursor="c0",
                                   page_count=1, accepted=1, quarantined=0, observed=1)

    committed = decide_page_write(
        requested=requested_done,
        outcome=PageCommitOutcome(PageCommitResult.COMMITTED, "page_committed",
                                  offers_written=1),
        stored=None,
    )
    assert committed.run_status is IngestionRunStatus.SUCCEEDED
    assert committed.checkpoint is not None and committed.checkpoint.exhausted

    unfinished = decide_page_write(
        requested=requested_more,
        outcome=PageCommitOutcome(PageCommitResult.COMMITTED, "page_committed",
                                  offers_written=1),
        stored=None,
    )
    assert unfinished.run_status is IngestionRunStatus.RUNNING
    assert unfinished.resumable

    conflict = decide_page_write(
        requested=requested_done,
        outcome=PageCommitOutcome(PageCommitResult.CONFLICT, "offer_external_key_conflict",
                                  offers_conflicted=1),
        stored=stored_running,
    )
    assert conflict.run_status is IngestionRunStatus.RUNNING
    assert conflict.checkpoint is stored_running
    assert conflict.durable is False

    # A conflict on a run with no committed evidence cannot be trusted as
    # anything but blocked.
    blocked = decide_page_write(
        requested=requested_done,
        outcome=PageCommitOutcome(PageCommitResult.CONFLICT, "run_scope_conflict",
                                  offers_conflicted=1),
        stored=None,
    )
    assert blocked.run_status is IngestionRunStatus.BLOCKED
    assert blocked.terminal

    replay = decide_page_write(
        requested=requested_done,
        outcome=PageCommitOutcome(PageCommitResult.REPLAYED, "page_replayed",
                                  offers_replayed=1),
        stored=RunCheckpoint(run_id=run_scope.run_id,
                             status=IngestionRunStatus.SUCCEEDED, cursor=None,
                             page_count=1, accepted=1, quarantined=0, observed=1),
    )
    assert replay.durable and not replay.advanced
    assert replay.run_status is IngestionRunStatus.SUCCEEDED


def test_page_write_refuses_a_success_it_cannot_justify() -> None:
    run_scope = scope()
    stored = RunCheckpoint(run_id=run_scope.run_id, status=IngestionRunStatus.RUNNING,
                           cursor=None, page_count=1, accepted=1, quarantined=0, observed=1)
    # succeeded + a conflicting offer
    with pytest.raises(PersistenceError):
        PageWrite(result=PageCommitResult.COMMITTED, reason_code="page_committed",
                  run_status=IngestionRunStatus.SUCCEEDED, checkpoint=stored,
                  offers_conflicted=1)
    # succeeded + a rolled-back page result
    with pytest.raises(PersistenceError):
        PageWrite(result=PageCommitResult.CONFLICT, reason_code="offer_conflict",
                  run_status=IngestionRunStatus.SUCCEEDED, checkpoint=stored)
    # succeeded + an exhausted-checkpoint violation
    with pytest.raises(PersistenceError):
        PageWrite(result=PageCommitResult.COMMITTED, reason_code="page_committed",
                  run_status=IngestionRunStatus.SUCCEEDED, checkpoint=None)
    # a partial run that was not produced by a partially committed page
    with pytest.raises(PersistenceError):
        PageWrite(result=PageCommitResult.COMMITTED, reason_code="page_committed",
                  run_status=IngestionRunStatus.PARTIAL, checkpoint=stored)
    # success with an unexhausted checkpoint
    with pytest.raises(PersistenceError):
        PageWrite(result=PageCommitResult.COMMITTED, reason_code="page_committed",
                  run_status=IngestionRunStatus.SUCCEEDED,
                  checkpoint=RunCheckpoint(run_id=run_scope.run_id,
                                           status=IngestionRunStatus.SUCCEEDED,
                                           cursor="c1", page_count=2,
                                           accepted=1, quarantined=0, observed=1))


def test_run_checkpoint_rejects_inconsistent_counters() -> None:
    run_scope = scope()
    with pytest.raises(PersistenceError):
        RunCheckpoint(run_id=run_scope.run_id, status=IngestionRunStatus.RUNNING,
                      cursor="c1", page_count=1, accepted=1, quarantined=1, observed=1)
    with pytest.raises(PersistenceError):
        RunCheckpoint(run_id=run_scope.run_id, status=IngestionRunStatus.RUNNING,
                      cursor="c1", page_count=0, accepted=1, quarantined=0, observed=1)
    with pytest.raises(PersistenceError):
        RunCheckpoint(run_id=run_scope.run_id, status=None, cursor="c1", page_count=1,
                      accepted=1, quarantined=0, observed=1)
    with pytest.raises(PersistenceError):
        RunCheckpoint(run_id=run_scope.run_id, status=IngestionRunStatus.RUNNING,
                      cursor=None, page_count=1, accepted=-1, quarantined=1, observed=0)
    empty = RunCheckpoint.empty(run_scope.run_id)
    assert empty.status is None and not empty.terminal and not empty.resumable


def test_guarded_terminal_statement_cannot_write_a_success() -> None:
    statement = terminal_status_statement(run_id=scope().run_id, tenant_id=TENANT,
                                          status=IngestionRunStatus.PARTIAL,
                                          reason_code="page_budget_exhausted", at=NOW)
    compact = " ".join(str(statement).split())
    assert "UPDATE equipment_v2_ingestion_runs" in compact
    # The guard: a completed run is never downgraded by this writer.
    assert "equipment_v2_ingestion_runs.status !=" in compact
    assert "tenant_id =" in compact

    for status in (IngestionRunStatus.SUCCEEDED, IngestionRunStatus.RUNNING,
                   IngestionRunStatus.FAILED):
        with pytest.raises(PersistenceError) as refused:
            terminal_status_statement(run_id=scope().run_id, tenant_id=TENANT,
                                      status=status, reason_code="some_code", at=NOW)
        assert refused.value.reason_code == "guarded_status_not_writable"

    with pytest.raises(PersistenceError):
        terminal_status_statement(run_id=scope().run_id, tenant_id=TENANT,
                                  status=IngestionRunStatus.PARTIAL,
                                  reason_code="Payload leak sku-1", at=NOW)


def test_run_scope_is_the_whole_idempotency_key_and_has_no_project_or_cabinet_scope() -> None:
    run_scope = scope()
    assert set(run_scope.__slots__) == {"tenant_id", "source_id", "source_digest", "window"}
    assert not any("project" in name or "cabinet" in name for name in run_scope.__slots__)
    # Same key, same run; changed component, changed run.
    assert scope().run_id == run_scope.run_id
    assert scope(digest=DIGEST_B).run_id != run_scope.run_id
    assert scope(tenant_id=OTHER_TENANT).run_id != run_scope.run_id
    assert scope(source_id="source-other").run_id != run_scope.run_id
    assert scope(window=IngestionWindow(start=NOW - timedelta(days=3),
                                        end=NOW - timedelta(days=2))).run_id != run_scope.run_id
    # Bounded primary key shape.
    assert len(run_scope.run_id) <= 40
    assert run_scope.run_id.startswith("eir_")
    assert run_scope.window_prefix == run_window_prefix(
        tenant_id=TENANT, source_id=SOURCE_ID, window=WINDOW)
    for invalid in ("", "not-a-digest", "sha256:" + "z" * 64):
        with pytest.raises(PersistenceError):
            scope(digest=invalid)


# --------------------------------------------------------------------------
# 10. source-level contracts: no DDL, no duplicated encoding, no project scope
# --------------------------------------------------------------------------


def _code_without_docstrings(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    docstrings: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", ())
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstrings.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    dropped: set[int] = set()
    for start, end in docstrings:
        dropped.update(range(start, end + 1))
    return "\n".join(line for number, line in enumerate(lines, start=1)
                     if number not in dropped)


def test_persistence_module_declares_no_ddl_and_no_project_or_cabinet_scope() -> None:
    code = _code_without_docstrings(PERSISTENCE_PATH)
    for forbidden in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "alembic",
                      "project_id", "cabinet_id", "create_all", "op."):
        assert forbidden not in code, forbidden


def test_persistence_module_reuses_the_durable_run_key_and_envelope() -> None:
    code = _code_without_docstrings(PERSISTENCE_PATH)
    # The run key and window prefix come from the durable module: one derivation.
    assert "from prodavan.application.migration.equipment_ingestion.durable import" in code
    assert "run_row_id" in code and "run_window_prefix" in code
    assert "def run_row_id" not in code
    assert "def run_window_prefix" not in code
    # The page-evidence envelope is durable's; this module never re-encodes it.
    assert "run_checkpoint" not in code
    assert "page_index" not in code
    assert "snapshot_key" not in code
    assert "metadata_version" not in code
    # The page transaction itself is delegated, not re-implemented.
    assert "store.commit_page(" in code
    assert "pg_insert" not in code
    assert "on_conflict_do_nothing" not in code


def test_persistence_module_declares_the_documented_public_surface() -> None:
    tree = ast.parse(PERSISTENCE_PATH.read_text(encoding="utf-8"))
    module = next(node for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "__all__"
                          for target in node.targets))
    exported = {element.value for element in module.value.elts}
    assert exported == {
        "DurablePageStore", "EquipmentIngestionPersistence", "IngestionRunScope",
        "PageWrite", "PersistenceError", "RunCheckpoint", "RunOpening",
        "TERMINAL_RUN_STATUSES", "decide_page_write", "terminal_status_statement",
    }
