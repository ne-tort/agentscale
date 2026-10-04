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
    # Иванов: офферов 2; эффективная позиция line_2 (Кабель, best h4=450)
    # — Закупка агрегирует бюджетные снапшоты (выбор → best), как Бюджетирование.
    assert proc["Иванов"]["offers_count"] == 2
    assert proc["Иванов"]["margin_pct"] == 15
    assert proc["Иванов"]["delivery_rub"] == 300
    assert proc["Иванов"]["sum_rub"] == 450.0
    assert proc["Иванов"]["selected_count"] == 1
    assert proc["Иванов"]["sum_margin_rub"] == round(450.0 * 0.15, 2)
    # Сидоров: эффективная позиция line_1 (best h3=120, qty 2 → 240),
    # markup default 0.1 (нет в реестре) → маржа 24
    assert proc["Сидоров"]["offers_count"] == 1
    assert proc["Сидоров"]["sum_rub"] == 240.0
    assert proc["Сидоров"]["selected_count"] == 1
    assert proc["Сидоров"]["sum_margin_rub"] == 24.0
    # Петров: оффер есть, но не эффективен ни для одной позиции — строка
    # с нулевыми суммами (иначе на Закупках нельзя выбрать его товар)
    assert proc["Петров"]["offers_count"] == 1
    assert proc["Петров"]["sum_rub"] == 0.0
    assert proc["Петров"]["selected_count"] == 0
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
