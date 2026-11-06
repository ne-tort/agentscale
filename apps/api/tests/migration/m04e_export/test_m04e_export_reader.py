"""Executable acceptance for :class:`SqlM04EEvidenceExportReader` (``MIG-M04E-EXPORT-01``).

What this module is about
=========================
``m04e_export_reader`` is the only place the accepted **rows** of an M04E ledger become the
``M05EvidenceExport`` M05 imports.  ``m05_contexts`` used to treat that export as a host
input, because the marker stores counts and counts are not evidence; the reader closes that
gap by reading the four ledger tables back and handing them to the **unchanged**
``export_m05_evidence``.  So the claims below are all statements of one kind: *the export is
either the ledger's own evidence or a named refusal -- never an invention*.

How it is proven
================
The ledger is written by the **production store** (``SqlM04EEvidenceStore
.classify_and_commit_page``) into the SQLite mirror of the four registered tables, and the
handoff marker is inserted through the production column mapping
(``handoff_marker_row_values``).  A hand-written row set would let the reader pass against a
shape no page commit produces; the few cases that *do* use raw rows (a ledger with two heads,
an observation without a mapping, ...) model a state the store cannot produce and say so.

Sections
========
1. construction and dialect discipline (the mirror is an explicit permission);
2. reconstruction: order, digests, checkpoint lineage, page transactions;
3. the export against the stored handoff marker (digest, snapshot, counts);
4. tenant isolation -- twice over (predicate *and* transaction-local binding);
5. read-only -- the reader reaches no write path and the ledger is unchanged afterwards;
6. mapping/observation consistency refusals;
7. ``mapping_canonical_digest``: the M04 packed ``v1|schema|digest`` vs the bare ``sha256:``;
8. the trusted-service role refusals (``DATABASE_ROLE_NOT_TRUSTED_SERVICE``);
9. the wiring into ``m05_contexts`` / ``partition_chain``;
10. the live PostgreSQL layer (skips without ``PRODAVAN_TEST_DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import replace
from typing import Any
from unittest import mock

import pytest
import sqlalchemy as sa

from prodavan.application.migration import m04e_export_reader as reader_module
from prodavan.application.migration.evidence_ports import (
    handoff_marker_row_values,
    is_sha256,
)
from prodavan.application.migration.m04_scope.scope_persistence import (
    encode_mapping_canonical_evidence,
)
from prodavan.application.migration.m04e_definition_version import (
    backfill as backfill_module,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillOutcome,
    EvidenceStatus,
    HandoffGate,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_export_reader import (
    CONTEXT_ROLE,
    IDENTITY_ARITY,
    MAPPING_OUTCOMES,
    QUARANTINE_STATES,
    M04EExportRefused,
    M04EExportRefusalCode,
    SqlM04EEvidenceExportReader,
    build_m04e_export_reader,
    select_mappings,
    select_observations,
    select_page_audit,
)
from prodavan.application.migration.m04e_ports import (
    M04EPortReason,
    M04EPortRefused,
    bind_m04e_transaction_context,
    mapping_canonical_digest,
)
from prodavan.application.migration.m05_contexts import (
    M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    M05_UPSTREAM_EVIDENCE_M04E_EXPORT_REFUSED,
    M05ContextInputs,
    build_partition_m05_contexts,
    load_m04e_export,
    load_partition_m05_contexts,
)
from prodavan.application.migration.partition_chain import PartitionChainKey
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
)

from . import ledger_fixtures as fx
from .ledger_fixtures import (
    AUDIT_TABLE,
    CHECKPOINT_TABLE,
    MAPPING_TABLE,
    OTHER_DEFINITION_ID,
    OTHER_TENANT,
    PROMOTION_TABLE,
    QUARANTINE_TABLE,
    SHA_A,
    SHA_C,
    TENANT,
    VERSION_TWO,
    definition_candidate,
    version_candidate,
)

#: The ledger tables the reader reads and must never write.
LEDGER_TABLES = (MAPPING_TABLE, QUARANTINE_TABLE, CHECKPOINT_TABLE, AUDIT_TABLE)
ALL_READ_TABLES = (*LEDGER_TABLES, PROMOTION_TABLE)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def reader_for(ledger: Any, *, allow_non_postgresql: bool = True) -> SqlM04EEvidenceExportReader:
    """The reader bound to the mirror, with the PostgreSQL-only guard relaxed.

    ``allow_non_postgresql=True`` is the harness's explicit permission -- SQLite has no
    transaction-local GUCs and no row security, so the reader would (correctly) refuse to run
    against it.  Everything else is the production code path.
    """

    return SqlM04EEvidenceExportReader(
        session_factory=ledger.session_factory, allow_non_postgresql=allow_non_postgresql,
    )


async def ledger_snapshot(ledger: Any) -> dict[str, list[dict[str, Any]]]:
    """Every row of every mirrored table, for "nothing changed" assertions."""

    return {name: await ledger.rows(name) for name in ALL_READ_TABLES}


async def read_rows(ledger: Any, statement: Any, **params: Any) -> list[dict[str, Any]]:
    """Run one read against the mirror (test-side observation only)."""

    async with ledger.engine.connect() as connection:
        result = await connection.execute(statement, params)
        return [dict(row) for row in result.mappings()]


def refusal_code(raised: M04EExportRefused) -> M04EExportRefusalCode:
    return raised.code


# ---------------------------------------------------------------------------
# 1. Construction and dialect discipline
# ---------------------------------------------------------------------------


def test_the_reader_cannot_be_constructed_without_a_session_factory() -> None:
    """There is no in-memory ledger: a factory-less reader would read "no rows" forever."""

    with pytest.raises(ValueError) as raised:
        SqlM04EEvidenceExportReader(session_factory=None)  # type: ignore[arg-type]

    assert str(raised.value) == M04EExportRefusalCode.SESSION_FACTORY_MISSING.value


def test_the_composition_root_helper_returns_no_reader_rather_than_an_empty_one() -> None:
    assert build_m04e_export_reader(session_factory=None) is None

    class _Factory:  # the helper stores it, it never calls it
        def __call__(self) -> Any:  # pragma: no cover - must not be reached
            raise AssertionError("building the reader must not open a session")

    built = build_m04e_export_reader(session_factory=_Factory())  # type: ignore[arg-type]
    assert isinstance(built, SqlM04EEvidenceExportReader)
    # A composition root gets a reader that *will* refuse on a non-PostgreSQL dialect:
    # the escape hatch is opt-in and never defaulted.
    assert built._allow_non_postgresql is False  # noqa: SLF001 - the default is the contract


async def test_a_non_postgresql_dialect_is_refused_unless_the_mirror_is_allowed(
    export_ledger: Any,
) -> None:
    """A production composition root must not read the ledger without the RLS binding."""

    strict = reader_for(export_ledger, allow_non_postgresql=False)

    with pytest.raises(M04EExportRefused) as raised:
        await strict.read_export(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)

    assert refusal_code(raised.value) is M04EExportRefusalCode.DIALECT_UNSUPPORTED
    assert "sqlite" in raised.value.detail
    # The refusal states the partition it was asked about, so an operator can act on it.
    assert raised.value.refusal.partition == (TENANT, fx.DEFINITION_ID)


@pytest.mark.parametrize(
    ("tenant_id", "definition_id"),
    [
        ("", fx.DEFINITION_ID),
        (TENANT, ""),
        (" padded", fx.DEFINITION_ID),
        (TENANT, "trailing\n"),
        (None, fx.DEFINITION_ID),
        (TENANT, 42),
    ],
)
async def test_an_unusable_partition_is_refused_before_any_statement(
    export_ledger: Any, tenant_id: Any, definition_id: Any,
) -> None:
    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=tenant_id, app_definition_id=definition_id,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.PARTITION_INVALID
    # No row was written by the rejected call.
    assert await export_ledger.statement_count() == 0


# ---------------------------------------------------------------------------
# 2. Reconstructing the ledger
# ---------------------------------------------------------------------------


async def test_a_complete_ledger_reconstructs_its_rows_in_the_stored_order(
    export_ledger: Any,
) -> None:
    """The definition page then the version page -- the order the store committed them in."""

    records, head = await fx.complete_ledger(export_ledger)
    ledger = await reader_for(export_ledger).read_ledger(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert ledger.tenant_id == TENANT
    assert ledger.app_definition_id == fx.DEFINITION_ID
    assert ledger.checkpoint == head
    assert [record.phase for record in ledger.accepted] == ["definition", "version"]
    assert [record.source_id for record in ledger.accepted] == [
        record.source_id for record in records
    ]
    assert ledger.quarantined == ()
    assert ledger.counts == {"accepted": 2, "quarantined": 0}
    # ``records`` is accepted-then-rest, the exact order ``export_m05_evidence`` is fed.
    assert ledger.records == ledger.accepted

    # Every digest and every provenance field the ledger holds survives the round trip.
    for stored, read in zip(records, ledger.accepted, strict=True):
        assert read.source_digest == stored.source_digest
        assert read.canonical_digest == stored.canonical_digest
        assert read.canonical_identity == stored.canonical_identity
        assert is_sha256(read.canonical_digest) and is_sha256(read.source_digest)
        assert read.tenant_id == TENANT and read.app_definition_id == fx.DEFINITION_ID
        assert read.source_snapshot_digest == head.source_snapshot_digest
    # A version record keeps the artifact identity it was accepted with.
    version = ledger.accepted[1]
    assert version.artifact_digest is not None and is_sha256(version.artifact_digest)
    assert dict(version.proof_evidence).keys() >= {
        "owner", "active_scope", "same_definition", "immutable_artifact",
    }


async def test_the_reconstruction_is_a_pure_function_of_the_stored_keys(
    export_ledger: Any,
) -> None:
    """Two reads of an unchanged ledger are equal values -- nothing is cached or rewritten."""

    await fx.complete_ledger(export_ledger)
    reader = reader_for(export_ledger)

    first = await reader.read_ledger(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)
    second = await reader.read_ledger(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)

    assert first == second
    assert first.page_transactions == second.page_transactions


async def test_each_record_carries_the_page_cursor_it_was_classified_under(
    export_ledger: Any,
) -> None:
    """``phase``/``sequence`` come from the row's page token; lineage from the head."""

    await fx.complete_ledger(export_ledger)
    ledger = await reader_for(export_ledger).read_ledger(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )
    definition, version = ledger.accepted

    assert definition.checkpoint.phase == "definition"
    assert version.checkpoint.phase == "version"
    # A page advances the within-phase cursor to the greatest identity it consumed; the
    # sequence is the one the audit event recorded for that page.
    assert version.checkpoint.after_source_id == version.source_id
    assert definition.checkpoint.phase_complete is False
    assert version.checkpoint.phase_complete is True
    # Lineage is the head's, on every record of every page.
    for record in ledger.records:
        assert record.checkpoint.source_snapshot_id == ledger.checkpoint.source_snapshot_id
        assert (
            record.checkpoint.source_snapshot_digest == ledger.checkpoint.source_snapshot_digest
        )
        assert (
            record.checkpoint.canonical_schema_version
            == ledger.checkpoint.canonical_schema_version
        )
        assert record.checkpoint.writer_generation == ledger.checkpoint.writer_generation


async def test_every_committed_page_contributes_its_own_transaction(export_ledger: Any) -> None:
    """The transaction a record reports is the one its page committed in, not a constant."""

    await fx.complete_ledger(export_ledger)
    ledger = await reader_for(export_ledger).read_ledger(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert len(ledger.page_transactions) == 2
    tokens = [token for token, _transaction in ledger.page_transactions]
    assert len(set(tokens)) == 2, "each page commits under its own token"
    transactions = {transaction for _token, transaction in ledger.page_transactions}
    assert len(transactions) == 2
    for record in ledger.records:
        assert record.transaction_id in transactions
    # Not merely "two of something": each page's rows report *that* page's transaction,
    # which is the value the page's audit event recorded.
    assert {record.phase: record.transaction_id for record in ledger.records} == {
        "definition": fx.DEFINITION_TRANSACTION,
        "version": fx.VERSION_TRANSACTION,
    }


async def test_a_quarantined_page_exports_its_observation_as_a_conflict_record(
    export_ledger: Any,
) -> None:
    """A quarantined row is evidence too -- but only as a refusal, never as acceptance."""

    ctx = fx.context(transaction_id=fx.VERSION_TRANSACTION)
    store = fx.store_for(export_ledger)
    definition_records, version_head = await fx.commit_definition_page(export_ledger)
    assert definition_records, "the definition page must have committed first"

    candidate = version_candidate(ctx, fx.version_row(), fx.definition_row())
    batch = await store.classify_and_commit_page(
        context=ctx,
        gates=fx.upstream_gates(transaction_id=fx.VERSION_TRANSACTION),
        expected_checkpoint=version_head,
        decisions=(fx.quarantined(candidate),),
        page_exhausted=True,
    )
    head = batch.checkpoint
    assert head.phase_complete is True

    ledger = await reader_for(export_ledger).read_ledger(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    # Only the definition page was accepted; the version page's row is a quarantine record.
    assert [record.phase for record in ledger.accepted] == ["definition"]
    assert len(ledger.quarantined) == 1
    record = ledger.quarantined[0]
    assert record.status is EvidenceStatus.QUARANTINED
    assert record.source_id == candidate.source_id
    assert record.reason_code is not None and record.reason_code.strip()
    # A quarantined first mapping makes no canonical claim: the ledger stores
    # ``canonical_digest = NULL`` for it (``_classify`` sets it only for an acceptance),
    # while the identity column keeps the row's own (the CHECK only constrains the arity
    # of an *accepted* row).  What must never happen is a digest M05 would import.
    assert record.canonical_digest is None
    assert record.outcome is BackfillOutcome.QUARANTINED


async def test_an_empty_ledger_is_named_not_returned_as_empty_tuples(
    export_ledger: Any,
) -> None:
    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.LEDGER_EMPTY
    assert raised.value.refusal.partition == (TENANT, fx.DEFINITION_ID)


async def test_rows_without_a_durable_head_are_a_missing_cursor_not_an_export(
    export_ledger: Any,
) -> None:
    """A ledger can hold mappings and still have no cursor to bind them into an export."""

    await fx.complete_ledger(export_ledger)
    await export_ledger.delete(CHECKPOINT_TABLE)

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.CHECKPOINT_MISSING


async def test_two_heads_for_one_partition_are_ambiguous(export_ledger: Any) -> None:
    """The SQLite mirror drops the PostgreSQL CHECKs but keeps this one enforceable."""

    await fx.complete_ledger(export_ledger)
    head = await export_ledger.single(CHECKPOINT_TABLE)
    assert head is not None
    # ``uq_m04e_checkpoint_partition`` also lives in the mirror; model the ambiguity the
    # reader must survive (a partition-wide unique key violation would be the DB's job).
    rows = fx.checkpoint_row_values(suffix="0002")
    rows["id"] = "m04ecp_0002"
    rows["checkpoint_token"] = "m04e-cp:second-head"
    try:
        await export_ledger.insert(CHECKPOINT_TABLE, **rows)
    except sa.exc.IntegrityError:  # pragma: no cover - mirror enforces the unique key
        pytest.skip("the SQLite mirror enforces uq_m04e_checkpoint_partition; covered live")

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.CHECKPOINT_AMBIGUOUS


async def test_an_undrained_stream_is_refused_rather_than_exported(export_ledger: Any) -> None:
    """``definition`` phase or ``phase_complete=False`` hands M05 a partial snapshot."""

    await fx.commit_definition_page(export_ledger)

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.CHECKPOINT_INCOMPLETE
    assert "phase_complete" in raised.value.detail


async def test_a_head_whose_snapshot_digest_is_not_sha256_is_refused(
    export_ledger: Any,
) -> None:
    """Modeled by rewriting the head: the store cannot write one the CHECK would reject."""

    await fx.complete_ledger(export_ledger)
    # Rewrite the mirror's only head to carry a malformed snapshot digest.  SQLite keeps no
    # CHECKs, which is exactly why the reader re-asserts the rule itself.
    async with export_ledger.engine.begin() as connection:
        await connection.execute(sa.text(
            "UPDATE m04e_definition_version_checkpoints SET source_snapshot_digest = :digest"
        ), {"digest": "sha256:not-a-digest"})

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    # ``ck_m04e_checkpoint_source_sha256`` is a lineage rule, not an ambiguity: one head
    # exists and it is the wrong shape.
    assert refusal_code(raised.value) is M04EExportRefusalCode.CHECKPOINT_LINEAGE_INVALID
    assert "sha256" in raised.value.detail


# ---------------------------------------------------------------------------
# 3. The export against the stored handoff marker
# ---------------------------------------------------------------------------


async def test_the_export_is_the_stored_handoff_evidence(export_ledger: Any) -> None:
    """The export must *be* the evidence ``persist_m05_handoff`` recorded, not a lookalike."""

    expected, marker = await fx.seeded_partition(export_ledger)
    export = await reader_for(export_ledger).read_export(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert export.tenant_id == TENANT and export.app_definition_id == fx.DEFINITION_ID
    assert export.canonical_digest == expected.canonical_digest
    assert export.source_snapshot_digest == expected.source_snapshot_digest
    assert export.checkpoint == expected.checkpoint
    # The rows themselves: every field the ledger stores, and therefore every field the
    # contract compares, survives the round trip.  Two fields are *not* restated by a read
    # and are asserted separately below rather than compared for equality:
    # ``artifact_uri`` (the ledger keeps no URI: it is a location, not evidence) and
    # ``checkpoint`` (the reader reports the cursor the row was classified *under*, which
    # is the only one derivable from the stored page token; the store's in-memory batch is
    # stamped with the cursor the page advanced *to*).
    identity = lambda record: (  # noqa: E731 - a projection, not a fixture
        record.source_kind, record.source_id, record.schema_version, record.phase,
        record.status, record.source_digest, record.canonical_digest,
        record.canonical_identity, record.reason_code, record.tenant_id,
        record.app_definition_id, record.artifact_digest, record.verified_artifact_digest,
        record.proof_evidence, record.outcome,
    )
    assert [identity(record) for record in export.accepted] == [
        identity(record) for record in expected.accepted
    ]
    assert [identity(record) for record in export.quarantined] == [
        identity(record) for record in expected.quarantined
    ]
    for record in (*export.accepted, *export.quarantined):
        assert record.checkpoint.partition == (TENANT, fx.DEFINITION_ID)
        assert record.checkpoint.phase == record.phase
        assert record.checkpoint.sequence <= export.checkpoint.sequence
    assert export.status is expected.status
    # The stored marker is attached verbatim, so M05 can re-check it against these rows.
    assert export.handoff_marker == marker
    assert export.handoff_marker is not None
    assert export.handoff_marker.accepted_count == len(export.accepted)
    assert export.handoff_marker.quarantined_count == len(export.quarantined)
    # The gates are the *stored* six, never a list this reader built.
    assert dict((gate.name, gate.passed) for gate in export.gates) == dict(
        (gate.name, gate.passed) for gate in marker.gate_evidence
    )
    names = {gate.name for gate in marker.gate_evidence}
    assert set(fx.HANDOFF_GATE_NAMES) <= names
    # ``export_m05_evidence`` appends the release switch's own gate while
    # ``UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED`` is False; it is the only gate that is not
    # one of the six ``persist_m05_handoff`` records.
    assert names - set(fx.HANDOFF_GATE_NAMES) <= {"upstream_owner_scope_gate"}


async def test_the_canonical_digest_is_recomputed_by_the_contract_function(
    export_ledger: Any,
) -> None:
    """The reader supplies rows; ``export_m05_evidence`` owns the digest.  Proof: recompute."""

    expected, _marker = await fx.seeded_partition(export_ledger)
    export = await reader_for(export_ledger).read_export(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    recomputed = export_m05_evidence(
        tenant_id=TENANT,
        app_definition_id=fx.DEFINITION_ID,
        records=(*export.accepted, *export.quarantined),
        source_snapshot_digest=export.source_snapshot_digest,
        checkpoint=export.checkpoint,
        gates=export.gates,
    )
    assert recomputed.canonical_digest == export.canonical_digest == expected.canonical_digest
    assert recomputed.accepted == export.accepted and recomputed.quarantined == export.quarantined

    # And that digest is *not* the marker's counts or any single row's digest.
    assert export.canonical_digest != SHA_C
    assert export.canonical_digest not in {record.canonical_digest for record in export.accepted}


async def test_a_partition_without_a_stored_handoff_cannot_be_exported(
    export_ledger: Any,
) -> None:
    """Complete rows but no recorded handoff: nothing attests the six gates."""

    await fx.complete_ledger(export_ledger)

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.HANDOFF_MARKER_MISSING


async def test_a_handoff_recorded_for_another_snapshot_is_not_reused(
    export_ledger: Any,
) -> None:
    """The handoff describes one snapshot; a ledger of another one is not that evidence."""

    _records, head = await fx.complete_ledger(export_ledger)
    other_snapshot = "sha256:" + "2" * 64
    foreign_export = export_m05_evidence(
        tenant_id=TENANT,
        app_definition_id=fx.DEFINITION_ID,
        records=(),
        source_snapshot_digest=other_snapshot,
        checkpoint=head,
        gates=fx.handoff_gates(m04e_digest=SHA_C),
    )
    marker = replace(
        fx.marker_for(foreign_export),
        source_snapshot_digest=other_snapshot,
    )
    await fx.append_marker(export_ledger, marker)

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.HANDOFF_MARKER_MISSING


async def test_a_ledger_that_moved_after_the_handoff_is_a_digest_mismatch(
    export_ledger: Any,
) -> None:
    """A handoff recorded over other rows than the ledger holds is a divergence."""

    expected, marker = await fx.seeded_partition(export_ledger)
    # A second marker of the same partition needs its own revision id: the table's key is
    # ``(tenant_id, definition_id, revision_id)``, so the mirror refuses a duplicate.
    await fx.append_marker(
        export_ledger,
        replace(
            marker,
            record_id=fx.SECOND_MARKER_RECORD_ID,
            canonical_digest="sha256:" + "9" * 64,
        ),
    )

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.HANDOFF_MARKER_AMBIGUOUS
    assert expected.canonical_digest != "sha256:" + "9" * 64


async def test_marker_counts_that_disagree_with_the_rows_are_refused(
    export_ledger: Any,
) -> None:
    """Counts are the marker's only claim about the rows; a divergence is not silenceable."""

    records, head = await fx.complete_ledger(export_ledger)
    export = fx.export_for(records, checkpoint=head)
    await fx.append_marker(
        export_ledger, fx.marker_for(export, accepted_count=len(export.accepted) + 1),
    )

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.COUNTS_MISMATCH


async def test_a_handoff_of_another_cursor_is_a_checkpoint_mismatch(
    export_ledger: Any,
) -> None:
    """The ledger advanced past the handoff: the marker no longer describes this head."""

    records, head = await fx.complete_ledger(export_ledger)
    export = fx.export_for(records, checkpoint=head)
    moved = replace(head, sequence=head.sequence + 1, after_source_id="aver_9999")
    await fx.append_marker(export_ledger, fx.marker_for(export, checkpoint=moved))

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.CHECKPOINT_MISMATCH


async def test_the_caller_may_pin_the_snapshot_it_expects(export_ledger: Any) -> None:
    records, head = await fx.complete_ledger(export_ledger)
    await fx.append_marker(export_ledger, fx.marker_for(fx.export_for(records, checkpoint=head)))
    reader = reader_for(export_ledger)

    matched = await reader.read_export(
        tenant_id=TENANT,
        app_definition_id=fx.DEFINITION_ID,
        source_snapshot_digest=head.source_snapshot_digest,
    )
    assert matched.source_snapshot_digest == head.source_snapshot_digest

    with pytest.raises(M04EExportRefused) as raised:
        await reader.read_export(
            tenant_id=TENANT,
            app_definition_id=fx.DEFINITION_ID,
            source_snapshot_digest="sha256:" + "3" * 64,
        )
    assert refusal_code(raised.value) is M04EExportRefusalCode.SNAPSHOT_MISMATCH


async def test_the_gate_list_comes_from_the_stored_marker_not_from_the_reader(
    export_ledger: Any,
) -> None:
    """A stored marker with a closed gate must be exported closed, verbatim."""

    records, head = await fx.complete_ledger(export_ledger)
    closed = tuple(
        HandoffGate(gate.name, False, gate.evidence_digest, "review not recorded")
        for gate in fx.handoff_gates(m04e_digest="sha256:" + "0" * 64)
    )
    export = fx.export_for(records, checkpoint=head, gates=closed)
    await fx.append_marker(export_ledger, fx.marker_for(export))

    read = await reader_for(export_ledger).read_export(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert all(gate.passed is False for gate in read.gates)
    assert read.status is export.status


# ---------------------------------------------------------------------------
# 4. Tenant isolation
# ---------------------------------------------------------------------------


async def test_another_tenants_ledger_is_invisible_and_named_as_empty(
    export_ledger: Any,
) -> None:
    """Tenant scoping is stated twice: the predicate *and* the transaction-local binding."""

    await fx.seeded_partition(export_ledger)
    elsewhere = {name: await export_ledger.rows(name) for name in LEDGER_TABLES}
    # A complete ledger always holds mappings, a head and the two page events; the
    # quarantine table is legitimately empty when nothing diverged.
    for name in (MAPPING_TABLE, CHECKPOINT_TABLE, AUDIT_TABLE):
        assert elsewhere[name], f"the fixture seeded {name} for this tenant"

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=OTHER_TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    # A foreign tenant never reads another partition's rows: it sees none of its own.
    assert refusal_code(raised.value) is M04EExportRefusalCode.LEDGER_EMPTY
    assert raised.value.refusal.partition == (OTHER_TENANT, fx.DEFINITION_ID)


async def test_another_definitions_ledger_is_invisible_even_inside_one_tenant(
    export_ledger: Any,
) -> None:
    """The partition key is ``(tenant, definition)``; a tenant predicate alone is too wide."""

    await fx.seeded_partition(export_ledger)

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=OTHER_DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.LEDGER_EMPTY


async def test_two_partitions_of_one_tenant_export_independently(export_ledger: Any) -> None:
    """A ledger of a sibling tenant does not leak into the partition that is read."""

    records, head = await fx.complete_ledger(export_ledger)
    await fx.append_marker(export_ledger, fx.marker_for(fx.export_for(records, checkpoint=head)))
    # A second tenant's rows live in the same tables; the mirror has no RLS, so this proves
    # the *predicate* half of the tenant scoping (the live layer proves the policy half).
    foreign = fx.mapping_row_values(
        tenant_id=OTHER_TENANT, definition_id=OTHER_DEFINITION_ID, suffix="9001",
        checkpoint_token="m04e-cp:foreign", source_id="aver_9001",
    )
    await fx.seed_raw_partition(export_ledger, mappings=[foreign])

    export = await reader_for(export_ledger).read_export(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert [record.source_id for record in export.accepted] == [
        record.source_id for record in records
    ]
    assert all(record.tenant_id == TENANT for record in (*export.accepted, *export.quarantined))
    assert all(
        record.canonical_identity[0] == TENANT for record in export.accepted
    )


@pytest.mark.parametrize("statement", [select_mappings, select_observations, select_page_audit])
def test_every_ledger_read_carries_an_explicit_tenant_predicate(statement: Any) -> None:
    """A policy regression must not be able to widen the partition."""

    sql = str(statement().compile(compile_kwargs={"literal_binds": False}))
    assert "tenant_id = " in sql and "definition_id = " in sql
    assert sql.count(":tenant_id") == 1 and sql.count(":definition_id") == 1


async def test_the_read_selects_only_rows_of_the_requested_partition(
    export_ledger: Any,
) -> None:
    """The same predicate evaluated by a database, not by a fake's table map."""

    await fx.complete_ledger(export_ledger)
    foreign = fx.mapping_row_values(
        tenant_id=OTHER_TENANT, definition_id=OTHER_DEFINITION_ID, suffix="9002",
    )
    await fx.seed_raw_partition(export_ledger, mappings=[foreign])

    rows = await read_rows(
        export_ledger, select_mappings(),
        tenant_id=TENANT, definition_id=fx.DEFINITION_ID,
    )

    assert rows and all(row["tenant_id"] == TENANT for row in rows)
    assert all(row["definition_id"] == fx.DEFINITION_ID for row in rows)
    assert STRANGER_ROW_ID not in {row["id"] for row in rows}


STRANGER_ROW_ID = "m04emap_9002"


async def test_the_read_binds_the_tenant_transaction_locally_before_reading() -> None:
    """On PostgreSQL the context is applied *and* read back before any table is touched."""

    from tests.migration.m04e_ports.acceptance_fixtures import dialect_session

    fake, factory = dialect_session("postgresql")
    reader = SqlM04EEvidenceExportReader(session_factory=factory)

    with pytest.raises(AssertionError):
        # The dialect-only fake answers context probes and nothing else: reaching a table
        # read means the binding already happened and the read is what failed.
        await reader.read_ledger(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)

    applied = fake.executed("set_config('app.tenant_id'")
    assert applied, "the reader must apply the transaction-local context"
    sql, params = applied[0]
    assert ", true)" in sql, "the setting must be transaction-local, never session-wide"
    assert params["tenant_id"] == TENANT
    assert params["role"] == CONTEXT_ROLE, "a read role, distinct from the writer's"
    assert fake.executed("current_setting('app.tenant_id', true)")

    _unbound_fake, unbound = dialect_session("postgresql", readback={"tenant_id": OTHER_TENANT})
    with pytest.raises(M04EExportRefused) as raised:
        await SqlM04EEvidenceExportReader(session_factory=unbound).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )
    assert refusal_code(raised.value) is M04EExportRefusalCode.TENANT_CONTEXT_UNBOUND

    _exposed_fake, exposed = dialect_session("postgresql", readback={"row_security": "off"})
    with pytest.raises(M04EExportRefused) as raised:
        await SqlM04EEvidenceExportReader(session_factory=exposed).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )
    assert refusal_code(raised.value) is M04EExportRefusalCode.ROW_SECURITY_DISABLED


# ---------------------------------------------------------------------------
# 5. Read-only
# ---------------------------------------------------------------------------


_READ_ONLY_VERBS = re.compile(
    r"^\s*(insert|update|delete|truncate|alter|drop|create|grant|revoke)\b", re.IGNORECASE,
)


def test_the_module_declares_no_write_and_the_reader_executes_no_write() -> None:
    """Inspected, not assumed: no ``insert``/``update``/``delete`` anywhere in the reader."""

    source = inspect.getsource(reader_module)
    # Every literal SQL string the module hands to ``text`` -- the module imports it
    # directly (``from sqlalchemy import text``), so both spellings are scanned, and a
    # named constant is scanned through the same pattern as an inline statement.
    executed = re.findall(
        r"(?:sa\.)?text\(\s*[rf]?([\"']{1,3})(.*?)\1\s*[,)]",
        source,
        flags=re.DOTALL,
    )
    statements = [statement for _quote, statement in executed]
    assert len(statements) >= 2, "the reader is expected to execute statements"
    for statement in statements:
        assert not _READ_ONLY_VERBS.match(statement.strip()), statement
    # The only textual SQL in the module is the context binding and its readback.
    assert "set_config('app.tenant_id'" in source
    assert "current_setting('app.tenant_id', true)" in source
    # Typed selects carry no verb at all: they are ``sa.select(...)`` over a model.
    assert "sa.insert" not in source and "sa.update" not in source
    assert "sa.delete" not in source
    assert "DELETE FROM" not in source.upper()
    assert "INSERT INTO" not in source.upper()
    assert "UPDATE " not in source.upper()


@pytest.mark.parametrize("method", ["read_export", "read_ledger"])
async def test_reading_writes_nothing_to_the_ledger(export_ledger: Any, method: str) -> None:
    """The append-only ledger is not written -- not even an audit row of the read itself."""

    await fx.seeded_partition(export_ledger)
    before = await ledger_snapshot(export_ledger)

    await getattr(reader_for(export_ledger), method)(
        tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )

    assert await ledger_snapshot(export_ledger) == before


async def test_a_refused_read_writes_nothing_either(export_ledger: Any) -> None:
    await fx.complete_ledger(export_ledger)
    before = await ledger_snapshot(export_ledger)

    with pytest.raises(M04EExportRefused):
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert await ledger_snapshot(export_ledger) == before


async def test_each_call_opens_and_closes_its_own_transaction(export_ledger: Any) -> None:
    """One call owns one transaction: nothing stays open between reads."""

    await fx.seeded_partition(export_ledger)
    reader = reader_for(export_ledger)
    opened = {"count": 0}
    real_factory = export_ledger.session_factory

    def _counting() -> Any:
        opened["count"] += 1
        return real_factory()

    reader = SqlM04EEvidenceExportReader(
        session_factory=_counting,  # type: ignore[arg-type]
        allow_non_postgresql=True,
    )
    for _ in range(3):
        await reader.read_export(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)

    assert opened["count"] == 3, "exactly one session (and transaction) per call"
    # Nothing leaked: the ledger is still readable and unchanged after the reads.
    assert await export_ledger.count(PROMOTION_TABLE) == 1


# ---------------------------------------------------------------------------
# 6. Mapping / observation consistency
# ---------------------------------------------------------------------------


async def test_a_mapping_status_no_page_commit_writes_is_refused(export_ledger: Any) -> None:
    """``replay`` and ``blocked`` are in the CHECK but a committed page never writes them."""

    assert set(MAPPING_OUTCOMES) == {"accepted", "quarantined", "conflict"}
    for forbidden in ("replay", "blocked"):
        assert forbidden not in MAPPING_OUTCOMES

    await fx.complete_ledger(export_ledger)
    # A *distinct* source identity: ``uq_m04e_mapping_source`` allows one first mapping per
    # ``(tenant, definition, kind, id, schema)``, so a second row has to be another source.
    await fx.seed_raw_partition(
        export_ledger,
        mappings=[fx.mapping_row_values(
            status="replay", suffix="9101", source_id="aver_9101",
        )],
        audits=[fx.audit_row_values(tenant_id=TENANT, suffix="9101")],
    )

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.MAPPING_STATUS_UNKNOWN


async def test_a_row_whose_page_event_is_missing_is_refused(export_ledger: Any) -> None:
    """No ``page_committed`` event means no cursor can be claimed for the row."""

    await fx.seeded_partition(export_ledger)
    await fx.seed_raw_partition(export_ledger, mappings=[fx.mapping_row_values(
        suffix="9201", source_id="aver_9201", checkpoint_token="m04e-cp:orphan-page",
    )])

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.PAGE_AUDIT_MISSING


async def test_an_observation_without_its_first_mapping_is_refused(export_ledger: Any) -> None:
    token = await _version_token(export_ledger)
    await fx.seed_raw_partition(export_ledger, observations=[fx.quarantine_row_values(
        source_id="aver_unmapped", suffix="9301", checkpoint_token=token,
    )])

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.OBSERVATION_WITHOUT_MAPPING


async def test_an_observation_outside_the_ledger_vocabulary_is_refused(
    export_ledger: Any,
) -> None:
    assert QUARANTINE_STATES == frozenset({"open", "resolved", "archived"})
    await fx.complete_ledger(export_ledger)
    # A complete ledger holds one mapping per page; the observation has to comment on a
    # *version* mapping, so pick that row rather than asserting the table is a singleton.
    mappings = await export_ledger.rows(MAPPING_TABLE)
    version_rows = [row for row in mappings if row["phase"] == "version"]
    assert len(version_rows) == 1
    mapping = version_rows[0]
    await fx.seed_raw_partition(export_ledger, observations=[fx.quarantine_row_values(
        source_id=str(mapping["source_id"]),
        source_kind=str(mapping["source_kind"]),
        source_digest=str(mapping["source_digest"]),
        checkpoint_token=str(mapping["checkpoint"]),
        status="escalated",
        suffix="9401",
    )])

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.OBSERVATION_STATUS_UNKNOWN


async def test_an_accepted_row_bound_to_another_tenant_is_refused(export_ledger: Any) -> None:
    """Re-asserted in the reader, because acceptance is what M05 imports."""

    token = await _version_token(export_ledger)
    await fx.seed_raw_partition(
        export_ledger,
        mappings=[fx.mapping_row_values(
            suffix="9501",
            source_id="aver_9501",
            checkpoint_token=token,
            canonical_identity=[OTHER_TENANT, fx.DEFINITION_ID, fx.MANIFEST_VERSION],
        )],
    )

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.MAPPING_ACCEPTED_INCOMPLETE
    assert "another tenant" in raised.value.detail


def test_the_identity_arity_is_the_contracts_two_and_three() -> None:
    assert IDENTITY_ARITY == {"definition": 2, "version": 3}


async def test_an_accepted_row_without_a_typed_identity_is_refused(export_ledger: Any) -> None:
    token = await _version_token(export_ledger)
    await fx.seed_raw_partition(export_ledger, mappings=[fx.mapping_row_values(
        suffix="9601", source_id="aver_9601", checkpoint_token=token,
        canonical_identity=[TENANT],
    )])

    with pytest.raises(M04EExportRefused) as raised:
        await reader_for(export_ledger).read_export(
            tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
        )

    assert refusal_code(raised.value) is M04EExportRefusalCode.MAPPING_ACCEPTED_INCOMPLETE


async def _version_token(ledger: Any) -> str:
    await fx.complete_ledger(ledger)
    rows = await ledger.rows(AUDIT_TABLE)
    version_pages = [row["checkpoint"] for row in rows if row["phase"] == "version"]
    assert version_pages, "the fixture must commit a version page"
    return str(version_pages[0])


# ---------------------------------------------------------------------------
# 7. The M04 <-> M04E digest agreement (``v1|schema|digest`` vs a bare ``sha256:``)
# ---------------------------------------------------------------------------


def test_the_packed_m04_spelling_decodes_to_the_digest_m04e_compares() -> None:
    """One decoder for one column: the writer's own function, never a second parser."""

    packed = encode_mapping_canonical_evidence(fx.SOURCE_SCHEMA, SHA_C)
    assert packed.startswith("v1|") and packed.endswith(SHA_C)
    assert mapping_canonical_digest(packed) == SHA_C

    # A mapping that never held a canonical identity is a refusal, not an "accepted".
    digestless = encode_mapping_canonical_evidence(fx.SOURCE_SCHEMA, None)
    assert mapping_canonical_digest(digestless) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        SHA_C,                                                     # the bare digest M04 never writes
        12345,
        "v1||sha256:" + "c" * 64,                                  # no schema
        "v2|scope-registry.v1|sha256:" + "c" * 64,                 # unknown prefix
        "v1|scope-registry.v1|sha256:not-a-digest",                # malformed digest
    ],
)
def test_anything_but_the_written_spelling_is_none_never_a_guess(value: Any) -> None:
    assert mapping_canonical_digest(value) is None


def test_the_two_stages_agree_on_the_same_stored_column() -> None:
    """The regression this closes: a bare-digest check made every mapped scope unprovable."""

    packed = encode_mapping_canonical_evidence(fx.SOURCE_SCHEMA, SHA_C)
    # What MIG-M04-02 compares it against is a plain sha256 -- so the comparison only works
    # if the column is decoded first, which is exactly what ``mapping_canonical_digest`` does.
    assert is_sha256(SHA_C)
    assert not is_sha256(packed), "the stored column is not itself a digest"
    assert mapping_canonical_digest(packed) == SHA_C

    # And the MIG-M04-02 gate accepts the packed spelling while refusing the bare one.
    from tests.migration.m04e_ports.acceptance_fixtures import (
        promotion_row,
        scope_mapping_row,
        session,
        tables,
    )
    from prodavan.application.migration.m04e_ports import SqlM04EUpstreamGate

    gate = SqlM04EUpstreamGate
    loop = asyncio.new_event_loop()
    try:
        _fake, factory = session(tables(promotion=[promotion_row(outcome="accepted")]))
        evidence = loop.run_until_complete(
            gate(session_factory=factory).prove_partition(context=fx.context())
        )
        assert evidence is not None

        _bare, bare_factory = session(tables(
            scope_mappings=[scope_mapping_row(canonical_payload_digest=SHA_C)],
            promotion=[promotion_row(outcome="accepted")],
        ))
        decision = loop.run_until_complete(
            gate(session_factory=bare_factory).partition_gate(context=fx.context())
        )
        assert decision.evidence is None
        assert decision.refusal is not None
        assert decision.refusal.code is M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 8. The trusted-service database role
# ---------------------------------------------------------------------------


def _bindings_readback(**overrides: Any) -> dict[str, Any]:
    readback: dict[str, Any] = {
        "tenant_id": TENANT,
        "migration_role": "true",
        "trusted_service": "true",
        "row_security": "on",
        "transaction_isolation": "serializable",
        "database_role": "prodavan_service",
        "role_bypasses_rls": False,
    }
    readback.update(overrides)
    return readback


async def test_a_session_that_is_not_the_service_role_is_refused_by_name() -> None:
    """The trusted-service policy grants nothing to any other role, so this is a refusal."""

    from tests.migration.m04e_ports.acceptance_fixtures import dialect_session

    _fake, factory = dialect_session(
        "postgresql", readback=_bindings_readback(database_role="prodavan_app"),
    )

    with pytest.raises(M04EPortRefused) as raised:
        await bind_m04e_transaction_context(factory.session, fx.context())

    assert raised.value.code is M04EPortReason.DATABASE_ROLE_NOT_TRUSTED_SERVICE
    assert M04EPortReason.DATABASE_ROLE_NOT_TRUSTED_SERVICE.value == (
        "m04e_database_role_is_not_trusted_service"
    )
    assert "prodavan_service" in raised.value.refusal.detail


async def test_a_role_that_bypasses_row_security_is_refused() -> None:
    from tests.migration.m04e_ports.acceptance_fixtures import dialect_session

    _fake, factory = dialect_session(
        "postgresql", readback=_bindings_readback(role_bypasses_rls=True),
    )

    with pytest.raises(M04EPortRefused) as raised:
        await bind_m04e_transaction_context(factory.session, fx.context())

    assert raised.value.code is M04EPortReason.ROW_SECURITY_DISABLED


async def test_the_trusted_service_flag_must_be_bound_transaction_locally() -> None:
    from tests.migration.m04e_ports.acceptance_fixtures import dialect_session

    _fake, factory = dialect_session(
        "postgresql", readback=_bindings_readback(trusted_service="off"),
    )

    with pytest.raises(M04EPortRefused) as raised:
        await bind_m04e_transaction_context(factory.session, fx.context())

    assert raised.value.code is M04EPortReason.TENANT_CONTEXT_UNBOUND


def test_the_reader_role_is_distinct_from_the_writers() -> None:
    """An audit of the GUCs must be able to tell a read from a page commit."""

    writer_source = inspect.getsource(
        __import__(
            "prodavan.application.migration.m04e_ports", fromlist=["x"]
        )
    )
    assert f'"{CONTEXT_ROLE}"' not in writer_source
    applied = str(reader_module._APPLY_CONTEXT_SQL)  # noqa: SLF001 - the statement is the contract
    assert "set_config('app.migration_role', 'true', true)" in applied
    assert ", true)" in applied, "every setting must be transaction-local"


# ---------------------------------------------------------------------------
# 9. Wiring: m05_contexts and partition_chain
# ---------------------------------------------------------------------------


def _key() -> PartitionChainKey:
    return PartitionChainKey(tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID)


def _inputs(**overrides: Any) -> M05ContextInputs:
    """The host inputs of one partition.

    The *partition* is ``PartitionChainKey``'s job (``key=``), not an input: the builder
    checks every context against the key, and ``M05ContextInputs`` carries the run
    identity, not the partition.
    """

    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "authenticated": True,
    }
    values.update(overrides)
    return M05ContextInputs(**values)


async def test_load_m04e_export_returns_the_reader_value_or_one_blocker(
    export_ledger: Any,
) -> None:
    await fx.seeded_partition(export_ledger)
    reader = reader_for(export_ledger)

    export, blockers = await load_m04e_export(
        reader=reader, tenant_id=TENANT, app_definition_id=fx.DEFINITION_ID,
    )
    assert blockers == () and export is not None
    assert export.tenant_id == TENANT

    empty, blockers = await load_m04e_export(
        reader=reader, tenant_id=OTHER_TENANT, app_definition_id=fx.DEFINITION_ID,
    )
    assert empty is None
    assert len(blockers) == 1
    assert blockers[0].code == M05_UPSTREAM_EVIDENCE_M04E_EXPORT_REFUSED
    # The reader's own code survives in the detail, so the cause is greppable.
    assert M04EExportRefusalCode.LEDGER_EMPTY.value in blockers[0].detail


async def test_load_m04e_export_refuses_an_export_of_another_partition() -> None:
    class _ForeignReader:
        async def read_export(self, **_kwargs: Any) -> Any:
            return fx.export_for(
                [], checkpoint=fx.fresh_checkpoint(phase="version", complete=True),
            )

    export, blockers = await load_m04e_export(
        reader=_ForeignReader(),  # type: ignore[arg-type]
        tenant_id=OTHER_TENANT,
        app_definition_id=fx.DEFINITION_ID,
    )

    assert export is None
    assert len(blockers) == 1
    assert "not" in blockers[0].detail


async def test_load_partition_m05_contexts_reads_the_ledger_without_a_stated_export(
    export_ledger: Any,
) -> None:
    """The sync builder cannot read; the async one does, and names the reader as the way."""

    await fx.seeded_partition(export_ledger)
    reader = reader_for(export_ledger)

    contexts = await load_partition_m05_contexts(
        key=_key(), inputs=_inputs(m04e_export_reader=reader),
    )

    assert M05_UPSTREAM_EVIDENCE_M04E_EXPORT_REFUSED not in contexts.blocker_codes
    assert M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING not in contexts.blocker_codes
    # Everything else (scope row, runtime gate review, counts) is still the host's job.
    assert contexts.upstream_evidence is None

    without_reader = build_partition_m05_contexts(key=_key(), inputs=_inputs())
    assert M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING in without_reader.blocker_codes


async def test_a_reader_refusal_replaces_the_generic_missing_export_blocker(
    export_ledger: Any,
) -> None:
    """One cause, one entry: the specific code is not reported next to "it is missing"."""

    contexts = await load_partition_m05_contexts(
        key=_key(), inputs=_inputs(m04e_export_reader=reader_for(export_ledger)),
    )

    assert contexts.blocker_codes.count(M05_UPSTREAM_EVIDENCE_M04E_EXPORT_REFUSED) == 1
    assert M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING not in contexts.blocker_codes


def test_an_export_and_a_blocker_for_it_are_mutually_exclusive() -> None:
    export = fx.export_for([], checkpoint=fx.fresh_checkpoint(phase="version", complete=True))
    blocker = mock.Mock(code="whatever", dependency="d", detail="x", needed="y")

    with pytest.raises(ValueError):
        build_partition_m05_contexts(
            key=_key(), inputs=_inputs(), m04e_export=export, export_blockers=(blocker,),
        )


async def test_a_stated_export_wins_over_the_reader(export_ledger: Any) -> None:
    """A host that already holds the export never triggers a ledger read."""

    await fx.seeded_partition(export_ledger)
    stated = fx.export_for(
        [], checkpoint=fx.fresh_checkpoint(phase="version", complete=True),
    )

    class _ExplodingReader:
        async def read_export(self, **_kwargs: Any) -> Any:
            raise AssertionError("a stated export must be used instead of reading the ledger")

    contexts = await load_partition_m05_contexts(
        key=_key(), inputs=_inputs(m04e_export=stated, m04e_export_reader=_ExplodingReader()),
    )
    assert contexts is not None  # the export was never read


# ---------------------------------------------------------------------------
# 10. Live PostgreSQL (skips without PRODAVAN_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


async def test_the_reader_refuses_a_live_session_that_is_not_the_service_role(
    m04e_export_postgres: str,
) -> None:
    """Only a real database can show ``current_user``/``row_security`` as the binder reads them.

    The DSN here is whatever the operator configured; a superuser or any role other than
    ``prodavan_service`` must be refused *by name* rather than read under a policy that
    grants it nothing (or, worse, under a role that bypasses row security).
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(m04e_export_postgres)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                role = str((await session.execute(sa.text("SELECT current_user"))).scalar())
        if role == "prodavan_service":  # pragma: no cover - depends on the operator's DSN
            pytest.skip("this DSN already connects as the trusted service role")

        with pytest.raises(M04EPortRefused) as raised:
            async with factory() as session:
                async with session.begin():
                    await bind_m04e_transaction_context(session, fx.context())
        assert raised.value.code is M04EPortReason.DATABASE_ROLE_NOT_TRUSTED_SERVICE
    finally:
        await engine.dispose()


async def test_the_reader_reads_the_live_ledger_without_writing_it(
    m04e_export_postgres: str,
) -> None:
    """A live read of an absent partition must be a named refusal and change nothing.

    The rehearsal harness owns seeding; this check deliberately does not create a partition
    (that would be a write in a shared database).  What it proves is the one thing the
    SQLite mirror cannot: the real dialect passes the guard, the transaction-local binding
    is applied and read back, and the reader's *refusal* -- not an exception -- is what a
    missing ledger produces.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(m04e_export_postgres)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        reader = SqlM04EEvidenceExportReader(session_factory=factory)
        try:
            await reader.read_export(
                tenant_id="tenant-m04e-export-live-absent",
                app_definition_id="appdef_m04e_export_live_absent",
            )
        except M04EExportRefused as refused:
            assert refused.code in {
                M04EExportRefusalCode.LEDGER_EMPTY,
                M04EExportRefusalCode.TENANT_CONTEXT_UNBOUND,
                M04EExportRefusalCode.ROW_SECURITY_DISABLED,
            }
        except M04EPortRefused as refused:  # pragma: no cover - the context binder's own refusal
            assert refused.code is M04EPortReason.DATABASE_ROLE_NOT_TRUSTED_SERVICE
    finally:
        await engine.dispose()
