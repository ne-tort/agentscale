"""Static registration and metadata contracts; never import ORM models."""

from __future__ import annotations

import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = API_ROOT / "src" / "prodavan" / "infrastructure" / "persistence" / "models"
MODELS_INIT = MODELS_DIR / "__init__.py"
MODEL_PREFIX = "prodavan.infrastructure.persistence.models."

CANDIDATE_MODELS = {
    "owner_registry.py": {
        "CanonicalTenantAnchorRow": "scope_owner_tenant_anchors",
        "ScopeOwnerCabinetAnchorRow": "scope_owner_cabinet_anchors",
        "ScopeOwnerProjectAnchorRow": "scope_owner_project_anchors",
        "ScopeRegistryRow": "scope_registry",
        "ScopeOwnerMappingRow": "scope_owner_mappings",
        "ScopeOwnerQuarantineRow": "scope_owner_quarantine",
        "ScopeOwnerAuditRow": "scope_owner_audit",
    },
    "dynamic_app_definition_version.py": {
        "DynamicAppDefinitionFoundationRow": "m04e_dynamic_app_definitions",
        "DynamicAppDefinitionVersionFoundationRow": "m04e_dynamic_app_versions",
        "M04EDefinitionVersionPromotionEvidenceRow": "m04e_definition_version_promotion_evidence",
    },
    "plugin_instance_registry.py": {
        "PluginInstanceRegistryRow": "m05_plugin_instances",
        "PluginInstanceRegistryLinkRow": "m05_plugin_instance_registry_links",
        "M05PluginInstancePromotionEvidenceRow": "m05_plugin_instance_promotion_evidence",
    },
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_strings(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.Tuple, ast.List)):
        return ()
    if not all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts):
        return ()
    return tuple(item.value for item in node.elts)


def _string_args(call: ast.Call) -> tuple[str, ...]:
    if not all(isinstance(argument, ast.Constant) and isinstance(argument.value, str) for argument in call.args):
        return ()
    return tuple(argument.value for argument in call.args)


def _class_nodes(path: Path) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef)
    }


def _class_tables(path: Path) -> dict[str, str]:
    tables: dict[str, str] = {}
    for name, node in _class_nodes(path).items():
        for statement in node.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in targets):
                value = statement.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    tables[name] = value.value
    return tables


def _package_imports(tree: ast.Module) -> set[tuple[str, str]]:
    return {
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }


def _exported_names(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return {
                item.value
                for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    return set()


def _mapped_columns(class_node: ast.ClassDef) -> set[str]:
    return {
        statement.target.id
        for statement in class_node.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "mapped_column"
    }


def _constraint_calls(class_node: ast.ClassDef, call_name: str) -> list[ast.Call]:
    calls = []
    for statement in class_node.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "__table_args__" for target in targets):
            continue
        calls.extend(
            node for node in ast.walk(statement.value)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == call_name
        )
    return calls


def _unique_keys(class_node: ast.ClassDef) -> set[tuple[str, ...]]:
    keys = {
        _string_args(call)
        for call in _constraint_calls(class_node, "UniqueConstraint")
        if _string_args(call)
    }
    primary_key = []
    for statement in class_node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        value = statement.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "mapped_column":
            continue
        if any(
            keyword.arg == "primary_key"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in value.keywords
        ):
            primary_key.append(statement.target.id)
    if primary_key:
        keys.add(tuple(primary_key))
    return keys


def _foreign_keys(class_node: ast.ClassDef) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    result = []
    for call in _constraint_calls(class_node, "ForeignKeyConstraint"):
        if len(call.args) < 2:
            continue
        local = _literal_strings(call.args[0])
        remote = _literal_strings(call.args[1])
        if local and remote:
            result.append((local, remote))
    for statement in class_node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        value = statement.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "mapped_column":
            continue
        for child in ast.walk(value):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "ForeignKey"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                result.append(((statement.target.id,), (child.args[0].value,)))
    return result


def _all_model_contracts():
    tables: dict[str, list[tuple[Path, str, ast.ClassDef]]] = {}
    class_tables: dict[str, str] = {}
    for path in MODELS_DIR.rglob("*.py"):
        for name, table_name in _class_tables(path).items():
            node = _class_nodes(path)[name]
            tables.setdefault(table_name, []).append((path, name, node))
            class_tables[name] = table_name
    keys = {
        table_name: set().union(*(_unique_keys(node) for _, _, node in owners))
        for table_name, owners in tables.items()
    }

    # These target keys are deliberately installed by the owning foundation modules.
    owner_tree = _tree(MODELS_DIR / "owner_registry.py")
    for call in ast.walk(owner_tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        receiver = call.func.value
        if (
            call.func.attr == "append_constraint"
            and isinstance(receiver, ast.Attribute)
            and receiver.attr == "__table__"
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "ProjectRow"
            and call.args
            and isinstance(call.args[0], ast.Call)
            and isinstance(call.args[0].func, ast.Name)
            and call.args[0].func.id == "UniqueConstraint"
        ):
            key = _string_args(call.args[0])
            if key:
                keys.setdefault("projects", set()).add(key)

    plugin_tree = _tree(MODELS_DIR / "plugin_instance_registry.py")
    for call in ast.walk(plugin_tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "_append_unique_key":
            continue
        if len(call.args) < 2 or not isinstance(call.args[0], ast.Attribute):
            continue
        table_ref = call.args[0]
        if table_ref.attr != "__table__" or not isinstance(table_ref.value, ast.Name):
            continue
        table_name = class_tables.get(table_ref.value.id)
        key = _literal_strings(call.args[1])
        if table_name and key:
            keys.setdefault(table_name, set()).add(key)

    # The M05 reciprocal targets are declared as tuples then installed by loops.
    for loop in ast.walk(plugin_tree):
        if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Tuple):
            continue
        loop_names = {item.id for item in loop.target.elts if isinstance(item, ast.Name)}
        if "_columns" not in loop_names or not isinstance(loop.iter, (ast.Tuple, ast.List)):
            continue
        table_name = None
        for call in ast.walk(loop):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "_append_unique_key":
                continue
            if call.args and isinstance(call.args[0], ast.Attribute):
                ref = call.args[0]
                if ref.attr == "__table__" and isinstance(ref.value, ast.Name):
                    table_name = class_tables.get(ref.value.id)
        if table_name is None:
            continue
        for item in loop.iter.elts:
            if not isinstance(item, ast.Tuple) or not item.elts:
                continue
            key = _literal_strings(item.elts[0])
            if key:
                keys.setdefault(table_name, set()).add(key)
    return tables, keys


def test_candidate_tables_have_one_source_owner_and_are_registered() -> None:
    tables, _ = _all_model_contracts()
    imports = _package_imports(_tree(MODELS_INIT))
    exports = _exported_names(_tree(MODELS_INIT))

    registered_owners: dict[str, list[tuple[str, str]]] = {}
    for module, class_name in imports:
        if not module.startswith(MODEL_PREFIX) or module == MODEL_PREFIX + "base":
            continue
        filename = module.removeprefix(MODEL_PREFIX) + ".py"
        path = MODELS_DIR / filename
        if not path.is_file():
            continue
        table_name = _class_tables(path).get(class_name)
        if table_name:
            registered_owners.setdefault(table_name, []).append((filename, class_name))
    assert all(len(owners) == 1 for owners in registered_owners.values())

    for filename, models in CANDIDATE_MODELS.items():
        declared = _class_tables(MODELS_DIR / filename)
        module = MODEL_PREFIX + filename.removesuffix(".py")
        for class_name, table_name in models.items():
            assert declared.get(class_name) == table_name
            owners = [(path.name, owner_name) for path, owner_name, _ in tables.get(table_name, [])]
            assert owners == [(filename, class_name)]
            assert (module, class_name) in imports
            assert class_name in exports


def test_candidate_foreign_key_targets_have_matching_unique_keys() -> None:
    tables, unique_keys = _all_model_contracts()
    for filename, models in CANDIDATE_MODELS.items():
        class_nodes = _class_nodes(MODELS_DIR / filename)
        for class_name in models:
            for _, targets in _foreign_keys(class_nodes[class_name]):
                remote_tables = {target.rsplit(".", 1)[0] for target in targets}
                remote_columns = tuple(target.rsplit(".", 1)[1] for target in targets)
                assert len(remote_tables) == 1
                remote_table = remote_tables.pop()
                assert remote_table in tables
                assert remote_columns in unique_keys[remote_table]


def test_tenant_type_field_and_typed_anchor_composite_keys_match() -> None:
    tree = _tree(MODELS_DIR / "owner_registry.py")
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    tenant = classes["CanonicalTenantAnchorRow"]
    cabinet = classes["ScopeOwnerCabinetAnchorRow"]
    registry = classes["ScopeRegistryRow"]

    assert "tenant_type" in _mapped_columns(tenant)
    assert "tenant_type" in _mapped_columns(registry)
    project = classes["ScopeOwnerProjectAnchorRow"]
    assert "tenant_type" in _mapped_columns(cabinet)
    assert "tenant_type" in _mapped_columns(project)
    assert ("tenant_id", "tenant_type", "owner_state") in _unique_keys(tenant)
    assert ("cabinet_id", "tenant_id", "tenant_type", "owner_state") in _unique_keys(cabinet)
    assert ("tenant_id", "tenant_type", "owner_state") in {
        local for local, remote in _foreign_keys(registry)
        if remote[:1] == ("scope_owner_tenant_anchors.tenant_id",)
    }


def test_project_cabinet_and_company_anchors_are_independent_and_typed() -> None:
    tree = _tree(MODELS_DIR / "owner_registry.py")
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    project = classes["ScopeOwnerProjectAnchorRow"]
    cabinet = classes["ScopeOwnerCabinetAnchorRow"]
    project_fks = set(_foreign_keys(project))
    cabinet_fks = set(_foreign_keys(cabinet))

    assert (("project_id", "tenant_id", "cabinet_id"),
            ("projects.id", "projects.company_id", "projects.cabinet_id")) in project_fks
    assert not any(
        local == ("project_id",) and remote == ("projects.id",)
        for local, remote in project_fks
    )
    assert (("tenant_id",), ("companies.id",)) in project_fks
    assert (("cabinet_id",), ("cabinet_instances.id",)) in project_fks
    assert (("cabinet_id", "tenant_id"),
            ("cabinet_company_grants.cabinet_id", "cabinet_company_grants.company_id")) in project_fks
    assert (("cabinet_id", "tenant_id", "tenant_type"),
            ("scope_owner_cabinet_anchors.cabinet_id", "scope_owner_cabinet_anchors.tenant_id",
             "scope_owner_cabinet_anchors.tenant_type")) in project_fks
    assert (("cabinet_id", "tenant_id", "tenant_type"),
            ("scope_owner_cabinet_anchors.cabinet_id", "scope_owner_cabinet_anchors.tenant_id",
             "scope_owner_cabinet_anchors.tenant_type")) in cabinet_fks
    assert (("tenant_id",), ("companies.id",)) in cabinet_fks
    assert (("cabinet_id",), ("cabinet_instances.id",)) in cabinet_fks
    assert ("id", "company_id", "cabinet_id") in _all_model_contracts()[1]["projects"]


def test_m05_reciprocal_scope_registry_keys_are_unique_in_both_tables() -> None:
    _, unique_keys = _all_model_contracts()
    for suffix in ((), ("cabinet_id",), ("cabinet_id", "project_id")):
        scope_key = ("id", "plugin_instance_id", "tenant_id", *suffix)
        instance_key = ("scope_registry_id", "plugin_instance_id", "tenant_id", *suffix)
        assert scope_key in unique_keys["scope_registry"]
        assert instance_key in unique_keys["m05_plugin_instances"]


def test_m05_reciprocal_fk_declarations_match_the_unique_target_keys() -> None:
    tree = _tree(MODELS_DIR / "plugin_instance_registry.py")
    _, unique_keys = _all_model_contracts()
    expected = {
        (("id", "plugin_instance_id", "tenant_id"),
         ("scope_registry_id", "plugin_instance_id", "tenant_id")),
        (("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
         ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id")),
        (("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
         ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id")),
    }
    for loop in (node for node in ast.walk(tree) if isinstance(node, ast.For)):
        if not isinstance(loop.target, ast.Tuple):
            continue
        names = tuple(item.id for item in loop.target.elts if isinstance(item, ast.Name))
        if names != ("_columns", "_targets", "_name"):
            continue
        cases = {
            (_literal_strings(item.elts[0]), _literal_strings(item.elts[1]))
            for item in loop.iter.elts
            if isinstance(item, ast.Tuple) and len(item.elts) == 3
        }
        assert cases == expected
        fk_calls = [
            node for node in ast.walk(loop)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ForeignKeyConstraint"
        ]
        assert len(fk_calls) == 1
        fk = fk_calls[0]
        assert len(fk.args) == 2
        assert isinstance(fk.args[0], ast.Call)
        assert isinstance(fk.args[0].func, ast.Name) and fk.args[0].func.id == "list"
        assert isinstance(fk.args[0].args[0], ast.Name) and fk.args[0].args[0].id == "_columns"
        assert isinstance(fk.args[1], ast.ListComp)
        assert isinstance(fk.args[1].elt, ast.JoinedStr)
        assert isinstance(fk.args[1].generators[0].target, ast.Name)
        assert fk.args[1].generators[0].target.id == "column"
        assert isinstance(fk.args[1].generators[0].iter, ast.Name)
        assert fk.args[1].generators[0].iter.id == "_targets"
        literal_prefix = "".join(
            part.value for part in fk.args[1].elt.values if isinstance(part, ast.Constant)
        )
        formatted_columns = [
            part.value.id for part in fk.args[1].elt.values
            if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
        ]
        assert literal_prefix == "m05_plugin_instances."
        assert formatted_columns == ["column"]
        for local_columns, target_columns in expected:
            assert local_columns in unique_keys["scope_registry"]
            assert target_columns in unique_keys["m05_plugin_instances"]
        return
    raise AssertionError("M05 reciprocal ForeignKeyConstraint loop is missing")


def test_legacy_grant_is_registered_and_canonical_duplicate_is_not() -> None:
    tables, _ = _all_model_contracts()
    assert {(path.name, name) for path, name, _ in tables["dynamic_app_capability_grants"]} == {
        ("dynamic_app_grants.py", "DynamicAppCapabilityGrantRow"),
        ("canonical_grants.py", "CanonicalGrantRow"),
    }
    imports = _package_imports(_tree(MODELS_INIT))
    assert (MODEL_PREFIX + "dynamic_app_grants", "DynamicAppCapabilityGrantRow") in imports
    assert not any(module == MODEL_PREFIX + "canonical_grants" for module, _ in imports)


def test_alembic_metadata_loads_the_package_registration_surface() -> None:
    env_tree = _tree(API_ROOT / "alembic" / "env.py")
    imports = _package_imports(env_tree)
    assert (MODEL_PREFIX.rstrip("."), "Base") in imports
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "target_metadata" for target in node.targets)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "metadata"
        for node in env_tree.body
    )
