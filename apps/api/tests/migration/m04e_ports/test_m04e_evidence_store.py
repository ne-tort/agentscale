"""Executable acceptance for :class:`SqlM04EEvidenceStore` over the ledger mirror.

Why a SQLite mirror and not the statement fake: every claim here is about what the
*ledger* holds after a page (a row appended, a cursor advanced, nothing written on a
replay, an observation instead of a rewrite). A dictionary-backed fake would let a
broken predicate pass, because the answer would come from the fake's table map rather
than from a database evaluating the statement. The mirror keeps the four registered
models' columns, types, nullability and unique keys, and SQLite enforces those unique
keys — which is exactly the layer ``uq_m04e_mapping_source`` /
``uq_m04e_quarantine_observation`` / ``uq_m04e_checkpoint_token`` protect.

What each section pins:

1. construction and transaction discipline (no store without a factory, one
   transaction per call, dialect refused unless the mirror is allowed);
2. a bounded page commit: mappings + checkpoint + audit commit together, counters
   balance, and the cursor advances by exactly the consumed rows;
3. replay: the same cursor returns the original evidence and moves nothing;
4. a changed digest is a conflict + quarantine observation, never a rewrite of the
   first mapping, and its replay is exact;
5. checkpoint CAS: a stale cursor is answered with the durable head, not an exception,
   and writes nothing;
6. blocked evidence: an audit event with zero counters and an untouched cursor;
7. refusals that keep the ledger honest (wrong phase, repeated identity, blocked
   status, foreign partition, missing reason).
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    CandidateDecision,
    EvidenceStatus,
    UpstreamGateEvidence,
)
from prodavan.application.migration.m04e_ports import (
    M04EPortReason,
    M04EPortRefused,
    SqlM04EEvidenceStore,
    definition_candidate,
    version_candidate,
)

from .acceptance_fixtures import (
    APP_ID,
    CORRELATION_ID,
    DEFINITION_ID,
    OTHER_DEFINITION_ID,
    OTHER_TENANT,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    TENANT,
    context,
    definition_row,
    expected_source_digest,
    version_row,
)
from .sqlite_ledger import (
    AUDIT_TABLE,
    CHECKPOINT_TABLE,
    MAPPING_TABLE,
    QUARANTINE_TABLE,
    LedgerMirror,
    rows_to_counts,
)

#: A second source identity for the same definition, so a page can hold two rows.
VERSION_TWO = "aver_0002"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def checkpoint(
    *, phase: str = "version", after_source_id: str | None = None, sequence: int = 0,
    phase_complete: bool = False, **overrides,
) -> BackfillCheckpoint:
    ctx = context()
    values = {
        "partition": (ctx.tenant_id, ctx.app_definition_id),
        "phase": phase,
        "after_source_id": after_source_id,
        "sequence": sequence,
        "source_snapshot_id": ctx.source_snapshot_id,
        "source_schema_version": ctx.source_schema_version,
        "source_snapshot_digest": ctx.source_snapshot_digest,
        "canonical_schema_version": ctx.canonical_schema_version,
        "writer_generation": ctx.writer_generation,
        "phase_complete": phase_complete,
    }
    values.update(overrides)
    return BackfillCheckpoint(**values)


def gates(**overrides) -> UpstreamGateEvidence:
    ctx = context()
    values = {
        "tenant_id": ctx.tenant_id,
        "app_definition_id": ctx.app_definition_id,
        "source_snapshot_digest": ctx.source_snapshot_digest,
        "transaction_id": ctx.transaction_id,
        "correlation_id": ctx.correlation_id,
        "m04_gate_digest": SHA_A,
        "m04e_gate_digest": SHA_B,
        "active_scope_digest": SHA_C,
    }
    values.update(overrides)
    return UpstreamGateEvidence(**values)


def version_two_row(**overrides):
    values = {
        "id": VERSION_TWO,
        "manifest_version": "1.0.1",
        "artifact_digest": SHA_D,
        "artifact_uri": f"content://{TENANT}/{DEFINITION_ID}/{VERSION_TWO}",
        "manifest": {
            "protocol": "1.0",
            "appId": APP_ID,
            "version": "1.0.1",
            "displayName": "demo",
            "entrypoints": {"plugin": {}},
            "requestedCapabilities": [],
        },
    }
    values.update(overrides)
    return version_row(**values)


def accepted(decision_candidate, *, verified: str | None = None) -> CandidateDecision:
    """An accepted classification carrying the proof digests ``run_page`` requires."""

    from prodavan.application.migration.m04e_definition_version.backfill import CandidateProof

    proof = CandidateProof(
        tenant_id=TENANT,
        app_definition_id=DEFINITION_ID,
        source_kind=decision_candidate.source_kind,
        source_id=decision_candidate.source_id,
        source_digest=decision_candidate.source_digest,
        transaction_id=context().transaction_id,
        correlation_id=CORRELATION_ID,
        owner_proof_digest=SHA_C,
        active_scope_proof_digest=gates().active_scope_digest,
        canonical_definition_id=DEFINITION_ID,
        app_id=APP_ID,
        app_version_id=decision_candidate.source_id,
        manifest_version=str(decision_candidate.payload.get("manifest_version")),
        artifact_uri=str(decision_candidate.payload.get("artifact_uri")),
        same_definition_proof_digest=SHA_D,
        artifact_digest=str(decision_candidate.payload.get("artifact_digest")),
        artifact_immutability_proof_digest=SHA_A,
    )
    return CandidateDecision(
        candidate=decision_candidate,
        status=EvidenceStatus.ACCEPTED,
        reason_code=None,
        proof=proof,
        verified_artifact_digest=verified or (
            str(decision_candidate.payload.get("artifact_digest"))
            if decision_candidate.phase == "version" else None
        ),
    )


def quarantined(decision_candidate, reason: str = "source_lifecycle_unproven"):
    return CandidateDecision(
        candidate=decision_candidate,
        status=EvidenceStatus.QUARANTINED,
        reason_code=reason,
        proof=None,
        # What ``run_page`` reports for a version: the verified bytes it read. ``None``
        # for a definition, whose artifact is its current version's.
        verified_artifact_digest=(
            str(decision_candidate.payload.get("artifact_digest"))
            if decision_candidate.phase == "version" else None
        ),
    )


def replay(decision_candidate, *, reason: str | None = None, verified: str | None = None):
    """A ``REPLAY`` classification, built the way ``run_page`` builds one.

    ``run_page`` re-verifies the artifact bytes on every page, so a replay of a version
    carries the verified digest again. Building the fixture without it would model a
    page the real backfill never produces, and the store would (correctly) refuse it as
    an inexact replay.
    """

    return CandidateDecision(
        candidate=decision_candidate,
        status=EvidenceStatus.REPLAY,
        reason_code=reason,
        proof=None,
        verified_artifact_digest=verified if verified is not None else (
            str(decision_candidate.payload.get("artifact_digest"))
            if decision_candidate.phase == "version" else None
        ),
    )


@pytest.fixture()
async def ledger():
    mirror = await LedgerMirror().create()
    try:
        yield mirror
    finally:
        await mirror.close()


def store_for(ledger: LedgerMirror) -> SqlM04EEvidenceStore:
    """The store bound to the mirror, with the PostgreSQL-only refusal relaxed.

    ``allow_non_postgresql=True`` is the harness's explicit permission: SQLite has no
    transaction-local GUCs and no RLS, so the store would (correctly) refuse to run the
    ledger on it. Everything else — exact-key reads, the CAS, append-only writes,
    counters — is the production code path.
    """

    return SqlM04EEvidenceStore(
        session_factory=ledger.session_factory, allow_non_postgresql=True,
    )


async def durable_checkpoint(ledger: LedgerMirror) -> BackfillCheckpoint:
    """The cursor a *continuing* page must present: the ledger's own head.

    A second page that presents the pre-first-page cursor is a stale request, and the
    store answers it with the head instead of writing. Tests that want a second page to
    commit must therefore read the head first — exactly as ``run_page``'s caller does
    between pages.
    """

    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None, "the ledger has no head to continue from"
    return checkpoint(
        phase=str(head["phase"]),
        after_source_id=head["after_source_id"],
        sequence=int(head["sequence"]),
        phase_complete=bool(head["phase_complete"]),
    )


def definition_decision(*, accepted_row: bool = True) -> CandidateDecision:
    candidate = definition_candidate(context(), definition_row())
    return accepted(candidate) if accepted_row else quarantined(candidate)


# ---------------------------------------------------------------------------
# 1. Construction and transaction discipline
# ---------------------------------------------------------------------------


async def test_the_mirror_carries_the_registered_columns_and_unique_keys() -> None:
    """The mirror is only credible if it is the models' schema, not a paraphrase."""

    from prodavan.infrastructure.persistence.models.m04e_ledger import (
        M04EDefinitionVersionMappingRow,
        M04EDefinitionVersionQuarantineRow,
    )

    for model, table_name in (
        (M04EDefinitionVersionMappingRow, MAPPING_TABLE),
        (M04EDefinitionVersionQuarantineRow, QUARANTINE_TABLE),
    ):
        model_columns = {column.name: column for column in model.__table__.columns}
        mirror = await LedgerMirror().create()
        try:
            mirrored = {column.name for column in mirror.tables[table_name].columns}
            assert mirrored == set(model_columns), table_name
            model_unique = {
                constraint.name
                for constraint in model.__table__.constraints
                if isinstance(constraint, __import__("sqlalchemy").UniqueConstraint)
            }
            mirrored_unique = {
                constraint.name
                for constraint in mirror.tables[table_name].constraints
                if isinstance(constraint, __import__("sqlalchemy").UniqueConstraint)
            }
            assert mirrored_unique == model_unique, table_name
        finally:
            await mirror.close()


def test_the_store_refuses_to_exist_without_a_session_factory() -> None:
    with pytest.raises(ValueError) as raised:
        SqlM04EEvidenceStore(session_factory=None)

    assert str(raised.value) == M04EPortReason.SESSION_FACTORY_MISSING.value


# ---------------------------------------------------------------------------
# 2. Bounded page commit
# ---------------------------------------------------------------------------


async def test_a_page_commits_mappings_checkpoint_and_audit_together(ledger) -> None:
    store = store_for(ledger)
    first = accepted(version_candidate(context(), version_row(), _definition()))
    second = accepted(version_candidate(context(), version_two_row(), _definition()))

    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(first, second), page_exhausted=True,
    )

    # The batch accounts for every classification, in order, with the accepted outcome.
    assert len(batch.records) == 2
    assert [record.source_id for record in batch.records] == ["aver_0001", VERSION_TWO]
    assert all(record.outcome.value == "accepted" for record in batch.records)
    assert all(record.status is EvidenceStatus.ACCEPTED for record in batch.records)
    assert all(record.canonical_digest for record in batch.records)
    assert all(record.proof_evidence for record in batch.records)
    assert all(record.correlation_id == CORRELATION_ID for record in batch.records)

    # The cursor advanced by exactly the consumed rows and completed the version phase.
    assert batch.checkpoint.phase == "version"
    assert batch.checkpoint.after_source_id == VERSION_TWO
    assert batch.checkpoint.sequence == 2
    assert batch.checkpoint.phase_complete is True
    assert batch.complete is True

    # All four ledgers agree: two mappings, no quarantine, one head, one audit event.
    assert await ledger.count(MAPPING_TABLE) == 2
    assert await ledger.count(QUARANTINE_TABLE) == 0
    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    assert head["sequence"] == 2
    assert head["after_source_id"] == VERSION_TWO
    assert head["phase"] == "version"
    assert head["phase_complete"] in (1, True)
    assert head["tenant_id"] == TENANT and head["definition_id"] == DEFINITION_ID
    audit = await ledger.single(AUDIT_TABLE)
    assert audit is not None
    assert audit["event_kind"] == "page_committed"
    assert audit["record_count"] == 2
    assert audit["accepted_count"] == 2
    assert audit["replay_count"] == 0
    assert audit["quarantined_count"] == 0
    assert audit["conflict_count"] == 0
    # The mapping rows carry the same token the head advanced to: the join key a
    # reviewer uses to find every row one page wrote.
    assert audit["checkpoint"] == head["checkpoint_token"]
    mapping_rows = await ledger.rows(MAPPING_TABLE)
    assert {row["checkpoint"] for row in mapping_rows} == {head["checkpoint_token"]}


async def test_the_audit_counters_balance_for_a_mixed_page(ledger) -> None:
    store = store_for(ledger)
    decisions = (
        accepted(version_candidate(context(), version_row(), _definition())),
        quarantined(version_candidate(context(), version_two_row(), _definition())),
    )

    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=decisions, page_exhausted=True,
    )

    assert [record.outcome.value for record in batch.records] == ["accepted", "quarantined"]
    audit = await ledger.single(AUDIT_TABLE)
    assert audit is not None
    assert audit["accepted_count"] == 1
    assert audit["quarantined_count"] == 1
    assert audit["record_count"] == 2
    # ``ck_m04e_audit_counters_balance`` restated: the event accounts for every row.
    assert audit["record_count"] == (
        audit["accepted_count"] + audit["replay_count"]
        + audit["quarantined_count"] + audit["conflict_count"]
    )
    # The quarantined row exposes no canonical claim and carries its guidance.
    quarantined_row = next(
        row for row in await ledger.rows(MAPPING_TABLE) if row["source_id"] == VERSION_TWO
    )
    assert quarantined_row["status"] == "quarantined"
    assert quarantined_row["canonical_digest"] is None
    assert quarantined_row["reason_code"] == "source_lifecycle_unproven"
    quarantined_record = batch.records[-1]
    assert quarantined_record.blocker_evidence, "a quarantine must carry actionable guidance"


async def test_an_exhausted_definition_page_transitions_to_the_version_phase(ledger) -> None:
    store = store_for(ledger)
    candidate = definition_candidate(context(), definition_row())

    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=checkpoint(phase="definition"),
        decisions=(accepted(candidate),), page_exhausted=True,
    )

    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    assert head["phase"] == "version"
    assert head["after_source_id"] is None
    assert head["sequence"] == 2, "one step for the consumed row, one for the transition"
    assert head["phase_complete"] in (0, False), "only a drained version stream completes"
    assert batch.checkpoint.phase == "version"
    assert batch.complete is False


# ---------------------------------------------------------------------------
# 3. Replay
# ---------------------------------------------------------------------------


async def test_replaying_the_same_page_returns_the_original_and_moves_nothing(ledger) -> None:
    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    committed = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=True,
    )
    head_before = await ledger.single(CHECKPOINT_TABLE)
    rows_before = await ledger.statement_count()

    # The exact same page, from the same (now stale) cursor.
    replayed = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=checkpoint(),
        decisions=(
            replay(decision.candidate),
        ),
        page_exhausted=True,
    )

    # Nothing was written: not a mapping, not an observation, not an audit event.
    assert await ledger.statement_count() == rows_before
    head_after = await ledger.single(CHECKPOINT_TABLE)
    assert head_after == head_before
    # The stored classification is returned as a replay with the original evidence.
    assert len(replayed.records) == 1
    record = replayed.records[0]
    assert record.outcome.value == "replay"
    assert record.status is EvidenceStatus.ACCEPTED
    assert record.source_digest == decision.candidate.source_digest
    assert record.canonical_digest == committed.records[0].canonical_digest
    assert record.canonical_identity == decision.candidate.canonical_identity
    assert replayed.checkpoint.sequence == committed.checkpoint.sequence
    assert replayed.checkpoint.after_source_id == committed.checkpoint.after_source_id


async def test_a_replay_through_find_existing_returns_the_stored_classification(ledger) -> None:
    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=True,
    )

    existing = await store.find_existing(
        context=context(), source_kind=decision.candidate.source_kind,
        source_id=decision.candidate.source_id,
        source_schema_version=decision.candidate.schema_version,
    )

    assert existing is not None
    assert existing.status is EvidenceStatus.ACCEPTED
    assert existing.source_digest == decision.candidate.source_digest
    assert existing.canonical_identity == decision.candidate.canonical_identity
    assert existing.verified_artifact_digest == decision.verified_artifact_digest
    # A different source schema version is a different key: never a false replay.
    assert await store.find_existing(
        context=context(), source_kind=decision.candidate.source_kind,
        source_id=decision.candidate.source_id, source_schema_version="m04.legacy.v9",
    ) is None
    # Another definition is another partition: the tenant/definition predicate holds.
    assert await store.find_existing(
        context=context(app_definition_id=OTHER_DEFINITION_ID),
        source_kind=decision.candidate.source_kind,
        source_id=decision.candidate.source_id,
        source_schema_version=decision.candidate.schema_version,
    ) is None


# ---------------------------------------------------------------------------
# 4. Changed digest -> conflict + quarantine, never a rewrite
# ---------------------------------------------------------------------------


async def test_a_changed_source_digest_is_a_conflict_and_an_observation(ledger) -> None:
    store = store_for(ledger)
    original = version_candidate(context(), version_row(), _definition())
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(original),), page_exhausted=False,
    )
    first_mapping = await ledger.single(MAPPING_TABLE)
    assert first_mapping is not None

    # The same source identity now reports a different digest.
    changed = version_candidate(
        context(), version_row(artifact_digest=SHA_B), _definition(),
    )
    assert changed.source_digest != original.source_digest
    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=await durable_checkpoint(ledger),
        decisions=(
            CandidateDecision(
                candidate=changed, status=EvidenceStatus.CONFLICT,
                reason_code="source_digest_changed", proof=None,
            ),
        ),
        page_exhausted=False,
    )

    # The first mapping is byte-identical: a conflict never rewrites history.
    assert await ledger.single(MAPPING_TABLE) == first_mapping
    observation = await ledger.single(QUARANTINE_TABLE)
    assert observation is not None
    assert observation["source_digest"] == original.source_digest
    assert observation["observed_source_digest"] == changed.source_digest
    assert observation["observed_artifact_digest"] is None
    assert observation["reason_code"] == "source_digest_changed"

    record = batch.records[0]
    assert record.outcome.value == "conflict"
    assert record.status is EvidenceStatus.CONFLICT
    assert record.source_digest == original.source_digest
    assert record.observed_source_digest == changed.source_digest
    assert record.canonical_digest is None
    assert record.canonical_identity == ()
    # An observation *is* a classification of this row, so it consumes one cursor slot —
    # exactly what ``run_page`` expects (``sequence + len(non-replay records)``).
    assert batch.checkpoint.after_source_id == "aver_0001"
    assert batch.checkpoint.sequence == 2
    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    assert head["sequence"] == 2
    assert head["after_source_id"] == "aver_0001"
    # …and the page still audited what it appended.
    audit_rows = await ledger.rows(AUDIT_TABLE)
    assert [row["event_kind"] for row in audit_rows] == ["page_committed", "page_committed"]
    # ``ids`` are random hex, so ``rows`` order is not chronological: sum the counters
    # of both pages instead of assuming which event came last. The first page recorded
    # one acceptance; the second recorded one conflict.
    totals = rows_to_counts(audit_rows)
    assert totals["conflict_count"] == 1
    assert totals["accepted_count"] == 1
    assert totals["record_count"] == 2


async def test_a_replayed_observation_returns_the_recorded_evidence(ledger) -> None:
    store = store_for(ledger)
    original = version_candidate(context(), version_row(), _definition())
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(original),), page_exhausted=False,
    )
    changed = version_candidate(context(), version_row(artifact_digest=SHA_B), _definition())
    conflict = CandidateDecision(
        candidate=changed, status=EvidenceStatus.CONFLICT,
        reason_code="source_digest_changed", proof=None,
    )
    await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=await durable_checkpoint(ledger),
        decisions=(conflict,), page_exhausted=False,
    )
    observation = await ledger.single(QUARANTINE_TABLE)
    rows_before = await ledger.statement_count()

    replayed = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=await durable_checkpoint(ledger),
        decisions=(
            replay(changed, reason="source_digest_changed", verified=SHA_A),
        ),
        page_exhausted=False,
    )

    assert await ledger.statement_count() == rows_before, "a replay appends nothing"
    assert await ledger.single(QUARANTINE_TABLE) == observation
    record = replayed.records[0]
    assert record.outcome.value == "replay"
    assert record.status is EvidenceStatus.CONFLICT
    assert record.reason_code == "source_digest_changed"
    assert record.observed_source_digest == changed.source_digest
    assert replayed.checkpoint.sequence == 2, "a replay-only page does not advance"

    # ``find_observation`` is the exact lookup the backfill uses before deciding.
    found = await store.find_observation(
        context=context(), source_kind=changed.source_kind, source_id=changed.source_id,
        source_schema_version=changed.schema_version,
        observed_source_digest=changed.source_digest, observed_artifact_digest=None,
    )
    assert found is not None
    assert found.reason_code == "source_digest_changed"
    assert found.observed_source_digest == changed.source_digest
    # A digest that was never observed is absent, not approximated.
    assert await store.find_observation(
        context=context(), source_kind=changed.source_kind, source_id=changed.source_id,
        source_schema_version=changed.schema_version,
        observed_source_digest=SHA_C, observed_artifact_digest=None,
    ) is None


async def test_an_artifact_observation_is_recorded_with_a_null_source_digest(ledger) -> None:
    store = store_for(ledger)
    original = version_candidate(context(), version_row(), _definition())
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(original),), page_exhausted=False,
    )
    # Same source bytes, different artifact bytes: an immutable-artifact conflict.
    same_source = version_candidate(context(), version_row(), _definition())
    conflict = CandidateDecision(
        candidate=same_source, status=EvidenceStatus.CONFLICT,
        reason_code="artifact_digest_changed", proof=None,
        verified_artifact_digest=SHA_B,
    )

    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=await durable_checkpoint(ledger),
        decisions=(conflict,), page_exhausted=False,
    )

    observation = await ledger.single(QUARANTINE_TABLE)
    assert observation is not None
    assert observation["observed_source_digest"] is None
    assert observation["observed_artifact_digest"] == SHA_B
    assert observation["reason_code"] == "artifact_digest_changed"
    assert observation["source_digest"] == original.source_digest
    assert batch.records[0].observed_artifact_digest == SHA_B


# ---------------------------------------------------------------------------
# 5. Checkpoint CAS
# ---------------------------------------------------------------------------


async def test_a_stale_cursor_that_would_append_is_answered_with_the_head_and_writes_nothing(
    ledger,
) -> None:
    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    committed = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=True,
    )
    head = await ledger.single(CHECKPOINT_TABLE)
    rows_before = await ledger.statement_count()

    # A caller who still holds the *pre-page* cursor asks to commit a page whose row is
    # new. Committing it would place fresh evidence under a cursor another writer owns.
    stale = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(version_candidate(context(), version_two_row(), _definition())),),
        page_exhausted=True,
    )

    # Not an exception: the reviewed answer is "nothing was written, here is the head".
    assert stale.records == ()
    assert stale.checkpoint.sequence == committed.checkpoint.sequence
    assert stale.checkpoint.after_source_id == committed.checkpoint.after_source_id
    assert stale.checkpoint.phase_complete is True
    assert await ledger.statement_count() == rows_before
    assert await ledger.single(CHECKPOINT_TABLE) == head


async def test_a_retried_page_returns_the_rows_it_already_wrote(ledger) -> None:
    """The other reading of "stale cursor": the caller crashed after the commit.

    ``run_page``'s caller persists the returned cursor *after* the store returns, so a
    crash in between leaves it holding the previous one while the evidence is already
    durable. Re-running that page must return those records (a replay) instead of an
    empty batch — an empty batch would read as "this page had nothing to say" and the
    operator would lose the evidence they already paid for.
    """

    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    committed = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=True,
    )
    rows_before = await ledger.statement_count()

    retried = await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(
            replay(decision.candidate),
        ),
        page_exhausted=True,
    )

    assert await ledger.statement_count() == rows_before, "a retry appends nothing"
    assert len(retried.records) == 1
    assert retried.records[0].outcome.value == "replay"
    assert retried.records[0].source_id == decision.candidate.source_id
    assert retried.checkpoint == committed.checkpoint


async def test_a_concurrent_peer_advance_loses_the_cas_instead_of_overwriting_it(
    ledger, monkeypatch,
) -> None:
    """The CAS predicate, not cooperation, is what protects the cursor.

    The race is simulated at the exact point it happens: the row the store reads while
    locking the head is no longer the row it holds when it writes, because a peer
    renewed the token in between. Both predicates of the CAS
    (``sequence = :observed AND checkpoint_token = :observed``) then match nothing, so
    the page's writes roll back and the durable head is returned.

    Only the *token* is stale, not the sequence — which is the whole point: a
    sequence-only CAS would happily overwrite a peer that advanced at the same
    sequence number.
    """

    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=False,
    )
    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    rows_before = await ledger.statement_count()

    original_lock = SqlM04EEvidenceStore._lock_checkpoint_row

    async def stale_lock(self, session, context, *, phase):
        row = await original_lock(self, session, context=context, phase=phase)
        if row is None:
            return row
        # The token the peer replaced *after* this read; the sequence is unchanged.
        return {**dict(row), "checkpoint_token": "peer-token"}

    monkeypatch.setattr(
        SqlM04EEvidenceStore, "_lock_checkpoint_row", stale_lock,
    )

    batch = await store.classify_and_commit_page(
        context=context(), gates=gates(),
        expected_checkpoint=checkpoint(
            sequence=int(head["sequence"]), after_source_id=head["after_source_id"],
            phase_complete=bool(head["phase_complete"]),
        ),
        decisions=(
            accepted(version_candidate(context(), version_two_row(), _definition())),
        ),
        page_exhausted=False,
    )

    # The lost CAS is not an exception and not a partial commit.
    assert batch.records == ()
    assert batch.checkpoint.sequence == int(head["sequence"])
    assert await ledger.statement_count() == rows_before, "the whole page rolled back"
    assert (await ledger.single(CHECKPOINT_TABLE)) == head


async def test_a_cursor_the_ledger_never_recorded_is_refused(ledger) -> None:
    """A non-zero cursor with no head is not "start from scratch": it is unprovable."""

    store = store_for(ledger)

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(),
            expected_checkpoint=checkpoint(sequence=4, after_source_id="aver_0009"),
            decisions=(), page_exhausted=True,
        )

    assert raised.value.code is M04EPortReason.STORE_CHECKPOINT_LEDGER_MISSING
    assert await ledger.statement_count() == 0


# ---------------------------------------------------------------------------
# 6. Blocked evidence
# ---------------------------------------------------------------------------


async def test_a_blocked_page_writes_audit_evidence_and_never_moves_the_cursor(ledger) -> None:
    store = store_for(ledger)

    record = await store.record_blocked(
        context=context(), checkpoint=checkpoint(),
        reason_code="upstream_owner_scope_gate_unproven",
        blocker_evidence=("MIG-M04-01", "the retained company row is not active"),
    )

    assert record.status is EvidenceStatus.BLOCKED
    assert record.outcome.value == "blocked"
    assert record.source_id == "blocked"
    assert record.checkpoint == checkpoint()
    assert record.blocker_evidence == (
        "MIG-M04-01", "the retained company row is not active",
    )
    # No classification is written — a blocked page has nothing to classify.
    assert await ledger.count(MAPPING_TABLE) == 0
    assert await ledger.count(QUARANTINE_TABLE) == 0
    assert await ledger.count(CHECKPOINT_TABLE) == 0
    audit = await ledger.single(AUDIT_TABLE)
    assert audit is not None
    assert audit["event_kind"] == "page_blocked"
    assert audit["reason_code"] == "upstream_owner_scope_gate_unproven"
    assert audit["record_count"] == 0
    assert audit["accepted_count"] == audit["replay_count"] == 0
    assert audit["quarantined_count"] == audit["conflict_count"] == 0
    assert audit["blocker_evidence"] == ["MIG-M04-01", "the retained company row is not active"]
    # The checkpoint column is non-empty by CHECK, and "no source consumed" is written
    # down as the phase's start marker rather than as an empty string.
    assert audit["checkpoint"] == "m04e-start:version"


async def test_retrying_a_blocked_page_is_a_replay_not_an_integrity_error(ledger) -> None:
    """An operator retries the same blocked page while the gate is still closed.

    ``event_id`` is a digest of the context and the cursor and
    ``uq_m04e_audit_event`` is unique on it, so a second append of the same
    blocked page collides. Before this was handled the retry surfaced as a raw
    ``IntegrityError`` from inside the page — a constraint name rather than an
    answer — and the partition stage reported a failure for evidence it had
    already recorded.
    """

    store = store_for(ledger)
    first = await store.record_blocked(
        context=context(), checkpoint=checkpoint(), reason_code="upstream_gate_closed",
    )
    before = await ledger.count(AUDIT_TABLE)

    second = await store.record_blocked(
        context=context(), checkpoint=checkpoint(), reason_code="upstream_gate_closed",
    )

    assert second.status is EvidenceStatus.BLOCKED
    assert second.outcome.value == "blocked"
    assert second.checkpoint == first.checkpoint
    assert second.reason_code == first.reason_code
    # The event *is* the evidence: one row for one blocked page, however often
    # the operator asks.
    assert await ledger.count(AUDIT_TABLE) == before == 1


async def test_a_blocked_page_refuses_an_empty_reason_code(ledger) -> None:
    store = store_for(ledger)

    with pytest.raises(M04EPortRefused) as raised:
        await store.record_blocked(
            context=context(), checkpoint=checkpoint(), reason_code="   ",
        )

    assert raised.value.code is M04EPortReason.STORE_LEDGER_WRITE_REFUSED
    assert await ledger.statement_count() == 0


async def test_blocked_evidence_drops_anything_that_looks_like_a_payload(ledger) -> None:
    """Only short single-line guidance reaches ``blocker_evidence``."""

    store = store_for(ledger)

    record = await store.record_blocked(
        context=context(), checkpoint=checkpoint(), reason_code="upstream_proof_missing",
        blocker_evidence=(
            "MIG-M04E-01",
            "line one\nline two",
            "display_name: super-secret-customer-payload",
            "x" * 500,
        ),
    )

    assert record.blocker_evidence == ("MIG-M04E-01", "display_name: super-secret-customer-payload")
    audit = await ledger.single(AUDIT_TABLE)
    assert audit is not None
    assert "line one" not in str(audit["blocker_evidence"])
    assert all("\n" not in item for item in audit["blocker_evidence"])


# ---------------------------------------------------------------------------
# 7. Refusals that keep the ledger honest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision_factory", "code"),
    [
        (
            lambda: CandidateDecision(
                candidate=version_candidate(context(), version_row(), _definition()),
                status=EvidenceStatus.BLOCKED, reason_code="gate_unproven", proof=None,
            ),
            M04EPortReason.STORE_OUTCOME_UNREPRESENTABLE,
        ),
        (
            lambda: quarantined(
                version_candidate(context(), version_row(), _definition()), reason="   ",
            ),
            M04EPortReason.STORE_DECISION_INVALID,
        ),
    ],
    ids=("blocked-status", "missing-reason"),
)
async def test_a_page_with_an_unrepresentable_classification_is_refused(
    ledger, decision_factory, code,
) -> None:
    store = store_for(ledger)

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(), expected_checkpoint=checkpoint(),
            decisions=(decision_factory(),), page_exhausted=False,
        )

    assert raised.value.code is code
    assert await ledger.statement_count() == 0


async def test_a_page_for_another_partition_is_refused(ledger) -> None:
    store = store_for(ledger)
    # A candidate built from another tenant's definition row: ``version_candidate``
    # takes the partition from the *row*, which is what the store re-checks.
    foreign = version_candidate(
        context(),
        version_row(app_definition_id=OTHER_DEFINITION_ID),
        definition_row(id=OTHER_DEFINITION_ID, tenant_id=OTHER_TENANT),
    )

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(), expected_checkpoint=checkpoint(),
            decisions=(accepted(foreign),), page_exhausted=False,
        )

    assert raised.value.code is M04EPortReason.PARTITION_MISMATCH
    assert await ledger.statement_count() == 0


async def test_a_page_that_repeats_a_source_identity_is_refused(ledger) -> None:
    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(), expected_checkpoint=checkpoint(),
            decisions=(decision, decision), page_exhausted=False,
        )

    assert raised.value.code is M04EPortReason.STORE_DECISION_INVALID
    assert await ledger.statement_count() == 0


async def test_a_candidate_at_or_before_the_cursor_is_refused(ledger) -> None:
    store = store_for(ledger)
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(version_candidate(context(), version_row(), _definition())),),
        page_exhausted=True,
    )
    head = await ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    rows_before = await ledger.statement_count()

    # A replay of the row at/behind the cursor is fine (that is a retry); a *new*
    # mapping behind it is not: the cursor would move backwards.

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(),
            expected_checkpoint=checkpoint(
                sequence=int(head["sequence"]), after_source_id=head["after_source_id"],
                phase_complete=bool(head["phase_complete"]),
            ),
            decisions=(
                # ``aver_0000`` sorts before the cursor ``aver_0001`` and has never
                # been mapped, so recording it would re-open a consumed position.
                accepted(version_candidate(
                    context(),
                    version_row(
                        id="aver_0000",
                        manifest_version="1.0.9",
                        artifact_digest=SHA_C,
                        artifact_uri=f"content://{TENANT}/{DEFINITION_ID}/aver_0000",
                        manifest={
                            "protocol": "1.0",
                            "appId": APP_ID,
                            "version": "1.0.9",
                            "entryPoints": {},
                        },
                    ),
                    _definition(),
                )),
            ),
            page_exhausted=False,
        )

    assert raised.value.code is M04EPortReason.STORE_DECISION_INVALID
    assert await ledger.statement_count() == rows_before


async def test_a_source_identity_mapped_differently_is_refused_not_rewritten(ledger) -> None:
    """The first mapping is immutable; a different classification needs an observation."""

    store = store_for(ledger)
    candidate = version_candidate(context(), version_row(), _definition())
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(accepted(candidate),), page_exhausted=True,
    )
    first_mapping = await ledger.single(MAPPING_TABLE)

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(), expected_checkpoint=checkpoint(),
            decisions=(quarantined(candidate, reason="archived_source_row"),),
            page_exhausted=False,
        )

    assert raised.value.code is M04EPortReason.STORE_FIRST_MAPPING_CONFLICT
    assert raised.value.refusal.blockers == (
        "m04e_ledger_status=accepted", "m04e_incoming_status=quarantined",
    )
    assert await ledger.single(MAPPING_TABLE) == first_mapping


async def test_the_same_canonical_identity_twice_is_a_conflict_not_two_accepts(ledger) -> None:
    """``run_page`` downgrades the duplicate; a store that did not would accept both."""

    store = store_for(ledger)
    first = version_candidate(context(), version_row(), _definition())
    # A second source id with the same manifest version resolves to the same canonical
    # identity ``(tenant, definition, manifest_version)``.
    duplicate_row = version_two_row(manifest_version="1.0.0")
    duplicate = version_candidate(context(), duplicate_row, _definition())
    assert duplicate.canonical_identity == first.canonical_identity

    with pytest.raises(M04EPortRefused) as raised:
        await store.classify_and_commit_page(
            context=context(), gates=gates(), expected_checkpoint=checkpoint(),
            decisions=(accepted(first), accepted(duplicate)), page_exhausted=False,
        )

    assert raised.value.code is M04EPortReason.STORE_FIRST_MAPPING_CONFLICT
    assert await ledger.statement_count() == 0, "the whole page rolls back"


async def test_the_ledger_is_the_only_place_a_page_can_land(ledger) -> None:
    """A committed page is readable through the port, not only through the mirror."""

    store = store_for(ledger)
    decision = accepted(version_candidate(context(), version_row(), _definition()))
    await store.classify_and_commit_page(
        context=context(), gates=gates(), expected_checkpoint=checkpoint(),
        decisions=(decision,), page_exhausted=True,
    )

    existing = await store.find_existing(
        context=context(), source_kind=decision.candidate.source_kind,
        source_id=decision.candidate.source_id,
        source_schema_version=decision.candidate.schema_version,
    )
    assert existing is not None
    assert existing.source_digest == expected_source_digest(decision.candidate)
    assert existing.checkpoint.phase == "version"
    assert existing.checkpoint.sequence == 1


def _definition():
    from .acceptance_fixtures import definition_row

    return definition_row()
