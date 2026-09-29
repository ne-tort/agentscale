"""Unit tests — equipment budget: fill workbook, sync, export actions."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from xml.etree import ElementTree as ET

import pytest

from prodavan.application.modules import equipment_budget as budget
from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "kp-template.xlsx"


def _row(**body):
    return dict(body)


# --------------------------------------------------------------- money caches


def test_money_cache_commerce_parity() -> None:
    cache = budget.money_cache(2, 100, 0.22, 0.1)
    assert cache["H"] == 200.0
    assert cache["K"] == 110.0
    assert cache["M"] == 220.0
    assert cache["J"] == round(110 / 1.22, 6)
    assert cache["L"] == round(220 / 1.22, 6)
    assert cache["N"] == 10.0
    assert cache["O"] == 20.0


def test_money_cache_missing_price_is_empty() -> None:
    assert budget.money_cache(1, None, 0.22, 0.1) == {}


# ------------------------------------------------------------ workbook fill


@pytest.mark.skipif(not TEMPLATE.is_file(), reason="template not shipped")
def test_fill_budget_workbook_on_real_template() -> None:
    rows = [
        _row(title="SSD Samsung", part_number="MZ-77Q", qty=2, price_in=100.0,
             vat=0.22, markup=0.1, seller="OCS", comment="В наличии (+)"),
        _row(title="RAM Kingston", part_number="KVR26", qty=1, price_in=50.0,
             vat=0.22, markup=0.1, seller="DNS", comment=""),
    ]
    out = budget.fill_budget_workbook(TEMPLATE.read_bytes(), rows, {})
    assert out and out != TEMPLATE.read_bytes()

    with zipfile.ZipFile(TEMPLATE, "r") as z:
        tpl_shared = z.read("xl/sharedStrings.xml")
        tpl_styles = z.read("xl/styles.xml")
        tpl_sheet2 = z.read("xl/worksheets/sheet2.xml")
    with zipfile.ZipFile(__import__("io").BytesIO(out), "r") as z:
        names = z.namelist()
        assert "xl/sharedStrings.xml" in names
        # styles/sharedStrings are byte-identical: nothing else was touched
        assert z.read("xl/sharedStrings.xml") == tpl_shared
        assert z.read("xl/styles.xml") == tpl_styles
        sheet1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        sheet2 = z.read("xl/worksheets/sheet2.xml")

    # budget values written directly
    assert "MZ-77Q" in sheet1 and "SSD Samsung" in sheet1 and "OCS" in sheet1
    # template formulas preserved (never rewritten)
    assert "PRODUCT(E7,F7)" in sheet1
    assert "PRODUCT(F7,1+I7)" in sheet1
    # cached totals next to the formulas
    assert "220" in sheet1  # M4 total (2 * 110)
    # КП sheet: trimmed to 2 items — the empty-slot formulas are gone
    assert "Бюджетирование!D7" in sheet2.decode("utf-8")
    assert "Бюджетирование!D8" in sheet2.decode("utf-8")
    assert "Бюджетирование!D9" not in sheet2.decode("utf-8")
    assert "Бюджетирование!L4" in sheet2.decode("utf-8")
    # merged item rows (B12:D12 style) survived the trim
    assert sheet2 != tpl_sheet2
    # workbook still opens cleanly for openpyxl
    from openpyxl import load_workbook

    wb = load_workbook(__import__("io").BytesIO(out))
    assert "Бюджетирование" in wb.sheetnames and "КП" in wb.sheetnames
    assert wb["Бюджетирование"].max_row and wb["Бюджетирование"].max_row > 900


@pytest.mark.skipif(not TEMPLATE.is_file(), reason="template not shipped")
def test_fill_budget_workbook_empty_rows_placeholders() -> None:
    out = budget.fill_budget_workbook(TEMPLATE.read_bytes(), [], {})
    sheet1 = zipfile.ZipFile(__import__("io").BytesIO(out)).read(
        "xl/worksheets/sheet1.xml"
    ).decode("utf-8")
    # no rows → no D7 value written (placeholder stays untouched)
    assert "Не найден" not in sheet1


# ------------------------------------------------------------- budget actions


def _executor() -> ModuleActionExecutor:
    return ModuleActionExecutor(session=MagicMock())  # type: ignore[arg-type]


async def _invoke(executor, monkeypatch, action, row_id=None):
    async def _load(*args, **kwargs):  # noqa: ANN003
        return action

    monkeypatch.setattr(executor, "_load_action", _load)
    return await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id=action["id"],
        row_id=row_id,
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )


@pytest.mark.asyncio
async def test_budget_sync_creates_and_updates(monkeypatch) -> None:
    executor = _executor()
    rows_by_table: dict[str, list[dict]] = {
        "request_lines": [
            {"row_id": "line_1", "body": {"title": "SSD 1TB", "qty": 2, "part_number": "MZ",
                                           "selected_offer_id": "offer_1"}},
        ],
        "found_offers": [
            {"row_id": "offer_1", "body": {"line_id": "line_1", "title": "SSD Samsung 980",
                                           "part_number": "MZ-77Q", "price": 100,
                                           "catalog_id": "cat_1", "is_selected": True}},
        ],
        "budget_lines": [
            {"row_id": "bud_1", "body": {"line_id": "line_1", "title": "OLD",
                                         "vat": 0.3, "markup": 0.15, "comment": "keep me"}},
        ],
        "catalogs": [
            {"row_id": "cat_1", "body": {"name": "OCS прайс"}},
        ],
    }

    async def _rows(**kwargs):  # noqa: ANN003
        return rows_by_table[kwargs["table_slug"]]

    created: list[dict] = []
    updated: list[dict] = []

    async def _create(**kwargs):  # noqa: ANN003
        created.append(kwargs)
        return {"row_id": "bud_new", "body": kwargs.get("body") or {}}

    async def _update(**kwargs):  # noqa: ANN003
        updated.append(kwargs)
        return {"row_id": kwargs.get("row_id")}

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    monkeypatch.setattr(executor, "_modules", SimpleNamespaceModule(_create))
    monkeypatch.setattr(executor, "_update_row_for_scope", _update)

    out = await _invoke(
        executor,
        monkeypatch,
        {
            "id": "budget_sync_lines",
            "kind": "equipment.budget_sync",
            "params": {
                "lines_table": "request_lines",
                "offers_table": "found_offers",
                "budget_table": "budget_lines",
            },
        },
    )
    assert out["kind"] == "equipment.budget_sync"
    # existing row updated with the offer snapshot, user fields untouched
    body = updated[0]["body"]
    assert body["title"] == "SSD Samsung 980"
    assert body["part_number"] == "MZ"
    assert body["qty"] == 2
    assert body["price_in"] == 100
    assert body["seller"] == "OCS прайс"
    assert body["vat"] == 0.3
    assert body["markup"] == 0.15
    assert body["comment"] == "keep me"
    assert out["updated"] == 1 and out["created"] == 0


class SimpleNamespaceModule:
    def __init__(self, create):
        self._create = create

    async def create_data_row(self, **kwargs):  # noqa: ANN003
        return await self._create(**kwargs)


@pytest.mark.asyncio
async def test_budget_sync_creates_row_with_defaults(monkeypatch) -> None:
    executor = _executor()
    rows_by_table = {
        "request_lines": [{"row_id": "line_2", "body": {"title": "RAM", "qty": 1}}],
        "found_offers": [],
        "budget_lines": [],
        "catalogs": [],
    }

    async def _rows(**kwargs):  # noqa: ANN003
        return rows_by_table[kwargs["table_slug"]]

    created: list[dict] = []

    async def _create(**kwargs):  # noqa: ANN003
        created.append(kwargs)
        return {"row_id": "bud_new", "body": kwargs.get("body") or {}}

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    monkeypatch.setattr(executor, "_modules", SimpleNamespaceModule(_create))
    monkeypatch.setattr(executor, "_update_row_for_scope", AsyncMock())

    out = await _invoke(
        executor,
        monkeypatch,
        {
            "id": "budget_sync_lines",
            "kind": "equipment.budget_sync",
            "params": {},
        },
    )
    assert out["created"] == 1
    body = created[0]["body"]
    assert body["title"] == "RAM"
    assert body["price_in"] == 0
    assert body["vat"] == 0.22 and body["markup"] == 0.1
    assert body["seller"] == "Не найден"
    assert body["part_number"] == "Не определен"


@pytest.mark.asyncio
async def test_budget_export_saves_document(monkeypatch) -> None:
    executor = _executor()

    async def _rows(**kwargs):  # noqa: ANN003
        if kwargs["table_slug"] == "budget_lines":
            return [
                {"row_id": "b1", "body": {"title": "SSD", "qty": 1, "price_in": 10,
                                          "vat": 0.22, "markup": 0.1, "seller": "OCS"}},
            ]
        return []

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    monkeypatch.setattr(
        executor, "_resolve_documents_company_id", AsyncMock(return_value="co1")
    )
    ref = {"asset_id": "a", "version_id": "v", "filename": "budget.xlsx"}

    async def _save(_self, data, **kwargs):  # noqa: ANN003
        assert kwargs["filename"] == "budget.xlsx"
        assert kwargs["company_id"] == "co1"
        return ref

    monkeypatch.setattr(
        "prodavan.application.documents.service.DocumentsService.save_document", _save
    )

    out = await _invoke(
        executor,
        monkeypatch,
        {
            "id": "budget_export",
            "kind": "equipment.budget_export",
            "params": {"budget_table": "budget_lines"},
        },
    )
    assert out["kind"] == "equipment.budget_export"
    assert out["file_ref"] == ref
    assert out["rows"] == 1


@pytest.mark.asyncio
async def test_budget_export_requires_rows(monkeypatch) -> None:
    executor = _executor()

    async def _rows(**kwargs):  # noqa: ANN003
        return []

    monkeypatch.setattr(executor, "_list_rows_for_scope", _rows)
    with pytest.raises(AppError) as err:
        await _invoke(
            executor,
            monkeypatch,
            {"id": "budget_export", "kind": "equipment.budget_export", "params": {}},
        )
    assert "no budget lines" in (err.value.detail or "")


@pytest.mark.asyncio
async def test_maybe_auto_budget_sync_fires_for_lines_table(monkeypatch) -> None:
    executor = _executor()
    called: list[str] = []

    async def _list_actions(*args, **kwargs):  # noqa: ANN003
        return [
            {
                "id": "budget_sync_lines",
                "kind": "equipment.budget_sync",
                "enabled": True,
                "params": {"lines_table": "request_lines", "offers_table": "found_offers"},
                "trigger": {"on": ["row.updated"]},
            }
        ]

    async def _sync(**kwargs):  # noqa: ANN003
        called.append(kwargs.get("params", {}).get("lines_table", "?"))

    monkeypatch.setattr(executor, "_list_actions", _list_actions)
    monkeypatch.setattr(executor, "_budget_sync", _sync)
    await executor.maybe_auto_budget_sync(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="request_lines",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )
    await executor.maybe_auto_budget_sync(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )
    assert called == ["request_lines"]
