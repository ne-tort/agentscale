"""Durable persistence for equipment ingestion runs (MIG-EQ-INGEST-04).

This module is the *run-scope* persistence repository for equipment ingestion.
It owns the durable answers to four questions and nothing else:

1. Which run does ``(tenant_id, source_id, source_digest, window)`` belong to?
   (:meth:`EquipmentIngestionPersistence.open` — idempotent admission.)
2. What is the durable page checkpoint of that run right now?
   (:meth:`EquipmentIngestionPersistence.resume`.)
3. May this page be committed, and what happened durably if it was?
   (:meth:`EquipmentIngestionPersistence.commit_page`.)
4. What is the durable terminal disposition of the run?
   (:meth:`EquipmentIngestionPersistence.close` / ``mark_partial``.)

Relation to :mod:`prodavan.application.migration.equipment_ingestion.durable`
---------------------------------------------------------------------------
``durable.py`` owns the *storage encoding* of the ``equipment_v2_*`` tables: the
page-evidence envelope inside ``equipment_v2_catalog_snapshots.metadata_json``
(``snapshot_key``, ``page.page_index``, ``run_checkpoint``, artifact evidence),
the deterministic snapshot/offer row ids, the offer attribute namespace, the
admission decision and the page transaction itself. That encoding is a single
source of truth and is **reused here, never re-derived**: a second checkpoint
encoding in the same JSONB column would silently corrupt ``page_chain`` /
``build_run`` reads for both writers.

This module adds the layer ``durable.py`` deliberately leaves to its callers:

* a typed, bounded ``(tenant, source, digest, window)`` scope key that is
  *constructed*, not inferred, so a run can never be addressed by a partial or
  foreign identity;
* a compact checkpoint view (``RunCheckpoint``) instead of a full ``IngestionRun``,
  so resume/observability paths do not have to rehydrate offers;
* the **terminal discipline** as a durable-layer precondition rather than a
  service-side convention: a run may only be written as ``succeeded`` when the
  page that carried it was committed by the same transaction, its checkpoint is
  exhausted (``cursor is None``), every offer of that final page is accounted
  for as written or replayed, and no offer conflicted. Everything else resolves
  to ``failed`` / ``partial`` / ``blocked`` with an explicit redacted reason
  code. A page that is not already part of a completed run is refused outright,
  so a ``succeeded`` run can never grow another page (replays stay allowed);
* a guarded writer for the two dispositions ``durable.py`` does not itself
  write (``partial`` and ``blocked`` runs), expressed as one ``UPDATE`` whose
  ``WHERE`` clause excludes ``succeeded`` so no code path can downgrade or
  fabricate a success. No new status value is introduced: ``partial`` is the
  existing ``IngestionRunStatus.PARTIAL`` of the ingestion contract, and the
  ``status`` column is a free-form ``String(24)`` with no value check, so this
  writer stays inside the established domain;
* explicit reliance on row-level security rather than on it being bypassed:
  ``equipment_v2_ingestion_runs`` is tenant-rooted in the RLS registry and its
  catalog children inherit the tenant through ``ingestion_run_id``. Every
  predicate here also carries ``tenant_id``, so a session whose trusted tenant
  context is missing fails closed (RLS returns no rows) instead of crossing
  tenants. This module never sets the tenant GUC and never uses a
  ``BYPASSRLS``/superuser path.
* a run-row ownership re-read after admission (:meth:`_verify_run_ownership`).
  ``durable.run_row_id`` now derives the primary key as
  ``run_window_prefix(...) + digest``, so the id carries the tenant, the source
  and the window and always starts with its own window prefix. That closed both
  defects the earlier derivation had: two tenants could share one primary key,
  and a run id built from the window token alone would never match the
  ``id LIKE '<prefix>%'`` admission scan, making "same window, different digest"
  undetectable. The ownership re-read is kept as defence in depth — it refuses
  with ``run_identity_collision`` rather than trusting the derivation.

Schema boundaries (revision ``2026110305``), respected by construction:

* ``equipment_v2_ingestion_runs`` has **no** digest, window, or cursor column.
  The window/digest identity materializes in the primary key through
  ``durable.run_row_id``/``run_window_prefix``; the page cursor lives in the
  page-evidence envelope of the committed snapshot (``run_checkpoint``).
* ``equipment_v2_*`` has **no** ``project_id`` and **no** ``cabinet_id``. This
  module therefore has no project or cabinet scope: not a field, not a
  predicate, not an RLS claim, not a default. Tenant is the only database scope,
  and it is never inferred from an id prefix, payload, or caller argument.
* Offers are keyed by ``(snapshot_id, external_key)`` (``uq_equipment_v2_offer_external``)
  and snapshots by ``(ingestion_run_id, content_hash)``
  (``uq_equipment_v2_snapshot_hash``); a repeat never duplicates either.

Fail-closed rules encoded here:

* a foreign tenant, an unbound run/snapshot/artifact tuple, an inactive run, or a
  success requested without an exhausted checkpoint raises a redacted
  :class:`PersistenceError` **before** any write is attempted;
* a repeated ingest of the same digest returns ``replays`` and writes nothing;
* a changed digest inside the same window is a ``conflict``, never a second run;
* a partially written page is reported as ``partial`` with the adapter's reason
  code and can never be reported as ``succeeded``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol, runtime_checkable

from sqlalchemy import Update, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.equipment_ingestion.durable import (
    DurableEquipmentIngestionStore,
    IngestionWindow,
    RunAdmission,
    RunAdmissionState,
    run_row_id,
    run_window_prefix,
)
from prodavan.application.migration.equipment_ingestion.ingestion import (
    CatalogSnapshot,
    IngestionRun,
    IngestionRunStatus,
    PageCommitOutcome,
    PageCommitResult,
    RawArtifact,
    SourceIdentity,
    SupplierIdentity,
    normalize_page_commit_outcome,
)
from prodavan.infrastructure.persistence.models.equipment_v2 import (
    EquipmentIngestionRunRow,
)

_REDACTED_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_TENANT_ID_LIMIT = 64
_SOURCE_ID_LIMIT = 120
_RUN_ID_LIMIT = 40

#: Statuses that make a run final. ``partial`` is deliberately absent: a partial
#: run keeps its checkpoint and stays resumable.
TERMINAL_RUN_STATUSES = frozenset({
    IngestionRunStatus.SUCCEEDED,
    IngestionRunStatus.FAILED,
    IngestionRunStatus.BLOCKED,
})

#: Statuses the guarded writer may write. ``succeeded`` is not among them: it is
#: only ever produced by the page transaction that wrote the last batch.
_GUARDED_WRITABLE_STATUSES = frozenset({
    IngestionRunStatus.PARTIAL,
    IngestionRunStatus.BLOCKED,
})

#: A page transaction that committed its page: the checkpoint advanced.
_COMMITTED_PAGE_RESULTS = frozenset({
    PageCommitResult.COMMITTED,
    PageCommitResult.REPLAYED,
    PageCommitResult.COMMITTED_INCOMPLETE,
})

#: Page results whose own transaction wrote the run row, so the requested run
#: record *is* durable state and needs no read-back.
_SELF_EVIDENT_PAGE_RESULTS = frozenset({
    PageCommitResult.COMMITTED,
    PageCommitResult.COMMITTED_INCOMPLETE,
})


class PersistenceError(Exception):
    """Redacted, fail-closed persistence refusal.

    ``reason_code`` is the only text that may reach logs or audit evidence; it
    never contains payload, credential, supplier, or row text.
    """

    def __init__(self, reason_code: str) -> None:
        if not isinstance(reason_code, str) or not _REDACTED_CODE_RE.fullmatch(reason_code):
            raise ValueError("persistence reason_code must be a redacted code")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_code(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not _REDACTED_CODE_RE.fullmatch(value):
        raise PersistenceError(f"{name}_not_a_redacted_code")
    return value


def _require_aware(value: Any, *, name: str) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise PersistenceError(f"{name}_must_be_timezone_aware")
    return value


def _require_identifier(value: Any, *, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PersistenceError(f"{name}_invalid")
    return value


@dataclass(frozen=True, slots=True)
class IngestionRunScope:
    """Idempotency key of exactly one ingest run.

    ``(tenant_id, source_id, source_digest, window)`` is the whole key. There is
    no project or cabinet component: those columns do not exist in the schema.

    The window and digest are not stored as columns; they materialize in the run
    primary key through :func:`durable.run_row_id`, and the window prefix is what
    makes "same window, changed digest" detectable as an indexed lookup instead
    of a scan.
    """

    tenant_id: str
    source_id: str
    source_digest: str
    window: IngestionWindow

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, name="tenant_id", limit=_TENANT_ID_LIMIT)
        _require_identifier(self.source_id, name="source_id", limit=_SOURCE_ID_LIMIT)
        if not isinstance(self.source_digest, str) or not _SHA256_RE.fullmatch(
                self.source_digest):
            raise PersistenceError("source_digest_invalid")
        if not isinstance(self.window, IngestionWindow):
            raise PersistenceError("ingestion_window_invalid")

    @property
    def run_id(self) -> str:
        """Deterministic run primary key for this scope."""
        return run_row_id(tenant_id=self.tenant_id, source_id=self.source_id,
                          window=self.window, source_digest=self.source_digest)

    @property
    def window_prefix(self) -> str:
        """Indexed prefix matching every run admitted for the same window."""
        return run_window_prefix(tenant_id=self.tenant_id, source_id=self.source_id,
                                 window=self.window)


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    """Durable checkpoint of a run, rebuilt from committed page evidence.

    ``status is None`` means "admitted, nothing committed yet": the run row may
    exist, but there is no page evidence and therefore no cursor to resume from.
    """

    run_id: str
    status: IngestionRunStatus | None
    cursor: str | None
    page_count: int
    accepted: int
    quarantined: int
    observed: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise PersistenceError("run_identity_missing")
        if self.status is not None and not isinstance(self.status, IngestionRunStatus):
            raise PersistenceError("run_status_invalid")
        for name in ("page_count", "accepted", "quarantined", "observed"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PersistenceError(f"run_{name}_invalid")
        if self.accepted + self.quarantined != self.observed:
            raise PersistenceError("run_counters_inconsistent")
        if self.status is None and (self.page_count or self.observed or self.cursor is not None):
            raise PersistenceError("unstarted_run_has_evidence")
        if self.page_count == 0 and (self.observed or self.cursor is not None):
            raise PersistenceError("pageless_run_has_evidence")

    @classmethod
    def empty(cls, run_id: str) -> "RunCheckpoint":
        """Checkpoint of an admitted run with zero committed pages."""
        return cls(run_id=run_id, status=None, cursor=None, page_count=0,
                   accepted=0, quarantined=0, observed=0)

    @classmethod
    def from_run(cls, run: IngestionRun) -> "RunCheckpoint":
        return cls(
            run_id=run.run_id,
            status=run.status,
            cursor=run.checkpoint_cursor,
            page_count=run.page_count,
            accepted=run.record_count,
            quarantined=run.quarantined_count,
            observed=run.observed_count,
        )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    @property
    def resumable(self) -> bool:
        """True when a next page can be requested from :attr:`cursor`."""
        return self.status is not None and not self.terminal and not self.exhausted

    @property
    def exhausted(self) -> bool:
        """True when the source was consumed to its end (no further cursor)."""
        return self.cursor is None


@dataclass(frozen=True, slots=True)
class RunOpening:
    """Typed result of idempotent run admission."""

    state: RunAdmissionState
    checkpoint: RunCheckpoint | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RunAdmissionState):
            raise PersistenceError("run_opening_state_invalid")
        if self.reason_code is not None:
            _require_code(self.reason_code, name="run_opening_reason")
        if self.state is RunAdmissionState.BLOCKED:
            if not self.reason_code:
                raise PersistenceError("blocked_run_opening_requires_reason")
            if self.checkpoint is not None:
                raise PersistenceError("blocked_run_opening_has_checkpoint")
            return
        if self.checkpoint is None:
            raise PersistenceError("admitted_run_opening_requires_checkpoint")
        if self.state is not RunAdmissionState.CONFLICT and self.reason_code is not None:
            # Only a refusal carries a reason: an admitted run is self-describing.
            raise PersistenceError("admitted_run_opening_has_reason")

    @property
    def run_id(self) -> str | None:
        return self.checkpoint.run_id if self.checkpoint is not None else None

    @property
    def writes_nothing(self) -> bool:
        """True when the caller must not commit pages for this opening."""
        return self.state in (RunAdmissionState.REPLAYS, RunAdmissionState.CONFLICT,
                              RunAdmissionState.BLOCKED)


@dataclass(frozen=True, slots=True)
class PageWrite:
    """What a page commit durably did, and what the run looks like afterwards.

    ``run_status`` is the run disposition *as durable state*, not a wish: for a
    rolled-back page it is the status the run already had (or ``blocked`` when
    that cannot be established), never ``succeeded``.
    """

    result: PageCommitResult
    reason_code: str
    run_status: IngestionRunStatus
    checkpoint: RunCheckpoint | None = None
    offers_written: int = 0
    offers_replayed: int = 0
    offers_conflicted: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.result, PageCommitResult):
            raise PersistenceError("page_result_invalid")
        _require_code(self.reason_code, name="page_reason")
        if not isinstance(self.run_status, IngestionRunStatus):
            raise PersistenceError("page_run_status_invalid")
        for name in ("offers_written", "offers_replayed", "offers_conflicted"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PersistenceError(f"page_{name}_invalid")
        if self.result is PageCommitResult.PARTIALLY_COMMITTED and (
                self.run_status is not IngestionRunStatus.PARTIAL):
            raise PersistenceError("partially_committed_page_must_report_partial")
        if self.result is PageCommitResult.COMMITTED_INCOMPLETE and (
                self.run_status is IngestionRunStatus.SUCCEEDED):
            raise PersistenceError("committed_incomplete_page_cannot_succeed")
        if self.run_status is not IngestionRunStatus.SUCCEEDED:
            return
        # Everything below constrains a report whose run status is ``succeeded``.
        # An exhausted checkpoint is required either way: it is the only durable
        # proof that the source was consumed rather than abandoned mid-cursor.
        if self.checkpoint is None or not self.checkpoint.exhausted:
            raise PersistenceError("succeeded_run_requires_exhausted_checkpoint")
        if self.result in (PageCommitResult.COMMITTED, PageCommitResult.REPLAYED):
            # A success produced by *this* call: the decisive page must be a
            # committed one, with no conflicted offer.
            if self.offers_conflicted:
                raise PersistenceError("succeeded_page_has_conflicts")
            return
        if self.result is PageCommitResult.COMMITTED_INCOMPLETE:
            raise PersistenceError("committed_incomplete_page_cannot_succeed")
        # A rolled-back page reported against a run that was already succeeded:
        # nothing changed, so reporting the stored success is correct. It is not
        # a success transition, and the guard above keeps it unexhausted-proof.

    @property
    def durable(self) -> bool:
        """True when the page transaction committed its evidence."""
        return self.result in _COMMITTED_PAGE_RESULTS

    @property
    def advanced(self) -> bool:
        """True when this call moved the durable checkpoint."""
        return self.result is PageCommitResult.COMMITTED

    @property
    def terminal(self) -> bool:
        return self.run_status in TERMINAL_RUN_STATUSES

    @property
    def resumable(self) -> bool:
        """True when the durable checkpoint is a cursor a forward re-run can use.

        This is a property of the committed evidence, not of the reported status:
        a page that committed before a post-commit failure is reported ``failed``
        and is still resumable from its advanced checkpoint.
        """
        return (self.checkpoint is not None and self.checkpoint.resumable
                and not self.checkpoint.exhausted)


def decide_page_write(*, requested: IngestionRun, outcome: PageCommitOutcome,
                      stored: RunCheckpoint | None) -> PageWrite:
    """Map one page transaction outcome onto the run's durable disposition.

    Pure: no session, no clock, no I/O. ``requested`` is the run record the page
    transaction was asked to write; ``stored`` is the run as it is *after* the
    call (``None`` when there is no committed page evidence, or when the caller
    did not need a read because the page committed).
    """
    if not isinstance(requested, IngestionRun):
        raise PersistenceError("page_requested_run_invalid")
    if not isinstance(outcome, PageCommitOutcome):
        raise PersistenceError("page_outcome_invalid")
    if stored is not None and not isinstance(stored, RunCheckpoint):
        raise PersistenceError("page_stored_checkpoint_invalid")
    if stored is not None and stored.run_id != requested.run_id:
        raise PersistenceError("page_stored_run_mismatch")

    written = outcome.offers_written
    replayed = outcome.offers_replayed
    conflicted = outcome.offers_conflicted

    if outcome.result is PageCommitResult.COMMITTED:
        # The transaction wrote the page and the run row together, so the
        # requested record is durable state.
        return PageWrite(
            result=outcome.result, reason_code=outcome.reason_code,
            run_status=requested.status,
            checkpoint=RunCheckpoint.from_run(requested),
            offers_written=written, offers_replayed=replayed,
            offers_conflicted=conflicted,
        )

    if outcome.result is PageCommitResult.COMMITTED_INCOMPLETE:
        # Page durable, post-commit requirement failed. The checkpoint advanced,
        # so a forward re-run resumes after this page; the run is reported failed
        # and is never running or succeeded. The durable run row still holds the
        # requested status until ``close(..., failed)`` persists the failure.
        return PageWrite(
            result=outcome.result, reason_code=outcome.reason_code,
            run_status=IngestionRunStatus.FAILED,
            checkpoint=RunCheckpoint.from_run(requested),
            offers_written=written, offers_replayed=replayed,
            offers_conflicted=conflicted,
        )

    if outcome.result is PageCommitResult.REPLAYED:
        # Nothing was written: the page was already durable with identical
        # evidence, so the stored disposition wins over the requested one.
        return PageWrite(
            result=outcome.result, reason_code=outcome.reason_code,
            run_status=stored.status if stored is not None and stored.status is not None
            else requested.status,
            checkpoint=stored if stored is not None else RunCheckpoint.from_run(requested),
            offers_written=written, offers_replayed=replayed,
            offers_conflicted=conflicted,
        )

    if outcome.result is PageCommitResult.PARTIALLY_COMMITTED:
        # Offer batches landed but the page never reached its terminal state and
        # the checkpoint stayed put. That is a resumable partial run, never a
        # success.
        return PageWrite(
            result=outcome.result, reason_code=outcome.reason_code,
            run_status=IngestionRunStatus.PARTIAL,
            checkpoint=stored,
            offers_written=written, offers_replayed=replayed,
            offers_conflicted=conflicted,
        )

    # CONFLICT and CHECKPOINT_CONFLICT: the page transaction rolled back and the
    # run row was not written. Report the status the run actually holds; when no
    # committed evidence exists the run cannot be trusted, so it is blocked.
    return PageWrite(
        result=outcome.result, reason_code=outcome.reason_code,
        run_status=stored.status if stored is not None and stored.status is not None
        else IngestionRunStatus.BLOCKED,
        checkpoint=stored,
        offers_written=written, offers_replayed=replayed,
        offers_conflicted=conflicted,
    )


def terminal_status_statement(*, run_id: str, tenant_id: str,
                              status: IngestionRunStatus, reason_code: str,
                              at: datetime) -> Update:
    """Guarded ``UPDATE`` for the dispositions the page transaction never writes.

    The ``WHERE`` clause excludes ``succeeded``, so a completed run can never be
    downgraded to ``partial``/``blocked`` and no code path can reach ``succeeded``
    through this statement. ``rowcount == 0`` therefore means "nothing was
    written", not "written with no effect".
    """
    _require_identifier(run_id, name="run_id", limit=_RUN_ID_LIMIT)
    _require_identifier(tenant_id, name="tenant_id", limit=_TENANT_ID_LIMIT)
    if status not in _GUARDED_WRITABLE_STATUSES:
        raise PersistenceError("guarded_status_not_writable")
    _require_code(reason_code, name="terminal_reason")
    _require_aware(at, name="terminal_at")
    return (
        update(EquipmentIngestionRunRow)
        .where(
            EquipmentIngestionRunRow.id == run_id,
            EquipmentIngestionRunRow.tenant_id == tenant_id,
            EquipmentIngestionRunRow.status != IngestionRunStatus.SUCCEEDED.value,
        )
        .values(
            status=status.value,
            error_message=reason_code,
            finished_at=at,
        )
    )


@runtime_checkable
class DurablePageStore(Protocol):
    """The storage primitives this repository composes.

    Signature-compatible with :class:`durable.DurableEquipmentIngestionStore`,
    and deliberately narrow: nothing about payloads, artifacts, or indexing.
    """

    async def admit_run(self, *, source: SourceIdentity, supplier: SupplierIdentity,
                        window: IngestionWindow, source_digest: str,
                        now: datetime | None = None) -> RunAdmission: ...

    async def load_run(self, run_id: str) -> IngestionRun | None: ...

    async def commit_page(self, *, artifact: RawArtifact, payload: bytes,
                          snapshot: CatalogSnapshot, run: IngestionRun,
                          expected_checkpoint: str | None
                          ) -> PageCommitOutcome | PageCommitResult: ...

    async def mark_run_succeeded(self, *, run_id: str, at: datetime) -> bool: ...

    async def mark_run_failed(self, *, run_id: str, error_code: str,
                              at: datetime) -> bool: ...


class EquipmentIngestionPersistence:
    """Tenant-scoped durable repository for equipment ingestion runs.

    The repository never infers identity: the tenant comes from the constructor,
    the source/supplier from typed call arguments, and the run key from an
    explicit :class:`IngestionRunScope`. There is no project or cabinet scope
    because ``equipment_v2_*`` has no such columns.
    """

    def __init__(self, *, store: DurablePageStore, tenant_id: str,
                 session_factory: async_sessionmaker[AsyncSession] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        _require_identifier(tenant_id, name="tenant_id", limit=_TENANT_ID_LIMIT)
        self._store = store
        self._tenant_id = tenant_id
        self._session_factory = session_factory
        self._clock = clock

    @classmethod
    def over_session_factory(cls, *, session_factory: async_sessionmaker[AsyncSession],
                             tenant_id: str, offer_batch_size: int = 500,
                             clock: Callable[[], datetime] | None = None
                             ) -> "EquipmentIngestionPersistence":
        """Production wiring: the durable store plus the guarded run writer."""
        store = DurableEquipmentIngestionStore(
            session_factory=session_factory, tenant_id=tenant_id,
            offer_batch_size=offer_batch_size, clock=clock,
        )
        return cls(store=store, tenant_id=tenant_id, session_factory=session_factory,
                   clock=clock)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def scope(self, *, source_id: str, source_digest: str,
              window: IngestionWindow) -> IngestionRunScope:
        """Build a scope bound to this repository's tenant.

        Callers cannot address another tenant's run, because there is no way to
        pass a tenant argument here.
        """
        return IngestionRunScope(tenant_id=self._tenant_id, source_id=source_id,
                                 source_digest=source_digest, window=window)

    # -- admission -------------------------------------------------------

    async def open(self, scope: IngestionRunScope, *, source: SourceIdentity,
                   supplier: SupplierIdentity,
                   now: datetime | None = None) -> RunOpening:
        """Create, resume, replay, or refuse the run for ``scope``.

        Same digest and window returns the same run (``created`` once, then
        ``resumed``/``replays``); a changed digest in the same window returns
        ``conflict`` and creates nothing; an incomplete or foreign identity
        returns ``blocked`` with a redacted code.
        """
        self._require_scope(scope)
        if not isinstance(source, SourceIdentity) or not source.complete:
            return RunOpening(RunAdmissionState.BLOCKED,
                              reason_code="canonical_source_owner_identity_incomplete")
        if not isinstance(supplier, SupplierIdentity) or not supplier.complete:
            return RunOpening(RunAdmissionState.BLOCKED,
                              reason_code="canonical_supplier_identity_incomplete")
        if (source.tenant_id, source.source_id) != (scope.tenant_id, scope.source_id):
            return RunOpening(RunAdmissionState.BLOCKED, reason_code="source_scope_mismatch")
        started = now if now is not None else (self._clock() if self._clock else None)
        if started is not None:
            _require_aware(started, name="open_now")

        admission = await self._store.admit_run(
            source=source, supplier=supplier, window=scope.window,
            source_digest=scope.source_digest, now=started,
        )
        if not isinstance(admission, RunAdmission):
            raise PersistenceError("run_admission_invalid")
        if admission.state is RunAdmissionState.BLOCKED:
            return RunOpening(RunAdmissionState.BLOCKED,
                              reason_code=admission.reason_code)
        if admission.run_id != scope.run_id:
            # The store answered for a different run than the scope names: the
            # run key is not ours, so nothing may be written under this scope.
            raise PersistenceError("run_identity_mismatch")

        checkpoint = await self.resume(scope)
        if checkpoint is None:
            checkpoint = RunCheckpoint.empty(scope.run_id)
        if admission.state is RunAdmissionState.REPLAYS and not checkpoint.exhausted:
            # A replayed digest must describe a fully consumed run; a replay that
            # still has a cursor means the evidence is inconsistent.
            raise PersistenceError("replay_run_not_exhausted")
        if admission.state is RunAdmissionState.CONFLICT:
            # The window is already bound to another digest: the caller must not
            # commit pages, and the reason code says why.
            return RunOpening(
                RunAdmissionState.CONFLICT, checkpoint=checkpoint,
                reason_code=admission.reason_code or "run_window_digest_conflict",
            )
        await self._verify_run_ownership(scope)
        return RunOpening(state=admission.state, checkpoint=checkpoint)

    async def resume(self, scope: IngestionRunScope) -> RunCheckpoint | None:
        """Durable checkpoint rebuilt from committed page evidence, if any."""
        self._require_scope(scope)
        run = await self._store.load_run(scope.run_id)
        if run is None:
            return None
        if not isinstance(run, IngestionRun):
            raise PersistenceError("run_record_invalid")
        if run.run_id != scope.run_id:
            raise PersistenceError("run_identity_mismatch")
        if run.source.tenant_id != self._tenant_id:
            # Another tenant's evidence is invisible, not merely unreadable.
            return None
        if run.source.source_id != scope.source_id:
            raise PersistenceError("run_source_mismatch")
        return RunCheckpoint.from_run(run)

    # -- page commit -----------------------------------------------------

    async def commit_page(self, scope: IngestionRunScope, *, artifact: RawArtifact,
                         payload: bytes, snapshot: CatalogSnapshot,
                         run: IngestionRun, expected_checkpoint: str | None
                         ) -> PageWrite:
        """Commit exactly one page and report the run's durable disposition.

        The write itself is one transaction in the store: snapshot (with
        ``content_hash``), the page's offer batches under
        ``ON CONFLICT (snapshot_id, external_key) DO NOTHING``, the advanced
        checkpoint, and the run row — including a terminal status, which is
        therefore only ever written by the same transaction that wrote the last
        batch of the page.

        Preconditions are checked before the store is touched, so a refused call
        leaves no partial page behind. In particular a page whose snapshot is not
        already part of a *completed* run is refused: a ``succeeded`` run is an
        ended stream and must not grow a new page. Re-committing a page the run
        already holds stays allowed, because that is an idempotent replay and the
        store answers ``REPLAYED`` without writing.
        """
        self._require_scope(scope)
        self._require_bound_page(scope=scope, artifact=artifact, snapshot=snapshot, run=run)
        if run.status is IngestionRunStatus.SUCCEEDED and run.checkpoint_cursor is not None:
            # Terminal status without an exhausted checkpoint would claim success
            # for a source that was not consumed.
            raise PersistenceError("terminal_run_requires_exhausted_checkpoint")
        if run.status not in (IngestionRunStatus.RUNNING, IngestionRunStatus.PARTIAL,
                              IngestionRunStatus.SUCCEEDED):
            raise PersistenceError("run_not_active")

        prior = await self._load_durable_run(scope)
        if prior is not None and prior.status is IngestionRunStatus.SUCCEEDED:
            if snapshot.snapshot_id not in prior.snapshot_ids:
                # Append-after-success: the run is ended, so a new page is a
                # caller error, never a silent no-op and never a second success.
                raise PersistenceError("run_already_succeeded")

        outcome = normalize_page_commit_outcome(await self._store.commit_page(
            artifact=artifact, payload=payload, snapshot=snapshot, run=run,
            expected_checkpoint=expected_checkpoint,
        ))
        stored = None
        if outcome.result not in _SELF_EVIDENT_PAGE_RESULTS:
            # Only COMMITTED/COMMITTED_INCOMPLETE wrote the run row in this same
            # transaction. Everything else — including a replay, which wrote
            # nothing — is reported from the durable record, never from the
            # request.
            stored = await self.resume(scope)
        decision = decide_page_write(requested=run, outcome=outcome, stored=stored)
        if decision.terminal and decision.run_status is IngestionRunStatus.SUCCEEDED:
            # A success needs the last batch accounted for as durable evidence:
            # every offer either inserted by this transaction or already stored
            # with identical evidence. A short count means a batch was skipped.
            self._require_last_batch_committed(snapshot=snapshot, outcome=outcome)
        return decision

    # -- run disposition -------------------------------------------------

    async def close(self, scope: IngestionRunScope, *,
                    status: IngestionRunStatus, reason_code: str,
                    at: datetime) -> bool:
        """Write the run's disposition; ``False`` means nothing was written.

        ``succeeded`` is only accepted when the durable page chain ends with an
        exhausted checkpoint, which the store verifies inside its own
        transaction. ``failed`` cannot overwrite a succeeded run. ``partial`` and
        ``blocked`` go through the guarded statement, which excludes succeeded in
        its ``WHERE`` clause.
        """
        self._require_scope(scope)
        _require_code(reason_code, name="terminal_reason")
        _require_aware(at, name="terminal_at")
        if status is IngestionRunStatus.SUCCEEDED:
            return bool(await self._store.mark_run_succeeded(run_id=scope.run_id, at=at))
        if status is IngestionRunStatus.FAILED:
            return bool(await self._store.mark_run_failed(
                run_id=scope.run_id, error_code=reason_code, at=at))
        if status in _GUARDED_WRITABLE_STATUSES:
            return await self._write_guarded_status(scope, status=status,
                                                    reason_code=reason_code, at=at)
        raise PersistenceError("run_status_not_terminal")

    async def mark_partial(self, scope: IngestionRunScope, *, reason_code: str,
                           at: datetime) -> bool:
        """Record a resumable partial run (page budget spent, batch written)."""
        return await self.close(scope, status=IngestionRunStatus.PARTIAL,
                                reason_code=reason_code, at=at)

    async def _write_guarded_status(self, scope: IngestionRunScope, *,
                                    status: IngestionRunStatus, reason_code: str,
                                    at: datetime) -> bool:
        if self._session_factory is None:
            raise PersistenceError("guarded_run_writer_unavailable")
        statement = terminal_status_statement(
            run_id=scope.run_id, tenant_id=self._tenant_id, status=status,
            reason_code=reason_code, at=at,
        )
        async with self._session_factory() as session, session.begin():
            result = await session.execute(statement)
        rowcount = getattr(result, "rowcount", 0)
        if not isinstance(rowcount, int) or isinstance(rowcount, bool):
            raise PersistenceError("guarded_run_write_unverified")
        return rowcount > 0

    # -- invariants ------------------------------------------------------

    async def _verify_run_ownership(self, scope: IngestionRunScope) -> None:
        """Refuse an admitted run whose row belongs to another owner.

        The durable key derivation now folds tenant and source into the run id,
        so a cross-owner collision should be impossible. This re-read is kept as
        defence in depth: any future regression in the derivation turns into a
        typed ``run_identity_collision`` refusal instead of a write against
        another owner's row.

        Only runs with a session factory can re-read; a store-only wiring keeps
        its admission answer.
        """
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            result = await session.execute(
                select(EquipmentIngestionRunRow.tenant_id, EquipmentIngestionRunRow.source_id)
                .where(EquipmentIngestionRunRow.id == scope.run_id)
            )
            row = result.first()
        if row is None:
            # The row is not visible in this tenant's scope at all: either it was
            # never written or RLS hid it. Either way it is not ours to drive.
            return
        tenant_id, source_id = row[0], row[1]
        if tenant_id != self._tenant_id or source_id != scope.source_id:
            raise PersistenceError("run_identity_collision")

    async def _load_durable_run(self, scope: IngestionRunScope) -> IngestionRun | None:
        """The run as durably stored, or ``None`` when it has no page evidence."""
        run = await self._store.load_run(scope.run_id)
        if run is None:
            return None
        if not isinstance(run, IngestionRun):
            raise PersistenceError("run_record_invalid")
        if run.run_id != scope.run_id:
            raise PersistenceError("run_identity_mismatch")
        if run.source.tenant_id != self._tenant_id:
            return None
        if run.source.source_id != scope.source_id:
            raise PersistenceError("run_source_mismatch")
        return run

    @staticmethod
    def _require_last_batch_committed(*, snapshot: CatalogSnapshot,
                                      outcome: PageCommitOutcome) -> None:
        """A success needs every offer of the final page accounted for."""
        expected = len(snapshot.offers)
        if outcome.result is PageCommitResult.COMMITTED:
            accounted = outcome.offers_written
        elif outcome.result is PageCommitResult.REPLAYED:
            accounted = outcome.offers_replayed
        else:
            raise PersistenceError("success_page_result_invalid")
        if accounted != expected:
            raise PersistenceError("final_batch_not_committed")

    def _require_scope(self, scope: IngestionRunScope) -> None:
        if not isinstance(scope, IngestionRunScope):
            raise PersistenceError("run_scope_invalid")
        if scope.tenant_id != self._tenant_id:
            raise PersistenceError("tenant_scope_mismatch")

    def _require_bound_page(self, *, scope: IngestionRunScope, artifact: RawArtifact,
                            snapshot: CatalogSnapshot, run: IngestionRun) -> None:
        if not isinstance(artifact, RawArtifact) or not isinstance(
                snapshot, CatalogSnapshot) or not isinstance(run, IngestionRun):
            raise PersistenceError("page_evidence_invalid")
        if run.run_id != scope.run_id:
            raise PersistenceError("run_identity_mismatch")
        if snapshot.run_id != run.run_id or artifact.run_id != run.run_id:
            raise PersistenceError("page_run_mismatch")
        if snapshot.source_id != scope.source_id or artifact.source_id != scope.source_id:
            raise PersistenceError("page_source_mismatch")
        if snapshot.tenant_id != self._tenant_id:
            raise PersistenceError("tenant_scope_mismatch")
        if run.source.tenant_id != self._tenant_id:
            raise PersistenceError("tenant_scope_mismatch")
        if run.source.source_id != scope.source_id:
            raise PersistenceError("run_source_mismatch")
        if snapshot.artifact_id != artifact.artifact_id:
            raise PersistenceError("page_artifact_provenance_mismatch")
        if snapshot.supplier_id != run.supplier.supplier_id:
            raise PersistenceError("page_supplier_provenance_mismatch")
        if snapshot.owner_id != run.source.canonical_owner_id:
            raise PersistenceError("page_owner_provenance_mismatch")


__all__ = [
    "DurablePageStore",
    "EquipmentIngestionPersistence",
    "IngestionRunScope",
    "PageWrite",
    "PersistenceError",
    "RunCheckpoint",
    "RunOpening",
    "TERMINAL_RUN_STATUSES",
    "decide_page_write",
    "terminal_status_statement",
]
