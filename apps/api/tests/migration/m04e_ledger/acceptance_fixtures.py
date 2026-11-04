"""Acceptance fixtures for the M04E backfill ledger (task MIG-DDL-M04E-LEDGER-01).

Static review inputs, authored during the verification freeze and intentionally **not
executed** as part of this task. Nothing here connects to a database: the revision is
replayed in-process against a recording ``alembic.op`` stand-in, and the rows below are
transient ORM state used only to exercise the append-only/monotonicity guards.

``EVIDENCE_STORE_REQUIRED_DDL`` is quoted below as the *specification* of this DDL, so
``test_migration_contract.py`` can compare the authored tables against the text the
``EvidenceStore`` port refused over instead of against a reviewer's memory.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import sqlalchemy as sa

import prodavan.infrastructure.persistence.models as models  # noqa: F401  (registration)
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.m04e_ledger import (
    M04EDefinitionVersionAuditRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
)

#: ``apps/api`` — the first directory on the path that has no ``__init__.py``.
API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"
REVISION_FILE = VERSIONS / "2026110315_m04e_ledger.py"

REVISION_ID = "2026110315"
DOWN_REVISION_ID = "2026110314"

MAPPING_TABLE = "m04e_definition_version_mappings"
QUARANTINE_TABLE = "m04e_definition_version_quarantine"
CHECKPOINT_TABLE = "m04e_definition_version_checkpoints"
AUDIT_TABLE = "m04e_definition_version_audit"

LEDGER_TABLES = (MAPPING_TABLE, QUARANTINE_TABLE, CHECKPOINT_TABLE, AUDIT_TABLE)

MANAGED_MODELS = (
    M04EDefinitionVersionMappingRow,
    M04EDefinitionVersionQuarantineRow,
    M04EDefinitionVersionCheckpointRow,
    M04EDefinitionVersionAuditRow,
)

#: Static boundary of this artifact: nothing was executed to produce it.
BOUNDARY = {
    "execution": "not run",
    "alembic_upgrade": False,
    "database": False,
    "positive_canonical_writes": False,
    "verification_freeze": True,
}

#: DDL shapes no ledger revision may emit. Note what is *absent*: this revision is
#: also the registry re-apply for its own tables, so it legitimately emits
#: ``ENABLE/FORCE ROW LEVEL SECURITY``, ``DROP POLICY IF EXISTS`` and ``CREATE POLICY``
#: for the whole canonical registry — exactly like ``2026110312`` and ``2026110314``.
#: Privileges, writers and data statements are never part of a table-creation step.
FORBIDDEN_DDL = (
    "GRANT ",
    "REVOKE ",
    "INSERT INTO",
    "UPDATE M04E_",
    "DELETE FROM",
    "TRUNCATE",
    "DROP TABLE",
    "DROP TRIGGER",
    "DROP FUNCTION",
    "CREATE ROLE",
    "SET ROLE",
)

#: Column families that would mean raw material leaked into the ledger. A reason code,
#: a digest, an identifier or a cursor token is admissible; a payload is not.
FORBIDDEN_COLUMN_NAMES = (
    "payload",
    "raw",
    "manifest",
    "description",
    "body",
    "secret",
    "password",
    "token_secret",
    "api_key",
    "content",
)

#: The two tables ``EVIDENCE_STORE_REQUIRED_DDL`` enumerates column by column. Parsed
#: from the port module itself so the assertion cannot drift from the contract.
SPEC_TABLES = (MAPPING_TABLE, CHECKPOINT_TABLE)

#: The two tables whose spec entry is prose; their required columns are listed here
#: verbatim from ``EVIDENCE_STORE_REQUIRED_DDL`` items 2 and 4.
PROSE_SPEC_COLUMNS = {
    QUARANTINE_TABLE: (
        "observed_source_digest",
        "observed_artifact_digest",
        "reason_code",
        "blocker_evidence",
        "checkpoint",
    ),
    AUDIT_TABLE: (
        "event_kind",
        "reason_code",
        "correlation_id",
        "checkpoint",
        "actor",
        "accepted_count",
        "quarantined_count",
    ),
}


class RecordedOps:
    """Minimal stand-in for the ``alembic.op`` proxy.

    Tables are rebuilt with real SQLAlchemy objects so the recorded revision can be
    compared structurally with the ORM metadata. The recording metadata starts empty:
    this revision declares no foreign key, so it needs no scaffold table.
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
    """Load an Alembic revision by path: ``alembic/versions`` is not an import package."""

    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_revision_module() -> Any:
    return load_revision_module_from(REVISION_FILE)


def revision_literal(path: Path) -> str | None:
    """Read ``revision`` from a module without importing it (``op`` must not run)."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "revision":
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    return None


def distinct_revision_literals(path: Path) -> tuple[str | None, str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    revision: str | None = None
    down: str | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "revision":
            revision = ast.literal_eval(node.value)
        elif target.id == "down_revision":
            down = ast.literal_eval(node.value)
    return revision, down


def revision_graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        revision, down = distinct_revision_literals(path)
        if revision is not None:
            graph[revision] = down
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
        revision = revision_literal(path)
        if revision is not None:
            installers.add(revision)
    return frozenset(installers)


def spec_columns(table: str) -> tuple[str, ...]:
    """Columns an ``EVIDENCE_STORE_REQUIRED_DDL`` entry enumerates for ``table``.

    The port module is the specification: this helper reads the literal tuple and
    extracts the parenthesised column list that follows the table name, so the test
    compares authored DDL against the contract text rather than against a paraphrase.
    """

    from prodavan.application.migration.m04e_ports import EVIDENCE_STORE_REQUIRED_DDL

    entry = next(item for item in EVIDENCE_STORE_REQUIRED_DDL if item.startswith(f"{table} ("))
    body = entry[len(table) :].lstrip()
    assert body.startswith("("), entry
    depth = 0
    for index, character in enumerate(body):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                inner = body[1:index]
                break
    else:  # pragma: no cover - a malformed contract string would land here
        raise AssertionError(f"unbalanced column list in {entry!r}")
    return tuple(
        column.strip() for column in inner.split(",") if column.strip()
    )


_SHA = "sha256:" + "a" * 64
_OTHER_SHA = "sha256:" + "b" * 64


def mapping_row(**overrides: Any) -> M04EDefinitionVersionMappingRow:
    values: dict[str, Any] = {
        "id": "m04emap-1",
        "tenant_id": "tenant-a",
        "definition_id": "definition-1",
        "source_kind": "m04e.legacy.app_definition",
        "source_id": "src-1",
        "source_schema_version": "legacy.v1",
        "phase": "definition",
        "status": "accepted",
        "source_digest": _SHA,
        "canonical_digest": _OTHER_SHA,
        "canonical_identity": ["tenant-a", "app-one"],
        "reason_code": None,
        "proof_evidence": {"owner": _SHA, "active_scope": _SHA},
        "artifact_digest": None,
        "verified_artifact_digest": None,
        "checkpoint": "checkpoint-token-1",
        "correlation_id": "correlation-1",
    }
    values.update(overrides)
    return M04EDefinitionVersionMappingRow(**values)


def quarantine_row(**overrides: Any) -> M04EDefinitionVersionQuarantineRow:
    values: dict[str, Any] = {
        "id": "m04equar-1",
        "tenant_id": "tenant-a",
        "definition_id": "definition-1",
        "phase": "definition",
        "source_kind": "m04e.legacy.app_definition",
        "source_id": "src-1",
        "source_schema_version": "legacy.v1",
        "source_digest": _SHA,
        "observed_source_digest": _OTHER_SHA,
        "observed_artifact_digest": None,
        "reason_code": "source_digest_changed",
        "blocker_evidence": ["restore the reviewed artifact lineage"],
        "checkpoint": "checkpoint-token-2",
        "status": "open",
        "correlation_id": "correlation-1",
    }
    values.update(overrides)
    return M04EDefinitionVersionQuarantineRow(**values)


def checkpoint_row(**overrides: Any) -> M04EDefinitionVersionCheckpointRow:
    values: dict[str, Any] = {
        "id": "m04ecp-1",
        "tenant_id": "tenant-a",
        "definition_id": "definition-1",
        "phase": "definition",
        "after_source_id": "src-9",
        "sequence": 9,
        "phase_complete": False,
        "source_snapshot_id": "snapshot-1",
        "source_schema_version": "legacy.v1",
        "source_snapshot_digest": _SHA,
        "canonical_schema_version": "m04e.v1",
        "writer_generation": 1,
        "checkpoint_token": "checkpoint-token-9",
    }
    values.update(overrides)
    return M04EDefinitionVersionCheckpointRow(**values)


def audit_row(**overrides: Any) -> M04EDefinitionVersionAuditRow:
    values: dict[str, Any] = {
        "id": "m04eaudit-1",
        "event_id": "event-1",
        "tenant_id": "tenant-a",
        "definition_id": "definition-1",
        "phase": "definition",
        "event_kind": "page_committed",
        "reason_code": None,
        "correlation_id": "correlation-1",
        "checkpoint": "checkpoint-token-1",
        "sequence": 10,
        "actor": "migration-actor",
        "transaction_id": "tx-1",
        "record_count": 3,
        "accepted_count": 1,
        "replay_count": 1,
        "quarantined_count": 1,
        "conflict_count": 0,
        "blocker_evidence": [],
    }
    values.update(overrides)
    return M04EDefinitionVersionAuditRow(**values)


ROW_FACTORIES = {
    MAPPING_TABLE: mapping_row,
    QUARANTINE_TABLE: quarantine_row,
    CHECKPOINT_TABLE: checkpoint_row,
    AUDIT_TABLE: audit_row,
}

#: ``(case, table, constraint name, predicate fragment)``. Each case names the object
#: that must reject the row and the fragment that carries the rule; the objects are
#: asserted against both the recorded revision DDL and the ORM metadata.
NEGATIVE_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "mapping_status_outside_the_backfill_vocabulary",
        MAPPING_TABLE,
        "ck_m04e_mapping_status",
        "status IN ('accepted', 'replay', 'quarantined', 'conflict', 'blocked')",
    ),
    (
        "mapping_phase_outside_definition_and_version",
        MAPPING_TABLE,
        "ck_m04e_mapping_phase",
        "phase IN ('definition', 'version')",
    ),
    (
        "accepted_mapping_without_canonical_digest",
        MAPPING_TABLE,
        "ck_m04e_mapping_accepted_has_canonical_digest",
        "status <> 'accepted' OR canonical_digest IS NOT NULL",
    ),
    (
        "non_accepted_mapping_without_reason_code",
        MAPPING_TABLE,
        "ck_m04e_mapping_non_accepted_requires_reason",
        "status = 'accepted' OR (reason_code IS NOT NULL",
    ),
    (
        "malformed_mapping_source_digest",
        MAPPING_TABLE,
        "ck_m04e_mapping_source_sha256",
        "source_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "canonical_identity_is_not_a_json_array",
        MAPPING_TABLE,
        "ck_m04e_mapping_canonical_identity_shape",
        "jsonb_typeof(canonical_identity) <> 'array' THEN false",
    ),
    (
        "accepted_definition_mapping_with_the_wrong_identity_arity",
        MAPPING_TABLE,
        "ck_m04e_mapping_canonical_identity_shape",
        "jsonb_array_length(canonical_identity) = CASE phase WHEN 'definition' THEN 2 ELSE 3 END",
    ),
    (
        "proof_evidence_is_not_a_json_object",
        MAPPING_TABLE,
        "ck_m04e_mapping_proof_evidence_object",
        "jsonb_typeof(proof_evidence) = 'object'",
    ),
    (
        "observation_without_a_redacted_reason_code",
        QUARANTINE_TABLE,
        "ck_m04e_quarantine_reason_nonempty",
        "length(btrim(reason_code)) > 0",
    ),
    (
        "observation_carries_both_observed_digests",
        QUARANTINE_TABLE,
        "ck_m04e_quarantine_single_observation",
        "(observed_source_digest IS NOT NULL) <> (observed_artifact_digest IS NOT NULL)",
    ),
    (
        "observation_carries_neither_observed_digest",
        QUARANTINE_TABLE,
        "ck_m04e_quarantine_single_observation",
        "(observed_source_digest IS NOT NULL) <> (observed_artifact_digest IS NOT NULL)",
    ),
    (
        "source_observation_that_does_not_change_the_digest",
        QUARANTINE_TABLE,
        "ck_m04e_quarantine_observation_differs",
        "observed_source_digest IS NULL OR observed_source_digest <> source_digest",
    ),
    (
        "blocker_evidence_is_not_a_json_array",
        QUARANTINE_TABLE,
        "ck_m04e_quarantine_blocker_evidence_array",
        "jsonb_typeof(blocker_evidence) = 'array'",
    ),
    (
        "negative_checkpoint_sequence",
        CHECKPOINT_TABLE,
        "ck_m04e_checkpoint_sequence_nonnegative",
        "sequence >= 0",
    ),
    (
        "definition_phase_claimed_complete",
        CHECKPOINT_TABLE,
        "ck_m04e_checkpoint_complete_only_version",
        "phase_complete = false OR phase = 'version'",
    ),
    (
        "empty_cursor_string_instead_of_null",
        CHECKPOINT_TABLE,
        "ck_m04e_checkpoint_cursor_nonempty",
        "after_source_id IS NULL OR length(btrim(after_source_id)) > 0",
    ),
    (
        "malformed_checkpoint_snapshot_digest",
        CHECKPOINT_TABLE,
        "ck_m04e_checkpoint_source_sha256",
        "source_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "negative_checkpoint_writer_generation",
        CHECKPOINT_TABLE,
        "ck_m04e_checkpoint_writer_generation_nonnegative",
        "writer_generation IS NULL OR writer_generation >= 0",
    ),
    (
        "audit_event_kind_outside_the_closed_vocabulary",
        AUDIT_TABLE,
        "ck_m04e_audit_event_kind",
        "event_kind IN ('page_committed', 'page_blocked')",
    ),
    (
        "blocked_page_without_a_reason_code",
        AUDIT_TABLE,
        "ck_m04e_audit_blocked_requires_reason",
        "event_kind <> 'page_blocked' OR (reason_code IS NOT NULL",
    ),
    (
        "committed_page_whose_counters_do_not_add_up",
        AUDIT_TABLE,
        "ck_m04e_audit_counters_balance",
        "record_count = accepted_count + replay_count + quarantined_count + conflict_count",
    ),
    (
        "blocked_page_claiming_classifications",
        AUDIT_TABLE,
        "ck_m04e_audit_counters_balance",
        "event_kind = 'page_blocked' AND record_count = 0",
    ),
    (
        "negative_audit_counter",
        AUDIT_TABLE,
        "ck_m04e_audit_counters_nonnegative",
        "accepted_count >= 0 AND replay_count >= 0",
    ),
    (
        "audit_row_without_actor",
        AUDIT_TABLE,
        "ck_m04e_audit_actor_nonempty",
        "length(btrim(actor)) > 0",
    ),
    (
        "audit_row_without_transaction_identity",
        AUDIT_TABLE,
        "ck_m04e_audit_transaction_nonempty",
        "length(btrim(transaction_id)) > 0",
    ),
)

__all__ = [
    "API_ROOT",
    "AUDIT_TABLE",
    "BOUNDARY",
    "CHECKPOINT_TABLE",
    "DOWN_REVISION_ID",
    "FORBIDDEN_COLUMN_NAMES",
    "FORBIDDEN_DDL",
    "LEDGER_TABLES",
    "MANAGED_MODELS",
    "MAPPING_TABLE",
    "NEGATIVE_CASES",
    "PROSE_SPEC_COLUMNS",
    "QUARANTINE_TABLE",
    "REVISION_FILE",
    "REVISION_ID",
    "ROW_FACTORIES",
    "RecordedOps",
    "SPEC_TABLES",
    "audit_row",
    "chain_from_head",
    "checkpoint_row",
    "distinct_revision_literals",
    "head_revisions",
    "load_revision_module",
    "load_revision_module_from",
    "mapping_row",
    "policy_installers",
    "quarantine_row",
    "revision_graph",
    "revision_literal",
    "spec_columns",
]
