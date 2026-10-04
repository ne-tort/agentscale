"""data.select_row (select_offer_primary) — контур проекта и кросс-чатовая
связность («Закупка» распределяет товары между поставщиками разных заказов).

Регрессии, которые здесь ловим:
- project_id терялся между invoke() и _select_row → пост-пайплайн уходил в
  cabinet-контур и «Закупка» пересобиралась только по активному чату;
- выбор работал только в активном чате: товар из ДРУГОГО чата (агрегат
  Закупки) нельзя было ни увидеть выбранным, ни переключить;
- повторный клик по выбранному офферу не снимал выбор (возврат к автовыбору
  best был невозможен).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import prodavan.application.modules.equipment_offers_service as offers_mod
from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="u1", roles=frozenset())


# Два чата проекта: offer_a1/offer_a2 в чате A (позиция line_a), offer_b1 в
# чате B (позиция line_b). Все строки несут session_id — как реальные row dicts.
STORE: dict[str, list[dict[str, Any]]] = {}


def _seed_store() -> None:
    STORE.clear()
    STORE.update(
        {
            "found_offers": [
                {
                    "row_id": "offer_a1",
                    "session_id": "chat_a",
                    "body": {"line_id": "line_a", "seller": "Иванов", "is_selected": True},
                },
                {
                    "row_id": "offer_a2",
                    "session_id": "chat_a",
                    "body": {"line_id": "line_a", "seller": "Петров", "is_selected": False},
                },
                {
                    "row_id": "offer_b1",
                    "session_id": "chat_b",
                    "body": {"line_id": "line_b", "seller": "Иванов", "is_selected": False},
                },
            ],
            "request_lines": [
                {
                    "row_id": "line_a",
                    "session_id": "chat_a",
                    "body": {"title": "SSD", "selected_offer_id": "offer_a1", "status": "selected"},
                },
                {
                    "row_id": "line_b",
                    "session_id": "chat_b",
                    "body": {"title": "RAM", "status": "matched"},
                },
            ],
        }
    )


class FakeRowIO:
    """Подмена ModuleRowIO: списки/запись по бакету session_id."""

    def __init__(self, session, *, cabinet_id, project_id, principal, employee, session_id):  # noqa: ANN001, D107
        self._sid = session_id
        self._project_id = project_id

    async def list(self, table_slug: str) -> list[dict[str, Any]]:
        return [dict(r) for r in STORE.get(table_slug, []) if r.get("session_id") == self._sid]

    async def list_project_wide(self, table_slug: str) -> list[dict[str, Any]]:
        return [dict(r) for r in STORE.get(table_slug, [])]

    async def create(self, table_slug: str, body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        raise AssertionError("select_row не создаёт строк")

    async def update(self, table_slug: str, row_id: str, body: dict[str, Any]) -> dict[str, Any]:
        for row in STORE.get(table_slug, []):
            if row["row_id"] == row_id:
                # запись должна идти в бакет самой строки
                assert row.get("session_id") == self._sid, (
                    f"write {table_slug}/{row_id} в чужую сессию {self._sid}"
                )
                row["body"] = dict(body)
                return dict(row)
        raise AssertionError(f"update of missing row {table_slug}/{row_id}")

    async def delete(self, table_slug: str, row_id: str) -> bool:  # noqa: ARG002
        raise AssertionError("select_row не удаляет строк")


def _executor() -> ModuleActionExecutor:
    return ModuleActionExecutor(session=MagicMock())  # type: ignore[arg-type]


def _params() -> dict[str, Any]:
    return {
        "table_slug": "found_offers",
        "select_field": "is_selected",
        "group_by": "line_id",
        "parent": {
            "table_slug": "request_lines",
            "id_from": "line_id",
            "set_field": "selected_offer_id",
        },
    }


def _patch_io(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    _seed_store()
    monkeypatch.setattr(offers_mod, "ModuleRowIO", FakeRowIO)
    pipeline_calls: list[dict[str, Any]] = []

    async def _budget(self, **kwargs):  # noqa: ANN001, ANN003
        pipeline_calls.append(kwargs)

    monkeypatch.setattr(ModuleActionExecutor, "maybe_auto_budget_sync", _budget)
    return pipeline_calls


@pytest.mark.asyncio
async def test_select_row_same_chat_switches_and_scopes_pipeline(monkeypatch) -> None:
    calls = _patch_io(monkeypatch)
    executor = _executor()

    result = await executor._select_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        params=_params(),
        row_id="offer_a2",
        principal=_principal(),
        employee=None,
        project_id="proj_1",
        session_id="chat_a",
    )

    assert result["selected"] is True
    offers = {r["row_id"]: r["body"] for r in STORE["found_offers"]}
    assert offers["offer_a2"]["is_selected"] is True
    assert offers["offer_a1"]["is_selected"] is False  # снят выбор у соседа
    assert offers["offer_b1"]["is_selected"] is False  # другой чат не тронут
    line_a = next(r for r in STORE["request_lines"] if r["row_id"] == "line_a")
    assert line_a["body"]["selected_offer_id"] == "offer_a2"
    assert line_a["body"]["status"] == "selected"
    # пайплайн запущен с project_id и сессией целевой строки (без cabinet-fallback)
    assert len(calls) == 1
    assert calls[0]["project_id"] == "proj_1"
    assert calls[0]["session_id"] == "chat_a"


@pytest.mark.asyncio
async def test_select_row_toggle_off_returns_to_auto_best(monkeypatch) -> None:
    calls = _patch_io(monkeypatch)
    executor = _executor()

    result = await executor._select_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        params=_params(),
        row_id="offer_a1",  # уже выбран
        principal=_principal(),
        employee=None,
        project_id="proj_1",
        session_id="chat_a",
    )

    assert result["selected"] is False
    offers = {r["row_id"]: r["body"] for r in STORE["found_offers"]}
    assert offers["offer_a1"]["is_selected"] is False
    assert offers["offer_a2"]["is_selected"] is False
    line_a = next(r for r in STORE["request_lines"] if r["row_id"] == "line_a")
    assert line_a["body"]["selected_offer_id"] is None
    assert line_a["body"]["status"] == "matched"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_select_row_cross_chat_writes_into_rows_own_chat(monkeypatch) -> None:
    """Закупка: выбираем товар чата B, находясь в чате A — запись идёт в
    бакет чата B, пайплайн пересчитывает оба чата (его бюджет + наше зеркало)."""
    calls = _patch_io(monkeypatch)
    executor = _executor()

    result = await executor._select_row(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        params=_params(),
        row_id="offer_b1",
        principal=_principal(),
        employee=None,
        project_id="proj_1",
        session_id="chat_a",
    )

    assert result["selected"] is True
    offers = {r["row_id"]: r["body"] for r in STORE["found_offers"]}
    assert offers["offer_b1"]["is_selected"] is True
    # выборы чата A не тронуты (другая позиция)
    assert offers["offer_a1"]["is_selected"] is True
    line_b = next(r for r in STORE["request_lines"] if r["row_id"] == "line_b")
    assert line_b["body"]["selected_offer_id"] == "offer_b1"
    assert line_b["body"]["status"] == "selected"
    # два прогона пайплайна: сначала чат строки (его бюджет), затем активный
    assert [c["session_id"] for c in calls] == ["chat_b", "chat_a"]
    assert all(c["project_id"] == "proj_1" for c in calls)


@pytest.mark.asyncio
async def test_select_row_unknown_row_404(monkeypatch) -> None:
    _patch_io(monkeypatch)
    executor = _executor()
    with pytest.raises(Exception) as excinfo:
        await executor._select_row(
            cabinet_id="cab_1",
            module_id="mod_equipment",
            params=_params(),
            row_id="nope",
            principal=_principal(),
            employee=None,
            project_id="proj_1",
            session_id="chat_a",
        )
    assert getattr(excinfo.value, "status", None) == 404
