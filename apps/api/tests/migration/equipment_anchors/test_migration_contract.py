"""Acceptance contract for revision ``2026110319`` (``equipment_v2`` anchors).

What this module proves, and why each item matters:

1. **Chain shape.** The revision sits directly on ``2026110318``, is the only file
   claiming its id, and the tree still has exactly one head.  (The id was taken:
   ``2026110318_rls_policy_rename`` already claimed ``2026110318`` on the same
   parent, so drafting this work at that id created a second head.  The pin below is
   what stops that from recurring silently.)
2. **Model parity.** Every ``ADD COLUMN`` / ``CREATE TABLE`` / index the revision
   declares is compared with ``Base.metadata`` object by object, so the model stays
   the schema source of truth and ``alembic revision --autogenerate`` stays empty.
3. **Nullable compatibility.** The new columns are ``NULL``-able, declare no server
   default, and are not part of any NOT NULL transition: a row written before the
   backfill keeps working, and nothing invents a project for it.
4. **Registry parity.** Both association tables are registered in
   ``CANONICAL_TABLE_POLICIES`` with ``owner_revision`` equal to this revision, the
   three anchored tables read their anchor columns *directly* instead of through a
   chain, ``uncovered_relations()`` is empty, and every predicate stays
   deny-by-default.
5. **Install order.** This revision calls ``upgrade_guard_sql`` (it is the registry
   re-apply for the tables it creates) and it is the newest installer of the chain,
   which is what the gate in ``tests/migration/rls/test_policy_install_order.py``
   checks globally.
6. **Downgrade symmetry.** Every object created is dropped, in reverse order, and the
   two association tables' policies are the only policies withdrawn.  The anchored
   tables get their pre-anchor (tenant-only) policy back, because a downgrade that
   left them unpolicied would be a weakening rather than a restore.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlalchemy as sa

from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.rls import policies

from .anchors_fixtures import (
    REVISION_ID,
    REVISION_PATH,
    VERSIONS_DIR,
    index_signature,
    load_revision,
    replay,
    revision_header,
    table_signature,
)

#: The three tables that gain the anchors, and the two the revision creates.
ANCHORED_TABLES = (
    "equipment_v2_ingestion_runs",
    "equipment_v2_requests",
    "equipment_v2_tenders",
)
ANCHOR_COLUMNS = ("project_id", "cabinet_id")
ASSOCIATION_TABLES = (
    "equipment_v2_candidate_group_offers",
    "equipment_v2_tender_candidate_groups",
)


@pytest.fixture(scope="module")
def upgrade():  # noqa: ANN201 - Replay dataclass
    return replay("upgrade")


@pytest.fixture(scope="module")
def downgrade():  # noqa: ANN201 - Replay dataclass
    return replay("downgrade")


def _model(table_name: str) -> sa.Table:
    return Base.metadata.tables[table_name]


def _spec(table_name: str) -> policies.TablePolicySpec:
    return next(spec for spec in policies.CANONICAL_TABLE_POLICIES if spec.table == table_name)


# --------------------------------------------------------------------------- #
# 1. chain shape
# --------------------------------------------------------------------------- #


def test_revision_is_chained_after_the_policy_rename_without_a_second_head() -> None:
    module = load_revision()

    assert module.revision == REVISION_ID
    assert module.down_revision == "2026110318"
    assert module.branch_labels is None
    assert module.depends_on is None

    header = revision_header()
    assert header["revision"] == REVISION_ID
    assert header["down_revision"] == "2026110318"

    claimants = sorted(path.name for path in VERSIONS_DIR.glob(f"{REVISION_ID}_*.py"))
    assert claimants == [REVISION_PATH.name], f"revision id {REVISION_ID} is claimed by {claimants}"

    # Exactly one head for the whole tree: a fork anywhere would leave more.
    headers: dict[str, str | None] = {}
    for path in VERSIONS_DIR.glob("*.py"):
        values: dict[str, object] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"revision", "down_revision"}
            ):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        revision = values.get("revision")
        if isinstance(revision, str):
            down = values.get("down_revision")
            headers[revision] = down if isinstance(down, str) or down is None else None
    referenced = {down for down in headers.values() if down}
    heads = sorted(set(headers) - referenced)
    assert heads == [REVISION_ID], f"expected this revision to be the single head, got {heads}"


def test_the_revision_touches_only_equipment_tables() -> None:
    """A scope revision for one module must not quietly alter another module's tables."""

    record = replay("upgrade")
    touched = {column.table for column in record.added_columns} | set(record.tables)
    assert touched == set(ANCHORED_TABLES) | set(ASSOCIATION_TABLES), sorted(touched)

    # No data statement, no trigger, no grant, no ``ALTER`` outside the anchors:
    # the only raw SQL is the RLS registry pass.
    for statement in record.statements:
        assert "upgrade_guard_sql" not in statement  # it was executed, not emitted
        assert "ENABLE ROW LEVEL SECURITY" in statement or "CREATE POLICY" in statement
        assert "DROP TABLE" not in statement.upper()


# --------------------------------------------------------------------------- #
# 2. model <-> revision parity
# --------------------------------------------------------------------------- #


def test_added_columns_match_the_model(upgrade) -> None:  # noqa: ANN001
    added = {(item.table, item.column.name): item.column for item in upgrade.added_columns}

    expected = {(table, column) for table in ANCHORED_TABLES for column in ANCHOR_COLUMNS}
    assert set(added) == expected, sorted(set(added) ^ expected)

    for (table_name, column_name), declared in sorted(added.items()):
        model_column = _model(table_name).c[column_name]
        assert _signature(declared) == _signature(model_column), f"{table_name}.{column_name}"


def _signature(column: sa.Column) -> tuple[object, ...]:
    return (str(column.type), bool(column.nullable), column.server_default)


def test_added_columns_carry_their_restrict_foreign_keys(upgrade) -> None:  # noqa: ANN001
    created = {(key.table, key.local, key.referent, key.remote, key.ondelete) for key in upgrade.foreign_keys}

    expected = set()
    for table_name in ANCHORED_TABLES:
        for column_name in ANCHOR_COLUMNS:
            target = {
                "project_id": "projects",
                "cabinet_id": "cabinet_instances",
            }[column_name]
            expected.add((table_name, (column_name,), target, ("id",), "RESTRICT"))

    assert created == expected, sorted(created ^ expected)

    # The model must declare the very same keys, or the revision drifts from metadata.
    for table_name in ANCHORED_TABLES:
        for constraint in _model(table_name).foreign_key_constraints:
            if set(constraint.column_keys) & set(ANCHOR_COLUMNS):
                assert (constraint.ondelete or "").upper() == "RESTRICT", table_name


def test_association_tables_match_the_model_exactly(upgrade) -> None:  # noqa: ANN001
    assert tuple(upgrade.table_order) == ASSOCIATION_TABLES

    for table_name in ASSOCIATION_TABLES:
        declared = table_signature(upgrade.tables[table_name])
        modelled = table_signature(_model(table_name))

        # Constraint *names* are excluded here on purpose and checked separately: the
        # revisions in this chain name every key explicitly while the declarative
        # models leave most of them unnamed, and the parity contract is about shape.
        # Comparing names would fail on a difference that has no schema meaning.
        for key in ("columns", "primary_key", "unique", "checks"):
            assert declared[key] == modelled[key], f"{table_name}: {key}"

        def shapes(signature: dict[str, Any]) -> set[tuple[Any, ...]]:
            return {(local, remote, ondelete) for _name, local, remote, ondelete in signature["foreign_keys"]}

        assert shapes(declared) == shapes(modelled), table_name

    # A key the model *does* name must carry that very name in the migration; keys the
    # model leaves unnamed get PostgreSQL's derived name, which is not a drift.
    for table_name in ASSOCIATION_TABLES:
        declared_names = {
            name for name, _local, _remote, _ondelete in table_signature(upgrade.tables[table_name])["foreign_keys"]
        }
        model_names = {
            constraint.name
            for constraint in _model(table_name).foreign_key_constraints
            if constraint.name is not None
        }
        assert model_names <= declared_names, f"{table_name}: {sorted(model_names - declared_names)}"


def test_association_tables_are_created_after_their_parents(upgrade) -> None:  # noqa: ANN001
    created: list[str] = []
    for table_name in upgrade.table_order:
        for constraint in upgrade.tables[table_name].foreign_key_constraints:
            for element in constraint.elements:
                parent = element.target_fullname.split(".", 1)[0]
                assert parent in created or parent in Base.metadata.tables, (
                    f"{table_name} references {parent} before it exists"
                )
        created.append(table_name)


def test_indexes_match_the_model(upgrade) -> None:  # noqa: ANN001
    declared = {(name, table) for name, table, _columns in upgrade.indexes}
    assert declared == {
        ("ix_equipment_v2_ingestion_project_created", "equipment_v2_ingestion_runs"),
        ("ix_equipment_v2_ingestion_cabinet_id", "equipment_v2_ingestion_runs"),
        ("ix_equipment_v2_requests_project_status_created", "equipment_v2_requests"),
        ("ix_equipment_v2_requests_cabinet_id", "equipment_v2_requests"),
        ("ix_equipment_v2_tenders_project_status", "equipment_v2_tenders"),
        ("ix_equipment_v2_tenders_cabinet_id", "equipment_v2_tenders"),
        ("ix_ecg_offer_by_offer", "equipment_v2_candidate_group_offers"),
        ("ix_ecg_offer_project", "equipment_v2_candidate_group_offers"),
        ("ix_etender_group_by_group", "equipment_v2_tender_candidate_groups"),
        ("ix_etender_group_project", "equipment_v2_tender_candidate_groups"),
    }

    # Every index this revision creates is declared on the model with the same
    # expressions (including ``created_at DESC``), so autogenerate sees no diff.
    for name, table_name, columns in upgrade.indexes:
        model_index = next((index for index in _model(table_name).indexes if index.name == name), None)
        assert model_index is not None, f"{name} is created but not declared on {table_name}"
        assert index_signature(model_index) == (name, columns, False, None), name


def test_deferred_ordinal_uniqueness_is_deferrable(upgrade) -> None:  # noqa: ANN001
    """A reorder exchanges two ordinals in one statement; an immediate key collides."""

    for table_name, key in (
        ("equipment_v2_candidate_group_offers", "uq_ecg_offer_ordinal"),
        ("equipment_v2_tender_candidate_groups", "uq_etender_group_ordinal"),
    ):
        unique = next(
            constraint
            for constraint in upgrade.tables[table_name].constraints
            if isinstance(constraint, sa.UniqueConstraint) and constraint.name == key
        )
        assert unique.deferrable is True
        assert unique.initially == "DEFERRED"


# --------------------------------------------------------------------------- #
# 3. nullable compatibility
# --------------------------------------------------------------------------- #


def test_new_columns_are_nullable_without_a_server_default(upgrade) -> None:  # noqa: ANN001
    """Existing rows must keep working: no NOT NULL, no invented project."""

    for item in upgrade.added_columns:
        assert item.column.nullable is True, f"{item.table}.{item.column.name}"
        assert item.column.server_default is None, f"{item.table}.{item.column.name}"

        model_column = _model(item.table).c[item.column.name]
        assert model_column.nullable is True
        assert model_column.server_default is None


def test_the_revision_never_makes_a_column_not_null(upgrade) -> None:  # noqa: ANN001
    """The NOT NULL step is the *post-backfill* revision, not this one (design 2.1).

    Scoped to the columns this revision adds on the pre-existing tables: the
    association tables it *creates* legitimately have ``NOT NULL`` keys, but no
    table that already had rows may gain a mandatory column.
    """

    source = REVISION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(REVISION_PATH))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    }
    # ``ALTER COLUMN ... SET NOT NULL`` is the only way to tighten an existing column,
    # and this revision must not do it.
    assert "alter_column" not in calls

    for item in upgrade.added_columns:
        assert item.column.nullable is True, f"{item.table}.{item.column.name} would break existing rows"
        assert item.column.server_default is None, f"{item.table}.{item.column.name} invents a value"


def test_unanchored_rows_stay_visible_to_their_tenant_only() -> None:
    """``allows_null`` is not a weakening - the tenant clause is still mandatory.

    A row written before the backfill has ``project_id IS NULL``.  It must remain
    reachable by its own tenant (that is what "compatible" means here) and must stay
    invisible to every other tenant and to a session with no tenant context at all.

    The anchored case is asymmetric on purpose: ``project_id IS NULL OR project_id =
    current_setting(...)`` means an anchored row still requires the *matching* project
    GUC, so a session with no project in its context does not see it.  That is the
    correct fail-closed reading - "no project context" must not silently widen to
    "every project of the tenant" - and the earlier draft of this test asserted the
    opposite, which modelled a context the policy never produces.
    """

    from tests.migration.rls.acceptance_fixtures import (
        PROJECT_A,
        PROJECT_B,
        TENANT_A,
        TENANT_B,
        scope_visible,
    )

    own_tenant = {"app.tenant_id": TENANT_A, "app.cabinet_id": None, "app.project_id": None}
    own_project = {"app.tenant_id": TENANT_A, "app.cabinet_id": None, "app.project_id": PROJECT_A}
    other_tenant = {"app.tenant_id": TENANT_B, "app.cabinet_id": None, "app.project_id": None}
    no_context = {"app.tenant_id": None, "app.cabinet_id": None, "app.project_id": None}

    for table_name in ANCHORED_TABLES:
        spec = _spec(table_name)

        legacy_row = {"tenant_id": TENANT_A, "project_id": None, "cabinet_id": None}
        tables = {table_name: [legacy_row]}
        assert scope_visible(spec, legacy_row, tables, own_tenant) is True, table_name
        assert scope_visible(spec, legacy_row, tables, own_project) is True, table_name
        assert scope_visible(spec, legacy_row, tables, other_tenant) is False, table_name
        assert scope_visible(spec, legacy_row, tables, no_context) is False, table_name

        # A project-anchored row is narrowed to its project, never widened.
        anchored = {"tenant_id": TENANT_A, "project_id": PROJECT_A, "cabinet_id": None}
        tables[table_name] = [anchored]
        wrong_project = {"app.tenant_id": TENANT_A, "app.cabinet_id": None, "app.project_id": PROJECT_B}
        assert scope_visible(spec, anchored, tables, own_project) is True, table_name
        assert scope_visible(spec, anchored, tables, wrong_project) is False, table_name
        assert scope_visible(spec, anchored, tables, own_tenant) is False, table_name
        assert scope_visible(spec, anchored, tables, other_tenant) is False, table_name


def test_anchor_predicates_are_direct_columns_not_chains() -> None:
    """The point of the task: two or three hops become one column read."""

    for table_name in ANCHORED_TABLES:
        spec = _spec(table_name)
        assert spec.project is not None and spec.project.column == "project_id", table_name
        assert spec.cabinet is not None and spec.cabinet.column == "cabinet_id", table_name
        assert spec.project.chain == () and spec.cabinet.chain == ()

        predicate = policies.tenant_predicate(spec)
        assert "EXISTS (SELECT 1 FROM" not in predicate, table_name
        assert f"{table_name}.project_id IS NULL OR {table_name}.project_id =" in predicate

    # The catalog chain keeps its inherited tenant on purpose: snapshots and offers
    # have no tenant column, and the shared catalog has no project of its own.
    assert _spec("equipment_v2_catalog_snapshots").tenant.column is None
    assert _spec("equipment_v2_catalog_offers").tenant.column is None


# --------------------------------------------------------------------------- #
# 4. registry parity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table_name", ASSOCIATION_TABLES)
def test_new_tables_are_registered_with_this_revision(table_name: str) -> None:
    spec = _spec(table_name)

    assert spec.owner_revision == REVISION_ID
    assert spec.tenant is not None and spec.tenant.column == "tenant_id"
    assert spec.project is not None and spec.project.column == "project_id"
    assert spec.cabinet is not None and spec.cabinet.column == "cabinet_id"
    assert spec.trusted_command == "ALL"
    assert table_name in Base.metadata.tables


def test_every_table_the_revision_creates_is_registered() -> None:
    created = {spec.table for spec in policies.CANONICAL_TABLE_POLICIES if spec.owner_revision == REVISION_ID}

    assert created == set(ASSOCIATION_TABLES), sorted(created)


def test_model_metadata_has_no_uncovered_canonical_table() -> None:
    assert policies.uncovered_relations(Base.metadata.tables.keys()) == ()


def test_registry_is_internally_consistent() -> None:
    policies.validate_registry()

    for spec in policies.CANONICAL_TABLE_POLICIES:
        predicate = policies.tenant_predicate(spec)
        if spec.tenant is None:
            assert predicate == "false", spec.table
        else:
            # A NULL anchor can never satisfy the tenant clause, so the predicate is
            # deny-by-default even where a scope column is optional.
            assert predicate != "true", spec.table
            assert policies.TENANT_SETTING in predicate, spec.table


def test_scope_columns_resolve_on_the_model() -> None:
    """A direct anchor the model does not declare would make the policy uninstallable."""

    for spec in policies.CANONICAL_TABLE_POLICIES:
        model = Base.metadata.tables[spec.table]
        for column in policies.scope_columns(spec):
            assert column in model.c, f"{spec.table}.{column} is policied but not declared"


# --------------------------------------------------------------------------- #
# 5. install order
# --------------------------------------------------------------------------- #


def test_this_revision_is_a_policy_installer_for_its_own_tables() -> None:
    source = REVISION_PATH.read_text(encoding="utf-8")
    assert "upgrade_guard_sql" in source, (
        "a table-creating revision must re-apply the registry in the same step; "
        "leaving the policy to 'the next pass' is the defect the install-order gate exists for"
    )

    module = load_revision()
    assert module._POLICIES_ADDED_HERE == frozenset({REVISION_ID})


def test_this_revision_is_the_newest_policy_installer_of_the_chain() -> None:
    """The local half of ``tests/migration/rls/test_policy_install_order.py``."""

    installers: list[tuple[str, Path]] = []
    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "upgrade_guard_sql" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "revision"
            ):
                installers.append((ast.literal_eval(node.value), path))

    newest = max(value for value, _path in installers)
    assert newest == REVISION_ID, f"the newest registry installer is {newest}, expected {REVISION_ID}"

    # ...and every registered table's owner is at or before it.
    late = sorted(
        spec.table for spec in policies.CANONICAL_TABLE_POLICIES if spec.owner_revision > REVISION_ID
    )
    assert late == [], late


def test_the_guard_defers_a_table_whose_scope_columns_do_not_exist_yet() -> None:
    """Why the guard changed: a predicate may name a later revision's column.

    ``2026110307`` walks the *current* registry, and the equipment rows it policies
    were created without anchors.  Without the column guard that installer would emit
    ``... project_id = current_setting(...)`` against a table that has no such column
    and abort ``upgrade head``.  The guard must therefore skip such a block, report it
    like an absent relation, and leave the policy to the revision that adds the column.
    """

    spec = _spec("equipment_v2_requests")
    sql = policies.upgrade_guard_sql(spec)

    assert "pg_attribute" in sql
    assert "RAISE NOTICE" in sql
    assert "RETURN;" in sql
    # The skip is reported before any policy statement.
    assert sql.index("pg_attribute") < sql.index("CREATE POLICY")

    # ``downgrade`` never defers: ``DROP POLICY`` reads no column.
    drop_sql = policies.downgrade_guard_sql(spec)
    assert "pg_attribute" not in drop_sql

    # A table with no scope columns at all keeps the original, column-free guard.
    simple = next(
        spec for spec in policies.CANONICAL_TABLE_POLICIES if policies.scope_columns(spec) == ("tenant_id",)
    )
    assert "pg_attribute" in policies.upgrade_guard_sql(simple)


def test_missing_scope_columns_matches_the_guard() -> None:
    spec = _spec("equipment_v2_requests")
    assert policies.scope_columns(spec) == ("tenant_id", "cabinet_id", "project_id")
    # The helper reads the catalog; with a fake bind it reports nothing missing,
    # which is what the installer's verification relies on.
    from .anchors_fixtures import _FakeBind

    assert policies.missing_scope_columns(_FakeBind(), spec) == ()


# --------------------------------------------------------------------------- #
# 6. downgrade symmetry
# --------------------------------------------------------------------------- #


def test_downgrade_drops_everything_it_created_in_reverse_order(upgrade, downgrade) -> None:  # noqa: ANN001
    assert [table for table, _column in downgrade.dropped_columns] == [
        table for table, _column in reversed([(item.table, item.column.name) for item in upgrade.added_columns])
    ]

    assert [name for name, _table, _type in downgrade.dropped_constraints] == [
        key.name for key in reversed(upgrade.foreign_keys)
    ]
    assert {table for _name, table, _type in downgrade.dropped_constraints} == set(ANCHORED_TABLES)

    assert downgrade.dropped_indexes == list(reversed([(name, table) for name, table, _ in upgrade.indexes]))
    assert downgrade.dropped_tables == list(reversed(upgrade.table_order))


def test_downgrade_withdraws_only_the_policies_it_introduced(upgrade, downgrade) -> None:  # noqa: ANN001
    installed = _policy_names_in(upgrade.statements, "CREATE")
    dropped = _policy_names_in(downgrade.statements, "DROP")

    # The two association tables' pairs are installed by this revision and withdrawn
    # again by its downgrade...
    owned = {policy for table in ASSOCIATION_TABLES for policy in _spec(table).policy_names}
    assert owned <= installed
    assert owned <= dropped
    # ...while no other revision's table loses its policy, apart from the three
    # anchored tables that get their pre-anchor pair restored in the same step.
    foreign = {
        policy
        for spec in policies.CANONICAL_TABLE_POLICIES
        if spec.owner_revision != REVISION_ID
        for policy in spec.policy_names
    }
    assert not (dropped & foreign) - _pre_anchor_names()


def test_downgrade_restores_the_pre_anchor_policies(upgrade, downgrade) -> None:  # noqa: ANN001
    """Leaving the anchored tables unpolicied after a downgrade would be a weakening."""

    module = load_revision()
    restored = {spec.table for spec in module.PRE_ANCHOR_POLICIES}
    assert restored == set(ANCHORED_TABLES)

    recreated = _policy_names_in(downgrade.statements, "CREATE")
    for table in ANCHORED_TABLES:
        for name in _spec(table).policy_names:
            assert name in recreated, f"{name} is not restored by the downgrade"

    # The restored predicate is the tenant-only one the schema supports after the
    # columns are gone: no reference to a dropped column anywhere.
    carried = {name for table in ANCHORED_TABLES for name in _spec(table).policy_names}
    for statement in downgrade.statements:
        if _policy_names_in([statement], "CREATE") & carried:
            assert "project_id" not in statement
            assert "cabinet_id" not in statement


def _policy_names_in(statements: list[str], verb: str) -> set[str]:
    """Names of the ``CREATE POLICY`` / ``DROP POLICY IF EXISTS`` statements."""

    import re

    return set(re.findall(rf"{verb} POLICY (?:IF EXISTS )?(\w+) ON ", "\n".join(statements)))


def _pre_anchor_names() -> set[str]:
    return {policy for table in ANCHORED_TABLES for policy in _spec(table).policy_names}
