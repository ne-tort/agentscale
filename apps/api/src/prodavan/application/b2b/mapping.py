"""B2B card -> Agentscale tender row body (structured, no raw HTML)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from prodavan.domain.tenders import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_INACTIVE,
    WORK_IN_PROGRESS,
    WORK_INVALID,
    WORK_NEW,
    WORK_PROCESSED,
)

from .dto import B2BCard, B2BPosition

TERMINAL_STATUSES = frozenset({"archived", "canceled", "cancelled", "deleted"})
_PN_KEYS = ("part_number", "partnumber", "part-number", "артикул", "код", "pn", "каталожный номер")


def stable_row_id(source_ref: str) -> str:
    digest = hashlib.sha1(source_ref.encode("utf-8")).hexdigest()[:16]
    return f"tnd_{digest}"


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _qty(value: Any) -> float:
    return _as_float(value) or 1.0


def _part_number(position: B2BPosition) -> str:
    for item in position.additional_fields:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("label") or item.get("title") or "").strip().lower()
        value = item.get("value") or item.get("text")
        if value and (label in _PN_KEYS or any(k in label for k in _PN_KEYS)):
            return str(value).strip()[:200]
    return ""


def _position_comment(position: B2BPosition, *, lot_name: str) -> str:
    parts: list[str] = []
    if lot_name:
        parts.append(f"Лот: {lot_name}")
    for item in position.additional_fields:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("label") or "").strip()
        value = item.get("value") or item.get("text")
        if value and label:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)[:2000]


def _line_id(position: B2BPosition, ordinal: int, seen: set[str]) -> str:
    raw = f"{position.lot_id or ''}|{position.group_number or ''}|{position.name}|{ordinal}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    candidate = f"p{digest}"
    while candidate in seen:
        candidate = f"p{digest}{ordinal}"
    seen.add(candidate)
    return candidate


def _lines(card: B2BCard) -> list[dict[str, Any]]:
    lots = {lot.lot_id: lot for lot in card.lots if lot.lot_id is not None}
    seen: set[str] = set()
    lines: list[dict[str, Any]] = []
    for ordinal, position in enumerate(card.positions):
        title = (position.name or "").strip()
        if not title:
            continue
        lot = lots.get(position.lot_id)
        lines.append(
            {
                "id": _line_id(position, ordinal, seen),
                "title": title,
                "part_number": _part_number(position),
                "qty": _qty(position.quantity),
                "unit": (position.unit_name or (lot.unit_name if lot else "") or "").strip(),
                "price": _as_float(position.price_unit),
                "comment": _position_comment(position, lot_name=lot.name if lot else ""),
                "source_position_id": str(ordinal + 1),
            }
        )
    if not lines:
        for ordinal, lot in enumerate(card.lots):
            title = (lot.name or lot.description or "").strip()
            if not title:
                continue
            lines.append(
                {
                    "id": f"l{lot.lot_id if lot.lot_id is not None else ordinal + 1}",
                    "title": title,
                    "part_number": "",
                    "qty": _qty(lot.quantity),
                    "unit": (lot.unit_name or "").strip(),
                    "price": _as_float(lot.price),
                    "comment": (lot.description or "")[:2000],
                    "source_position_id": str(ordinal + 1),
                }
            )
    return lines


def _legacy_status(work_status: str) -> str:
    return {
        WORK_NEW: "draft",
        WORK_IN_PROGRESS: "active",
        WORK_PROCESSED: "won",
        WORK_INVALID: "irrelevant",
    }.get(work_status, "active")


def _content_hash(body: dict[str, Any]) -> str:
    """Hash only source-of-truth fields; workflow/user edits must not trigger."""
    ignored = {
        "content_hash",
        "work_status",
        "lifecycle_status",
        "status",
        "comment",
        "taken_at",
        "taken_by",
        "imported_chat_ids",
        "project_ids",
        "last_synced_at",
        "positions_count",
        "linked_positions_count",
        "found_pct",
        "sum_rub",
        "margin_pct",
        "sum_margin_rub",
        "suppliers_count",
    }
    payload = {k: v for k, v in body.items() if k not in ignored}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def card_to_tender_body(
    card: B2BCard,
    *,
    category: str,
    streams: str,
    existing: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now(UTC)
    old = dict(existing or {})
    work_status = str(old.get("work_status") or WORK_NEW).strip() or WORK_NEW
    if work_status not in {"new", "in_progress", "processed", "invalid", "on_hold"}:
        work_status = WORK_NEW
    deadline = card.date_end.isoformat() if card.date_end else None
    lifecycle = LIFECYCLE_INACTIVE if (
        (card.status or "").lower() in TERMINAL_STATUSES
        or (card.date_end is not None and card.date_end <= at)
    ) else LIFECYCLE_ACTIVE
    lines = _lines(card)
    customer = card.customer or card.organizer
    body: dict[str, Any] = {
        "title": (card.name or "Тендер B2B-Center").strip()[:500],
        "customer": (customer.name if customer else "") or "",
        "customer_inn": (customer.inn if customer else "") or "",
        "deadline_at": deadline,
        "status": str(old.get("status") or _legacy_status(work_status)),
        "work_status": work_status,
        "lifecycle_status": lifecycle,
        "description": (card.description or "")[:8000],
        "comment": str(old.get("comment") or (card.comment or card.payment_terms or ""))[:8000],
        "number": (card.number or "")[:64],
        "federal_law": (card.federal_law or "")[:32],
        "trade_type": (card.trade_type or "")[:128],
        "organizer": (card.organizer.name if card.organizer else "") or "",
        "procedure_place": (card.procedure_place or "")[:2000],
        "price_total": _as_float(card.price_total),
        "currency": (card.currency or card.currency_code or "RUB")[:16],
        "published_at": card.publish_date.isoformat() if card.publish_date else None,
        "source_url": card.web_url[:2000],
        "source_ref": f"b2b:{card.uid}",
        "source_platform": "b2b-center",
        "category": category,
        "okpd_codes": card.okpd_codes,
        "source_streams": streams,
        "lines_json": lines,
        "positions_count": len(lines),
        "last_synced_at": at.isoformat(),
    }
    for key in ("imported_chat_ids", "taken_at", "taken_by", "project_ids", "relevance_note", "meta"):
        if key in old:
            body[key] = old[key]
    body["content_hash"] = _content_hash(body)
    return body


def changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = set(old) | set(new)
    out = [k for k in sorted(keys) if old.get(k) != new.get(k)]
    return out
