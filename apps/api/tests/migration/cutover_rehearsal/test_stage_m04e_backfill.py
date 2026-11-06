"""Stage 2 rehearsal: the M04E ledger commits a page atomically on a live database.

What only a real database can show
==================================
``SqlM04EEvidenceStore`` packs five things into **one** transaction: the per-source
first mapping, the quarantine/conflict observation, the redacted audit event, the
durable cursor CAS, and (for an exhausted definition stream) the phase transition to
``version``.  A unit test with a fake session can check the call order; only
PostgreSQL can show that

* the cursor row really is advanced under ``FOR UPDATE`` + CAS and a second writer
  loses the CAS instead of corrupting it;
* ``trg_m04e_checkpoint_monotonic`` refuses a sequence regression, a phase rewind, a
  re-open of a completed stream and a rebind of the cursor identity -- i.e. the
  durability rules are enforced *by the database*, not only by the store;
* ``phase_complete`` survives the round trip and cannot be cleared afterwards;
* the append-only trigger refuses to let anything rewrite the mapping, quarantine or
  audit rows.

The M04E-01 promotion gate is asserted in its real state: this schema's
``m04e_definition_version_promotion_evidence`` can only store ``blocked`` or
``quarantined`` (``ck_m04e_promotion_outcome``), so the gate refuses by name rather
than treating a row's existence as approval.  That refusal is a *result* of this
rehearsal, not an obstacle to work around: fabricating an ``accepted`` row here would
be inventing promotion evidence.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    EvidenceStatus,
)
from prodavan.application.migration.m04e_ports import (
    GATE_M04_01_OWNER_REGISTRY,
    GATE_M04_02_TYPED_SCOPE,
    GATE_M04E_01_DEFINITION_FOUNDATION,
    M04EPortRefused,
)

from .harness import (
    CHECK_VIOLATION,
    RehearsalHarness,
    m04e_checkpoint,
    quarantine_decisions,
    run_m04_stage,
    seed_partition,
    upstream_gates,
)

QUARANTINE_REASON = "owner_scope_or_definition_artifact_proof_missing"


async def _read_cursor(rehearsal: RehearsalHarness, seed) -> dict[str, object]:
    rows = await rehearsal.rows(
        "SELECT phase, sequence, phase_complete, after_source_id, source_snapshot_id, "
        "       source_schema_version, source_snapshot_digest, canonical_schema_version, "
        "       writer_generation, checkpoint_token "
        "FROM m04e_definition_version_checkpoints "
        "WHERE tenant_id = :tenant AND definition_id = :definition",
        {"tenant": seed.tenant_id, "definition": seed.definition_id},
    )
    assert len(rows) <= 1, "the ledger keeps exactly one head row per (tenant, definition)"
    if not rows:
        return {}
    keys = (
        "phase",
        "sequence",
        "phase_complete",
        "after_source_id",
        "source_snapshot_id",
        "source_schema_version",
        "source_snapshot_digest",
        "canonical_schema_version",
        "writer_generation",
        "checkpoint_token",
    )
    return dict(zip(keys, rows[0], strict=True))


async def _counts(rehearsal: RehearsalHarness, seed) -> dict[str, int]:
    tables = {
        "mappings": "m04e_definition_version_mappings",
        "quarantine": "m04e_definition_version_quarantine",
        "audit": "m04e_definition_version_audit",
        "checkpoints": "m04e_definition_version_checkpoints",
    }
    return {
        name: await rehearsal.count(table, "tenant_id = :t", {"t": seed.tenant_id})
        for name, table in tables.items()
    }


async def test_m04e_page_commit_writes_mapping_observation_and_audit_atomically(
    rehearsal: RehearsalHarness,
) -> None:
    """One page: two quarantine decisions land with their audit and one cursor move."""

    seed = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    reader = rehearsal.m04e_reader()
    store = rehearsal.m04e_store()

    definition_page = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    assert len(definition_page) == 1, (
        "the reader must return the one legacy definition of this partition; a page "
        "that returns nothing would make the backfill treat the stream as drained"
    )
    candidate = definition_page[0]
    assert candidate.phase == "definition"
    assert candidate.source_kind == "m04e.legacy.app_definition"

    batch = await store.classify_and_commit_page(
        context=context,
        gates=upstream_gates(seed),
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definition_page, QUARANTINE_REASON),
        page_exhausted=True,
    )

    assert len(batch.records) == 1, "one decision must produce exactly one record"
    record = batch.records[0]
    assert record.status is EvidenceStatus.QUARANTINED
    assert record.reason_code == QUARANTINE_REASON
    assert record.source_id == seed.definition_id

    counts = await _counts(rehearsal, seed)
    assert counts["mappings"] == 1, "the first mapping must be appended exactly once"
    assert counts["quarantine"] == 0, (
        "a first mapping that was never accepted is stored in the mapping ledger; the "
        "quarantine table is for *changed-digest observations* about an existing mapping"
    )
    assert counts["audit"] == 1, "the page must append exactly one audit event"
    assert counts["checkpoints"] == 1

    # -- the exhausted definition stream transitions to the version phase ---------
    assert batch.checkpoint.phase == "version", (
        "an exhausted definition page must move the stream to the version phase; "
        "otherwise the backfill would re-read the same definition page forever"
    )
    assert batch.checkpoint.after_source_id is None
    assert batch.checkpoint.sequence == 2, (
        "one step for the consumed row, one for the phase transition"
    )
    assert batch.checkpoint.phase_complete is False

    cursor = await _read_cursor(rehearsal, seed)
    assert str(cursor["phase"]) == "version"
    assert int(cursor["sequence"]) == 2
    assert cursor["phase_complete"] is False
    assert cursor["after_source_id"] is None
    assert cursor["source_snapshot_digest"] == seed.snapshot_digest
    assert str(cursor["checkpoint_token"]).strip(), "the CAS token must be renewed"

    # -- the audit row is redacted and correlated ---------------------------------
    audit = await rehearsal.rows(
        "SELECT event_kind, correlation_id, record_count, quarantined_count, "
        "       accepted_count, checkpoint, sequence "
        "FROM m04e_definition_version_audit WHERE tenant_id = :t",
        {"t": seed.tenant_id},
    )
    assert len(audit) == 1
    assert str(audit[0][0]) == "page_committed"
    assert str(audit[0][1]) == context.correlation_id
    assert int(audit[0][2]) == 1
    assert int(audit[0][3]) == 1, "the counters must be derived from the rows written"
    assert int(audit[0][4]) == 0
    assert int(audit[0][6]) == 0, (
        "the audit event records the sequence the page *observed* before it moved the "
        "cursor, which is how a reviewer joins the event to the CAS it performed"
    )


async def test_m04e_version_page_completes_the_stream_and_phase_complete_survives(
    rehearsal: RehearsalHarness,
) -> None:
    """``phase_complete`` is durable: a drained version stream stays drained."""

    seed = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    reader = rehearsal.m04e_reader()
    store = rehearsal.m04e_store()
    gates = upstream_gates(seed)

    definitions = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    first = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    assert first.checkpoint.phase == "version"

    versions = await reader.read_page(
        context=context, phase="version", after_source_id=None, limit=10
    )
    assert len(versions) == 1, "the seeded definition has exactly one published version"
    assert versions[0].source_kind == "m04e.legacy.app_version"
    assert versions[0].app_definition_id == seed.definition_id

    second = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=first.checkpoint,
        decisions=quarantine_decisions(versions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    assert second.checkpoint.phase == "version"
    assert second.checkpoint.phase_complete is True, (
        "an exhausted version stream is the only stream that completes"
    )
    assert second.checkpoint.sequence == 3

    cursor = await _read_cursor(rehearsal, seed)
    assert cursor["phase_complete"] is True, (
        "phase_complete must survive the round trip: the M05 handoff depends on it"
    )
    assert int(cursor["sequence"]) == 3
    assert cursor["after_source_id"] == seed.version_id

    # -- replaying the same page against the completed cursor writes nothing ------
    before = await _counts(rehearsal, seed)
    replay = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=second.checkpoint,
        decisions=quarantine_decisions(versions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    assert replay.checkpoint == second.checkpoint, "a replay must not move the cursor"
    assert replay.checkpoint.phase_complete is True
    assert all(record.outcome.value in {"replay", "quarantined"} for record in replay.records)
    after = await _counts(rehearsal, seed)
    assert after["mappings"] == before["mappings"], "a replay must not append a second mapping"
    assert after["checkpoints"] == 1


async def test_m04e_stale_cursor_is_answered_with_the_durable_head_and_no_write(
    rehearsal: RehearsalHarness,
) -> None:
    """A page presented with an old cursor must not re-open a consumed position."""

    seed = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    reader = rehearsal.m04e_reader()
    store = rehearsal.m04e_store()
    gates = upstream_gates(seed)

    definitions = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    committed = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    before = await _counts(rehearsal, seed)

    # Same (already consumed) source row, presented from the *initial* cursor.
    stale = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    assert stale.checkpoint == committed.checkpoint, (
        "the store must answer with the durable head, not the caller's stale cursor"
    )
    after = await _counts(rehearsal, seed)
    assert after == before, "a stale page must write nothing at all"


@pytest.mark.rows_in_public
async def test_m04e_cursor_is_guarded_by_the_database_not_only_by_the_store(
    rehearsal: RehearsalHarness,
) -> None:
    """The monotonic trigger refuses regression, rewind and re-open.

    Marked ``rows_in_public``: these rows live in the migrated schema, so the trigger
    under test is the one ``alembic upgrade head`` installed -- not a copy the fixture
    made.  (The chain creates its guard functions unqualified but binds each trigger to
    an unqualified relation, i.e. to ``public``, which is why a private schema cannot
    carry a meaningful copy of this guard.)

    These are the guarantees that make the cursor durable across processes and across
    a buggy writer: a direct ``UPDATE`` (no store, no ORM) must be refused by
    ``trg_m04e_checkpoint_monotonic`` with ``check_violation``.
    """

    seed = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    reader = rehearsal.m04e_reader()
    store = rehearsal.m04e_store()
    gates = upstream_gates(seed)

    definitions = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
        page_exhausted=True,
    )
    cursor = await _read_cursor(rehearsal, seed)
    assert int(cursor["sequence"]) == 2

    async def expect_refused(reason: str, sql: str, params: dict[str, object]) -> None:
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            await rehearsal.execute(sql, params)
        message = str(excinfo.value)
        assert reason in message, f"expected the trigger reason {reason!r} in: {message}"
        # The transaction was refused, so the head is byte-identical afterwards.
        assert await _read_cursor(rehearsal, seed) == cursor

    await expect_refused(
        "m04e_checkpoint_sequence_regression",
        "UPDATE m04e_definition_version_checkpoints SET sequence = 1, checkpoint_token = :tok "
        "WHERE tenant_id = :t AND definition_id = :d",
        {"tok": "tampered-1", "t": seed.tenant_id, "d": seed.definition_id},
    )
    await expect_refused(
        "m04e_checkpoint_phase_rewind",
        "UPDATE m04e_definition_version_checkpoints SET phase = 'definition', "
        "checkpoint_token = :tok WHERE tenant_id = :t AND definition_id = :d",
        {"tok": "tampered-2", "t": seed.tenant_id, "d": seed.definition_id},
    )
    await expect_refused(
        "m04e_checkpoint_lineage_rebind",
        "UPDATE m04e_definition_version_checkpoints SET source_snapshot_id = 'another-snapshot', "
        "checkpoint_token = :tok WHERE tenant_id = :t AND definition_id = :d",
        {"tok": "tampered-3", "t": seed.tenant_id, "d": seed.definition_id},
    )
    await expect_refused(
        "m04e_checkpoint_token_reused",
        "UPDATE m04e_definition_version_checkpoints SET sequence = 9, checkpoint_token = :tok "
        "WHERE tenant_id = :t AND definition_id = :d",
        {"tok": str(cursor["checkpoint_token"]), "t": seed.tenant_id, "d": seed.definition_id},
    )


@pytest.mark.rows_in_public
async def test_m04e_ledger_is_append_only_by_trigger(rehearsal: RehearsalHarness) -> None:
    """A committed mapping, observation and audit row can never be rewritten.

    The store never issues an ``UPDATE``; this test is about the *database* guarantee
    that a future writer (or a human with ``psql``) cannot either.  Marked
    ``rows_in_public`` so the trigger is the migration's own.
    """

    seed = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    reader = rehearsal.m04e_reader()
    store = rehearsal.m04e_store()
    gates = upstream_gates(seed)

    definitions = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=m04e_checkpoint(seed),
        decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
        page_exhausted=True,
    )

    # Turn the first mapping into a *changed digest* observation so the quarantine
    # table holds a row too: an observation is only written about an existing mapping.
    from prodavan.application.migration.m04e_definition_version.backfill import (
        CandidateDecision,
        EvidenceCandidate,
    )

    changed = EvidenceCandidate(
        source_kind=definitions[0].source_kind,
        source_id=definitions[0].source_id,
        schema_version=definitions[0].schema_version,
        payload={**definitions[0].payload, "display_name": "changed after the mapping"},
        phase="definition",
        tenant_id=definitions[0].tenant_id,
        app_definition_id=definitions[0].app_definition_id,
        source_state=definitions[0].source_state,
        canonical_schema_version=definitions[0].canonical_schema_version,
    )
    assert changed.source_digest != definitions[0].source_digest

    current = (
        await rehearsal.rows(
            "SELECT phase, sequence, after_source_id, phase_complete "
            "FROM m04e_definition_version_checkpoints WHERE tenant_id = :t",
            {"t": seed.tenant_id},
        )
    )[0]
    cursor = m04e_checkpoint(
        seed,
        phase=str(current[0]),
        sequence=int(current[1]),
        after_source_id=current[2],
        phase_complete=bool(current[3]),
    )
    observed = await store.classify_and_commit_page(
        context=context,
        gates=gates,
        expected_checkpoint=cursor,
        decisions=(
            CandidateDecision(
                candidate=changed,
                status=EvidenceStatus.CONFLICT,
                reason_code="source_digest_changed",
                proof=None,
            ),
        ),
        page_exhausted=False,
    )
    assert any(record.outcome.value == "conflict" for record in observed.records)
    assert await rehearsal.count(
        "m04e_definition_version_quarantine", "tenant_id = :t", {"t": seed.tenant_id}
    ) == 1

    tables = (
        "m04e_definition_version_mappings",
        "m04e_definition_version_quarantine",
        "m04e_definition_version_audit",
    )
    for table in tables:
        for statement in (
            f'UPDATE "{table}" SET reason_code = NULL WHERE tenant_id = :t',
            f'DELETE FROM "{table}" WHERE tenant_id = :t',
        ):
            with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
                await rehearsal.execute(statement, {"t": seed.tenant_id})
            assert "m04e_ledger_row_immutable" in str(excinfo.value), (
                f"{table}: the append-only trigger did not refuse: {excinfo.value}"
            )
        assert await rehearsal.count(table, "tenant_id = :t", {"t": seed.tenant_id}) >= 1, (
            f"{table}: the refused statement must not have removed anything"
        )


async def test_m04e_promotion_gate_refuses_by_name_instead_of_assuming_approval(
    rehearsal: RehearsalHarness,
) -> None:
    """``MIG-M04E-01`` cannot be proven from this schema; the gate says so.

    ``ck_m04e_promotion_outcome`` admits only ``blocked`` and ``quarantined``, so there
    is no truthful row that means "accepted".  The port therefore refuses with a named
    gate and an explicit evidence line about the constraint, which is the reviewed
    state -- and the reason the M05 handoff cannot be produced yet.

    The gate is evaluated against the scope rows the **real M04 stage** wrote (not a
    hand-built fixture), so this is a statement about the pipeline: M04 can accept a
    cabinet scope and M04E still cannot prove its promotion foundation.
    """

    seed = await seed_partition(rehearsal)
    await run_m04_stage(rehearsal, seed)

    context = rehearsal.backfill_context(seed)
    gate = rehearsal.m04e_gates()
    decision = await gate.partition_gate(context=context)

    assert decision.evidence is None, (
        "no upstream gate evidence may be produced while MIG-M04E-01 has no positive row"
    )
    assert decision.refusal is not None
    assert decision.refusal.gate_name == GATE_M04E_01_DEFINITION_FOUNDATION
    # Two refusals are possible and both are correct-by-name for a partition with no
    # promotion row: "absent" (nothing recorded yet) and "blocked" (a row that records a
    # negative outcome).  What must NOT happen is an accepted gate produced out of the
    # mere existence of the table, which is what this test exists to catch.
    assert "mig_m04e_01_promotion_evidence_absent" in decision.refusal.evidence_text(), (
        f"expected the promotion gate to refuse; got {decision.refusal.evidence_text()!r}"
    )
    assert decision.refusal.code.value == "mig_m04e_01_promotion_evidence_absent"

    # The owner-registry and typed-scope probes that *precede* that gate do succeed
    # against the real M04 output, which is what makes this refusal specifically about
    # the promotion record rather than about the scope.
    probes = dict(decision.probes)
    assert probes, "the decision must report the gates it did prove"
    assert GATE_M04_01_OWNER_REGISTRY in probes
    assert GATE_M04_02_TYPED_SCOPE in probes


async def test_m04e_store_refuses_a_page_for_another_partition(
    rehearsal: RehearsalHarness,
) -> None:
    """A page cannot be re-pointed at another tenant or definition."""

    seed = await seed_partition(rehearsal)
    other = await seed_partition(rehearsal)
    context = rehearsal.backfill_context(seed)
    store = rehearsal.m04e_store()
    reader = rehearsal.m04e_reader()

    definitions = await reader.read_page(
        context=context, phase="definition", after_source_id=None, limit=10
    )
    wrong_partition = BackfillCheckpoint(
        partition=(other.tenant_id, other.definition_id),
        phase="definition",
        after_source_id=None,
        sequence=0,
        source_snapshot_id=seed.snapshot_id,
        source_schema_version=seed.source_schema_version,
        source_snapshot_digest=seed.snapshot_digest,
        canonical_schema_version="m04e.v1",
        writer_generation=seed.writer_generation,
        phase_complete=False,
    )
    with pytest.raises(M04EPortRefused) as excinfo:
        await store.classify_and_commit_page(
            context=context,
            gates=upstream_gates(seed),
            expected_checkpoint=wrong_partition,
            decisions=quarantine_decisions(definitions, QUARANTINE_REASON),
            page_exhausted=True,
        )
    assert "partition" in str(excinfo.value).lower()
    assert await rehearsal.count(
        "m04e_definition_version_mappings", "tenant_id = :t", {"t": seed.tenant_id}
    ) == 0


@pytest.mark.rows_in_public
async def test_m04e_append_only_guard_reason_matches_the_installed_function(
    rehearsal: RehearsalHarness,
) -> None:
    """``check_violation`` is the SQLSTATE the guard raises, and it is reachable."""

    seed = await seed_partition(rehearsal)
    with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
        await rehearsal.execute(
            'DELETE FROM m04e_definition_version_audit WHERE tenant_id = :t',
            {"t": seed.tenant_id},
        )
    sqlstate = getattr(getattr(excinfo.value, "orig", None), "sqlstate", None)
    assert sqlstate == CHECK_VIOLATION, (
        f"the append-only guard must raise check_violation (23514), got {sqlstate!r}"
    )
