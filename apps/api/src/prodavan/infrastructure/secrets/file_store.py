"""File-backed secret store — same secret_ref contract as vault (L03)."""

from __future__ import annotations

import secrets
from pathlib import Path

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

REF_PREFIX = "file://ai_keys/"


class FileSecretStore:
    """Stores plaintext under SECRETS_DIR; DB holds only secret_ref."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or settings.secrets_dir

    def _path_for(self, key_id: str) -> Path:
        safe = key_id.replace("/", "_").replace("..", "_")
        return self._root / "ai_keys" / f"{safe}.secret"

    def put(self, key_id: str, secret: str) -> str:
        if not secret or not secret.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="secret required",
            )
        path = self._path_for(key_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret.strip(), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"{REF_PREFIX}{key_id}.secret"

    def get(self, secret_ref: str) -> str:
        if not secret_ref.startswith(REF_PREFIX):
            raise AppError(
                code="SECRET_BACKEND_UNSUPPORTED",
                title="Secret backend unsupported",
                status=500,
                detail=f"unsupported secret_ref scheme: {secret_ref.split(':', 1)[0]}",
            )
        name = secret_ref.removeprefix(REF_PREFIX)
        key_id = name.removesuffix(".secret")
        path = self._path_for(key_id)
        if not path.is_file():
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="secret_ref missing on disk",
            )
        return path.read_text(encoding="utf-8").strip()

    def delete(self, secret_ref: str) -> None:
        if not secret_ref.startswith(REF_PREFIX):
            return
        name = secret_ref.removeprefix(REF_PREFIX)
        key_id = name.removesuffix(".secret")
        path = self._path_for(key_id)
        if path.is_file():
            path.unlink()

    @staticmethod
    def mask(secret: str | None) -> str | None:
        if not secret:
            return None
        if len(secret) <= 4:
            return "****"
        return f"****{secret[-4:]}"


def new_key_id() -> str:
    return f"aik_{secrets.token_hex(8)}"
