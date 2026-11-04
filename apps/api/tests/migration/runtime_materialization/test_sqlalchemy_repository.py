"""MIG-RES-RUNTIME-PERSISTENCE-01 SQL adapter behavior; intentionally unrun."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import MetaData, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from prodavan.application.migration.runtime_materialization.sqlalchemy_repository import (
    PersistenceStatus,
    SqlAlchemyRuntimeRevisionRepository,
)
from prodavan.application.migration.runtime_materialization.state_machine import (
    DependencyEvidence,
    RuntimeAuditRecord,
    canonical_digest,
)
from prodavan.application.migration.runtime_api.contracts import RuntimeApiProjectScope
from prodavan.application.runtime_revision import (
    CapabilityBundle,
    MaterializationEntry,
    MaterializationStatus,
    ProjectRuntimeRevision,
    RevisionState,
)
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    CapabilityBundleRow,
    MaterializationEntryRow,
    ProjectRuntimeRevisionRow,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


#: Metadata owned by this module.
#:
#: ``RuntimeAuditFixtureRow`` is a *test* table. Declaring it on ``Base`` registered it
#: in ``Base.metadata`` process-wide, so any other test that checks the real metadata
#: (for example the RLS coverage report, which must find every canonical table
#: registered) saw a table that does not exist in production and failed -- but only
#: when the whole suite ran together, never when that directory ran alone. A private
#: metadata keeps the fixture invisible to everyone else.
FixtureBase = declarative_base(metadata=MetaData())


class RuntimeAuditFixtureRow(FixtureBase):
    __tablename__ = "runtime_materialization_test_audit"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(nullable=False)


TENANT = "runtime-persist-company"
CABINET = "runtime-persist-cabinet"
PROJECT = "runtime-persist-project"
POD = "runtime-persist-pod"
REVISION = "runtime-persist-revision"
BUNDLE = "runtime-persist-bundle"
IMAGE = "sha256:" + "a" * 64
CONTENT = "sha256:" + "b" * 64
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EVIDENCE = DependencyEvidence(
    bridge_lease_ref="lease://runtime-persist/lease",
    bridge_lease_expires_at=NOW + timedelta(minutes=5),
    bridge_lease_generation=3,
    projected_secret_refs=("vault://runtime-persist/projected",),
    image_digest=IMAGE,
    agent_run_id="agent-run-fixture",
    approval_id="approval-fixture",
    event_id="event-fixture",
    contracts_ready=True,
)
SCOPE = RuntimeApiProjectScope(PROJECT, TENANT, CABINET, POD, "project/runtime-persist")
TABLES = (
    CompanyRow.__table__,
    EmployeeRow.__table__,
    CabinetInstanceRow.__table__,
    AiProviderKeyRow.__table__,
    ProjectRow.__table__,
    ProjectPodRow.__table__,
    CapabilityBundleRow.__table__,
    ProjectRuntimeRevisionRow.__table__,
    MaterializationEntryRow.__table__,
    )


class SessionAuditWriter:
    def __init__(self, *, fail_after_add: bool = False) -> None:
        self.fail_after_add = fail_after_add

    async def append(self, *, session, record: RuntimeAuditRecord) -> None:
        session.add(RuntimeAuditFixtureRow(
            id=f"audit-{record.event}-{record.generation}",
            event=record.event,
            outcome=record.outcome,
            reason_code=record.reason_code,
            resource_digest=record.resource_digest,
            correlation_digest=record.correlation_digest,
            plan_digest=record.plan_digest,
            generation=record.generation,
        ))
        if self.fail_after_add:
            raise RuntimeError("injected audit failure")


@pytest.fixture
async def repository():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        # Real tables come from the production metadata; the fixture table comes from
        # its own, so the two never share a registry.
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
        await connection.run_sync(
            lambda sync: FixtureBase.metadata.create_all(sync, tables=(RuntimeAuditFixtureRow.__table__,))
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(CompanyRow(
            id=TENANT, name="Runtime Test", login_slug="runtime-test", status="active",
        ))
        session.add(EmployeeRow(
            id="runtime-test-employee", login="runtime@test.local", email="runtime@test.local", status="active",
        ))
        session.add(CabinetInstanceRow(
            id=CABINET, name="Runtime Test", schema_name="runtime_test_cabinet", company_id=TENANT,
            status="active",
        ))
        session.add(AiProviderKeyRow(
            id="runtime-test-ai-key", name="Runtime Test", provider="test", api_kind="test",
            owner_scope="company", owner_company_id=TENANT, secret_ref="vault://runtime/test", status="active",
        ))
        session.add(ProjectRow(
            id=PROJECT, company_id=TENANT, cabinet_id=CABINET, name="Runtime Test", slug="runtime-test",
            status="active", visibility_mode="cabinet_shared", workspace_key="runtime-test-workspace",
            container_ref="runtime-test-container",
        ))
        session.add(ProjectPodRow(
            id=POD, project_id=PROJECT, workspace_key="runtime-test-workspace", status="running",
            desired_state="running", hydrate_generation=0,
        ))
    repo = SqlAlchemyRuntimeRevisionRepository(session_factory=factory)
    try:
        yield repo, factory, engine
    finally:
        await engine.dispose()


def make_revision(*, state: RevisionState = RevisionState.PLANNED) -> ProjectRuntimeRevision:
    manifest = {"capabilities": ["agent"], "runtime_image_digest": IMAGE}
    return ProjectRuntimeRevision(
        revision_id=REVISION,
        project_id=PROJECT,
        revision=1,
        bundle=CapabilityBundle(BUNDLE, PROJECT, 1, manifest, canonical_digest(manifest)),
        state=state,
        pod_id=POD,
        hydrate_generation=0,
        bridge_generation=1,
        runtime_image_digest=IMAGE,
        created_by="operator-fixture",
        source_event="runtime-request-1",
    )


def make_entry(*, key: str = "operation-idem-1", digest: str = CONTENT,
               status: MaterializationStatus = MaterializationStatus.PLANNED) -> MaterializationEntry:
    return MaterializationEntry(
        entry_id="runtime-entry-1",
        revision_id=REVISION,
        workspace_path="workspace/runtime/config.yaml",
        kind="runtime_config",
        generation=1,
        status=status,
        content_hash=None,
        source_capability_id="capability-agent-runtime",
        provenance={"source_kind": "capability", "source_id": "capability-agent-runtime", "source_revision": "v1"},
        idempotency_key=key,
        operation_digest=digest,
    )


def audit_record(*, outcome: str, generation: int, event: str) -> RuntimeAuditRecord:
    return RuntimeAuditRecord(
        event=event,
        outcome=outcome,
        reason_code="runtime_apply_failed" if outcome == "failed" else "persistence_fixture",
        resource_digest=canonical_digest(SCOPE.resource_key),
        correlation_digest=canonical_digest("runtime-correlation-1"),
        plan_digest=canonical_digest({"revision": REVISION, "generation": generation}),
        generation=generation,
    )


async def seed_revision(repo: SqlAlchemyRuntimeRevisionRepository) -> ProjectRuntimeRevision:
    result = await repo.create_revision(scope=SCOPE, revision=make_revision())
    assert result.status is PersistenceStatus.WRITTEN
    assert result.revision is not None
    return result.revision


async def claim_generation(repo: SqlAlchemyRuntimeRevisionRepository, *,
                           entries: list[MaterializationEntry], audit=None):
    return await repo.begin_apply(
        scope=SCOPE,
        revision_id=REVISION,
        expected_state=RevisionState.PLANNED,
        expected_generation=0,
        entries=entries,
        evidence=EVIDENCE,
        at=NOW,
        audit_writer=audit or SessionAuditWriter(),
        audit_record=audit_record(outcome="started", generation=1, event="runtime.materialization.started"),
    )


@pytest.mark.asyncio
async def test_revision_and_materialization_round_trip(repository):
    repo, _, _ = repository
    revision = make_revision()
    created = await repo.create_revision(scope=SCOPE, revision=revision)
    assert created.status is PersistenceStatus.WRITTEN
    assert created.revision == revision

    entry = make_entry()
    claimed = await claim_generation(repo, entries=[entry])
    assert claimed.status is PersistenceStatus.WRITTEN
    assert claimed.revision is not None and claimed.revision.state is RevisionState.APPLYING
    assert len(claimed.entries) == 1
    assert claimed.entries[0].provenance == entry.provenance
    assert claimed.entries[0].operation_digest == CONTENT

    restored = await repo.get(scope=SCOPE, revision_id=REVISION)
    listed = await repo.list_entries(scope=SCOPE, revision_id=REVISION)
    assert restored is not None
    assert restored.bundle.manifest == revision.bundle.manifest
    assert restored.bundle.digest == revision.bundle.digest
    assert restored.pod_id == POD and restored.runtime_image_digest == IMAGE
    assert listed == list(claimed.entries)


@pytest.mark.asyncio
async def test_exact_idempotent_plan_replay_and_changed_digest_conflict(repository):
    repo, _, _ = repository
    await seed_revision(repo)
    entry = make_entry()
    first = await claim_generation(repo, entries=[entry])
    assert first.status is PersistenceStatus.WRITTEN

    replay = await repo.begin_apply(
        scope=SCOPE, revision_id=REVISION, expected_state=RevisionState.APPLYING,
        expected_generation=0, entries=[entry], audit_writer=SessionAuditWriter(),
        evidence=EVIDENCE, at=NOW,
        audit_record=audit_record(outcome="started", generation=1, event="runtime.materialization.started"),
    )
    assert replay.status is PersistenceStatus.REPLAY
    assert replay.reason_code == "materialization_exact_plan_replay"
    assert replay.entries == first.entries

    changed = await repo.begin_apply(
        scope=SCOPE, revision_id=REVISION, expected_state=RevisionState.APPLYING,
        expected_generation=0, entries=[make_entry(digest="sha256:" + "c" * 64)],
        evidence=EVIDENCE, at=NOW,
        audit_writer=SessionAuditWriter(),
        audit_record=audit_record(outcome="started", generation=1, event="runtime.materialization.started"),
    )
    assert changed.status is PersistenceStatus.CONFLICT
    assert changed.reason_code == "idempotency_key_plan_conflict"
    assert changed.entries == first.entries


@pytest.mark.asyncio
async def test_stale_generation_cas_is_zero_row_conflict(repository):
    repo, _, _ = repository
    await seed_revision(repo)
    first = await claim_generation(repo, entries=[make_entry()])
    assert first.status is PersistenceStatus.WRITTEN

    stale = await repo.begin_apply(
        scope=SCOPE, revision_id=REVISION, expected_state=RevisionState.APPLYING,
        expected_generation=0, entries=[make_entry(key="operation-idem-stale")],
        evidence=EVIDENCE, at=NOW,
        audit_writer=SessionAuditWriter(),
        audit_record=audit_record(outcome="started", generation=1, event="runtime.materialization.started"),
    )
    assert stale.status is PersistenceStatus.CONFLICT
    assert stale.reason_code == "stale_generation_cas"
    assert stale.affected_rows == 0
    current = await repo.list_entries(scope=SCOPE, revision_id=REVISION)
    assert len(current) == 1 and current[0].generation == 1


@pytest.mark.asyncio
async def test_missing_lease_secret_or_image_evidence_blocks_before_claim(repository):
    repo, factory, _ = repository
    await seed_revision(repo)
    invalid_evidence = (
        DependencyEvidence(),
        DependencyEvidence(
            bridge_lease_ref="lease://runtime-persist/expired",
            bridge_lease_expires_at=NOW - timedelta(seconds=1),
            bridge_lease_generation=3, projected_secret_refs=("vault://runtime-persist/projected",),
            image_digest=IMAGE, agent_run_id="agent-run-fixture", approval_id="approval-fixture",
            event_id="event-fixture", contracts_ready=True,
        ),
        DependencyEvidence(
            bridge_lease_ref="lease://runtime-persist/no-secret",
            bridge_lease_expires_at=NOW + timedelta(minutes=5), bridge_lease_generation=3,
            projected_secret_refs=(), image_digest=IMAGE, agent_run_id="agent-run-fixture",
            approval_id="approval-fixture", event_id="event-fixture", contracts_ready=True,
        ),
        DependencyEvidence(
            bridge_lease_ref="lease://runtime-persist/wrong-image",
            bridge_lease_expires_at=NOW + timedelta(minutes=5), bridge_lease_generation=3,
            projected_secret_refs=("vault://runtime-persist/projected",), image_digest="sha256:" + "c" * 64,
            agent_run_id="agent-run-fixture", approval_id="approval-fixture", event_id="event-fixture",
            contracts_ready=True,
        ),
    )
    for evidence in invalid_evidence:
        blocked = await repo.begin_apply(
            scope=SCOPE, revision_id=REVISION, expected_state=RevisionState.PLANNED,
            expected_generation=0, entries=[make_entry()], evidence=evidence, at=NOW,
            audit_writer=SessionAuditWriter(),
            audit_record=audit_record(outcome="started", generation=1, event="runtime.materialization.started"),
        )
        assert blocked.status is PersistenceStatus.BLOCKED
    async with factory() as session:
        revision = await session.get(ProjectRuntimeRevisionRow, REVISION)
        entries = (await session.scalars(select(MaterializationEntryRow))).all()
    assert revision is not None and revision.state == "planned"
    assert entries == []


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_revision_and_materialization_changes(repository):
    repo, factory, _ = repository
    await seed_revision(repo)
    failed = await claim_generation(repo, entries=[make_entry()], audit=SessionAuditWriter(fail_after_add=True))
    assert failed.status is PersistenceStatus.BLOCKED
    assert failed.reason_code == "audit_write_failed"

    async with factory() as session:
        revision = await session.get(ProjectRuntimeRevisionRow, REVISION)
        entries = (await session.scalars(select(MaterializationEntryRow))).all()
        audits = (await session.scalars(select(RuntimeAuditFixtureRow))).all()
    assert revision is not None and revision.state == RevisionState.PLANNED.value
    assert entries == [] and audits == []


@pytest.mark.asyncio
async def test_partial_apply_is_persisted_failed_and_never_ready(repository):
    repo, factory, _ = repository
    await seed_revision(repo)
    first = make_entry()
    second = MaterializationEntry(
        entry_id="runtime-entry-2", revision_id=REVISION,
        workspace_path="workspace/runtime/bridge.yaml", kind="runtime_config", generation=1,
        status=MaterializationStatus.PLANNED, provenance={"source_kind": "capability", "source_id": "bridge"},
        idempotency_key="operation-idem-2", operation_digest="sha256:" + "c" * 64,
    )
    started = await claim_generation(repo, entries=[first, second])
    assert started.status is PersistenceStatus.WRITTEN
    applied_one = MaterializationEntry(
        entry_id=first.entry_id, revision_id=REVISION, workspace_path=first.workspace_path,
        kind=first.kind, generation=1, status=MaterializationStatus.APPLIED,
        content_hash=CONTENT, source_capability_id=first.source_capability_id,
        provenance=first.provenance, idempotency_key=first.idempotency_key,
        operation_digest=first.operation_digest,
    )
    failed_two = MaterializationEntry(
        entry_id=second.entry_id, revision_id=REVISION, workspace_path=second.workspace_path,
        kind=second.kind, generation=1, status=MaterializationStatus.FAILED,
        provenance=second.provenance, idempotency_key=second.idempotency_key,
        operation_digest=second.operation_digest, error="runtime_apply_failed",
    )
    finished = await repo.finish_failed(
        scope=SCOPE, revision_id=REVISION, expected_generation=1,
        failure_code="runtime_apply_failed", diagnostics={"reason": "runtime_apply_failed", "completed": 1},
        entry_updates=[applied_one, failed_two], audit_writer=SessionAuditWriter(),
        audit_record=audit_record(outcome="failed", generation=1, event="runtime.materialization.finished"),
    )
    assert finished.status is PersistenceStatus.WRITTEN
    assert finished.revision is not None and finished.revision.state is RevisionState.FAILED
    assert {entry.status for entry in finished.entries} == {MaterializationStatus.APPLIED, MaterializationStatus.FAILED}
    assert all(entry.generation == 1 for entry in finished.entries)

    async with factory() as session:
        stored = await session.get(ProjectRuntimeRevisionRow, REVISION)
    assert stored is not None and stored.state == "failed" and stored.state != "ready"
    assert not hasattr(repo, "mark_ready")
