"""WAVE11: миграции 2026100527/2026100528 — довоз промптов «Подбора техники».

Проверяет чистую логику аддитивного слияния: недостающие правила дописываются,
уже существующие (в том числе отредактированные пользователем) не трогаются;
системный AGENTS.md заменяется только если он явно не кастомизирован.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from prodavan.application.platform.equipment_prompt_content import (
    EQUIPMENT_AGENTS_MD,
    EQUIPMENT_RULES_FILES,
)
from prodavan.application.platform.product_module_seeds import _equipment_prompt_seed_rows

_API_ROOT = Path(__file__).resolve().parents[2]

_STOCK_HEADER = "# Prodavan — агент подбора техники"


def _load_migration(filename: str, alias: str) -> ModuleType:
    path = _API_ROOT / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    return _load_migration("2026100527_equipment_prompts_refresh.py", "prompts_refresh_27")


@pytest.fixture(scope="module")
def agents_migration() -> ModuleType:
    return _load_migration("2026100528_agents_md_refresh_fix.py", "agents_md_refresh_28")


def _seed_row(row_id: str) -> dict:
    row = next(r for r in _equipment_prompt_seed_rows() if r["row_id"] == row_id)
    return dict(row["body"])


# ------------------------------------------------------- правила (2026100527)


def test_merge_rules_files_is_additive(migration: ModuleType) -> None:
    """Старый инстанс (5 правил) догоняется до актуального сида (7 правил)."""
    legacy_names = {
        "10-request.md",
        "20-search.md",
        "30-identify.md",
        "40-groups.md",
        "50-rank.md",
    }
    legacy = [
        dict(f)
        for f in _seed_row("equipment_prompts_rules_default")["files_json"]
        if f["name"] in legacy_names
    ]
    assert len(legacy) == 5
    # пользователь отредактировал одно правило — его текст нельзя затирать
    edited = next(f for f in legacy if f["name"] == "40-groups.md")
    edited["body"] = "МОЯ ПРАВКА"

    merged, added = migration._merge_rules_files(legacy)

    assert sorted(added) == ["60-builds.md", "70-ready-builds.md"]
    assert [f["name"] for f in merged] == [n for n, _, _ in EQUIPMENT_RULES_FILES]
    assert next(f for f in merged if f["name"] == "40-groups.md")["body"] == "МОЯ ПРАВКА"
    priorities = [f["priority"] for f in merged]
    assert priorities == sorted(priorities)


def test_merge_rules_files_idempotent(migration: ModuleType) -> None:
    """Повторный прогон ничего не дописывает."""
    current = _seed_row("equipment_prompts_rules_default")["files_json"]
    merged, added = migration._merge_rules_files(current)
    assert added == []
    assert [f["name"] for f in merged] == [f["name"] for f in current]


def test_merge_rules_files_handles_empty(migration: ModuleType) -> None:
    """Пустой files_json не роняет миграцию — правила создаются с нуля."""
    merged, added = migration._merge_rules_files([])
    assert sorted(added) == sorted(n for n, _, _ in EQUIPMENT_RULES_FILES)
    assert len(merged) == len(EQUIPMENT_RULES_FILES)
    assert all(str(f["body"]).strip() for f in merged)


# --------------------------------------------------- AGENTS.md (2026100528)


def test_agents_row_updated_when_stock(agents_migration: ModuleType) -> None:
    """Стоковый AGENTS.md старой версии заменяется актуальным.

    Регресс 2026100527: текст лежит в files_json[name == "AGENTS.md"].body,
    а не в body["body"] — из-за этого строка считалась кастомизированной и
    пропускалась, поэтому системный промпт не обновлялся.
    """
    legacy = _seed_row("equipment_prompts_agents_default")
    for f in legacy["files_json"]:
        if f["name"] == "AGENTS.md":
            f["body"] = _STOCK_HEADER + "\n\nСтарая версия без каталога сборок.\n"

    new_body, reason = agents_migration._refresh_agents_body(legacy)

    assert reason == "updated"
    assert new_body is not None
    text = next(f["body"] for f in new_body["files_json"] if f["name"] == "AGENTS.md")
    assert text == EQUIPMENT_AGENTS_MD
    assert "ready_builds_catalog" in text
    assert "70-ready-builds.md" in text


def test_agents_row_skipped_when_customized(agents_migration: ModuleType) -> None:
    """Кастомизированный системный промпт не затирается."""
    legacy = _seed_row("equipment_prompts_agents_default")
    for f in legacy["files_json"]:
        if f["name"] == "AGENTS.md":
            f["body"] = "# Мой собственный промпт закупщика\n\nОсобые правила компании.\n"

    new_body, reason = agents_migration._refresh_agents_body(legacy)

    assert reason == "customized"
    assert new_body is None


def test_agents_row_idempotent(agents_migration: ModuleType) -> None:
    """Актуальный промпт повторно не переписывается."""
    current = _seed_row("equipment_prompts_agents_default")
    new_body, reason = agents_migration._refresh_agents_body(current)
    assert reason == "already_current"
    assert new_body is None


def test_agents_row_without_agents_file_is_ignored(agents_migration: ModuleType) -> None:
    new_body, reason = agents_migration._refresh_agents_body({"files_json": []})
    assert reason == "no_agents_file"
    assert new_body is None


def test_seed_agents_md_matches_migration_expectations() -> None:
    """Маркер и заголовок в текущем сиде совпадают с тем, что ждёт миграция.

    Иначе миграция считала бы свежий промпт устаревшим и переписывала его при
    каждом применении.
    """
    assert EQUIPMENT_AGENTS_MD.lstrip().startswith(_STOCK_HEADER)
    assert "ready_builds_catalog" in EQUIPMENT_AGENTS_MD
