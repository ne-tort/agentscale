"""Миграция обновления промптов до актуального сида (дефолтная ревизия).

Почему это вообще нужно: промпты живут строками данных модуля, а не в коде.
Предыдущая миграция обновляла их «аддитивно» — файлы правил только
дописывались, AGENTS.md обновлялся по маркеру. В результате правки текста
(чистка мусора, новый `05-tenders.md`) не доезжали до существующих инстансов:
на проде оставались 7 файлов правил без тендерного и старый AGENTS.md.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_API = Path(__file__).resolve().parents[2]
_MIGRATION = _API / "alembic" / "versions" / "2026102301_equipment_prompts_consolidated.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("prompts_consolidated", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_is_linear_head() -> None:
    m = _load_migration()
    assert m.revision == "2026102301"
    assert m.down_revision == "2026102202"


def test_rules_payload_covers_whole_seed_in_priority_order() -> None:
    from prodavan.application.platform.equipment_prompt_content import (
        EQUIPMENT_RULES_FILES,
    )

    m = _load_migration()
    payload = m._rules_files_payload()

    assert [f["name"] for f in payload] == [n for n, _, _ in EQUIPMENT_RULES_FILES]
    assert [f["priority"] for f in payload] == [p for _, _, p in EQUIPMENT_RULES_FILES]
    # тело каждого файла — из сида, а не заглушка
    for entry, (_, body, _) in zip(payload, EQUIPMENT_RULES_FILES):
        assert entry["body"] == body
        assert entry["id"] == f"rules_default_{entry['name']}"


def test_rules_payload_includes_tenders_rule() -> None:
    """Тендерное правило — то, что предыдущая «аддитивная» миграция не добавила."""
    m = _load_migration()
    names = [f["name"] for f in m._rules_files_payload()]
    assert "05-tenders.md" in names
    # приоритет должен ставить его первым
    assert names[0] == "05-tenders.md"


def test_agents_payload_is_the_new_consolidated_text() -> None:
    from prodavan.application.platform.equipment_prompt_content import (
        EQUIPMENT_AGENTS_MD,
    )

    m = _load_migration()
    payload = m._agents_files_payload()

    assert len(payload) == 1
    entry = payload[0]
    assert entry["name"] == "AGENTS.md"
    assert entry["body"] == EQUIPMENT_AGENTS_MD
    # актуальный AGENTS.md ссылается на вынесенное тендерное правило
    assert "05-tenders.md" in entry["body"]
    # и остался операционным листом, а не разросшимся справочником
    assert len(EQUIPMENT_AGENTS_MD.splitlines()) < 160