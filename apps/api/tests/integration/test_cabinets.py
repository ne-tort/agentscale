"""Cabinet Runtime contract tests (L06)."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")

from prodavan.config.settings import settings
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _token(*, sub: str, email: str | None = None, platform_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "platform_admin": platform_admin,
        "roles": [] if not platform_admin else ["platform.admin"],
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    return TestClient(create_app())


def test_cabinets_require_auth(client: TestClient) -> None:
    r = client.get("/api/v1/cabinets")
    assert r.status_code == 401


@requires_postgres
def test_create_cabinet_base_seed_and_peer_isolation(client: TestClient) -> None:
    admin = _token(sub="padmin", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "CabCo", "admin_email": "owner@cabco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    owner_tok = _token(sub="owner-sub", email="owner@cabco.test")
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {owner_tok}"})
    assert me.status_code == 200

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "My Base", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    body = cab.json()
    assert body["id"].startswith("cab_")
    assert body["schema_name"].startswith("cab_inst_")
    assert body["status"] == "active"
    cabinet_id = body["id"]

    tabs = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/tabs",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert tabs.status_code == 200
    titles = {t["title"] for t in tabs.json()}
    assert {"Projects", "Chat", "Context", "Tables", "Tools"}.issubset(titles)

    tbl = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "slug": "suppliers",
            "label": "Suppliers",
            "columns": [{"name": "name", "type": "text", "required": True}],
        },
    )
    assert tbl.status_code == 201, tbl.text

    listed = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200
    assert any(t["slug"] == "suppliers" for t in listed.json())

    row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/data/suppliers/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"values": {"name": "DNS Shop"}},
    )
    assert row.status_code == 201, row.text
    row_id = row.json()["id"]

    rows = client.get(
        f"/api/v1/cabinets/{cabinet_id}/data/suppliers/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert rows.status_code == 200
    assert any(r["name"] == "DNS Shop" for r in rows.json()["rows"])

    updated = client.post(
        f"/api/v1/cabinets/{cabinet_id}/data/suppliers/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"id": row_id, "values": {"name": "DNS Updated"}},
    )
    assert updated.status_code == 200

    deleted = client.delete(
        f"/api/v1/cabinets/{cabinet_id}/data/suppliers/rows/{row_id}",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert deleted.status_code == 204

    peer = _token(sub="peer-sub", email="peer@other.test")
    denied = client.get(
        f"/api/v1/cabinets/{cabinet_id}",
        headers={"Authorization": f"Bearer {peer}"},
    )
    assert denied.status_code == 403
    assert "peer" in (denied.json().get("detail") or "").lower()

    archived = client.post(
        f"/api/v1/cabinets/{cabinet_id}/archive",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    stub = client.post(
        f"/api/v1/cabinets/{cabinet_id}/materialize-stub?project_id=proj_test1",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert stub.status_code == 200
    assert stub.json()["status"] == "materialized"

    write_blocked = client.post(
        f"/api/v1/cabinets/{cabinet_id}/data/suppliers/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"values": {"name": "After archive"}},
    )
    assert write_blocked.status_code == 409


@requires_postgres
def test_mcp_tools_dispatch_and_ban_sql(client: TestClient) -> None:
    admin = _token(sub="padmin2", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "McpCo", "admin_email": "mcp@cabco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    owner_tok = _token(sub="mcp-owner", email="mcp@cabco.test")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {owner_tok}"}).status_code == 200

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "MCP Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    tools = client.get(
        f"/api/v1/cabinets/{cabinet_id}/mcp/tools",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert tools.status_code == 200
    names = {t["name"] for t in tools.json()["tools"]}
    assert "cabinet.tables.list" in names

    create = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp/call",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "tool": "cabinet.tables.create",
            "arguments": {
                "slug": "parts",
                "label": "Parts",
                "columns": [{"name": "sku", "type": "text", "required": True}],
            },
        },
    )
    assert create.status_code == 200, create.text
    assert create.json()["result"]["slug"] == "parts"

    upsert = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp/call",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "tool": "cabinet.rows.upsert",
            "arguments": {"table_slug": "parts", "values": {"sku": "PN-1"}},
        },
    )
    assert upsert.status_code == 200, upsert.text
    row_id = upsert.json()["result"]["id"]

    query = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp/call",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"tool": "cabinet.rows.query", "arguments": {"table_slug": "parts"}},
    )
    assert query.status_code == 200
    assert any(r["id"] == row_id for r in query.json()["result"]["rows"])

    banned = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp/call",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"tool": "cabinet.sql.execute", "arguments": {"sql": "SELECT 1"}},
    )
    assert banned.status_code == 403

    pkg_b64 = base64.b64encode(build_minimal_package_zip(name="suppliers_sync")).decode()
    deployed = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"zip_base64": pkg_b64},
    )
    assert deployed.status_code == 201, deployed.text
    assert deployed.json()["name"] == "suppliers_sync"
    assert deployed.json()["status"] == "active"

    listed = client.get(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert listed.status_code == 200
    assert any(p["name"] == "suppliers_sync" for p in listed.json())

    disabled = client.post(
        f"/api/v1/cabinets/{cabinet_id}/mcp-packages/suppliers_sync/disable",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"


@requires_postgres
def test_bundle_export_import_new_schema(client: TestClient) -> None:
    admin = _token(sub="padmin-bundle", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "BundleCo", "admin_email": "bundle@cabco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    owner_tok = _token(sub="bundle-owner", email="bundle@cabco.test")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {owner_tok}"}).status_code == 200

    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Source Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    source_id = cab.json()["id"]
    source_schema = cab.json()["schema_name"]

    assert (
        client.post(
            f"/api/v1/cabinets/{source_id}/meta/tables",
            headers={"Authorization": f"Bearer {owner_tok}"},
            json={
                "slug": "items",
                "label": "Items",
                "columns": [{"name": "title", "type": "text", "required": True}],
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/cabinets/{source_id}/data/items/rows",
            headers={"Authorization": f"Bearer {owner_tok}"},
            json={"values": {"title": "Widget"}},
        ).status_code
        == 201
    )

    exported = client.get(
        f"/api/v1/cabinets/{source_id}/bundle",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert exported.status_code == 200, exported.text
    zip_b64 = exported.json()["zip_base64"]
    assert exported.json()["format"] == "cabinet.bundle"

    imported = client.post(
        "/api/v1/cabinets/import",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"company_id": company_id, "zip_base64": zip_b64, "name": "Copy Cab"},
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    new_id = body["cabinet"]["id"]
    assert new_id != source_id
    assert body["cabinet"]["schema_name"] != source_schema
    assert body["cabinet"]["name"] == "Copy Cab"

    rows = client.get(
        f"/api/v1/cabinets/{new_id}/data/items/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert rows.status_code == 200
    assert any(r["title"] == "Widget" for r in rows.json()["rows"])


@requires_postgres
def test_meta_mutate_columns_views_tabs(client: TestClient) -> None:
    admin = _token(sub="meta-admin", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "MetaMutCo", "admin_email": "meta@cabco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    owner_tok = _token(sub="meta-owner", email="meta@cabco.test")
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "Meta Mut Cab", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text
    cabinet_id = cab.json()["id"]

    tbl = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "slug": "parts",
            "label": "Parts",
            "columns": [{"name": "name", "type": "text", "required": True}],
        },
    )
    assert tbl.status_code == 201, tbl.text

    dup_tbl = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "slug": "parts",
            "label": "Parts dup",
            "columns": [{"name": "name", "type": "text", "required": True}],
        },
    )
    assert dup_tbl.status_code == 409

    col = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables/parts/columns",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"name": "sku", "type": "text", "required": False},
    )
    assert col.status_code == 201, col.text
    assert col.json()["name"] == "sku"

    meta = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/tables/parts",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert meta.status_code == 200
    col_names = {c["name"] for c in meta.json()["columns"]}
    assert {"name", "sku"}.issubset(col_names)

    row = client.post(
        f"/api/v1/cabinets/{cabinet_id}/data/parts/rows",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"values": {"name": "Capacitor", "sku": "C-100"}},
    )
    assert row.status_code == 201, row.text

    reserved = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/views",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"slug": "projects", "table_slug": "parts", "ui_json": {"version": 1, "kind": "collection"}},
    )
    assert reserved.status_code == 409

    view = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/views",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={
            "slug": "parts_list",
            "table_slug": "parts",
            "ui_json": {"version": 1, "kind": "collection", "title_field": "name"},
        },
    )
    assert view.status_code == 201, view.text
    assert view.json()["slug"] == "parts_list"

    views = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/views",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert views.status_code == 200
    assert any(v["slug"] == "parts_list" for v in views.json())

    tab = client.post(
        f"/api/v1/cabinets/{cabinet_id}/meta/tabs",
        headers={"Authorization": f"Bearer {owner_tok}"},
        json={"title": "Parts", "order": 50, "view_slug": "parts_list"},
    )
    assert tab.status_code == 201, tab.text
    assert tab.json()["view_slug"] == "parts_list"

    tabs = client.get(
        f"/api/v1/cabinets/{cabinet_id}/meta/tabs",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert tabs.status_code == 200
    assert any(t["title"] == "Parts" and t.get("view_slug") == "parts_list" for t in tabs.json())
