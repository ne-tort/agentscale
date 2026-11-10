from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodavan.application.plugins.bootstrap.contracts import (
    PluginBootstrapResponse,
    PluginScopeResponse,
    RpcOperationResponse,
)
from prodavan.domain.plugins.types import PluginRpcOperations, RpcOperationDeclaration
from prodavan.domain.plugins.validation import validate_manifest

pytestmark = pytest.mark.integration


def _root() -> Path:
    return Path(__file__).resolve().parents[5]


def _read(relative: str) -> str:
    return (_root() / relative).read_text(encoding="utf-8")


def test_bootstrap_dto_matches_web_types_and_contains_no_secret_material() -> None:
    expected = {
        "plugin_id",
        "manifest_version",
        "artifact_digest",
        "static_origin",
        "plugin_shell_origin",
        "frontend_entry",
        "frontend_base_path",
        "route_path",
        "capabilities",
        "rpc_operations",
        "public_pages",
        "scope",
        "grant_expires_at",
    }
    assert set(PluginBootstrapResponse.model_fields) == expected
    assert set(RpcOperationResponse.model_fields) == {
        "capability_id",
        "capability_version",
        "operation",
        "http_method",
        "backend_path",
        "public",
    }
    assert set(PluginScopeResponse.model_fields) == {
        "company_id",
        "cabinet_id",
        "project_id",
        "session_id",
    }
    web_types = _read("apps/web/src/api/types.ts")
    for name in (
        "frontend_entry",
        "frontend_base_path",
        "route_path",
        "rpc_operations",
        "public_pages",
        "static_origin",
        "plugin_shell_origin",
        "grant_expires_at",
    ):
        assert name in web_types
    for forbidden in ("capability_token", "dsn", "webhook_secret_ref", "admin_receipt"):
        assert forbidden not in PluginBootstrapResponse.model_fields


def test_public_spa_route_is_outside_require_auth_and_preserves_route_path() -> None:
    router = _read("apps/web/src/app/router.tsx")
    routes = _read("apps/web/src/app/plugin-routes.tsx")
    page = _read("apps/web/src/features/plugins/PluginPage.tsx")
    frame = _read("apps/web/src/features/plugins/usePluginFrame.ts")

    assert "PUBLIC_PLUGIN_ROUTE_ELEMENTS" in router
    assert router.index("PUBLIC_PLUGIN_ROUTE_ELEMENTS") < router.index("<Route element={<RequireAuth />}>")
    assert '"/public/plugins/:companyId/:pluginId"' in routes
    assert "<ConnectedPluginPage publicPage" in routes
    assert "createPluginGatewayTransport" in routes
    assert "publicPage," in routes
    assert "pluginRoutePathFromLocation" in page
    assert "routePath," in page
    assert "getPluginInfo({ pluginId, companyId, publicPage, routePath" in frame


def test_frontend_rpc_fields_reach_manifest_scoped_on_request_broker() -> None:
    info = _read("apps/web/src/features/plugins/pluginInfo.ts")
    page = _read("apps/web/src/features/plugins/PluginPage.tsx")
    broker = _read("apps/web/src/features/plugins/operationBroker.ts")
    rpc = _read("apps/web/src/features/plugins/rpc.ts")

    for field in (
        "frontend_entry",
        "frontend_base_path",
        "route_path",
        "rpc_operations",
        "public_pages",
    ):
        assert field in info
    assert "createManifestOperationBroker" in page
    assert "operations: frame.rpcOperations" in page
    assert "transport: props.operationTransport" in page
    assert "onRequest" in page
    assert "options.onRequest(request)" in rpc
    assert "declaration.backend_path" in broker
    assert "request.input" in broker
    assert "generic proxy" not in broker.lower()


def test_hello_manifest_has_typed_unique_operations_and_current_frontend_version() -> None:
    manifest = json.loads(_read("packages/plugins/hello-plugin/manifest.json"))
    validate_manifest(manifest)
    operations = manifest["rpc_operations"]
    assert len(operations) == 2
    assert {item["capability"]["version"] for item in operations} == {"1.0"}
    assert {item["capability"]["id"] for item in operations} == {"hello.greetings"}
    paths = [item["backend_path"] for item in operations]
    assert len(set(paths)) == len(paths)
    assert paths == ["/api/greetings/list", "/api/greetings"]
    assert all(item["public"] is False for item in operations)
    assert "/api/stats" not in paths
    declarations = PluginRpcOperations(
        tuple(
            RpcOperationDeclaration(
                capability_id=item["capability"]["id"],
                capability_version=item["capability"]["version"],
                operation=item["operation"],
                http_method=item["http_method"],
                backend_path=item["backend_path"],
                public=item["public"],
            )
            for item in operations
        )
    )
    assert declarations.operations == tuple(declarations.operations)

    frontend = _read("packages/plugins/hello-plugin/frontend/app.js")
    assert 'message.capability = { id: "hello.greetings", version: "1.0" }' in frontend
    assert 'rpcRequest("list"' in frontend
    assert 'rpcRequest("greet"' in frontend


def test_manifest_schema_and_domain_rpc_declaration_stay_in_lockstep() -> None:
    schema = json.loads(_read("packages/schemas/plugin-manifest/v1/schema.json"))
    operation_schema = schema["$defs"]["rpcOperation"]
    assert operation_schema["additionalProperties"] is False
    assert set(operation_schema["required"]) == {
        "capability",
        "operation",
        "http_method",
        "backend_path",
    }
    assert set(operation_schema["properties"]) == {
        "capability",
        "operation",
        "http_method",
        "backend_path",
        "public",
    }
    assert schema["properties"]["rpc_operations"]["uniqueItems"] is True
