"""Employee equipment-search facade contracts."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from prodavan.application.modules.equipment_search import EquipmentSearchService


@dataclass
class _Hit:
    source: dict
    doc_id: str
    sort: list[str]


@dataclass
class _Result:
    hits: list[_Hit]
    total: int
    next_search_after: list[str] | None = None


class _Search:
    def __init__(self, docs_by_index: dict[str, list[dict]]) -> None:
        self.docs_by_index = docs_by_index
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        docs = self.docs_by_index[kwargs["index"]]
        after = kwargs.get("search_after")
        if after:
            docs = [doc for doc in docs if doc["src_hash"] > after[0]]
        size = kwargs["size"]
        page = docs[:size]
        hits = [_Hit(doc, doc["src_hash"], [doc["src_hash"]]) for doc in page]
        return _Result(
            hits=hits,
            total=len(self.docs_by_index[kwargs["index"]]),
            next_search_after=hits[-1].sort if hits else None,
        )


@pytest.mark.asyncio
async def test_list_page_applies_global_offset_after_catalog_merge(monkeypatch) -> None:
    docs = [
        {"src_hash": f"{number:04d}", "title": f"item-{number}"}
        for number in range(600)
    ]
    search = _Search(
        {
            "equipment__catalog_a": docs[::2],
            "equipment__catalog_b": docs[1::2],
        }
    )
    monkeypatch.setattr(
        "prodavan.core.infra.opensearch_manager.get_search_index_service",
        lambda: search,
    )
    svc = EquipmentSearchService(object())
    monkeypatch.setattr(
        svc, "_ready_indexes", lambda cabinet_id: _ready_indexes(),
    )

    async def _ready_indexes():
        return ["equipment__catalog_a", "equipment__catalog_b"]

    first = await svc.list_page(cabinet_id="cabinet-1", page=1, page_size=2)
    second = await svc.list_page(cabinet_id="cabinet-1", page=2, page_size=2)
    deep = await svc.list_page(cabinet_id="cabinet-1", page=151, page_size=2)

    assert [row["src_hash"] for row in first["items"]] == ["0000", "0001"]
    assert [row["src_hash"] for row in second["items"]] == ["0002", "0003"]
    assert [row["src_hash"] for row in deep["items"]] == ["0300", "0301"]
    assert second["total"] == 600
    assert all(call["from_"] == 0 for call in search.calls)
    assert any(call.get("search_after") for call in search.calls)
