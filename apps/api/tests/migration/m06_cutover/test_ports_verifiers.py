"""Acceptance contract for the four executable cutover verifiers.

Every case seeds the four registered evidence tables (plus the writer fence) and then asks a
verifier one question. The positive cases prove the verifier can say "yes" from stored rows;
the negative cases prove it never says "yes" from a caller-asserted boolean, a missing row, a
cross-partition row, a stale epoch or an unready row.
"""

from dataclasses import replace

import pytest

from prodavan.application.migration.m06_cutover.ports import (
    SqlCanonicalRepairVerifier,
    SqlGateVerifier,
    SqlLegacyOwnerVerifier,
    SqlRollbackVerifier,
)

from .acceptance_fixtures import (
    OTHER_PARTITION,
    PARTITION,
    digest,
    gate,
    legacy_owner,
    repair_evidence,
    rollback_evidence,
)
from .ports_fixtures import (
    M04_TABLE,
    M04E_TABLE,
    M05_TABLE,
    M06_TABLE,
    attach_repair_block,
    seed_fence,
    seed_gate_rows,
    seed_legacy_owner_row,
)
from .sqlite_evidence import EvidenceDatabase

TENANT = PARTITION.tenant_id
DEFINITION = PARTITION.app_definition_id


@pytest.fixture
async def env():
    database = await EvidenceDatabase().create()
    try:
        yield database
    finally:
        await database.close()


async def _mark_not_ready(env: EvidenceDatabase, table: str, row_id: str) -> None:
    """Rewrite the row as a refusal the way a blocked producer would (column + envelope)."""

    for row in await env.rows(table):
        if row["id"] == row_id:
            envelope = dict(row["gate_evidence"])
            envelope["status"] = "blocked"
            await env.update(
                table, where={"id": row_id}, outcome="blocked", gate_evidence=envelope
            )
            return
    raise AssertionError(f"row {row_id} not seeded in {table}")


# ---------------------------------------------------------------------------
# GateVerifier
# ---------------------------------------------------------------------------


async def test_gate_verifier_accepts_a_fully_stored_m04_m04e_m05_m06_chain(env):
    complete = gate(epoch=3)
    await seed_gate_rows(env, complete)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=3)

    verifier = SqlGateVerifier(session_factory=env.session_factory)
    assert await verifier.verify(evidence=complete) is True


async def test_gate_verifier_refuses_incomplete_evidence_without_reading_the_database(env):
    verifier = SqlGateVerifier(session_factory=env.session_factory)
    assert await verifier.verify(evidence=replace(gate(), m05=None)) is False
    assert await verifier.verify(evidence=replace(gate(), unresolved_positive_bindings=1)) is False
    assert await verifier.verify(evidence=replace(gate(), final_delta=None)) is False


async def test_gate_verifier_refuses_when_a_stage_row_is_absent(env):
    complete = gate()
    await seed_gate_rows(env, complete)
    await env.execute(f"DELETE FROM {M05_TABLE}")
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_an_unready_stage_row(env):
    complete = gate()
    await seed_gate_rows(env, complete)
    assert complete.m04e is not None
    await _mark_not_ready(env, M04E_TABLE, complete.m04e.evidence_id)
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_a_stage_row_with_a_different_canonical_digest(env):
    complete = gate()
    await seed_gate_rows(env, complete, m05_canonical_digest=digest("8"))
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_a_cross_partition_stage_row(env):
    complete = gate()
    await seed_gate_rows(env, complete)
    await env.execute(f"UPDATE {M04_TABLE} SET tenant_id = 'tenant-b'")
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_an_m06_row_from_another_writer_epoch(env):
    complete = gate(epoch=3)
    await seed_gate_rows(env, complete, m06_writer_epoch=4)
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_an_m06_row_that_does_not_cite_the_drain_receipt(env):
    complete = gate()
    await seed_gate_rows(env, complete)
    assert complete.legacy_writer_drain is not None
    drain_id = complete.legacy_writer_drain.evidence_id
    for row in await env.rows(M06_TABLE):
        envelope = dict(row["gate_evidence"])
        envelope["lineage"] = [
            ref for ref in envelope["lineage"] if ref["evidence_id"] != drain_id
        ]
        await env.update(M06_TABLE, where={"id": row["id"]}, gate_evidence=envelope)
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_an_m06_row_that_enabled_canonical_positive_writes(env):
    complete = gate()
    await seed_gate_rows(env, complete)
    await env.execute(
        f"UPDATE {M06_TABLE} SET canonical_positive_writes = 1"  # SQLite stores booleans
    )
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


async def test_gate_verifier_refuses_when_the_live_writer_fence_is_ahead_of_the_gate(env):
    complete = gate(epoch=3)
    await seed_gate_rows(env, complete)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=4,
                     scope_id="instance-2", fence_id="fence-ahead")
    assert not await SqlGateVerifier(session_factory=env.session_factory).verify(evidence=complete)


# ---------------------------------------------------------------------------
# LegacyOwnerVerifier
# ---------------------------------------------------------------------------


async def test_legacy_owner_verifier_accepts_a_stored_exclusive_owner_marker(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=3)
    assert await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=owner) is True


async def test_legacy_owner_verifier_refuses_without_a_stored_owner_block(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"),
                                extra_blocks={"legacy_owner": {}})
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=owner)


async def test_legacy_owner_verifier_refuses_a_non_exclusive_writer_claim(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    for row in await env.rows(M04_TABLE):
        envelope = dict(row["gate_evidence"])
        block = dict(envelope["legacy_owner"])
        block["exclusive_write_owner"] = False
        envelope["legacy_owner"] = block
        await env.update(M04_TABLE, where={"id": row["id"]}, gate_evidence=envelope)
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=owner)


async def test_legacy_owner_verifier_refuses_a_stale_epoch_or_foreign_fence(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=4,
                     scope_id="instance-2", fence_id="fence-ahead")
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=owner)


async def test_legacy_owner_verifier_refuses_a_row_that_already_enabled_positive_writes(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"),
                                canonical_positive_writes=True)
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=owner)


async def test_legacy_owner_verifier_refuses_an_unknown_marker(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    unknown = replace(owner, evidence_id="legacy-owner-unstored")
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=unknown)


async def test_legacy_owner_verifier_refuses_another_partitions_marker(env):
    owner = legacy_owner(3)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    foreign = replace(owner, partition=OTHER_PARTITION)
    assert not await SqlLegacyOwnerVerifier(
        session_factory=env.session_factory
    ).verify(evidence=foreign)


# ---------------------------------------------------------------------------
# RollbackVerifier
# ---------------------------------------------------------------------------


async def test_rollback_verifier_accepts_stored_drained_canonical_claims(env):
    owner = legacy_owner(3)
    rollback = rollback_evidence(4)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"), rollback=rollback)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=4)
    assert await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=rollback) is True


async def test_rollback_verifier_refuses_a_remaining_canonical_writer(env):
    owner = legacy_owner(3)
    rollback = rollback_evidence(4)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"), rollback=rollback)
    for row in await env.rows(M04_TABLE):
        envelope = dict(row["gate_evidence"])
        block = dict(envelope["rollback"])
        block["active_canonical_writers_remaining"] = 1
        envelope["rollback"] = block
        await env.update(M04_TABLE, where={"id": row["id"]}, gate_evidence=envelope)
    assert not await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=rollback)


async def test_rollback_verifier_refuses_unresolved_legacy_claims(env):
    owner = legacy_owner(3)
    rollback = rollback_evidence(4)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"), rollback=rollback)
    for row in await env.rows(M04_TABLE):
        envelope = dict(row["gate_evidence"])
        block = dict(envelope["rollback"])
        block["unresolved_legacy_claims"] = 2
        envelope["rollback"] = block
        await env.update(M04_TABLE, where={"id": row["id"]}, gate_evidence=envelope)
    assert not await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=rollback)


async def test_rollback_verifier_refuses_a_different_evidence_digest(env):
    owner = legacy_owner(3)
    rollback = rollback_evidence(4)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"), rollback=rollback)
    forged = replace(rollback, evidence_digest=digest("7"))
    assert not await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=forged)


async def test_rollback_verifier_refuses_when_the_fence_moved_forward_after_the_proof(env):
    owner = legacy_owner(3)
    rollback = rollback_evidence(4)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"), rollback=rollback)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=5)
    assert not await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=rollback)


async def test_rollback_verifier_refuses_without_any_stored_owner_row(env):
    assert not await SqlRollbackVerifier(
        session_factory=env.session_factory
    ).verify(evidence=rollback_evidence(4))


# ---------------------------------------------------------------------------
# CanonicalRepairVerifier
# ---------------------------------------------------------------------------


async def _repair_ready_env(env: EvidenceDatabase, *, epoch: int = 5):
    complete = gate(epoch=epoch)
    owner = legacy_owner(epoch)
    await seed_legacy_owner_row(env, owner=owner, canonical_digest=digest("0"))
    await seed_gate_rows(env, complete)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=epoch)
    repair = repair_evidence(epoch, "promote-final", digest("3"))
    await attach_repair_block(env, repair, gate=complete)
    return complete, repair


async def test_repair_verifier_accepts_a_fully_resolvable_repair_proof(env):
    _, repair = await _repair_ready_env(env)
    assert await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=repair) is True


async def test_repair_verifier_refuses_when_a_stage_reference_is_unstored(env):
    _, repair = await _repair_ready_env(env)
    broken = replace(
        repair,
        gate_evidence=tuple(
            replace(ref, evidence_id="never-stored") if ref.stage == "M04E" else ref
            for ref in repair.gate_evidence
        ),
    )
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=broken)


async def test_repair_verifier_refuses_a_count_that_contradicts_the_stored_block(env):
    _, repair = await _repair_ready_env(env)
    for row in await env.rows(M06_TABLE):
        envelope = dict(row["gate_evidence"])
        block = dict(envelope["canonical_repair"])
        block["unknown_mappings"] = 1
        envelope["canonical_repair"] = block
        await env.update(M06_TABLE, where={"id": row["id"]}, gate_evidence=envelope)
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=repair)


async def test_repair_verifier_refuses_a_parent_lineage_mismatch(env):
    _, repair = await _repair_ready_env(env)
    broken = replace(repair, parent_evidence_id="some-other-record")
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=broken)


async def test_repair_verifier_refuses_when_no_writer_fence_exists(env):
    _, repair = await _repair_ready_env(env)
    await env.execute("DELETE FROM dynamic_app_capability_writer_epochs")
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=repair)


async def test_repair_verifier_refuses_a_fence_ahead_of_the_repair(env):
    _, repair = await _repair_ready_env(env)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=6,
                     scope_id="instance-2", fence_id="fence-ahead")
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=repair)


async def test_repair_verifier_refuses_a_source_snapshot_that_drifted_from_the_stored_row(env):
    _, repair = await _repair_ready_env(env)
    await env.execute(f"UPDATE {M06_TABLE} SET source_digest = :digest",
                      digest=digest("2"))
    assert not await SqlCanonicalRepairVerifier(
        session_factory=env.session_factory
    ).verify(evidence=repair)
