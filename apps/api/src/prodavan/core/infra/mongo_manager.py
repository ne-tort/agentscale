"""MongoManager — Motor client + LifespanResource (Document Store)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.application.document_store.adapters.memory_store import InMemoryDocumentStore
from prodavan.application.document_store.adapters.mongo_store import MongoDocumentStore
from prodavan.application.document_store.ports.document_store import DocumentStorePort
from prodavan.application.document_store.service import DocumentStoreService
from prodavan.core.infra.startup_ping import ping_with_retry, resolve_ping_retry
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: MongoManager | None = None


def get_mongo_manager() -> MongoManager | None:
    return _manager


def set_mongo_manager(manager: MongoManager | None) -> None:
    global _manager
    _manager = manager


def get_document_store_service() -> DocumentStoreService:
    mgr = get_mongo_manager()
    if mgr is None:
        raise RuntimeError("MongoManager is not started (lifespan)")
    return mgr.service


class MongoManager(LifespanResource):
    """Connect to Mongo when enabled; otherwise in-memory adapter for local/tests."""

    def __init__(
        self,
        *,
        url: str | None = None,
        database: str = "prodavan",
        enabled: bool = False,
        required: bool = False,
        ping_attempts: int | None = None,
        ping_delay_sec: float | None = None,
    ) -> None:
        self._url = (url or "").strip() or None
        self._database = (database or "prodavan").strip() or "prodavan"
        self._enabled_flag = bool(enabled) and self._url is not None
        self._required = required
        self._ping_attempts, self._ping_delay_sec = resolve_ping_retry(
            ping_attempts, ping_delay_sec
        )
        self._client: Any = None
        self._db: Any = None
        self._store: DocumentStorePort = InMemoryDocumentStore()
        self._service = DocumentStoreService(self._store)

    @property
    def name(self) -> str:
        return "mongodb"

    @property
    def enabled(self) -> bool:
        return self._enabled_flag

    @property
    def service(self) -> DocumentStoreService:
        return self._service

    @property
    def store(self) -> DocumentStorePort:
        return self._store

    async def startup(self) -> None:
        set_mongo_manager(self)
        if not self._enabled_flag:
            logger.info("mongodb: disabled (MONGODB_URL empty or MONGODB_ENABLED=false)")
            self._store = InMemoryDocumentStore()
            self._service = DocumentStoreService(self._store)
            return
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._client = AsyncIOMotorClient(self._url, serverSelectionTimeoutMS=5000)
            self._db = self._client[self._database]
            ok = await ping_with_retry(
                "mongodb",
                lambda: self._db.command("ping"),
                attempts=self._ping_attempts,
                delay_sec=self._ping_delay_sec,
            )
            if not ok:
                raise RuntimeError("mongodb ping failed")
            self._store = MongoDocumentStore(db=self._db)
            self._service = DocumentStoreService(self._store)
            logger.info("mongodb: connected db=%s", self._database)
        except Exception:
            logger.exception("mongodb: ping failed on startup")
            await self._close_client()
            if self._required:
                if get_mongo_manager() is self:
                    set_mongo_manager(None)
                raise
            self._store = InMemoryDocumentStore()
            self._service = DocumentStoreService(self._store)
            logger.warning("mongodb: falling back to in-memory store")

    async def shutdown(self) -> None:
        await self._close_client()
        if get_mongo_manager() is self:
            set_mongo_manager(None)

    async def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.exception("mongodb: close failed")
            self._client = None
            self._db = None

    async def health(self) -> bool | None:
        if not self._enabled_flag:
            return None
        try:
            return await self._store.ping()
        except Exception:
            return False

    async def ping(self) -> bool:
        result = await self.health()
        return bool(result)
