"""Test-owned SQLite adapter for M05 database acceptance contracts.

This adapter is an executable acceptance fixture only. It is not production
persistence, does not implement PostgreSQL RLS, and must never be wired into the
application. Its schema is created in an ephemeral SQLite database by tests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine


metadata = MetaData()

instances = Table(
    "test_m05_plugin_instances",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("plugin_instance_id", String(40), nullable=False),
    Column("definition_id", String(80), nullable=False),
    Column("version_id", String(80), nullable=False),
    Column("tenant_id", String(40), nullable=False),
    Column("scope_kind", String(32), nullable=False),
    Column("scope_id", String(40), nullable=False),
    Column("scope_registry_id", String(40), nullable=False),
    Column("cabinet_id", String(40)),
    Column("project_id", String(40)),
    Column("artifact_digest", String(71), nullable=False),
    Column("subject_id", String(160), nullable=False),
    Column("session_id", String(160), nullable=False),
    Column("lifecycle", String(16), nullable=False),
    Column("runtime_generation", Integer, nullable=False),
    Column("writer_generation", Integer, nullable=False),
    Column("source_kind", String(80), nullable=False),
    Column("source_id", String(80), nullable=False),
    Column("source_schema_version", String(40), nullable=False),
    Column("source_digest", String(71), nullable=False),
    UniqueConstraint(
        "plugin_instance_id", "definition_id", "version_id", "tenant_id", "scope_kind", "scope_id",
        name="uq_test_m05_exact_instance_identity",
    ),
    UniqueConstraint(
        "source_kind", "source_id", "source_schema_version", name="uq_test_m05_source_identity",
    ),
    CheckConstraint("runtime_generation >= 0 AND writer_generation >= 0"),
    CheckConstraint("id = plugin_instance_id"),
    CheckConstraint(
        "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id "
        "AND (project_id IS NULL OR cabinet_id IS NOT NULL)"
    ),
    CheckConstraint("length(source_id) <= 80"),
)

source_mappings = Table(
    "test_m05_import_mappings",
    metadata,
    Column("tenant_id", String(40), primary_key=True),
    Column("definition_id", String(80), primary_key=True),
    Column("source_kind", String(80), primary_key=True),
    Column("source_id", String(80), primary_key=True),
    Column("source_schema_version", String(40), primary_key=True),
    Column("source_digest", String(71), nullable=False),
    Column("identity_json", String, nullable=False),
    CheckConstraint("length(source_id) <= 80"),
)

import_conflicts = Table(
    "test_m05_import_conflicts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(40), nullable=False),
    Column("definition_id", String(80), nullable=False),
    Column("source_kind", String(80), nullable=False),
    Column("source_id", String(80), nullable=False),
    Column("source_schema_version", String(40), nullable=False),
    Column("original_digest", String(71), nullable=False),
    Column("observed_digest", String(71), nullable=False),
    Column("reason_code", String(80), nullable=False),
    Column("correlation_id", String(128), nullable=False),
)

checkpoints = Table(
    "test_m05_import_checkpoints",
    metadata,
    Column("tenant_id", String(40), primary_key=True),
    Column("definition_id", String(80), primary_key=True),
    Column("source_snapshot_id", String(200), nullable=False),
    Column("source_schema_version", String(40), nullable=False),
    Column("source_snapshot_digest", String(71), nullable=False),
    Column("after_source_id", String(80)),
    Column("sequence", Integer, nullable=False),
    CheckConstraint("sequence >= 0"),
    CheckConstraint("length(after_source_id) <= 80"),
)

subjects = Table(
    "test_m05_subjects",
    metadata,
    Column("tenant_id", String(40), primary_key=True),
    Column("subject_id", String(160), primary_key=True),
    Column("active", Integer, nullable=False),
)

subject_sessions = Table(
    "test_m05_subject_sessions",
    metadata,
    Column("tenant_id", String(40), primary_key=True),
    Column("subject_id", String(160), primary_key=True),
    Column("session_id", String(160), primary_key=True),
    Column("active", Integer, nullable=False),
)

audits = Table(
    "test_m05_runtime_audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event", String(80), nullable=False),
    Column("outcome", String(24), nullable=False),
    Column("reason_code", String(80), nullable=False),
    Column("correlation_id", String(128), nullable=False),
    Column("runtime_generation", Integer),
    Column("writer_generation", Integer),
)


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    """Complete registry identity plus its verbatim immutable source key."""

    id: str
    plugin_instance_id: str
    definition_id: str
    version_id: str
    tenant_id: str
    scope_kind: str
    scope_id: str
    scope_registry_id: str
    cabinet_id: str | None
    project_id: str | None
    artifact_digest: str
    subject_id: str
    session_id: str
    source_kind: str
    source_id: str
    source_schema_version: str
    source_digest: str

    def registry_values(self) -> dict[str, Any]:
        return asdict(self)

    def source_key(self) -> tuple[str, str, str, str, str]:
        return (self.tenant_id, self.definition_id, self.source_kind, self.source_id,
                self.source_schema_version)


@dataclass(frozen=True, slots=True)
class AuthenticatedContext:
    tenant_id: str
    subject_id: str
    session_id: str
    authenticated: bool = True


@dataclass(frozen=True, slots=True)
class SnapshotKey:
    tenant_id: str
    definition_id: str
    source_snapshot_id: str
    source_schema_version: str
    source_snapshot_digest: str

    def values(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    key: SnapshotKey
    after_source_id: str | None
    sequence: int


@dataclass(frozen=True, slots=True)
class CasAudit:
    event: str
    outcome: str
    reason_code: str
    correlation_id: str
    runtime_generation: int | None
    writer_generation: int | None


@dataclass(frozen=True, slots=True)
class CasResult:
    changed: bool
    reason_code: str
    runtime_generation: int | None
    writer_generation: int | None


@dataclass(frozen=True, slots=True)
class ImportResult:
    outcome: str
    reason_code: str


class CheckpointConflict(RuntimeError):
    pass


class SourceIdentityConflict(RuntimeError):
    pass


class SQLiteM05AcceptanceAdapter:
    """Small SQLAlchemy fake for transaction/CAS acceptance, not runtime use."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.fail_audit = False

    def create_schema(self) -> None:
        """Create only the disposable fixture schema when its tests are explicitly run."""
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TRIGGER test_m05_instance_identity_immutable
                BEFORE UPDATE OF id, plugin_instance_id, definition_id, version_id,
                    tenant_id, scope_kind, scope_id, scope_registry_id, cabinet_id, project_id,
                    artifact_digest, subject_id, session_id, source_kind, source_id,
                    source_schema_version, source_digest
                ON test_m05_plugin_instances
                BEGIN
                    SELECT RAISE(ABORT, 'test M05 identity and source fields are immutable');
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER test_m05_mapping_append_only_update
                BEFORE UPDATE ON test_m05_import_mappings
                BEGIN
                    SELECT RAISE(ABORT, 'test M05 source mappings are append-only');
                END
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER test_m05_mapping_append_only_delete
                BEFORE DELETE ON test_m05_import_mappings
                BEGIN
                    SELECT RAISE(ABORT, 'test M05 source mappings are append-only');
                END
                """
            )

    def seed_session(
        self,
        context: AuthenticatedContext,
        *,
        active: bool = True,
        subject_active: bool = True,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(insert(subjects).values(
                tenant_id=context.tenant_id,
                subject_id=context.subject_id,
                active=int(subject_active),
            ))
            connection.execute(insert(subject_sessions).values(
                tenant_id=context.tenant_id,
                subject_id=context.subject_id,
                session_id=context.session_id,
                active=int(active),
            ))

    def seed_instance(
        self,
        identity: InstanceIdentity,
        *,
        lifecycle: str = "published",
        runtime_generation: int = 4,
        writer_generation: int = 7,
    ) -> None:
        values = identity.registry_values() | {
            "lifecycle": lifecycle,
            "runtime_generation": runtime_generation,
            "writer_generation": writer_generation,
        }
        with self.engine.begin() as connection:
            connection.execute(insert(instances).values(**values))

    def seed_mapping(self, identity: InstanceIdentity) -> None:
        """Seed pre-existing evidence for replay/conflict acceptance cases."""
        with self.engine.begin() as connection:
            connection.execute(insert(source_mappings).values(
                **self._mapping_values(identity),
            ))

    @staticmethod
    def _mapping_values(identity: InstanceIdentity) -> dict[str, Any]:
        return {
            "tenant_id": identity.tenant_id,
            "definition_id": identity.definition_id,
            "source_kind": identity.source_kind,
            "source_id": identity.source_id,
            "source_schema_version": identity.source_schema_version,
            "source_digest": identity.source_digest,
            "identity_json": json.dumps(identity.registry_values(), sort_keys=True, separators=(",", ":")),
        }

    @staticmethod
    def _checkpoint_values(checkpoint: Checkpoint) -> dict[str, Any]:
        return checkpoint.key.values() | {
            "after_source_id": checkpoint.after_source_id,
            "sequence": checkpoint.sequence,
        }

    @staticmethod
    def _audit_values(record: CasAudit) -> dict[str, Any]:
        return asdict(record)

    def _append_audit(self, connection: Connection, record: CasAudit) -> None:
        if self.fail_audit:
            raise RuntimeError("acceptance audit sink unavailable")
        connection.execute(insert(audits).values(**self._audit_values(record)))

    @staticmethod
    def _identity_predicate(identity: InstanceIdentity):
        values = identity.registry_values()
        return and_(*(instances.c[name] == value for name, value in values.items()))

    @staticmethod
    def _binding_matches(context: AuthenticatedContext, identity: InstanceIdentity) -> bool:
        return bool(
            context.authenticated
            and context.tenant_id == identity.tenant_id
            and context.subject_id == identity.subject_id
            and context.session_id == identity.session_id
        )

    def _active_session_exists(self, connection: Connection, context: AuthenticatedContext) -> bool:
        subject = connection.execute(select(subjects.c.active).where(and_(
            subjects.c.tenant_id == context.tenant_id,
            subjects.c.subject_id == context.subject_id,
        ))).one_or_none()
        session = connection.execute(select(subject_sessions.c.active).where(and_(
            subject_sessions.c.tenant_id == context.tenant_id,
            subject_sessions.c.subject_id == context.subject_id,
            subject_sessions.c.session_id == context.session_id,
        ))).one_or_none()
        return bool(subject is not None and subject.active == 1
                    and session is not None and session.active == 1)

    def advance_writer(
        self,
        *,
        identity: InstanceIdentity,
        context: AuthenticatedContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_writer_generation: int,
        correlation_id: str,
    ) -> CasResult:
        if next_writer_generation != expected_writer_generation + 1:
            raise ValueError("writer generation must advance exactly one")
        return self._compare_and_set(
            identity=identity,
            context=context,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation,
            next_runtime_generation=expected_runtime_generation,
            next_writer_generation=next_writer_generation,
            event="plugin_instance.writer_generation.advance",
            correlation_id=correlation_id,
        )

    def advance_runtime(
        self,
        *,
        identity: InstanceIdentity,
        context: AuthenticatedContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_runtime_generation: int,
        next_writer_generation: int,
        correlation_id: str,
    ) -> CasResult:
        if (next_runtime_generation != expected_runtime_generation + 1
                or next_writer_generation != expected_writer_generation + 1):
            raise ValueError("runtime restart must advance both generations exactly one")
        return self._compare_and_set(
            identity=identity,
            context=context,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation,
            next_runtime_generation=next_runtime_generation,
            next_writer_generation=next_writer_generation,
            event="plugin_instance.runtime_generation.advance",
            correlation_id=correlation_id,
        )

    def _compare_and_set(
        self,
        *,
        identity: InstanceIdentity,
        context: AuthenticatedContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_runtime_generation: int,
        next_writer_generation: int,
        event: str,
        correlation_id: str,
    ) -> CasResult:
        with self.engine.begin() as connection:
            binding_ok = self._binding_matches(context, identity) and self._active_session_exists(connection, context)
            if not binding_ok:
                reason = "authenticated_subject_session_binding_denied"
                self._append_audit(connection, CasAudit(
                    event, "denied", reason, correlation_id,
                    expected_runtime_generation, expected_writer_generation,
                ))
                return CasResult(False, reason, None, None)

            predicate = and_(
                self._identity_predicate(identity),
                instances.c.tenant_id == context.tenant_id,
                instances.c.lifecycle == "published",
                instances.c.runtime_generation == expected_runtime_generation,
                instances.c.writer_generation == expected_writer_generation,
            )
            result = connection.execute(update(instances).where(predicate).values(
                runtime_generation=next_runtime_generation,
                writer_generation=next_writer_generation,
            ))
            if result.rowcount != 1:
                reason = "stale_generation_zero_rows"
                self._append_audit(connection, CasAudit(
                    event, "stale-generation", reason, correlation_id,
                    expected_runtime_generation, expected_writer_generation,
                ))
                return CasResult(False, reason, None, None)

            row = connection.execute(select(
                instances.c.runtime_generation, instances.c.writer_generation,
            ).where(instances.c.id == identity.id)).one()
            self._append_audit(connection, CasAudit(
                event, "advanced", "cas_updated_one_row", correlation_id,
                row.runtime_generation, row.writer_generation,
            ))
            return CasResult(True, "cas_updated_one_row", row.runtime_generation, row.writer_generation)

    def commit_import(
        self,
        *,
        identity: InstanceIdentity,
        context: AuthenticatedContext,
        expected_checkpoint: Checkpoint | None,
        next_checkpoint: Checkpoint,
        correlation_id: str,
    ) -> ImportResult:
        if (next_checkpoint.key.tenant_id, next_checkpoint.key.definition_id) != (
            identity.tenant_id, identity.definition_id,
        ):
            raise ValueError("checkpoint partition must match the exact instance identity")
        if next_checkpoint.key != (expected_checkpoint.key if expected_checkpoint else next_checkpoint.key):
            raise ValueError("checkpoint snapshot identity cannot change within a page")
        expected_sequence = expected_checkpoint.sequence + 1 if expected_checkpoint else 1
        previous_source_id = expected_checkpoint.after_source_id if expected_checkpoint else None
        if (next_checkpoint.sequence != expected_sequence
                or next_checkpoint.after_source_id != identity.source_id
                or (previous_source_id is not None and identity.source_id <= previous_source_id)):
            raise ValueError("next checkpoint must advance one ordered source row")

        with self.engine.begin() as connection:
            if not (self._binding_matches(context, identity)
                    and self._active_session_exists(connection, context)):
                reason = "authenticated_subject_session_binding_denied"
                self._append_audit(connection, CasAudit(
                    "plugin_instance.import", "denied", reason, correlation_id, None, None,
                ))
                return ImportResult("denied", reason)

            current = self._load_checkpoint(connection, next_checkpoint.key)
            if current != expected_checkpoint:
                reason = "stale_checkpoint_zero_rows"
                self._append_audit(connection, CasAudit(
                    "plugin_instance.import", "stale-generation", reason, correlation_id, None, None,
                ))
                return ImportResult("denied", reason)

            mapping_values = self._mapping_values(identity)
            source_key_predicate = and_(
                source_mappings.c.tenant_id == identity.tenant_id,
                source_mappings.c.definition_id == identity.definition_id,
                source_mappings.c.source_kind == identity.source_kind,
                source_mappings.c.source_id == identity.source_id,
                source_mappings.c.source_schema_version == identity.source_schema_version,
            )
            prior = connection.execute(select(source_mappings).where(source_key_predicate)).mappings().one_or_none()
            conflict = prior is not None and (
                prior["source_digest"] != identity.source_digest
                or prior["identity_json"] != mapping_values["identity_json"]
            )

            if conflict:
                assert prior is not None
                reason = "immutable_source_identity_conflict"
                connection.execute(insert(import_conflicts).values(
                    tenant_id=identity.tenant_id,
                    definition_id=identity.definition_id,
                    source_kind=identity.source_kind,
                    source_id=identity.source_id,
                    source_schema_version=identity.source_schema_version,
                    original_digest=prior["source_digest"],
                    observed_digest=identity.source_digest,
                    reason_code=reason,
                    correlation_id=correlation_id,
                ))
                outcome = "conflict"
            elif prior is not None:
                reason = "immutable_mapping_replay"
                outcome = "replayed"
            else:
                connection.execute(insert(source_mappings).values(**mapping_values))
                reason = "mapping_checkpoint_committed"
                outcome = "imported"

            next_values = self._checkpoint_values(next_checkpoint)
            if current is None:
                connection.execute(insert(checkpoints).values(**next_values))
            else:
                expected_values = self._checkpoint_values(current)
                cas_predicate = and_(*(checkpoints.c[name] == value
                                       for name, value in expected_values.items()))
                updated = connection.execute(update(checkpoints).where(cas_predicate).values(
                    after_source_id=next_checkpoint.after_source_id,
                    sequence=next_checkpoint.sequence,
                ))
                if updated.rowcount != 1:
                    raise CheckpointConflict("durable checkpoint compare-and-set affected zero rows")

            self._append_audit(connection, CasAudit(
                "plugin_instance.import", outcome, reason, correlation_id,
                None, None,
            ))
            return ImportResult(outcome, reason)

    @staticmethod
    def _load_checkpoint(connection: Connection, key: SnapshotKey) -> Checkpoint | None:
        row = connection.execute(select(checkpoints).where(and_(
            checkpoints.c.tenant_id == key.tenant_id,
            checkpoints.c.definition_id == key.definition_id,
        ))).mappings().one_or_none()
        if row is None:
            return None
        stored_key = SnapshotKey(
            row["tenant_id"], row["definition_id"], row["source_snapshot_id"],
            row["source_schema_version"], row["source_snapshot_digest"],
        )
        return Checkpoint(stored_key, row["after_source_id"], row["sequence"])

    def load_checkpoint(self, key: SnapshotKey) -> Checkpoint | None:
        with self.engine.connect() as connection:
            return self._load_checkpoint(connection, key)

    def list_mappings(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(source_mappings)).mappings()]

    def list_conflicts(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                select(import_conflicts).order_by(import_conflicts.c.id)
            ).mappings()]

    def read_instance(self, instance_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(instances).where(instances.c.id == instance_id)).mappings().one_or_none()
            return dict(row) if row is not None else None

    def list_audits(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(audits).order_by(audits.c.id)).mappings()]
