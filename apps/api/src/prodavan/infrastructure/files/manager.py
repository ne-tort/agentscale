"""FileStoreManager — single object-store LifespanResource (S3/local + mirror + presign)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from prodavan.core.lifespan.resource import LifespanResource
from prodavan.infrastructure.files.local_store import LocalFileStore
from prodavan.infrastructure.files.minio_store import MinioFileStore
from prodavan.infrastructure.files.port import FileStorePort, ObjectHead

logger = logging.getLogger(__name__)

_manager: FileStoreManager | None = None

BackendName = Literal["local", "s3"]


def get_file_store() -> FileStoreManager:
    mgr = get_file_store_optional()
    if mgr is None or mgr._primary is None:
        raise RuntimeError("FileStoreManager is not started (lifespan)")
    return mgr


def get_file_store_optional() -> FileStoreManager | None:
    return _manager


def set_file_store(manager: FileStoreManager | None) -> None:
    global _manager
    _manager = manager


def ensure_file_store(*, storage_root: Path | None = None) -> FileStoreManager:
    mgr = get_file_store_optional()
    if mgr is not None and mgr._primary is not None:
        return mgr
    from prodavan.config.settings import settings

    fallback = FileStoreManager(
        backend="local",
        storage_root=storage_root or settings.storage_root,
    )
    fallback._primary = fallback._local
    set_file_store(fallback)
    logger.warning("file_store: using local fallback without lifespan startup")
    return fallback


class FileStoreManager(LifespanResource):
    """Unified blob I/O: content ``blobs/``, workspace ``projects/``, packages ``cabinet_packages/``."""

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
        self._primary: FileStorePort | None = None
        self._local = LocalFileStore(self._storage_root)

    @property
    def name(self) -> str:
        return "file_store"

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def store(self) -> FileStorePort:
        assert self._primary is not None
        return self._primary

    def put_bytes_sync(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        self.store.put_bytes(key, data, content_type=content_type)
        if self._backend_name == "s3" and self._mirror_local:
            self._local.put_bytes(key, data, content_type=content_type)

    def get_bytes_sync(self, key: str) -> bytes:
        try:
            return self.store.get_bytes(key)
        except FileNotFoundError:
            if self._backend_name == "s3" and self._mirror_local:
                return self._local.get_bytes(key)
            raise

    def delete_sync(self, key: str) -> bool:
        deleted = self.store.delete(key)
        if self._backend_name == "s3" and self._mirror_local:
            deleted = self._local.delete(key) or deleted
        return deleted

    def head_sync(self, key: str) -> ObjectHead:
        return self.store.head(key)

    def exists_sync(self, key: str) -> bool:
        if self.store.exists(key):
            return True
        if self._backend_name == "s3" and self._mirror_local:
            return self._local.exists(key)
        return False

    def list_prefix_sync(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return self.store.list_prefix(prefix, limit=limit)

    def list_child_prefixes_sync(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return self.store.list_child_prefixes(prefix, limit=limit)

    def delete_prefix_sync(self, prefix: str) -> int:
        deleted = self.store.delete_prefix(prefix)
        if self._backend_name == "s3" and self._mirror_local:
            deleted = max(deleted, self._local.delete_prefix(prefix))
        elif self._backend_name == "s3":
            self._local.delete_prefix(prefix)
        return deleted

    def delete_prefix_verified_sync(self, prefix: str) -> dict[str, Any]:
        deleted = self.delete_prefix_sync(prefix)
        remaining_keys = self.list_prefix_sync(prefix, limit=5)
        return {
            "ok": len(remaining_keys) == 0,
            "deleted": int(deleted),
            "remaining": len(remaining_keys),
            "remaining_sample": remaining_keys,
            "prefix": prefix,
        }

    def prefix_size_sync(self, prefix: str) -> int:
        return self.store.prefix_size(prefix)

    def presign_get_sync(self, key: str, *, ttl_seconds: int = 900) -> str:
        return self.store.presign_get(key, ttl_seconds=ttl_seconds)

    def presign_put_sync(
        self,
        key: str,
        *,
        ttl_seconds: int = 900,
        content_type: str | None = None,
    ) -> str:
        return self.store.presign_put(key, ttl_seconds=ttl_seconds, content_type=content_type)

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        await asyncio.to_thread(self.put_bytes_sync, key, data, content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes_sync, key)

    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(self.delete_sync, key)

    async def head(self, key: str) -> ObjectHead:
        return await asyncio.to_thread(self.head_sync, key)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self.exists_sync, key)

    async def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return await asyncio.to_thread(self.list_prefix_sync, prefix, limit=limit)

    async def list_child_prefixes(self, prefix: str, *, limit: int = 1000) -> list[str]:
        return await asyncio.to_thread(self.list_child_prefixes_sync, prefix, limit=limit)

    async def delete_prefix(self, prefix: str) -> int:
        return await asyncio.to_thread(self.delete_prefix_sync, prefix)

    async def prefix_size(self, prefix: str) -> int:
        return await asyncio.to_thread(self.prefix_size_sync, prefix)

    async def presign_get(self, key: str, *, ttl_seconds: int = 900) -> str:
        return await asyncio.to_thread(self.presign_get_sync, key, ttl_seconds=ttl_seconds)

    async def presign_put(
        self,
        key: str,
        *,
        ttl_seconds: int = 900,
        content_type: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self.presign_put_sync, key, ttl_seconds=ttl_seconds, content_type=content_type
        )

    async def startup(self) -> None:
        set_file_store(self)
        if self._backend_name == "local":
            self._primary = self._local
            logger.info("file_store: backend=local root=%s", self._storage_root)
            return

        if not self._s3_endpoint_url or not self._s3_access_key or not self._s3_secret_key:
            msg = "OBJECT_STORE_BACKEND=s3 requires S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY"
            if self._required:
                raise RuntimeError(msg)
            logger.warning("%s — falling back to local", msg)
            self._backend_name = "local"
            self._primary = self._local
            return

        store = MinioFileStore(
            endpoint_url=self._s3_endpoint_url,
            access_key=self._s3_access_key,
            secret_key=self._s3_secret_key,
            bucket=self._s3_bucket,
            region=self._s3_region,
        )
        try:
            if not store.health():
                raise RuntimeError("s3 head_bucket failed (bucket must exist; init Job owns create)")
        except Exception:
            logger.exception("file_store: s3 startup failed")
            if self._required:
                set_file_store(None)
                raise
            logger.warning("file_store: falling back to local")
            self._backend_name = "local"
            self._primary = self._local
            return

        self._primary = store
        logger.info(
            "file_store: backend=s3 endpoint=%s bucket=%s mirror_local=%s",
            self._s3_endpoint_url,
            self._s3_bucket,
            self._mirror_local,
        )

    async def shutdown(self) -> None:
        if self._primary is not None:
            self._primary.close()
            self._primary = None
        if get_file_store_optional() is self:
            set_file_store(None)

    async def health(self) -> bool | None:
        if self._primary is None:
            return False
        try:
            return bool(self._primary.health())
        except Exception:
            return False
