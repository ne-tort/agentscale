"""``ProductionDocumentStoreService``: только реальный запущенный store.

Адаптер не покрывался тестами. Его единственная задача — не дать платформе
молча уйти в in-memory документ-store: при отсутствии/выключенном менеджере
или при store другого типа он обязан отказать ДО вызова ``find``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.document_store.adapters.mongo_store import MongoDocumentStore
from prodavan.infrastructure.plugins.capabilities.mongo import ProductionDocumentStoreService


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def find(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"items": []}


class _Manager:
    def __init__(self, *, enabled: bool, store: Any, service: Any) -> None:
        self.enabled = enabled
        self.store = store
        self.service = service


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: Any) -> None:
    from prodavan.core.infra import mongo_manager

    monkeypatch.setattr(mongo_manager, "get_mongo_manager", lambda: manager)


async def test_forwards_to_the_real_service(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_manager(monkeypatch, _Manager(
        enabled=True, store=object.__new__(MongoDocumentStore), service=recorder,
    ))
    result = await ProductionDocumentStoreService().find(namespace="plugins", collection="docs")
    assert result == {"items": []}
    assert recorder.calls == [{"namespace": "plugins", "collection": "docs"}]


@pytest.mark.parametrize(
    "manager",
    [
        None,
        SimpleNamespace(enabled=False, store=None, service=_Recorder()),
        SimpleNamespace(enabled=True, store=object(), service=_Recorder()),
    ],
)
async def test_absent_disabled_or_foreign_store_fails_closed(
    monkeypatch: pytest.MonkeyPatch, manager: Any,
) -> None:
    # Ни один из этих случаев не должен тихо отдать in-memory результат.
    _patch_manager(monkeypatch, manager)
    with pytest.raises(RuntimeError, match="not started"):
        await ProductionDocumentStoreService().find(namespace="plugins", collection="docs")


async def test_foreign_store_service_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    _patch_manager(monkeypatch, SimpleNamespace(enabled=True, store=object(), service=recorder))
    with pytest.raises(RuntimeError):
        await ProductionDocumentStoreService().find(namespace="plugins", collection="docs")
    assert recorder.calls == []
