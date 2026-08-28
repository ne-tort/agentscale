"""Platform Admin self-service profile (Keycloak login + password)."""

from __future__ import annotations

from prodavan.application.auth.lifecycle import publish_rename_command
from prodavan.domain.companies.login import validate_login_username
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.keycloak.provisioning import get_provisioning


class AdminProfileService:
    async def get_profile(self, principal: Principal) -> dict:
        username = (principal.username or principal.sub or "").strip()
        if not username:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="username not available in token",
            )
        return {
            "sub": principal.sub,
            "username": username,
            "password_set": True,
        }

    async def set_login(self, principal: Principal, *, login: str) -> dict:
        new_login = validate_login_username(login)
        old = (principal.username or principal.sub or "").strip()
        if new_login == old:
            return {"username": new_login, "password_set": True}
        await publish_rename_command(
            client_ref=f"admin:{principal.sub}",
            sub=principal.sub,
            old_username=old or None,
            new_username=new_login,
            email=principal.email,
        )
        return {"username": new_login, "password_set": True}

    async def set_password(self, principal: Principal, *, password: str) -> dict:
        pwd = (password or "").strip()
        if len(pwd) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="password required (min 8 chars)",
            )
        username = (principal.username or principal.sub or "").strip()
        if not username:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="username not available in token",
            )
        await get_provisioning().set_company_password(username=username, password=pwd)
        return {"username": username, "password_set": True}
