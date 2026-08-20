"""Parse S4B JSON/ZIP payloads into item dicts. No invented prices."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any
from urllib.parse import urljoin, urlparse

from prodavan.domain.s4b_stock import effective_s4b_availability

_DISTR_DATE_RE = re.compile(r"\s+от\s+\d{1,2}\.\d{1,2}(\.\d{1,2})?$", re.I)


def normalize_distributor(raw: str) -> str:
    return _DISTR_DATE_RE.sub("", (raw or "").strip())


def clean_price(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and "call" in raw.lower():
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw).strip())
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = parts[0] + "." + "".join(parts[1:])
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def clean_quantity(raw: Any) -> str:
    if raw is None:
        return "0"
    text = str(raw).strip()
    return text or "0"


def offer_fingerprint(pn: str, distributor: str, name: str, availability: str) -> str:
    key = "|".join(
        [
            (pn or "").strip().lower(),
            normalize_distributor(distributor).lower(),
            (name or "").strip().lower(),
            (availability or "").strip().lower(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def resolve_poll_url(base_url: str, url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    text = url.strip()
    if not text or text.upper() == "X" or text == "-":
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if text.startswith("/"):
        return origin + text
    if len(text) < 5 and "." not in text:
        return None
    return urljoin(origin + "/", text.lstrip("/"))


def parse_upstream_error(obj: dict[str, Any]) -> dict[str, Any] | None:
    status = obj.get("status")
    if isinstance(status, str) and status.lower().startswith("error"):
        return {
            "ok": False,
            "error_code": "auth_failed" if "авториз" in status.lower() else "upstream_error",
            "error": status,
        }
    url = obj.get("url")
    if isinstance(url, str) and url.strip().upper() == "X" and "results" not in obj:
        return {"ok": False, "error_code": "upstream_error", "error": "S4B url=X without results"}
    return None


def to_outbound_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    raw = item.get("availability")
    out["availability_raw"] = raw
    lead = item.get("delivery_time") or item.get("lead_time")
    out["lead_time"] = lead or None
    if lead and not out.get("delivery_time"):
        out["delivery_time"] = lead
    out["availability"] = effective_s4b_availability(out)
    return out


def parse_response(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if raw.get("ok") is False:
        return [], {"queries": 0, "parse_errors": 0}
    items: list[dict[str, Any]] = []
    parse_errors = 0
    blocks = raw.get("results") or []
    per_query: list[dict[str, Any]] = []
    for block in blocks:
        query = block.get("in") or ""
        q_stock = 0
        q_nostock = 0
        for availability, key in (("stock", "listStock"), ("noStock", "listNoStock")):
            rows = (block.get(key) or {}).get("rows") or []
            for row in rows:
                if not isinstance(row, list) or len(row) < 3:
                    parse_errors += 1
                    continue
                try:
                    if availability == "stock":
                        if len(row) < 10:
                            parse_errors += 1
                            continue
                        pn = str(row[1]).strip()
                        name = str(row[2]).strip()
                        price = clean_price(row[7])
                        qty = clean_quantity(row[3])
                        distributor = normalize_distributor(str(row[8]))
                        brand = str(row[9]).strip()
                        delivery = str(row[5]).strip()
                        s4b_id = str(row[0]).strip()
                    else:
                        if len(row) < 7:
                            parse_errors += 1
                            continue
                        pn = str(row[1]).strip()
                        name = str(row[2]).strip()
                        price = clean_price(row[4] if len(row) > 4 else row[3])
                        qty = "Уточните"
                        distributor = normalize_distributor(str(row[5]))
                        brand = str(row[6]).strip()
                        delivery = "Под заказ"
                        s4b_id = str(row[0]).strip()
                    items.append(
                        {
                            "s4b_id": s4b_id,
                            "query": query,
                            "pn": pn,
                            "name": name,
                            "price_rub": price,
                            "quantity": qty,
                            "brand": brand,
                            "distributor": distributor,
                            "availability": availability,
                            "delivery_time": delivery,
                            "fingerprint": offer_fingerprint(pn, distributor, name, availability),
                        }
                    )
                    if availability == "stock":
                        q_stock += 1
                    else:
                        q_nostock += 1
                except Exception:
                    parse_errors += 1
                    continue
        per_query.append({"query": query, "stock": q_stock, "no_stock": q_nostock})
    return items, {"queries": len(blocks), "parse_errors": parse_errors, "per_query": per_query}


def decode_body_bytes(data: bytes, content_type: str = "") -> dict[str, Any]:
    if "application/zip" in (content_type or "") or (len(data) >= 2 and data[:2] == b"PK"):
        return decode_zip_bytes(data)
    head = data[:256].lstrip()
    if head.startswith(b"<") or b"<html" in head[:64].lower():
        return {
            "ok": False,
            "error_code": "auth_failed",
            "error": "S4B returned HTML (usually bad login/password)",
        }
    obj = _decode_json_bytes(data)
    if obj is None:
        return {"ok": False, "error_code": "parse", "error": "Invalid JSON from S4B"}
    err = parse_upstream_error(obj)
    return err or obj


def decode_zip_bytes(data: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            json_names = [n for n in names if n.lower().endswith(".json")]
            xlsx_names = [n for n in names if n.lower().endswith(".xlsx")]
            if json_names:
                payload = json.loads(archive.read(json_names[0]).decode("utf-8"))
                if not isinstance(payload, dict):
                    return {"ok": False, "error_code": "parse", "error": "ZIP JSON is not an object"}
                err = parse_upstream_error(payload)
                return err or payload
            if xlsx_names:
                return {
                    "ok": False,
                    "error_code": "zip_xlsx_not_parsed",
                    "error": "S4B ZIP contains xlsx; rows not invented",
                }
    except zipfile.BadZipFile:
        return {"ok": False, "error_code": "parse", "error": "Corrupt S4B ZIP"}
    return {"ok": False, "error_code": "parse", "error": "S4B ZIP has no JSON/XLSX"}


def _decode_json_bytes(data: bytes) -> dict[str, Any] | None:
    for encoding in ("utf-8", "cp1251"):
        try:
            obj = json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None
