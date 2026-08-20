"""Search user catalogs and rank offers (no invented prices)."""

from __future__ import annotations

import json
import uuid

from prodavan.cabinets.electronics_procurement.catalogs.indexer import search_exact_pn
from prodavan.infrastructure.storage.catalog_storage import catalog_dir, user_catalogs_root


def list_ready_catalogs(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> list[dict]:
    root = user_catalogs_root(tenant_id, cabinet_id)
    if not root.exists():
        return []
    items: list[dict] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "ready":
            continue
        if manifest.get("archived"):
            continue
        items.append(manifest)
    return items


def search_lineitems_in_catalogs(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    lineitems: list[dict],
) -> tuple[list[dict], list[str]]:
    catalogs = list_ready_catalogs(tenant_id, cabinet_id)
    offers: list[dict] = []
    logs: list[str] = []
    if not catalogs:
        logs.append("catalog skipped=no_databases")
        return offers, logs

    seq = 0
    for catalog in catalogs:
        slug = catalog["slug"]
        db_path = catalog_dir(tenant_id, cabinet_id, slug) / "catalog.sqlite"
        trusted = bool(catalog.get("trusted_seller"))
        hits_total = 0
        for item in lineitems:
            pn = item.get("part_number")
            if not pn:
                continue
            for hit in search_exact_pn(db_path, pn):
                seq += 1
                hits_total += 1
                offers.append(
                    {
                        "offer_id": f"off_{seq:04d}",
                        "line_id": item["line_id"],
                        "part_number": hit["part_number"],
                        "seller": slug,
                        "title": hit.get("title") or pn,
                        "price": hit["price"],
                        "currency": hit.get("currency") or "RUB",
                        "in_stock": True,
                        "source": "catalog",
                        "match_type": "exact",
                        "trusted_seller": trusted,
                    }
                )
        logs.append(f"catalog slug={slug} trusted={trusted} hits={hits_total}")
    return offers, logs


def rank_selections(lineitems: list[dict], offers: list[dict]) -> dict:
    by_line: dict[str, list[dict]] = {}
    for offer in offers:
        by_line.setdefault(offer["line_id"], []).append(offer)

    selections: list[dict] = []
    for item in lineitems:
        candidates = by_line.get(item["line_id"], [])
        if not candidates:
            continue
        pn = item.get("part_number")
        same_pn = [o for o in candidates if pn and o.get("part_number") == pn]
        pool = same_pn or candidates
        trusted = [o for o in pool if o.get("trusted_seller")]
        ranked = trusted or pool
        ranked.sort(key=lambda o: (o.get("price") is None, o.get("price") or 0))
        primary = ranked[0]
        alt = [o["offer_id"] for o in pool if o["offer_id"] != primary["offer_id"]]
        selections.append(
            {
                "line_id": item["line_id"],
                "primary_offer_id": primary["offer_id"],
                "alternative_offer_ids": alt,
            }
        )
    return {"selections": selections}
