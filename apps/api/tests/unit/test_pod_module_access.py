"""Unit tests — Pod module meta scope + access helpers + MCP merge."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.mcp.platform_modules_mcp import (
    PACKAGE_NAME,
    merge_platform_modules_mcp,
    platform_modules_mcp_package,
)
from prodavan.application.pod_identity.bridge import (
    build_launch_scopes,
    module_actions_scope,
    module_meta_scope,
    module_rows_scope,
)
from prodavan.application.tenant_infra.pod_modules import PodModuleDataService
from prodavan.domain.errors import AppError
from prodavan.domain.modules import ModuleBindKind


def test_build_launch_scopes_includes_meta() -> None:
    scopes = build_launch_scopes(["mod-a", "mod-b"])
    assert module_rows_scope("mod-a") in scopes
    assert module_actions_scope("mod-a") in scopes
    assert module_meta_scope("mod-a") in scopes
    assert module_meta_scope("mod-b") in scopes


def test_merge_platform_modules_mcp_unique_first() -> None:
    platform = platform_modules_mcp_package()
    merged = merge_platform_modules_mcp(
        [{"name": "other", "command": "x"}, {"name": PACKAGE_NAME, "command": "old"}],
        platform_pkg=platform,
    )
    assert merged[0]["name"] == PACKAGE_NAME
    assert merged[0]["command"] == "python"
    assert [p["name"] for p in merged] == [PACKAGE_NAME, "other"]


@pytest.mark.asyncio
async def test_put_meta_requires_meta_scope() -> None:
    session = AsyncMock()
    project = SimpleNamespace(id="proj-1", company_id="co-1", cabinet_id="cab-1")
    session.get = AsyncMock(return_value=project)
    svc = PodModuleDataService(session)
    bridge = SimpleNamespace(
        project_id="proj-1",
        company_id="co-1",
        cabinet_id="cab-1",
        pod_id="pod-1",
        require_project=MagicMock(),
        require_scope=MagicMock(side_effect=AppError(
            code="FORBIDDEN", title="Forbidden", status=403, detail="missing"
        )),
    )
    with pytest.raises(AppError) as exc:
        await svc.put_meta_document(
            bridge=bridge,  # type: ignore[arg-type]
            project_id="proj-1",
            module_id="mod-a",
            slug="tables",
            body=[],
        )
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_list_bound_modules_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    project = SimpleNamespace(id="proj-1", company_id="co-1", cabinet_id="cab-1")
    mod = SimpleNamespace(id="mod-a", name="Equipment", status="active")

    async def _get(model, key):  # noqa: ANN001
        if key == "proj-1":
            return project
        if key == "mod-a":
            return mod
        return None

    session.get = AsyncMock(side_effect=_get)
    svc = PodModuleDataService(session)

    monkeypatch.setattr(
        svc._bindings,
        "list_module_ids_for_project",
        AsyncMock(return_value=["mod-a"]),
    )
    monkeypatch.setattr(
        svc._bindings,
        "get_project_binding",
        AsyncMock(
            return_value=SimpleNamespace(
                bind_kind=ModuleBindKind.LOCAL,
                child_may_edit=False,
            )
        ),
    )
    monkeypatch.setattr(
        svc._instances,
        "get_instance",
        AsyncMock(return_value=SimpleNamespace(id="inst-1")),
    )

    bridge = SimpleNamespace(
        project_id="proj-1",
        company_id="co-1",
        cabinet_id="cab-1",
        pod_id="pod-1",
        require_project=MagicMock(),
    )
    items = await svc.list_bound_modules(bridge=bridge, project_id="proj-1")  # type: ignore[arg-type]
    assert items[0]["module_id"] == "mod-a"
    assert items[0]["bind_kind"] == ModuleBindKind.LOCAL
    assert items[0]["meta_writable"] is True
    assert items[0]["data_writable"] is True
    assert items[0]["instance_id"] == "inst-1"


@pytest.mark.asyncio
async def test_global_locked_not_writable(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    svc = PodModuleDataService(session)
    monkeypatch.setattr(
        svc._bindings,
        "get_project_binding",
        AsyncMock(
            return_value=SimpleNamespace(
                bind_kind=ModuleBindKind.GLOBAL,
                child_may_edit=False,
            )
        ),
    )
    bind_kind, meta_w, data_w = await svc._binding_flags(project_id="p", module_id="m")
    assert bind_kind == ModuleBindKind.GLOBAL
    assert meta_w is False
    assert data_w is False
