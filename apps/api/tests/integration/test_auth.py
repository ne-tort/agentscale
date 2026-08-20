"""Auth flow integration tests."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodavan.infrastructure.persistence.models.tenants import Cabinet
from prodavan.infrastructure.persistence.rls import apply_rls
from tests.conftest import DATABASE_URL, requires_postgres
from tests.helpers import admin_headers, register_user


@requires_postgres
@pytest.mark.asyncio
async def test_register_endpoint_closed(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com",
            "password": "securepass123",
            "display_name": "X",
            "tenant_slug": "tenant-x",
            "tenant_display_name": "X",
        },
    )
    assert resp.status_code == 410


@requires_postgres
@pytest.mark.asyncio
async def test_login_me_flow(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="user")
    assert reg["token_type"] == "bearer"
    assert reg["user"]["login_id"] == f"user-{unique_suffix}"
    assert reg["user"]["role"] == "user"
    assert len(reg["tenants"]) == 1

    login = await client.post(
        "/api/v1/auth/login",
        json={"login_id": f"user-{unique_suffix}", "password": "securepass123"},
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200
    me_data = me.json()
    assert me_data["user"]["login_id"] == f"user-{unique_suffix}"
    assert me_data["tenant"]["slug"] == f"user-{unique_suffix}"
    assert len(me_data["cabinet_ids"]) >= 1


@requires_postgres
@pytest.mark.asyncio
async def test_profile_password_and_immutability(client: AsyncClient, unique_suffix: str) -> None:
    reg = await register_user(client, unique_suffix, prefix="prof")
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    patched = await client.patch(
        "/api/v1/me/profile",
        headers=headers,
        json={
            "contact_person": "Ivan",
            "phone": "+79990001122",
            "email": f"ivan-{unique_suffix}@example.com",
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["contact_person"] == "Ivan"
    assert body["login_id"] == f"prof-{unique_suffix}"
    assert body["company_name"] == "Test Tenant"

    cleared = await client.patch(
        "/api/v1/me/profile",
        headers=headers,
        json={"email": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["email"] is None

    invalid = await client.patch(
        "/api/v1/me/profile",
        headers=headers,
        json={"email": "not-an-email"},
    )
    assert invalid.status_code == 422
    inv = invalid.json()
    assert inv.get("code") == "VALIDATION_ERROR"
    assert isinstance(inv.get("detail"), str)
    assert "[" not in inv["detail"]

    bad = await client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": "wrong", "new_password": "newpass12345"},
    )
    assert bad.status_code == 400

    ok = await client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": "securepass123", "new_password": "newpass12345"},
    )
    assert ok.status_code == 204

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"login_id": f"prof-{unique_suffix}", "password": "securepass123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"login_id": f"prof-{unique_suffix}", "password": "newpass12345"},
    )
    assert new_login.status_code == 200


@requires_postgres
@pytest.mark.asyncio
async def test_admin_crud_and_non_admin_forbidden(
    client: AsyncClient, unique_suffix: str
) -> None:
    headers = await admin_headers(client)
    stats = await client.get("/api/v1/admin/stats", headers=headers)
    assert stats.status_code == 200, stats.text

    create = await client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "login_id": f"co-{unique_suffix}",
            "company_name": "Acme",
            "password": "securepass123",
            "email": f"co-{unique_suffix}@example.com",
        },
    )
    assert create.status_code == 201, create.text
    user_id = create.json()["id"]

    listed = await client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    assert any(u["id"] == user_id for u in listed.json())

    suspended = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"login_id": f"co-{unique_suffix}", "password": "securepass123"},
    )
    assert blocked.status_code == 403

    await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=headers,
        json={"status": "active"},
    )

    user_reg = await register_user(client, unique_suffix, prefix="na")
    user_headers = {"Authorization": f"Bearer {user_reg['access_token']}"}
    forbidden = await client.get("/api/v1/admin/stats", headers=user_headers)
    assert forbidden.status_code == 403

    deleted = await client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"


@requires_postgres
@pytest.mark.asyncio
async def test_neg_ten_01_cross_tenant_cabinet_isolation(
    client: AsyncClient,
    unique_suffix: str,
) -> None:
    """NEG-TEN-01: RLS hides cabinets from other tenants."""
    reg_a = await register_user(client, unique_suffix, prefix="a")
    await register_user(client, unique_suffix, prefix="b")

    tenant_a_id = reg_a["tenants"][0]["id"]
    user_a_id = reg_a["user"]["id"]

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            await apply_rls(
                session,
                user_id=uuid.UUID(user_a_id),
                tenant_id=uuid.UUID(tenant_a_id),
                local=True,
            )
            visible = await session.execute(select(Cabinet))
            cabinets = list(visible.scalars().all())
    await engine.dispose()

    assert len(cabinets) >= 1
    assert all(str(c.tenant_id) == str(tenant_a_id) for c in cabinets)
