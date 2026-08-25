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
    DEMO_EMPLOYEE_DISPLAY_NAME,
    DEMO_EMPLOYEE_EMAIL,
    DEMO_EMPLOYEE_SUB,
    PLATFORM_ADMIN_SUB,
    mint_test_access_token,
)
from prodavan.infrastructure.keycloak.invite import get_invite_client
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

router = APIRouter(prefix="/auth", tags=["auth"])

Persona = Literal["platform_admin", "demo_employee"]


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

    persona: Persona = Field(description="platform_admin | demo_employee")


async def _ensure_demo_employee(session: SessionDep) -> None:
    """Idempotent seed: Prodavan Demo company + demo@prodavan.local company admin."""
    from prodavan.domain.identity import MembershipRole

    email = DEMO_EMPLOYEE_EMAIL.lower()
    existing_emp = await session.execute(
        select(EmployeeRow).where(EmployeeRow.email == email).order_by(EmployeeRow.created_at.asc())
    )
    if existing_emp.scalars().first() is not None:
        return

    svc = IdentityCommandService(session, get_invite_client())
    existing_co = await session.execute(
        select(CompanyRow).where(CompanyRow.name == DEMO_COMPANY_NAME).order_by(CompanyRow.created_at.asc())
    )
    company = existing_co.scalars().first()
    if company is not None:
        await svc.invite_employee(
            company_id=company.id,
            email=email,
            display_name=DEMO_EMPLOYEE_DISPLAY_NAME,
            role=MembershipRole.COMPANY_ADMIN,
        )
        return

    await svc.create_company_with_admin(
        name=DEMO_COMPANY_NAME,
        admin_email=email,
        admin_display_name=DEMO_EMPLOYEE_DISPLAY_NAME,
    )


@router.post("/test/login")
async def test_login(body: TestLoginBody, session: SessionDep) -> dict:
    """Dev/CI only: mint HS256 JWT (+ seed demo company for demo_employee)."""
    _require_test_auth_mode()

    if body.persona == "platform_admin":
        token, expires_in = mint_test_access_token(sub=PLATFORM_ADMIN_SUB, platform_admin=True)
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "persona": body.persona,
        }

    await _ensure_demo_employee(session)
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
