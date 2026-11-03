"""Opaque, stable list cursors for the Content API.

Keyset pagination on ``(created_at, id)`` descending — the same order the list
queries use, so pages stay stable while new rows are inserted.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def encode_cursor(*, created_at: datetime | None, row_id: str) -> str:
    payload = {
        "t": created_at.isoformat() if created_at else None,
        "i": row_id,
    }
    return _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def decode_cursor(cursor: str | None) -> tuple[datetime | None, str] | None:
    """Return ``(created_at, id)`` or ``None``; malformed cursors are rejected."""
    if not cursor:
        return None
    try:
        payload: Any = json.loads(_unb64(cursor).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("malformed cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("malformed cursor")
    row_id = payload.get("i")
    if not isinstance(row_id, str) or not row_id:
        raise ValueError("malformed cursor")
    raw_time = payload.get("t")
    created_at: datetime | None = None
    if isinstance(raw_time, str) and raw_time:
        try:
            created_at = datetime.fromisoformat(raw_time)
        except ValueError as exc:
            raise ValueError("malformed cursor") from exc
    return created_at, row_id


__all__ = ["encode_cursor", "decode_cursor"]
