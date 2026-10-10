"""WAVE11: миграция 2026100527 — довоз промптов «Подбора техники» до инстансов.

Проверяет чистую логику аддитивного слияния: недостающие правила дописываются,
уже существующие (в том числе отредактированные пользователем) не трогаются.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from prodavan.application.platform.equipment_prompt_content import EQUIPMENT_RULES_FILES
from prodavan.application.platform.product_module_seeds import _equipment_prompt_seed_rows

_MIGRATION_PATH = (
    # tests/unit/<this file> → apps/api
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2026100527_equipment_prompts_refresh.py"
)


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prompts_refresh_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_rules_files() -> list[dict]:
    row = next(
        r for r in _equipment_prompt_seed_rows() if r["row_id"] == "equipment_prompts_rules_default"
    )
    return [dict(f) for f in row["body"]["files_json"]]


def test_merge_rules_files_is_additive(migration: ModuleType) -> None:
    """Старый инстанс (5 правил) догоняется до актуального сида (7 правил)."""
    legacy_names = {
        "10-request.md",
        "20-search.md",
        "30-identify.md",
        "40-groups.md",
        "50-rank.md",
    }
    legacy = [f for f in _seed_rules_files() if f["name"] in legacy_names]
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
    current = _seed_rules_files()
    merged, added = migration._merge_rules_files(current)
    assert added == []
    assert [f["name"] for f in merged] == [f["name"] for f in current]


def test_merge_rules_files_handles_empty(migration: ModuleType) -> None:
    """Пустой/битый files_json не роняет миграцию — правила создаются с нуля."""
    merged, added = migration._merge_rules_files([])
    assert sorted(added) == sorted(n for n, _, _ in EQUIPMENT_RULES_FILES)
    assert len(merged) == len(EQUIPMENT_RULES_FILES)
    assert all(f["body"].strip() for f in merged)


def test_agents_marker_and_header_match_current_seed() -> None:
    """Маркер актуальности AGENTS.md действительно есть в текущем сиде.

    Иначе миграция сочла бы свежий промпт устаревшим и переписывала бы его
    при каждом применении.
    """
    from prodavan.application.platform.equipment_prompt_content import EQUIPMENT_AGENTS_MD

    assert EQUIPMENT_AGENTS_MD.lstrip().startswith("# Prodavan — агент подбора техники")
    assert "ready_builds_catalog" in EQUIPMENT_AGENTS_MD
