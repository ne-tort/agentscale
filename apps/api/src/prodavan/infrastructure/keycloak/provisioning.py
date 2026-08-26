"""Identity provisioning port — Keycloak Admin (invite / company principal / disable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from prodavan.domain.identity import ROLE_COMPANY, ROLE_EMPLOYEE


@dataclass
class InviteResult:
    keycloak_user_id: str
    email: str
    required_actions: list[str] = field(default_factory=lambda: ["UPDATE_PASSWORD", "VERIFY_EMAIL"])
    realm_roles: list[str] = field(default_factory=list)


@dataclass
class CompanyPrincipalResult:
    """Company org user: username = company_id, password set by Admin (no email required)."""

    keycloak_user_id: str
    username: str
    realm_roles: list[str] = field(default_factory=list)


class IdentityProvisioningPort(Protocol):
    async def invite_employee(
        self,
        *,
        email: str,
        display_name: str | None,
        realm_roles: list[str] | None = None,
    ) -> InviteResult: ...

    async def create_company_principal(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None,
    ) -> CompanyPrincipalResult: ...

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None: ...

    async def disable_username(self, *, username: str) -> None: ...

    async def set_company_password(self, *, username: str, password: str) -> None: ...


class FakeIdentityProvisioning:
    """In-memory provisioning for tests / AUTH without live KC Admin."""

    def __init__(self) -> None:
        self.invites: list[dict[str, object]] = []
        self.company_principals: list[dict[str, object]] = []
        self.disabled: list[str] = []
        self._n = 0
        self._employee_by_email: dict[str, str] = {}
        self._company_by_username: dict[str, str] = {}

    async def invite_employee(
        self,
        *,
        email: str,
        display_name: str | None,
        realm_roles: list[str] | None = None,
    ) -> InviteResult:
        normalized = email.lower().strip()
        roles = list(realm_roles or [ROLE_EMPLOYEE])
        self.invites.append({"email": normalized, "display_name": display_name, "realm_roles": roles})
        existing = self._employee_by_email.get(normalized)
        if existing is not None:
            return InviteResult(keycloak_user_id=existing, email=normalized, realm_roles=roles)
        self._n += 1
        user_id = f"kc_fake_{self._n}"
        self._employee_by_email[normalized] = user_id
        return InviteResult(keycloak_user_id=user_id, email=normalized, realm_roles=roles)

    async def create_company_principal(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None,
    ) -> CompanyPrincipalResult:
        uname = username.strip()
        roles = [ROLE_COMPANY]
        self.company_principals.append(
            {
                "username": uname,
                "password": password,
                "display_name": display_name,
                "realm_roles": roles,
            }
        )
        existing = self._company_by_username.get(uname)
        if existing is not None:
            return CompanyPrincipalResult(keycloak_user_id=existing, username=uname, realm_roles=roles)
        self._n += 1
        user_id = f"kc_co_fake_{self._n}"
        self._company_by_username[uname] = user_id
        return CompanyPrincipalResult(keycloak_user_id=user_id, username=uname, realm_roles=roles)

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
    """Composition root for Keycloak Admin provisioning."""
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
    "ROLE_COMPANY",
    "ROLE_EMPLOYEE",
]
