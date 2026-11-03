"""Static migration contract for revision 2026110303 (MIG-DDL-M05-01).

Everything here is reviewed during the migration verification freeze and is
intentionally **not executed** in this worktree: the checks read the revision
source through ``ast`` plus the ORM metadata through SQLAlchemy, and never a live
database, an Alembic runtime, or rendered DDL.

Covered contract:

* the revision is pinned to the wave chain (``down_revision = "2026110302"``);
* the three M05 tables are created with exactly the ORM columns, types,
  nullability, defaults, checks, uniques, foreign keys and indexes;
* the cyclic registry/instance relation is realized with named, deferrable
  ``use_alter`` foreign keys added after both tables exist, and removed before any
  table is dropped on downgrade;
* every foreign-key target owned by an earlier revision is a real unique key in
  the metadata and is verified (or, for the M05-only registry anchors, created
  idempotently) by the revision;
* every negative acceptance case named by the M05 model module has named
  enforcement objects in both the metadata and the revision DDL.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionFoundationRow,
    DynamicAppDefinitionVersionFoundationRow,
)
from prodavan.infrastructure.persistence.models.owner_registry import ScopeRegistryRow
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05_ACCEPTANCE_CASES,
    M05PluginInstancePromotionEvidenceRow,
    PluginInstanceRegistryLinkRow,
    PluginInstanceRegistryRow,
)

API_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"
REVISION_PATH = VERSIONS_DIR / "2026110303_m05_plugin_instance_registry.py"

INSTANCE_TABLE = "m05_plugin_instances"
LINK_TABLE = "m05_plugin_instance_registry_links"
PROMOTION_TABLE = "m05_plugin_instance_promotion_evidence"
SCOPE_REGISTRY_TABLE = "scope_registry"
DEFINITION_TABLE = "m04e_dynamic_app_definitions"
VERSION_TABLE = "m04e_dynamic_app_versions"

M05_MODELS = {
    INSTANCE_TABLE: PluginInstanceRegistryRow,
    LINK_TABLE: PluginInstanceRegistryLinkRow,
    PROMOTION_TABLE: M05PluginInstancePromotionEvidenceRow,
}
TARGET_MODELS = {
    DEFINITION_TABLE: DynamicAppDefinitionFoundationRow,
    VERSION_TABLE: DynamicAppDefinitionVersionFoundationRow,
    SCOPE_REGISTRY_TABLE: ScopeRegistryRow,
}

EXACT_IDENTITY_KEY = (
    "plugin_instance_id",
    "definition_id",
    "version_id",
    "tenant_id",
    "scope_kind",
    "scope_id",
)

# Negative/edge acceptance cases required by MIG-DDL-M05-01, mapped to the named
# objects that enforce them. Must cover every case exported by the M05 model.
NEGATIVE_ACCEPTANCE_ENFORCEMENT = {
    "duplicate_exact_instance_identity_is_rejected": (
        "uq_m05_plugin_instance_exact_identity",
        "uq_m05_plugin_instance_grant_identity",
        "ck_m05_instance_exact_public_id",
    ),
    "cross_tenant_scope_tuple_is_rejected_or_quarantined": (
        "fk_m05_instance_definition_tenant",
        "fk_m05_instance_registry_tenant_link",
        "ck_m05_instance_tenant_canonical_id",
        "ck_m05_instance_typed_scope_shape",
    ),
    "definition_version_mismatch_is_rejected": (
        "fk_m05_instance_version_definition_artifact",
        "uq_m05_plugin_instance_grant_identity",
    ),
    "artifact_digest_mismatch_is_quarantined": (
        "fk_m05_instance_version_definition_artifact",
        "ck_m05_instance_artifact_sha256",
    ),
    "missing_registry_link_is_not_positive": (
        "ck_m05_instance_published_evidence_required",
        "fk_m05_instance_registry_tenant_link",
        "uq_m05_registry_link_instance",
    ),
    "both_reciprocal_fk_directions_require_validation": (
        "fk_m05_instance_registry_tenant_link",
        "fk_m05_registry_instance_tenant_link",
        "fk_m05_instance_registry_cabinet_link",
        "fk_m05_registry_instance_cabinet_link",
        "fk_m05_instance_registry_project_link",
        "fk_m05_registry_instance_project_link",
    ),
    "invalid_typed_scope_shape_is_rejected": (
        "ck_m05_instance_typed_scope_shape",
        "ck_m05_instance_scope_kind",
        "ck_m05_registry_link_typed_shape",
        "ck_m05_instance_parent_scope_nonempty",
    ),
    "unresolved_m04_scope_or_m04e_dependency_keeps_partition_blocked": (
        "ck_m05_instance_promotion_outcome",
        "ck_m05_instance_no_positive_writes",
        "fk_m05_instance_definition_tenant",
    ),
    "legacy_published_state_is_not_mapped_to_active_without_review": (
        "ck_m05_instance_lifecycle",
        "ck_m05_instance_published_evidence_required",
    ),
    "source_instance_id_is_preserved_and_immutable": (
        "uq_m05_instance_source_identity",
        "ck_m05_instance_source_id_nonempty",
        "ck_m05_instance_source_kind_nonempty",
        "ck_m05_instance_source_schema_nonempty",
    ),
    "legacy_source_id_over_canonical_width_requires_inventory_before_mapping": (
        "uq_m05_instance_source_identity",
        "ck_m05_instance_source_sha256",
    ),
}

ROUND_TRIP_DROP_ORDER = [PROMOTION_TABLE, LINK_TABLE, INSTANCE_TABLE]


# --------------------------------------------------------------------------- #
# source / metadata helpers
# --------------------------------------------------------------------------- #
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _revision_module():
    spec = importlib.util.spec_from_file_location("m05_revision_2026110303", REVISION_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {REVISION_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _revision_module()
TREE = _tree(REVISION_PATH)
SOURCE = REVISION_PATH.read_text(encoding="utf-8")
NAMESPACE = {
    name: getattr(MODULE, name)
    for name in dir(MODULE)
    if isinstance(getattr(MODULE, name), str)
}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"revision has no {name}()")


def _value(node, namespace: dict[str, object] | None = None):
    if node is None:
        return None
    if isinstance(node, ast.Name) and namespace and node.id in namespace:
        return namespace[node.id]
    return ast.literal_eval(node)


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keyword(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _normalize(sql: str) -> str:
    return " ".join(sql.split())


def _created_tables(tree: ast.Module) -> dict[str, dict[str, object]]:
    tables: dict[str, dict[str, object]] = {}
    for node in ast.walk(_function(tree, "upgrade")):
        if not isinstance(node, ast.Call) or _call_name(node) != "create_table":
            continue
        table = _value(node.args[0], NAMESPACE)
        spec: dict[str, object] = {
            "columns": [],
            "checks": {},
            "uniques": {},
            "fks": {},
            "pk": [],
        }
        for argument in node.args[1:]:
            if not isinstance(argument, ast.Call):
                continue
            kind = _call_name(argument)
            if kind == "Column":
                nullable = _keyword(argument, "nullable")
                spec["columns"].append(
                    {
                        "name": _value(argument.args[0], NAMESPACE),
                        "type": ast.unparse(argument.args[1]) if len(argument.args) > 1 else "",
                        "nullable": True if nullable is None else _value(nullable),
                        "server_default": _keyword(argument, "server_default") is not None,
                    }
                )
            elif kind == "CheckConstraint":
                spec["checks"][_value(_keyword(argument, "name"))] = _normalize(
                    _value(argument.args[0], NAMESPACE)
                )
            elif kind == "UniqueConstraint":
                spec["uniques"][_value(_keyword(argument, "name"))] = tuple(
                    _value(item, NAMESPACE) for item in argument.args
                )
            elif kind == "ForeignKeyConstraint":
                spec["fks"][_value(_keyword(argument, "name"))] = (
                    tuple(_value(item, NAMESPACE) for item in argument.args[0].elts),
                    tuple(_value(item, NAMESPACE) for item in argument.args[1].elts),
                    _value(_keyword(argument, "ondelete")),
                )
            elif kind == "PrimaryKeyConstraint":
                spec["pk"].append([_value(item, NAMESPACE) for item in argument.args])
        tables[table] = spec
    return tables


def _calls(tree: ast.Module, function_name: str) -> list[dict[str, object]]:
    """Ordered ``(op, name, table, lineno)`` view of one revision function."""
    function = _function(tree, function_name)
    nodes = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    nodes.sort(key=lambda node: (node.lineno, node.col_offset))
    view = []
    for call in nodes:
        name = _call_name(call)
        if name is None:
            continue
        values = []
        for index in (0, 1):
            try:
                values.append(
                    _value(call.args[index], NAMESPACE) if len(call.args) > index else None
                )
            except (ValueError, SyntaxError):
                values.append("<dynamic>")
        view.append(
            {"op": name, "name": values[0], "table": values[1], "lineno": call.lineno}
        )
    return view


def _created_indexes(tree: ast.Module) -> dict[str, tuple[str, tuple[str, ...]]]:
    indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
    for node in ast.walk(_function(tree, "upgrade")):
        if isinstance(node, ast.Call) and _call_name(node) == "create_index":
            indexes[_value(node.args[0], NAMESPACE)] = (
                _value(node.args[1], NAMESPACE),
                tuple(_value(item, NAMESPACE) for item in node.args[2].elts),
            )
    return indexes


def _orm_checks(table) -> dict[str, str]:
    return {
        constraint.name: _normalize(str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _orm_uniques(table) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _orm_foreign_keys(table) -> dict[str, dict[str, object]]:
    keys = {}
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        keys[constraint.name] = {
            "local": tuple(column.name for column in constraint.columns),
            "remote": tuple(element.target_fullname for element in constraint.elements),
            "ondelete": constraint.ondelete,
            "deferrable": bool(constraint.deferrable),
            "initially": constraint.initially,
        }
    return keys


def _orm_indexes(table) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns) for index in table.indexes
    }


def _expected_ddl_type(column) -> str:
    """Render the ORM type the way the revision must spell it."""
    type_ = column.type
    if isinstance(type_, JSONB):
        return "json_payload"
    if isinstance(type_, Text):
        return "sa.Text()"
    if isinstance(type_, DateTime):
        assert type_.timezone is True, f"{column.name} must be timezone aware"
        return "sa.DateTime(timezone=True)"
    if isinstance(type_, String):
        return f"sa.String({type_.length})"
    if isinstance(type_, Integer):
        return "sa.Integer()"
    if isinstance(type_, Boolean):
        return "sa.Boolean()"
    raise AssertionError(f"unhandled ORM column type for {column.name}: {type_!r}")


def _ddl_names() -> set[str]:
    names: set[str] = set()
    for spec in _created_tables(TREE).values():
        names |= set(spec["checks"])
        names |= set(spec["uniques"])
        names |= set(spec["fks"])
    names |= {spec["name"] for spec in MODULE.DEFERRED_FOREIGN_KEYS}
    return names


def _orm_names() -> set[str]:
    names: set[str] = set()
    for model in M05_MODELS.values():
        names |= set(_orm_checks(model.__table__))
        names |= set(_orm_uniques(model.__table__))
        names |= set(_orm_foreign_keys(model.__table__))
    names |= set(_orm_uniques(ScopeRegistryRow.__table__))
    names |= set(_orm_foreign_keys(ScopeRegistryRow.__table__))
    return names


def _declared_value(path: Path, name: str):
    """Read ``name = ...`` or ``name: type = ...`` from a revision module."""
    for node in _tree(path).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    return None


def _revision_ids() -> dict[str, Path]:
    ids: dict[str, Path] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        revision_id = _declared_value(path, "revision")
        if revision_id is not None:
            ids.setdefault(revision_id, path)
    return ids


def _declared_down_revision(path: Path):
    return _declared_value(path, "down_revision")


# --------------------------------------------------------------------------- #
# 1. revision chain
# --------------------------------------------------------------------------- #
def test_revision_is_pinned_to_the_wave_chain() -> None:
    assert MODULE.revision == "2026110303"
    assert MODULE.down_revision == "2026110302"
    assert REVISION_PATH.name.startswith("2026110303_m05_plugin_instance_registry")
    assert MODULE.branch_labels is None
    assert MODULE.depends_on is None

    assert len(list(VERSIONS_DIR.glob("2026110303_*.py"))) == 1

    ids = _revision_ids()
    assert ids["2026110303"] == REVISION_PATH
    # Sibling wave revisions are authored in parallel by other agents; their links
    # are checked only where present so this contract never forks the chain.
    assert "2026110202" in ids, "the pre-wave head must still exist"
    for revision_id, expected_parent in (
        ("2026110302", "2026110301"),
        ("2026110301", "2026110202"),
    ):
        if revision_id in ids:
            assert ids[revision_id].name.startswith(revision_id)
            assert _declared_down_revision(ids[revision_id]) == expected_parent

    # Exactly one revision may claim this revision's parent, so the chain cannot fork.
    claimants = [
        revision_id
        for revision_id, path in ids.items()
        if _declared_down_revision(path) == MODULE.down_revision
    ]
    assert claimants == ["2026110303"], (
        f"down_revision {MODULE.down_revision} must be claimed once, got {claimants}"
    )


# --------------------------------------------------------------------------- #
# 2. metadata parity
# --------------------------------------------------------------------------- #
def test_exactly_the_three_m05_tables_are_created() -> None:
    assert set(_created_tables(TREE)) == set(M05_MODELS)


def test_created_columns_match_orm_columns() -> None:
    created = _created_tables(TREE)
    for table_name, model in M05_MODELS.items():
        orm_columns = list(model.__table__.columns)
        ddl_columns = created[table_name]["columns"]
        assert [column["name"] for column in ddl_columns] == [
            column.name for column in orm_columns
        ], f"{table_name}: column order/name drift against the ORM"
        for orm_column, ddl_column in zip(orm_columns, ddl_columns):
            assert ddl_column["type"] == _expected_ddl_type(orm_column), (
                f"{table_name}.{orm_column.name}: DDL type drift "
                f"({ddl_column['type']} vs {_expected_ddl_type(orm_column)})"
            )
            assert ddl_column["nullable"] is orm_column.nullable, (
                f"{table_name}.{orm_column.name}: nullability drift"
            )
            assert ddl_column["server_default"] is (orm_column.server_default is not None), (
                f"{table_name}.{orm_column.name}: server_default presence drift"
            )


def test_json_columns_use_the_postgresql_jsonb_type() -> None:
    assert "json_payload = postgresql.JSONB(astext_type=sa.Text())" in SOURCE
    ddl_columns = _created_tables(TREE)[PROMOTION_TABLE]["columns"]
    jsonb_columns = {
        column["name"] for column in ddl_columns if column["type"] == "json_payload"
    }
    assert jsonb_columns == {
        "composite_key_evidence",
        "reciprocal_fk_evidence",
        "quarantine_counts",
        "lifecycle_distribution",
        "dependency_digests",
        "gate_evidence",
    }
    for column_name in jsonb_columns:
        assert (
            M05PluginInstancePromotionEvidenceRow.__table__.c[column_name].nullable is False
        ), f"{column_name} must stay required evidence"


def test_named_checks_match_orm_expressions() -> None:
    created = _created_tables(TREE)
    for table_name, model in M05_MODELS.items():
        assert created[table_name]["checks"] == _orm_checks(model.__table__), (
            f"{table_name}: check-constraint names or expressions drift"
        )
        assert created[table_name]["checks"], f"{table_name}: no named checks emitted"


def test_named_uniques_match_orm_metadata() -> None:
    created = _created_tables(TREE)
    for table_name, model in M05_MODELS.items():
        assert created[table_name]["uniques"] == _orm_uniques(model.__table__), (
            f"{table_name}: unique-constraint names or columns drift"
        )
        assert created[table_name]["pk"] == [["id"]], f"{table_name}: primary key drift"


def test_inline_foreign_keys_match_orm_targets() -> None:
    created = _created_tables(TREE)
    orm_keys = _orm_foreign_keys(PluginInstanceRegistryRow.__table__)
    inline = created[INSTANCE_TABLE]["fks"]
    assert set(inline) == {
        "fk_m05_instance_definition_tenant",
        "fk_m05_instance_version_definition_artifact",
    }
    for name, (local, remote, ondelete) in inline.items():
        assert local == orm_keys[name]["local"], f"{name}: local columns drift"
        assert remote == orm_keys[name]["remote"], f"{name}: target columns drift"
        assert ondelete == orm_keys[name]["ondelete"] == "RESTRICT", f"{name}: ondelete drift"
    assert orm_keys["fk_m05_instance_definition_tenant"]["remote"] == (
        f"{DEFINITION_TABLE}.id",
        f"{DEFINITION_TABLE}.tenant_id",
    )
    assert orm_keys["fk_m05_instance_version_definition_artifact"]["remote"] == (
        f"{VERSION_TABLE}.id",
        f"{VERSION_TABLE}.definition_id",
        f"{VERSION_TABLE}.artifact_digest",
    )


def test_indexes_match_orm_metadata() -> None:
    expected = {}
    for table_name, model in M05_MODELS.items():
        for index_name, columns in _orm_indexes(model.__table__).items():
            expected[index_name] = (table_name, columns)
    assert _created_indexes(TREE) == expected


def test_exact_instance_identity_key_matches_the_contract() -> None:
    orm_uniques = _orm_uniques(PluginInstanceRegistryRow.__table__)
    ddl_uniques = _created_tables(TREE)[INSTANCE_TABLE]["uniques"]
    assert orm_uniques["uq_m05_plugin_instance_exact_identity"] == EXACT_IDENTITY_KEY
    assert ddl_uniques["uq_m05_plugin_instance_exact_identity"] == EXACT_IDENTITY_KEY
    assert orm_uniques["uq_m05_plugin_instance_grant_identity"] == (
        "id",
        "definition_id",
        "version_id",
        "tenant_id",
        "scope_kind",
        "scope_id",
    )
    assert ddl_uniques["uq_m05_plugin_instance_grant_identity"] == (
        "id",
        "definition_id",
        "version_id",
        "tenant_id",
        "scope_kind",
        "scope_id",
    )
    assert (
        _orm_checks(PluginInstanceRegistryRow.__table__)["ck_m05_instance_exact_public_id"]
        == "plugin_instance_id = id"
    )


def test_source_id_width_stays_verbatim_and_bounded() -> None:
    # The target keeps the legacy source installation ID verbatim but bounded at
    # String(80); the canonical instance/scope identity stays String(40).
    table = PluginInstanceRegistryRow.__table__
    assert table.c.source_id.type.length == 80
    assert table.c.source_kind.type.length == 80
    assert table.c.source_schema_version.type.length == 40
    assert table.c.plugin_instance_id.type.length == 40
    assert table.c.scope_id.type.length == 40
    ddl_columns = {column["name"]: column for column in _created_tables(TREE)[INSTANCE_TABLE]["columns"]}
    assert ddl_columns["source_id"]["type"] == "sa.String(80)"
    assert ddl_columns["plugin_instance_id"]["type"] == "sa.String(40)"


# --------------------------------------------------------------------------- #
# 3. staged cyclic foreign keys
# --------------------------------------------------------------------------- #
def test_deferred_foreign_key_specs_are_use_alter_and_named() -> None:
    specs = {spec["name"]: spec for spec in MODULE.DEFERRED_FOREIGN_KEYS}
    assert set(specs) == {
        "fk_m05_registry_instance_link_evidence",
        "fk_m05_instance_registry_tenant_link",
        "fk_m05_instance_registry_cabinet_link",
        "fk_m05_instance_registry_project_link",
        "fk_m05_registry_instance_tenant_link",
        "fk_m05_registry_instance_cabinet_link",
        "fk_m05_registry_instance_project_link",
    }
    for name, spec in specs.items():
        assert spec["use_alter"] is True, f"{name}: must be staged with use_alter"
        assert spec["deferrable"] is True, f"{name}: must be deferrable"
        assert spec["initially"] == "DEFERRED", f"{name}: must start deferred"
        assert spec["ondelete"] == "RESTRICT", f"{name}: evidence must not cascade"
        assert len(spec["local_columns"]) == len(spec["remote_columns"]) >= 1


def test_deferred_foreign_keys_match_orm_columns_and_properties() -> None:
    created = _created_tables(TREE)
    for spec in MODULE.DEFERRED_FOREIGN_KEYS:
        name = spec["name"]
        source_table = TARGET_MODELS.get(spec["source"])
        assert source_table is not None, f"{name}: unknown source table {spec['source']}"
        orm_fk = _orm_foreign_keys(source_table.__table__)[name]
        assert spec["local_columns"] == orm_fk["local"], f"{name}: local columns drift"
        assert tuple(
            f"{spec['referent']}.{column}" for column in spec["remote_columns"]
        ) == orm_fk["remote"], f"{name}: target columns drift"
        assert orm_fk["ondelete"] == "RESTRICT"
        assert orm_fk["deferrable"] is True
        assert orm_fk["initially"] == "DEFERRED"
        # Late-bound constraints must not also be emitted inline.
        assert name not in created.get(spec["source"], {}).get("fks", {})


def test_each_reciprocal_foreign_key_has_a_unique_target_key() -> None:
    specs = MODULE.DEFERRED_FOREIGN_KEYS
    instance_to_registry = {
        tuple(spec["remote_columns"]) for spec in specs if spec["source"] == INSTANCE_TABLE
    }
    assert instance_to_registry == {
        tuple(columns) for _name, columns in MODULE.SCOPE_REGISTRY_ANCHOR_KEYS
    }
    registry_to_instance = {
        tuple(spec["remote_columns"])
        for spec in specs
        if spec["source"] == SCOPE_REGISTRY_TABLE
    }
    instance_uniques = set(_orm_uniques(PluginInstanceRegistryRow.__table__).values())
    ddl_instance_uniques = set(_created_tables(TREE)[INSTANCE_TABLE]["uniques"].values())
    assert registry_to_instance <= instance_uniques
    assert registry_to_instance <= ddl_instance_uniques
    link_fk = next(
        spec for spec in specs if spec["name"] == "fk_m05_registry_instance_link_evidence"
    )
    assert link_fk["remote_columns"] == ("id",)
    assert PluginInstanceRegistryLinkRow.__table__.c.id.primary_key is True


def test_late_foreign_keys_are_created_after_both_tables_exist() -> None:
    upgrade_body = ast.unparse(_function(TREE, "upgrade"))
    late_body = ast.unparse(_function(TREE, "_create_deferred_foreign_keys"))

    # Every cyclic constraint is added by the dedicated helper, once per spec, and
    # never inline in create_table().
    assert "op.create_foreign_key" not in upgrade_body
    assert "op.create_foreign_key" in late_body
    assert "reversed" not in late_body
    assert "DEFERRED_FOREIGN_KEYS" in late_body
    assert late_body.count("op.create_foreign_key") == 1, (
        "the helper must emit exactly one create_foreign_key call inside its loop"
    )
    assert _created_tables(TREE)[INSTANCE_TABLE]["fks"].keys().isdisjoint(
        {spec["name"] for spec in MODULE.DEFERRED_FOREIGN_KEYS}
    )

    # The helper runs only after all three M05 tables exist, and the fail-closed
    # target verification plus the registry anchor keys run before any of it.
    helper_call = upgrade_body.rindex("_create_deferred_foreign_keys()")
    assert helper_call > upgrade_body.rindex("create_table(")
    assert upgrade_body.index("_verify_foreign_target_keys()") < upgrade_body.index(
        "create_table("
    )
    assert upgrade_body.index("_ensure_scope_registry_anchor_keys()") < upgrade_body.index(
        "create_table("
    )
    assert upgrade_body.index("_ensure_scope_registry_anchor_keys()") < helper_call

    # Both sides of the cycle are real objects by then: the instance table exists
    # from this revision and scope_registry from revision 2026110301.
    upgraded_tables = set(_created_tables(TREE))
    for spec in MODULE.DEFERRED_FOREIGN_KEYS:
        assert spec["source"] in upgraded_tables | {SCOPE_REGISTRY_TABLE}
        assert spec["referent"] in upgraded_tables | {SCOPE_REGISTRY_TABLE}


# --------------------------------------------------------------------------- #
# 4. cross-revision foreign targets
# --------------------------------------------------------------------------- #
def test_required_target_unique_keys_exist_in_metadata() -> None:
    assert MODULE.REQUIRED_TARGET_UNIQUE_KEYS
    for table, name, columns, owner in MODULE.REQUIRED_TARGET_UNIQUE_KEYS:
        assert owner in {"2026110301", "2026110302"}, f"{name}: unexpected owner revision"
        assert table in TARGET_MODELS, f"{name}: unknown target table"
        target_uniques = _orm_uniques(TARGET_MODELS[table].__table__)
        assert name in target_uniques, (
            f"{name}: metadata has no unique key on {table}; "
            f"{owner} must emit it or the M05 FK stays blocked"
        )
        assert target_uniques[name] == tuple(columns) or sorted(
            target_uniques[name]
        ) == sorted(columns), f"{name}: target key columns drift"


def test_scope_registry_anchor_keys_match_metadata_and_are_idempotent() -> None:
    registry_uniques = _orm_uniques(ScopeRegistryRow.__table__)
    assert MODULE.SCOPE_REGISTRY_ANCHOR_KEYS
    for name, columns in MODULE.SCOPE_REGISTRY_ANCHOR_KEYS:
        assert name in registry_uniques, (
            f"{name}: the M05 model module must append this key to scope_registry metadata"
        )
        assert registry_uniques[name] == tuple(columns)
        assert name in SOURCE
    # Created only when absent, never unconditionally: revision 2026110301 states
    # that these keys belong to 2026110303, so this revision owns them end-to-end.
    assert "IF NOT EXISTS (" in SOURCE
    assert "ADD CONSTRAINT" in SOURCE
    downgrade_source = ast.unparse(_function(TREE, "downgrade"))
    assert "_drop_scope_registry_anchor_keys" in downgrade_source
    helper = ast.unparse(_function(TREE, "_drop_scope_registry_anchor_keys"))
    assert "drop_constraint" in helper and "type_='unique'" in helper
    assert "SCOPE_REGISTRY_ANCHOR_KEYS" in helper
    assert SCOPE_REGISTRY_TABLE in helper
    assert "IF EXISTS" not in helper, "a missing anchor key must fail loudly"


def test_upgrade_fails_closed_with_an_explicit_blocker() -> None:
    assert MODULE.BLOCKER_PREFIX == "MIG-DDL-M05-01 blocker"
    assert MODULE.BLOCKER_PREFIX in SOURCE
    assert "RAISE EXCEPTION" in SOURCE
    assert "contype IN ('p', 'u')" in SOURCE
    assert "pg_constraint" in SOURCE
    helper = ast.unparse(_function(TREE, "_verify_foreign_target_keys"))
    assert "REQUIRED_TARGET_UNIQUE_KEYS" in helper
    assert "_require_unique_key" in helper


# --------------------------------------------------------------------------- #
# 5. downgrade symmetry
# --------------------------------------------------------------------------- #
def test_downgrade_removes_foreign_keys_before_tables() -> None:
    downgrade = _function(TREE, "downgrade")
    calls = _calls(TREE, "downgrade")
    table_drops = [call["name"] for call in calls if call["op"] == "drop_table"]
    assert table_drops == ROUND_TRIP_DROP_ORDER
    first_table_drop_line = min(
        call["lineno"] for call in calls if call["op"] == "drop_table"
    )

    # All seven late foreign keys are dropped through one loop over the specs.
    loop = next(node for node in ast.walk(downgrade) if isinstance(node, ast.For))
    loop_source = ast.unparse(loop)
    assert "reversed(DEFERRED_FOREIGN_KEYS)" in loop_source
    assert "drop_constraint" in loop_source
    assert "spec['name']" in loop_source and "spec['source']" in loop_source
    assert "type_='foreignkey'" in loop_source, (
        "every staged FK must be dropped from its own source table first"
    )
    assert (loop.end_lineno or loop.lineno) < first_table_drop_line, (
        "no table may be dropped before the reciprocal foreign keys are gone"
    )

    # The M05-only registry anchor keys are owned by this revision (2026110301
    # explicitly defers them), so they are released after every M05 FK is gone and
    # before the tables disappear.
    downgrade_source = ast.unparse(downgrade)
    assert "_drop_scope_registry_anchor_keys" in downgrade_source
    anchor_line = downgrade_source.index("_drop_scope_registry_anchor_keys")
    assert anchor_line > downgrade_source.index("drop_constraint")
    assert anchor_line < downgrade_source.index("drop_table")

    assert not [call for call in calls if call["op"].startswith("create_")]
    assert not [call for call in _calls(TREE, "upgrade") if call["op"] == "drop_table"]
    created_indexes = {
        call["name"] for call in _calls(TREE, "upgrade") if call["op"] == "create_index"
    }
    dropped_indexes = {
        call["name"] for call in calls if call["op"] == "drop_index"
    }
    assert created_indexes == dropped_indexes


# --------------------------------------------------------------------------- #
# 6. scope of this revision
# --------------------------------------------------------------------------- #
def test_revision_does_not_enable_rls_backfill_or_positive_writes() -> None:
    upper = SOURCE.upper()
    assert "ENABLE ROW LEVEL SECURITY" not in upper
    assert "FORCE ROW LEVEL SECURITY" not in upper
    assert "CREATE POLICY" not in upper
    assert "2026110307" in SOURCE, "RLS must be documented as deferred to 2026110307"
    assert "INSERT INTO" not in upper
    assert "DELETE FROM" not in upper
    assert "TRUNCATE" not in upper
    assert "op.alter_column" not in SOURCE
    assert "op.add_column" not in SOURCE
    assert 'create_table("scope_registry"' not in SOURCE
    assert "from prodavan" not in SOURCE, "the revision must not import ORM models"


def test_promotion_evidence_cannot_enable_positive_writes() -> None:
    checks = _orm_checks(M05PluginInstancePromotionEvidenceRow.__table__)
    ddl_checks = _created_tables(TREE)[PROMOTION_TABLE]["checks"]
    assert checks["ck_m05_instance_no_positive_writes"] == "canonical_positive_writes = false"
    assert ddl_checks["ck_m05_instance_no_positive_writes"] == checks[
        "ck_m05_instance_no_positive_writes"
    ]
    assert checks["ck_m05_instance_promotion_outcome"] == (
        "outcome IN ('blocked', 'quarantined')"
    )
    assert 'server_default=sa.text("false")' in SOURCE
    assert """server_default=sa.text("'blocked'")""" in SOURCE
    assert "legacy owner remains authoritative; preserve evidence and repair forward" in SOURCE


# --------------------------------------------------------------------------- #
# 7. negative acceptance coverage
# --------------------------------------------------------------------------- #
def test_every_model_acceptance_case_has_named_enforcement() -> None:
    assert set(NEGATIVE_ACCEPTANCE_ENFORCEMENT) == set(M05_ACCEPTANCE_CASES)
    ddl_names = _ddl_names()
    orm_names = _orm_names()
    for case, names in NEGATIVE_ACCEPTANCE_ENFORCEMENT.items():
        assert names, f"{case}: no enforcement object declared"
        missing_ddl = set(names) - ddl_names
        missing_orm = set(names) - orm_names
        assert not missing_ddl, f"{case}: not enforced by revision 2026110303: {sorted(missing_ddl)}"
        assert not missing_orm, f"{case}: absent from ORM metadata: {sorted(missing_orm)}"


def test_the_six_required_negative_cases_are_explicitly_covered() -> None:
    required = {
        "duplicate_exact_instance_identity_is_rejected",
        "cross_tenant_scope_tuple_is_rejected_or_quarantined",
        "definition_version_mismatch_is_rejected",
        "artifact_digest_mismatch_is_quarantined",
        "missing_registry_link_is_not_positive",
        "both_reciprocal_fk_directions_require_validation",
    }
    assert required <= set(NEGATIVE_ACCEPTANCE_ENFORCEMENT)
    for case in sorted(required):
        assert len(NEGATIVE_ACCEPTANCE_ENFORCEMENT[case]) >= 2, (
            f"{case}: needs at least two named enforcement objects"
        )


def test_reciprocal_link_evidence_table_is_staged_not_authoritative() -> None:
    link_checks = _orm_checks(PluginInstanceRegistryLinkRow.__table__)
    assert link_checks["ck_m05_registry_link_state"] == (
        "owner_state IN ('active', 'archived', 'quarantined')"
    )
    assert link_checks["ck_m05_registry_link_typed_shape"] == (
        "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id"
    )
    uniques = _orm_uniques(PluginInstanceRegistryLinkRow.__table__)
    assert uniques["uq_m05_registry_link_instance"] == ("plugin_instance_id",)
    # The link row is not an authorization surface: it must not copy definition,
    # version or artifact anchors from the instance row.
    link_columns = set(PluginInstanceRegistryLinkRow.__table__.columns.keys())
    assert link_columns.isdisjoint(
        {"definition_id", "version_id", "artifact_digest", "lifecycle", "subject_id"}
    )
