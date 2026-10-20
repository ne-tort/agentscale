"""TTL 72ч для «Интернет товары» (web_products) и id документа индекса."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prodavan.application.modules.web_products_opensearch import (
    web_product_doc,
    web_product_doc_id,
    web_products_index_name,
)
from prodavan.application.modules.web_products_ttl_sweep import (
    WEB_PRODUCTS_TTL,
    _row_timestamp,
)


def test_ttl_is_72_hours() -> None:
    assert WEB_PRODUCTS_TTL == timedelta(hours=72)


def test_row_timestamp_prefers_updated_at() -> None:
    body = {
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-20T12:00:00+00:00",
    }
    ts = _row_timestamp(body)
    assert ts is not None and ts.day == 20


def test_row_timestamp_falls_back_to_created_at() -> None:
    body = {"created_at": "2026-10-15T08:30:00Z"}
    ts = _row_timestamp(body)
    assert ts is not None and ts.tzinfo is not None


def test_row_timestamp_missing_returns_none() -> None:
    assert _row_timestamp({}) is None


def test_row_timestamp_bad_value_returns_none() -> None:
    assert _row_timestamp({"updated_at": "not-a-date"}) is None


def test_expiry_boundary() -> None:
    """Строка ровно на границе TTL считается истёкшей."""
    now = datetime.now(UTC)
    created = now - WEB_PRODUCTS_TTL
    assert _row_timestamp({"created_at": created.isoformat()}) <= now - WEB_PRODUCTS_TTL


def test_fresh_row_is_kept() -> None:
    now = datetime.now(UTC)
    fresh = now - timedelta(hours=71)
    assert _row_timestamp({"created_at": fresh.isoformat()}) > now - WEB_PRODUCTS_TTL


# ------------------------------------------------------------- индекс

def test_index_name_per_cabinet() -> None:
    assert web_products_index_name("cab_abc123") == "web_products_cab_abc123"
    # разные кабинеты → разные индексы (не смешиваем товары)
    assert web_products_index_name("cab_a") != web_products_index_name("cab_b")


def test_index_name_sanitizes() -> None:
    assert web_products_index_name("cab/with spaces!") == "web_products_cab_with_spaces"


def test_doc_id_is_stable_and_dedups() -> None:
    a = web_product_doc_id(
        cabinet_id="cab_1", part_number="PN-1", shop="DNS", title="RTX 5060"
    )
    b = web_product_doc_id(
        cabinet_id="cab_1", part_number="pn-1", shop="dns", title="rtx 5060"
    )
    # регистр не создаёт дубль
    assert a == b


def test_doc_id_differs_by_shop() -> None:
    a = web_product_doc_id(cabinet_id="c", part_number="PN", shop="DNS", title="X")
    b = web_product_doc_id(cabinet_id="c", part_number="PN", shop="Citilink", title="X")
    assert a != b


def test_doc_has_pipeline_fields() -> None:
    doc = web_product_doc(
        "row_1",
        {"title": "RTX 5060", "part_number": "PN-1", "price": "51499", "shop": "DNS"},
    )
    assert doc["part_number"] == "PN-1"
    # пайплайн матчит по part_number ИЛИ src_hash
    assert doc["src_hash"] == "pn-1"
    assert doc["price_rub"] == 51499.0
    assert doc["supplier"] == "DNS"
    assert doc["source_kind"] == "web"
    assert doc["currency"] == "RUB"


def test_doc_tolerates_bad_price() -> None:
    doc = web_product_doc("row_1", {"title": "X", "price": "по запросу"})
    assert doc["price_rub"] is None


def test_doc_without_part_number() -> None:
    doc = web_product_doc("row_1", {"title": "Кабель"})
    assert doc["part_number"] == ""
    assert doc["src_hash"] == ""
