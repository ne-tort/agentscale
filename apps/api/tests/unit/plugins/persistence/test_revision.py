"""Unit-тесты Alembic-ревизии ``2026110801`` (A10).

Ревизия НЕ выполняется: модуль грузится по пути, ``op`` подменяется шпионом,
а цепочка ревизий проверяется статически через AST всех файлов
``alembic/versions/*.py``. Никаких подключений к БД, alembic/psql не запускаются.

Проверяется: корректность ``revision``/``down_revision`` (= актуальная голова),
``upgrade`` создаёт ровно 6 таблиц (5 базовых + ``plugin_feature_flags``),
наличие FK/unique/индексов и ``server_default``, отсутствие RLS/расширений/
DDL к существующим таблицам, ``downgrade`` дропает в обратном порядке,
и что проекция ``app_version_projection`` не равна каноническому ``manifest``.
"""

from __future__ import annotations

import ast
from typing import Any

from .conftest import (
    MODELS_PATH,
    REPO_ROOT,
    REVISION_PATH,
    VERSIONS_DIR,
    load_revision_module,
    revision_literals,
)

EXPECTED_REVISION = "2026110801"
EXPECTED_DOWN_REVISION = "2026110321"

BASE_TABLES = {
    "plugin_capability_grants",
    "plugin_instances",
    "plugin_artifacts",
    "plugin_audit_events",
    "plugin_event_deliveries",
}
ALL_TABLES = BASE_TABLES | {"plugin_feature_flags"}

#: Таблицы platform-БД prodavan, которые ревизия НЕ ИМЕЕТ ПРАВА трогать.
LEGACY_TABLES = {
    "companies",
    "modules",
    "dynamic_app_definitions",
    "dynamic_app_versions",
    "dynamic_app_grants",
    "dynamic_app_instances",
    "m05_plugin_instances",
    "scope_registry",
}


def _all_revision_literals() -> dict[str, Any]:
    """Собрать ``revision``/``down_revision`` из AST каждого файла ревизий."""

    graph: dict[str, Any] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
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
                }:
                    try:
                        values[target.id] = ast.literal_eval(value)
                    except ValueError:
                        continue
        if "revision" in values:
            graph[str(values["revision"])] = values.get("down_revision")
    return graph


# --------------------------------------------------------------------------- revision / голова цепочки


def test_revision_identifiers() -> None:
    literals = revision_literals()
    assert literals["revision"] == EXPECTED_REVISION
    assert literals["down_revision"] == EXPECTED_DOWN_REVISION
    assert literals.get("branch_labels") is None
    assert literals.get("depends_on") is None


def test_down_revision_is_the_actual_head() -> None:
    """``down_revision`` = прежняя голова цепочки, и продолжает её только мы.

    Голова ``2026110321`` найдена статически (glob + чтение ``down_revision``),
    НЕ через alembic. До добавления этой ревизии на неё не ссылался ни один файл;
    теперь единственный «ребёнок» — сама ``2026110801``.
    """

    graph = _all_revision_literals()
    assert EXPECTED_DOWN_REVISION in graph, "ревизия-голова отсутствует в versions/"
    # Наша собственная ревизия тоже лежит в versions/ и ссылается на голову —
    # это и есть ожидаемое продолжение цепочки, а не «чужая» ветка.
    assert graph.get(EXPECTED_REVISION) == EXPECTED_DOWN_REVISION
    children = [
        revision
        for revision, parent in graph.items()
        if revision != EXPECTED_REVISION
        and (
            parent == EXPECTED_DOWN_REVISION
            or (isinstance(parent, (tuple, list)) and EXPECTED_DOWN_REVISION in parent)
        )
    ]
    assert children == [], (
        f"{EXPECTED_DOWN_REVISION} не голова: на неё ссылаются {children}"
    )


def test_module_exposes_same_identifiers() -> None:
    module = load_revision_module()
    assert module.revision == EXPECTED_REVISION
    assert module.down_revision == EXPECTED_DOWN_REVISION


# --------------------------------------------------------------------------- upgrade()


def test_upgrade_creates_exactly_six_tables(ops: Any) -> None:
    """5 базовых по ТЗ + plugin_feature_flags (решение родителя), и ничего ещё."""

    ops.module.upgrade()
    assert len(ops.created_tables) == 6
    assert set(ops.created_tables) == ALL_TABLES
    assert len(set(ops.created_tables)) == len(ops.created_tables), "есть дубликаты CREATE"


def test_upgrade_creates_parent_before_fk_children(ops: Any) -> None:
    """FK-зависимости соблюдены: artifacts -> instances -> grants."""

    ops.module.upgrade()
    order = {name: index for index, name in enumerate(ops.created_tables)}
    assert order["plugin_artifacts"] < order["plugin_instances"]
    assert order["plugin_instances"] < order["plugin_capability_grants"]


def test_upgrade_does_not_touch_existing_platform_tables(ops: Any) -> None:
    """Жёстко: ни ALTER/DROP/UPDATE к существующим таблицам prodavan."""

    ops.module.upgrade()
    assert set(ops.created_tables).isdisjoint(LEGACY_TABLES)
    assert ops.dropped_tables == []
    for call in ops.calls:
        assert call[0] not in {"alter_column", "drop_column", "add_column"}
    text = REVISION_PATH.read_text(encoding="utf-8")
    # Проверяем именно DDL-конструкции. Слово ``pgaudit`` встречается в
    # пояснительном комментарии («расширение НЕ требуется»), поэтому ищем
    # реальные операторы, а не голую подстроку.
    for forbidden in (
        "CREATE DATABASE",
        "CREATE ROLE",
        "CREATE SCHEMA",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "CREATE POLICY",
        "CREATE EXTENSION",
        "op.execute(",
    ):
        assert forbidden not in text, f"ревизия содержит запрещённую конструкцию: {forbidden}"


def test_upgrade_declares_required_columns(ops: Any) -> None:
    """Ключевые колонки присутствуют в каждой таблице."""

    ops.module.upgrade()
    assert {"company_id", "plugin_instance_id", "artifact_digest", "status"} <= set(
        ops.table_columns["plugin_capability_grants"]
    )
    assert {"active_job_ids", "wake_in_flight", "desired_replicas"} <= set(
        ops.table_columns["plugin_instances"]
    )
    assert {"manifest", "app_version_projection", "oci_digest", "is_current"} <= set(
        ops.table_columns["plugin_artifacts"]
    )
    assert {"sql_digest", "payload_digest", "occurred_at"} <= set(
        ops.table_columns["plugin_audit_events"]
    )
    assert {"subscription_id", "source_event_id", "offset", "partition"} <= set(
        ops.table_columns["plugin_event_deliveries"]
    )
    assert {"company_id", "enabled_modules", "updated_at"} <= set(
        ops.table_columns["plugin_feature_flags"]
    )


def test_upgrade_emits_audit_redaction_columns_only(ops: Any) -> None:
    """Критерий 11: в DDL аудита НЕТ raw sql/body/token/secret."""

    ops.module.upgrade()
    audit_columns = set(ops.table_columns["plugin_audit_events"])
    for forbidden in (
        "sql", "body", "token", "secret", "params", "payload", "details",
        "raw_sql", "request_body", "credentials",
    ):
        assert forbidden not in audit_columns


def test_upgrade_emits_unique_constraints_and_foreign_keys(ops: Any) -> None:
    """Именованные UNIQUE/FK объявлены в DDL."""

    ops.module.upgrade()
    constraints = {
        table: set(names) for table, names in ops.table_constraints.items()
    }
    assert "uq_plugin_capability_grant_company_instance" in constraints["plugin_capability_grants"]
    assert "fk_plugin_capability_grant_instance" in constraints["plugin_capability_grants"]
    assert "uq_plugin_artifact_plugin_version" in constraints["plugin_artifacts"]
    assert "uq_plugin_artifact_oci_digest" in constraints["plugin_artifacts"]
    assert "ck_plugin_artifact_projection_differs" in constraints["plugin_artifacts"]
    assert "fk_plugin_instance_artifact" in constraints["plugin_instances"]
    assert "uq_plugin_event_delivery_dedup" in constraints["plugin_event_deliveries"]
    assert "ck_plugin_feature_flags_enabled_modules_array" in constraints["plugin_feature_flags"]


def test_upgrade_emits_required_indexes(ops: Any) -> None:
    ops.module.upgrade()
    index_names = {item["name"] for item in ops.created_indexes}
    assert {
        "ix_plugin_capability_grants_company",
        "ix_plugin_capability_grants_plugin",
        "ix_plugin_audit_events_company_occurred",
        "ix_plugin_artifacts_plugin_status",
        "ix_plugin_artifacts_is_current",
        "uq_plugin_artifact_current",
        "ix_plugin_instances_company_status",
        "ix_plugin_event_deliveries_status_retry",
    } <= index_names


def test_partial_unique_index_matches_orm_literal(ops: Any) -> None:
    """Литерал WHERE дословно совпадает с ORM — иначе ``alembic check`` увидит drift."""

    ops.module.upgrade()
    partial = next(
        item for item in ops.created_indexes
        if item["name"] == "uq_plugin_artifact_current"
    )
    assert partial["kwargs"]["unique"] is True
    assert str(partial["kwargs"]["postgresql_where"]) == "is_current = true"


def test_upgrade_has_server_defaults(ops: Any) -> None:
    """``server_default`` там, где нужно: JSONB-списки, статусы, false, now()."""

    source = REVISION_PATH.read_text(encoding="utf-8")
    for token in (
        'server_default="[]"',
        'server_default="{}"',
        'server_default="false"',
        'server_default="active"',
        'server_default="installed"',
        'server_default="quarantined"',
        'server_default="pending"',
        'server_default=sa.func.now()',
    ):
        assert token in source, f"пропущен server_default: {token}"


# --------------------------------------------------------------------------- downgrade()


def test_downgrade_drops_all_six_tables_in_reverse_order(ops: Any) -> None:
    ops.module.upgrade()
    ops.dropped_tables.clear()
    ops.module.downgrade()
    assert len(ops.dropped_tables) == 6
    assert set(ops.dropped_tables) == ALL_TABLES
    create_order = {name: index for index, name in enumerate(ops.created_tables)}
    drop_order = {name: index for index, name in enumerate(ops.dropped_tables)}
    for name in ALL_TABLES:
        assert create_order[name] + drop_order[name] == 5, (
            f"{name}: downgrade не обратный к upgrade"
        )


def test_downgrade_touches_no_legacy_tables(ops: Any) -> None:
    ops.module.downgrade()
    assert set(ops.dropped_tables).isdisjoint(LEGACY_TABLES)


# --------------------------------------------------------------------------- форма проекции (§5.1)


def test_revision_keeps_manifest_and_projection_distinct(ops: Any) -> None:
    """Проекция не равна каноническому manifest: CHECK + две разные колонки."""

    ops.module.upgrade()
    columns = ops.table_columns["plugin_artifacts"]
    assert "manifest" in columns and "app_version_projection" in columns
    assert columns.count("manifest") == 1
    assert "ck_plugin_artifact_projection_differs" in ops.table_constraints["plugin_artifacts"]
    source = REVISION_PATH.read_text(encoding="utf-8")
    assert "manifest <> app_version_projection" in source
    assert "AppVersion" in source
    # Канонический manifest сохраняется под ключом ``plugin_manifest`` в проекции —
    # это инвариант ORM-хелпера, а не DDL; проверяем оба файла по их роли.
    assert "plugin_manifest" in MODELS_PATH.read_text(encoding="utf-8")


def test_revision_documents_dev_ephemeral_state() -> None:
    """Комментарий фиксирует: данные dev эфемерны, это состояние платформы."""

    source = REVISION_PATH.read_text(encoding="utf-8")
    assert "эфемерны" in source
    assert "СОСТОЯНИЕ ПЛАТФОРМЫ" in source
    assert "tenant_<company>" in source
    assert "переноса" in source and "бэкап" in source


def test_revision_file_lives_in_versions_dir() -> None:
    assert REVISION_PATH.parent == VERSIONS_DIR
    assert REVISION_PATH.name == "2026110801_plugin_platform.py"
    assert REPO_ROOT.name.startswith("wt-") or (REPO_ROOT / "apps" / "api").is_dir()
