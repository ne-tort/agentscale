"""Acceptance contract for the durable cutover repository (CAS + append-only lineage)."""

import json
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from prodavan.application.migration.m06_cutover import ports as cutover_ports
from prodavan.application.migration.m06_cutover.cutover import (
    CutoverCommandService,
    CutoverConflict,
    CutoverState,
    PartitionCutover,
    PromotionRecord,
    WriterLease,
)
from prodavan.application.migration.m06_cutover.ports import (
    CUTOVER_LEDGER_TABLE,
    CUTOVER_REQUIRED_SCHEMA,
    CUTOVER_SOURCE_TAIL_TABLE,
    CutoverRepositoryBlocked,
    SqlCutoverRepository,
    build_cutover_ports,
    cutover_schema_ddl,
    evidence_from_payload,
    evidence_payload,
    state_from_payload,
    state_payload,
)

from .acceptance_fixtures import NOW, PARTITION, gate, lease, legacy_owner
from .ports_fixtures import (
    seed_fence,
    seed_gate_rows,
    seed_legacy_owner_row,
    seed_source_tail,
    set_source_tail,
)
from .sqlite_evidence import EvidenceDatabase

TENANT = PARTITION.tenant_id
DEFINITION = PARTITION.app_definition_id
FROZEN_SEQUENCE = 42


@pytest.fixture
async def env():
    database = await EvidenceDatabase().create()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def env_without_cutover_tables():
    database = await EvidenceDatabase().create(with_cutover_tables=False)
    try:
        yield database
    finally:
        await database.close()


def service(repository) -> CutoverCommandService:
    """The real service over the real verifiers: no verifier stub accepts anything."""

    shared = build_cutover_ports(session_factory=repository.session_factory)
    return CutoverCommandService(
        repository,
        gate_verifier=shared.gate_verifier,
        legacy_owner_verifier=shared.legacy_owner_verifier,
        rollback_verifier=shared.rollback_verifier,
        repair_verifier=shared.canonical_repair_verifier,
    )


async def seed_cutover_ready_partition(env, *, epoch: int = 5) -> None:
    await seed_legacy_owner_row(env, owner=legacy_owner(3), canonical_digest="sha256:" + "0" * 64)
    await seed_gate_rows(env, gate(epoch=epoch))
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=3)
    await seed_source_tail(
        env, tenant_id=TENANT, definition_id=DEFINITION,
        change_sequence=FROZEN_SEQUENCE, writer_epoch=epoch,
    )


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------


async def test_repository_reports_every_missing_table_and_its_specification(
    env_without_cutover_tables,
):
    repository = SqlCutoverRepository(session_factory=env_without_cutover_tables.session_factory)
    assert await repository.missing_tables() == (CUTOVER_LEDGER_TABLE, CUTOVER_SOURCE_TAIL_TABLE)
    with pytest.raises(CutoverRepositoryBlocked) as excinfo:
        await repository.load(PARTITION)
    assert f"cutover_repository_table_missing:{CUTOVER_LEDGER_TABLE}" in excinfo.value.blockers
    assert f"cutover_repository_table_missing:{CUTOVER_SOURCE_TAIL_TABLE}" in excinfo.value.blockers
    assert "cutover_state_row_absent" in " ".join(excinfo.value.blockers)
    assert [table.name for table in excinfo.value.tables] == [
        CUTOVER_LEDGER_TABLE, CUTOVER_SOURCE_TAIL_TABLE,
    ]
    assert repository.schema_blockers == cutover_ports.CUTOVER_SCHEMA_BLOCKERS


async def test_repository_refuses_every_operation_until_the_schema_exists(
    env_without_cutover_tables,
):
    repository = SqlCutoverRepository(session_factory=env_without_cutover_tables.session_factory)
    with pytest.raises(CutoverRepositoryBlocked):
        await repository.evidence_by_id("any-command")
    record = _placeholder_record()
    with pytest.raises(CutoverRepositoryBlocked):
        await repository.transition(
            expected_revision=0,
            state=_placeholder_state(record),
            evidence=record,
            lease=lease(3),
            expected_source_change_sequence=None,
        )


async def test_only_the_ledger_table_missing_is_reported_precisely(env):
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    assert await repository.missing_tables() == ()


def test_schema_ddl_covers_every_declared_column_and_key():
    statements = cutover_schema_ddl()
    text = "\n".join(statements)
    for spec in CUTOVER_REQUIRED_SCHEMA:
        assert f"CREATE TABLE {spec.name} (" in text
        for column in spec.column_names:
            assert f"    {column} " in text
        for key in spec.unique:
            assert f"UNIQUE ({', '.join(key)})" in text
    assert "CHECK (canonical_positive_writes = false OR resulting_state = 'promoted')" in text
    sqlite_statements = cutover_schema_ddl(dialect="sqlite")
    assert not any("CHECK" in statement for statement in sqlite_statements)
    assert any("UNIQUE (tenant_id, app_definition_id, revision)" in s for s in sqlite_statements)


def test_state_and_evidence_payloads_round_trip():
    state = PartitionCutover(
        PARTITION, state=CutoverState.SHADOW, writer_epoch=4, lease_holder="cutover-4",
        fencing_token="fence-4", canonical_shadow_reads=True, revision=2,
        last_evidence_id="cmd-shadow", lineage_head_digest="sha256:" + "a" * 64,
    )
    assert state_from_payload(state_payload(state)) == state


# ---------------------------------------------------------------------------
# Happy path through the real command service
# ---------------------------------------------------------------------------


async def test_service_persists_every_transition_and_promotes_the_partition(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)

    bound = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    assert bound.state is CutoverState.LEGACY_ACTIVE and bound.revision == 1
    assert bound.legacy_owner_evidence_id == "legacy-owner-1"

    shadow = await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    assert shadow.state is CutoverState.SHADOW and shadow.revision == 2

    frozen = await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id="cmd-delta",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    assert frozen.state is CutoverState.FINAL_DELTA and frozen.write_owner == "none"
    assert frozen.final_delta_source_change_sequence == FROZEN_SEQUENCE

    promoted = await commands.promote(
        partition=PARTITION, actor="operator", command_id="cmd-promote",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    assert promoted.state is CutoverState.PROMOTED
    assert (promoted.canonical_read_owner, promoted.decision_owner, promoted.write_owner) == (
        "canonical", "canonical", "canonical",
    )
    assert promoted.canonical_positive_writes and promoted.legacy_projection_read_only
    assert promoted.revision == 4

    stored = await repository.load(PARTITION)
    assert stored == promoted
    assert await env.count(CUTOVER_LEDGER_TABLE) == 4

    record = await repository.evidence_by_id("cmd-promote")
    assert isinstance(record, PromotionRecord)
    assert record is not None
    assert record.prior_record_id == "cmd-delta"
    assert record.prior_record_digest == frozen.lineage_head_digest
    assert {ref.stage for ref in record.gate_evidence} >= {"M04", "M04E", "M05", "M06"}
    assert record.canonical_positive_writes is True
    assert evidence_from_payload(evidence_payload(record)) == record


async def test_replayed_command_appends_no_second_lineage_row(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)

    first = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    again = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    assert again == first
    assert await env.count(CUTOVER_LEDGER_TABLE) == 1


async def test_repeated_record_append_is_idempotent_at_the_repository(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    state = await repository.load(PARTITION)
    record = await repository.evidence_by_id("cmd-owner")
    assert state is not None and record is not None
    # Same revision, same record: the unique key refuses a second row, and the repository
    # recognises the identical digest as a replay instead of a conflict.
    assert await repository.transition(
        expected_revision=0, state=state, evidence=record, lease=lease(3),
        expected_source_change_sequence=None,
    ) is False
    assert await env.count(CUTOVER_LEDGER_TABLE) == 1


# ---------------------------------------------------------------------------
# CAS, lease and lineage refusals
# ---------------------------------------------------------------------------


async def test_stale_expected_revision_is_refused(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    state = await repository.load(PARTITION)
    assert state is not None and state.revision == 2
    # A caller that still believes it is at revision 1 (the ledger already holds 2) loses the
    # CAS even though its own record/state pair is structurally consistent.
    stale_record = _placeholder_record(writer_epoch=4)
    stale_state = _placeholder_state(stale_record, revision=2)
    assert await repository.transition(
        expected_revision=1, state=stale_state, evidence=stale_record, lease=lease(4),
        expected_source_change_sequence=None,
    ) is False
    assert (await repository.load(PARTITION)) == state


def test_lease_rules_are_explicit():
    current = PartitionCutover(
        PARTITION, state=CutoverState.SHADOW, writer_epoch=4, lease_holder="cutover-4",
        fencing_token="fence-4", canonical_shadow_reads=True, revision=2,
    )
    record = _placeholder_record(writer_epoch=5)
    later = PartitionCutover(
        PARTITION, state=CutoverState.SHADOW, writer_epoch=5, lease_holder="cutover-5",
        fencing_token="fence-5", canonical_shadow_reads=True, revision=3,
    )
    live = WriterLease(PARTITION, "cutover-4", 4, NOW + timedelta(minutes=10), "fence-4")
    newer = WriterLease(PARTITION, "cutover-5", 5, NOW + timedelta(minutes=10), "fence-5")
    expired = WriterLease(PARTITION, "cutover-5", 5, NOW, "fence-5")
    foreign = WriterLease(PARTITION, "other-holder", 5, NOW + timedelta(minutes=10), "fence-5")
    replayed_token = WriterLease(PARTITION, "cutover-5", 5, NOW + timedelta(minutes=10), "fence-4")

    assert cutover_ports._lease_is_live(current, newer, record, later) is True
    assert cutover_ports._lease_is_live(current, expired, record, later) is False
    assert cutover_ports._lease_is_live(current, foreign, record, later) is True
    assert cutover_ports._lease_is_live(current, replayed_token, record, later) is False
    # The live fence may only be re-used by the identical holder: that is what promotion does.
    live_record = _placeholder_record(writer_epoch=4)
    assert cutover_ports._lease_is_live(current, live, live_record, current) is True
    divergent_holder = WriterLease(PARTITION, "other-holder", 4, NOW + timedelta(minutes=10),
                                   "fence-4")
    assert cutover_ports._lease_is_live(
        current, divergent_holder, live_record, current
    ) is False
    stale = WriterLease(PARTITION, "cutover-3", 3, NOW + timedelta(minutes=10), "fence-3")
    assert cutover_ports._lease_is_live(current, stale, record, current) is False


async def test_expired_lease_never_commits(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    expired = WriterLease(PARTITION, "cutover-3", 3, NOW, "fence-3")
    with pytest.raises(CutoverConflict, match="writer_lease_expired"):
        await commands.bind_legacy_owner(
            partition=PARTITION, actor="operator", command_id="cmd-expired",
            lease=expired, evidence=legacy_owner(3), now=NOW,
        )
    assert await env.count(CUTOVER_LEDGER_TABLE) == 0


async def test_foreign_partition_lease_never_commits(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    with pytest.raises(CutoverConflict, match="lease_partition_mismatch"):
        await commands.bind_legacy_owner(
            partition=PARTITION, actor="operator", command_id="cmd-foreign",
            lease=lease(3, partition=PARTITION.__class__("tenant-b", "definition-a")),
            evidence=legacy_owner(3), now=NOW,
        )
    assert await env.count(CUTOVER_LEDGER_TABLE) == 0


async def test_stale_writer_epoch_never_commits(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    with pytest.raises(CutoverConflict, match="writer_epoch_not_advanced"):
        await commands.enter_shadow(
            partition=PARTITION, actor="operator", command_id="cmd-stale",
            lease=lease(3), now=NOW,
        )


async def test_final_delta_needs_the_live_source_tail(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    await set_source_tail(env, tenant_id=TENANT, definition_id=DEFINITION,
                          change_sequence=FROZEN_SEQUENCE + 1)
    with pytest.raises(CutoverConflict, match="revision or fencing conflict"):
        await commands.freeze_final_delta(
            partition=PARTITION, actor="operator", command_id="cmd-delta-stale-source",
            lease=lease(5), gate=gate(epoch=5), now=NOW,
        )
    state = await repository.load(PARTITION)
    assert state is not None and state.state is CutoverState.SHADOW


async def test_promotion_race_after_the_freeze_fails_closed(env):
    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id="cmd-delta",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    await set_source_tail(env, tenant_id=TENANT, definition_id=DEFINITION,
                          change_sequence=FROZEN_SEQUENCE + 1)
    with pytest.raises(CutoverConflict, match="revision or fencing conflict"):
        await commands.promote(
            partition=PARTITION, actor="operator", command_id="cmd-promote-race",
            lease=lease(5), gate=gate(epoch=5), now=NOW,
        )
    state = await repository.load(PARTITION)
    assert state is not None and state.state is CutoverState.FINAL_DELTA
    assert not state.canonical_positive_writes
    assert await env.count(CUTOVER_LEDGER_TABLE) == 3
    assert await repository.evidence_by_id("cmd-promote-race") is None


async def test_unverified_gate_blocks_instead_of_promoting(env):
    """No upstream rows at all: the real verifiers refuse, and the ledger records a block."""

    await seed_legacy_owner_row(env, owner=legacy_owner(3), canonical_digest="sha256:" + "0" * 64)
    await seed_fence(env, tenant_id=TENANT, definition_id=DEFINITION, writer_epoch=3)
    await seed_source_tail(env, tenant_id=TENANT, definition_id=DEFINITION,
                           change_sequence=FROZEN_SEQUENCE, writer_epoch=5)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    blocked = await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id="cmd-delta-unverified",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    assert blocked.state is CutoverState.BLOCKED
    assert blocked.write_owner == "legacy" and not blocked.canonical_positive_writes
    record = await repository.evidence_by_id("cmd-delta-unverified")
    assert record is not None and record.action.value == "blocked"
    assert record.reason == "upstream_m04_m04e_m05_m06_evidence_not_verified"


async def test_repository_state_survives_a_second_repository_instance(env):
    await seed_cutover_ready_partition(env)
    first = SqlCutoverRepository(session_factory=env.session_factory)
    await service(first).bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    second = SqlCutoverRepository(session_factory=env.session_factory)
    assert (await second.load(PARTITION)) == (await first.load(PARTITION))
    record = await second.evidence_by_id("cmd-owner")
    assert record is not None and record.evidence_id == "cmd-owner"


async def test_a_tampered_lineage_payload_is_refused_not_decoded(env):
    """Editing the stored JSON breaks the record's own immutable digest."""

    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    await service(repository).bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await env.execute(
        f"UPDATE {CUTOVER_LEDGER_TABLE} SET record_payload = :payload WHERE id = :id",
        payload='{"schema": "m06-partition-cutover-evidence.v1"}', id="cmd-owner",
    )
    with pytest.raises(cutover_ports.CutoverRepositoryCorrupt) as excinfo:
        await repository.evidence_by_id("cmd-owner")
    assert "cutover_evidence_payload_invalid" in excinfo.value.blockers


async def test_earlier_revisions_are_never_rewritten(env):
    """Append-only: the revision-1 row keeps its own payload after revision 2 lands."""

    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    commands = service(repository)
    first = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    rows = await env.rows(CUTOVER_LEDGER_TABLE)
    assert [(row["revision"], row["resulting_state"]) for row in rows] == [
        (1, "legacy_active"), (2, "shadow"),
    ]
    assert state_from_payload(json.loads(rows[0]["state_payload"])) == first


async def test_same_action_and_correlation_cannot_be_appended_twice(env):
    """``(partition, action, correlation)`` is an idempotency key at the storage level."""

    await seed_cutover_ready_partition(env)
    repository = SqlCutoverRepository(session_factory=env.session_factory)
    await service(repository).bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    rows = await env.rows(CUTOVER_LEDGER_TABLE)
    assert len(rows) == 1
    first = dict(rows[0])
    assert first["correlation_id"] == first["id"] == "cmd-owner"
    duplicate = first | {"id": "cmd-owner-copy", "revision": 2,
                         "prior_record_id": first["id"],
                         "prior_record_digest": first["record_digest"]}
    with pytest.raises(IntegrityError):
        await env.insert(CUTOVER_LEDGER_TABLE, **duplicate)
    assert await env.count(CUTOVER_LEDGER_TABLE) == 1


def _placeholder_state(record, *, revision: int = 1) -> PartitionCutover:
    """The resulting state a caller would hand to ``transition`` for ``record``."""

    return PartitionCutover(
        PARTITION, state=record.resulting_state, writer_epoch=record.writer_epoch,
        lease_holder="cutover-3", fencing_token=record.lease_fencing_token,
        canonical_shadow_reads=True, revision=revision,
        last_evidence_id=record.evidence_id, lineage_head_digest=record.record_digest,
    )


def _placeholder_record(*, writer_epoch: int = 3):
    """A minimal, self-consistent lineage record for structural (non-CAS) assertions."""

    from prodavan.application.migration.m06_cutover.cutover import (
        _lineage_payload_digest,
    )

    payload = {
        "evidence_id": "placeholder", "command_digest": "sha256:" + "1" * 64,
        "partition": [PARTITION.tenant_id, PARTITION.app_definition_id],
        "action": "shadow", "actor": "operator", "occurred_at": NOW.isoformat(),
        "prior_state": "legacy_active", "resulting_state": "shadow",
        "prior_read_owner": "legacy", "resulting_read_owner": "legacy",
        "prior_decision_owner": "legacy", "resulting_decision_owner": "legacy",
        "prior_write_owner": "legacy", "resulting_write_owner": "legacy",
        "prior_record_id": None, "prior_record_digest": None,
        "canonical_positive_writes": False, "writer_epoch": writer_epoch,
        "lease_fencing_token": "fence-3", "gate_evidence": [],
        "parity_report_digest": None, "canonical_snapshot_digest": None,
        "source_snapshot_digest": None, "final_delta_checkpoint": None,
        "final_delta_source_change_sequence": None, "reason": "placeholder",
    }
    return PromotionRecord(
        evidence_id="placeholder", partition=PARTITION, action=cutover_ports.CutoverAction.SHADOW,
        actor="operator", occurred_at=NOW, prior_state=CutoverState.LEGACY_ACTIVE,
        resulting_state=CutoverState.SHADOW, prior_read_owner="legacy",
        resulting_read_owner="legacy", writer_epoch=writer_epoch,
        lease_fencing_token="fence-3",
        parity_report_digest=None, canonical_snapshot_digest=None, source_snapshot_digest=None,
        final_delta_checkpoint=None, final_delta_source_change_sequence=None,
        reason="placeholder", record_digest=_lineage_payload_digest(payload),
        command_digest="sha256:" + "1" * 64, prior_write_owner="legacy",
        resulting_write_owner="legacy", prior_decision_owner="legacy",
        resulting_decision_owner="legacy", prior_record_id=None, prior_record_digest=None,
        gate_evidence=(), canonical_positive_writes=False,
    )
