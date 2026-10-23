"""Ручное добавление товара в «Мастер прайс».

Мастер-прайс — виртуальная таблица поверх OS-индексов каталогов, поэтому
«добавить товар» нельзя выразить записью в БД: строка обязана попасть в
индекс. Проверяем: документ собирается по каноническому маппингу (иначе
мастер-прайс его не увидит), продавец по умолчанию — «ИТ Взлёт», а фильтр
формы пропускает только поставщиков с флагом master_price.
"""

from __future__ import annotations

from prodavan.application.modules import equipment_master_price_manual as mp
from prodavan.application.modules.equipment_catalog_opensearch import (
    canonical_doc_mappings,
)


def _row(name: str, *, master_price: bool = True, enabled: bool = True) -> dict:
    return {"row_id": f"ts_{name}", "body": {"name": name, "master_price": master_price, "is_enabled": enabled}}


# --- продавцы ---------------------------------------------------------------


def test_preferred_seller_goes_first() -> None:
    sellers = mp.manual_sellers([_row("Zen"), _row("ИТ Взлёт"), _row("Alfa")])
    assert sellers[0] == "ИТ Взлёт"


def test_preferred_seller_without_yo() -> None:
    """«Взлет» без ё — тот же продавец: в данных поставщиков оба написания."""
    sellers = mp.manual_sellers([_row("Zen"), _row("ИТ Взлет")])
    assert sellers[0] == "ИТ Взлет"


def test_others_sorted_alphabetically() -> None:
    sellers = mp.manual_sellers([_row("Zen"), _row("Alfa"), _row("Mir")])
    assert sellers == ["Alfa", "Mir", "Zen"]


def test_only_flagged_sellers() -> None:
    sellers = mp.manual_sellers([
        _row("Flagged"),
        _row("NoFlag", master_price=False),
        _row("Disabled", enabled=False),
    ])
    assert sellers == ["Flagged"]


def test_sellers_deduplicated() -> None:
    sellers = mp.manual_sellers([_row("ИТ Взлёт"), _row("ИТ Взлёт")])
    assert sellers == ["ИТ Взлёт"]


def test_no_sellers_when_none_flagged() -> None:
    assert mp.manual_sellers([_row("X", master_price=False)]) == []


# --- документ индекса -------------------------------------------------------


def test_doc_matches_canonical_mapping() -> None:
    """Все поля документа должны быть в маппинге каталога.

    Мастер-прайс читает ручные строки ТЕМИ ЖЕ запросами (terms supplier,
    sort title.raw, price_num), поэтому лишнее/недостающее поле = строка не
    находится либо падает запрос.
    """
    doc = mp.manual_doc(
        {"supplier": "ИТ Взлёт", "title": "Мышь", "part_number": "PN1", "price": 100},
        cabinet_id="cab_1",
        company_id="co_1",
    )
    allowed = set(canonical_doc_mappings()["properties"])
    assert set(doc) <= allowed, set(doc) - allowed


def test_doc_price_parsed_to_number() -> None:
    doc = mp.manual_doc(
        {"supplier": "S", "title": "T", "price": "1 250,50", "rrc": "1490"},
        cabinet_id="cab_1",
    )
    assert doc["price_num"] == 1250.5
    assert doc["rrc_num"] == 1490.0


def test_doc_non_numeric_price_kept_as_text() -> None:
    """«По запросу» — валидная цена в прайсе, но не число."""
    doc = mp.manual_doc(
        {"supplier": "S", "title": "T", "price": "По запросу"},
        cabinet_id="cab_1",
    )
    assert doc["price"] == "По запросу"
    assert doc["price_num"] is None


def test_doc_empty_price() -> None:
    doc = mp.manual_doc({"supplier": "S", "title": "T"}, cabinet_id="cab_1")
    assert doc["price"] == ""
    assert doc["price_num"] is None


def test_doc_currency_defaults_to_rub() -> None:
    doc = mp.manual_doc({"supplier": "S", "title": "T"}, cabinet_id="cab_1")
    assert doc["currency"] == "RUB"


def test_doc_currency_normalized() -> None:
    doc = mp.manual_doc(
        {"supplier": "S", "title": "T", "currency": "usd"}, cabinet_id="cab_1"
    )
    assert doc["currency"] == "USD"


def test_doc_in_stock_defaults_true() -> None:
    doc = mp.manual_doc({"supplier": "S", "title": "T"}, cabinet_id="cab_1")
    assert doc["in_stock"] is True


def test_doc_in_stock_explicit_false() -> None:
    doc = mp.manual_doc(
        {"supplier": "S", "title": "T", "in_stock": False}, cabinet_id="cab_1"
    )
    assert doc["in_stock"] is False


def test_doc_src_hash_matches_catalog_identity() -> None:
    """src_hash — та же идентичность позиции, что у каталога.

    По ней подбор товаров матчит ручное предложение к позиции заказчика; другая
    формула = ручной товар никогда не попадёт в подбор.
    """
    from prodavan.application.modules.equipment_catalog_opensearch import source_hash

    doc = mp.manual_doc(
        {"supplier": "ИТ Взлёт", "title": "Мышь Logitech"}, cabinet_id="cab_1"
    )
    assert doc["src_hash"] == source_hash("ИТ Взлёт", "Мышь Logitech")


def test_doc_supplier_is_keyword_value() -> None:
    """supplier обязан быть ровно тем, что в trusted_sellers (фильтр terms)."""
    doc = mp.manual_doc(
        {"supplier": "  ИТ Взлёт  ", "title": "T"}, cabinet_id="cab_1"
    )
    assert doc["supplier"] == "ИТ Взлёт"


# --- стабильный doc_id (дедуп) ---------------------------------------------


def test_doc_id_stable_for_same_product() -> None:
    a = mp.manual_doc_id(cabinet_id="c", supplier="S", part_number="PN1", title="Мышь")
    b = mp.manual_doc_id(cabinet_id="c", supplier="s", part_number="pn1", title="мышь")
    assert a == b


def test_doc_id_differs_by_part_number() -> None:
    a = mp.manual_doc_id(cabinet_id="c", supplier="S", part_number="PN1", title="Мышь")
    b = mp.manual_doc_id(cabinet_id="c", supplier="S", part_number="PN2", title="Мышь")
    assert a != b


def test_doc_id_differs_by_cabinet() -> None:
    a = mp.manual_doc_id(cabinet_id="c1", supplier="S", part_number="PN", title="T")
    b = mp.manual_doc_id(cabinet_id="c2", supplier="S", part_number="PN", title="T")
    assert a != b


def test_doc_id_differs_by_supplier() -> None:
    a = mp.manual_doc_id(cabinet_id="c", supplier="A", part_number="PN", title="T")
    b = mp.manual_doc_id(cabinet_id="c", supplier="B", part_number="PN", title="T")
    assert a != b


# --- индекс -----------------------------------------------------------------


def test_manual_index_is_separate_from_catalogs() -> None:
    """Отдельный индекс, не индекс каталога.

    Индексация каталога делает wipe+reindex: ручные позиции в чужом индексе
    стёрлись бы при первой же переиндексации поставщика.
    """
    assert mp.MANUAL_INDEX == "c_master_price_manual"
    assert mp.MANUAL_INDEX != "c_" + "whatever"


def test_manual_index_readable_by_master_price_query() -> None:
    """Имя индекса не содержит префикс namespace дважды (OS добавит сам)."""
    assert not mp.MANUAL_INDEX.startswith("equipment__")
