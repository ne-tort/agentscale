"""Auth Service user-admin port — create users in Keycloak (no identity domain)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from prodavan.domain.identity import ROLE_EMPLOYEE


@dataclass(slots=True)
class RegisterResult:
    keycloak_user_id: str
    username: str
    email: str
    realm_roles: list[str] = field(default_factory=list)


class UserAdminPort(Protocol):
    async def register_user(
        self,
        *,
        username: str,
        email: str,
        password: str | None,
        realm_roles: list[str],
        display_name: str | None,
    ) -> RegisterResult: ...

    async def disable_user(
        self,
        *,
        keycloak_user_id: str | None,
        username: str | None,
        email: str | None,
    ) -> None: ...

    async def delete_user(
        self,
        *,
        keycloak_user_id: str | None,
        username: str | None,
        email: str | None,
    ) -> None: ...

    async def rename_user(
        self,
        *,
        keycloak_user_id: str | None,
        old_username: str | None,
        new_username: str,
        email: str | None,
    ) -> None: ...


class FakeUserAdmin:
    """In-memory user create for tests / AUTH without live KC Admin."""

    def __init__(self) -> None:
        self.registrations: list[dict[str, object]] = []
        self.disabled: list[str] = []
        self.deleted: list[str] = []
        self._n = 0
        self._by_username: dict[str, str] = {}
        self._by_email: dict[str, str] = {}

    async def register_user(
        self,
        *,
        username: str,
        email: str,
        password: str | None,
        realm_roles: list[str],
        display_name: str | None,
    ) -> RegisterResult:
        uname = username.strip()
        email_l = email.lower().strip()
        roles = list(realm_roles) or [ROLE_EMPLOYEE]
        self.registrations.append(
            {
                "username": uname,
                "email": email_l,
                "password": password,
                "realm_roles": roles,
                "display_name": display_name,
            }
        )
        existing = self._by_username.get(uname) or self._by_email.get(email_l)
        if existing is not None:
            return RegisterResult(
                keycloak_user_id=existing,
                username=uname,
                email=email_l,
                realm_roles=roles,
            )
        self._n += 1
        # Opaque fake sub — no domain semantics (company vs employee) in Auth.
        user_id = f"kc_fake_{self._n}"
        self._by_username[uname] = user_id
        self._by_email[email_l] = user_id
        return RegisterResult(
            keycloak_user_id=user_id,
            username=uname,
            email=email_l,
            realm_roles=roles,
        )

    async def disable_user(
        self,
        *,
        keycloak_user_id: str | None,
        username: str | None,
        email: str | None,
    ) -> None:
        self.disabled.append(keycloak_user_id or username or email or "")

    async def delete_user(
        self,
        *,
        keycloak_user_id: str | None,
        username: str | None,
        email: str | None,
    ) -> None:
        self.deleted.append(keycloak_user_id or username or email or "")
        if keycloak_user_id:
            for k, v in list(self._by_username.items()):
                if v == keycloak_user_id:
                    del self._by_username[k]
            for k, v in list(self._by_email.items()):
                if v == keycloak_user_id:
                    del self._by_email[k]

    async def rename_user(
        self,
        *,
        keycloak_user_id: str | None,
        old_username: str | None,
        new_username: str,
        email: str | None,
    ) -> None:
        uname = new_username.strip()
        old = (old_username or "").strip()
        sub = keycloak_user_id
        if sub is None and old:
            sub = self._by_username.get(old)
        if sub is None:
            return
        if old and old in self._by_username:
            del self._by_username[old]
        self._by_username[uname] = sub
        if email:
            self._by_email[email.lower().strip()] = sub


_fake: FakeUserAdmin | None = None
_http: UserAdminPort | None = None


def get_user_admin() -> UserAdminPort:
    """Composition root for Auth Service user create."""
    global _fake, _http
    from prodavan.config.settings import settings

    if settings.keycloak_invite_mode == "admin":
        if _http is None:
            from prodavan.domain.errors import AppError
            from prodavan.infrastructure.keycloak.user_admin_client import HttpUserAdminClient

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
            _http = HttpUserAdminClient(
                base_url=settings.keycloak_url or "",
                realm=settings.keycloak_realm,
                client_id=settings.keycloak_admin_client_id or "",
                client_secret=settings.keycloak_admin_client_secret or "",
            )
        return _http

    if _fake is None:
        _fake = FakeUserAdmin()
    return _fake


def reset_user_admin() -> None:
    global _fake, _http
    _fake = None
    _http = None


__all__ = [
    "RegisterResult",
    "UserAdminPort",
    "FakeUserAdmin",
    "get_user_admin",
    "reset_user_admin",
]
