"""The registered cutover schema is the schema the repository works against.

``MIG-DDL-CUTOVER-01`` turned ``CUTOVER_REQUIRED_SCHEMA`` into two registered models and an
Alembic revision, which is what unsticks ``SqlCutoverRepository``. ``ports.py`` deliberately
stayed the source of truth (``CUTOVER_REQUIRED_SCHEMA``, ``CUTOVER_LEDGER_TABLE``,
``CUTOVER_SOURCE_TAIL_TABLE``), so the questions this module answers are the ones that matter
for the port:

* are the names, columns and keys in the specification the ones the registered tables have;
* does the repository stop failing closed (``missing_tables() == ()``);
* do the state CAS and the command idempotency key actually reject at storage level on the
  schema the revision will create.

The containers come from ``EvidenceDatabase.create(cutover_from_models=True)``, which copies
the models' columns and unique keys. The PostgreSQL-only check predicates are not rendered
by that harness (SQLite cannot evaluate ``~``); they are asserted against the models and the
revision in ``tests/migration/m06_cutover_evidence``.

One finding is recorded rather than worked around: ``ports._ledger_values`` binds
``occurred_at`` as an ISO string while the reviewed specification declares ``TIMESTAMPTZ``.
That mismatch is pinned by its own test below — it is invisible to SQLite (which does not
enforce column types) and fatal under ``asyncpg`` — and named in
``M06_CUTOVER_EVIDENCE_DEFERRED['occurred_at_bound_as_iso_string']`` so it is not lost.
"""

from __future__ import annotations

import json
from datetime import datetime
from collections.abc import Mapping

import pytest

from prodavan.application.migration.m06_cutover.cutover import (
    CutoverAction,
    CutoverCommandService,
    CutoverState,
    PartitionCutover,
    PromotionRecord,
    _lineage_payload_digest,
)
from prodavan.application.migration.m06_cutover.ports import (
    CUTOVER_LEDGER_TABLE,
    CUTOVER_REQUIRED_SCHEMA,
    CUTOVER_SCHEMA_BLOCKERS,
    CUTOVER_SOURCE_TAIL_TABLE,
    SqlCutoverRepository,
    build_cutover_ports,
    evidence_payload,
    state_payload,
)
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.m06_cutover_evidence import (
    M06_CUTOVER_EVIDENCE_DEFERRED,
)

from .acceptance_fixtures import NOW, PARTITION, gate, lease, legacy_owner
from .ports_fixtures import seed_fence, seed_gate_rows, seed_legacy_owner_row, seed_source_tail
from .sqlite_evidence import CUTOVER_MODELS, EvidenceDatabase

TENANT = PARTITION.tenant_id
DEFINITION = PARTITION.app_definition_id
FROZEN_SEQUENCE = 42

SPEC_BY_TABLE = {spec.name: spec for spec in CUTOVER_REQUIRED_SCHEMA}
MODEL_BY_TABLE = {model.__tablename__: model for model in CUTOVER_MODELS}


@pytest.fixture
async def model_env():
    """A database whose two cutover tables were created from the registered models."""
    database = await EvidenceDatabase().create(cutover_from_models=True)
    try:
        yield database
    finally:
        await database.close()


def service(repository) -> CutoverCommandService:
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


def _json_text(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _shadow_record(*, evidence_id: str):
    """A minimal, self-consistent SHADOW lineage record for a hand-built ledger row."""

    payload = {
        "evidence_id": evidence_id, "command_digest": "sha256:" + "1" * 64,
        "partition": [PARTITION.tenant_id, PARTITION.app_definition_id],
        "action": "shadow", "actor": "operator", "occurred_at": NOW.isoformat(),
        "prior_state": "legacy_active", "resulting_state": "shadow",
        "prior_read_owner": "legacy", "resulting_read_owner": "legacy",
        "prior_decision_owner": "legacy", "resulting_decision_owner": "legacy",
        "prior_write_owner": "legacy", "resulting_write_owner": "legacy",
        "prior_record_id": None, "prior_record_digest": None,
        "canonical_positive_writes": False, "writer_epoch": 1,
        "lease_fencing_token": "fence-1", "gate_evidence": [],
        "parity_report_digest": None, "canonical_snapshot_digest": None,
        "source_snapshot_digest": None, "final_delta_checkpoint": None,
        "final_delta_source_change_sequence": None, "reason": "operator_entered_shadow",
    }
    return PromotionRecord(
        evidence_id=evidence_id, partition=PARTITION, action=CutoverAction.SHADOW,
        actor="operator", occurred_at=NOW, prior_state=CutoverState.LEGACY_ACTIVE,
        resulting_state=CutoverState.SHADOW, prior_read_owner="legacy",
        resulting_read_owner="legacy", writer_epoch=1, lease_fencing_token="fence-1",
        parity_report_digest=None, canonical_snapshot_digest=None, source_snapshot_digest=None,
        final_delta_checkpoint=None, final_delta_source_change_sequence=None,
        reason="operator_entered_shadow", record_digest=_lineage_payload_digest(payload),
        command_digest="sha256:" + "1" * 64, prior_write_owner="legacy",
        resulting_write_owner="legacy", prior_decision_owner="legacy",
        resulting_decision_owner="legacy", prior_record_id=None, prior_record_digest=None,
        gate_evidence=(), canonical_positive_writes=False,
    )


def _shadow_state(record, *, revision: int) -> PartitionCutover:
    return PartitionCutover(
        PARTITION, state=CutoverState.SHADOW, writer_epoch=record.writer_epoch,
        lease_holder="cutover-1", fencing_token=record.lease_fencing_token,
        canonical_shadow_reads=True, revision=revision,
        last_evidence_id=record.evidence_id,
        lineage_head_digest=record.record_digest,
    )


def _ledger_row(
    *,
    revision: int,
    evidence_id: str = "cmd-shadow-1",
    correlation_id: str = "corr-1",
) -> dict[str, object]:
    """One well-formed ledger row for the model-backed containers, no repository involved."""

    record = _shadow_record(evidence_id=evidence_id)
    state = _shadow_state(record, revision=revision)
    return {
        "id": evidence_id,
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION,
        "revision": revision,
        "action": record.action.value,
        "correlation_id": correlation_id,
        "command_digest": record.command_digest,
        "record_digest": record.record_digest,
        "prior_record_id": None,
        "prior_record_digest": None,
        "resulting_state": state.state.value,
        "writer_epoch": record.writer_epoch,
        "lease_fencing_token": record.lease_fencing_token,
        "canonical_positive_writes": False,
        "occurred_at": record.occurred_at,
        "state_payload": _json_text(state_payload(state)),
        "record_payload": _json_text(evidence_payload(record)),
    }


# ---------------------------------------------------------------------------
# Names and registration
# ---------------------------------------------------------------------------


def test_the_specification_names_are_the_registered_table_names() -> None:
    """``ports.py`` stays the source of truth; the models must own exactly its names."""
    assert set(SPEC_BY_TABLE) == {CUTOVER_LEDGER_TABLE, CUTOVER_SOURCE_TAIL_TABLE}
    assert set(MODEL_BY_TABLE) == set(SPEC_BY_TABLE)
    assert all(table_name in Base.metadata.tables for table_name in SPEC_BY_TABLE)


@pytest.mark.parametrize("table_name", tuple(SPEC_BY_TABLE))
def test_the_specification_columns_exist_on_the_registered_model(table_name: str) -> None:
    """Every column the repository writes or reads exists, in the specification's order."""
    spec = SPEC_BY_TABLE[table_name]
    model_columns = tuple(column.name for column in Base.metadata.tables[table_name].columns)

    assert model_columns == spec.column_names


@pytest.mark.parametrize("table_name", tuple(SPEC_BY_TABLE))
def test_the_model_keys_include_the_specification_keys(table_name: str) -> None:
    """The CAS and idempotency keys are the ones the specification declares."""
    import sqlalchemy as sa

    model_table = Base.metadata.tables[table_name]
    spec = SPEC_BY_TABLE[table_name]

    declared = {
        tuple(column.name for column in constraint.columns)
        for constraint in model_table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert declared == set(spec.unique)
    assert tuple(column.name for column in model_table.primary_key.columns) == spec.primary_key


def test_each_model_is_the_single_owner_of_its_table() -> None:
    for table_name, model in MODEL_BY_TABLE.items():
        assert Base.metadata.tables[table_name] is model.__table__
        owners = [
            mapper.class_
            for mapper in Base.registry.mappers
            if mapper.local_table is model.__table__
        ]
        assert owners == [model]


# ---------------------------------------------------------------------------
# The port now resolves the schema
# ---------------------------------------------------------------------------


async def test_the_repository_no_longer_reports_a_missing_table(model_env) -> None:
    """The fail-closed blocker the cutover was stuck behind is gone for this schema."""
    repository = SqlCutoverRepository(session_factory=model_env.session_factory)
    assert await repository.missing_tables() == ()
    # What it *was* blocked on is unchanged: the specification is still the contract.
    assert repository.required_schema == CUTOVER_REQUIRED_SCHEMA
    assert repository.schema_blockers == CUTOVER_SCHEMA_BLOCKERS


async def test_the_repository_answers_an_empty_partition(model_env) -> None:
    """``load`` reaches the ledger instead of refusing: an empty partition is ``None``."""
    repository = SqlCutoverRepository(session_factory=model_env.session_factory)
    assert await repository.load(PARTITION) is None
    assert await repository.evidence_by_id("cmd-unknown") is None


async def test_the_model_cas_refuses_a_second_row_for_one_revision(model_env) -> None:
    """``uq_m06_cutover_partition_revision`` is a storage-level CAS, not a convention."""
    from sqlalchemy.exc import IntegrityError

    await model_env.insert(CUTOVER_LEDGER_TABLE, **_ledger_row(revision=1))
    with pytest.raises(IntegrityError):
        await model_env.insert(
            CUTOVER_LEDGER_TABLE, **_ledger_row(revision=1, evidence_id="cmd-shadow-race")
        )
    assert await model_env.count(CUTOVER_LEDGER_TABLE) == 1


async def test_the_model_idempotency_key_refuses_a_replayed_correlation(model_env) -> None:
    """``uq_m06_cutover_command`` is per (partition, action, correlation), not per id."""
    from sqlalchemy.exc import IntegrityError

    await model_env.insert(CUTOVER_LEDGER_TABLE, **_ledger_row(revision=1))
    with pytest.raises(IntegrityError):
        await model_env.insert(
            CUTOVER_LEDGER_TABLE,
            **_ledger_row(revision=2, evidence_id="cmd-shadow-replay", correlation_id="corr-1"),
        )
    assert await model_env.count(CUTOVER_LEDGER_TABLE) == 1


async def test_a_recorded_row_round_trips_through_the_repository(model_env) -> None:
    """A row written with the model's declared types is readable by the codecs."""
    repository = SqlCutoverRepository(session_factory=model_env.session_factory)

    await model_env.insert(CUTOVER_LEDGER_TABLE, **_ledger_row(revision=1))

    state = await repository.load(PARTITION)
    record = await repository.evidence_by_id("cmd-shadow-1")
    assert state is not None and state.revision == 1
    assert record is not None and record.evidence_id == "cmd-shadow-1"
    assert state.lineage_head_digest == record.record_digest


async def test_the_whole_transition_chain_runs_on_the_registered_schema(model_env) -> None:
    """The path a host will run, against the schema the revision actually creates.

    Nothing here is stubbed: the real command service, the real verifiers over the five
    evidence tables, and the real repository appending through the model-declared columns
    and keys. What SQLite cannot judge is the ``timestamptz`` parameter typing, pinned
    separately by ``test_the_repository_binds_occurred_at_as_iso_text_...``.
    """

    await seed_cutover_ready_partition(model_env)
    repository = SqlCutoverRepository(session_factory=model_env.session_factory)
    commands = service(repository)

    bound = await commands.bind_legacy_owner(
        partition=PARTITION, actor="operator", command_id="cmd-owner",
        lease=lease(3), evidence=legacy_owner(3), now=NOW,
    )
    assert bound.state is CutoverState.LEGACY_ACTIVE and bound.revision == 1

    shadow = await commands.enter_shadow(
        partition=PARTITION, actor="operator", command_id="cmd-shadow",
        lease=lease(4), now=NOW,
    )
    assert shadow.state is CutoverState.SHADOW and shadow.revision == 2

    frozen = await commands.freeze_final_delta(
        partition=PARTITION, actor="operator", command_id="cmd-delta",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    assert frozen.state is CutoverState.FINAL_DELTA
    assert frozen.final_delta_source_change_sequence == FROZEN_SEQUENCE

    promoted = await commands.promote(
        partition=PARTITION, actor="operator", command_id="cmd-promote",
        lease=lease(5), gate=gate(epoch=5), now=NOW,
    )
    assert promoted.state is CutoverState.PROMOTED
    assert promoted.canonical_positive_writes

    # Append-only lineage: four rows, and the latest state is what the repository loads.
    assert await model_env.count(CUTOVER_LEDGER_TABLE) == 4
    assert await repository.load(PARTITION) == promoted
    record = await repository.evidence_by_id("cmd-promote")
    assert isinstance(record, PromotionRecord)
    assert record is not None and record.canonical_positive_writes is True


# ---------------------------------------------------------------------------
# The one cross-owner gap this DDL exposes
# ---------------------------------------------------------------------------


def test_the_repository_binds_occurred_at_as_a_datetime_for_a_timestamp_column() -> None:
    """The bind value must match the reviewed column type.

    ``_ledger_values`` hands ``occurred_at`` to a raw ``text()`` INSERT while the
    reviewed specification declares the column ``TIMESTAMPTZ``. SQLite does not enforce
    column types, so a string passed there and only asyncpg's codec refused it: the
    mismatch stayed invisible until this suite ran against PostgreSQL, where it would
    have failed at the first append of a live cutover.

    This test used to pin the mismatch (asserting a ``str``) as a reminder. The reminder
    did its job -- ``ports.py`` now binds a real ``datetime`` -- so it asserts the
    correct pairing instead. The envelope payload still carries the ISO form, because
    that one is JSON, not a bind value.
    """

    import sqlalchemy as sa

    from prodavan.application.migration.m06_cutover import ports

    record = _shadow_record(evidence_id="cmd-shadow-1")
    state = _shadow_state(record, revision=1)
    values = ports._ledger_values(state, record, lease(1))

    assert isinstance(values["occurred_at"], datetime), (
        "a string bind would fail on asyncpg timestamptz"
    )
    column = Base.metadata.tables[CUTOVER_LEDGER_TABLE].c["occurred_at"]
    assert isinstance(column.type, sa.DateTime)
    assert column.type.timezone is True, "the specification declares TIMESTAMPTZ"


def test_the_occurred_at_binding_gap_is_recorded() -> None:
    """A deferred cross-owner fix must be named, not silently assumed to be fine."""
    reason = M06_CUTOVER_EVIDENCE_DEFERRED["occurred_at_bound_as_iso_string"]

    assert "TIMESTAMPTZ" in reason
    assert "isoformat()" in reason
    assert "ports.py" in reason
