"""Auth flow integration tests."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodavan.infrastructure.persistence.models.tenants import Cabinet
from prodavan.infrastructure.persistence.rls import apply_rls
from tests.conftest import DATABASE_URL, requires_postgres


@requires_postgres
@pytest.mark.asyncio
async def test_register_login_me_flow(client: AsyncClient, unique_suffix: str) -> None:
    email = f"user-{unique_suffix}@example.com"
    register_body = {
        "email": email,
        "password": "securepass123",
        "display_name": "Test User",
        "tenant_slug": f"tenant-{unique_suffix}",
        "tenant_display_name": "Test Tenant",
    }
    reg = await client.post("/api/v1/auth/register", json=register_body)
    assert reg.status_code == 200, reg.text
    reg_data = reg.json()
    assert reg_data["token_type"] == "bearer"
    assert reg_data["user"]["email"] == email
    assert len(reg_data["tenants"]) == 1

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "securepass123"},
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200
    me_data = me.json()
    assert me_data["user"]["email"] == email
    assert me_data["tenant"]["slug"] == f"tenant-{unique_suffix}"
    assert len(me_data["cabinet_ids"]) >= 1


@requires_postgres
@pytest.mark.asyncio
async def test_neg_ten_01_cross_tenant_cabinet_isolation(
    client: AsyncClient,
    unique_suffix: str,
) -> None:
    """NEG-TEN-01: RLS hides cabinets from other tenants."""
    body_a = {
        "email": f"a-{unique_suffix}@example.com",
        "password": "securepass123",
        "display_name": "User A",
        "tenant_slug": f"tenant-a-{unique_suffix}",
        "tenant_display_name": "Tenant A",
    }
    body_b = {
        "email": f"b-{unique_suffix}@example.com",
        "password": "securepass123",
        "display_name": "User B",
        "tenant_slug": f"tenant-b-{unique_suffix}",
        "tenant_display_name": "Tenant B",
    }
    reg_a = await client.post("/api/v1/auth/register", json=body_a)
    reg_b = await client.post("/api/v1/auth/register", json=body_b)
    assert reg_a.status_code == 200
    assert reg_b.status_code == 200

    tenant_a_id = reg_a.json()["tenants"][0]["id"]
    user_a_id = reg_a.json()["user"]["id"]

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
