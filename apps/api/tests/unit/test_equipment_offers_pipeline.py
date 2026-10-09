"""WAVE7 equipment pipeline unit tests (fake IO + fake OpenSearch).

Проверяет разделение ответственности:
- материализация found_offers из OS по ключам групп (партномер + алиасы + хэши);
- отключённые поставщики не материализуются;
- best-оффер группы: приоритетный поставщик побеждает цену, stale уступает;
- best-группа позиции: точность → цена; лицо группы = выбранный ?? best;
- ручные переопределения (manual) не перезаписываются синхронизацией;
- бюджет: выбранный ?? best; маржа поставщика с markup_source;
- «Закупка»: агрегаты по поставщикам, доставка, disabled исключены;
- apply_procurement_row: маржа → реестр + бюджетные строки;
- легаси-офферы без group_id удаляются.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import prodavan.application.modules.equipment_fx as fx_mod
import prodavan.core.infra.opensearch_manager as os_manager_mod
from prodavan.application.modules.equipment_offers_service import (
    EquipmentPipelineService,
    ModuleRowIO,
)


class FakeIO(ModuleRowIO):
    """In-memory row store; same call surface as the real IO."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:  # noqa: D107
        self._tables: dict[str, list[dict[str, Any]]] = {
            table: list(rows) for table, rows in tables.items()
        }
        self._seq = 0
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.updated: list[tuple[str, str, dict[str, Any]]] = []
        self.deleted: list[tuple[str, str]] = []

    async def list(self, table_slug: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._tables.get(table_slug, [])]

    async def list_project_wide(self, table_slug: str) -> list[dict[str, Any]]:
        # Single-bucket fake: project-wide listing equals the plain list.
        return await self.list(table_slug)

    async def create(self, table_slug: str, body: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        row = {"row_id": f"row_{self._seq}", "body": dict(body)}
        self._tables.setdefault(table_slug, []).append(row)
        self.created.append((table_slug, dict(body)))
        return dict(row)

    async def update(
        self, table_slug: str, row_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        for row in self._tables.get(table_slug, []):
            if row["row_id"] == row_id:
                row["body"] = dict(body)
                self.updated.append((table_slug, row_id, dict(body)))
                return dict(row)
        raise AssertionError(f"update of missing row {table_slug}/{row_id}")

    async def delete(self, table_slug: str, row_id: str) -> bool:
        rows = self._tables.get(table_slug, [])
        before = len(rows)
        self._tables[table_slug] = [r for r in rows if r["row_id"] != row_id]
        if len(self._tables[table_slug]) != before:
            self.deleted.append((table_slug, row_id))
            return True
        return False

    def rows(self, table_slug: str) -> list[dict[str, Any]]:
        return self._tables.get(table_slug, [])

    def by_id(self, table_slug: str, row_id: str) -> dict[str, Any]:
        return next(r for r in self._tables.get(table_slug, []) if r["row_id"] == row_id)

    def body(self, table_slug: str, row_id: str) -> dict[str, Any]:
        return dict(self.by_id(table_slug, row_id)["body"])


def _doc(
    src_hash: str,
    *,
    part_number: str = "",
    title: str = "",
    supplier: str = "",
    price_num: float = 0.0,
    currency: str = "RUB",
    in_stock: bool = True,
) -> dict[str, Any]:
    return {
        "src_hash": src_hash,
        "part_number": part_number,
        "title": title,
        "brand": "Kingston",
        "price": str(price_num),
        "price_num": price_num,
        "supplier": supplier,
        "currency": currency,
        "in_stock": in_stock,
        "lead_time": "",
        "catalog_id": "cat_1",
        "source_catalog": "Test DB",
    }


DOCS = [
    _doc("h1", part_number="ABC-123", title="Товар A Иванов", supplier="Иванов", price_num=100.0),
    _doc("h2", part_number="ABC123", title="Товар A Петров", supplier="Петров", price_num=90.0),
    # приоритетный поставщик дороже — но best внутри группы
    _doc("h3", part_number="ABC-123", title="Товар A Сидоров", supplier="Сидоров", price_num=120.0),
    # позиция без партномера (хэш-группа), USD
    _doc("h4", part_number="", title="Кабель", supplier="Иванов", price_num=5.0, currency="USD"),
    # отключённый поставщик — не материализуется
    _doc("h5", part_number="ABC-123", title="Товар A Луков", supplier="Луков", price_num=10.0),
    # исчезнувшая из каталога позиция (h6 есть в офферах, нет в DOCS)
]

TABLES = {
    "catalogs": [
        {"row_id": "cat_1", "body": {"name": "Test DB", "status": "ready", "paused": False}}
    ],
    "request_lines": [
        {"row_id": "line_1", "body": {"title": "SSD 1TB", "part_number": "ABC-123", "qty": 2, "status": "open"}},
        {"row_id": "line_2", "body": {"title": "Кабель", "qty": 1, "status": "open"}},
    ],
    "found_groups": [
        {
            "row_id": "grp_1",
            "body": {
                "line_id": "line_1",
                "part_number": "ABC-123",
                "aliases_pn": "ABC123",
                "aliases_hash": "",
                "match_kind": "exact",
                "note": "",
            },
        },
        {
            "row_id": "grp_2",
            "body": {
                "line_id": "line_1",
                "part_number": "",
                "aliases_pn": "",
                "aliases_hash": "hX-missing",
                "match_kind": "analog",
                "note": "похож, но не тот",
            },
        },
        {
            "row_id": "grp_3",
            "body": {
                "line_id": "line_2",
                "part_number": "",
                "aliases_pn": "",
                "aliases_hash": "h4",
                "match_kind": "doubt",
                "note": "без ПН",
            },
        },
    ],
    "found_offers": [
        # легаси-оффер без group_id — должен быть удалён
        {"row_id": "offer_legacy", "body": {"title": "старый", "line_id": "line_1", "src_hash": "hX"}},
        # оффер с ручной правкой цены — цена не перезаписывается
        {
            "row_id": "offer_manual",
            "body": {
                "group_id": "grp_1",
                "line_id": "line_1",
                "title": "Товар A Иванов",
                "seller": "Иванов",
                "part_number": "ABC-123",
                "src_hash": "h1",
                "currency": "RUB",
                "price_orig": 100.0,
                "price": 100.0,
                "match_kind": "exact",
                "manual": {"price": True},
                "is_selected": False,
                "is_stale": False,
            },
        },
    ],
    "trusted_sellers": [
        {"row_id": "seller_ivanov", "body": {"name": "Иванов", "is_enabled": True, "margin_pct": 15, "delivery_rub": 300}},
        {"row_id": "seller_petrov", "body": {"name": "Петров", "is_enabled": True}},
        {"row_id": "seller_sidorov", "body": {"name": "Сидоров", "is_enabled": True, "priority_purchase": True}},
        {"row_id": "seller_lukov", "body": {"name": "Луков", "is_enabled": False}},
    ],
    "budget_lines": [],
    "procurement": [],
}


def _patch_os(monkeypatch, docs: list[dict[str, Any]]) -> None:
    class _FakeResult:
        def __init__(self, hits: list) -> None:
            self.hits = hits

    class _FakeSvc:
        async def search(self, **kwargs: Any) -> _FakeResult:
            return _FakeResult([SimpleNamespace(source=d) for d in docs])

    monkeypatch.setattr(os_manager_mod, "get_search_index_service", lambda: _FakeSvc())


async def _fake_convert(*, price: float, currency: str) -> tuple[float, str]:
    if currency == "RUB":
        return float(price), "RUB"
    return round(float(price) * 90.0, 2), currency


@pytest.fixture()
def io(monkeypatch) -> FakeIO:
    _patch_os(monkeypatch, DOCS)
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)
    return FakeIO(TABLES)


async def test_pipeline_materialize_best_budget_procurement(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    stats = await svc.run(io, materialize=True)

    offers = io.rows("found_offers")
    by_hash = {o["body"]["src_hash"]: o for o in offers if o["body"].get("src_hash")}

    # материализация: h1(уже был), h2, h3, h4 созданы; h5 (Луков) — нет
    assert set(by_hash) == {"h1", "h2", "h3", "h4"}
    assert stats["offers_created"] == 3
    # легаси-оффер удалён
    assert ("found_offers", "offer_legacy") in io.deleted

    # h4: USD 5 → RUB 450 (конвертация фейком), валюта исходная сохранена
    h4 = by_hash["h4"]["body"]
    assert h4["group_id"] == "grp_3"
    assert h4["line_id"] == "line_2"
    assert h4["match_kind"] == "doubt"
    assert h4["price_orig"] == 5.0
    assert h4["currency"] == "USD"
    assert h4["price"] == 450.0
    assert h4["source_title"] == "Кабель"

    # h1: manual price=True → цена не тронута (doc тот же, изменений нет)
    assert by_hash["h1"]["body"]["price"] == 100.0

    # --- best в группе grp_1: приоритетный Сидоров (120) побеждает Петрова (90)
    assert by_hash["h3"]["body"]["is_best"] is True
    assert by_hash["h2"]["body"]["is_best"] is False
    assert by_hash["h1"]["body"]["is_best"] is False
    # зелёный флаг приоритетности
    assert by_hash["h3"]["body"]["priority"] is True
    assert by_hash["h2"]["body"]["priority"] is False

    # --- best-группы позиций
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["is_best"] is True  # exact на line_1
    assert grp1["face_title"] == "Товар A Сидоров"
    assert grp1["face_price"] == 120.0
    assert grp1["face_seller"] == "Сидоров"
    assert grp1["offers_count"] == 3
    assert grp1["rank"] == 0
    grp2 = io.body("found_groups", "grp_2")  # analog, офферов нет
    assert grp2.get("is_best") is not True
    assert grp2["offers_count"] == 0
    grp3 = io.body("found_groups", "grp_3")
    assert grp3["is_best"] is True  # единственная группа line_2
    assert grp3["face_price"] == 450.0

    # found_count / статус позиций
    assert io.body("request_lines", "line_1")["found_count"] == 1
    assert io.body("request_lines", "line_1")["status"] == "matched"
    assert io.body("request_lines", "line_2")["found_count"] == 1

    # --- бюджет: line_1 ← best-группа grp_1 → best-оффер Сидоров (120)
    budget = io.rows("budget_lines")
    assert len(budget) == 2
    b1 = next(b for b in budget if b["body"]["line_id"] == "line_1")["body"]
    assert b1["seller"] == "Сидоров"
    assert b1["price_in"] == 120.0
    assert b1["qty"] == 2
    # у Сидорова нет margin_pct → дефолт 10%
    assert b1["markup"] == 0.1
    assert b1["markup_source"] == "default"
    b2 = next(b for b in budget if b["body"]["line_id"] == "line_2")["body"]
    assert b2["seller"] == "Иванов"
    assert b2["price_in"] == 450.0
    # маржа Иванова 15% → markup 0.15
    assert b2["markup"] == 0.15
    assert b2["markup_source"] == "seller"

    # --- закупка: Петров (без офферов? есть h2) / Иванов / Сидоров; Луков выключен
    proc = {p["body"]["seller"]: p["body"] for p in io.rows("procurement")}
    assert set(proc) == {"Иванов", "Петров", "Сидоров"}
    # Иванов: офферов 2; эффективная позиция line_2 (Кабель, best h4=450).
    # Доставка (300) — всегда расход: маржа = 450×0.15 − 300.
    assert proc["Иванов"]["offers_count"] == 2
    assert proc["Иванов"]["margin_pct"] == 15
    assert proc["Иванов"]["delivery_rub"] == 300
    assert proc["Иванов"]["sum_rub"] == 450.0
    assert proc["Иванов"]["selected_count"] == 1
    assert proc["Иванов"]["qty_total"] == 1
    assert proc["Иванов"]["sum_margin_rub"] == round(450.0 * 0.15 - 300, 2)
    assert proc["Иванов"]["sum_with_margin_rub"] == round(450.0 * 1.15 + 300, 2)
    # Сидоров: эффективная позиция line_1 (best h3=120, qty 2 → 240),
    # markup default 0.1 (нет в реестре) → маржа 24, доставки нет
    assert proc["Сидоров"]["offers_count"] == 1
    assert proc["Сидоров"]["sum_rub"] == 240.0
    assert proc["Сидоров"]["selected_count"] == 1
    assert proc["Сидоров"]["qty_total"] == 2
    assert proc["Сидоров"]["sum_margin_rub"] == 24.0
    # Петров: оффер есть, но не эффективен ни для одной позиции — строка
    # с нулевыми суммами (иначе на Закупках нельзя выбрать его товар)
    assert proc["Петров"]["offers_count"] == 1
    assert proc["Петров"]["sum_rub"] == 0.0
    assert proc["Петров"]["selected_count"] == 0
    assert proc["Петров"]["qty_total"] == 0
    # дефолт маржи 10 у Петрова и Сидорова (нет в реестре)
    assert proc["Петров"]["margin_pct"] == 10
    assert proc["Сидоров"]["margin_pct"] == 10


async def test_pipeline_respects_manual_selection_and_stale(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    # пользователь выбрал Петрова (h2) для line_1
    h2_row_id = next(
        o["row_id"] for o in io.rows("found_offers") if o["body"]["src_hash"] == "h2"
    )
    await io.update(
        "found_offers",
        h2_row_id,
        {**io.body("found_offers", h2_row_id), "is_selected": True},
    )
    # h3 исчез из каталога
    _docs2 = [d for d in DOCS if d["src_hash"] != "h3"]

    class _FakeResult:
        def __init__(self, hits: list) -> None:
            self.hits = hits

    class _FakeSvc:
        async def search(self, **kwargs: Any) -> _FakeResult:
            return _FakeResult([SimpleNamespace(source=d) for d in _docs2])

    import prodavan.core.infra.opensearch_manager as osm

    orig = osm.get_search_index_service
    osm.get_search_index_service = lambda: _FakeSvc()
    try:
        stats = await svc.run(io, materialize=True)
    finally:
        osm.get_search_index_service = orig

    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    # h3 исчез → is_stale, цена не тронута
    assert by_hash["h3"]["body"]["is_stale"] is True
    assert by_hash["h3"]["body"]["price"] == 120.0
    assert stats["offers_stale"] == 1

    # лицо группы = выбранный (Петров 90), не best (Сидоров пропал)
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["face_seller"] == "Петров"
    assert grp1["face_price"] == 90.0

    # бюджет следует выбору пользователя
    b1 = next(b for b in io.rows("budget_lines") if b["body"]["line_id"] == "line_1")["body"]
    assert b1["seller"] == "Петров"
    assert b1["price_in"] == 90.0

    # закупка: выбранный товар Петрова × qty 2 × цена 90; Петров без маржи → 10%
    proc = {p["body"]["seller"]: p["body"] for p in io.rows("procurement")}
    assert proc["Петров"]["selected_count"] == 1
    assert proc["Петров"]["sum_rub"] == 180.0
    assert proc["Петров"]["sum_margin_rub"] == pytest.approx(18.0)
    assert proc["Петров"]["sum_with_margin_rub"] == pytest.approx(198.0)


async def test_procurement_apply_updates_registry_and_budget(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    proc_row = next(p for p in io.rows("procurement") if p["body"]["seller"] == "Иванов")
    # пользователь поменял маржу Иванова на 25 и включил доставку
    await io.update(
        "procurement",
        proc_row["row_id"],
        {**proc_row["body"], "margin_pct": 25, "include_delivery": True},
    )
    result = await svc.apply_procurement_row(io, row_id=proc_row["row_id"])

    assert result["kind"] == "equipment.procurement_apply"
    # реестр обновлён
    assert io.body("trusted_sellers", "seller_ivanov")["margin_pct"] == 25
    # бюджетные строки Иванова (line_2, markup_source=seller) — обновлены
    b2 = next(b for b in io.rows("budget_lines") if b["body"]["line_id"] == "line_2")["body"]
    assert b2["markup"] == 0.25
    assert b2["markup_source"] == "seller"

    # Агрегаты пересчитаны ТЕМ ЖЕ проходом, что и sync: эффективные позиции
    # (бюджетные снапшоты), а не только явные чекбоксы — суммы не обнуляются.
    # Иванов: line_2 (Кабель, 450) × маржа 25% = 112.5; доставка 300 включена.
    proc = next(p for p in io.rows("procurement") if p["body"]["seller"] == "Иванов")["body"]
    assert proc["include_delivery"] is True
    assert proc["delivery_rub"] == 300
    assert proc["margin_pct"] == 25
    assert proc["sum_rub"] == 450.0
    assert proc["selected_count"] == 1
    assert proc["sum_margin_rub"] == pytest.approx(-187.5)
    assert proc["sum_with_margin_rub"] == pytest.approx(862.5)


async def test_budget_manual_markup_survives(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    b2_row = next(b for b in io.rows("budget_lines") if b["body"]["line_id"] == "line_2")
    body = dict(b2_row["body"])
    body["markup"] = 0.5
    body["markup_source"] = "manual"
    await io.update("budget_lines", b2_row["row_id"], body)

    # у Иванова поменялась маржа в реестре
    await io.update(
        "trusted_sellers",
        "seller_ivanov",
        {**io.body("trusted_sellers", "seller_ivanov"), "margin_pct": 20},
    )
    await svc.run(io, materialize=False)

    b2 = next(b for b in io.rows("budget_lines") if b["body"]["line_id"] == "line_2")["body"]
    assert b2["markup"] == 0.5  # ручная маржа не перезаписана
    assert b2["markup_source"] == "manual"


async def test_offer_manual_field_not_overwritten(io: FakeIO) -> None:
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    # каталог обновил цену h1: 100 → 150
    docs2 = [dict(d) for d in DOCS]
    for d in docs2:
        if d["src_hash"] == "h1":
            d["price_num"] = 150.0
            d["price"] = "150"
    _patch_os_monkey(docs2)

    await svc.run(io, materialize=True)

    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    # manual price → цена не изменилась, но price_orig обновился? нет — тоже manual
    assert by_hash["h1"]["body"]["price"] == 100.0
    assert by_hash["h1"]["body"]["price_orig"] == 100.0


def _patch_os_monkey(docs: list[dict[str, Any]]) -> None:
    import prodavan.core.infra.opensearch_manager as osm

    class _FakeResult:
        def __init__(self, hits: list) -> None:
            self.hits = hits

    class _FakeSvc:
        async def search(self, **kwargs: Any) -> _FakeResult:
            return _FakeResult([SimpleNamespace(source=d) for d in docs])

    osm.get_search_index_service = lambda: _FakeSvc()


async def test_disabled_seller_offers_are_frozen_not_stale(io: FakeIO) -> None:
    """Отключённый поставщик ≠ «позиция исчезла из каталога»: его офферы
    замирают без warning-флага is_stale (и без обновления цен)."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    assert by_hash["h3"]["body"]["is_stale"] is False

    # отключаем Сидорова; док h3 на месте
    await io.update(
        "trusted_sellers",
        "seller_sidorov",
        {**io.body("trusted_sellers", "seller_sidorov"), "is_enabled": False},
    )
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    assert by_hash["h3"]["body"]["is_stale"] is False
    # автовыбор уходит от отключённого поставщика: best/лицо — Петров (90)
    assert by_hash["h3"]["body"]["is_best"] is False
    assert by_hash["h2"]["body"]["is_best"] is True
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["face_seller"] == "Петров"

    # док h3 пропал из выдачи, но поставщик всё ещё отключён — warning'а нет
    docs2 = [d for d in DOCS if d["src_hash"] != "h3"]
    _patch_os_monkey(docs2)
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    assert by_hash["h3"]["body"]["is_stale"] is False

    # включили обратно, а дока нет — вот теперь честный stale
    await io.update(
        "trusted_sellers",
        "seller_sidorov",
        {**io.body("trusted_sellers", "seller_sidorov"), "is_enabled": True},
    )
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    assert by_hash["h3"]["body"]["is_stale"] is True
    assert by_hash["h3"]["body"]["price"] == 120.0  # цена не тронута


async def test_unsupported_currency_doc_skipped(io: FakeIO) -> None:
    """Документ в валюте вне RUB/USD/EUR не должен ронять пайплайн (enum
    колонки currency) — такой оффер пропускается с warning в логе."""
    docs2 = DOCS + [
        _doc("h9", part_number="ABC-123", title="Товар A Юанев", supplier="Юанев",
             price_num=10.0, currency="CNY"),
    ]
    _patch_os_monkey(docs2)
    svc = EquipmentPipelineService(session=object())
    stats = await svc.run(io, materialize=True)
    assert stats["offers_created"] == 3  # h2, h3, h4 — как раньше
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    assert "h9" not in by_hash
    # и поставщик не авто-регистрируется (оффера нет)
    assert all(s["body"]["name"] != "Юанев" for s in io.rows("trusted_sellers"))


async def test_orphan_groups_and_offers_cleaned(io: FakeIO) -> None:
    """Группа с несуществующей позицией (позицию удалили) чистится вместе
    с офферами — иначе мусор висит в «Найденных товарах» вечно."""
    io._tables["found_groups"].append(
        {
            "row_id": "grp_gone",
            "body": {
                "line_id": "line_deleted",
                "part_number": "ZZ-1",
                "match_kind": "exact",
            },
        }
    )
    io._tables["found_offers"].append(
        {
            "row_id": "offer_orphan",
            "body": {"group_id": "grp_gone", "line_id": "line_deleted",
                     "title": "сирота", "src_hash": "hGone"},
        }
    )
    svc = EquipmentPipelineService(session=object())
    stats = await svc.run(io, materialize=False)
    assert stats["groups_deleted"] == 1
    assert ("found_groups", "grp_gone") in io.deleted
    assert ("found_offers", "offer_orphan") in io.deleted


async def test_builds_follow_offer_prices(io: FakeIO) -> None:
    """«Сборка» связана с офферами: components_count/price_total пересчитываются
    пайплайном при обновлении цен (а не только в момент выбора в UI)."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    h2 = next(o for o in io.rows("found_offers") if o["body"]["src_hash"] == "h2")
    item = await io.create(
        "equipment_items", {"name": "Память", "offer_id": h2["row_id"], "qty": 2}
    )
    await io.create(
        "equipment_builds", {"name": "ПК", "slots": {"etype_ram": item["row_id"]}}
    )
    await svc.run(io, materialize=False)
    build = io.rows("equipment_builds")[0]["body"]
    assert build["components_count"] == 1
    assert build["price_total"] == 180.0  # 90 × 2

    # цена h2 выросла 90 → 95 — сборка следует за сверкой с каталогом
    docs2 = [dict(d) for d in DOCS]
    for d in docs2:
        if d["src_hash"] == "h2":
            d["price_num"] = 95.0
            d["price"] = "95"
    _patch_os_monkey(docs2)
    await svc.run(io, materialize=True)
    build = io.rows("equipment_builds")[0]["body"]
    assert build["price_total"] == 190.0


async def test_line_status_rolls_back(io: FakeIO) -> None:
    """Статус позиции вычислим целиком: протухший selected_offer_id очищается,
    matched без находок откатывается в open."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    assert io.body("request_lines", "line_1")["status"] == "matched"

    # битая ссылка на выбранный оффер → matched + очистка ссылки
    await io.update(
        "request_lines",
        "line_1",
        {**io.body("request_lines", "line_1"), "selected_offer_id": "ghost", "status": "selected"},
    )
    await svc.run(io, materialize=False)
    body = io.body("request_lines", "line_1")
    assert body["status"] == "matched"
    assert body["selected_offer_id"] is None

    # все группы позиции удалены → open
    await io.delete("found_groups", "grp_1")
    await io.delete("found_groups", "grp_2")
    await svc.run(io, materialize=False)
    body = io.body("request_lines", "line_1")
    assert body["status"] == "open"
    assert body["found_count"] == 0


async def test_noop_run_writes_nothing(io: FakeIO) -> None:
    """Повторный прогон без изменений — НОЛЬ записей (иначе каждый on_load
    страницы переписывал все группы → ложный «Проект требует обновления»)."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    io.created.clear()
    io.updated.clear()
    io.deleted.clear()

    stats = await svc.run(io, materialize=True)
    assert io.created == []
    assert io.updated == []
    assert io.deleted == []
    assert stats["offers_created"] == 0
    assert stats["budget_updated"] == 0
    assert stats["procurement_updated"] == 0


async def test_priority_seller_wins_only_within_same_match_tier(io: FakeIO) -> None:
    """Приоритетный поставщик бьёт цену ТОЛЬКО внутри одной точности:
    exact+приоритет > exact+дешевле; analog+приоритет НЕ вытесняет exact."""
    docs2 = DOCS + [
        # XYZ-1: только Петров 95 (не приоритет) — дешевле Сидорова из grp_1
        _doc("h7", part_number="XYZ-1", title="Деталь X Петров", supplier="Петров", price_num=95.0),
        # AN-1: Сидоров 50 (приоритет, дёшево) — но группа analog
        _doc("h9", part_number="AN-1", title="Аналог Сидоров", supplier="Сидоров", price_num=50.0),
    ]
    _patch_os_monkey(docs2)
    await io.create(
        "found_groups",
        {"line_id": "line_1", "part_number": "XYZ-1", "match_kind": "exact"},
    )
    await io.create(
        "found_groups",
        {"line_id": "line_1", "part_number": "AN-1", "match_kind": "analog"},
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    grp_x = next(g for g in io.rows("found_groups") if g["body"]["part_number"] == "XYZ-1")
    grp_an = next(g for g in io.rows("found_groups") if g["body"]["part_number"] == "AN-1")
    grp1 = io.body("found_groups", "grp_1")
    # grp_1: лицо — приоритетный Сидоров (120); grp_x — Петров (95).
    # Одинаковая точность (exact) → приоритет бьёт цену: лучшая = grp_1.
    assert grp1["face_priority"] is True
    assert grp1["is_best"] is True
    # face_priority/is_best пишутся при смене; отсутствие ключа == False (дефолт)
    assert grp_x["body"].get("face_priority") is not True
    assert grp_x["body"].get("is_best") is not True
    # analog с приоритетным и дешёвым оффером exact-группы не вытесняет
    assert grp_an["body"].get("is_best") is not True

    # «Альтернативы»: у line_1 три группы с офферами (grp_1, grp_x, grp_an) → 2;
    # grp_2 без офферов → 0; у line_2 одна группа → 0.
    assert grp1["alternatives_count"] == 2
    assert grp_x["body"]["alternatives_count"] == 2
    # пишется при смене; отсутствие ключа == 0 (дефолт колонки)
    assert (io.body("found_groups", "grp_2").get("alternatives_count") or 0) == 0
    assert (io.body("found_groups", "grp_3").get("alternatives_count") or 0) == 0


async def test_cascade_delete_line_removes_related_rows(io: FakeIO) -> None:
    """Удаление позиции заказчика сносит её группы, офферы и строку бюджета;
    удаление строки бюджета сносит саму позицию со всей цепочкой."""
    from prodavan.application.modules.equipment_offers_service import (
        cascade_equipment_delete,
    )

    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    assert any(b["body"]["line_id"] == "line_1" for b in io.rows("budget_lines"))

    # сервисы удаляют целевую строку сами, каскад — связанные
    await io.delete("request_lines", "line_1")
    deleted = await cascade_equipment_delete(
        object(),  # type: ignore[arg-type] — FakeIO не трогает session
        cabinet_id="cab_1",
        project_id=None,
        table_slug="request_lines",
        row_id="line_1",
        row_body={"title": "SSD 1TB"},
        principal=None,  # type: ignore[arg-type]
        employee=None,
        session_id=None,
        io=io,
    )
    assert deleted["found_groups"] == 2  # grp_1 + grp_2
    assert deleted["found_offers"] >= 3  # h1..h3 по line_id
    assert deleted["budget_lines"] == 1
    assert not io.rows("found_groups") or all(
        g["body"]["line_id"] != "line_1" for g in io.rows("found_groups")
    )
    assert all(
        (o["body"].get("line_id") or "") != "line_1" for o in io.rows("found_offers")
    )
    assert all(b["body"]["line_id"] != "line_1" for b in io.rows("budget_lines"))
    # line_2 цел
    assert io.rows("request_lines")[0]["row_id"] == "line_2"

    # удаление строки бюджета line_2 → сносит позицию и её цепочку
    b2 = io.rows("budget_lines")[0]
    await io.delete("budget_lines", b2["row_id"])
    deleted2 = await cascade_equipment_delete(
        object(),  # type: ignore[arg-type]
        cabinet_id="cab_1",
        project_id=None,
        table_slug="budget_lines",
        row_id=b2["row_id"],
        row_body=dict(b2["body"]),
        principal=None,  # type: ignore[arg-type]
        employee=None,
        session_id=None,
        io=io,
    )
    assert deleted2.get("request_lines") == 1
    assert deleted2.get("found_groups") == 1  # grp_3
    assert io.rows("request_lines") == []
    assert io.rows("found_groups") == []
    assert all(
        (o["body"].get("line_id") or "") != "line_2" for o in io.rows("found_offers")
    )


async def test_match_label_and_stock_and_offer_annotations(io: FakeIO) -> None:
    """match_label с «(под заказ)» при отсутствии наличия; per-offer
    аннотации: alternatives_count (другие поставщики позиции) + benefit_label."""
    docs2 = [dict(d) for d in DOCS]
    for d in docs2:
        if d["src_hash"] == "h2":
            d["in_stock"] = False  # Петров — под заказ
    _patch_os_monkey(docs2)
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers") if o["body"].get("src_hash")}
    # grp_1 exact; лицо — Сидоров (приоритетный, в наличии) → «Точное»
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["match_label"] == "Точное"
    assert grp1["face_in_stock"] is True
    # best оффера grp_1 — Сидоров (приоритет); Петров под заказ уступает не по
    # best, но его match_label несёт суффикс
    assert by_hash["h2"]["body"]["match_label"] == "Точное (под заказ)"
    assert by_hash["h3"]["body"]["match_label"] == "Точное"
    # alternatives: у офферов line_1 три поставщика → у каждого 2 альтернативы
    assert by_hash["h1"]["body"]["alternatives_count"] == 2
    # benefit: эффективный — best grp_1 = h3 (Сидоров 120), минимум линии —
    # h2 (90). Эффективный дороже минимума → переплата «−33.3%» (красный);
    # h2 дешевле эффективного → «+25.0%» (зелёный). «Выбран» больше не бейдж —
    # выбор виден зелёной строкой (is_effective).
    assert by_hash["h3"]["body"]["benefit_label"] == "−33.3%"
    assert by_hash["h3"]["body"]["benefit_tone"] == "worse"
    assert by_hash["h3"]["body"]["is_effective"] is True
    assert by_hash["h2"]["body"]["benefit_label"] == "+25.0%"
    assert by_hash["h2"]["body"]["benefit_tone"] == "better"
    assert by_hash["h2"]["body"].get("is_effective") is not True
    # line_2: единственный оффер (h4) → «Единственный»
    assert by_hash["h4"]["body"]["benefit_label"] == "Единственный"


async def test_best_offer_stock_beats_price(io: FakeIO) -> None:
    """ТЗ 2026-10-07: наличие — ПЕРВЫЙ критерий внутри группы (подзаказные
    уступают наличию даже будучи дешевле): наличие → цена → приоритет."""
    # h1 убран (у него ручная цена), h3 убран (приоритет) — чистый тест на
    # Петрове (90, в наличии) и Смирнове (80, под заказ)
    docs2 = [d for d in DOCS if d["src_hash"] not in ("h1", "h3", "h4")]
    docs2.append(
        _doc("h8", part_number="ABC123", title="Товар A Смирнов", supplier="Смирнов",
             price_num=80.0, in_stock=False)
    )
    _patch_os_monkey(docs2)
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers") if o["body"].get("src_hash")}
    # в наличии дороже ВЫИГРЫВАЕТ у дешевле+под заказ
    assert by_hash["h2"]["body"]["is_best"] is True
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["face_price"] == 90.0
    assert grp1["face_in_stock"] is True
    assert grp1["match_label"] == "Точное"

    # при равной цене — в наличии выигрывает
    docs3 = [dict(d) for d in docs2]
    for d in docs3:
        if d["src_hash"] == "h8":
            d["in_stock"] = False
        if d["src_hash"] == "h2":
            d["price_num"] = 80.0
            d["price"] = "80"
    _patch_os_monkey(docs3)
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers") if o["body"].get("src_hash")}
    assert by_hash["h2"]["body"]["is_best"] is True
    assert io.body("found_groups", "grp_1")["match_label"] == "Точное"


async def test_fx_rate_drift_reprices_offers(io: FakeIO, monkeypatch) -> None:
    """Курс ЦБ изменился, price_orig нет → ₽-цена переоценивается (оффер,
    лицо группы, бюджет). Ручные цены и stale-офферы заморожены."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    by_hash = {o["body"]["src_hash"]: o for o in io.rows("found_offers")}
    h4_id = by_hash["h4"]["row_id"]
    assert by_hash["h4"]["body"]["price"] == 450.0  # 5 USD × 90 (фейк-курс)

    async def _convert_100(*, price: float, currency: str) -> tuple[float, str]:
        if currency == "RUB":
            return float(price), "RUB"
        return round(float(price) * 100.0, 2), currency

    monkeypatch.setattr(fx_mod, "convert_offer_price", _convert_100)
    stats = await svc.run(io, materialize=True)  # те же доки, новый курс
    assert stats["offers_fx_repriced"] == 1  # только h4 валютный
    h4 = next(o for o in io.rows("found_offers") if o["body"]["src_hash"] == "h4")["body"]
    assert h4["price"] == 500.0
    assert h4["price_orig"] == 5.0  # исходная цена каталога не менялась
    assert io.body("found_groups", "grp_3")["face_price"] == 500.0
    b2 = next(b for b in io.rows("budget_lines") if b["body"]["line_id"] == "line_2")["body"]
    assert b2["price_in"] == 500.0

    # ручная цена заморожена даже при дрейфе курса
    await io.update(
        "found_offers", h4_id, {**io.body("found_offers", h4_id), "manual": {"price": True}}
    )

    async def _convert_110(*, price: float, currency: str) -> tuple[float, str]:
        if currency == "RUB":
            return float(price), "RUB"
        return round(float(price) * 110.0, 2), currency

    monkeypatch.setattr(fx_mod, "convert_offer_price", _convert_110)
    stats = await svc.run(io, materialize=True)
    assert stats["offers_fx_repriced"] == 0
    assert io.body("found_offers", h4_id)["price"] == 500.0

    # stale (позиция пропала из каталога) — цена тоже заморожена
    await io.update(
        "found_offers",
        h4_id,
        {**io.body("found_offers", h4_id), "manual": {}, "is_stale": True},
    )
    stats = await svc.run(io, materialize=False)
    assert stats["offers_fx_repriced"] == 0
    assert io.body("found_offers", h4_id)["price"] == 500.0


async def test_group_face_stale_flag(io: FakeIO) -> None:
    """Все офферы группы исчезли из каталога → лицо устарело (warning в UI)."""
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    # флаг пишется при смене; отсутствие ключа == False (дефолт колонки)
    assert io.body("found_groups", "grp_1").get("face_stale") is not True

    docs2 = [d for d in DOCS if d["src_hash"] not in {"h1", "h2", "h3"}]
    _patch_os_monkey(docs2)
    await svc.run(io, materialize=True)
    grp1 = io.body("found_groups", "grp_1")
    assert grp1["face_stale"] is True
    # is_best не снимается — группа лучшая, хоть и устаревшая
    assert grp1["is_best"] is True


def test_group_keys() -> None:
    from prodavan.application.modules.equipment_offers_service import (
        group_hash_keys,
        group_pn_keys,
    )

    body = {"part_number": "ABC-123", "aliases_pn": "abc123, XYZ", "aliases_hash": "h1; h2"}
    assert group_pn_keys(body) == ["ABC-123", "abc-123", "abc123", "ABC123", "XYZ", "xyz"]
    assert group_hash_keys(body) == ["h1", "h2"]


def test_mark_manual_overrides() -> None:
    from prodavan.application.modules.equipment_offers_service import (
        mark_manual_overrides,
    )

    # budget: markup изменён → manual
    body = mark_manual_overrides(
        table_slug="budget_lines",
        existing_body={"markup": 0.1},
        body={"markup": 0.2},
    )
    assert body["markup_source"] == "manual"

    # offers: изменение валюты фиксируется в manual
    body = mark_manual_overrides(
        table_slug="found_offers",
        existing_body={"currency": "RUB", "price": 100.0},
        body={"currency": "EUR"},
    )
    assert body["manual"] == {"currency": True}


async def test_group_without_offers_shows_pn_face_and_honest_label(monkeypatch) -> None:
    """Группа без офферов (каталог пуст/error): лицо = партномер, а не row_*,
    лейбл «(нет офферов)» вместо вводившего в заблуждение «(под заказ)»."""
    _patch_os(monkeypatch, [])
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)
    io = FakeIO(
        {
            "request_lines": [
                {"row_id": "line_1", "body": {"title": "Позиция", "part_number": "ZZZ-999", "qty": 1}},
            ],
            "found_groups": [
                {
                    "row_id": "grp_z",
                    "body": {
                        "line_id": "line_1",
                        "part_number": "ZZZ-999",
                        "match_kind": "exact",
                        "note": "каталог молчит",
                    },
                },
            ],
            "found_offers": [],
            "trusted_sellers": [],
            "budget_lines": [],
            "procurement": [],
        }
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    grp = io.body("found_groups", "grp_z")
    assert grp["offers_count"] == 0
    # без офферов лицо пустое (UI рисует «Нет оффера» warning), PN — из позиции
    assert grp["face_title"] == ""
    assert grp["line_part_number"] == "ZZZ-999"
    assert grp["match_label"] == "Точное"
    assert grp.get("face_price") is None


async def test_best_and_budget_skip_priceless_offer_when_priced_exists(monkeypatch) -> None:
    """Автовыбор: оффер БЕЗ цены (даже приоритетного поставщика) не бьёт
    priced-альтернативу; бюджет берёт цену и on_order-флаг честно."""
    _patch_os(monkeypatch, [])
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)
    io = FakeIO(
        {
            "request_lines": [
                {"row_id": "line_1", "body": {"title": "Позиция", "part_number": "ABC-1", "qty": 2}},
            ],
            "found_groups": [
                {
                    "row_id": "grp_1",
                    "body": {"line_id": "line_1", "part_number": "ABC-1", "match_kind": "exact"},
                },
            ],
            "found_offers": [
                {
                    "row_id": "offer_noprice",
                    "body": {
                        "group_id": "grp_1",
                        "line_id": "line_1",
                        "title": "Без цены приоритет",
                        "seller": "Приоритет",
                        "part_number": "ABC-1",
                        "src_hash": "hp",
                        "currency": "RUB",
                        "price": None,
                        "in_stock": False,
                        "priority": True,
                        "match_kind": "exact",
                    },
                },
                {
                    "row_id": "offer_priced",
                    "body": {
                        "group_id": "grp_1",
                        "line_id": "line_1",
                        "title": "С ценой",
                        "seller": "Обычный",
                        "part_number": "ABC-1",
                        "src_hash": "hq",
                        "currency": "RUB",
                        "price": 500.0,
                        "in_stock": True,
                        "priority": False,
                        "match_kind": "exact",
                    },
                },
            ],
            "trusted_sellers": [
                {"row_id": "s1", "body": {"name": "Приоритет", "is_enabled": True, "priority_purchase": True}},
                {"row_id": "s2", "body": {"name": "Обычный", "is_enabled": True}},
            ],
            "budget_lines": [],
            "procurement": [],
        }
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    grp = io.body("found_groups", "grp_1")
    assert grp["best_offer_id"] == "offer_priced"
    assert grp["face_price"] == 500.0
    budget = io.rows("budget_lines")
    assert len(budget) == 1
    assert budget[0]["body"]["price_in"] == 500.0
    assert budget[0]["body"]["on_order"] is False


async def test_budget_priceless_on_order_offer_gets_honest_snapshot(monkeypatch) -> None:
    """Единственный оффер — без цены и под заказ: бюджет честный
    (price_in=null, on_order=true), а не маскировка нулём."""
    _patch_os(monkeypatch, [])
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)
    io = FakeIO(
        {
            "request_lines": [
                {"row_id": "line_1", "body": {"title": "Позиция", "part_number": "ABC-2", "qty": 1}},
            ],
            "found_groups": [
                {
                    "row_id": "grp_1",
                    "body": {"line_id": "line_1", "part_number": "ABC-2", "match_kind": "exact"},
                },
            ],
            "found_offers": [
                {
                    "row_id": "offer_x",
                    "body": {
                        "group_id": "grp_1",
                        "line_id": "line_1",
                        "title": "Под заказ без цены",
                        "seller": "П",
                        "part_number": "ABC-2",
                        "src_hash": "hx",
                        "currency": "RUB",
                        "price": None,
                        "in_stock": False,
                        "match_kind": "exact",
                    },
                },
            ],
            "trusted_sellers": [],
            "budget_lines": [],
            "procurement": [],
        }
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)

    budget = io.rows("budget_lines")
    assert len(budget) == 1
    assert budget[0]["body"]["price_in"] is None
    assert budget[0]["body"]["on_order"] is True
    grp = io.body("found_groups", "grp_1")
    assert grp["match_label"] == "Точное (нет офферов)" or grp["offers_count"] == 1


async def test_best_prefers_instock_priceless_over_onorder_priced(monkeypatch) -> None:
    """ТЗ 2026-10-07: безценовой («Уточняйте») оффер В НАЛИЧИИ выигрывает у
    priced «под заказ»; но priced в наличии выигрывает у безценового в наличии."""
    _patch_os(monkeypatch, [])
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)

    def _offer(rid, *, price, in_stock):
        return {
            "row_id": rid,
            "body": {
                "group_id": "grp_1",
                "line_id": "line_1",
                "title": rid,
                "seller": "П",
                "part_number": "ABC-3",
                "src_hash": rid,
                "currency": "RUB",
                "price": price,
                "in_stock": in_stock,
                "match_kind": "exact",
            },
        }

    io = FakeIO(
        {
            "request_lines": [
                {"row_id": "line_1", "body": {"title": "П", "part_number": "ABC-3", "qty": 1}},
            ],
            "found_groups": [
                {"row_id": "grp_1", "body": {"line_id": "line_1", "part_number": "ABC-3", "match_kind": "exact"}},
            ],
            "found_offers": [
                _offer("o_noprice_stock", price=None, in_stock=True),
                _offer("o_priced_order", price=900.0, in_stock=False),
            ],
            "trusted_sellers": [],
            "budget_lines": [],
            "procurement": [],
        }
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=False)
    grp = io.body("found_groups", "grp_1")
    assert grp["best_offer_id"] == "o_noprice_stock"

    # второй прогон: добавился priced в наличии — он забирает best
    await io.create(
        "found_offers",
        {
            "group_id": "grp_1",
            "line_id": "line_1",
            "title": "priced stock",
            "seller": "П",
            "part_number": "ABC-3",
            "src_hash": "o_priced_stock",
            "currency": "RUB",
            "price": 1200.0,
            "in_stock": True,
            "match_kind": "exact",
        },
    )
    await svc.run(io, materialize=False)
    grp = io.body("found_groups", "grp_1")
    assert grp["best_offer_id"] != "o_noprice_stock"
    assert io.body("found_offers", grp["best_offer_id"])["price"] == 1200.0


async def test_budget_snapshot_carries_match_kind(monkeypatch) -> None:
    """Аналог/сомнение: бюджет хранит match_kind — UI красит наименование/P/N."""
    _patch_os(monkeypatch, [])
    monkeypatch.setattr(fx_mod, "convert_offer_price", _fake_convert)
    io = FakeIO(
        {
            "request_lines": [
                {"row_id": "line_1", "body": {"title": "П", "part_number": "ABC-4", "qty": 1}},
            ],
            "found_groups": [
                {"row_id": "grp_1", "body": {"line_id": "line_1", "part_number": "ABC-4", "match_kind": "analog"}},
            ],
            "found_offers": [
                {
                    "row_id": "offer_a",
                    "body": {
                        "group_id": "grp_1",
                        "line_id": "line_1",
                        "title": "Аналог",
                        "seller": "П",
                        "part_number": "ABC-4-AN",
                        "src_hash": "ha",
                        "currency": "RUB",
                        "price": 100.0,
                        "in_stock": True,
                        "match_kind": "analog",
                    },
                },
            ],
            "trusted_sellers": [],
            "budget_lines": [],
            "procurement": [],
        }
    )
    svc = EquipmentPipelineService(session=object())
    await svc.run(io, materialize=True)
    budget = io.rows("budget_lines")
    assert len(budget) == 1
    assert budget[0]["body"]["match_kind"] == "analog"


async def test_build_from_slot_groups_best_and_budget(io: FakeIO) -> None:
    """WAVE10: сборка собирается из групп-кандидатов слота.

    Слот = тип комплектующего; его кандидаты — found_groups с build_id+slot_type_id
    (материализуются как обычные группы). Цена сборки = Σ(best-группа слота).
    Среди сборок позиции — best (точность→цена); бюджет берётся из сборки.
    """
    svc = EquipmentPipelineService(session=object())
    # Сборка ПК на позиции line_1, слот CPU: две группы-кандидата с офферами.
    await io.create(
        "equipment_builds",
        {"name": "ПК Intel", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "aliases_pn": "ABC123",
            "match_kind": "exact",
        },
    )
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "",
            "aliases_hash": "h4",
            "match_kind": "doubt",
        },
    )
    # second build (alternative) на той же позиции — другой CPU, дороже
    await io.create(
        "equipment_builds",
        {"name": "ПК AMD", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build2_id = io.rows("equipment_builds")[1]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build2_id,
            "slot_type_id": "etype_cpu",
            "part_number": "",
            "aliases_hash": "h4",
            "match_kind": "doubt",
        },
    )

    await svc.run(io, materialize=True)

    b1 = next(r["body"] for r in io.rows("equipment_builds") if r["row_id"] == build_id)
    # слот CPU: best-группа = exact (grp на ABC-123), цена лица = 120 (Сидоров, priority)
    assert b1["components_count"] == 1
    assert b1["price_total"] == 120.0
    assert b1["match_kind"] == "exact"
    # две сборки позиции → одна best, у неё 1 альтернатива
    assert b1["is_best"] is True
    assert b1["alternatives_count"] == 1
    b2 = next(r["body"] for r in io.rows("equipment_builds") if r["row_id"] == build2_id)
    # is_best=False — дефолт, ключ может отсутствовать (без write-amplification)
    assert b2.get("is_best") is not True
    assert b2["price_total"] == 450.0

    # бюджет позиции line_1 взят ИЗ СБОРКИ
    budget = [r["body"] for r in io.rows("budget_lines") if r["body"].get("line_id") == "line_1"]
    assert budget
    assert budget[0]["build_id"] == build_id
    assert budget[0]["price_in"] == 120.0

    # снапшот сборок на позиции (виден в списке позиций)
    line_body = io.rows("request_lines")[0]["body"]
    assert line_body["builds_count"] == 2
    assert line_body["build_best_id"] == build_id
    assert line_body["build_best_price"] == 120.0

    # лучший кандидат слота помечен is_best; альтернатива — нет
    cpu_groups = [r for r in io.rows("found_groups") if r["body"].get("slot_type_id") == "etype_cpu"]
    best_slot = next(
        r["body"]
        for r in cpu_groups
        if r["body"].get("build_id") == build_id and r["body"].get("part_number") == "ABC-123"
    )
    assert best_slot.get("is_best") is True
    # владелец группы помечен: build — кандидат слота (не виден в общем списке)
    assert best_slot.get("owner_kind") == "build"
    line_groups = [r["body"] for r in io.rows("found_groups") if r["body"].get("line_id") == "line_1"]
    assert all(g.get("owner_kind") == "line" for g in line_groups)


async def test_slot_group_orphan_cleanup_keeps_build_groups(io: FakeIO) -> None:
    """Группа-кандидат слота (build_id) не считается сиротой и не удаляется."""
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {"build_id": build_id, "slot_type_id": "etype_cpu", "part_number": "ABC-123", "match_kind": "exact"},
    )
    slot_grp_id = io.rows("found_groups")[-1]["row_id"]

    await svc.run(io, materialize=False)
    assert ("found_groups", slot_grp_id) not in io.deleted


async def test_manual_slot_choice_drives_build_price_and_match(io: FakeIO) -> None:
    """Ручной выбор кандидата слота определяет цену/точность сборки.

    Регресс: раньше slot_price брался из автобest, а ручной выбор влиял только
    на is_best — UI показывал выбранный вариант, а итог считался по другому.
    """
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "aliases_pn": "ABC123",
            "match_kind": "exact",
        },
    )
    cheap_id = io.rows("found_groups")[-1]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "",
            "aliases_hash": "h4",
            "match_kind": "doubt",
        },
    )
    dear_id = io.rows("found_groups")[-1]["row_id"]

    # авто: exact дешевле (120) → он и в цене, и в match_kind
    await svc.run(io, materialize=True)
    b = io.body("equipment_builds", build_id)
    assert b["price_total"] == 120.0
    assert b["match_kind"] == "exact"
    assert io.body("found_groups", cheap_id)["is_best"] is True

    # ручной выбор дорогого doubt-кандидата → цена и точность следуют за ним
    body = io.body("equipment_builds", build_id)
    body["slots"] = {"etype_cpu": dear_id}
    await io.update("equipment_builds", build_id, body)
    await svc.run(io, materialize=False)

    b = io.body("equipment_builds", build_id)
    assert b["price_total"] == 450.0
    assert b["match_kind"] == "doubt"
    assert io.body("found_groups", dear_id)["is_best"] is True
    assert io.body("found_groups", cheap_id).get("is_best") is not True
    # бюджет позиции тоже следует за ручным выбором
    budget = [r["body"] for r in io.rows("budget_lines") if r["body"].get("line_id") == "line_1"]
    assert budget and budget[0]["price_in"] == 450.0


async def test_build_on_order_flag_and_budget(io: FakeIO) -> None:
    """on_order сборки — явный флаг, бюджет читает его (не текст match_label)."""
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_gpu",
            "part_number": "GPU-1",
            "match_kind": "exact",
        },
    )
    # оффер «под заказ» — пишем напрямую (материализация не нужна для проверки флага)
    await io.create(
        "found_offers",
        {
            "group_id": io.rows("found_groups")[-1]["row_id"],
            "title": "GPU",
            "seller": "Иванов",
            "part_number": "GPU-1",
            "src_hash": "h7",
            "currency": "RUB",
            "price_orig": 1000.0,
            "price": 1000.0,
            "in_stock": False,
            "is_stale": False,
        },
    )

    await svc.run(io, materialize=False)

    b = io.body("equipment_builds", build_id)
    assert b["on_order"] is True
    assert "под заказ" in b["match_label"].lower()
    budget = [r["body"] for r in io.rows("budget_lines") if r["body"].get("line_id") == "line_1"]
    assert budget and budget[0]["on_order"] is True


async def test_line_snapshot_cleared_when_builds_gone(io: FakeIO) -> None:
    """Снапшот build_* на позиции гаснет, когда сборки удалены."""
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "aliases_pn": "ABC123",
            "match_kind": "exact",
        },
    )
    await svc.run(io, materialize=True)
    assert io.body("request_lines", "line_1")["builds_count"] == 1
    assert io.body("request_lines", "line_1")["build_best_price"] == 120.0

    await io.delete("equipment_builds", build_id)
    await svc.run(io, materialize=False)

    line = io.body("request_lines", "line_1")
    assert line["builds_count"] == 0
    assert line["build_best_id"] == ""
    assert line["build_best_price"] is None
    # бюджет вернулся к обычному офферу позиции
    budget = [r["body"] for r in io.rows("budget_lines") if r["body"].get("line_id") == "line_1"]
    assert budget and budget[0].get("build_id") in ("", None)


async def test_orphan_build_cleaned_when_line_deleted_bypassing_cascade(io: FakeIO) -> None:
    """Сборка с мёртвым line_id удаляется пайплайном, её слот-группы — тоже."""
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_dead", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "match_kind": "exact",
        },
    )
    grp_id = io.rows("found_groups")[-1]["row_id"]

    await svc.run(io, materialize=True)

    assert ("equipment_builds", build_id) in io.deleted
    assert ("found_groups", grp_id) in io.deleted
    assert io.rows("equipment_builds") == []


@pytest.mark.parametrize("table_slug", ["request_lines", "equipment_builds"])
async def test_cascade_delete_covers_builds_and_slot_groups(
    io: FakeIO, table_slug: str
) -> None:
    """Каскад: удаление позиции тянет сборки + слот-группы; удаление сборки — её группы."""
    from prodavan.application.modules.equipment_offers_service import cascade_equipment_delete

    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "match_kind": "exact",
        },
    )
    grp_id = io.rows("found_groups")[-1]["row_id"]
    await io.create(
        "found_offers",
        {"group_id": grp_id, "title": "Товар", "seller": "Иванов", "src_hash": "h1"},
    )
    offer_id = io.rows("found_offers")[-1]["row_id"]

    if table_slug == "request_lines":
        row_id, row_body = "line_1", {}
    else:
        row_id, row_body = build_id, {}

    await cascade_equipment_delete(
        object(),
        cabinet_id="cab",
        project_id="proj",
        table_slug=table_slug,
        row_id=row_id,
        row_body=row_body,
        principal=None,
        employee=None,
        session_id=None,
        io=io,
    )

    # слот-группа и её оффер удалены в обоих сценариях
    assert ("found_groups", grp_id) in io.deleted
    assert ("found_offers", offer_id) in io.deleted
    if table_slug == "request_lines":
        # сборка — зависимая строка позиции, её тянет каскад
        assert ("equipment_builds", build_id) in io.deleted
    else:
        # саму сборку удаляет вызывающий сервис (контракт каскада: только зависимости)
        assert ("equipment_builds", build_id) not in io.deleted


async def test_procurement_attributes_build_components_to_sellers(io: FakeIO) -> None:
    """Закупка для позиции-сборки раскладывается по поставщикам компонентов.

    Регресс: строка бюджета сборки имеет seller="" — без раскладки позиция
    вообще не попадала в «Закупку», хотя деньги по ней есть.
    """
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_2", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    # CPU у «Иванов» (100) и GPU-хэш у «Петров» (h4 → 5 USD → 450 RUB)
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "",
            "aliases_hash": "h1",
            "match_kind": "exact",
        },
    )
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_gpu",
            "part_number": "",
            "aliases_hash": "h4",
            "match_kind": "exact",
        },
    )

    await svc.run(io, materialize=True)

    build = io.body("equipment_builds", build_id)
    assert build["components_count"] == 2
    total = build["price_total"]
    assert total > 0

    # строка бюджета — сборка (без единого продавца)
    budget = [r["body"] for r in io.rows("budget_lines") if r["body"].get("line_id") == "line_2"]
    assert budget and budget[0]["build_id"] == build_id
    assert budget[0]["seller"] == ""

    # закупка: компоненты сборки расложены по своим поставщикам.
    # Оба компонента у «Иванов» (h1 = 100, h4 = 5 USD → 450) → 550 = цене сборки.
    proc = {r["body"]["seller"]: r["body"] for r in io.rows("procurement")}
    assert proc, "закупка пустая — компоненты сборки не учтены"
    assert proc["Иванов"]["sum_rub"] == round(total, 2)
    assert proc["Иванов"]["selected_count"] == 2
    # обычная позиция line_1 (не сборка) считается как раньше — 2 × 120 у «Сидоров»
    assert proc["Сидоров"]["sum_rub"] == 240.0
    assert proc["Сидоров"]["selected_count"] == 1


async def test_stale_manual_slot_choice_self_heals(io: FakeIO) -> None:
    """Протухший ручной выбор слота заменяется фактически учтённым кандидатом.

    Иначе UI показывает один компонент, а price_total считается по другому.
    """
    svc = EquipmentPipelineService(session=object())
    await io.create(
        "equipment_builds",
        {"name": "ПК", "build_kind": "pc", "line_id": "line_1", "slots": {}},
    )
    build_id = io.rows("equipment_builds")[0]["row_id"]
    await io.create(
        "found_groups",
        {
            "build_id": build_id,
            "slot_type_id": "etype_cpu",
            "part_number": "ABC-123",
            "aliases_pn": "ABC123",
            "match_kind": "exact",
        },
    )
    live_id = io.rows("found_groups")[-1]["row_id"]

    # ручной выбор указывает на несуществующую группу
    body = io.body("equipment_builds", build_id)
    body["slots"] = {"etype_cpu": "grp_deleted"}
    await io.update("equipment_builds", build_id, body)

    await svc.run(io, materialize=True)

    b = io.body("equipment_builds", build_id)
    assert b["slots"]["etype_cpu"] == live_id
    assert b["price_total"] == 120.0
    assert io.body("found_groups", live_id)["is_best"] is True
