"""Typed DTOs for the B2B-Center Integration API (JSON, read-only).

Ported from the standalone b2b-crawler; intentionally limited to the fields the
Agentscale tender module needs. Everything is pure JSON — no HTML scraping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _int(value: Any) -> int | None:
    try:
        raw = str(value).strip()
        return int(float(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    return raw or None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    raw = str(value).strip()
    return [raw] if raw else []


def _dt(value: Any) -> datetime | None:
    """B2B-Center dates are unix seconds; <=0 means "not set"."""
    unix = _int(value)
    if unix is None or unix <= 0:
        return None
    try:
        return datetime.fromtimestamp(unix, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


class B2BFirm(BaseModel):
    name: str | None = None
    inn: str | None = None
    kpp: str | None = None

    @classmethod
    def from_api(cls, value: Any) -> B2BFirm | None:
        if not isinstance(value, dict):
            return None
        return cls(name=_str(value.get("name")), inn=_str(value.get("inn")), kpp=_str(value.get("kpp")))


class B2BLot(BaseModel):
    lot_id: int | None = None
    name: str = ""
    description: str | None = None
    okpd2: list[str] = Field(default_factory=list)
    okved2: list[str] = Field(default_factory=list)
    quantity: str | None = None
    unit_name: str | None = None
    price: Decimal | None = None
    price_no_tax: Decimal | None = None
    delivery_place: list[str] = Field(default_factory=list)

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> B2BLot:
        return cls(
            lot_id=_int(value.get("id")),
            name=_str(value.get("name")) or "",
            description=_str(value.get("description")),
            okpd2=_str_list(value.get("okpd2")),
            okved2=_str_list(value.get("okved2")),
            quantity=_str(value.get("quantity")),
            unit_name=_str(value.get("unit_name")),
            price=_dec(value.get("price")),
            price_no_tax=_dec(value.get("price_no_tax")),
            delivery_place=_str_list(value.get("delivery_place")),
        )


class B2BPosition(BaseModel):
    name: str = ""
    lot_id: int | None = None
    group_number: int | None = None
    quantity: Decimal | None = None
    unit_name: str | None = None
    price_unit: Decimal | None = None
    price_all: Decimal | None = None
    additional_fields: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> B2BPosition:
        fields = value.get("additional_fields")
        return cls(
            name=_str(value.get("name")) or "",
            lot_id=_int(value.get("lot_id")),
            group_number=_int(value.get("position_group_number")),
            quantity=_dec(value.get("quantity")),
            unit_name=_str(value.get("unit_name")),
            price_unit=_dec(value.get("price_unit")),
            price_all=_dec(value.get("price_all")),
            additional_fields=[x for x in fields or [] if isinstance(x, dict)],
        )


class B2BCard(BaseModel):
    """Full procedure card (`TradeProcedures.GetShortTrade`)."""

    uid: str
    number: str | None = None
    type: int | None = None
    trade_type: str | None = None
    is_open: bool | None = None
    federal_law: str | None = None
    name: str = ""
    description: str | None = None
    organizer: B2BFirm | None = None
    customer: B2BFirm | None = None
    status: str = ""
    date_end: datetime | None = None
    publish_date: datetime | None = None
    change_date: datetime | None = None
    payment_terms: str | None = None
    currency: str | None = None
    currency_code: str | None = None
    url: str | None = None
    procedure_place: str | None = None
    comment: str | None = None
    lots: list[B2BLot] = Field(default_factory=list)
    positions: list[B2BPosition] = Field(default_factory=list)

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> B2BCard:
        return cls(
            uid=str(value.get("id") or ""),
            number=_str(value.get("number")),
            type=_int(value.get("type")),
            trade_type=_str(value.get("trade_type")),
            is_open=value.get("is_open") if isinstance(value.get("is_open"), bool) else None,
            federal_law=_str(value.get("federal_law")),
            name=_str(value.get("name")) or "",
            description=_str(value.get("description")),
            organizer=B2BFirm.from_api(value.get("organizer")),
            customer=B2BFirm.from_api(value.get("customer")),
            status=_str(value.get("status")) or "",
            date_end=_dt(value.get("date_end")),
            publish_date=_dt(value.get("publish_date")),
            change_date=_dt(value.get("change_date")),
            payment_terms=_str(value.get("payment_terms")),
            currency=_str(value.get("currency")),
            currency_code=_str(value.get("currency_code")),
            url=_str(value.get("url")),
            procedure_place=_str(value.get("procedure_place")),
            comment=_str(value.get("comment")),
            lots=[B2BLot.from_api(x) for x in value.get("lots") or [] if isinstance(x, dict)],
            positions=[
                B2BPosition.from_api(x) for x in value.get("positions") or [] if isinstance(x, dict)
            ],
        )

    @property
    def okpd_codes(self) -> list[str]:
        out: list[str] = []
        for lot in self.lots:
            for code in lot.okpd2:
                if code not in out:
                    out.append(code)
        return sorted(out)

    @property
    def okved_codes(self) -> list[str]:
        out: list[str] = []
        for lot in self.lots:
            for code in lot.okved2:
                if code not in out:
                    out.append(code)
        return sorted(out)

    @property
    def price_total(self) -> Decimal | None:
        prices = [lot.price for lot in self.lots if lot.price is not None]
        if not prices:
            return None
        total = sum(prices)
        return None if total == 0 else total

    @property
    def position_names(self) -> list[str]:
        return [p.name for p in self.positions if p.name]

    @property
    def web_url(self) -> str:
        if self.url:
            return self.url
        return f"https://www.b2b-center.ru/procedure/view/{self.uid}/"


class B2BTradeRef(BaseModel):
    uid: str
    changed_at: datetime | None = None

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> B2BTradeRef | None:
        uid = _str(value.get("id"))
        if not uid:
            return None
        return cls(uid=uid, changed_at=_dt(value.get("date")))
