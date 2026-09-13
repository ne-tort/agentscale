"""OpenSearchManager — httpx client + LifespanResource (Search Index)."""

from __future__ import annotations

import logging

from prodavan.application.search_index.adapters.memory_store import InMemorySearchIndexStore
from prodavan.application.search_index.adapters.opensearch_store import OpenSearchStore
from prodavan.application.search_index.ports.search_index import SearchIndexPort
from prodavan.application.search_index.service import SearchIndexService
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: OpenSearchManager | None = None


def get_opensearch_manager() -> OpenSearchManager | None:
    return _manager


def set_opensearch_manager(manager: OpenSearchManager | None) -> None:
    global _manager
    _manager = manager


def get_search_index_service() -> SearchIndexService:
    mgr = get_opensearch_manager()
    if mgr is None:
        raise RuntimeError("OpenSearchManager is not started (lifespan)")
    return mgr.service


class OpenSearchManager(LifespanResource):
    """Connect to OpenSearch when enabled; otherwise in-memory adapter for local/tests."""

    def __init__(
        self,
        *,
        url: str | None = None,
        enabled: bool = False,
        required: bool = False,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._url = (url or "").strip() or None
        self._enabled_flag = bool(enabled) and self._url is not None
        self._required = required
        self._username = (username or "").strip() or None
        self._password = (password or "").strip() or None
        self._store: SearchIndexPort = InMemorySearchIndexStore()
        self._os_store: OpenSearchStore | None = None
        self._service = SearchIndexService(self._store)

    @property
    def name(self) -> str:
        return "opensearch"

    @property
    def enabled(self) -> bool:
        return self._enabled_flag

    @property
    def service(self) -> SearchIndexService:
        return self._service

    @property
    def store(self) -> SearchIndexPort:
        return self._store

    async def startup(self) -> None:
        set_opensearch_manager(self)
        if not self._enabled_flag:
            logger.info("opensearch: disabled (OPENSEARCH_URL empty or OPENSEARCH_ENABLED=false)")
            self._store = InMemorySearchIndexStore()
            self._service = SearchIndexService(self._store)
            return
        try:
            store = OpenSearchStore(
                base_url=self._url or "",
                username=self._username,
                password=self._password,
            )
            ok = await store.ping()
            if not ok:
                raise RuntimeError("opensearch ping returned false")
            self._os_store = store
            self._store = store
            self._service = SearchIndexService(self._store)
            logger.info("opensearch: connected url=%s", self._url)
        except Exception:
            logger.exception("opensearch: ping failed on startup")
            if self._os_store is not None:
                await self._os_store.aclose()
                self._os_store = None
            if self._required:
                if get_opensearch_manager() is self:
                    set_opensearch_manager(None)
                raise
            self._store = InMemorySearchIndexStore()
            self._service = SearchIndexService(self._store)
            logger.warning("opensearch: falling back to in-memory store")

    async def shutdown(self) -> None:
        if self._os_store is not None:
            await self._os_store.aclose()
            self._os_store = None
        if get_opensearch_manager() is self:
            set_opensearch_manager(None)

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
