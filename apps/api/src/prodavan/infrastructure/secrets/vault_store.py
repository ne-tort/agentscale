"""HashiCorp Vault KV v2 backend — same secret_ref contract as file store (L03)."""

from __future__ import annotations

import httpx

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

REF_PREFIX = "vault://ai_keys/"


class VaultSecretStore:
    """Stores secret under KV v2 path; DB holds vault://…/{key_id}."""

    def __init__(
        self,
        *,
        addr: str | None = None,
        token: str | None = None,
        mount: str | None = None,
        path_prefix: str | None = None,
        ref_prefix: str | None = None,
    ) -> None:
        self._addr = (addr or settings.vault_addr or "").rstrip("/")
        self._token = token if token is not None else settings.vault_token
        self._mount = (mount or settings.vault_kv_mount).strip("/")
        self._path_prefix = (path_prefix or settings.vault_kv_path_prefix).strip("/")
        self._ref_prefix = ref_prefix or REF_PREFIX

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise AppError(
                code="SECRET_BACKEND_UNSUPPORTED",
                title="Secret backend unsupported",
                status=500,
                detail="VAULT_TOKEN required for vault:// secrets",
            )
        return {"X-Vault-Token": self._token}

    def _data_url(self, key_id: str) -> str:
        safe = key_id.replace("/", "_").replace("..", "_")
        return f"{self._addr}/v1/{self._mount}/data/{self._path_prefix}/{safe}"

    def put(self, key_id: str, secret: str) -> str:
        if not self._addr:
            raise AppError(
                code="SECRET_BACKEND_UNSUPPORTED",
                title="Secret backend unsupported",
                status=500,
                detail="VAULT_ADDR not configured",
            )
        if not secret or not secret.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="secret required",
            )
        url = self._data_url(key_id)
        try:
            res = httpx.post(
                url,
                headers=self._headers(),
                json={"data": {"value": secret.strip()}},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                code="SECRET_BACKEND_ERROR",
                title="Secret backend error",
                status=502,
                detail=f"vault put failed: {exc}",
            ) from exc
        if res.status_code >= 300:
            raise AppError(
                code="SECRET_BACKEND_ERROR",
                title="Secret backend error",
                status=502,
                detail=f"vault put HTTP {res.status_code}",
            )
        return f"{self._ref_prefix}{key_id}"

    def get(self, secret_ref: str) -> str:
        if not secret_ref.startswith(self._ref_prefix):
            raise AppError(
                code="SECRET_BACKEND_UNSUPPORTED",
                title="Secret backend unsupported",
                status=500,
                detail=f"unsupported secret_ref scheme: {secret_ref.split(':', 1)[0]}",
            )
        if not self._addr:
            raise AppError(
                code="SECRET_BACKEND_UNSUPPORTED",
                title="Secret backend unsupported",
                status=500,
                detail="VAULT_ADDR not configured",
            )
        key_id = secret_ref.removeprefix(self._ref_prefix)
        url = self._data_url(key_id)
        try:
            res = httpx.get(url, headers=self._headers(), timeout=15.0)
        except httpx.HTTPError as exc:
            raise AppError(
                code="SECRET_BACKEND_ERROR",
                title="Secret backend error",
                status=502,
                detail=f"vault get failed: {exc}",
            ) from exc
        if res.status_code == 404:
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="vault secret missing",
            )
        if res.status_code >= 300:
            raise AppError(
                code="SECRET_BACKEND_ERROR",
                title="Secret backend error",
                status=502,
                detail=f"vault get HTTP {res.status_code}",
            )
        payload = res.json()
        data = (payload.get("data") or {}).get("data") or {}
        value = data.get("value")
        if not isinstance(value, str) or not value:
            raise AppError(
                code="SECRET_NOT_FOUND",
                title="Secret not found",
                status=500,
                detail="vault secret empty",
            )
        return value

    def delete(self, secret_ref: str) -> None:
        if not secret_ref.startswith(self._ref_prefix) or not self._addr:
            return
        key_id = secret_ref.removeprefix(self._ref_prefix)
        safe = key_id.replace("/", "_").replace("..", "_")
        # KV v2 metadata delete
        url = f"{self._addr}/v1/{self._mount}/metadata/{self._path_prefix}/{safe}"
        try:
            httpx.delete(url, headers=self._headers(), timeout=15.0)
        except httpx.HTTPError:
            pass
