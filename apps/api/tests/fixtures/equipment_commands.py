"""Reusable source fixtures for equipment command contract tests."""

from decimal import Decimal

REQUEST_ID = "request-1"
REQUEST_LINE_ID = "line-1"
GROUP_ID = "group-1"
OFFER_ID = "offer-1"

AGENT_GROUP_PAYLOAD = {
    "request_line_id": REQUEST_LINE_ID,
    "title": "Rack server candidates",
    "aliases_pn": ("SRV-1000",),
    "aliases_hash": ("hash-1",),
    "match_kind": "exact",
    "note": "Matches requested CPU and memory.",
}

AUTHORITATIVE_OFFER_FACTS = {
    "price": Decimal("100.00"),
    "currency": "RUB",
    "availability": 4,
}
