"""S4B credential vault: encrypt at rest, never expose password via API."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid

from prodavan.config.settings import settings
from prodavan.infrastructure.storage.catalog_storage import now_iso, vault_path, write_json


def _key() -> bytes:
    return hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_secret(plain: str) -> str:
    raw = _xor(plain.encode("utf-8"), _key())
    return base64.urlsafe_b64encode(raw).decode("ascii")


def mask_username(username: str) -> str:
    if "@" in username:
        local, domain = username.split("@", 1)
        shown = local[:2] if local else ""
        return f"{shown}***@{domain}"
    if len(username) <= 3:
        return "***"
    return f"{username[:2]}***"


def load_vault(tenant_id: uuid.UUID) -> dict | None:
    path = vault_path(tenant_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_vault(tenant_id: uuid.UUID, username: str, password: str) -> dict:
    payload = {
        "username": username,
        "password_ciphertext": encrypt_secret(password),
        "state": "credentials_invalid",
        "last_error": "s4b_ping not_implemented",
        "updated_at": now_iso(),
    }
    write_json(vault_path(tenant_id), payload)
    return payload


def delete_vault(tenant_id: uuid.UUID) -> None:
    path = vault_path(tenant_id)
    if path.is_file():
        path.unlink()


def public_status(tenant_id: uuid.UUID) -> dict:
    vault = load_vault(tenant_id)
    if vault is None:
        return {
            "state": "missing_credentials",
            "last_validated_at": None,
            "s4b_username_hint": None,
            "rate_limit_reset_at": None,
        }
    return {
        "state": vault.get("state", "missing_credentials"),
        "last_validated_at": None,
        "s4b_username_hint": mask_username(vault.get("username") or ""),
        "rate_limit_reset_at": None,
        "last_error": vault.get("last_error"),
    }
