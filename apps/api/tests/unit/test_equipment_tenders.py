"""WAVE14 «Тендеры»: агрегаты _sync_tenders + нормализация позиций MCP.

Проверяет:
- positions_count = max(план lines_json, факт связанных позиций);
- found_pct — доля matched/selected среди связанных позиций;
- сумма/маржа — по бюджетным снапшотам (manual markup → budget, иначе реестр);
- suppliers_count — уникальные seller бюджетных строк тендера;
- тендер без позиций/бюджета — нули, без падений;
- тендер без связанных позиций не затирает редактируемые поля;
- MCP _normalize_tender_lines: id, qty, валидация.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.mcp.prodavan_equipment_mcp.server import (
    _normalize_tender_lines,
)
from prodavan.application.modules.equipment_offers_service import (
    EquipmentPipelineService,
)

from test_equipment_offers_pipeline import FakeIO  # noqa: E402


def _tender(
    row_id: str,
    *,
    lines: list[dict[str, Any]] | None = None,
    title: str = "Тендер",
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "body": {
            "title": title,
            "customer": "Заказчик",
            "status": "active",
            "lines_json": lines or [],
        },
    }


def _line(row_id: str, tender_id: str, status: str) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "body": {
            "title": f"Позиция {row_id}",
            "qty": 2,
            "status": status,
            "tender_id": tender_id,
        },
    }


def _budget(line_id: str, *, qty: float, price: float, seller: str,
            markup: float | None = None, markup_source: str = "") -> dict[str, Any]:
    return {
        "row_id": f"b_{line_id}",
        "body": {
            "line_id": line_id,
            "qty": qty,
            "price_in": price,
            "seller": seller,
            "markup": markup,
            "markup_source": markup_source,
        },
    }


async def _sync(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    sellers = [
        {"row_id": "s_iv", "body": {"name": "Иванов", "is_enabled": True, "margin_pct": 20}},
    ]
    io._tables.setdefault("trusted_sellers", sellers)
    from prodavan.application.modules.equipment_offers_service import _SellerRegistry

    registry = _SellerRegistry(await io.list("trusted_sellers"))
    await svc._sync_tenders(io, registry=registry)


async def test_tender_aggregates_full() -> None:
    io = FakeIO(
        {
            "tenders": [
                _tender(
                    "t1",
                    lines=[
                        {"id": "tl1", "title": "A", "part_number": "", "qty": 2},
                        {"id": "tl2", "title": "B", "part_number": "", "qty": 1},
                    ],
                )
            ],
            "request_lines": [
                _line("l1", "t1", "selected"),
                _line("l2", "t1", "open"),
            ],
            "budget_lines": [
                # seller registry 20% → 2×100×0.2 = 40
                _budget("l1", qty=2, price=100.0, seller="Иванов"),
                # manual 50% → 1×200×0.5 = 100
                _budget("l2", qty=1, price=200.0, seller="Петров",
                        markup=0.5, markup_source="manual"),
            ],
        }
    )
    await _sync(io)

    body = io.body("tenders", "t1")
    assert body["positions_count"] == 2  # max(план 2, факт 2)
    assert body["linked_positions_count"] == 2
    assert body["found_pct"] == 50.0  # 1 из 2 matched/selected
    assert body["sum_rub"] == 400.0  # 2*100 + 1*200
    assert body["sum_margin_rub"] == 140.0  # 40 + 100
    assert body["margin_pct"] == 35.0  # 140/400
    assert body["suppliers_count"] == 2  # Иванов + Петров
    # редактируемые поля не тронуты
    assert body["title"] == "Тендер"
    assert body["customer"] == "Заказчик"


async def test_tender_planned_positions_before_import() -> None:
    """Тендер не взят в работу: позиций в чатах нет, но план из lines_json."""
    io = FakeIO(
        {
            "tenders": [
                _tender(
                    "t2",
                    lines=[{"id": f"tl{i}", "title": f"P{i}", "qty": 1} for i in range(5)],
                )
            ],
            "request_lines": [],
            "budget_lines": [],
        }
    )
    await _sync(io)

    body = io.body("tenders", "t2")
    assert body["positions_count"] == 5
    assert body["linked_positions_count"] == 0
    assert body["found_pct"] == 0.0
    assert body["sum_rub"] == 0.0
    assert body["suppliers_count"] == 0


async def test_tender_empty_no_crash_and_no_update() -> None:
    """Пустой тендер (0/0) — значения уже нулевые, update не нужен."""
    io = FakeIO(
        {
            "tenders": [
                {
                    "row_id": "t3",
                    "body": {
                        "title": "Пустой",
                        "positions_count": 0,
                        "linked_positions_count": 0,
                        "found_pct": 0.0,
                        "sum_rub": 0.0,
                        "margin_pct": 0.0,
                        "sum_margin_rub": 0.0,
                        "suppliers_count": 0,
                    },
                }
            ],
            "request_lines": [],
            "budget_lines": [],
        }
    )
    await _sync(io)
    # без изменений — записи не было (write-amplification guard)
    assert not any(t == "tenders" for t, _, _ in io.updated)


async def test_tender_foreign_lines_ignored() -> None:
    """Позиции другого тендера/без тендера в агрегаты не попадают."""
    io = FakeIO(
        {
            "tenders": [_tender("t4"), _tender("t5")],
            "request_lines": [
                _line("l1", "t4", "matched"),
                _line("l2", "t5", "selected"),
                {"row_id": "l3", "body": {"title": "Свободная", "status": "selected"}},
            ],
            "budget_lines": [
                _budget("l1", qty=1, price=50.0, seller="Иванов"),
                _budget("l2", qty=1, price=999.0, seller="Петров"),
                _budget("l3", qty=1, price=777.0, seller="Сидоров"),
            ],
        }
    )
    await _sync(io)

    t4 = io.body("tenders", "t4")
    assert t4["linked_positions_count"] == 1
    assert t4["found_pct"] == 100.0
    assert t4["sum_rub"] == 50.0
    assert t4["suppliers_count"] == 1

    t5 = io.body("tenders", "t5")
    assert t5["linked_positions_count"] == 1
    assert t5["sum_rub"] == 999.0
    assert t5["suppliers_count"] == 1


def test_normalize_tender_lines_ok() -> None:
    lines = _normalize_tender_lines(
        [
            {"title": "SSD 1TB", "qty": 2},
            {"id": "custom", "title": "RAM", "part_number": "ABC", "qty": 4},
        ]
    )
    assert lines[0] == {"id": "tl1", "title": "SSD 1TB", "part_number": "", "qty": 2.0}
    assert lines[1] == {"id": "custom", "title": "RAM", "part_number": "ABC", "qty": 4.0}


def test_normalize_tender_lines_validation() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="title"):
        _normalize_tender_lines([{"title": ""}])
    with pytest.raises(RuntimeError, match="duplicate"):
        _normalize_tender_lines([{"id": "a", "title": "x"}, {"id": "a", "title": "y"}])
    with pytest.raises(RuntimeError, match="qty"):
        _normalize_tender_lines([{"title": "x", "qty": 0}])
    with pytest.raises(RuntimeError, match="array"):
        _normalize_tender_lines({"not": "a list"})
