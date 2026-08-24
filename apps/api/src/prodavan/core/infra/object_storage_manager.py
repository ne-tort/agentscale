"""ObjectStorageManager — MinIO/S3 + local FS facade (C-OBJECT-STORE)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from prodavan.core.infra.object_store_backends import (
    LocalFsObjectStore,
    ObjectStoreBackend,
    S3ObjectStore,
)
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: ObjectStorageManager | None = None

BackendName = Literal["local", "s3"]


def get_object_storage() -> ObjectStorageManager:
    mgr = get_object_storage_optional()
    if mgr is None or mgr._primary is None:
        raise RuntimeError("ObjectStorageManager is not started (lifespan)")
    return mgr


def get_object_storage_optional() -> ObjectStorageManager | None:
    return _manager


def set_object_storage(manager: ObjectStorageManager | None) -> None:
    global _manager
    _manager = manager


def ensure_object_storage(*, storage_root: Path | None = None) -> ObjectStorageManager:
    """Return live manager, or a local fallback (tests/scripts without lifespan)."""
    mgr = get_object_storage_optional()
    if mgr is not None and mgr._primary is not None:
        return mgr
    from prodavan.config.settings import settings

    fallback = ObjectStorageManager(
        backend="local",
        storage_root=storage_root or settings.storage_root,
    )
    fallback._primary = fallback._local
    set_object_storage(fallback)
    logger.warning("object_storage: using local fallback without lifespan startup")
    return fallback


class ObjectStorageManager(LifespanResource):
    """Unified put/get/delete for product blobs.

    - ``local``: keys under ``storage_root`` (transitional; same layout as legacy FS).
    - ``s3``: MinIO/AWS; optional ``mirror_local`` keeps agent/local-ws paths hydrated.
    """

    def __init__(
        self,
        *,
        backend: BackendName = "local",
        storage_root: Path,
        s3_endpoint_url: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        s3_bucket: str = "prodavan",
        s3_region: str = "us-east-1",
        mirror_local: bool = True,
        required: bool = False,
    ) -> None:
        self._backend_name = backend
        self._storage_root = Path(storage_root)
        self._s3_endpoint_url = s3_endpoint_url
        self._s3_access_key = s3_access_key
        self._s3_secret_key = s3_secret_key
        self._s3_bucket = s3_bucket
        self._s3_region = s3_region
        self._mirror_local = mirror_local
        self._required = required
        self._primary: ObjectStoreBackend | None = None
        self._local = LocalFsObjectStore(self._storage_root)

    @property
    def name(self) -> str:
        return "object_storage"

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def put_bytes_sync(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        assert self._primary is not None
        self._primary.put_bytes(key, data, content_type=content_type)
        if self._backend_name == "s3" and self._mirror_local:
            self._local.put_bytes(key, data, content_type=content_type)

    def get_bytes_sync(self, key: str) -> bytes:
        assert self._primary is not None
        try:
            return self._primary.get_bytes(key)
        except FileNotFoundError:
            if self._backend_name == "s3" and self._mirror_local:
                return self._local.get_bytes(key)
            raise

    def delete_sync(self, key: str) -> bool:
        assert self._primary is not None
        deleted = self._primary.delete(key)
        if self._backend_name == "s3" and self._mirror_local:
            deleted = self._local.delete(key) or deleted
        return deleted

    def delete_prefix_sync(self, prefix: str) -> int:
        assert self._primary is not None
        deleted = self._primary.delete_prefix(prefix)
        if self._backend_name == "s3" and self._mirror_local:
            deleted = max(deleted, self._local.delete_prefix(prefix))
        elif self._backend_name == "s3":
            self._local.delete_prefix(prefix)
        return deleted

    def delete_prefix_verified_sync(self, prefix: str) -> dict[str, Any]:
        """delete_prefix then list_prefix(limit=1) — report leftovers for GC/retry."""
        deleted = self.delete_prefix_sync(prefix)
        remaining = self.list_prefix_sync(prefix, limit=1)
        return {
            "ok": len(remaining) == 0,
            "deleted": int(deleted),
            "remaining": len(remaining),
            "prefix": prefix,
        }

    def prefix_size_sync(self, prefix: str) -> int:
        assert self._primary is not None
        # Always measure primary SoT (S3 when enabled), not local mirror.
        return self._primary.prefix_size(prefix)

    def list_prefix_sync(self, prefix: str, *, limit: int = 1000) -> list[str]:
        assert self._primary is not None
        return self._primary.list_prefix(prefix, limit=limit)

    def list_child_prefixes_sync(self, prefix: str, *, limit: int = 1000) -> list[str]:
        assert self._primary is not None
        return self._primary.list_child_prefixes(prefix, limit=limit)

    def exists_sync(self, key: str) -> bool:
        assert self._primary is not None
        if self._primary.exists(key):
            return True
        if self._backend_name == "s3" and self._mirror_local:
            return self._local.exists(key)
        return False

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        await asyncio.to_thread(self.put_bytes_sync, key, data, content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes_sync, key)

    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(self.delete_sync, key)

    async def delete_prefix(self, prefix: str) -> int:
        return await asyncio.to_thread(self.delete_prefix_sync, prefix)

    async def prefix_size(self, prefix: str) -> int:
        return await asyncio.to_thread(self.prefix_size_sync, prefix)

    async def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return await asyncio.to_thread(self.list_prefix_sync, prefix, limit=limit)

    async def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return await asyncio.to_thread(self.list_child_prefixes_sync, prefix, limit=limit)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self.exists_sync, key)

    async def startup(self) -> None:
        set_object_storage(self)
        if self._backend_name == "local":
            self._primary = self._local
            logger.info("object_storage: backend=local root=%s", self._storage_root)
            return

        if not self._s3_endpoint_url or not self._s3_access_key or not self._s3_secret_key:
            msg = "OBJECT_STORE_BACKEND=s3 requires S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY"
            if self._required:
                raise RuntimeError(msg)
            logger.warning("%s — falling back to local", msg)
            self._backend_name = "local"
            self._primary = self._local
            return

        store = S3ObjectStore(
            endpoint_url=self._s3_endpoint_url,
            access_key=self._s3_access_key,
            secret_key=self._s3_secret_key,
            bucket=self._s3_bucket,
            region=self._s3_region,
        )
        try:
            store.ensure_bucket()
            if not store.health():
                raise RuntimeError("s3 health failed after ensure_bucket")
        except Exception:
            logger.exception("object_storage: s3 startup failed")
            if self._required:
                set_object_storage(None)
                raise
            logger.warning("object_storage: falling back to local")
            self._backend_name = "local"
            self._primary = self._local
            return

        self._primary = store
        logger.info(
            "object_storage: backend=s3 endpoint=%s bucket=%s mirror_local=%s",
            self._s3_endpoint_url,
            self._s3_bucket,
            self._mirror_local,
        )

    async def shutdown(self) -> None:
        if self._primary is not None:
            self._primary.close()
            self._primary = None
        if get_object_storage_optional() is self:
            set_object_storage(None)

    async def health(self) -> bool | None:
        if self._primary is None:
            return False
        try:
            return bool(self._primary.health())
        except Exception:
            return False
