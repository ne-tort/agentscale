"""Lifespan-lazy production Mongo DocumentStore access (never in-memory fallback)."""
from __future__ import annotations

from typing import Any


class ProductionDocumentStoreService:
    def _service(self) -> Any:
        from prodavan.application.document_store.adapters.mongo_store import MongoDocumentStore
        from prodavan.core.infra.mongo_manager import get_mongo_manager

        manager = get_mongo_manager()
        if manager is None or not manager.enabled or not isinstance(manager.store, MongoDocumentStore):
            raise RuntimeError("production MongoDocumentStore is not started")
        return manager.service

    async def find(self, **kwargs: Any) -> Any:
        return await self._service().find(**kwargs)


__all__ = ["ProductionDocumentStoreService"]
