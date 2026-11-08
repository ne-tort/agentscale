"""Liquibase changelog пилота: ровно 2 changesets, реальные typed-колонки,
индекс, DOWN-миграции, отсутствие версии плагина в именах схемы/таблиц.

Критерий 3 §11, §3 v4. Тесты написаны, но НЕ запускаются (verification freeze).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from .conftest import (
    CHANGELOG_PATH,
    ENTRYPOINT_PATH,
    DOCKERFILE_PATH,
    PLUGIN_VERSION,
    read_text,
)

NS = {"lb": "http://www.liquibase.org/xml/ns/dbchangelog"}


@pytest.fixture(scope="module")
def changelog_root() -> ET.Element:
    raw = read_text(CHANGELOG_PATH)
    return ET.fromstring(raw)


@pytest.fixture(scope="module")
def changesets(changelog_root: ET.Element) -> list[ET.Element]:
    return changelog_root.findall("lb:changeSet", NS)


# --- ровно 2 changesets -------------------------------------------------------

def test_changelog_is_well_formed_xml(changelog_root: ET.Element) -> None:
    assert changelog_root.tag.endswith("databaseChangeLog")
    assert changelog_root.get("logicalFilePath") == "migrations/changelog.xml", (
        "логический путь фиксирует checksum независимо от точки монтирования"
    )


def test_changelog_has_exactly_two_changesets(changesets: list[ET.Element]) -> None:
    assert len(changesets) == 2, "ровно 2 changesets: 0001 и 0002"
    assert [cs.get("id") for cs in changesets] == ["0001", "0002"]
    assert all(cs.get("author") for cs in changesets)


def test_each_changeset_has_explicit_rollback(changesets: list[ET.Element]) -> None:
    """§3.3: plan/rollback доступны плагину; у каждого changeset есть DOWN."""
    for cs in changesets:
        rollback = cs.find("lb:rollback", NS)
        assert rollback is not None, f"changeset {cs.get('id')} без rollback"


# --- changeset 0001: greetings, typed колонки --------------------------------

def test_0001_creates_greetings_with_typed_columns(changesets: list[ET.Element]) -> None:
    cs = changesets[0]
    tables = cs.findall("lb:createTable", NS)
    assert len(tables) == 1
    table = tables[0]
    assert table.get("tableName") == "greetings"

    cols = {c.get("name"): c for c in table.findall("lb:column", NS)}
    assert set(cols) == {
        "id", "company_id", "cabinet_id", "project_id", "message", "created_at"
    }
    assert cols["id"].get("type") == "UUID"
    assert cols["company_id"].get("type") == "TEXT"
    assert cols["cabinet_id"].get("type") == "TEXT"
    assert cols["project_id"].get("type") == "TEXT"
    assert cols["message"].get("type") == "TEXT"
    assert cols["created_at"].get("type") == "TIMESTAMPTZ"


def test_0001_constraints_and_defaults(changesets: list[ET.Element]) -> None:
    table = changesets[0].find("lb:createTable", NS)
    assert table is not None
    cols = {c.get("name"): c for c in table.findall("lb:column", NS)}

    pk = cols["id"].find("lb:constraints", NS)
    assert pk is not None and pk.get("primaryKey") == "true" and pk.get("nullable") == "false"

    assert cols["company_id"].find("lb:constraints", NS).get("nullable") == "false"  # type: ignore[union-attr]
    assert cols["message"].find("lb:constraints", NS).get("nullable") == "false"  # type: ignore[union-attr]
    assert cols["created_at"].get("defaultValueComputed") == "now()"
    # cabinet_id/project_id — НЕ nullable=false: детализация внутри БД компании.
    assert cols["cabinet_id"].find("lb:constraints", NS) is None
    assert cols["project_id"].find("lb:constraints", NS) is None


def test_0001_index_on_company_and_project(changesets: list[ET.Element]) -> None:
    indexes = changesets[0].findall("lb:createIndex", NS)
    assert len(indexes) == 1
    idx = indexes[0]
    assert idx.get("tableName") == "greetings"
    assert idx.get("indexName") == "idx_greetings_company_project"
    assert [c.get("name") for c in idx.findall("lb:column", NS)] == ["company_id", "project_id"]


# --- changeset 0002: greeting_stats ------------------------------------------

def test_0002_creates_greeting_stats(changesets: list[ET.Element]) -> None:
    cs = changesets[1]
    tables = cs.findall("lb:createTable", NS)
    assert len(tables) == 1
    table = tables[0]
    assert table.get("tableName") == "greeting_stats"
    cols = {c.get("name"): c for c in table.findall("lb:column", NS)}
    assert set(cols) == {"id", "greetings_count"}
    assert cols["id"].get("type") == "UUID"
    assert cols["id"].find("lb:constraints", NS).get("primaryKey") == "true"  # type: ignore[union-attr]
    assert cols["greetings_count"].get("type") == "INT"
    assert cols["greetings_count"].get("defaultValueNumeric") == "0"
    assert cols["greetings_count"].find("lb:constraints", NS).get("nullable") == "false"  # type: ignore[union-attr]


# --- НЕ jsonb, версия НЕ в имени схемы ---------------------------------------

def test_changelog_uses_real_tables_not_jsonb() -> None:
    raw = read_text(CHANGELOG_PATH)
    # Слово «jsonb» может встречаться в поясняющих комментариях («НЕ jsonb»),
    # поэтому проверяем ТОЛЬКО фактические типы колонок и DDL, а не текст.
    assert '<column name="' in raw
    assert "<createTable" in raw, "реальные DDL-таблицы Liquibase, а не строки данных"
    assert not re.search(r'type="(?:jsonb|json|bytea)"', raw, re.IGNORECASE), (
        "колонка с jsonb/json-типом: плагин обязан использовать typed columns"
    )
    assert not re.search(r'module_instance_data_rows\s*\(', raw)


def test_changelog_column_types_are_all_typed(changesets: list[ET.Element]) -> None:
    """Ни одной бестиповой/JSON-колонки: id UUID, тексты TEXT, счётчик INT.

    Колонки индекса (``<createIndex><column .../>``) типа не имеют — это не
    определение колонки таблицы, поэтому проверяем только ``createTable``.
    """
    allowed = {"UUID", "TEXT", "TIMESTAMPTZ", "INT"}
    for cs in changesets:
        for table in cs.findall("lb:createTable", NS):
            for col in table.findall("lb:column", NS):
                assert col.get("type") in allowed, (
                    f"неожиданный тип колонки: {col.get('name')}={col.get('type')}"
                )


def test_plugin_version_not_in_schema_or_table_names(changesets: list[ET.Element]) -> None:
    """§3.3: версия живёт в DATABASECHANGELOG и в пути статики, не в DDL."""
    raw = read_text(CHANGELOG_PATH)
    assert PLUGIN_VERSION not in raw
    for cs in changesets:
        for element in cs.iter():
            for attr in ("tableName", "indexName"):
                value = element.get(attr) or ""
                assert PLUGIN_VERSION not in value
                assert not re.search(r"v\d+\.\d+\.\d+", value), (
                    f"версия плагина в имени {attr}={value}"
                )
    # Никаких CREATE SCHEMA с версией / module_<version>.
    assert "create schema" not in raw.lower()
    assert f"module_{PLUGIN_VERSION}" not in raw


def test_changelog_has_no_ddl_outside_own_schema() -> None:
    """Плагин мигрирует ТОЛЬКО свою схему; platform-admin создаёт её при grant."""
    raw = read_text(CHANGELOG_PATH).lower()
    assert "create database" not in raw
    assert "create role" not in raw
    assert "drop database" not in raw
    assert "prodavan" not in raw


def test_entrypoint_applies_changelog_and_disables_execute_command() -> None:
    """§3.1/§3.2: liquibase update из changelog, shell-команды запрещены."""
    raw = read_text(ENTRYPOINT_PATH)
    assert "liquibase" in raw and "update" in raw
    assert "changelog.xml" in raw
    assert "LIQUIBASE_ALLOW_EXECUTE_COMMAND=false" in raw
    assert "set -eu" in raw, "fail-closed: любая ошибка миграции завершает контейнер"
    assert "exec" in raw
    # Контейнерный entrypoint, а не хостовый инфраструктурный .sh.
    assert "НЕ является" in raw or "контейнерный" in raw.lower()


def test_dockerfile_pins_liquibase_version_not_latest() -> None:
    raw = read_text(DOCKERFILE_PATH)
    assert re.search(r"LIQUIBASE_VERSION=\d+\.\d+\.\d+", raw)
    # Тег образа как таковой запрещён; упоминание в комментарии-ЗАПРЕТЕ — нет.
    assert not re.search(r"(?:FROM|ARG|image:)\s+\S*:latest\b", raw)
    assert "USER 10001:10001" in raw
    assert "python:3.13-slim" in raw
    assert "HEALTHCHECK" in raw
    # imagePullPolicy задаётся в k8s Deployment, не директивой Dockerfile.
    assert not re.search(r"^\s*(?:ENV|LABEL)\s+imagePullPolicy", raw, re.MULTILINE)


def test_changelog_path_matches_manifest() -> None:
    from .conftest import MANIFEST_PATH
    import json

    manifest = json.loads(read_text(MANIFEST_PATH))
    assert manifest["migrations"]["engine"] == "liquibase"
    rel = Path(manifest["migrations"]["path"])
    assert (CHANGELOG_PATH.parent.parent / rel).resolve() == CHANGELOG_PATH.resolve()
