"""Acceptance contract for owner-module action invocation parity.

These checks are intentionally added during the migration verification freeze and
are not run as part of the implementation task.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.api.v1.admin_modules import (
    ActionInvokeBody as AdminActionInvokeBody,
    invoke_platform_module_action,
)
from prodavan.api.v1.company_modules import (
    ActionInvokeBody as CompanyActionInvokeBody,
    invoke_company_module_action,
)
from prodavan.application.modules.module_action_executor import ModuleActionExecutor
from prodavan.domain.identity import Principal


@pytest.mark.asyncio
async def test_owner_executor_merges_invocation_params_over_action_defaults() -> None:
    """Owner actions receive the same per-invocation context as cabinet actions."""
    executor = ModuleActionExecutor(MagicMock())
    loaded = {
        "id": "index_catalog",
        "kind": "content.index_opensearch",
        "params": {"table_slug": "catalogs", "source": "default"},
    }
    captured: dict = {}

    executor._load_action = AsyncMock(return_value=loaded)  # type: ignore[method-assign]

    async def fake_index(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"ok": True}

    executor._index_opensearch = fake_index  # type: ignore[method-assign]

    result = await executor.invoke_owner(
        owner_kind="company",
        owner_id="co_1",
        module_id="mod_catalog",
        action_id="index_catalog",
        row_id="catalog_1",
        principal=Principal(sub="owner", roles=frozenset()),
        extra_params={"source": "selected", "src_hash": "hash_1"},
    )

    assert result == {"ok": True}
    assert captured["params"] == {
        "table_slug": "catalogs",
        "source": "selected",
        "src_hash": "hash_1",
    }
    assert captured["row_id"] == "catalog_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind,owner_id", [("platform", "platform"), ("company", "co_1")])
async def test_owner_executor_dispatches_index_tabular_with_owner_scope(
    owner_kind: str,
    owner_id: str,
) -> None:
    """Owner invokes may index tabular rows in their own instance scope."""
    executor = ModuleActionExecutor(MagicMock())
    executor._load_action = AsyncMock(
        return_value={
            "id": "index_catalog",
            "kind": "content.index_tabular",
            "params": {"table_slug": "catalogs"},
        }
    )  # type: ignore[method-assign]
    captured: dict = {}

    async def fake_index(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"kind": "content.index_tabular", "status": "ready"}

    executor._index_tabular = fake_index  # type: ignore[method-assign]

    result = await executor.invoke_owner(
        owner_kind=owner_kind,
        owner_id=owner_id,
        module_id="mod_catalog",
        action_id="index_catalog",
        row_id="catalog_1",
        principal=Principal(sub="owner", roles=frozenset()),
    )

    assert result == {"kind": "content.index_tabular", "status": "ready"}
    assert captured["owner_kind"] == owner_kind
    assert captured["owner_id"] == owner_id
    assert captured["row_id"] == "catalog_1"


@pytest.mark.asyncio
async def test_platform_owner_route_forwards_params() -> None:
    body = AdminActionInvokeBody.model_validate(
        {"row_id": "row_1", "params": {"src_hash": "hash_1"}}
    )
    executor = MagicMock()
    executor.invoke_owner = AsyncMock(return_value={"ok": True})
    module_service = MagicMock()
    module_service._get_row = AsyncMock()

    with (
        patch(
            "prodavan.application.modules.module_action_executor.ModuleActionExecutor",
            return_value=executor,
        ),
        patch("prodavan.api.v1.admin_modules.ModuleService", return_value=module_service),
    ):
        result = await invoke_platform_module_action(
            module_id="mod_1",
            action_id="index_catalog",
            body=body,
            principal=Principal(sub="admin", roles=frozenset({"platform_admin"})),
            _=MagicMock(),
            session=AsyncMock(),
        )

    assert result == {"ok": True}
    assert executor.invoke_owner.await_args.kwargs["extra_params"] == {"src_hash": "hash_1"}


@pytest.mark.asyncio
async def test_owner_auto_index_tabular_uses_owner_instance_scope() -> None:
    """Owner row writes auto-index the owner row instead of cabinet data."""
    executor = ModuleActionExecutor(MagicMock())
    captured: dict = {}

    executor._list_actions = AsyncMock(
        return_value=[
            {
                "id": "index_catalog",
                "kind": "content.index_tabular",
                "params": {"table_slug": "catalogs", "source_column": "source_file"},
                "trigger": {"on": ["row.updated"]},
            }
        ]
    )  # type: ignore[method-assign]

    async def fake_list(**kwargs):  # noqa: ANN003
        captured["list"] = kwargs
        return [
            {
                "row_id": "catalog_1",
                "body": {
                    "status": "draft",
                    "source_file": {"storage_key": "source-1", "filename": "catalog.csv"},
                },
            }
        ]

    async def fake_index(**kwargs):  # noqa: ANN003
        captured["index"] = kwargs

    executor._list_rows_for_scope = fake_list  # type: ignore[method-assign]
    executor._index_tabular = fake_index  # type: ignore[method-assign]

    await executor.maybe_auto_index_tabular(
        cabinet_id="",
        module_id="mod_catalog",
        table_slug="catalogs",
        row_id="catalog_1",
        principal=Principal(sub="owner", roles=frozenset()),
        employee=None,
        owner_kind="company",
        owner_id="co_1",
    )

    assert captured["list"]["owner_kind"] == "company"
    assert captured["list"]["owner_id"] == "co_1"
    assert captured["index"]["owner_kind"] == "company"
    assert captured["index"]["owner_id"] == "co_1"
    assert captured["index"]["row_id"] == "catalog_1"


@pytest.mark.asyncio
async def test_company_owner_route_forwards_params() -> None:
    body = CompanyActionInvokeBody.model_validate(
        {"row_id": "row_1", "params": {"src_hash": "hash_1"}}
    )
    executor = MagicMock()
    executor.invoke_owner = AsyncMock(return_value={"ok": True})
    entitlement = MagicMock()
    entitlement.require_company_actor = AsyncMock()
    company_service = MagicMock()
    company_service.get_for_company = AsyncMock()

    with (
        patch(
            "prodavan.application.modules.module_action_executor.ModuleActionExecutor",
            return_value=executor,
        ),
        patch("prodavan.api.v1.company_modules.EntitlementService", return_value=entitlement),
        patch("prodavan.api.v1.company_modules.CompanyModuleService", return_value=company_service),
    ):
        result = await invoke_company_module_action(
            company_id="co_1",
            module_id="mod_1",
            action_id="index_catalog",
            body=body,
            principal=Principal(sub="owner", roles=frozenset()),
            session=AsyncMock(),
            employee=None,
        )

    assert result == {"ok": True}
    assert executor.invoke_owner.await_args.kwargs["extra_params"] == {"src_hash": "hash_1"}
