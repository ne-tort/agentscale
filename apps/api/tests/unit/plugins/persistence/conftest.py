"""Общие фикстуры: загрузка ревизии по пути и ``op``-шпион без БД.

``alembic/versions`` не является import-пакетом (имена файлов начинаются с цифр),
поэтому ревизия грузится через ``importlib.util.spec_from_file_location``.
Вызовы ``upgrade()``/``downgrade()`` перехватываются подменённым прокси ``op``:
никакого подключения к PostgreSQL, никакого запуска alembic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import ast
from typing import Any

import pytest


#: Корень репозитория (worktree): ``.../apps/api/tests/unit/plugins/persistence``
#: -> поднимаемся на 6 уровней до ``wt-migration``.
REPO_ROOT = Path(__file__).resolve().parents[6]

REVISION_PATH = (
    REPO_ROOT
    / "apps"
    / "api"
    / "alembic"
    / "versions"
    / "2026110801_plugin_platform.py"
)

MODELS_PATH = (
    REPO_ROOT
    / "apps"
    / "api"
    / "src"
    / "prodavan"
    / "infrastructure"
    / "persistence"
    / "models"
    / "plugin_platform.py"
)

VERSIONS_DIR = REVISION_PATH.parent


def load_revision_module() -> Any:
    """Загрузить модуль ревизии по абсолютному пути (без выполнения DDL)."""

    spec = importlib.util.spec_from_file_location(
        "revision_2026110801_plugin_platform", REVISION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def revision_literals() -> dict[str, Any]:
    """Достать ``revision``/``down_revision`` из AST ревизии (без импорта alembic)."""

    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"), filename=str(REVISION_PATH))
    values: dict[str, Any] = {}
    for node in tree.body:
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
            value: ast.expr = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {
                "revision",
                "down_revision",
                "branch_labels",
                "depends_on",
            }:
                values[target.id] = ast.literal_eval(value)
    return values


class RecordingOps:
    """Минимальный stand-in для ``alembic.op``: собирает таблицы и вызовы."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.created_tables: list[str] = []
        self.dropped_tables: list[str] = []
        self.table_columns: dict[str, list[str]] = {}
        self.table_constraints: dict[str, list[str]] = {}
        self.created_indexes: list[dict[str, Any]] = []
        #: Модуль ревизии с подменённым ``op``. Заполняется фикстурой ``ops``:
        #: вызывать надо именно его ``upgrade()``/``downgrade()``, иначе запрос
        #: уйдёт в настоящий alembic-прокси.
        self.module: Any = None

    def create_table(self, table_name: str, *columns: Any, **kwargs: Any) -> None:
        self.created_tables.append(table_name)
        self.calls.append(("create_table", table_name))
        names: list[str] = []
        constraints: list[str] = []
        for column in columns:
            # У SQLAlchemy Column есть ``name``; у CONSTRAINT-объектов — ``name`` тоже,
            # но у них нет ``primary_key``. Различаем по наличию атрибута.
            if hasattr(column, "primary_key"):
                names.append(str(column.name))
            elif hasattr(column, "name") and column.name is not None:
                constraints.append(str(column.name))
        self.table_columns[table_name] = names
        self.table_constraints[table_name] = constraints
        # Именованные ограничения могут приходить и в kwargs (редкий случай).
        for key, value in kwargs.items():
            if key.endswith("constraint") and hasattr(value, "name"):
                constraints.append(str(value.name))

    def create_index(
        self, index_name: str, table_name: str, columns: Any, **kwargs: Any
    ) -> None:
        self.created_indexes.append(
            {
                "name": index_name,
                "table": table_name,
                "columns": tuple(columns),
                "kwargs": kwargs,
            }
        )
        self.calls.append(("create_index", table_name, index_name))

    def drop_index(self, index_name: str, table_name: str | None = None, **kwargs: Any) -> None:
        self.calls.append(("drop_index", table_name, index_name))

    def drop_table(self, table_name: str, **kwargs: Any) -> None:
        self.dropped_tables.append(table_name)
        self.calls.append(("drop_table", table_name))

    def execute(self, statement: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", str(statement)))

    def get_bind(self) -> None:
        # Ревизия 2026110801 не читает каталог: если вдруг начнёт — тест увидит None.
        return None


@pytest.fixture
def revision() -> Any:
    return load_revision_module()


@pytest.fixture
def ops(monkeypatch: pytest.MonkeyPatch) -> RecordingOps:
    """Подменить ``revision.op`` шпионом и вернуть его для проверок."""

    module = load_revision_module()
    recorder = RecordingOps()
    recorder.module = module
    monkeypatch.setattr(module, "op", recorder, raising=True)
    return recorder
