"""Public auth discovery (L01) + test-only login mint."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from prodavan.api.deps import SessionDep
from prodavan.application.identity.service import IdentityCommandService
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.auth.test_token import (
    DEMO_COMPANY_NAME,
    DEMO_COMPANY_PASSWORD,
    DEMO_COMPANY_SUB,
    DEMO_EMPLOYEE_DISPLAY_NAME,
    DEMO_EMPLOYEE_EMAIL,
    DEMO_EMPLOYEE_SUB,
    PLATFORM_ADMIN_SUB,
    mint_company_principal_token,
    mint_test_access_token,
)
from prodavan.infrastructure.keycloak.provisioning import get_provisioning
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

router = APIRouter(prefix="/auth", tags=["auth"])

Persona = Literal["platform_admin", "demo_employee", "company_principal"]


def _issuer_base() -> str | None:
    if settings.keycloak_issuer_url:
        return settings.keycloak_issuer_url.rstrip("/")
    if settings.keycloak_url:
        return f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
    return None


def _require_test_auth_mode() -> None:
    mode = settings.auth_mode.strip().lower()
    if mode != "test" or settings.app_env.strip().lower() == "prod":
        raise AppError(
            code="NOT_FOUND",
            title="Not Found",
            status=404,
            detail="Test login is only available when AUTH_MODE=test",
        )


@router.get("/config")
async def auth_config() -> dict:
    """Flutter / clients discover auth_mode and public OIDC endpoints."""
    mode = settings.auth_mode.strip().lower()
    issuer = _issuer_base()
    oidc: dict | None = None
    if issuer:
        oidc = {
            "issuer": issuer,
            "audience": settings.oidc_audience,
            "client_id": settings.oidc_flutter_client_id,
            "realm": settings.keycloak_realm,
            "discovery_url": f"{issuer}/.well-known/openid-configuration",
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
            "revocation_endpoint": f"{issuer}/protocol/openid-connect/revoke",
            "jwks_uri": settings.oidc_jwks_url or f"{issuer}/protocol/openid-connect/certs",
            "redirect_uri": settings.oidc_flutter_redirect_uri,
            "redirect_uri_desktop": settings.oidc_flutter_redirect_uri_desktop,
        }
    return {
        "auth_mode": mode,
        "oidc": oidc if mode == "oidc" else None,
    }


class TestLoginBody(BaseModel):
    model_config = {"extra": "forbid"}

    persona: Persona = Field(description="platform_admin | demo_employee | company_principal")


async def _ensure_demo_company(session: SessionDep) -> CompanyRow:
    """Idempotent seed: demo company principal (id+password) + human employee."""
    from prodavan.domain.identity import MembershipRole

    existing_co = await session.execute(
        select(CompanyRow).where(CompanyRow.name == DEMO_COMPANY_NAME).order_by(CompanyRow.created_at.asc())
    )
    company = existing_co.scalars().first()
    if company is None:
        svc = IdentityCommandService(session, get_provisioning())
        company, _ = await svc.create_company_with_admin(
            name=DEMO_COMPANY_NAME,
            password=DEMO_COMPANY_PASSWORD,
            admin_email=DEMO_EMPLOYEE_EMAIL,
            admin_display_name=DEMO_EMPLOYEE_DISPLAY_NAME,
        )
    if company.keycloak_sub != DEMO_COMPANY_SUB:
        company.keycloak_sub = DEMO_COMPANY_SUB
        await session.commit()
        await session.refresh(company)

    email = DEMO_EMPLOYEE_EMAIL.lower()
    existing_emp = await session.execute(
        select(EmployeeRow).where(EmployeeRow.email == email).order_by(EmployeeRow.created_at.asc())
    )
    emp = existing_emp.scalars().first()
    if emp is None:
        svc = IdentityCommandService(session, get_provisioning())
        emp = await svc.invite_employee(
            company_id=company.id,
            email=email,
            display_name=DEMO_EMPLOYEE_DISPLAY_NAME,
            role=MembershipRole.COMPANY_ADMIN,
        )
    # Pin stable test sub so /auth/test/login JWT matches employees.keycloak_sub
    # (Fake/admin invite ids are ephemeral; broker/password login use the same KC sub).
    if emp.keycloak_sub != DEMO_EMPLOYEE_SUB:
        emp.keycloak_sub = DEMO_EMPLOYEE_SUB
        await session.commit()
        await session.refresh(emp)
    return company


@router.post("/test/login")
async def test_login(body: TestLoginBody, session: SessionDep) -> dict:
    """Dev/CI only: mint HS256 JWT (+ seed demo company)."""
    _require_test_auth_mode()

    if body.persona == "platform_admin":
        token, expires_in = mint_test_access_token(sub=PLATFORM_ADMIN_SUB, platform_admin=True)
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "persona": body.persona,
        }

    company = await _ensure_demo_company(session)

    if body.persona == "company_principal":
        token, expires_in = mint_company_principal_token(
            sub=company.keycloak_sub or DEMO_COMPANY_SUB,
            company_id=company.id,
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "persona": body.persona,
            "company_id": company.id,
            "username": company.id,
        }

    token, expires_in = mint_test_access_token(
        sub=DEMO_EMPLOYEE_SUB,
        email=DEMO_EMPLOYEE_EMAIL,
        platform_admin=False,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "persona": body.persona,
    }
