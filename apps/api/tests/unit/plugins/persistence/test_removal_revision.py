"""Static guards для неисполняемого pending 0802; проходят в текущем прогоне.

Проверки читают AST и constants, но не вызывают Alembic, DDL/DML или БД.
Активные versions не изменяются и не проецируются в будущий cutover граф.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

from .conftest import REPO_ROOT, VERSIONS_DIR


DORMANT_REVISION_ID = "2026110802"
PENDING_DIR = VERSIONS_DIR.parent / "pending"
DORMANT_PATH = PENDING_DIR / "2026110802_remove_declarative_app.py"
REMOVAL_PATH = (
    REPO_ROOT
    / "apps"
    / "api"
    / "src"
    / "prodavan"
    / "application"
    / "plugins"
    / "strangler"
    / "removal.py"
)
CUTOVER_PATH = REMOVAL_PATH.with_name("cutover.py")

EXPECTED_HISTORICAL_TABLES: frozenset[str] = frozenset(
    {
        "modules",
        "module_cabinet_bindings",
        "module_company_grants",
        "module_instance_data_rows",
        "module_instance_meta_documents",
        "module_instances",
        "module_meta_documents",
        "module_project_bindings",
    }
)


def _load_dormant_inventory() -> Any:
    """Безопасно загрузить constants-only dormant module без Alembic."""

    spec = importlib.util.spec_from_file_location(
        "dormant_2026110802_inventory", DORMANT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_literals(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        else:
            continue
        for target, value in pairs:
            if isinstance(target, ast.Name) and target.id in {
                "revision",
                "down_revision",
                "branch_labels",
                "depends_on",
            }:
                try:
                    values[target.id] = ast.literal_eval(value)
                except ValueError:
                    continue
    return values


def _active_revision_graph() -> dict[str, Any]:
    graph: dict[str, Any] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values = _module_literals(path)
        if "revision" in values:
            graph[str(values["revision"])] = values.get("down_revision")
    return graph


def _parents(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value if item is not None)
    return ()


def test_0802_exists_only_as_pending_dormant_inventory() -> None:
    assert DORMANT_PATH.parent == PENDING_DIR
    assert DORMANT_PATH.is_file()
    assert tuple(PENDING_DIR.glob("*2026110802*.py")) == (DORMANT_PATH,)
    assert not (VERSIONS_DIR / DORMANT_PATH.name).exists()


def test_0802_is_not_active_or_referenced_by_active_graph() -> None:
    graph = _active_revision_graph()
    assert graph
    assert DORMANT_REVISION_ID not in graph
    for parent in graph.values():
        assert DORMANT_REVISION_ID not in _parents(parent)
    children = {
        parent
        for value in graph.values()
        for parent in _parents(value)
    }
    heads = {revision for revision in graph if revision not in children}
    assert DORMANT_REVISION_ID not in heads


def test_pending_has_no_alembic_executable_surface() -> None:
    tree = ast.parse(DORMANT_PATH.read_text(encoding="utf-8"))
    assigned_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in {
                "upgrade",
                "downgrade",
                "_assert_cutover_confirmed",
            }
        if isinstance(node, ast.Assign):
            assigned_names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned_names.add(node.target.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name.split(".")[0] for alias in node.names}
            assert names.isdisjoint({"alembic", "os", "subprocess", "shutil"})
    assert assigned_names.isdisjoint(
        {"revision", "down_revision", "branch_labels", "depends_on"}
    )
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_no_stale_drop_first_immediate_delete_or_fixed_head_runbook() -> None:
    for path in (DORMANT_PATH, REMOVAL_PATH, CUTOVER_PATH):
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "POST_UPGRADE_REMOVAL_CHECKLIST",
            "PLUGIN_CUTOVER_CONFIRMED=true",
            "CUTOVER_ENV",
            "DROP_ORDER",
            "СРАЗУ",
            "рабочая голова остаётся",
            "2026110801",
            "alembic upgrade head",
            "Revision ID:",
            "Revises:",
            "def upgrade",
            "def downgrade",
            "op.drop_table",
            "os.environ",
        ):
            assert forbidden not in source, f"{path}: запрещённый runbook marker {forbidden}"


def test_cutover_narrative_has_no_destructive_runbook() -> None:
    source = CUTOVER_PATH.read_text(encoding="utf-8")
    lower = source.lower()
    for forbidden in (
        "drop-first",
        "сразу удалить",
        "без переноса содержимого",
        "данные dev эфемерны",
        "дропание",
        "дроп module_*",
        "op.drop_table",
        "os.environ",
        "alembic upgrade head",
        "plugin_cutover_confirmed=true",
    ):
        assert forbidden not in lower
    for required in (
        "2026110802",
        "dormant/not active",
        "runtime/startup/deploy",
        "inert historical/target inventory",
        "code-first S4",
        "S5 ADR/review/acceptance",
        "тогдашней current head",
        "schema-only downgrade",
        "env guard",
        "metadata compatibility",
        "не выполняет DB, DML, drop, remove",
    ):
        assert required in source


def test_dormant_barrier_and_future_revision_contract() -> None:
    module = _load_dormant_inventory()
    assert module.DORMANT_REVISION_ID == DORMANT_REVISION_ID
    for token in ("move/copy/register/activate", "versions", "version_locations"):
        assert token in module.DORMANT_CUTOVER_BARRIER
    requirements = "\n".join(module.FUTURE_CUTOVER_REQUIREMENTS)
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


def test_pending_and_removal_preserved_inventories_match() -> None:
    from prodavan.application.plugins.strangler import removal

    module = _load_dormant_inventory()
    assert module.DORMANT_CUTOVER_BARRIER == removal.DORMANT_CUTOVER_BARRIER
    assert module.FUTURE_CUTOVER_REQUIREMENTS == removal.FUTURE_CUTOVER_REQUIREMENTS
    assert (
        module.PRESERVED_MIGRATION_ONLY_INVENTORY
        == removal.PRESERVED_MIGRATION_ONLY_INVENTORY
    )
    assert module.PRESERVED_DYNAMIC_INVENTORY == removal.PRESERVED_DYNAMIC_INVENTORY
    assert module.PRESERVED_PLUGIN_INVENTORY == removal.PRESERVED_PLUGIN_INVENTORY


def test_historical_table_inventory_is_not_a_drop_order() -> None:
    module = _load_dormant_inventory()
    inventory = module.HISTORICAL_MODULE_TABLE_INVENTORY
    assert len(inventory) == 8
    assert set(inventory) == EXPECTED_HISTORICAL_TABLES
    assert tuple(sorted(inventory)) == inventory
    assert not hasattr(module, "DROP_ORDER")
    assert not hasattr(module, "upgrade")
    assert not hasattr(module, "downgrade")


def test_no_startup_or_deploy_cutover_markers() -> None:
    startup_paths = (
        REPO_ROOT / "apps" / "api" / "alembic.ini",
        REPO_ROOT / "apps" / "api" / "alembic" / "env.py",
        REPO_ROOT / "apps" / "api" / "scripts" / "migrate.sh",
        REPO_ROOT / "apps" / "api" / "src" / "prodavan" / "main.py",
        REPO_ROOT / "apps" / "api" / "src" / "prodavan" / "main_plugin_gateway.py",
        REPO_ROOT / "apps" / "api" / "src" / "prodavan" / "main_pod.py",
    )
    for path in startup_paths:
        source = path.read_text(encoding="utf-8")
        for marker in (
            DORMANT_REVISION_ID,
            "PLUGIN_CUTOVER_CONFIRMED",
            "strangler.removal",
            "CODE_FIRST_REMOVAL_INVENTORY",
        ):
            assert marker not in source, f"{path}: startup marker {marker}"

    config_suffixes = {".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".ini"}
    for root in (REPO_ROOT / "infra", REPO_ROOT / ".github"):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in config_suffixes and path.name != "Dockerfile":
                continue
            source = path.read_text(encoding="utf-8")
            for marker in (DORMANT_REVISION_ID, "PLUGIN_CUTOVER_CONFIRMED"):
                assert marker not in source, f"{path}: deploy marker {marker}"


def test_runtime_does_not_import_dormant_removal_inventory() -> None:
    runtime_root = REPO_ROOT / "apps" / "api" / "src" / "prodavan"
    for path in runtime_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.endswith("strangler.removal")
                    for alias in node.names
                ), f"{path}: dormant removal import"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.endswith("strangler.removal"), (
                    f"{path}: dormant removal import"
                )
                if node.module.endswith("application.plugins.strangler"):
                    assert all(alias.name != "removal" for alias in node.names), (
                        f"{path}: dormant removal import"
                    )
