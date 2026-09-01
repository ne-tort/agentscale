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
from tests.integration.support import configure_and_launch, owner_bearer_token


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
    with TestClient(create_app()) as client:
        yield client


@requires_postgres
def test_admin_company_quotas_and_metrics(client: TestClient) -> None:
    admin = _token(sub="padmin", email="padmin@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "QuotaCo", "password": "test-company-pass", "admin_email": "boss@quotaco.test"},
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

    boss_tok = owner_bearer_token(_token, created.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "First", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text

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
        json={"name": "MetricsCo", "password": "test-company-pass", "admin_email": "boss@metricsco.test"},
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
    assert "employees_active" in match
    assert match["employees_active"] >= 1
    assert "projects_total" in match
    assert "agent_tokens_used" in match
    assert "ai_keys_bound" in match
    assert match["ai_keys_bound"] == 0
    assert "last_activity_at" in match
    assert "storage_bytes" in match
    assert "high_agent_usage" in match


@requires_postgres
def test_company_subscription_expiring_metrics(client: TestClient) -> None:
    admin = _token(sub="sub-admin", email="sub@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "SubCo", "password": "test-company-pass", "admin_email": "boss@subco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
    ends = (datetime.now(UTC) + timedelta(days=7)).isoformat()

    updated = client.put(
        f"/api/v1/admin/companies/{company_id}/subscription",
        headers={"Authorization": f"Bearer {admin}"},
        json={"subscription_lifetime": False, "subscription_ends_at": ends},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["subscription_expiring_soon"] is True

    metrics = client.get(
        f"/api/v1/admin/companies/{company_id}/metrics",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["subscription_expiring_soon"] is True
    assert metrics.json()["subscription_ends_at"] is not None


@requires_postgres
def test_company_metrics_key_expiring_soon(client: TestClient) -> None:
    admin = _token(sub="renew-admin", email="renew@example.com", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}
    created = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "RenewalCo", "password": "test-company-pass"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]
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
        json={"name": "DualCo", "password": "test-company-pass", "admin_email": "padmin@example.com", "admin_display_name": "Admin"},
    )
    assert created.status_code == 201

    dual_sub = created.json()["admin_employee"]["keycloak_sub"]
    dual = _token(sub=dual_sub, email="padmin@example.com", platform_admin=True)
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
        json={"name": "OrgCo", "password": "test-company-pass", "admin_email": "owner@orgco.test"},
    )
    company_id = created.json()["company"]["id"]
    boss_tok = owner_bearer_token(_token, created.json())
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
    items = org.json()["items"]
    assert len(items) >= 1
    assert any(i.get("name") == "OrgCab" for i in items)


@requires_postgres
def test_company_employees_and_summary(client: TestClient) -> None:
    admin = _token(sub="padmin3", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "EmpCo", "password": "test-company-pass", "admin_email": "boss@empco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    boss_tok = owner_bearer_token(_token, created.json())
    invite = client.post(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={
            "login": "member",
            "password": "test-employee-pass",
            "contact_email": "member@empco.test",
            "display_name": "Member",
            "role": "member",
        },
    )
    assert invite.status_code == 201, invite.text

    employees = client.get(
        f"/api/v1/companies/{company_id}/employees",
        headers={"Authorization": f"Bearer {boss_tok}"},
    )
    assert employees.status_code == 200
    emails = {e["email"] for e in employees.json()["items"]}
    contacts = {e.get("contact_email") for e in employees.json()["items"]}
    assert "boss@empco.test" in emails
    assert "member@empco.test" in contacts

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


@requires_postgres
def test_admin_company_description_and_running_cabinets(client: TestClient) -> None:
    admin = _token(sub="desc-admin", email="desc@example.com", platform_admin=True)
    created = client.post(
        "/api/v1/companies",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DescCo", "password": "test-company-pass", "admin_email": "boss@descco.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    patched = client.patch(
        f"/api/v1/admin/companies/{company_id}",
        headers={"Authorization": f"Bearer {admin}"},
        json={"description": "Platform customer"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "Platform customer"

    listed = client.get(
        "/api/v1/admin/companies",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert listed.status_code == 200, listed.text
    match = next(i for i in listed.json()["items"] if i["id"] == company_id)
    assert match["description"] == "Platform customer"
    assert match["running_cabinets"] == 0

    boss_tok = owner_bearer_token(_token, created.json())
    cab = client.post(
        "/api/v1/cabinets",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "RunCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    project = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "Active project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    key = client.post(
        "/api/v1/admin/ai-keys",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "name": "DescCo Key",
            "provider": "cursor",
            "api_kind": "cursor_sdk",
            "secret": "sk-desc",
            "company_ids": [company_id],
        },
    )
    assert key.status_code == 201, key.text

    owner_h = {"Authorization": f"Bearer {boss_tok}"}
    configure_and_launch(client, owner_h, project_id)

    listed_after = client.get(
        "/api/v1/admin/companies",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert listed_after.status_code == 200
    match_after = next(i for i in listed_after.json()["items"] if i["id"] == company_id)
    assert match_after["running_cabinets"] == 1


@requires_postgres
def test_admin_delete_company_with_invited_admin_only(client: TestClient) -> None:
    """Regression: memberships loaded by disable_employee must not block company DELETE."""
    admin = _token(sub="padmin-del-invite", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}
    created = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "InviteOnlyDelete", "password": "test-company-pass", "admin_email": "boss@inviteonly.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    deleted = client.delete(f"/api/v1/admin/companies/{company_id}", headers=admin_h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert deleted.json().get("soft") is True
    # Cascade runs inline when Celery disabled (CI); otherwise enqueued.
    if not deleted.json().get("cascade_enqueued"):
        assert deleted.json().get("employees_disabled")
    gone = client.get(f"/api/v1/admin/companies/{company_id}", headers=admin_h)
    assert gone.status_code == 404
    listed = client.get("/api/v1/admin/companies", headers=admin_h)
    assert listed.status_code == 200
    assert all(i["id"] != company_id for i in listed.json()["items"])
    again = client.delete(f"/api/v1/admin/companies/{company_id}", headers=admin_h)
    assert again.status_code == 404


@requires_postgres
def test_admin_delete_company_cascades_soft(client: TestClient) -> None:
    admin = _token(sub="padmin-del", email="padmin-del@example.com", platform_admin=True)
    admin_h = {"Authorization": f"Bearer {admin}"}
    created = client.post(
        "/api/v1/companies",
        headers=admin_h,
        json={"name": "DeleteMe", "password": "test-company-pass", "admin_email": "boss@deleteme.test"},
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["company"]["id"]

    boss_tok = owner_bearer_token(_token, created.json())
    boss_h = {"Authorization": f"Bearer {boss_tok}"}
    cab = client.post(
        "/api/v1/cabinets",
        headers=boss_h,
        json={"name": "DoomedCab", "company_id": company_id},
    )
    assert cab.status_code in (200, 201), cab.text
    cabinet_id = cab.json()["id"]

    proj = client.post(
        f"/api/v1/cabinets/{cabinet_id}/projects",
        headers=boss_h,
        json={"name": "DoomedProj"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    deleted = client.delete(f"/api/v1/admin/companies/{company_id}", headers=admin_h)
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted"] is True
    assert body["id"] == company_id
    assert body.get("soft") is True
    if not body.get("cascade_enqueued"):
        assert project_id in body.get("projects_soft_deleted", body.get("projects_deleted", []))
        assert cabinet_id in body.get("cabinets_soft_deleted", body.get("cabinets_deleted", []))

    gone = client.get(f"/api/v1/admin/companies/{company_id}", headers=admin_h)
    assert gone.status_code == 404

    if not body.get("cascade_enqueued"):
        proj_gone = client.get(f"/api/v1/projects/{project_id}", headers=boss_h)
        assert proj_gone.status_code in (403, 404)
        cab_gone = client.get(f"/api/v1/cabinets/{cabinet_id}", headers=boss_h)
        assert cab_gone.status_code in (403, 404)
        recycle = client.get("/api/v1/admin/recycle", headers=admin_h)
        assert recycle.status_code == 200
        assert any(p["id"] == project_id for p in recycle.json()["projects"])
        assert any(c["id"] == cabinet_id for c in recycle.json()["cabinets"])
