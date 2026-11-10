"""Static expectations для неисполняемого removal inventory; проходят в текущем прогоне."""

from __future__ import annotations

import ast
from pathlib import Path

from prodavan.application.plugins.strangler import removal as removal_module
from prodavan.application.plugins.strangler.removal import (
    CODE_FIRST_REMOVAL_INVENTORY,
    DORMANT_CUTOVER_BARRIER,
    FUTURE_CUTOVER_REQUIREMENTS,
    PRESERVED_DYNAMIC_GUARDS,
    PRESERVED_DYNAMIC_INVENTORY,
    PRESERVED_MIGRATION_ONLY_INVENTORY,
    PRESERVED_PLUGIN_INVENTORY,
    REMOVAL_BOUNDARIES,
)


REMOVED_LEGACY_HOST_FILES: tuple[str, ...] = (
    "apps/web/src/dynamic/declarative/host.tsx",
    "apps/web/src/dynamic/declarative/index.ts",
    "apps/web/src/dynamic/plugin-host.ts",
    "apps/web/src/dynamic/broker.ts",
    "apps/web/src/dynamic/plugin-host.test.ts",
    "apps/web/src/dynamic/declarative/acceptance.test.tsx",
)

EXPECTED_PRESERVED_DYNAMIC_INVENTORY: tuple[str, ...] = (
    "apps/web/src/dynamic/declarative/renderer.ts",
    "apps/web/src/dynamic/declarative/README.md",
    "apps/web/src/dynamic/schema.ts",
    "apps/web/src/dynamic/protocol.ts",
    "apps/web/src/dynamic/policy.ts",
    "apps/web/src/dynamic/registry-client/**",
    "apps/api/src/prodavan/application/dynamic_app*/**",
    "apps/api/src/prodavan/domain/dynamic_apps/**",
    "apps/api/src/prodavan/infrastructure/persistence/models/dynamic_app*.py",
)

EXPECTED_PRESERVED_MIGRATION_ONLY_INVENTORY: tuple[str, ...] = (
    "apps/api/src/prodavan/application/platform/product_module_upsert.py",
    "apps/api/src/prodavan/application/platform/product_module_seeds.py "
    "(PRODUCT_MODULES, mod_management_meta, seed_rows)",
    "apps/api/src/prodavan/application/platform/management_module_merge.py",
    "apps/api/src/prodavan/application/platform/bootstrap_config.py::"
    "DEFAULT_BASIC_MODULE_IDS",
    "apps/api/src/prodavan/domain/modules/types.py::GLOBAL_DEFAULT_PROJECT_MODULES",
    "apps/api/alembic/versions/2026110321_management_module_merge.py",
)

EXPECTED_PRESERVED_M05_INVENTORY: tuple[str, ...] = (
    "apps/api/src/prodavan/infrastructure/persistence/models/"
    "plugin_instance_registry.py (M05)",
    "apps/api/alembic/versions/2026110303_m05_plugin_instance_registry.py",
    "m05_plugin_instances",
    "m05_plugin_instance_registry_links",
    "m05_plugin_instance_promotion_evidence",
)

RENDERER_TESTS = "apps/web/src/dynamic/declarative/renderer.test.ts"
IMPORT_GRAPH_GUARD = "apps/web/src/dynamic/legacy-host-import-graph.test.ts"


def _deletion_inventory_text() -> str:
    return "\n".join(
        field
        for item in CODE_FIRST_REMOVAL_INVENTORY
        for field in (item.group, item.target, item.action, item.reference)
    )


def _removal_source() -> str:
    return Path(removal_module.__file__).read_text(encoding="utf-8")


def test_removed_legacy_hosts_are_not_future_deletion_targets() -> None:
    inventory = _deletion_inventory_text()
    for path in REMOVED_LEGACY_HOST_FILES:
        assert path not in inventory
    for forbidden in (
        "apps/web/src/dynamic/**",
        "apps/web/src/dynamic/declarative/**",
    ):
        assert forbidden not in inventory


def test_dynamic_inventory_is_explicitly_preserved_not_deleted() -> None:
    assert PRESERVED_DYNAMIC_INVENTORY == EXPECTED_PRESERVED_DYNAMIC_INVENTORY
    inventory = _deletion_inventory_text()
    for path in EXPECTED_PRESERVED_DYNAMIC_INVENTORY:
        assert path not in inventory


def test_migration_only_helpers_are_preserved_not_deleted() -> None:
    assert (
        PRESERVED_MIGRATION_ONLY_INVENTORY
        == EXPECTED_PRESERVED_MIGRATION_ONLY_INVENTORY
    )
    inventory = _deletion_inventory_text()
    for forbidden in (
        "product_module_upsert",
        "product_module_seeds",
        "mod_management_meta",
        "seed_rows",
        "management_module_merge",
        "DEFAULT_BASIC_MODULE_IDS",
        "GLOBAL_DEFAULT_PROJECT_MODULES",
    ):
        assert forbidden not in inventory
    assert any(
        "PRESERVED_MIGRATION_ONLY_INVENTORY" in boundary
        for boundary in REMOVAL_BOUNDARIES
    )


def test_m05_inventory_is_explicitly_preserved_not_deleted() -> None:
    assert PRESERVED_PLUGIN_INVENTORY == EXPECTED_PRESERVED_M05_INVENTORY
    inventory = _deletion_inventory_text()
    for path in EXPECTED_PRESERVED_M05_INVENTORY:
        assert path not in inventory
    assert any("M05 m05_*" in boundary for boundary in REMOVAL_BOUNDARIES)


def test_dynamic_guards_are_retained() -> None:
    assert PRESERVED_DYNAMIC_GUARDS == (RENDERER_TESTS, IMPORT_GRAPH_GUARD)
    inventory = _deletion_inventory_text()
    assert RENDERER_TESTS not in inventory
    assert IMPORT_GRAPH_GUARD not in inventory
    assert (
        "apps/web/src/dynamic/declarative/acceptance.test.tsx"
        not in inventory
    )
    assert any("PRESERVED_DYNAMIC_GUARDS" in boundary for boundary in REMOVAL_BOUNDARIES)


def test_dormant_0802_activation_is_forbidden() -> None:
    for token in ("2026110802", "move/copy/register/activate", "versions"):
        assert token in DORMANT_CUTOVER_BARRIER
    assert any(
        boundary == DORMANT_CUTOVER_BARRIER for boundary in REMOVAL_BOUNDARIES
    )


def test_future_cutover_requires_new_code_first_revision() -> None:
    requirements = "\n".join(FUTURE_CUTOVER_REQUIREMENTS)
    for token in (
        "S4 code-first",
        "S5",
        "ADR",
        "review",
        "acceptance",
        "тогдашней current head",
        "schema-only downgrade",
        "env guard",
    ):
        assert token in requirements
    assert "2026110802 не переиспользуется" in requirements


def test_no_stale_drop_first_or_fixed_head_runbook() -> None:
    source = _removal_source()
    for forbidden in (
        "POST_UPGRADE_REMOVAL_CHECKLIST",
        "PLUGIN_CUTOVER_CONFIRMED=true",
        "CUTOVER_ENV",
        "DROP_ORDER",
        "СРАЗУ",
        "рабочая голова остаётся",
        "2026110801",
        "alembic upgrade head",
    ):
        assert forbidden not in source


def test_inventory_items_are_review_candidates_not_commands() -> None:
    assert CODE_FIRST_REMOVAL_INVENTORY
    for item in CODE_FIRST_REMOVAL_INVENTORY:
        assert item.action.startswith(
            ("S4 review candidate:", "S5 acceptance criterion:")
        )


def test_inventory_has_no_executable_deletion_or_database_calls() -> None:
    tree = ast.parse(_removal_source())
    forbidden_calls = {
        "unlink",
        "rmtree",
        "remove",
        "drop_table",
        "execute",
        "system",
        "run",
        "Popen",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_calls
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name.split(".")[0] for alias in node.names}
            assert names.isdisjoint({"os", "shutil", "subprocess", "alembic"})
