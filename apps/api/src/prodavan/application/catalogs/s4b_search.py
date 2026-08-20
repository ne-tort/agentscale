"""Search S4B live offers for line items with a part number. Never invent prices."""

from __future__ import annotations

import uuid
from typing import Any

from prodavan.application.integrations.s4b_parse import to_outbound_item
from prodavan.application.integrations.s4b_runtime import get_s4b_gateway
from prodavan.application.integrations.s4b_trusted import split_trusted
from prodavan.domain.s4b_stock import is_s4b_in_stock
from prodavan.infrastructure.auth.s4b_vault import decrypt_secret, load_vault


def search_lineitems_in_s4b(
    tenant_id: uuid.UUID,
    lineitems: list[dict],
    *,
    s4b_enabled: bool,
    start_seq: int = 0,
) -> tuple[list[dict], list[str]]:
    logs: list[str] = []
    if not s4b_enabled:
        logs.append("s4b skipped=profile_not_electronics")
        return [], logs

    vault = load_vault(tenant_id)
    if vault is None:
        logs.append("s4b skipped=missing_credentials; on_order never imported")
        return [], logs
    if vault.get("state") != "credentials_valid":
        logs.append(
            f"s4b skipped={vault.get('state') or 'credentials_invalid'}; on_order never imported"
        )
        return [], logs

    part_numbers = [str(item["part_number"]).strip() for item in lineitems if item.get("part_number")]
    if not part_numbers:
        logs.append("s4b skipped=no_part_numbers")
        return [], logs

    username = vault.get("username") or ""
    password = decrypt_secret(vault["password_ciphertext"])
    result = get_s4b_gateway().search_by_part_numbers(username, password, part_numbers)
    if not result.get("ok"):
        logs.append(f"s4b skipped={result.get('error_code') or 'upstream_error'}")
        return [], logs

    outbound = [to_outbound_item(item) for item in (result.get("items") or [])]
    in_stock = [item for item in outbound if is_s4b_in_stock(item)]
    dropped = len(outbound) - len(in_stock)
    trusted, other = split_trusted(in_stock)
    logs.append(
        f"s4b live hits={len(in_stock)} dropped_on_order={dropped} "
        f"trusted={len(trusted)} other={len(other)}"
    )
    offers = _to_offers(lineitems, trusted, other, start_seq=start_seq)
    return offers, logs


def _to_offers(
    lineitems: list[dict],
    trusted: list[dict[str, Any]],
    other: list[dict[str, Any]],
    *,
    start_seq: int,
) -> list[dict]:
    by_query: dict[str, list[dict]] = {}
    for item in lineitems:
        pn = item.get("part_number")
        if pn:
            by_query.setdefault(str(pn).strip().casefold(), []).append(item)

    offers: list[dict] = []
    seq = start_seq
    for source_item, trusted_flag in [(row, True) for row in trusted] + [(row, False) for row in other]:
        price = source_item.get("price_rub")
        if price is None:
            continue
        query = str(source_item.get("query") or source_item.get("pn") or "").strip()
        matches = by_query.get(query.casefold()) or by_query.get(
            str(source_item.get("pn") or "").strip().casefold()
        )
        if not matches:
            continue
        line = matches[0]
        pn = str(source_item.get("pn") or query)
        seq += 1
        seller = f"{source_item.get('distributor') or 's4b'}-s4b"
        offers.append(
            {
                "offer_id": f"off_{seq:04d}",
                "line_id": line["line_id"],
                "part_number": pn,
                "seller": seller,
                "title": source_item.get("name") or pn,
                "price": price,
                "currency": "RUB",
                "in_stock": True,
                "source": "s4b",
                "match_type": "exact" if pn == line.get("part_number") else "equivalent",
                "trusted_seller": trusted_flag,
            }
        )
    return offers
