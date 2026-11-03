"""SQLite acceptance contracts for the test-owned M05 fixture.

Intentionally authored but unrun. Passing these tests would exercise SQLite fixture
semantics only, never production PostgreSQL persistence or promotion readiness.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from .sqlite_acceptance import (
    AuthenticatedContext,
    CasAudit,
    Checkpoint,
    InstanceIdentity,
    SnapshotKey,
    SQLiteM05AcceptanceAdapter,
    instances,
    source_mappings,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


@pytest.fixture
def adapter():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    store = SQLiteM05AcceptanceAdapter(engine)
    store.create_schema()
    yield store
    engine.dispose()


def identity(*, source_id: str = "source-1", source_digest: str = DIGEST_A) -> InstanceIdentity:
    return InstanceIdentity(
        id="instance-1",
        plugin_instance_id="instance-1",
        definition_id="definition-1",
        version_id="version-1",
        tenant_id="tenant-1",
        scope_kind="plugin_instance",
        scope_id="instance-1",
        scope_registry_id="registry-1",
        cabinet_id="cabinet-1",
        project_id=None,
        artifact_digest=DIGEST_A,
        subject_id="subject-1",
        session_id="session-1",
        source_kind="legacy-instance",
        source_id=source_id,
        source_schema_version="schema-v1",
        source_digest=source_digest,
    )


def context(**changes) -> AuthenticatedContext:
    return replace(AuthenticatedContext("tenant-1", "subject-1", "session-1"), **changes)


def snapshot_key() -> SnapshotKey:
    return SnapshotKey("tenant-1", "definition-1", "snapshot-1", "schema-v1", DIGEST_A)


def checkpoint(after_source_id: str | None, sequence: int) -> Checkpoint:
    return Checkpoint(snapshot_key(), after_source_id, sequence)


def prepare_runtime(adapter: SQLiteM05AcceptanceAdapter) -> tuple[InstanceIdentity, AuthenticatedContext]:
    item = identity()
    auth = context()
    adapter.seed_session(auth)
    adapter.seed_instance(item)
    return item, auth


def test_writer_cas_compares_complete_identity_and_advances_exactly_one(adapter):
    item, auth = prepare_runtime(adapter)

    result = adapter.advance_writer(
        identity=item, context=auth, expected_runtime_generation=4,
        expected_writer_generation=7, next_writer_generation=8, correlation_id="corr-1",
    )
    assert result.changed
    assert (result.runtime_generation, result.writer_generation) == (4, 8)
    assert adapter.list_audits()[-1]["outcome"] == "advanced"

    stale_writer = adapter.advance_writer(
        identity=item, context=auth, expected_runtime_generation=4,
        expected_writer_generation=7, next_writer_generation=8, correlation_id="corr-stale-writer",
    )
    assert not stale_writer.changed
    assert stale_writer.reason_code == "stale_generation_zero_rows"
    assert adapter.list_audits()[-1]["outcome"] == "stale-generation"

    wrong_identity = replace(item, artifact_digest=DIGEST_B)
    denied = adapter.advance_writer(
        identity=wrong_identity, context=auth, expected_runtime_generation=4,
        expected_writer_generation=8, next_writer_generation=9, correlation_id="corr-2",
    )
    assert not denied.changed and denied.reason_code == "stale_generation_zero_rows"
    persisted = adapter.read_instance(item.id)
    assert persisted is not None and persisted["writer_generation"] == 8
    assert adapter.list_audits()[-1]["outcome"] == "stale-generation"


def test_runtime_cas_compares_both_generations_and_denies_stale_worker(adapter):
    item, auth = prepare_runtime(adapter)
    advanced = adapter.advance_runtime(
        identity=item, context=auth, expected_runtime_generation=4,
        expected_writer_generation=7, next_runtime_generation=5,
        next_writer_generation=8, correlation_id="corr-runtime",
    )
    assert advanced.changed
    assert (advanced.runtime_generation, advanced.writer_generation) == (5, 8)

    stale = adapter.advance_runtime(
        identity=item, context=auth, expected_runtime_generation=4,
        expected_writer_generation=7, next_runtime_generation=5,
        next_writer_generation=8, correlation_id="corr-stale",
    )
    assert not stale.changed and stale.reason_code == "stale_generation_zero_rows"
    persisted = adapter.read_instance(item.id)
    assert persisted is not None
    assert (persisted["runtime_generation"], persisted["writer_generation"]) == (5, 8)
    assert adapter.list_audits()[-1]["outcome"] == "stale-generation"


def test_authenticated_subject_session_tenant_and_active_session_are_required(adapter):
    item, _ = prepare_runtime(adapter)
    for invalid in (context(subject_id="subject-other"), context(session_id="session-other"),
                    context(tenant_id="tenant-other"), context(authenticated=False)):
        result = adapter.advance_writer(
            identity=item, context=invalid, expected_runtime_generation=4,
            expected_writer_generation=7, next_writer_generation=8, correlation_id="corr-deny",
        )
        assert not result.changed
        assert result.reason_code == "authenticated_subject_session_binding_denied"

    inactive = AuthenticatedContext("tenant-1", "subject-2", "inactive")
    adapter.seed_session(inactive, active=False)
    inactive_identity = replace(
        item, id="instance-2", plugin_instance_id="instance-2", scope_id="instance-2",
        subject_id=inactive.subject_id, session_id=inactive.session_id, source_id="source-2",
    )
    adapter.seed_instance(inactive_identity)
    result = adapter.advance_writer(
        identity=inactive_identity, context=inactive, expected_runtime_generation=4,
        expected_writer_generation=7, next_writer_generation=8, correlation_id="corr-inactive",
    )
    assert not result.changed
    persisted = adapter.read_instance(inactive_identity.id)
    assert persisted is not None and persisted["writer_generation"] == 7

    inactive_subject = AuthenticatedContext("tenant-1", "subject-3", "active-session")
    adapter.seed_session(inactive_subject, subject_active=False)
    inactive_subject_identity = replace(
        item, id="instance-3", plugin_instance_id="instance-3", scope_id="instance-3",
        subject_id=inactive_subject.subject_id, session_id=inactive_subject.session_id, source_id="source-3",
    )
    adapter.seed_instance(inactive_subject_identity)
    result = adapter.advance_writer(
        identity=inactive_subject_identity, context=inactive_subject,
        expected_runtime_generation=4, expected_writer_generation=7,
        next_writer_generation=8, correlation_id="corr-inactive-subject",
    )
    assert not result.changed
    persisted = adapter.read_instance(inactive_subject_identity.id)
    assert persisted is not None and persisted["writer_generation"] == 7
    assert all(row["outcome"] == "denied" for row in adapter.list_audits())


def test_import_replay_preserves_mapping_and_checkpoint_and_mutation_conflicts(adapter):
    item, auth = prepare_runtime(adapter)
    adapter.commit_import(
        identity=item, context=auth, expected_checkpoint=None,
        next_checkpoint=checkpoint("source-1", 1), correlation_id="corr-import-first",
    )
    replay_source = replace(item, source_id="source-2")
    digest_source = replace(item, source_id="source-3")
    identity_source = replace(item, id="instance-4", plugin_instance_id="instance-4",
                              scope_id="instance-4", source_id="source-4")
    for source in (replay_source, digest_source, identity_source):
        adapter.seed_mapping(source)
    replayed = adapter.commit_import(
        identity=replay_source, context=auth, expected_checkpoint=checkpoint("source-1", 1),
        next_checkpoint=checkpoint("source-2", 2), correlation_id="corr-replay",
    )
    assert replayed.outcome == "replayed"
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-2", 2)
    original_mappings = adapter.list_mappings()
    assert len(original_mappings) == 4

    changed_digest = replace(digest_source, source_digest=DIGEST_B)
    conflict = adapter.commit_import(
        identity=changed_digest, context=auth,
        expected_checkpoint=checkpoint("source-2", 2),
        next_checkpoint=checkpoint("source-3", 3), correlation_id="corr-conflict-digest",
    )
    assert conflict.outcome == "conflict"
    assert conflict.reason_code == "immutable_source_identity_conflict"
    assert adapter.list_mappings() == original_mappings
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-3", 3)

    changed_identity = replace(identity_source, id="instance-5", plugin_instance_id="instance-5",
                               scope_id="instance-5")
    conflict = adapter.commit_import(
        identity=changed_identity, context=auth,
        expected_checkpoint=checkpoint("source-3", 3),
        next_checkpoint=checkpoint("source-4", 4), correlation_id="corr-conflict-identity",
    )
    assert conflict.outcome == "conflict"
    assert conflict.reason_code == "immutable_source_identity_conflict"
    assert adapter.list_mappings() == original_mappings
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-4", 4)
    conflicts = adapter.list_conflicts()
    assert len(conflicts) == 2
    assert all(row["reason_code"] == "immutable_source_identity_conflict" for row in conflicts)
    assert {row["original_digest"] for row in conflicts} == {DIGEST_A}


def test_resumable_checkpoint_is_snapshot_pinned_ordered_and_cas_protected(adapter):
    item, auth = prepare_runtime(adapter)
    first = adapter.commit_import(
        identity=item, context=auth, expected_checkpoint=None,
        next_checkpoint=checkpoint("source-1", 1), correlation_id="corr-page-1",
    )
    assert first.outcome == "imported"
    resumed = adapter.commit_import(
        identity=replace(item, source_id="source-2"), context=auth,
        expected_checkpoint=checkpoint("source-1", 1),
        next_checkpoint=checkpoint("source-2", 2), correlation_id="corr-page-2",
    )
    assert resumed.outcome == "imported"
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-2", 2)

    stale = adapter.commit_import(
        identity=replace(item, source_id="source-3"), context=auth,
        expected_checkpoint=checkpoint("source-1", 1),
        next_checkpoint=checkpoint("source-3", 2), correlation_id="corr-stale-checkpoint",
    )
    assert stale.outcome == "denied" and stale.reason_code == "stale_checkpoint_zero_rows"

    wrong_keys = (
        SnapshotKey("tenant-1", "definition-1", "snapshot-other", "schema-v1", DIGEST_A),
        SnapshotKey("tenant-1", "definition-1", "snapshot-1", "schema-other", DIGEST_A),
        SnapshotKey("tenant-1", "definition-1", "snapshot-1", "schema-v1", DIGEST_B),
    )
    for wrong_key in wrong_keys:
        wrong_lineage = adapter.commit_import(
            identity=replace(item, source_id="source-3"), context=auth,
            expected_checkpoint=Checkpoint(wrong_key, "source-2", 2),
            next_checkpoint=Checkpoint(wrong_key, "source-3", 3),
            correlation_id="corr-wrong-snapshot-lineage",
        )
        assert wrong_lineage.outcome == "denied"
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-2", 2)


def test_audit_failure_rolls_back_mapping_checkpoint_and_runtime_cas(adapter):
    item, auth = prepare_runtime(adapter)
    adapter.fail_audit = True

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        adapter.commit_import(
            identity=item, context=auth, expected_checkpoint=None,
            next_checkpoint=checkpoint("source-1", 1), correlation_id="corr-import-rollback",
        )
    assert adapter.list_mappings() == []
    assert adapter.load_checkpoint(snapshot_key()) is None
    assert adapter.list_audits() == []

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        adapter.advance_writer(
            identity=item, context=auth, expected_runtime_generation=4,
            expected_writer_generation=7, next_writer_generation=8, correlation_id="corr-cas-rollback",
        )
    persisted = adapter.read_instance(item.id)
    assert persisted is not None
    assert (persisted["runtime_generation"], persisted["writer_generation"]) == (4, 7)
    assert adapter.list_audits() == []


def test_import_subject_session_tenant_mismatch_writes_no_mapping_or_checkpoint(adapter):
    item, _ = prepare_runtime(adapter)
    for invalid in (context(subject_id="subject-other"), context(session_id="session-other"),
                    context(tenant_id="tenant-other"), context(authenticated=False)):
        result = adapter.commit_import(
            identity=item, context=invalid, expected_checkpoint=None,
            next_checkpoint=checkpoint("source-1", 1), correlation_id="corr-import-denied",
        )
        assert result.outcome == "denied"
        assert result.reason_code == "authenticated_subject_session_binding_denied"
        assert adapter.list_mappings() == []
        assert adapter.load_checkpoint(snapshot_key()) is None
    assert all(row["outcome"] == "denied" for row in adapter.list_audits())


def test_fixture_audit_contract_omits_raw_and_authenticated_identity_fields():
    assert set(CasAudit.__dataclass_fields__) == {
        "event", "outcome", "reason_code", "correlation_id", "runtime_generation", "writer_generation",
    }


def test_source_cursor_is_strictly_increasing_and_snapshot_identity_cannot_change(adapter):
    item, auth = prepare_runtime(adapter)
    adapter.commit_import(
        identity=replace(item, source_id="source-2"), context=auth, expected_checkpoint=None,
        next_checkpoint=checkpoint("source-2", 1), correlation_id="corr-order",
    )
    with pytest.raises(ValueError, match="ordered source row"):
        adapter.commit_import(
            identity=replace(item, source_id="source-1"), context=auth,
            expected_checkpoint=checkpoint("source-2", 1),
            next_checkpoint=checkpoint("source-1", 2), correlation_id="corr-backward",
        )
    other = SnapshotKey("tenant-1", "definition-1", "snapshot-other", "schema-v1", DIGEST_A)
    with pytest.raises(ValueError, match="snapshot identity"):
        adapter.commit_import(
            identity=replace(item, source_id="source-3"), context=auth,
            expected_checkpoint=checkpoint("source-2", 1),
            next_checkpoint=Checkpoint(other, "source-3", 2), correlation_id="corr-change-snapshot",
        )
    assert adapter.load_checkpoint(snapshot_key()) == checkpoint("source-2", 1)


def test_max_width_source_id_is_preserved_verbatim(adapter):
    item, auth = prepare_runtime(adapter)
    source_id = "x" * 80
    result = adapter.commit_import(
        identity=replace(item, source_id=source_id), context=auth,
        expected_checkpoint=None, next_checkpoint=checkpoint(source_id, 1),
        correlation_id="corr-source-width",
    )
    assert result.outcome == "imported"
    assert adapter.list_mappings()[0]["source_id"] == source_id


def test_instance_source_identity_is_database_immutable(adapter):
    item, _ = prepare_runtime(adapter)
    with pytest.raises(IntegrityError, match="identity and source fields are immutable"):
        with adapter.engine.begin() as connection:
            connection.execute(update(instances).where(instances.c.id == item.id).values(
                source_id="rewritten-source",
                source_digest=DIGEST_B,
            ))

    persisted = adapter.read_instance(item.id)
    assert persisted is not None
    assert persisted["source_id"] == item.source_id
    assert persisted["source_digest"] == item.source_digest


def test_import_mapping_is_append_only_in_fixture_database(adapter):
    item, auth = prepare_runtime(adapter)
    result = adapter.commit_import(
        identity=item, context=auth, expected_checkpoint=None,
        next_checkpoint=checkpoint("source-1", 1), correlation_id="corr-append-only",
    )
    assert result.outcome == "imported"

    with pytest.raises(IntegrityError, match="source mappings are append-only"):
        with adapter.engine.begin() as connection:
            connection.execute(update(source_mappings).values(source_digest=DIGEST_B))

    with pytest.raises(IntegrityError, match="source mappings are append-only"):
        with adapter.engine.begin() as connection:
            connection.execute(delete(source_mappings))

    assert len(adapter.list_mappings()) == 1
    assert adapter.list_mappings()[0]["source_digest"] == item.source_digest
