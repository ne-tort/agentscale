"""Unit-тесты финальной Alembic-ревизии ``2026110802`` (§8 этап 6).

Ревизия НЕ выполняется: модуль грузится по пути, ``op`` подменяется шпионом
(``RecordingOps`` из conftest.py), цепочка ревизий проверяется статически через
AST всех файлов ``alembic/versions/*.py``. Никаких подключений к БД, alembic/psql
не запускаются.

Проверяется: ``revision``/``down_revision``, единственная голова цепочки,
``upgrade()`` БЕЗ ``PLUGIN_CUTOVER_CONFIRMED=true`` Отказывает и не трогает
ни одной таблицы, а С подтверждением дропает РОВНО 8 таблиц ``module_*``
в корректном FK-порядке и ни одной лишней, ``downgrade()`` бросает
``NotImplementedError``, в исходнике НЕТ DDL/DML переноса данных
DDL/DML переноса данных (CREATE TABLE/INSERT/SELECT/COPY/ALTER TABLE ... ADD/
pg_dump/backup/snapshot/dual), docstring явно фиксирует «без переноса
содержимого».
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from .conftest import REPO_ROOT, RecordingOps, VERSIONS_DIR

REVISION_ID = "2026110802"
DOWN_REVISION_ID = "2026110801"

REVISION_PATH = VERSIONS_DIR / "2026110802_remove_declarative_app.py"

#: ТОЧНЫЙ порядок дропа: дети перед родителями (обратный FK-зависимостям).
#: Проверен по models/modules.py и ревизиям 2026082703/2026082705/2026090503.
EXPECTED_DROP_ORDER: tuple[str, ...] = (
    "module_instance_data_rows",
    "module_instance_meta_documents",
    "module_instances",
    "module_project_bindings",
    "module_cabinet_bindings",
    "module_company_grants",
    "module_meta_documents",
    "modules",
)

EXPECTED_TABLES: frozenset[str] = frozenset(EXPECTED_DROP_ORDER)

#: Таблицы, которые ревизия НЕ ИМЕЕТ ПРАВА трогать (plugin_* + чужие legacy).
FORBIDDEN_TABLES: frozenset[str] = frozenset(
    {
        "plugin_artifacts",
        "plugin_instances",
        "plugin_capability_grants",
        "plugin_audit_events",
        "plugin_event_deliveries",
        "plugin_feature_flags",
        "companies",
        "projects",
        "cabinet_instances",
        "dynamic_app_definitions",
        "dynamic_app_versions",
    }
)


def load_removal_revision() -> Any:
    """Загрузить модуль ревизии по абсолютному пути (``versions`` не пакет)."""

    spec = importlib.util.spec_from_file_location(
        "revision_2026110802_remove_declarative_app", REVISION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_literals(path: Path) -> dict[str, Any]:
    """Достать ``revision``/``down_revision``/... из AST одного файла."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
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


def _revision_graph() -> dict[str, Any]:
    """Собрать ``revision -> down_revision`` из AST всех файлов versions/."""

    graph: dict[str, Any] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values = _module_literals(path)
        if "revision" in values:
            graph[str(values["revision"])] = values.get("down_revision")
    return graph


@pytest.fixture
def removal_ops(monkeypatch: pytest.MonkeyPatch) -> RecordingOps:
    """Подменить ``revision.op`` шпионом, вернуть его для проверок.

    Тесты проверяют ИСПОЛНЯЕМОСТЬ ревизии, поэтому явно включают рубильник
    ``PLUGIN_CUTOVER_CONFIRMED`` (без него upgrade() обязан отказать).
    """

    monkeypatch.setenv("PLUGIN_CUTOVER_CONFIRMED", "true")
    module = load_removal_revision()
    recorder = RecordingOps()
    recorder.module = module
    monkeypatch.setattr(module, "op", recorder, raising=True)
    return recorder


def test_upgrade_refuses_without_cutover_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без явного флага НЕ трогаем ни одной таблицы (защита dev-контура).

    scripts/migrate.sh / CI Gate зовут `alembic upgrade head` при каждом
    деплое. Результат автоматического дропа 8 таблиц под работающим
    declarative-app был бы фатален, поэтому ревизия обязана громко упасть.
    """
    monkeypatch.delenv("PLUGIN_CUTOVER_CONFIRMED", raising=False)
    module = load_removal_revision()
    recorder = RecordingOps()
    recorder.module = module
    monkeypatch.setattr(module, "op", recorder, raising=True)

    with pytest.raises(RuntimeError, match="cutover НЕ подтверждён"):
        module.upgrade()
    assert recorder.calls == [], "без подтверждения не должно быть ни одного DDL"


def test_upgrade_refuses_false_or_garbage_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строгое сравнение: 'false'/'1'/пустота НЕ являются подтверждением."""
    module = load_removal_revision()
    recorder = RecordingOps()
    recorder.module = module
    monkeypatch.setattr(module, "op", recorder, raising=True)
    for value in ("", "false", "1", "yes", "trueish"):
        monkeypatch.setenv("PLUGIN_CUTOVER_CONFIRMED", value)
        with pytest.raises(RuntimeError, match="cutover НЕ подтверждён"):
            module.upgrade()
    assert recorder.calls == []


# --------------------------------------------------------------------------- revision / голова цепочки


def test_revision_file_lives_in_versions_dir() -> None:
    assert REVISION_PATH.parent == VERSIONS_DIR
    assert REVISION_PATH.name == "2026110802_remove_declarative_app.py"
    assert REPO_ROOT.name.startswith("wt-") or (REPO_ROOT / "apps" / "api").is_dir()


def test_revision_identifiers() -> None:
    literals = _module_literals(REVISION_PATH)
    assert literals["revision"] == REVISION_ID
    assert literals["down_revision"] == DOWN_REVISION_ID
    assert literals.get("branch_labels") is None
    assert literals.get("depends_on") is None


def test_module_exposes_same_identifiers() -> None:
    module = load_removal_revision()
    assert module.revision == REVISION_ID
    assert module.down_revision == DOWN_REVISION_ID


def test_down_revision_is_previous_head_and_single_head() -> None:
    """``2026110801`` — прежняя голова; после нас ЕДИНСТВЕННАЯ голова ``2026110802``.

    Статически (AST) проверяем: нет висячих ``down_revision``, ровно одна
    вершина без детей, и ``2026110801`` больше не голова (на неё ссылаемся мы).
    """

    graph = _revision_graph()
    assert DOWN_REVISION_ID in graph, "прежняя голова отсутствует в versions/"
    assert graph[REVISION_ID] == DOWN_REVISION_ID

    children: dict[str, list[str]] = {}
    for revision, parent in graph.items():
        parents = parent if isinstance(parent, (tuple, list)) else [parent]
        for item in parents:
            if item is not None:
                children.setdefault(str(item), []).append(revision)

    heads = sorted(revision for revision in graph if not children.get(revision))
    assert heads == [REVISION_ID], f"ожидалась одна голова {REVISION_ID}, получили {heads}"
    assert children.get(DOWN_REVISION_ID) == [REVISION_ID], (
        "2026110801 должен продолжаться только ревизией 2026110802"
    )
    # Ни один down_revision не указывает на несуществующую ревизию.
    dangling = {
        f"{rev} -> {parent}"
        for rev, parent in graph.items()
        for parent in (parent if isinstance(parent, (tuple, list)) else [parent])
        if parent is not None and str(parent) not in graph
    }
    assert not dangling, f"висячие down_revision: {sorted(dangling)}"


# --------------------------------------------------------------------------- upgrade()


def test_upgrade_drops_exactly_eight_tables_in_correct_order(
    removal_ops: RecordingOps,
) -> None:
    """РОВНО 8 таблиц, в порядке детей-перед-родителями, ни одной лишней."""

    removal_ops.module.upgrade()
    assert removal_ops.dropped_tables == list(EXPECTED_DROP_ORDER), (
        f"нарушен FK-порядок дропа: {removal_ops.dropped_tables}"
    )
    assert len(removal_ops.dropped_tables) == 8
    assert len(set(removal_ops.dropped_tables)) == 8, "есть дубликаты DROP"
    assert set(removal_ops.dropped_tables) == EXPECTED_TABLES


def test_upgrade_drop_order_respects_fk_dependencies(
    removal_ops: RecordingOps,
) -> None:
    """Каждая зависимая таблица дропается РАНЬШЕ своего родителя."""

    removal_ops.module.upgrade()
    order = {name: i for i, name in enumerate(removal_ops.dropped_tables)}
    assert order["module_instance_data_rows"] < order["module_instances"]
    assert order["module_instance_meta_documents"] < order["module_instances"]
    assert order["module_instances"] < order["modules"]
    assert order["module_project_bindings"] < order["modules"]
    assert order["module_cabinet_bindings"] < order["modules"]
    assert order["module_company_grants"] < order["modules"]
    assert order["module_meta_documents"] < order["modules"]
    assert order["modules"] == 7, "modules обязан дропаться последним"


def test_upgrade_touches_no_other_tables(removal_ops: RecordingOps) -> None:
    """Ни plugin_*, ни чужие legacy-таблицы, ни create/alter/execute."""

    removal_ops.module.upgrade()
    assert set(removal_ops.dropped_tables).isdisjoint(FORBIDDEN_TABLES)
    assert set(removal_ops.dropped_tables) == EXPECTED_TABLES
    assert removal_ops.created_tables == []
    assert removal_ops.created_indexes == []
    assert [call[0] for call in removal_ops.calls] == ["drop_table"] * 8
    for call in removal_ops.calls:
        assert call[0] not in {
            "create_table",
            "create_index",
            "drop_index",
            "execute",
            "alter_column",
            "add_column",
            "drop_column",
        }


def test_upgrade_matches_contexts_registry() -> None:
    """Набор таблиц совпадает с MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER."""

    from prodavan.application.plugins.strangler.contexts import (
        MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER,
    )

    assert set(MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER) == EXPECTED_TABLES
    assert len(MODULE_TABLES_DROPPED_WITHOUT_DATA_TRANSFER) == 8


# --------------------------------------------------------------------------- downgrade()


def test_downgrade_raises_not_implemented_error() -> None:
    """ЯВНЫЙ запрет DOWN-миграции: не «тихий pass», а NotImplementedError."""

    module = load_removal_revision()
    with pytest.raises(NotImplementedError) as exc:
        module.downgrade()
    message = str(exc.value)
    assert "2026110802" in message
    assert "downgrade" in message.lower() or "DOWN" in message
    assert "фича-флаг" in message or "plugin_enabled_modules" in message


def test_downgrade_does_not_recreate_tables(removal_ops: RecordingOps) -> None:
    """Даже при подменённом ``op`` downgrade не создаёт таблиц/индексов."""

    with pytest.raises(NotImplementedError):
        removal_ops.module.downgrade()
    assert removal_ops.created_tables == []
    assert removal_ops.created_indexes == []
    assert removal_ops.calls == []


# --------------------------------------------------------------------------- контракт исходника


def test_source_has_no_data_transfer_ddl_or_dml() -> None:
    """В исходнике НЕТ CREATE TABLE/INSERT/SELECT/COPY/ALTER ADD/pg_dump/..."""

    source = REVISION_PATH.read_text(encoding="utf-8")
    upper = source.upper()
    for forbidden in (
        "CREATE TABLE",
        "INSERT INTO",
        "INSERT ",
        "SELECT ",
        "COPY ",
        "ALTER TABLE",
        "PG_DUMP",
        "BACKUP",
        "SNAPSHOT",
        "DUAL",
        "CREATE INDEX",
        "CREATE POLICY",
        "CREATE EXTENSION",
        "ROW LEVEL SECURITY",
        "OP.EXECUTE(",
    ):
        assert forbidden not in upper, (
            f"ревизия содержит запрещённую конструкцию переноса/DDL: {forbidden}"
        )


def test_source_has_only_drop_table_calls() -> None:
    """В теле ревизии единственный вызов ``op.*`` — ``op.drop_table`` в цикле,
    а ``DROP_ORDER`` содержит ровно 8 имён (иначе цикл дропнет не то/не столько).
    """

    tree = ast.parse(REVISION_PATH.read_text(encoding="utf-8"))
    op_calls: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            op_calls.append(node.func.attr)
    assert op_calls == ["drop_table"], (
        f"ожидался только op.drop_table, получили {op_calls}"
    )
    module = load_removal_revision()
    assert module.DROP_ORDER == EXPECTED_DROP_ORDER
    assert len(module.DROP_ORDER) == 8


def test_upgrade_requires_cutover_env_flag_in_docstring_and_checklist() -> None:
    """Рубильник зафиксирован и в docstring ревизии, и в чек-листе родителя."""

    from prodavan.application.plugins.strangler.removal import REMOVAL_BOUNDARIES

    doc = load_removal_revision().__doc__ or ""
    assert "PLUGIN_CUTOVER_CONFIRMED=true" in doc
    assert "RuntimeError" in doc
    assert any("PLUGIN_CUTOVER_CONFIRMED=true" in item for item in REMOVAL_BOUNDARIES)
    source = REVISION_PATH.read_text(encoding="utf-8")
    assert "CUTOVER_ENV" in source and "PLUGIN_CUTOVER_CONFIRMED" in source
    assert "raise RuntimeError" in source
    assert "_assert_cutover_confirmed()" in source


def test_docstring_fixes_no_content_transfer() -> None:
    """Docstring ЯВНО фиксирует «без переноса содержимого» и границы."""

    module = load_removal_revision()
    doc = module.__doc__ or ""
    assert "БЕЗ ПЕРЕНОСА СОДЕРЖИМОГО" in doc
    assert "без переноса содержимого" in doc
    assert "module_*" in doc
    assert "plugin_*" in doc
    assert "tenant_" in doc
    assert "эфемерны" in doc
    assert "DOWN" in doc
    assert "NotImplementedError" in doc


def test_downgrade_has_russian_explanation() -> None:
    """Комментарий/downgrade по-русски объясняет: dev, откат = фича-флаг."""

    source = REVISION_PATH.read_text(encoding="utf-8")
    assert "данные dev эфемерны" in source.lower() or "Данные dev эфемерны" in source
    assert "выключить фича-флаг" in source
    assert "raise NotImplementedError" in source
    assert "тихого pass" in source
