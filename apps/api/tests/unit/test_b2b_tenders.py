"""B2B mapping/filter unit tests (no DB, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prodavan.application.b2b.dto import B2BCard
from prodavan.application.b2b.filters import B2BCategoryFilter
from prodavan.application.b2b.mapping import card_to_tender_body, stable_row_id


def _card(**overrides):
    now = datetime.now(UTC)
    data = {
        "id": "12345",
        "number": "223-123",
        "name": "Поставка серверов и ноутбуков",
        "description": "Серверы 2U, ноутбуки для сотрудников",
        "status": "actual",
        "federal_law": "223fz",
        "date_end": int((now + timedelta(days=7)).timestamp()),
        "publish_date": int((now - timedelta(days=1)).timestamp()),
        "url": "https://www.b2b-center.ru/procedure/view/12345/",
        "customer": {"name": "ООО Заказчик", "inn": "7700000000"},
        "organizer": {"name": "ООО Организатор"},
        "lots": [{"id": 1, "name": "Серверы", "okpd2": ["26.20.14"], "price": 1000000}],
        "positions": [
            {
                "name": "Сервер 2U",
                "quantity": 2,
                "unit_name": "шт",
                "price_unit": 500000,
                "additional_fields": [{"name": "Артикул", "value": "SRV-1"}],
            },
            {"name": "Ноутбук", "quantity": 10, "unit_name": "шт"},
        ],
    }
    data.update(overrides)
    return B2BCard.from_api(data)


def test_filter_matches_it_category_and_rejects_expired():
    card = _card()
    flt = B2BCategoryFilter(categories=["it"], min_remaining_hours=1)
    decision = flt.match(card)
    assert decision.matched is True
    assert decision.category == "it"

    expired = _card(date_end=int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
    decision = flt.match(expired)
    assert decision.matched is False
    assert "deadline passed" in decision.reasons


def test_filter_rejects_foreign_category():
    card = _card(
        name="Поставка продуктов питания",
        description="Мясо, рыба, овощи",
        lots=[{"id": 1, "name": "Питание", "okpd2": ["10.11"]}],
        positions=[{"name": "Мясо", "quantity": 10}],
    )
    decision = B2BCategoryFilter(categories=["it"]).match(card)
    assert decision.matched is False
    assert decision.category == ""


def test_mapping_structured_fields_and_stable_id():
    card = _card()
    body = card_to_tender_body(card, category="it", streams="b2b-center")
    assert body["source_ref"] == "b2b:12345"
    assert body["source_url"].endswith("/12345/")
    assert body["number"] == "223-123"
    assert body["category"] == "it"
    assert body["customer_inn"] == "7700000000"
    assert body["positions_count"] == 2
    assert body["lines_json"][0]["part_number"] == "SRV-1"
    assert body["lines_json"][0]["price"] == 500000.0
    assert body["lines_json"][1]["qty"] == 10.0
    assert body["work_status"] == "new"
    assert body["lifecycle_status"] == "active"
    assert body["content_hash"]
    assert stable_row_id("b2b:12345") == stable_row_id("b2b:12345")


def test_update_preserves_workflow_and_changes_hash_on_description():
    card = _card()
    first = card_to_tender_body(card, category="it", streams="b2b-center")
    existing = {
        **first,
        "work_status": "in_progress",
        "comment": "ручной комментарий",
        "taken_by": "emp_1",
        "imported_chat_ids": ["sess_1"],
    }
    changed = _card(description="Серверы 2U, ноутбуки; добавлено СХД")
    second = card_to_tender_body(
        changed, category="it", streams="b2b-center", existing=existing
    )
    assert second["work_status"] == "in_progress"
    assert second["comment"] == "ручной комментарий"
    assert second["taken_by"] == "emp_1"
    assert second["imported_chat_ids"] == ["sess_1"]
    assert second["content_hash"] != first["content_hash"]
    assert "СХД" in second["description"]
