"""Owner-scoped module secrets — platform / company (and shared get routing).

Cabinet secrets stay in ``cabinet_secret_store`` (legacy ``cabinet_secrets/`` paths).
Platform/company seed rows use ``module_secrets/{owner_kind}/{owner_id}/…``.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.vault_store import VaultSecretStore

FILE_PREFIX = "file://module_secrets/"
VAULT_PREFIX = "vault://module_secrets/"

_ALLOWED_OWNER_KINDS = frozenset({"platform", "company"})


def new_secret_id() -> str:
    return f"sec_{secrets.token_hex(8)}"


def secret_ref_prefix(secret_ref: str) -> str:
    if len(secret_ref) <= 28:
        return secret_ref
    return secret_ref[:24] + "…"


def is_owner_module_secret_ref(secret_ref: str) -> bool:
    return secret_ref.startswith(FILE_PREFIX) or secret_ref.startswith(VAULT_PREFIX)


def parse_owner_module_secret_ref(secret_ref: str) -> tuple[str, str, str] | None:
    """Return (owner_kind, owner_id, secret_id) or None if not an owner module ref."""
    for prefix in (FILE_PREFIX, VAULT_PREFIX):
        if not secret_ref.startswith(prefix):
            continue
        rest = secret_ref.removeprefix(prefix)
        parts = rest.split("/")
        if len(parts) != 3:
            return None
        owner_kind, owner_id, secret_id = parts
        if owner_kind not in _ALLOWED_OWNER_KINDS or not owner_id or not secret_id:
            return None
        return owner_kind, owner_id, secret_id
    return None


def assert_module_secret_ref_scope(
    secret_ref: str,
    *,
    cabinet_id: str | None = None,
    owner_kind: str | None = None,
    owner_id: str | None = None,
) -> None:
    """Validate secret_ref belongs to the active runtime scope (cabinet or owner instance)."""
    if secret_ref.startswith(("file://cabinet_secrets/", "vault://cabinet_secrets/")):
        if not cabinet_id:
            raise AppError(
                code="SECRET_SCOPE_VIOLATION",
                title="Secret scope violation",
                status=403,
                detail="cabinet secret_ref requires cabinet scope",
            )
        from prodavan.infrastructure.secrets.cabinet_secret_store import assert_cabinet_secret_scope

        assert_cabinet_secret_scope(secret_ref, cabinet_id)
        return
    if secret_ref.startswith((FILE_PREFIX, VAULT_PREFIX)):
        if not owner_kind or not owner_id:
            raise AppError(
                code="SECRET_SCOPE_VIOLATION",
                title="Secret scope violation",
                status=403,
                detail="module secret_ref requires owner scope",
            )
        assert_owner_module_secret_scope(
            secret_ref, owner_kind=owner_kind, owner_id=owner_id
        )
        return
    raise AppError(
        code="SECRET_SCOPE_VIOLATION",
        title="Secret scope violation",
        status=403,
        detail="unsupported secret_ref prefix for module row",
    )


def assert_owner_module_secret_scope(
    secret_ref: str, *, owner_kind: str, owner_id: str
) -> None:
    parsed = parse_owner_module_secret_ref(secret_ref)
    if parsed is None:
        raise AppError(
            code="SECRET_SCOPE_VIOLATION",
            title="Secret scope violation",
            status=403,
            detail="secret_ref must use module_secrets/{owner_kind}/{owner_id}/…",
        )
    kind, oid, _ = parsed
    if kind != owner_kind or oid != owner_id:
        raise AppError(
            code="SECRET_SCOPE_VIOLATION",
            title="Secret scope violation",
            status=403,
            detail=f"secret_ref belongs to {kind}/{oid}, not {owner_kind}/{owner_id}",
        )


class OwnerModuleSecretStore:
    def __init__(self, root: Path | None = None, *, vault_store: VaultSecretStore | None = None) -> None:
        self._root = root or settings.secrets_dir
        self._vault = vault_store
        if self._vault is None and settings.vault_addr:
            base = settings.vault_kv_path_prefix.strip("/")
            prefix = f"{base}/module_secrets" if base else "module_secrets"
            self._vault = VaultSecretStore(path_prefix=prefix, ref_prefix=VAULT_PREFIX)

    def put(self, *, owner_kind: str, owner_id: str, secret: str, secret_id: str | None = None) -> str:
        if owner_kind not in _ALLOWED_OWNER_KINDS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unsupported owner_kind for module secret: {owner_kind}",
            )
        if not owner_id or not str(owner_id).strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="owner_id required",
            )
        if not secret or not secret.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="secret required",
            )
        sid = secret_id or new_secret_id()
        key_id = f"{owner_kind}/{owner_id}/{sid}"
        if self._vault is not None and settings.vault_addr:
            return self._vault.put(key_id, secret.strip())
        return self._put_file(owner_kind=owner_kind, owner_id=owner_id, secret_id=sid, secret=secret.strip())

    def get(self, secret_ref: str) -> str:
        if secret_ref.startswith(VAULT_PREFIX):
            if self._vault is None:
                raise AppError(
                    code="SECRET_BACKEND_UNSUPPORTED",
                    title="Secret backend unsupported",
                    status=500,
                    detail="vault:// module secret but VAULT_ADDR not configured",
                )
            return self._vault.get(secret_ref)
        if secret_ref.startswith(FILE_PREFIX):
            return self._get_file(secret_ref)
        raise AppError(
            code="SECRET_BACKEND_UNSUPPORTED",
            title="Secret backend unsupported",
            status=500,
            detail=f"unsupported module secret_ref: {secret_ref.split(':', 1)[0]}",
        )

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith(VAULT_PREFIX):
            if self._vault is not None:
                self._vault.delete(secret_ref)
            return
        if secret_ref.startswith(FILE_PREFIX):
            self._delete_file(secret_ref)

    def _file_path(self, owner_kind: str, owner_id: str, secret_id: str) -> Path:
        safe_kind = owner_kind.replace("/", "_").replace("..", "_")
        safe_owner = owner_id.replace("/", "_").replace("..", "_")
        safe_sec = secret_id.replace("/", "_").replace("..", "_")
        return self._root / "module_secrets" / safe_kind / safe_owner / f"{safe_sec}.secret"

    def _put_file(self, *, owner_kind: str, owner_id: str, secret_id: str, secret: str) -> str:
        path = self._file_path(owner_kind, owner_id, secret_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"{FILE_PREFIX}{owner_kind}/{owner_id}/{secret_id}"

    def _parse_file_ref(self, secret_ref: str) -> tuple[str, str, str]:
        parsed = parse_owner_module_secret_ref(secret_ref)
        if parsed is None:
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="invalid module secret_ref",
            )
        return parsed

    def _get_file(self, secret_ref: str) -> str:
        owner_kind, owner_id, secret_id = self._parse_file_ref(secret_ref)
        path = self._file_path(owner_kind, owner_id, secret_id)
        if not path.is_file():
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="module secret missing on disk",
            )
        return path.read_text(encoding="utf-8").strip()

    def _delete_file(self, secret_ref: str) -> None:
        owner_kind, owner_id, secret_id = self._parse_file_ref(secret_ref)
        path = self._file_path(owner_kind, owner_id, secret_id)
        if path.is_file():
            path.unlink()


_owner_store: OwnerModuleSecretStore | None = None


def get_owner_module_secret_store() -> OwnerModuleSecretStore:
    global _owner_store
    if _owner_store is None:
        _owner_store = OwnerModuleSecretStore()
    return _owner_store
