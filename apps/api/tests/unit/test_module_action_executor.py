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


def test_remote_probe_connection_key_ignores_ui_flags() -> None:
    from prodavan.application.modules.module_action_executor import remote_probe_connection_key

    params = {
        "dsn_column": "remote_dsn",
        "remote_table_column": "remote_table",
        "remote_database_column": "remote_database",
        "remote_user_column": "remote_user",
        "remote_password_column": "remote_password",
    }
    base = {
        "remote_dsn": {"secret_ref": "vault://cabinet_secrets/a"},
        "remote_database": "",
        "remote_table": "",
        "remote_user": "",
        "remote_password": None,
        "remote_dsn_has_user": False,
        "remote_dsn_reachable": False,
    }
    flagged = {
        **base,
        "remote_dsn_has_user": True,
        "remote_dsn_reachable": True,
        "remote_auth_failed": True,
        "status": "error",
    }
    assert remote_probe_connection_key(base, params) == remote_probe_connection_key(flagged, params)
    changed = {**base, "remote_dsn": {"secret_ref": "vault://cabinet_secrets/b"}}
    assert remote_probe_connection_key(base, params) != remote_probe_connection_key(changed, params)


@pytest.mark.asyncio
async def test_auto_probe_skips_when_connection_inputs_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ModuleActionExecutor(session=_FakeSession([  # type: ignore[arg-type]
        {
            "id": "probe_catalog_remote",
            "kind": "content.probe_remote_sql",
            "params": {"table_slug": "catalogs"},
        }
    ]))
    body = {
        "source_kind": "remote",
        "remote_dsn": {"secret_ref": "vault://cabinet_secrets/old"},
        "remote_database": "",
        "remote_table": "",
        "status": "error",
    }
    called: list[str] = []

    async def _list_rows(**kwargs):  # noqa: ANN003
        return [{"row_id": "r1", "body": dict(body)}]

    async def _probe(**kwargs):  # noqa: ANN003
        called.append("probe")
        return {}

    monkeypatch.setattr(executor, "_list_module_rows", _list_rows)
    monkeypatch.setattr(executor, "_probe_remote_sql", _probe)

    await executor.maybe_auto_probe_remote_sql(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        row_id="r1",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
        previous_body=dict(body),
    )
    assert called == []

    await executor.maybe_auto_probe_remote_sql(
        cabinet_id="cab_1",
        module_id="mod_equipment",
        table_slug="catalogs",
        row_id="r1",
        principal=Principal(sub="u1", roles=frozenset()),
        employee=None,
        previous_body={
            **body,
            "remote_dsn": {"secret_ref": "vault://cabinet_secrets/older"},
        },
    )
    assert called == ["probe"]


@pytest.mark.asyncio
async def test_invoke_owner_rejects_non_remote_actions() -> None:
    executor = ModuleActionExecutor(session=_FakeSession([  # type: ignore[arg-type]
        {"id": "create", "kind": "data.create_row", "params": {"table_slug": "x"}},
    ]))
    with pytest.raises(AppError) as exc:
        await executor.invoke_owner(
            owner_kind="platform",
            owner_id="platform",
            module_id="mod_equipment",
            action_id="create",
            principal=Principal(sub="admin", roles=frozenset({"platform_admin"})),
        )
    assert exc.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_invoke_owner_allows_index_opensearch_kind() -> None:
    """Owner whitelist includes content.index_opensearch (manual reindex button)."""
    from unittest.mock import AsyncMock, patch

    executor = ModuleActionExecutor(session=_FakeSession([  # type: ignore[arg-type]
        {
            "id": "index_catalog_opensearch",
            "kind": "content.index_opensearch",
            "params": {"table_slug": "catalogs"},
        },
    ]))
    with patch.object(
        executor,
        "_index_opensearch",
        new=AsyncMock(return_value={"kind": "content.index_opensearch", "status": "indexing"}),
    ) as mocked:
        out = await executor.invoke_owner(
            owner_kind="platform",
            owner_id="platform",
            module_id="mod_equipment",
            action_id="index_catalog_opensearch",
            row_id="cat_1",
            principal=Principal(sub="admin", roles=frozenset({"platform_admin"})),
        )
    assert out["status"] == "indexing"
    mocked.assert_awaited_once()
