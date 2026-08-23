"""Integration tests — L04 admin company control plane."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MODE", "test")
os.environ.setdefault("AUTH_TEST_SECRET", "dev-only-test-secret-change-me")
os.environ.setdefault("KEYCLOAK_INVITE_MODE", "fake")

from prodavan.config.settings import settings
from prodavan.domain.identity import Contour
from prodavan.infrastructure.auth.jwt import reset_jwt_validator
from prodavan.infrastructure.keycloak.invite import reset_invite_client
from prodavan.main import create_app
from tests.conftest import requires_postgres


def _token(
    *,
    sub: str,
    email: str | None = None,
    platform_admin: bool = False,
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "aud": settings.oidc_audience,
        "exp": now + timedelta(hours=1),
        "iat": now,
        "platform_admin": platform_admin,
        "roles": roles or ([] if not platform_admin else ["platform.admin"]),
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.auth_test_secret, algorithm="HS256")


@pytest.fixture()
def client() -> TestClient:
    reset_jwt_validator()
    reset_invite_client()
    return TestClient(create_app())


@requires_postgres
def test_admin_company_quotas_and_metrics(client: TestClient) -> None:
    admin = _token(sub="padmin", email="padmin@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "QuotaCo", "admin_email": "boss@quotaco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    detail = client.get(
        f"/api/v1/admin/companies/{company_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["cabinet_quota"]["max_cabinets"] == 10
    assert body["metrics"]["employees"] == 1

    set_q = client.put(
        f"/api/v1/admin/companies/{company_id}/cabinet-quotas",
        headers={"Authorization": f"Bearer {admin}"},
        json={"max_cabinets": 1, "max_packages_per_cabinet": 2, "max_bundle_import_mb": 5},
    )
    assert set_q.status_code == 200
    assert set_q.json()["max_cabinets"] == 1

    policy = client.put(
        f"/api/v1/admin/companies/{company_id}/agent-policy",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "tool_preset": "chat_readonly",
            "preferred_provider": "cursor",
            "platform_fallback": False,
            "model_allowlist": ["gpt-4"],
        },
    )
    assert policy.status_code == 200
    assert policy.json()["tool_preset"] == "chat_readonly"

    boss_tok = _token(sub="boss-sub", email="boss@quotaco.test")
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "First", "company_id": company_id},
    )
    assert cab.status_code == 201, cab.text

    blocked = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "Second", "company_id": company_id},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CABINET_QUOTA"


@requires_postgres
def test_list_platform_metrics_companies(client: TestClient) -> None:
    admin = _token(sub="metrics-admin", email="metrics@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "MetricsCo", "admin_email": "boss@metricsco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    listed = client.get(
        "/api/v1/admin/metrics/companies",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert isinstance(items, list)
    match = next((i for i in items if i["company_id"] == company_id), None)
    assert match is not None
    assert match["name"] == "MetricsCo"
    assert match["employees_total"] >= 1
    assert "projects_total" in match
    assert "agent_tokens_used" in match
    assert "ai_keys_bound" in match
    assert match["ai_keys_bound"] == 0


@requires_postgres
def test_company_metrics_key_expiring_soon(client: TestClient) -> None:
    import asyncio

    from prodavan.infrastructure.persistence.database import get_session_factory
    from prodavan.infrastructure.persistence.models.identity import CompanyRow

    async def _company_id() -> str:
        factory = get_session_factory()
        async with factory() as session:
            co = CompanyRow(name="RenewalCo")
            session.add(co)
            await session.commit()
            await session.refresh(co)
            return co.id

    admin = _token(sub="renew-admin", email="renew@example.com", platform_admin=True)
    company_id = asyncio.run(_company_id())
    soon = (datetime.now(UTC) + timedelta(days=5)).isoformat()

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "Soon",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-soon",
            "next_renewal_at": soon,
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    metrics = client.get(
        f"/api/v1/admin/companies/{company_id}/metrics",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert metrics.status_code == 200, metrics.text
    body = metrics.json()
    assert body["ai_keys_bound"] == 1
    assert body["ai_keys_expiring_soon"] == 1
    assert body["next_key_renewal_at"] is not None


@requires_postgres
def test_platform_admin_dual_role_me(client: TestClient) -> None:
    admin = _token(sub="padmin", email="padmin@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DualCo", "admin_email": "padmin@example.com", "admin_display_name": "Admin"},
    )
    assert created.status_code == 201

    dual = _token(sub="dual-sub", email="padmin@example.com", platform_admin=True)
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {dual}"})
    assert me.status_code == 200
    body = me.json()
    assert body["employee"] is not None
    assert body["employee"]["status"] == "active"
    assert Contour.PLATFORM_ADMIN in body["contours"]


@requires_postgres
def test_company_org_cabinets_list(client: TestClient) -> None:
    admin = _token(sub="padmin2", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "OrgCo", "admin_email": "owner@orgco.test"},
    )
    company_id = created.json()["company"]["id"]
    boss_tok = _token(sub="owner-sub", email="owner@orgco.test")
    client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "OrgCab", "company_id": company_id},
    )

    org = client.get(
        f"/api/v1/companies/{company_id}/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert org.status_code == 200
    assert len(org.json()["items"]) == 1
    assert org.json()["items"][0]["name"] == "OrgCab"


@requires_postgres
def test_company_employees_and_summary(client: TestClient) -> None:
    admin = _token(sub="padmin3", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "EmpCo", "admin_email": "boss@empco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    boss_tok = _token(sub="boss-empco", email="boss@empco.test")
    invite = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"email": "member@empco.test", "display_name": "Member"},
    )
    assert invite.status_code == 201, invite.text

    employees = client.get(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert employees.status_code == 200
    emails = {e["email"] for e in employees.json()["items"]}
    assert "boss@empco.test" in emails
    assert "member@empco.test" in emails

    summary = client.get(
        f"/api/v1/companies/{company_id}/summary",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert summary.status_code == 200
    assert summary.json()["metrics"]["employees_total"] >= 2

    stranger = _token(sub="stranger", email="x@y.z")
    denied = client.get(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {stranger}"},
    )
    assert denied.status_code == 403
