"""Identity provisioning port — Keycloak Admin (disable / password; registration via Auth Kafka)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class InviteResult:
    """Legacy DTO kept for import compatibility; registration no longer returns this sync."""

    keycloak_user_id: str
    email: str
    required_actions: list[str] = field(default_factory=lambda: ["UPDATE_PASSWORD", "VERIFY_EMAIL"])
    realm_roles: list[str] = field(default_factory=list)


@dataclass
class CompanyPrincipalResult:
    """Legacy DTO kept for import compatibility; registration no longer returns this sync."""

    keycloak_user_id: str
    username: str
    realm_roles: list[str] = field(default_factory=list)


class IdentityProvisioningPort(Protocol):
    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None: ...

    async def disable_username(self, *, username: str) -> None: ...

    async def set_company_password(self, *, username: str, password: str) -> None: ...


class FakeIdentityProvisioning:
    """In-memory disable/password for tests / AUTH without live KC Admin."""

    def __init__(self) -> None:
        self.disabled: list[str] = []
        self.company_principals: list[dict[str, object]] = []

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None:
        self.disabled.append(keycloak_user_id or email)

    async def disable_username(self, *, username: str) -> None:
        self.disabled.append(username)

    async def set_company_password(self, *, username: str, password: str) -> None:
        self.company_principals.append(
            {"username": username.strip(), "password": password, "action": "set_password"}
        )


_fake: FakeIdentityProvisioning | None = None
_http: IdentityProvisioningPort | None = None


def get_provisioning() -> IdentityProvisioningPort:
    """Composition root for Keycloak Admin provisioning (non-registration)."""
    global _fake, _http
    from prodavan.config.settings import settings

    if settings.keycloak_invite_mode == "admin":
        if _http is None:
            from prodavan.domain.errors import AppError
            from prodavan.infrastructure.keycloak.admin_client import HttpKeycloakAdminClient

            if not all(
                [
                    settings.keycloak_url,
                    settings.keycloak_admin_client_id,
                    settings.keycloak_admin_client_secret,
                ]
            ):
                raise AppError(
                    code="AUTH_MISCONFIGURED",
                    title="Auth misconfigured",
                    status=500,
                    detail="KEYCLOAK_INVITE_MODE=admin requires KEYCLOAK_URL and admin client credentials",
                )
            _http = HttpKeycloakAdminClient(
                base_url=settings.keycloak_url or "",
                realm=settings.keycloak_realm,
                client_id=settings.keycloak_admin_client_id or "",
                client_secret=settings.keycloak_admin_client_secret or "",
            )
        return _http

    if _fake is None:
        _fake = FakeIdentityProvisioning()
    return _fake


def reset_provisioning() -> None:
    """Reset Fake + cached HTTP client (tests)."""
    global _fake, _http
    _fake = None
    _http = None
    from prodavan.application.auth.user_admin import reset_user_admin

    reset_user_admin()


# Back-compat aliases
KeycloakInvitePort = IdentityProvisioningPort
FakeKeycloakInviteClient = FakeIdentityProvisioning
get_invite_client = get_provisioning
reset_invite_client = reset_provisioning


__all__ = [
    "InviteResult",
    "CompanyPrincipalResult",
    "IdentityProvisioningPort",
    "FakeIdentityProvisioning",
    "get_provisioning",
    "reset_provisioning",
    "KeycloakInvitePort",
    "FakeKeycloakInviteClient",
    "get_invite_client",
    "reset_invite_client",
]
