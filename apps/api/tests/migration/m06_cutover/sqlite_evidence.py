"""SQLite acceptance harness for the cutover ports (test-owned, never production).

Why a mirror instead of ``Base.metadata.create_all``: four of the five evidence tables are
declared with PostgreSQL ``JSONB``, which the SQLite dialect cannot render, and the cutover
ledger does not exist in ``Base.metadata`` at all. The harness therefore copies each model's
columns (``JSONB`` -> ``JSON``) and renders the two cutover tables from
:func:`~prodavan.application.migration.m06_cutover.ports.cutover_schema_ddl` so the fixture
cannot drift from the advertised specification.

The mirrored tables are only containers: every read in the tests goes through the real ORM
model class, which is what proves the adapters agree with the registered models.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prodavan.application.migration.m06_cutover.ports import (
    CUTOVER_LEDGER_TABLE,
    CUTOVER_REQUIRED_SCHEMA,
    CUTOVER_SOURCE_TAIL_TABLE,
    cutover_schema_ddl,
)
from prodavan.infrastructure.persistence.models.canonical_grants import (
    CanonicalGrantWriterEpochRow,
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
)

MIRRORED_MODELS = (
    M04ScopePromotionEvidenceRow,
    M04EDefinitionVersionPromotionEvidenceRow,
    M05PluginInstancePromotionEvidenceRow,
    M06CanonicalGrantPromotionEvidenceRow,
    CanonicalGrantWriterEpochRow,
)


def mirror_table(model: Any, metadata: sa.MetaData) -> sa.Table:
    """Copy one registered model's columns, rendering ``JSONB`` as ``JSON``."""

    columns = []
    for column in model.__table__.columns:
        column_type = sa.JSON() if isinstance(column.type, JSONB) else column.type
        columns.append(sa.Column(
            column.name,
            column_type,
            primary_key=column.primary_key,
            nullable=not column.primary_key,
        ))
    return sa.Table(model.__tablename__, metadata, *columns)


def dialect_neutral_ddl() -> tuple[str, ...]:
    """``CREATE TABLE`` statements for the cutover ledger/tail, SQLite-flavoured.

    The checks are omitted because SQLite has no ``~`` regex operator; the SQLite rendering
    keeps the primary keys and unique keys, which are the CAS and idempotency contracts the
    repository depends on. ``cutover_schema_ddl()`` (PostgreSQL) keeps the checks too.
    """

    statements: list[str] = []
    for spec in CUTOVER_REQUIRED_SCHEMA:
        lines = [
            f"    {column.name} {column.sql_type}" + ("" if column.nullable else " NOT NULL")
            for column in spec.columns
        ]
        lines.append(f"    PRIMARY KEY ({', '.join(spec.primary_key)})")
        for key in spec.unique:
            lines.append(f"    UNIQUE ({', '.join(key)})")
        statements.append(f"CREATE TABLE {spec.name} (\n" + ",\n".join(lines) + "\n)")
        for index, columns in enumerate(spec.indexes):
            statements.append(
                f"CREATE INDEX ix_{spec.name}_{index} ON {spec.name} ({', '.join(columns)})"
            )
    return tuple(statements)


class EvidenceDatabase:
    """One ephemeral SQLite database with the evidence mirror and the cutover ledger."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {
            model.__tablename__: mirror_table(model, self.metadata)
            for model in MIRRORED_MODELS
        }
        self._created: list[str] = []
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create(self, *, with_cutover_tables: bool = True,
                     cutover_tables: Sequence[str] | None = None) -> "EvidenceDatabase":
        async with self.engine.begin() as connection:
            await connection.run_sync(self.metadata.create_all)
            names = (
                list(cutover_tables)
                if cutover_tables is not None
                else ([CUTOVER_LEDGER_TABLE, CUTOVER_SOURCE_TAIL_TABLE]
                      if with_cutover_tables else [])
            )
            for statement in dialect_neutral_ddl():
                table_name = statement.split()[2]
                if table_name in names:
                    await connection.exec_driver_sql(statement)
                    self._created.append(table_name)
        return self

    async def close(self) -> None:
        await self.engine.dispose()

    @property
    def created_cutover_tables(self) -> tuple[str, ...]:
        return tuple(self._created)

    async def insert(self, table_name: str, **values: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(self.tables[table_name].insert().values(**values))

    async def update(self, table_name: str, *, where: Mapping[str, Any], **values: Any) -> None:
        table = self.tables[table_name]
        predicate = sa.and_(*(table.c[key] == value for key, value in where.items()))
        async with self.engine.begin() as connection:
            await connection.execute(table.update().where(predicate).values(**values))

    async def execute(self, statement: str, **params: Any) -> None:
        """Run one statement against the fixture database (seeding the cutover tail)."""

        async with self.engine.begin() as connection:
            await connection.execute(sa.text(statement), params)

    async def count(self, table_name: str) -> int:
        table = self.tables[table_name]
        async with self.engine.connect() as connection:
            return int((await connection.scalar(sa.select(sa.func.count()).select_from(table))) or 0)

    async def rows(self, table_name: str) -> list[dict[str, Any]]:
        table = self.tables[table_name]
        async with self.engine.connect() as connection:
            result = await connection.execute(sa.select(table).order_by(table.c.id))
            return [dict(row) for row in result.mappings()]


# ---------------------------------------------------------------------------
# Seeding helpers for one partition
# ---------------------------------------------------------------------------


def scope_evidence_values(
    *,
    evidence_id: str,
    tenant_id: str,
    canonical_digest: str,
    source_snapshot_digest: str,
    writer_generation: int,
    evidence_digest: str,
    outcome: str = "ready",
    canonical_positive_writes: bool = False,
    gate_evidence: Mapping[str, Any] | None = None,
    scope_id: str | None = None,
) -> dict[str, Any]:
    """Row values for ``m04_scope_promotion_evidence`` (the M04 stage row).

    ``evidence_digest`` is the digest of this row's own evidence envelope; it is written
    into the envelope so the verifier can prove the row records the digest it is cited by.
    """

    envelope = {"evidence_id": evidence_id, "evidence_digest": evidence_digest}
    envelope.update(dict(gate_evidence or {}))
    return {
        "id": evidence_id,
        "tenant_id": tenant_id,
        "scope_kind": "tenant",
        "scope_id": scope_id or tenant_id,
        "cabinet_id": None,
        "project_id": None,
        "scope_registry_id": f"sr-{tenant_id}",
        "revision_id": evidence_id,
        "previous_revision_id": None,
        "outcome": outcome,
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_digest": source_snapshot_digest,
        "canonical_digest": canonical_digest,
        "canonical_schema_version": "scope-registry.v1",
        "checkpoint_digest": "sha256:" + "7" * 64,
        "gate_evidence_digest": "sha256:" + "8" * 64,
        "rls_policy_digest": "sha256:" + "6" * 64,
        "rls_security_review_id": "review-1",
        "rls_database_role": "migration_role",
        "gate_evidence": envelope,
        "reason_code": None,
        "writer_generation": writer_generation,
        "correlation_id": "corr-m04",
        "actor_id": "operator",
        "canonical_positive_writes": canonical_positive_writes,
        "recorded_at": datetime(2026, 11, 1, 13, 0, tzinfo=UTC),
    }


def m04e_evidence_values(
    *,
    evidence_id: str,
    tenant_id: str,
    definition_id: str,
    canonical_digest: str,
    source_snapshot_digest: str,
    evidence_digest: str,
    parent_id: str | None,
    parent_digest: str | None,
    outcome: str = "ready",
    canonical_positive_writes: bool = False,
) -> dict[str, Any]:
    """Row values for ``m04e_definition_version_promotion_evidence``.

    The table's ``outcome`` check admits only blocked/quarantined in PostgreSQL, so this
    fixture writes the true status into the envelope as the live writer does; the adapter
    reads either form.
    """

    return {
        "id": evidence_id,
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "revision_id": evidence_id,
        "outcome": "blocked",
        "source_snapshot_id": "snapshot-1",
        "source_digest": source_snapshot_digest,
        "target_digest": canonical_digest,
        "valid_count": 1,
        "archived_count": 0,
        "quarantined_count": 0,
        "current_version_resolution_digest": "sha256:" + "5" * 64,
        "artifact_equality_digest": "sha256:" + "4" * 64,
        "gate_evidence": {
            "schema": "m04e-m05-handoff-marker.v1",
            "status": outcome,
            "evidence_id": evidence_id,
            "evidence_digest": evidence_digest,
            "m04": {"evidence_id": parent_id, "evidence_digest": parent_digest},
        },
        "canonical_positive_writes": canonical_positive_writes,
        "reviewer": "operator",
        "previous_revision_id": None,
        "recorded_at": datetime(2026, 11, 1, 13, 5, tzinfo=UTC),
        "notes": "redacted M04E fixture row",
    }


def m05_evidence_values(
    *,
    evidence_id: str,
    tenant_id: str,
    definition_id: str,
    canonical_digest: str,
    source_snapshot_digest: str,
    evidence_digest: str,
    parent_id: str | None,
    parent_digest: str | None,
    writer_generation: int,
    outcome: str = "ready",
    canonical_positive_writes: bool = False,
) -> dict[str, Any]:
    """Row values for ``m05_plugin_instance_promotion_evidence`` (the M05 stage row)."""

    return {
        "id": evidence_id,
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "partition_id": f"{tenant_id}:{definition_id}",
        "revision_id": evidence_id,
        "outcome": "blocked",
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "m05.v1",
        "source_digest": source_snapshot_digest,
        "target_digest": canonical_digest,
        "composite_key_evidence": {},
        "reciprocal_fk_evidence": {"legacy_writer_fenced": True},
        "quarantine_counts": {"quarantined": 0},
        "lifecycle_distribution": {"imported": 1},
        "dependency_digests": {},
        "checkpoint": "checkpoint-1",
        "gate_evidence": {
            "schema": "m05-m06-promotion-record.v1",
            "status": outcome,
            "evidence_id": evidence_id,
            "evidence_digest": evidence_digest,
            "m04e": {"evidence_id": parent_id, "evidence_digest": parent_digest},
        },
        "accepted_count": 1,
        "quarantined_count": 0,
        "archived_count": 0,
        "canonical_positive_writes": canonical_positive_writes,
        "writer_generation": writer_generation,
        "fencing_generation": writer_generation,
        "correlation_id": "corr-m05",
        "actor_id": "operator",
        "decision_owner": "legacy",
        "reviewer": None,
        "previous_revision_id": None,
        "recorded_at": datetime(2026, 11, 1, 13, 10, tzinfo=UTC),
        "rollback_gate": "legacy owner remains authoritative; preserve evidence and repair forward",
    }


def m06_evidence_values(
    *,
    evidence_id: str,
    tenant_id: str,
    definition_id: str,
    canonical_digest: str,
    source_snapshot_digest: str,
    evidence_digest: str,
    writer_epoch: int,
    references: Sequence[tuple[str, str | None]],
    extra: Mapping[str, Any] | None = None,
    outcome: str = "ready",
    canonical_positive_writes: bool = False,
) -> dict[str, Any]:
    """Row values for ``m06_canonical_grant_promotion_evidence`` (the M06 gate row).

    ``references`` are the ``(evidence_id, evidence_digest)`` pairs the row must physically
    record: the M05 parent, the legacy owner marker, the drain receipt, the final-delta
    receipt and the parity report digest.
    """

    envelope: dict[str, Any] = {
        "schema": "m06-cutover-gate.v1",
        "status": outcome,
        "evidence_id": evidence_id,
        "evidence_digest": evidence_digest,
        "lineage": [
            {"evidence_id": evidence_id_value, "evidence_digest": evidence_digest_value}
            for evidence_id_value, evidence_digest_value in references
        ],
    }
    envelope.update(extra or {})
    return {
        "id": evidence_id,
        "tenant_id": tenant_id,
        "definition_id": definition_id,
        "partition_id": f"{tenant_id}:{definition_id}",
        "revision_id": evidence_id,
        "outcome": outcome,
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "m06.v1",
        "source_digest": source_snapshot_digest,
        "target_digest": canonical_digest,
        "dependency_digests": {},
        "constraint_inventory": {},
        "index_inventory": {},
        "policy_inventory": {},
        "trusted_role_epoch_evidence": {},
        "gate_evidence": envelope,
        "canonical_positive_writes": canonical_positive_writes,
        "writer_epoch": writer_epoch,
        "correlation_id": "corr-m06",
        "decision_owner": "legacy",
        "reviewer": "operator",
        "recorded_at": datetime(2026, 11, 1, 13, 20, tzinfo=UTC),
        "rollback_gate": "legacy remains authoritative; preserve evidence and repair forward",
    }


def fence_values(
    *, fence_id: str, tenant_id: str, definition_id: str, writer_epoch: int,
    scope_kind: str = "plugin_instance", scope_id: str = "instance-1",
) -> dict[str, Any]:
    """Row values for ``dynamic_app_capability_writer_epochs``.

    ``partition_id`` follows the registered check constraint
    ``tenant:definition:scope_kind:scope_id``.
    """

    return {
        "id": fence_id,
        "partition_id": f"{tenant_id}:{definition_id}:{scope_kind}:{scope_id}",
        "tenant_id": tenant_id,
        "app_definition_id": definition_id,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "writer_epoch": writer_epoch,
        "advanced_by": "operator",
        "correlation_id": "corr-fence",
        "advanced_at": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    }
