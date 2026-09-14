"""Secret store protocol + backends (file:// and vault://) — L03."""

from __future__ import annotations

from typing import Protocol

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.cabinet_secret_store import (
    FILE_PREFIX as CABINET_FILE_PREFIX,
)
from prodavan.infrastructure.secrets.cabinet_secret_store import (
    VAULT_PREFIX as CABINET_VAULT_PREFIX,
)
from prodavan.infrastructure.secrets.cabinet_secret_store import (
    get_cabinet_secret_store,
)
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from prodavan.infrastructure.secrets.owner_module_secret_store import (
    FILE_PREFIX as OWNER_FILE_PREFIX,
)
from prodavan.infrastructure.secrets.owner_module_secret_store import (
    VAULT_PREFIX as OWNER_VAULT_PREFIX,
)
from prodavan.infrastructure.secrets.owner_module_secret_store import (
    get_owner_module_secret_store,
)
from prodavan.infrastructure.secrets.vault_store import VaultSecretStore


class SecretStore(Protocol):
    def put(self, key_id: str, secret: str) -> str: ...

    def get(self, secret_ref: str) -> str: ...

    def delete(self, secret_ref: str) -> None: ...


class RoutingSecretStore:
    """Route get/delete by secret_ref scheme; put uses configured default backend."""

    def __init__(
        self,
        *,
        file_store: FileSecretStore | None = None,
        vault_store: VaultSecretStore | None = None,
    ) -> None:
        self._file = file_store or FileSecretStore()
        self._vault = vault_store
        if self._vault is None and settings.vault_addr:
            self._vault = VaultSecretStore()

    def put(self, key_id: str, secret: str) -> str:
        if self._vault is not None and settings.vault_addr:
            return self._vault.put(key_id, secret)
        return self._file.put(key_id, secret)

    def get(self, secret_ref: str) -> str:
        if secret_ref.startswith(CABINET_VAULT_PREFIX) or secret_ref.startswith(CABINET_FILE_PREFIX):
            return get_cabinet_secret_store().get(secret_ref)
        if secret_ref.startswith(OWNER_VAULT_PREFIX) or secret_ref.startswith(OWNER_FILE_PREFIX):
            return get_owner_module_secret_store().get(secret_ref)
        if secret_ref.startswith("vault://"):
            if self._vault is None:
                raise AppError(
                    code="SECRET_BACKEND_UNSUPPORTED",
                    title="Secret backend unsupported",
                    status=500,
                    detail="vault:// ref but VAULT_ADDR not configured",
                )
            return self._vault.get(secret_ref)
        return self._file.get(secret_ref)

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith(CABINET_VAULT_PREFIX) or secret_ref.startswith(CABINET_FILE_PREFIX):
            get_cabinet_secret_store().delete(secret_ref)
            return
        if secret_ref.startswith(OWNER_VAULT_PREFIX) or secret_ref.startswith(OWNER_FILE_PREFIX):
            get_owner_module_secret_store().delete(secret_ref)
            return
        if secret_ref.startswith("vault://"):
            if self._vault is not None:
                self._vault.delete(secret_ref)
            return
        self._file.delete(secret_ref)


def get_secret_store() -> RoutingSecretStore:
    return RoutingSecretStore()
