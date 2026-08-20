"""S4B parse/stock: drop on_order, keep named prices only."""

from prodavan.application.integrations.s4b_parse import decode_zip_bytes, parse_response, to_outbound_item
from prodavan.application.integrations.s4b_trusted import is_trusted_distributor
from prodavan.domain.s4b_stock import is_s4b_in_stock


def test_parse_drops_nostock_via_availability() -> None:
    raw = {
        "results": [
            {
                "in": "910-001793",
                "listStock": {
                    "rows": [
                        [
                            "1",
                            "910-001793",
                            "Mouse",
                            "5",
                            "5",
                            "1-2 дн",
                            "x",
                            "1200",
                            "Merlion от 07.07",
                            "Logitech",
                        ],
                        [
                            "3",
                            "910-001793",
                            "Mouse backorder",
                            "2",
                            "2",
                            "под заказ",
                            "x",
                            "900",
                            "Merlion",
                            "Logitech",
                        ],
                    ]
                },
                "listNoStock": {
                    "rows": [
                        ["2", "910-001793", "Mouse OO", "~1", "1", "RandomCo", "X"],
                    ]
                },
            }
        ]
    }
    items, _ = parse_response(raw)
    outbound = [to_outbound_item(it) for it in items]
    kept = [it for it in outbound if is_s4b_in_stock(it)]
    assert len(items) == 3
    assert len(kept) == 1
    assert kept[0]["price_rub"] == 1200.0
    assert kept[0]["distributor"] == "Merlion"
    assert is_trusted_distributor("Merlion")


def test_rank_trusted_min_price_beats_cheaper_untrusted() -> None:
    from prodavan.application.catalogs.search import rank_selections

    lineitems = [{"line_id": "line_001", "part_number": "910-001793"}]
    offers = [
        {
            "offer_id": "off_1",
            "line_id": "line_001",
            "part_number": "910-001793",
            "price": 100,
            "trusted_seller": False,
        },
        {
            "offer_id": "off_2",
            "line_id": "line_001",
            "part_number": "910-001793",
            "price": 1200,
            "trusted_seller": True,
        },
    ]
    ranked = rank_selections(lineitems, offers)
    assert ranked["selections"][0]["primary_offer_id"] == "off_2"
    assert "off_1" in ranked["selections"][0]["alternative_offer_ids"]


def test_rate_limited_status_is_not_auth_failed() -> None:
    from prodavan.application.integrations.s4b_parse import parse_upstream_error

    err = parse_upstream_error(
        {"status": "error,Слишком высокая частота запросов. Too frequently."}
    )
    assert err is not None
    assert err["error_code"] == "rate_limited"
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("results.xlsx", b"not-parsed")
    decoded = decode_zip_bytes(buf.getvalue())
    assert decoded["ok"] is False
    assert decoded["error_code"] == "zip_xlsx_not_parsed"
