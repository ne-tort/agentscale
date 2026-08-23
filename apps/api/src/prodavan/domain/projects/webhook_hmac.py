"""HMAC helpers for signed project webhook ingress (L07)."""

from __future__ import annotations

import hashlib
import hmac


def webhook_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(*, secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = webhook_signature(secret, body)
    return hmac.compare_digest(expected, header.strip())
