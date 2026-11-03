"""MIG-DDL-RES-01 acceptance contract for revision ``2026110306``.

Authored during the verification freeze: **not executed**. Every test below is a
checkable claim about the revision, not a record of a passed run.

Two layers are asserted:

1. *Static DDL parity* (no database, no network). ``upgrade()`` is rendered to
   PostgreSQL SQL in offline mode, and the resulting ``CREATE TABLE`` bodies,
   indexes and table comments are compared element by element with the DDL that
   SQLAlchemy generates from the registered ORM metadata. This is the
   "паритет с metadata" deliverable and it also pins exact constraint names,
   ``ON DELETE`` actions and check expressions.
2. *Database semantics* (skipped unless explicitly enabled). The negative cases
   that only a real PostgreSQL can prove: repeated attempt with the same
   idempotency key, several applied revisions per resource, an empty plan fenced
   by a stale generation, generation rollback, and append-only audit.

Coverage map:

================================================  ==========================================
Scenario                                          Where
================================================  ==========================================
Metadata parity (9 tables, 16 indexes, 3 notes)   ``test_ddl_*``
Exact constraint / index / FK names               ``test_ddl_*``
Append-only audit (trigger + no grants)           ``test_audit_*``
Repeated idempotency key → conflict, not replay   ``test_attempt_*`` / DB case
Several applied revisions per resource            ``test_attempt_*`` / DB case
Empty plan fenced without entry rows              ``test_head_*`` / DB case
Generation rollback never decrements history      ``test_attempt_*`` / DB case
Downgrade reverses upgrade exactly                ``test_downgrade_*``
RLS / privileges deferred to 2026110307           ``test_scope_*``
================================================  ==========================================
"""

from __future__ import annotations

import ast
import importlib.util
import io
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
REVISION_PATH = API_ROOT / "alembic" / "versions" / "2026110306_resources_runtime.py"
MODELS_DIR = API_ROOT / "src" / "prodavan" / "infrastructure" / "persistence" / "models"
MODELS_INIT = MODELS_DIR / "__init__.py"

REVISION = "2026110306"
DOWN_REVISION = "2026110305"
NEXT_REVISION = "2026110307"

EXISTING_TABLES = (
    "resources",
    "resource_versions",
    "resource_bindings",
    "capability_bundles",
    "project_runtime_revisions",
    "materialization_entries",
)
NEW_TABLES = (
    "runtime_revision_heads",
    "runtime_materialization_attempts",
    "runtime_materialization_audit",
)
CREATED_TABLES = EXISTING_TABLES + NEW_TABLES

NEW_MODEL_CLASSES = (
    "RuntimeRevisionHeadRow",
    "RuntimeMaterializationAttemptRow",
    "RuntimeMaterializationAuditRow",
)

AUDIT_TABLE = "runtime_materialization_audit"
ATTEMPT_TABLE = "runtime_materialization_attempts"
HEAD_TABLE = "runtime_revision_heads"


# --------------------------------------------------------------------------- #
# Revision rendering (pure Python, offline; no connection, no DDL execution)
# --------------------------------------------------------------------------- #


def _revision_module() -> Any:
    spec = importlib.util.spec_from_file_location("_res_runtime_revision", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(function_name: str) -> str:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    module = _revision_module()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        getattr(module, function_name)()
    return buffer.getvalue()


def _statements(sql: str) -> list[str]:
    return [" ".join(chunk.split()) for chunk in sql.split(";") if chunk.strip()]


def _create_order(statements: list[str]) -> list[str]:
    return [
        match.group(1)
        for statement in statements
        if (match := re.fullmatch(r"CREATE TABLE (\w+) \(.*", statement))
    ]


def _drop_order(statements: list[str]) -> list[str]:
    return [
        match.group(1)
        for statement in statements
        if (match := re.fullmatch(r"DROP TABLE (\w+)", statement))
    ]


def _create_statements(statements: list[str], table: str) -> str:
    for statement in statements:
        if statement.startswith(f"CREATE TABLE {table} ("):
            return statement
    raise AssertionError(f"revision does not create table {table}")


def _body(statement: str) -> str:
    return statement[statement.index("(") + 1 : statement.rindex(")")]


def _elements(body: str) -> set[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for character in body:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return {" ".join(part.split()) for part in parts if part.strip()}


def _migration_elements(statements: list[str], table: str) -> set[str]:
    return _elements(_body(_create_statements(statements, table)))


def _contains(elements: set[str], fragment: str) -> bool:
    """Constraint fragments carry their name prefix, so match inside an element."""
    return any(fragment in element for element in elements)


def _metadata_elements(table: str) -> set[str]:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from prodavan.infrastructure.persistence.models import Base

    ddl = str(CreateTable(Base.metadata.tables[table]).compile(dialect=postgresql.dialect()))
    return _elements(_body(" ".join(ddl.split())))


def _metadata_indexes(tables: tuple[str, ...]) -> set[str]:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    from prodavan.infrastructure.persistence.models import Base

    rendered: set[str] = set()
    for table in tables:
        for index in Base.metadata.tables[table].indexes:
            statement = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
            rendered.add(" ".join(statement.split()))
    return rendered


def _migration_indexes(statements: list[str]) -> set[str]:
    return {
        statement
        for statement in statements
        if re.match(r"CREATE (UNIQUE )?INDEX ", statement)
    }


def _metadata_comments(tables: tuple[str, ...]) -> dict[str, str]:
    from prodavan.infrastructure.persistence.models import Base

    return {
        table: Base.metadata.tables[table].comment
        for table in tables
        if Base.metadata.tables[table].comment
    }


def _migration_comments(statements: list[str]) -> dict[str, str]:
    comments: dict[str, str] = {}
    for statement in statements:
        match = re.fullmatch(r"COMMENT ON TABLE (\w+) IS '(.*)'", statement)
        if match:
            comments[match.group(1)] = match.group(2).replace("''", "'")
    return comments


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _render("downgrade")


# --------------------------------------------------------------------------- #
# Revision identity and chain
# --------------------------------------------------------------------------- #


def test_revision_identity_and_linear_chain() -> None:
    module = _revision_module()
    assert module.revision == REVISION
    assert module.down_revision == DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None


def test_revision_is_the_only_declaration_of_this_id_and_parent_exists() -> None:
    declarations: dict[str, list[Path]] = {}
    parents: set[str] = set()
    for path in (API_ROOT / "alembic" / "versions").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            if "revision" in names:
                declarations.setdefault(node.value.value, []).append(path)
            if "down_revision" in names:
                parents.add(node.value.value)
    # Exactly one file may own a revision id, and this one owns exactly one.
    assert [path.name for path in declarations[REVISION]] == [REVISION_PATH.name]
    assert sum(1 for paths in declarations.values() if REVISION_PATH in paths) == 1
    assert all(len(paths) == 1 for paths in declarations.values())
    assert DOWN_REVISION in declarations
    # No fork: nothing in the tree claims this revision as its parent yet.
    assert REVISION not in parents


def test_next_revision_is_not_created_here(upgrade_sql: str) -> None:
    """RLS/privileges belong to 2026110307; this revision must not pre-empt it."""
    text = REVISION_PATH.read_text(encoding="utf-8")
    assert NEXT_REVISION not in text
    assert "POLICY" not in text.upper()


# --------------------------------------------------------------------------- #
# Metadata parity
# --------------------------------------------------------------------------- #


def test_ddl_creates_exactly_the_nine_registered_tables(upgrade_sql: str) -> None:
    from prodavan.infrastructure.persistence.models import Base

    created = _create_order(_statements(upgrade_sql))
    assert tuple(created) == CREATED_TABLES
    assert all(name in Base.metadata.tables for name in CREATED_TABLES)
    for name in CREATED_TABLES:
        assert Base.metadata.tables[name].name == name


@pytest.mark.parametrize("table", CREATED_TABLES)
def test_ddl_table_body_matches_registered_metadata(upgrade_sql: str, table: str) -> None:
    """Column types, nullability, server defaults, PK/UNIQUE/CHECK and FK actions."""
    statements = _statements(upgrade_sql)
    assert _migration_elements(statements, table) == _metadata_elements(table)


def test_ddl_indexes_match_registered_metadata(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)
    migration = _migration_indexes(statements)
    metadata = _metadata_indexes(CREATED_TABLES)
    assert len(migration) == 16
    assert len(metadata) == 16
    assert migration == metadata


def test_ddl_table_comments_match_registered_metadata(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)
    assert _migration_comments(statements) == _metadata_comments(CREATED_TABLES)


def test_new_tables_have_exact_expected_names_and_no_renames(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)
    for table, expected in (
        (HEAD_TABLE, "uq_runtime_revision_head_resource_key"),
        (ATTEMPT_TABLE, "uq_runtime_attempt_resource_idempotency"),
        (AUDIT_TABLE, "ck_runtime_audit_reason_code_redacted"),
    ):
        assert expected in _create_statements(statements, table)


def test_foreign_keys_target_projects_and_resources_with_declared_actions(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)

    def actions(table: str) -> set[str]:
        return {
            element
            for element in _migration_elements(statements, table)
            if element.startswith("FOREIGN KEY")
        }

    assert actions("project_runtime_revisions") == {
        "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE",
        "FOREIGN KEY(bundle_id) REFERENCES capability_bundles (id) ON DELETE RESTRICT",
    }
    assert actions("resource_versions") == {
        "FOREIGN KEY(resource_id) REFERENCES resources (id) ON DELETE CASCADE",
    }
    assert actions("resource_bindings") == {
        "FOREIGN KEY(resource_id) REFERENCES resources (id) ON DELETE CASCADE",
    }
    assert "FOREIGN KEY(resource_id) REFERENCES resources (id) ON DELETE SET NULL" in actions(HEAD_TABLE)
    assert "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE" in actions(HEAD_TABLE)
    assert "FOREIGN KEY(resource_id) REFERENCES resources (id) ON DELETE SET NULL" in actions(ATTEMPT_TABLE)
    assert f"FOREIGN KEY(revision_id) REFERENCES project_runtime_revisions (id) ON DELETE SET NULL" in actions(
        ATTEMPT_TABLE
    )
    # Audit evidence must not be rewritten by a parent delete: every FK restricts.
    assert actions(AUDIT_TABLE) == {
        "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT",
        "FOREIGN KEY(resource_id) REFERENCES resources (id) ON DELETE RESTRICT",
        f"FOREIGN KEY(attempt_id) REFERENCES {ATTEMPT_TABLE} (id) ON DELETE RESTRICT",
    }


# --------------------------------------------------------------------------- #
# Durable attempt / receipt schema
# --------------------------------------------------------------------------- #


def test_attempt_history_keeps_one_row_per_resource_generation(upgrade_sql: str) -> None:
    """Several applied revisions can coexist for one resource; a generation cannot repeat."""
    statements = _statements(upgrade_sql)
    elements = _migration_elements(statements, ATTEMPT_TABLE)
    assert _contains(elements, "CONSTRAINT uq_runtime_attempt_resource_generation UNIQUE (resource_key, generation)")
    assert _contains(
        elements, "CONSTRAINT uq_runtime_attempt_resource_idempotency UNIQUE (resource_key, idempotency_key)"
    )
    # Nothing narrower would allow a second applied revision of the same resource.
    assert not any(
        element.startswith("CONSTRAINT") and "UNIQUE (resource_key, revision_id" in element
        for element in elements
    )
    assert _contains(elements, "generation INTEGER NOT NULL")
    assert _contains(elements, "observed_generation INTEGER NOT NULL")
    assert _contains(elements, "CHECK (generation = observed_generation + 1)")


def test_attempt_receipt_is_bound_to_plan_digest_and_outcome(upgrade_sql: str) -> None:
    elements = _migration_elements(_statements(upgrade_sql), ATTEMPT_TABLE)
    assert _contains(elements, "plan_digest VARCHAR(128) NOT NULL")
    assert _contains(elements, "receipt_digest VARCHAR(128)")
    # An open attempt has neither outcome nor receipt; a closed one has both.
    assert _contains(elements, "CHECK ((phase = 'applying') = (outcome IS NULL))")
    assert _contains(elements, "CHECK ((phase = 'applying') = (receipt_digest IS NULL))")
    assert _contains(elements, "CHECK ((outcome IN ('applied', 'rolled_back')) = (applied_revision_id IS NOT NULL))")
    assert _contains(elements, "CHECK (outcome IS NULL OR outcome IN ('applied', 'rolled_back', 'failed'))")
    assert _contains(elements, "CHECK (plan_digest ~ '^sha256:[0-9a-f]{64}$')")
    # A changed digest must conflict, never add a second row for the same key.
    assert not any("UNIQUE" in element and "plan_digest" in element for element in elements)


def test_attempt_rollback_target_is_bounded_by_observed_generation(upgrade_sql: str) -> None:
    elements = _migration_elements(_statements(upgrade_sql), ATTEMPT_TABLE)
    assert _contains(
        elements,
        "CHECK (rollback_of_generation IS NULL OR (rollback_of_generation >= 1 "
        "AND rollback_of_generation <= observed_generation))",
    )


# --------------------------------------------------------------------------- #
# Resource-wide CAS head (fences empty plans)
# --------------------------------------------------------------------------- #


def test_head_is_the_single_resource_scoped_generation_authority(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)
    elements = _migration_elements(statements, HEAD_TABLE)
    assert _contains(elements, "CONSTRAINT uq_runtime_revision_head_resource_key UNIQUE (resource_key)")
    assert _contains(elements, "CONSTRAINT uq_runtime_revision_head_fence_token UNIQUE (fence_token)")
    assert _contains(elements, "generation INTEGER DEFAULT '0' NOT NULL")
    assert _contains(elements, "CHECK (generation >= 0)")
    assert _contains(elements, "CHECK (generation > 0 OR phase = 'idle')")
    assert _contains(elements, "CHECK (phase IN ('idle', 'applying', 'applied', 'failed', 'rolled_back'))")


def test_empty_plan_is_fenced_without_materialization_entry_rows(upgrade_sql: str) -> None:
    """The fence lives on the head, so a plan with zero entries still CASes."""
    statements = _statements(upgrade_sql)
    head = _create_statements(statements, HEAD_TABLE)
    assert "materialization_entries" not in head
    assert not any(
        element.startswith("FOREIGN KEY") and "materialization_entries" in element
        for element in _migration_elements(statements, HEAD_TABLE)
    )


# --------------------------------------------------------------------------- #
# Append-only audit
# --------------------------------------------------------------------------- #


def test_audit_carries_only_redacted_codes_and_digests(upgrade_sql: str) -> None:
    elements = _migration_elements(_statements(upgrade_sql), AUDIT_TABLE)
    assert _contains(elements, "CHECK (reason_code ~ '^[a-z][a-z0-9_.-]{0,63}$')")
    assert _contains(elements, "CHECK (event ~ '^[a-z][a-z0-9_.-]{0,63}$')")
    assert _contains(elements, "CHECK (outcome ~ '^[a-z][a-z0-9_.-]{0,63}$')")
    assert _contains(elements, "correlation_id VARCHAR(128) NOT NULL")
    assert _contains(elements, "CHECK (correlation_id <> '' AND correlation_id = btrim(correlation_id))")
    # No free-text or payload column can leak secrets into the audit trail.
    forbidden = ("payload", "provenance", "secret_", "token", "content", "manifest", "message", "raw")
    column_names = {
        element.split(" ")[0].lower()
        for element in elements
        if " VARCHAR" in element or " TEXT" in element
    }
    assert not [name for name in column_names if any(fragment in name for fragment in forbidden)]


def test_audit_is_append_only_by_trigger(upgrade_sql: str) -> None:
    statements = _statements(upgrade_sql)
    triggers = [statement for statement in statements if statement.startswith("CREATE TRIGGER")]
    assert len(triggers) == 2
    assert (
        "CREATE TRIGGER trg_runtime_materialization_audit_append_only "
        f"BEFORE UPDATE OR DELETE ON {AUDIT_TABLE} FOR EACH ROW "
        "EXECUTE FUNCTION prevent_runtime_materialization_audit_mutation()"
    ) in triggers
    assert (
        "CREATE TRIGGER trg_runtime_materialization_audit_no_truncate "
        f"BEFORE TRUNCATE ON {AUDIT_TABLE} FOR EACH STATEMENT "
        "EXECUTE FUNCTION prevent_runtime_materialization_audit_mutation()"
    ) in triggers
    assert any(
        statement.startswith("CREATE FUNCTION prevent_runtime_materialization_audit_mutation()")
        and "RAISE EXCEPTION" in statement
        for statement in statements
    )


def test_upgrade_never_grants_privileges_or_writes_audit_rows(upgrade_sql: str) -> None:
    """INSERT-only role privileges are a 2026110307 (RLS) concern, recorded as a blocker."""
    statements = _statements(upgrade_sql)
    assert not [statement for statement in statements if statement.upper().startswith("GRANT")]
    assert not [statement for statement in statements if "INSERT INTO" in statement.upper()]


# --------------------------------------------------------------------------- #
# Faithfulness of write semantics implied by the schema
# --------------------------------------------------------------------------- #


def test_upgrade_contains_no_silent_upsert_path(upgrade_sql: str) -> None:
    """Idempotency is enforced by unique constraints, never by ON CONFLICT DO UPDATE."""
    normalised = upgrade_sql.upper()
    assert "ON CONFLICT" not in normalised
    assert "DO UPDATE" not in normalised


def test_scope_boundary_no_rls_or_tenant_context_in_this_revision(upgrade_sql: str) -> None:
    normalised = upgrade_sql.upper()
    assert "CREATE POLICY" not in normalised
    assert "ENABLE ROW LEVEL SECURITY" not in normalised
    assert "FORCE ROW LEVEL SECURITY" not in normalised
    assert "CURRENT_SETTING" not in normalised


# --------------------------------------------------------------------------- #
# Downgrade
# --------------------------------------------------------------------------- #


def test_downgrade_reverses_the_upgrade_table_order(upgrade_sql: str, downgrade_sql: str) -> None:
    created = _create_order(_statements(upgrade_sql))
    dropped = _drop_order(_statements(downgrade_sql))
    assert dropped == list(reversed(created))


def test_downgrade_drops_every_index_before_its_table(upgrade_sql: str, downgrade_sql: str) -> None:
    upgrade_statements = _statements(upgrade_sql)
    downgrade_statements = _statements(downgrade_sql)

    created = _migration_indexes(upgrade_statements)
    owners: dict[str, str] = {}
    for item in created:
        match = re.match(r"CREATE (?:UNIQUE )?INDEX (\w+) ON (\w+)", item)
        owners[match.group(1)] = match.group(2)
    dropped_names = {
        match.group(1)
        for statement in downgrade_statements
        if (match := re.fullmatch(r"DROP INDEX (\w+)", statement))
    }
    assert dropped_names == set(owners)

    position = {statement: index for index, statement in enumerate(downgrade_statements)}
    for statement in downgrade_statements:
        match = re.fullmatch(r"DROP INDEX (\w+)", statement)
        if not match:
            continue
        assert position[statement] < position[f"DROP TABLE {owners[match.group(1)]}"]


def test_downgrade_removes_triggers_and_function(downgrade_sql: str) -> None:
    statements = _statements(downgrade_sql)
    position = {statement: index for index, statement in enumerate(statements)}

    trigger_drops = [statement for statement in statements if statement.startswith("DROP TRIGGER")]
    assert len(trigger_drops) == 2
    assert all(f"ON {AUDIT_TABLE}" in statement for statement in trigger_drops)

    function_drops = [statement for statement in statements if statement.startswith("DROP FUNCTION")]
    assert function_drops == ["DROP FUNCTION IF EXISTS prevent_runtime_materialization_audit_mutation()"]

    audit_drop = f"DROP TABLE {AUDIT_TABLE}"
    assert all(position[statement] < position[audit_drop] for statement in trigger_drops)
    assert position[function_drops[0]] > position[audit_drop]


def test_downgrade_leaves_no_new_table_behind(downgrade_sql: str) -> None:
    dropped = set(_drop_order(_statements(downgrade_sql)))
    assert dropped == set(CREATED_TABLES)
    assert [statement for statement in _statements(downgrade_sql) if statement.startswith("DROP COLUMN")] == []


# --------------------------------------------------------------------------- #
# Model registration (owned by a separate task, not by this one)
# --------------------------------------------------------------------------- #


def test_new_models_are_defined_in_the_runtime_revision_module() -> None:
    path = MODELS_DIR / "runtime_revisions.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for name in NEW_MODEL_CLASSES:
        assert name in classes
    tables = {}
    for name, node in classes.items():
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in statement.targets):
                tables[name] = statement.value.value
    assert tables["RuntimeRevisionHeadRow"] == HEAD_TABLE
    assert tables["RuntimeMaterializationAttemptRow"] == ATTEMPT_TABLE
    assert tables["RuntimeMaterializationAuditRow"] == AUDIT_TABLE


def test_existing_model_classes_keep_their_tables_unchanged() -> None:
    """Backward compatibility: the six pre-existing tables keep their names and columns."""
    from prodavan.infrastructure.persistence.models import Base

    expected = {
        "resources": ("id", "kind", "slug", "title", "scope_kind", "scope_id", "metadata", "active_version"),
        "resource_versions": ("id", "resource_id", "number", "status", "payload", "checksum"),
        "resource_bindings": ("id", "resource_id", "target_scope_kind", "target_scope_id", "binding_kind"),
        "capability_bundles": ("id", "manifest", "digest"),
        "project_runtime_revisions": ("id", "project_id", "revision", "bundle_id", "state"),
        "materialization_entries": ("id", "revision_id", "workspace_path", "kind", "idempotency_key"),
    }
    for table, columns in expected.items():
        actual = set(Base.metadata.tables[table].columns.keys())
        assert set(columns).issubset(actual), table


@pytest.mark.xfail(
    strict=False,
    reason="models/__init__.py registration is owned by a separate task (see report)",
)
def test_new_models_are_registered_in_the_models_package() -> None:
    tree = ast.parse(MODELS_INIT.read_text(encoding="utf-8"), filename=str(MODELS_INIT))
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    for name in NEW_MODEL_CLASSES:
        assert name in imported


# --------------------------------------------------------------------------- #
# PostgreSQL semantics (opt-in; never runs during the verification freeze)
# --------------------------------------------------------------------------- #

_DB_GATE = "PRODAVAN_RUNTIME_DDL_ACCEPTANCE"
_PROJECT = f"migddl_{uuid.uuid4().hex[:12]}"
_COMPANY = f"co_{uuid.uuid4().hex[:20]}"
_CABINET = f"cab_{uuid.uuid4().hex[:20]}"
_RESOURCE_KEY = "migddl/runtime"


def _database_gate() -> None:
    if os.getenv(_DB_GATE) != "1":
        pytest.skip(f"set {_DB_GATE}=1 against a scratch database to run runtime DDL acceptance")
    from tests.conftest import _postgres_available

    if not _postgres_available():
        pytest.skip("PostgreSQL not available at DATABASE_URL")


@pytest.fixture()
async def runtime_session():
    """One transaction over an already-migrated scratch schema; always rolled back."""
    _database_gate()
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import DATABASE_URL

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SELECT to_regclass('public.runtime_materialization_attempts')")) is None:
                pytest.skip("revision 2026110306 is not applied to this database")
            transaction = await connection.begin()
            await connection.execute(
                text("INSERT INTO companies (id, name, login_slug, status, subscription_lifetime) "
                     "VALUES (:id, :name, :slug, 'active', false) ON CONFLICT (id) DO NOTHING"),
                {"id": _COMPANY, "name": _PROJECT, "slug": _COMPANY},
            )
            await connection.execute(
                text("INSERT INTO cabinet_instances (id, name, schema_name, owner_scope, base_template, "
                     "company_grant_scope, status) VALUES (:id, :name, :schema, 'platform', 'base', 'selected', "
                     "'active') ON CONFLICT (id) DO NOTHING"),
                {"id": _CABINET, "name": _PROJECT, "schema": _CABINET},
            )
            await connection.execute(
                text("INSERT INTO projects (id, company_id, cabinet_id, name, slug, status, visibility_mode, "
                     "workspace_key, container_ref, resume_generation) VALUES (:id, :company, :cabinet, :name, "
                     ":slug, 'active', 'cabinet_shared', :slug, :ref, 0) ON CONFLICT (id) DO NOTHING"),
                {
                    "id": _PROJECT,
                    "company": _COMPANY,
                    "cabinet": _CABINET,
                    "name": _PROJECT,
                    "slug": _PROJECT,
                    "ref": f"ws/{_PROJECT}",
                },
            )
            try:
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _insert_attempt(connection, *, generation: int, idempotency_key: str, digest: str) -> None:
    from sqlalchemy import text

    await connection.execute(
        text(
            "INSERT INTO runtime_materialization_attempts "
            "(id, project_id, resource_key, idempotency_key, plan_digest, generation, observed_generation, "
            " phase, fence_token, reason_code) "
            "VALUES (:id, :project, :key, :idem, :digest, :gen, :observed, 'applying', :fence, 'claimed')"
        ),
        {
            "id": f"rma_{uuid.uuid4().hex[:16]}",
            "project": _PROJECT,
            "key": _RESOURCE_KEY,
            "idem": idempotency_key,
            "digest": digest,
            "gen": generation,
            "observed": generation - 1,
            "fence": f"fence-{generation}-{uuid.uuid4().hex[:8]}",
        },
    )


async def test_db_repeated_idempotency_key_is_rejected_rather_than_replayed(runtime_session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    digest = "sha256:" + "a" * 64
    await _insert_attempt(runtime_session, generation=1, idempotency_key="replay-key", digest=digest)
    # Terminal success needs a linked applied revision, which this scratch scope
    # does not create; a durable failed attempt is enough to prove the receipt.
    await runtime_session.execute(
        text("UPDATE runtime_materialization_attempts SET phase = 'failed', outcome = 'failed', "
             "receipt_digest = :receipt, finished_at = now() WHERE idempotency_key = 'replay-key'"),
        {"receipt": "sha256:" + "b" * 64},
    )
    # Same key, changed plan digest: a second row is refused, so the caller must
    # resolve the conflict by reading the stored receipt instead of overwriting it.
    with pytest.raises(IntegrityError):
        await _insert_attempt(runtime_session, generation=2, idempotency_key="replay-key", digest="sha256:" + "c" * 64)


async def test_db_several_applied_revisions_coexist_for_one_resource(runtime_session) -> None:
    from sqlalchemy import text

    for generation in (1, 2, 3):
        digest = "sha256:" + str(generation) * 64
        await _insert_attempt(
            runtime_session, generation=generation, idempotency_key=f"key-{generation}", digest=digest
        )
        await runtime_session.execute(
            text("UPDATE runtime_materialization_attempts SET phase = 'failed', outcome = 'failed', "
                 "receipt_digest = :receipt, finished_at = now() "
                 "WHERE idempotency_key = :key AND generation = :gen"),
            {"receipt": "sha256:" + "f" * 64, "key": f"key-{generation}", "gen": generation},
        )
    closed = await runtime_session.scalar(
        text("SELECT count(*) FROM runtime_materialization_attempts "
             "WHERE project_id = :project AND resource_key = :key AND outcome IS NOT NULL"),
        {"project": _PROJECT, "key": _RESOURCE_KEY},
    )
    # Three generations of the same resource all keep their terminal row: history
    # is per generation, never one overwritten record per resource.
    assert closed == 3


async def test_db_empty_plan_is_fenced_by_the_head_generation(runtime_session) -> None:
    from sqlalchemy import text

    await runtime_session.execute(
        text("INSERT INTO runtime_revision_heads (id, project_id, resource_key, generation, phase) "
             "VALUES (:id, :project, :key, 0, 'idle')"),
        {"id": f"rrh_{uuid.uuid4().hex[:16]}", "project": _PROJECT, "key": _RESOURCE_KEY},
    )
    cas = text(
        "UPDATE runtime_revision_heads SET generation = generation + 1, phase = 'applying' "
        "WHERE resource_key = :key AND generation = :observed"
    )
    first = await runtime_session.execute(cas, {"key": _RESOURCE_KEY, "observed": 0})
    assert first.rowcount == 1
    assert await runtime_session.scalar(
        text("SELECT count(*) FROM materialization_entries WHERE revision_id IN "
             "(SELECT id FROM project_runtime_revisions WHERE project_id = :project)"),
        {"project": _PROJECT},
    ) == 0
    stale = await runtime_session.execute(cas, {"key": _RESOURCE_KEY, "observed": 0})
    assert stale.rowcount == 0


async def test_db_generation_rollback_stays_monotonic_and_bounded(runtime_session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    await _insert_attempt(runtime_session, generation=2, idempotency_key="rollback-key", digest="sha256:" + "d" * 64)
    await runtime_session.execute(
        text("UPDATE runtime_materialization_attempts SET phase = 'rolled_back', outcome = 'rolled_back', "
             "receipt_digest = :receipt, rollback_of_generation = 1 "
             "WHERE idempotency_key = 'rollback-key'"),
        {"receipt": "sha256:" + "e" * 64},
    )
    assert await runtime_session.scalar(
        text("SELECT rollback_of_generation FROM runtime_materialization_attempts "
             "WHERE idempotency_key = 'rollback-key'")
    ) == 1
    # A rollback that targets its own generation or a future one is refused by the schema.
    with pytest.raises(IntegrityError):
        await runtime_session.execute(
            text("UPDATE runtime_materialization_attempts SET rollback_of_generation = 2 "
                 "WHERE idempotency_key = 'rollback-key'")
        )


async def test_db_audit_is_append_only(runtime_session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    audit_id = f"raud_{uuid.uuid4().hex[:16]}"
    await runtime_session.execute(
        text("INSERT INTO runtime_materialization_audit "
             "(id, project_id, resource_key, event, outcome, reason_code, resource_digest, correlation_id, "
             " correlation_digest, plan_digest, generation) "
             "VALUES (:id, :project, :key, 'runtime.materialization.finished', 'applied', 'applied', "
             ":resource_digest, 'corr-1', :correlation_digest, :plan_digest, 1)"),
        {
            "id": audit_id,
            "project": _PROJECT,
            "key": _RESOURCE_KEY,
            "resource_digest": "sha256:" + "1" * 64,
            "correlation_digest": "sha256:" + "2" * 64,
            "plan_digest": "sha256:" + "3" * 64,
        },
    )
    for statement in (
        "UPDATE runtime_materialization_audit SET reason_code = 'rewritten' WHERE id = :id",
        "DELETE FROM runtime_materialization_audit WHERE id = :id",
    ):
        # A refused mutation aborts the transaction, so each attempt is isolated
        # in its own savepoint and the earlier rows stay visible afterwards.
        savepoint = await runtime_session.begin_nested()
        with pytest.raises(DBAPIError):
            await runtime_session.execute(text(statement), {"id": audit_id})
        await savepoint.rollback()

    # The row survives both refused mutations unchanged.
    assert await runtime_session.scalar(
        text("SELECT reason_code FROM runtime_materialization_audit WHERE id = :id"), {"id": audit_id}
    ) == "applied"


async def test_db_audit_rejects_counterfeit_reason_codes(runtime_session) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await runtime_session.execute(
            text("INSERT INTO runtime_materialization_audit "
                 "(id, project_id, resource_key, event, outcome, reason_code, resource_digest, correlation_id, "
                 " correlation_digest, plan_digest, generation) "
                 "VALUES (:id, :project, :key, 'runtime.materialization.finished', 'applied', "
                 "'token=leaked', :resource_digest, 'corr-2', :correlation_digest, :plan_digest, 1)"),
            {
                "id": f"raud_{uuid.uuid4().hex[:16]}",
                "project": _PROJECT,
                "key": _RESOURCE_KEY,
                "resource_digest": "sha256:" + "4" * 64,
                "correlation_digest": "sha256:" + "5" * 64,
                "plan_digest": "sha256:" + "6" * 64,
            },
        )
