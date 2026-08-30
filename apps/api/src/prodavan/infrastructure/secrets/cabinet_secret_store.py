"""Cabinet-scoped module secrets — file:// and vault://cabinet_secrets/."""

from __future__ import annotations

import secrets
from pathlib import Path

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.vault_store import VaultSecretStore

FILE_PREFIX = "file://cabinet_secrets/"
VAULT_PREFIX = "vault://cabinet_secrets/"


def new_secret_id() -> str:
    return f"sec_{secrets.token_hex(8)}"


def secret_ref_prefix(secret_ref: str) -> str:
    if len(secret_ref) <= 28:
        return secret_ref
    return secret_ref[:24] + "…"


class CabinetSecretStore:
    def __init__(self, root: Path | None = None, *, vault_store: VaultSecretStore | None = None) -> None:
        self._root = root or settings.secrets_dir
        self._vault = vault_store
        if self._vault is None and settings.vault_addr:
            base = settings.vault_kv_path_prefix.strip("/")
            prefix = f"{base}/cabinet_secrets" if base else "cabinet_secrets"
            self._vault = VaultSecretStore(path_prefix=prefix, ref_prefix=VAULT_PREFIX)

    def put(self, *, cabinet_id: str, secret: str, secret_id: str | None = None) -> str:
        if not secret or not secret.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="secret required",
            )
        sid = secret_id or new_secret_id()
        key_id = f"{cabinet_id}/{sid}"
        if self._vault is not None and settings.vault_addr:
            return self._vault.put(key_id, secret.strip())
        return self._put_file(cabinet_id=cabinet_id, secret_id=sid, secret=secret.strip())

    def get(self, secret_ref: str) -> str:
        if secret_ref.startswith(VAULT_PREFIX):
            if self._vault is None:
                raise AppError(
                    code="SECRET_BACKEND_UNSUPPORTED",
                    title="Secret backend unsupported",
                    status=500,
                    detail="vault:// cabinet secret but VAULT_ADDR not configured",
                )
            return self._vault.get(secret_ref)
        if secret_ref.startswith(FILE_PREFIX):
            return self._get_file(secret_ref)
        raise AppError(
            code="SECRET_BACKEND_UNSUPPORTED",
            title="Secret backend unsupported",
            status=500,
            detail=f"unsupported cabinet secret_ref: {secret_ref.split(':', 1)[0]}",
        )

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith(VAULT_PREFIX):
            if self._vault is not None:
                self._vault.delete(secret_ref)
            return
        if secret_ref.startswith(FILE_PREFIX):
            self._delete_file(secret_ref)

    def _file_path(self, cabinet_id: str, secret_id: str) -> Path:
        safe_cab = cabinet_id.replace("/", "_").replace("..", "_")
        safe_sec = secret_id.replace("/", "_").replace("..", "_")
        return self._root / "cabinet_secrets" / safe_cab / f"{safe_sec}.secret"

    def _put_file(self, *, cabinet_id: str, secret_id: str, secret: str) -> str:
        path = self._file_path(cabinet_id, secret_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"{FILE_PREFIX}{cabinet_id}/{secret_id}"

    def _parse_file_ref(self, secret_ref: str) -> tuple[str, str]:
        rest = secret_ref.removeprefix(FILE_PREFIX)
        if "/" not in rest:
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="invalid cabinet secret_ref",
            )
        cabinet_id, secret_id = rest.split("/", 1)
        return cabinet_id, secret_id

    def _get_file(self, secret_ref: str) -> str:
        cabinet_id, secret_id = self._parse_file_ref(secret_ref)
        path = self._file_path(cabinet_id, secret_id)
        if not path.is_file():
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="cabinet secret missing on disk",
            )
        return path.read_text(encoding="utf-8").strip()

    def _delete_file(self, secret_ref: str) -> None:
        cabinet_id, secret_id = self._parse_file_ref(secret_ref)
        path = self._file_path(cabinet_id, secret_id)
        if path.is_file():
            path.unlink()


_cabinet_store: CabinetSecretStore | None = None


def get_cabinet_secret_store() -> CabinetSecretStore:
    global _cabinet_store
    if _cabinet_store is None:
        _cabinet_store = CabinetSecretStore()
    return _cabinet_store
