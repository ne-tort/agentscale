"""Хук записи ручной позиции мастер-прайса.

Главная гарантия фичи: строка формы обязана попасть в OpenSearch. Если хук
перестанет вызываться (переименовали таблицу, убрали вызов из сервиса), форма
продолжит «успешно сохранять», а товара в прайсе не будет — молчаливый отказ,
который легко не заметить.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.application.modules import equipment_master_price_manual as mp
from prodavan.application.modules.module_action_executor import ModuleActionExecutor

_ORIGINAL_INDEX = mp.index_manual_row


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Executor(ModuleActionExecutor):
    """Executor с подменённым чтением строк (без БД)."""

    def __init__(self, rows: list[dict]) -> None:  # noqa: D107
        super().__init__(session=_FakeSession())  # type: ignore[arg-type]
        self._rows = rows
        self.default_seller_calls = 0

    async def _list_module_rows(self, **_kwargs):  # noqa: ANN003, ANN201
        return self._rows

    async def _default_master_price_seller(self, _cabinet_id: str) -> str:  # noqa: ANN201
        self.default_seller_calls += 1
        return "ИТ Взлёт"


@pytest.fixture()
def indexed(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Подменяем фактическую запись в OS: тест проверяет вызов, не OpenSearch."""
    calls: list[dict] = []

    async def fake_index(_session, *, cabinet_id, body, company_id=None, project_id=None):  # noqa: ANN001
        calls.append(
            {
                "cabinet_id": cabinet_id,
                "body": body,
                "company_id": company_id,
                "project_id": project_id,
            }
        )
        return True

    monkeypatch.setattr(mp, "index_manual_row", fake_index)
    return calls


@pytest.fixture()
def saved_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Подменяем запись строки в БД (ModuleInstanceService)."""
    saved: list[dict] = []

    class _FakeInstSvc:
        def __init__(self, _session) -> None:  # noqa: ANN001
            pass

        async def ensure_cabinet_instance(self, **_kw):  # noqa: ANN003, ANN201
            return SimpleNamespace(id="inst")

        async def upsert_data_row(self, **kw):  # noqa: ANN003, ANN201
            saved.append(kw)
            return {"row_id": kw.get("row_id"), "body": kw.get("body")}

    monkeypatch.setattr(
        "prodavan.application.modules.module_instance_service.ModuleInstanceService",
        _FakeInstSvc,
    )
    return saved


@pytest.mark.asyncio
async def test_other_tables_are_ignored(indexed: list[dict]) -> None:
    ex = _Executor([{"row_id": "r1", "body": {"title": "T", "supplier": "S"}}])
    await ex.maybe_sync_master_price_manual(
        cabinet_id="cab",
        module_id="mod_equipment",
        table_slug="request_lines",
        row_id="r1",
        principal=None,  # type: ignore[arg-type]
        employee=None,
    )
    assert indexed == []


@pytest.mark.asyncio
async def test_manual_row_is_indexed(indexed: list[dict]) -> None:
    body = {"supplier": "ИТ Взлёт", "title": "Мышь", "part_number": "PN1", "price": 100}
    ex = _Executor([{"row_id": "r1", "body": body}])
    await ex.maybe_sync_master_price_manual(
        cabinet_id="cab",
        module_id="mod_equipment",
        table_slug=mp.MANUAL_TABLE,
        row_id="r1",
        principal=None,  # type: ignore[arg-type]
        employee=None,
    )
    assert len(indexed) == 1
    assert indexed[0]["cabinet_id"] == "cab"
    assert indexed[0]["body"]["part_number"] == "PN1"
    # продавец уже был — дефолт не подставлялся
    assert ex.default_seller_calls == 0


@pytest.mark.asyncio
async def test_missing_seller_gets_default(
    indexed: list[dict], saved_rows: list[dict]
) -> None:
    """Форма сохраняет поля по одному: первым может уйти «Наименование».

    Без подстановки продавца строка не прошла бы `terms.supplier` и выпала бы
    из прайса, хотя пользователь её «успешно сохранил».
    """
    ex = _Executor([{"row_id": "r1", "body": {"title": "Мышь"}}])
    await ex.maybe_sync_master_price_manual(
        cabinet_id="cab",
        module_id="mod_equipment",
        table_slug=mp.MANUAL_TABLE,
        row_id="r1",
        principal=None,  # type: ignore[arg-type]
        employee=None,
    )
    assert ex.default_seller_calls == 1
    # дефолт записан в строку
    assert saved_rows and saved_rows[0]["body"]["supplier"] == "ИТ Взлёт"
    assert len(indexed) == 1


@pytest.mark.asyncio
async def test_missing_row_is_noop(indexed: list[dict]) -> None:
    ex = _Executor([])
    await ex.maybe_sync_master_price_manual(
        cabinet_id="cab",
        module_id="mod_equipment",
        table_slug=mp.MANUAL_TABLE,
        row_id="gone",
        principal=None,  # type: ignore[arg-type]
        employee=None,
    )
    assert indexed == []


@pytest.mark.asyncio
async def test_incomplete_row_not_indexed() -> None:
    """Пустой документ засорил бы прайс строкой без названия/продавца."""
    ok = await _ORIGINAL_INDEX(
        _FakeSession(), cabinet_id="cab", body={"title": ""}  # type: ignore[arg-type]
    )
    assert ok is False


@pytest.mark.asyncio
async def test_row_without_supplier_not_indexed() -> None:
    ok = await _ORIGINAL_INDEX(
        _FakeSession(), cabinet_id="cab", body={"title": "Мышь"}  # type: ignore[arg-type]
    )
    assert ok is False


@pytest.mark.asyncio
async def test_empty_cabinet_not_indexed() -> None:
    ok = await _ORIGINAL_INDEX(
        _FakeSession(), cabinet_id="", body={"title": "T", "supplier": "S"}  # type: ignore[arg-type]
    )
    assert ok is False
