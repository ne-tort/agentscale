"""MIG-DDL-M04EVID-01 contract for Alembic revision ``2026110313``.

Static acceptance artifact, authored during the verification freeze and
intentionally **not executed** as part of this task.

The test never connects to a database and never runs ``alembic upgrade``. It replaces
the ``alembic.op`` proxy with a recorder, executes the revision module's
``upgrade()``/``downgrade()`` in-process, and compares the emitted objects with the
SQLAlchemy metadata that is the schema source of truth
(``apps/api/src/prodavan/infrastructure/persistence/models/m04_scope_evidence.py``).

What it proves while reasoning over code:

* revision identifiers keep the wave chain ``2026110313 -> 2026110312``;
* exactly one table is created — ``m04_scope_promotion_evidence`` — with column,
  type, nullability and server-default parity, named check-constraint parity, unique
  and foreign-key parity, primary-key parity and index parity against the model;
* the partition key ``(tenant_id, scope_kind, scope_id)`` and the per-partition
  ``revision_id`` identity are both indexed/constrained, and a second row with the same
  ``revision_id`` inside one partition cannot exist;
* ``outcome`` admits ``ready`` here, and the three older promotion-evidence tables keep
  their ``('blocked','quarantined')`` limit untouched (blocker
  ``promotion_outcome_cannot_express_ready`` is fixed for new rows only);
* a ``ready`` row must name the actor whose membership was proven, and a non-ready row
  must carry a redacted reason code — both enforced by named checks;
* the row is append-only: the ORM guard covers every column and rejects an updated
  one, and ``canonical_positive_writes`` is pinned to ``false``;
* the upgrade fails closed with a named ``MIG-DDL-M04EVID-01 blocker`` when the
  ``scope_registry`` foreign-key target is missing, before any table is created;
* the table is registered in the canonical RLS registry (tenant scope on
  ``tenant_id``, ``owner_revision`` ``2026110313``) and the coverage report sees it,
  while the revision itself emits **no** RLS, grant, backfill or data statement —
  the policy is installed by the next registry re-apply revision;
* ``downgrade()`` removes only this revision's two indexes and the table, and never
  touches data.

Row-level outcomes (duplicate ``revision_id`` insert, missing actor on a ``ready`` row,
unknown ``scope_registry_id`` reference, cross-tenant registry reference) need a live
PostgreSQL database and stay review inputs until the freeze is lifted. The named
objects that must produce each rejection are asserted below, and the residual gap that
a single-column foreign key cannot close (tenant equality of the registry reference) is
recorded in the model's ``M04_SCOPE_EVIDENCE_DEFERRED``.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import alembic
import alembic.op  # noqa: F401 — load the proxy module so it can be swapped below
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import make_transient_to_detached

import prodavan.infrastructure.persistence.models as models
from prodavan.infrastructure.persistence.models import Base
from prodavan.infrastructure.persistence.models.canonical_grants import (
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES,
    M04_SCOPE_EVIDENCE_DEFERRED,
    M04ScopePromotionEvidenceRow,
    _APPEND_ONLY_FIELDS,
    _reject_scope_evidence_mutation,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.rls import policies


REVISION_ID = "2026110313"
DOWN_REVISION_ID = "2026110312"

EVIDENCE_TABLE = "m04_scope_promotion_evidence"
SCOPE_REGISTRY_TABLE = "scope_registry"

PARTITION_INDEX = "ix_m04_scope_promotion_partition"
RECORDED_AT_INDEX = "ix_m04_scope_promotion_recorded_at"

REVISION_CONSTRAINT = "uq_m04_scope_promotion_revision"
REGISTRY_FOREIGN_KEY = "fk_m04_scope_promotion_registry"
OUTCOME_CHECK = "ck_m04_scope_promotion_outcome"
READY_ACTOR_CHECK = "ck_m04_scope_promotion_ready_requires_actor"
NON_READY_REASON_CHECK = "ck_m04_scope_promotion_non_ready_requires_reason"

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = API_ROOT / "alembic" / "versions"
REVISION_FILE = VERSIONS / "2026110313_m04_scope_evidence.py"

MODEL = M04ScopePromotionEvidenceRow
MANAGED_TABLES = (EVIDENCE_TABLE,)

_DIALECT = postgresql.dialect()

# DDL shapes that must not appear in this revision. RLS belongs to the next registry
# re-apply revision; grants, writers and backfill are never part of a table DDL step.
FORBIDDEN_DDL = (
    "ROW LEVEL SECURITY",
    "CREATE POLICY",
    "GRANT ",
    "REVOKE ",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "TRUNCATE",
    "ALTER TABLE",
)

#: The two oldest promotion tables keep their two-state limit; M06 already admits
#: ``ready``/``promotable`` and is the precedent this table follows.
LEGACY_TWO_STATE_MODELS = (
    M04EDefinitionVersionPromotionEvidenceRow,
    M05PluginInstancePromotionEvidenceRow,
)

#: Static boundary of this artifact: nothing was executed to produce it.
BOUNDARY = {
    "execution": "not run",
    "alembic_upgrade": False,
    "database": False,
    "positive_canonical_writes": False,
    "verification_freeze": True,
}

#: Negative cases, each mapped to the named object that must reject the row and to the
#: predicate fragment that carries the rule. The objects are asserted against both the
#: recorded revision DDL and the ORM metadata, so model and revision cannot drift.
NEGATIVE_CASES = (
    (
        "outcome_outside_the_three_states",
        OUTCOME_CHECK,
        "outcome IN ('blocked', 'quarantined', 'ready')",
    ),
    (
        "plugin_instance_scope_cannot_claim_scope_evidence",
        "ck_m04_scope_promotion_scope_kind",
        "scope_kind IN ('tenant', 'cabinet', 'project')",
    ),
    (
        "scope_kind_and_scope_id_disagree",
        "ck_m04_scope_promotion_typed_shape",
        "scope_kind = 'tenant' AND scope_id = tenant_id",
    ),
    (
        "project_scope_without_cabinet_anchor",
        "ck_m04_scope_promotion_typed_shape",
        "scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
        "AND cabinet_id IS NOT NULL",
    ),
    (
        "ready_row_without_actor_membership",
        READY_ACTOR_CHECK,
        "outcome <> 'ready' OR (actor_id IS NOT NULL AND length(btrim(actor_id)) > 0)",
    ),
    (
        "non_ready_row_without_reason_code",
        NON_READY_REASON_CHECK,
        "outcome = 'ready' OR (reason_code IS NOT NULL AND length(btrim(reason_code)) > 0)",
    ),
    (
        "malformed_source_snapshot_digest",
        "ck_m04_scope_promotion_source_sha256",
        "source_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "malformed_canonical_digest",
        "ck_m04_scope_promotion_canonical_sha256",
        "canonical_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "malformed_checkpoint_digest",
        "ck_m04_scope_promotion_checkpoint_sha256",
        "checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "malformed_gate_evidence_digest",
        "ck_m04_scope_promotion_gate_sha256",
        "gate_evidence_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "malformed_rls_policy_digest",
        "ck_m04_scope_promotion_rls_sha256",
        "rls_policy_digest ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "rls_review_or_role_missing",
        "ck_m04_scope_promotion_rls_review_nonempty",
        "length(btrim(rls_security_review_id)) > 0 AND length(btrim(rls_database_role)) > 0",
    ),
    (
        "gate_evidence_is_not_a_json_object",
        "ck_m04_scope_promotion_gate_evidence_object",
        "jsonb_typeof(gate_evidence) = 'object'",
    ),
    (
        "missing_correlation_id",
        "ck_m04_scope_promotion_correlation_nonempty",
        "length(btrim(correlation_id)) > 0",
    ),
    (
        "negative_writer_generation",
        "ck_m04_scope_promotion_writer_generation_nonnegative",
        "writer_generation >= 0",
    ),
    (
        "positive_writer_flag_is_refused",
        "ck_m04_scope_promotion_no_positive_writes",
        "canonical_positive_writes = false",
    ),
)

_SHA = "sha256:" + "a" * 64


class RecordedOps:
    """Minimal stand-in for the ``alembic.op`` proxy.

    Tables are rebuilt with real SQLAlchemy objects so the recorded revision can be
    compared structurally with the ORM metadata. Local ``companies`` and
    ``scope_registry`` stubs keep the foreign-key target resolvable inside the recording
    metadata; they are test scaffolding and are never created by the revision.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.metadata = sa.MetaData()
        sa.Table("companies", self.metadata, sa.Column("id", sa.String(40), primary_key=True))
        sa.Table(
            SCOPE_REGISTRY_TABLE,
            self.metadata,
            sa.Column("id", sa.String(40), primary_key=True),
        )
        self.tables: dict[str, sa.Table] = {}

    def create_table(self, table_name: str, *columns: Any, **kwargs: Any) -> None:
        self.tables[table_name] = sa.Table(table_name, self.metadata, *columns, **kwargs)
        self.calls.append(("create_table", table_name))

    def create_index(
        self, index_name: str, table_name: str, columns: Any, **kwargs: Any
    ) -> None:
        self.calls.append(("create_index", table_name, index_name, tuple(columns), kwargs))

    def drop_index(self, index_name: str, table_name: str | None = None, **kwargs: Any) -> None:
        self.calls.append(("drop_index", table_name, index_name))

    def drop_table(self, table_name: str, **kwargs: Any) -> None:
        self.calls.append(("drop_table", table_name))

    def execute(self, statement: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", str(statement)))


def _load_revision_module_from(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_revision_module() -> Any:
    return _load_revision_module_from(REVISION_FILE)


@pytest.fixture()
def revision(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, RecordedOps]:
    """Import the revision with a recording ``op`` proxy and run ``upgrade()``."""
    recorder = RecordedOps()
    monkeypatch.setattr(alembic, "op", recorder)
    module = _load_revision_module()
    module.upgrade()
    return module, recorder


def _revision_literal(path: Path) -> str | None:
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


def _type_label(type_: Any) -> str:
    return _DIALECT.type_compiler.process(type_)


def _default_text(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    return " ".join(str(column.server_default.arg).split()).lower()


def _column_signature(table: sa.Table) -> list[tuple[str, str, bool, str | None]]:
    return [
        (column.name, _type_label(column.type), column.nullable, _default_text(column))
        for column in table.columns
    ]


def _check_catalog(table: sa.Table) -> dict[str | None, str]:
    return {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _unique_catalog(table: sa.Table) -> dict[str | None, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _fk_catalog(table: sa.Table) -> dict[str | None, tuple[Any, ...]]:
    return {
        constraint.name: (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
            constraint.deferrable,
            constraint.initially,
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _unique_key_column_sets(table: sa.Table) -> set[tuple[str, ...]]:
    keys = {tuple(column.name for column in table.primary_key.columns)}
    keys.update(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )
    return keys


def _recorded_indexes(recorder: RecordedOps) -> dict[str, tuple[str, ...]]:
    return {
        call[2]: tuple(call[3])
        for call in recorder.calls
        if call[0] == "create_index" and call[1] == EVIDENCE_TABLE
    }


def _scope_evidence_row() -> M04ScopePromotionEvidenceRow:
    """A complete, valid accepted row; state only, never persisted."""
    return M04ScopePromotionEvidenceRow(
        id="m04scope-1",
        tenant_id="tenant-a",
        scope_kind="tenant",
        scope_id="tenant-a",
        cabinet_id=None,
        project_id=None,
        scope_registry_id="registry-1",
        revision_id="rev-1",
        outcome="ready",
        source_snapshot_id="snapshot-1",
        source_snapshot_digest=_SHA,
        canonical_digest=_SHA,
        canonical_schema_version="scope-registry.v1",
        checkpoint_digest=_SHA,
        gate_evidence_digest=_SHA,
        rls_policy_digest=_SHA,
        rls_security_review_id="rls-review-1",
        rls_database_role="prodavan_migration",
        gate_evidence={"gates": [], "redaction": "identifiers_digests_counts_only"},
        writer_generation=1,
        correlation_id="correlation-1",
        actor_id="actor-1",
        canonical_positive_writes=False,
    )


def test_revision_identifiers_follow_the_wave_chain() -> None:
    # Identifiers are plain module attributes: the module is loaded with the real
    # ``op`` proxy, which is never called at import time.
    module = _load_revision_module()

    assert REVISION_FILE.is_file()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID
    assert module.branch_labels is None
    assert module.depends_on is None
    assert module.EVIDENCE_TABLE == EVIDENCE_TABLE


def test_successor_revision_chains_from_this_one() -> None:
    """A later revision (registry re-apply) must not fork the head."""
    successors = sorted(VERSIONS.glob("2026110314*.py"))
    if not successors:
        pytest.skip("successor revision is not authored yet")

    for path in successors:
        module = _load_revision_module_from(path)
        assert module.down_revision == REVISION_ID


def test_creates_only_the_evidence_table(revision: tuple[Any, RecordedOps]) -> None:
    _, recorder = revision

    assert set(recorder.tables) == set(MANAGED_TABLES)
    assert EVIDENCE_TABLE in Base.metadata.tables
    assert MODEL.__tablename__ == EVIDENCE_TABLE
    assert Base.metadata.tables[EVIDENCE_TABLE] is MODEL.__table__


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_columns_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = recorder.tables[table_name]
    expected = Base.metadata.tables[table_name]

    assert recorded.schema is None
    assert _column_signature(recorded) == _column_signature(expected)


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_check_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[table_name])
    expected = _check_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every check constraint is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_unique_constraints_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[table_name])
    expected = _unique_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every unique constraint is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_foreign_keys_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = _fk_catalog(recorder.tables[table_name])
    expected = _fk_catalog(Base.metadata.tables[table_name])

    assert recorded == expected
    assert all(name for name in recorded), "every foreign key is explicitly named"


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_primary_keys_match_metadata(
    revision: tuple[Any, RecordedOps], table_name: str
) -> None:
    _, recorder = revision

    recorded = tuple(column.name for column in recorder.tables[table_name].primary_key.columns)
    expected = tuple(column.name for column in Base.metadata.tables[table_name].primary_key.columns)

    assert recorded == expected
    assert recorded == ("id",)


@pytest.mark.parametrize("table_name", MANAGED_TABLES)
def test_indexes_match_metadata(revision: tuple[Any, RecordedOps], table_name: str) -> None:
    _, recorder = revision

    recorded = {
        (call[2], tuple(call[3]))
        for call in recorder.calls
        if call[0] == "create_index" and call[1] == table_name
    }
    expected = {
        (index.name, tuple(column.name for column in index.columns))
        for index in Base.metadata.tables[table_name].indexes
    }

    assert recorded == expected


def test_partition_and_time_indexes_are_the_documented_ones(
    revision: tuple[Any, RecordedOps],
) -> None:
    """The partition lookup and the newest-row lookup must both be indexed."""
    _, recorder = revision

    indexes = _recorded_indexes(recorder)

    assert set(indexes) == {PARTITION_INDEX, RECORDED_AT_INDEX}
    assert indexes[PARTITION_INDEX] == ("tenant_id", "scope_kind", "scope_id", "recorded_at")
    assert indexes[RECORDED_AT_INDEX] == ("recorded_at",)


def test_revision_id_is_unique_inside_one_partition(
    revision: tuple[Any, RecordedOps],
) -> None:
    _, recorder = revision

    recorded = _unique_catalog(recorder.tables[EVIDENCE_TABLE])
    expected = _unique_catalog(Base.metadata.tables[EVIDENCE_TABLE])

    assert recorded[REVISION_CONSTRAINT] == ("tenant_id", "scope_kind", "scope_id", "revision_id")
    assert recorded == expected


def test_registry_reference_is_a_restricted_foreign_key(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A row cannot cite a scope-registry identity that does not exist."""
    _, recorder = revision

    recorded = _fk_catalog(recorder.tables[EVIDENCE_TABLE])
    expected = _fk_catalog(Base.metadata.tables[EVIDENCE_TABLE])

    assert recorded == expected
    assert recorded[REGISTRY_FOREIGN_KEY] == (
        ("scope_registry_id",),
        (f"{SCOPE_REGISTRY_TABLE}.id",),
        "RESTRICT",
        None,
        None,
    )

    target = Base.metadata.tables[SCOPE_REGISTRY_TABLE]
    assert ("id",) in _unique_key_column_sets(target), (
        "fk_m04_scope_promotion_registry must reference a primary or unique key"
    )


def test_outcome_admits_ready_and_the_two_oldest_tables_do_not(
    revision: tuple[Any, RecordedOps],
) -> None:
    """Blocker ``promotion_outcome_cannot_express_ready`` is fixed for new M04 rows."""
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[EVIDENCE_TABLE])[OUTCOME_CHECK]
    expected = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])[OUTCOME_CHECK]

    assert recorded == expected
    assert "ready" in recorded
    assert "'blocked'" in recorded and "'quarantined'" in recorded

    # The two oldest promotion tables keep the two-state limit: rewriting an applied
    # CHECK would be a data migration, not a schema fix.
    for model in LEGACY_TWO_STATE_MODELS:
        legacy = _check_catalog(model.__table__)
        outcome_names = [name for name in legacy if name and name.endswith("promotion_outcome")]
        assert len(outcome_names) == 1, model.__tablename__
        text = legacy[outcome_names[0]]
        assert "ready" not in text
        assert "'blocked'" in text and "'quarantined'" in text

    # M06 already admits an accepted status and is the precedent followed here.
    m06 = _check_catalog(M06CanonicalGrantPromotionEvidenceRow.__table__)
    assert "ready" in m06["ck_m06_grant_promotion_outcome"]
    assert "promotable" in m06["ck_m06_grant_promotion_outcome"]


@pytest.mark.parametrize(
    "case, constraint_name, fragment",
    NEGATIVE_CASES,
    ids=[case[0] for case in NEGATIVE_CASES],
)
def test_each_negative_case_is_enforced_by_a_named_constraint(
    revision: tuple[Any, RecordedOps],
    case: str,
    constraint_name: str,
    fragment: str,
) -> None:
    _, recorder = revision

    recorded = _check_catalog(recorder.tables[EVIDENCE_TABLE])
    expected = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])

    assert constraint_name in recorded, f"{case} has no enforcing check constraint"
    assert recorded[constraint_name] == expected[constraint_name]
    assert " ".join(fragment.split()) in recorded[constraint_name], case


def test_scope_kinds_are_the_typed_owner_scopes() -> None:
    """Plugin-instance scope is refused by contract and by this table's checks."""
    shape = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])[
        "ck_m04_scope_promotion_typed_shape"
    ]

    assert "scope_kind = 'tenant'" in shape
    assert "scope_kind = 'cabinet'" in shape
    assert "scope_kind = 'project'" in shape
    assert "plugin_instance" not in shape


def test_append_only_guard_covers_every_column_and_fires() -> None:
    """An accepted evidence row is never rewritten."""
    columns = {column.name for column in MODEL.__table__.columns}

    assert set(_APPEND_ONLY_FIELDS) == columns
    assert sa.event.contains(MODEL, "before_update", _reject_scope_evidence_mutation)

    # ``make_transient_to_detached`` gives the instance a committed state, which is what
    # the mapper sees for a persisted row: unmodified attributes carry no history.
    unchanged = _scope_evidence_row()
    make_transient_to_detached(unchanged)
    _reject_scope_evidence_mutation(None, None, unchanged)

    for field, value in (
        ("outcome", "quarantined"),
        ("canonical_digest", "sha256:" + "b" * 64),
        ("canonical_positive_writes", True),
    ):
        changed = _scope_evidence_row()
        make_transient_to_detached(changed)
        setattr(changed, field, value)
        with pytest.raises(ValueError, match="append-only") as raised:
            _reject_scope_evidence_mutation(None, None, changed)
        assert field in str(raised.value)


def test_ready_row_is_the_only_consumable_status() -> None:
    """The read port must be able to select ``outcome = 'ready'`` directly."""
    outcome = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])[OUTCOME_CHECK]

    states = outcome.split("IN", 1)[1].strip()
    assert states.count("'") == 6, outcome
    assert "'ready'" in states
    assert states.index("'ready'") > states.index("'quarantined'")

    # A READY row without an actor is refused by the database, not by a reader.
    ready_gate = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])[READY_ACTOR_CHECK]
    assert ready_gate.startswith("outcome <> 'ready' OR (actor_id IS NOT NULL")

    reason_gate = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])[NON_READY_REASON_CHECK]
    assert reason_gate.startswith("outcome = 'ready' OR (reason_code IS NOT NULL")


def test_canonical_positive_writes_stay_false() -> None:
    checks = _check_catalog(Base.metadata.tables[EVIDENCE_TABLE])

    assert checks["ck_m04_scope_promotion_no_positive_writes"] == (
        "canonical_positive_writes = false"
    )
    column = Base.metadata.tables[EVIDENCE_TABLE].c.canonical_positive_writes
    assert column.nullable is False
    assert _default_text(column) == "false"


def test_upgrade_fails_closed_before_creating_the_table(
    revision: tuple[Any, RecordedOps],
) -> None:
    """A partial schema aborts with a named blocker instead of an unvalidatable FK."""
    _, recorder = revision

    execute_positions = [
        index for index, call in enumerate(recorder.calls) if call[0] == "execute"
    ]
    create_position = next(
        index for index, call in enumerate(recorder.calls) if call[0] == "create_table"
    )

    assert len(execute_positions) == 1
    assert execute_positions[0] < create_position

    guard = recorder.calls[execute_positions[0]][1]
    assert "MIG-DDL-M04EVID-01 blocker" in guard
    assert f"to_regclass('{SCOPE_REGISTRY_TABLE}') IS NULL" in guard
    assert "RAISE EXCEPTION" in guard
    assert "pg_constraint" in guard and "contype IN ('p', 'u')" in guard
    assert "ARRAY['id']" in guard
    assert REGISTRY_FOREIGN_KEY in guard
    assert "EXCEPTION WHEN" not in guard, "a real DDL failure must abort, not be swallowed"


def test_revision_creates_no_rls_grants_or_data(revision: tuple[Any, RecordedOps]) -> None:
    """RLS belongs to the next registry re-apply revision; no writer or data path here."""
    _, recorder = revision

    assert {call[0] for call in recorder.calls} <= {
        "create_table",
        "create_index",
        "execute",
    }

    for call in recorder.calls:
        if call[0] != "execute":
            continue
        statement = call[1].upper()
        for forbidden in FORBIDDEN_DDL:
            assert forbidden not in statement, f"unexpected DDL fragment {forbidden!r}"


def test_downgrade_removes_only_this_revision_objects_in_reverse_order(
    revision: tuple[Any, RecordedOps],
) -> None:
    module, recorder = revision

    upgrade_call_count = len(recorder.calls)
    module.downgrade()
    downgrade_calls = recorder.calls[upgrade_call_count:]

    assert [call[0] for call in downgrade_calls] == [
        "drop_index",
        "drop_index",
        "drop_table",
    ]
    assert [call[2] for call in downgrade_calls if call[0] == "drop_index"] == [
        RECORDED_AT_INDEX,
        PARTITION_INDEX,
    ]
    assert all(
        call[1] == EVIDENCE_TABLE for call in downgrade_calls if call[0] == "drop_index"
    )
    assert downgrade_calls[-1] == ("drop_table", EVIDENCE_TABLE)
    assert not any(call[0] == "execute" for call in downgrade_calls), (
        "downgrade must not emit data or policy statements"
    )


def test_model_is_registered_once_in_the_models_package() -> None:
    assert getattr(models, "M04ScopePromotionEvidenceRow") is MODEL
    assert "M04ScopePromotionEvidenceRow" in models.__all__
    assert Base.metadata.tables[EVIDENCE_TABLE] is MODEL.__table__

    owners = [
        mapper.class_ for mapper in Base.registry.mappers if mapper.local_table is MODEL.__table__
    ]
    assert owners == [MODEL]


def test_table_is_registered_in_the_rls_registry_next_to_the_promotion_tables() -> None:
    """Membership alone is not coverage, but an unregistered sibling is not detectible."""
    tables = policies.registry_tables()
    assert tables.count(EVIDENCE_TABLE) == 1

    specs = {spec.table: spec for spec in policies.CANONICAL_TABLE_POLICIES}
    spec = specs[EVIDENCE_TABLE]
    assert spec.owner_revision == REVISION_ID
    assert spec.tenant is not None and spec.tenant.column == "tenant_id"
    assert spec.tenant.chain == ()
    assert spec.cabinet is None and spec.project is None
    assert spec.tenant_restriction is None
    assert spec.trusted_command == "SELECT"
    assert spec.policy_names == (
        f"{EVIDENCE_TABLE}_tenant_isolation",
        f"{EVIDENCE_TABLE}_trusted_service_access",
    )

    # Declared inside the M04/M04E block - together with the promotion-evidence
    # family it belongs to - rather than appended at the end of the registry.
    #
    # The earlier form of this assertion pinned the entry to ``index(m04e_definition_
    # version_promotion_evidence) + 1``.  That slot now belongs to the four-table M04E
    # backfill ledger block of ``2026110315``, which was declared contiguously after
    # the same anchor by ``MIG-DDL-M04E-LEDGER-01``; two entries cannot both be the
    # immediate successor of one anchor.  The reviewed intent is "grouped with the
    # M04/M04E tables, not appended", so assert that instead of a neighbour index.
    order = [entry.table for entry in policies.CANONICAL_TABLE_POLICIES]
    position = order.index(EVIDENCE_TABLE)
    m04_family = [
        index
        for index, table in enumerate(order)
        if table.startswith(("scope_owner_", "scope_registry", "m04e_", "m04_scope_"))
    ]
    assert position == max(m04_family), (
        "the M04 scope evidence table must close the M04/M04E declaration block, "
        f"got {order[position]} at {position} of {m04_family}"
    )
    first_non_m04 = min(
        (index for index, table in enumerate(order) if not table.startswith(("scope_owner_", "scope_registry", "m04e_", "m04_scope_"))),
        default=len(order),
    )
    assert position + 1 == first_non_m04, (
        "the M04 scope evidence table must be declared before the M05/M06/runtime groups, "
        f"not appended after them (position {position}, first foreign entry {first_non_m04})"
    )
    assert position < len(order) - 1, "appending the entry at the end of the registry is not 'next to' its family"

    # The tenant predicate is deny-by-default and reads the transaction-local GUC.
    predicate = policies.tenant_predicate(spec)
    assert predicate == (
        f"{EVIDENCE_TABLE}.tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    )

    # The coverage report sees this table family and finds nothing uncovered.
    assert EVIDENCE_TABLE.startswith(policies.CANONICAL_TABLE_PREFIXES)
    assert policies.uncovered_relations(Base.metadata.tables.keys()) == ()


def test_policy_installation_is_deferred_to_the_next_registry_reapply() -> None:
    """Recorded gap: this revision creates the table, a later one installs its policy."""
    installers: list[str] = []
    for path in VERSIONS.glob("*.py"):
        if "upgrade_guard_sql" not in path.read_text(encoding="utf-8"):
            continue
        value = _revision_literal(path)
        if value is not None:
            installers.append(value)

    assert DOWN_REVISION_ID in installers, "the previous registry re-apply must stay in place"

    later = sorted(revision for revision in installers if revision > REVISION_ID)
    if not later:
        pytest.skip(
            "deferred by design: no registry re-apply revision after "
            f"{REVISION_ID} is authored yet, so "
            "test_policy_install_order::test_every_registered_table_is_policied_after_"
            "its_creating_revision reports this table as late until it lands"
        )

    assert min(later) > REVISION_ID


def test_model_records_the_deferred_gaps() -> None:
    """The trade-offs are review inputs, not silent omissions."""
    assert set(M04_SCOPE_EVIDENCE_DEFERRED) == {
        "rls_policy_installation",
        "registry_version_not_bumped",
        "scope_registry_tenant_equality",
        "in_place_append_only_enforcement",
        "writer_and_composition_root",
    }
    for reason in M04_SCOPE_EVIDENCE_DEFERRED.values():
        assert len(reason.split()) >= 8, "each deferred gap needs a reviewed justification"

    # The RLS follow-up is named precisely enough to be scheduled without re-deriving it.
    rls = M04_SCOPE_EVIDENCE_DEFERRED["rls_policy_installation"]
    assert "test_every_registered_table_is_policied_after_its_creating_revision" in rls
    assert "WAVE_TABLES" in rls
    assert "registry_version_not_bumped" in M04_SCOPE_EVIDENCE_DEFERRED
    assert "SECURITY_REVIEW_ID" in M04_SCOPE_EVIDENCE_DEFERRED["registry_version_not_bumped"]

    assert "only_a_ready_row_is_consumable_partition_evidence" in M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES
    assert "outcome_admits_blocked_quarantined_and_ready" in M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES
    assert "append_only_row_rejects_every_orm_update" in M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES
    assert (
        "partition_read_no_longer_refuses_for_a_missing_table"
        in M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES
    )
    assert len(set(M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES)) == len(
        M04_SCOPE_EVIDENCE_ACCEPTANCE_CASES
    )


def test_boundary_states_no_execution_and_no_positive_enablement() -> None:
    assert BOUNDARY == {
        "execution": "not run",
        "alembic_upgrade": False,
        "database": False,
        "positive_canonical_writes": False,
        "verification_freeze": True,
    }
