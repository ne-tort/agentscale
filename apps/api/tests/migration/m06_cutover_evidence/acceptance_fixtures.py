"""Fixtures for the M06 cutover DDL contract (task ``MIG-DDL-CUTOVER-01``).

Nothing here connects to a database: revision ``2026110316`` is replayed in-process
against a recording ``alembic.op`` stand-in, so the objects it *declares* are compared
with the SQLAlchemy metadata that is the schema source of truth
(``models/m06_cutover_evidence.py``) and with the port's reviewed specification
(``application/migration/m06_cutover/ports.py::CUTOVER_REQUIRED_SCHEMA``).

The chain helpers are deliberately independent of the other migration test packages:
the revision graph is derived from ``alembic/versions`` with :mod:`ast`, so a new
revision cannot silently change what "the chain" means for this contract.
"""

from __future__ import annotations

import ast
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from prodavan.application.migration.m06_cutover.cutover import (
    CutoverAction,
    CutoverState,
    PartitionCutover,
    PartitionKey,
    PromotionRecord,
    WriterLease,
    _lineage_payload_digest,
)
from prodavan.application.migration.m06_cutover.ports import (
    CUTOVER_LEDGER_TABLE,
    CUTOVER_SOURCE_TAIL_TABLE,
)
from prodavan.infrastructure.persistence.models.m06_cutover_evidence import (
    M06PartitionCutoverEvidenceRow,
    M06PartitionCutoverSourceTailRow,
)

#: ``apps/api`` — the first directory on the path that has no ``__init__.py``.
API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"
REVISION_FILE = VERSIONS / "2026110316_m06_cutover_evidence.py"

REVISION_ID = "2026110316"
DOWN_REVISION_ID = "2026110315"

#: The revision that succeeded ``2026110316``: ``2026110317`` (MIG-AUTHSESS-01, the durable
#: correlation -> subject store) creates ``auth_sessions`` and attaches here. ``2026110316``
#: is therefore no longer the head, exactly as this revision succeeded ``2026110315`` — the
#: "is this the head" assertions below compare against the successor rather than the constant
#: ``2026110316``, the same way ``tests/migration/m04e_ledger`` does.
SUCCESSOR_REVISION_ID = "2026110317"

LEDGER_TABLE = CUTOVER_LEDGER_TABLE
SOURCE_TAIL_TABLE = CUTOVER_SOURCE_TAIL_TABLE

CUTOVER_TABLES = (LEDGER_TABLE, SOURCE_TAIL_TABLE)

MANAGED_MODELS = (
    M06PartitionCutoverEvidenceRow,
    M06PartitionCutoverSourceTailRow,
)

#: The specification type names ``CUTOVER_REQUIRED_SCHEMA`` uses, rendered the way the
#: PostgreSQL dialect compiles them. ``TIMESTAMPTZ`` is PostgreSQL's own alias for
#: ``timestamp with time zone``.
SPEC_TYPE_LABELS = {
    "TEXT": "TEXT",
    "BIGINT": "BIGINT",
    "BOOLEAN": "BOOLEAN",
    "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
}

POSTGRESQL = postgresql.dialect()

#: DDL shapes no cutover revision may emit. This revision is also the registry
#: re-apply for its own tables, so it legitimately emits ``ENABLE/FORCE ROW LEVEL
#: SECURITY``, ``DROP POLICY IF EXISTS`` and ``CREATE POLICY``; privileges, writers and
#: data statements are never part of a table-creation step.
FORBIDDEN_DDL = (
    "GRANT ",
    "REVOKE ",
    "INSERT INTO",
    "UPDATE M06_",
    "DELETE FROM",
    "TRUNCATE",
    "DROP TABLE",
    "DROP TRIGGER",
    "DROP FUNCTION",
    "CREATE ROLE",
    "SET ROLE",
)


class RecordedOps:
    """Minimal stand-in for the ``alembic.op`` proxy.

    Tables are rebuilt as real SQLAlchemy objects in an isolated metadata so the
    recorded revision can be compared structurally with the ORM metadata.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.tables: dict[str, sa.Table] = {}
        self._metadata = sa.MetaData()

    def create_table(self, table_name: str, *columns: Any, **kwargs: Any) -> None:
        self.tables[table_name] = sa.Table(table_name, self._metadata, *columns, **kwargs)
        self.calls.append(("create_table", table_name))

    def create_index(
        self, index_name: str, table_name: str, columns: Any, **kwargs: Any
    ) -> None:
        self.calls.append(("create_index", table_name, index_name, tuple(columns), kwargs))

    def drop_index(
        self, index_name: str, table_name: str | None = None, **kwargs: Any
    ) -> None:
        self.calls.append(("drop_index", table_name, index_name))

    def drop_table(self, table_name: str, **kwargs: Any) -> None:
        self.calls.append(("drop_table", table_name))

    def execute(self, statement: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", str(statement)))


def load_revision_module_from(path: Path) -> Any:
    """Load an Alembic revision by path: ``alembic/versions`` is not a package."""

    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_revision_module() -> Any:
    return load_revision_module_from(REVISION_FILE)


def _literals(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {
            "revision", "down_revision", "branch_labels", "depends_on",
        }:
            values[target.id] = ast.literal_eval(node.value)
    return values


def revision_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        revision = _literals(path).get("revision")
        if isinstance(revision, str):
            graph[revision] = _literals(path).get("down_revision")
    return graph


def head_revisions() -> tuple[str, ...]:
    graph = revision_graph()
    children = {down for down in graph.values() if down}
    return tuple(sorted(revision for revision in graph if revision not in children))


def chain_from_head() -> tuple[str, ...]:
    graph = revision_graph()
    heads = head_revisions()
    assert len(heads) == 1, f"expected exactly one head, found {list(heads)}"
    chain: list[str] = []
    current: str | None = heads[0]
    while current is not None:
        chain.append(current)
        current = graph.get(current)
    return tuple(chain)


def policy_installers() -> frozenset[str]:
    """Revisions that emit ``policies.upgrade_guard_sql`` — the only policy installer."""

    installers: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        if "upgrade_guard_sql" not in path.read_text(encoding="utf-8"):
            continue
        revision = _literals(path).get("revision")
        if isinstance(revision, str):
            installers.add(revision)
    return frozenset(installers)


def type_label(type_: sa.types.TypeEngine) -> str:
    return POSTGRESQL.type_compiler.process(type_)


def squash(text: str) -> str:
    return " ".join(str(text).split())


def column_signature(table: sa.Table) -> list[tuple[str, str, bool, str | None]]:
    return [
        (column.name, type_label(column.type), column.nullable, _default_text(column))
        for column in table.columns
    ]


def _default_text(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    return squash(str(column.server_default.arg)).lower()


def check_catalog(table: sa.Table) -> dict[str | None, str]:
    return {
        constraint.name: squash(str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def unique_catalog(table: sa.Table) -> dict[str | None, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


# ---------------------------------------------------------------------------
# One partition and one self-consistent ledger transition
# ---------------------------------------------------------------------------

NOW = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
PARTITION = PartitionKey("tenant-a", "definition-a")


def digest(char: str) -> str:
    return "sha256:" + char * 64


def lease(epoch: int = 1, *, partition: PartitionKey = PARTITION) -> WriterLease:
    return WriterLease(
        partition, f"cutover-{epoch}", epoch, NOW + timedelta(minutes=10), f"fence-{epoch}"
    )


def shadow_record(*, evidence_id: str = "cmd-shadow", epoch: int = 1) -> PromotionRecord:
    """A minimal, self-consistent SHADOW lineage record for the first transition."""

    payload = {
        "evidence_id": evidence_id, "command_digest": digest("1"),
        "partition": [PARTITION.tenant_id, PARTITION.app_definition_id],
        "action": CutoverAction.SHADOW.value, "actor": "operator",
        "occurred_at": NOW.isoformat(),
        "prior_state": CutoverState.LEGACY_ACTIVE.value,
        "resulting_state": CutoverState.SHADOW.value,
        "prior_read_owner": "legacy", "resulting_read_owner": "legacy",
        "prior_decision_owner": "legacy", "resulting_decision_owner": "legacy",
        "prior_write_owner": "legacy", "resulting_write_owner": "legacy",
        "prior_record_id": None, "prior_record_digest": None,
        "canonical_positive_writes": False, "writer_epoch": epoch,
        "lease_fencing_token": f"fence-{epoch}", "gate_evidence": [],
        "parity_report_digest": None, "canonical_snapshot_digest": None,
        "source_snapshot_digest": None, "final_delta_checkpoint": None,
        "final_delta_source_change_sequence": None, "reason": "operator_entered_shadow",
    }
    return PromotionRecord(
        evidence_id=evidence_id, partition=PARTITION, action=CutoverAction.SHADOW,
        actor="operator", occurred_at=NOW,
        prior_state=CutoverState.LEGACY_ACTIVE, resulting_state=CutoverState.SHADOW,
        prior_read_owner="legacy", resulting_read_owner="legacy", writer_epoch=epoch,
        lease_fencing_token=f"fence-{epoch}", parity_report_digest=None,
        canonical_snapshot_digest=None, source_snapshot_digest=None,
        final_delta_checkpoint=None, final_delta_source_change_sequence=None,
        reason="operator_entered_shadow", record_digest=_lineage_payload_digest(payload),
        command_digest=digest("1"), prior_write_owner="legacy",
        resulting_write_owner="legacy", prior_decision_owner="legacy",
        resulting_decision_owner="legacy", prior_record_id=None,
        prior_record_digest=None, gate_evidence=(), canonical_positive_writes=False,
    )


def shadow_state(record: PromotionRecord, *, revision: int = 1) -> PartitionCutover:
    """The resulting state a caller appends together with ``record``."""

    return PartitionCutover(
        PARTITION, state=CutoverState.SHADOW, writer_epoch=record.writer_epoch,
        lease_holder=f"cutover-{record.writer_epoch}",
        fencing_token=record.lease_fencing_token, canonical_shadow_reads=True,
        revision=revision, last_evidence_id=record.evidence_id,
        lineage_head_digest=record.record_digest,
    )


__all__ = [
    "API_ROOT",
    "CUTOVER_TABLES",
    "DOWN_REVISION_ID",
    "FORBIDDEN_DDL",
    "LEDGER_TABLE",
    "MANAGED_MODELS",
    "NOW",
    "PARTITION",
    "POSTGRESQL",
    "REVISION_FILE",
    "REVISION_ID",
    "RecordedOps",
    "SOURCE_TAIL_TABLE",
    "SPEC_TYPE_LABELS",
    "SUCCESSOR_REVISION_ID",
    "chain_from_head",
    "check_catalog",
    "column_signature",
    "digest",
    "head_revisions",
    "lease",
    "load_revision_module",
    "load_revision_module_from",
    "policy_installers",
    "revision_graph",
    "shadow_record",
    "shadow_state",
    "squash",
    "type_label",
    "unique_catalog",
]
