"""Keycloak Admin invite port (L01) — no passwords in Prodavan API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class InviteResult:
    keycloak_user_id: str
    email: str
    required_actions: list[str] = field(default_factory=lambda: ["UPDATE_PASSWORD", "VERIFY_EMAIL"])


class KeycloakInvitePort(Protocol):
    async def invite_user(self, *, email: str, display_name: str | None) -> InviteResult: ...

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None: ...


class FakeKeycloakInviteClient:
    """In-memory invite client for tests / AUTH without live KC Admin."""

    def __init__(self) -> None:
        self.invites: list[dict[str, str | None]] = []
        self.disabled: list[str] = []
        self._n = 0

    async def invite_user(self, *, email: str, display_name: str | None) -> InviteResult:
        self._n += 1
        self.invites.append({"email": email, "display_name": display_name})
        return InviteResult(keycloak_user_id=f"kc_fake_{self._n}", email=email)

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None:
        self.disabled.append(keycloak_user_id or email)


_fake: FakeKeycloakInviteClient | None = None


def get_invite_client() -> KeycloakInvitePort:
    global _fake
    from prodavan.config.settings import settings

    if settings.keycloak_invite_mode == "admin":
        from prodavan.infrastructure.keycloak.admin_client import HttpKeycloakInviteClient

        if not all(
            [
                settings.keycloak_url,
                settings.keycloak_admin_client_id,
                settings.keycloak_admin_client_secret,
            ]
        ):
            from prodavan.domain.errors import AppError

            raise AppError(
                code="AUTH_MISCONFIGURED",
                title="Auth misconfigured",
                status=500,
                detail="KEYCLOAK_INVITE_MODE=admin requires KEYCLOAK_URL and admin client credentials",
            )
        return HttpKeycloakInviteClient(
            base_url=settings.keycloak_url or "",
            realm=settings.keycloak_realm,
            client_id=settings.keycloak_admin_client_id or "",
            client_secret=settings.keycloak_admin_client_secret or "",
        )

    if _fake is None:
        _fake = FakeKeycloakInviteClient()
    return _fake


def reset_invite_client() -> None:
    global _fake
    _fake = None
