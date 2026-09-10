"""Unit tests for ModuleActionExecutor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


class _FakeModules:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create_data_row(self, **kwargs):  # noqa: ANN003
        self.created.append(kwargs)
        return {"row_id": "row_test", "body": kwargs.get("body") or {}}


class _FakeSession:
    def __init__(self, actions: list[dict] | None) -> None:
        self._actions = actions

    async def execute(self, _query):  # noqa: ANN001
        class _Result:
            def __init__(self, body):  # noqa: ANN001
                self._body = body

            def scalar_one_or_none(self):
                return self._body

        return _Result(self._actions)


@pytest.mark.asyncio
async def test_invoke_data_create_row(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_modules = _FakeModules()
    executor = ModuleActionExecutor(session=_FakeSession([  # type: ignore[arg-type]
        {
            "id": "add_note",
            "kind": "data.create_row",
            "params": {"table_slug": "notes", "defaults": {"title": "New"}},
        }
    ]))
    monkeypatch.setattr(executor, "_modules", fake_modules)

    result = await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_1",
        action_id="add_note",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
    )
    assert result["kind"] == "data.create_row"
    assert fake_modules.created[0]["table_slug"] == "notes"


@pytest.mark.asyncio
async def test_unknown_action_kind() -> None:
    executor = ModuleActionExecutor(session=_FakeSession([  # type: ignore[arg-type]
        {"id": "x", "kind": "http.webhook"}
    ]))
    with pytest.raises(AppError) as exc:
        await executor.invoke(
            cabinet_id="cab_1",
            module_id="mod_1",
            action_id="x",
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
        )
    assert exc.value.status == 501


@pytest.mark.asyncio
async def test_list_remote_databases_uses_project_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ModuleActionExecutor(
        session=_FakeSession(  # type: ignore[arg-type]
            [
                {
                    "id": "list_catalog_remote_databases",
                    "kind": "content.list_remote_sql_databases",
                    "enabled": True,
                    "params": {"table_slug": "catalogs", "dsn_column": "remote_dsn"},
                }
            ]
        )
    )

    seen: dict[str, object] = {}

    async def _assert_project(**kwargs):  # noqa: ANN003
        seen["assert"] = kwargs

    async def _list_dbs(**kwargs):  # noqa: ANN003
        seen["list"] = kwargs
        return {"kind": "content.list_remote_sql_databases", "databases": []}

    monkeypatch.setattr(executor, "_assert_project_in_cabinet", _assert_project)
    monkeypatch.setattr(executor, "_list_remote_sql_databases", _list_dbs)

    result = await executor.invoke(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        action_id="list_catalog_remote_databases",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
        row_id="row_1",
        project_id="proj_1",
    )
    assert result["kind"] == "content.list_remote_sql_databases"
    assert seen["assert"]["project_id"] == "proj_1"
    assert seen["assert"]["cabinet_id"] == "cab_1"
    assert seen["list"]["project_id"] == "proj_1"
    assert seen["list"]["row_id"] == "row_1"


@pytest.mark.asyncio
async def test_assert_project_rejects_foreign_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ModuleActionExecutor(session=_FakeSession([]))  # type: ignore[arg-type]

    class _Policy:
        def __init__(self, _session):  # noqa: ANN001
            pass

        async def require_access(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(cabinet_id="cab_other", id=kwargs["project_id"])

    monkeypatch.setattr(
        "prodavan.application.project_service.access.ProjectAccessPolicy",
        _Policy,
    )
    with pytest.raises(AppError) as exc:
        await executor._assert_project_in_cabinet(
            cabinet_id="cab_1",
            project_id="proj_1",
            principal=Principal(sub="u1", roles=frozenset()),
            employee=None,
        )
    assert exc.value.status == 422
