"""Process-wide S4B gateway (tests replace with a fake; default is HTTP)."""

from __future__ import annotations

from prodavan.cabinets.electronics_procurement.integrations.s4b_port import S4BGateway
from prodavan.infrastructure.integrations.http_s4b import HttpS4BGateway

_gateway: S4BGateway | None = None


def get_s4b_gateway() -> S4BGateway:
    global _gateway
    if _gateway is None:
        _gateway = HttpS4BGateway()
    return _gateway


def set_s4b_gateway(gateway: S4BGateway | None) -> None:
    global _gateway
    _gateway = gateway
