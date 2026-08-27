"""Auth Service DTOs — token pair + identity claims from Keycloak JWT."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"


@dataclass(slots=True, frozen=True)
class AuthPrincipalClaims:
    """Identity extracted from access token (Keycloak JWT ``sub`` is the user id)."""

    sub: str
    roles: tuple[str, ...] = ()
    email: str | None = None
    username: str | None = None


@dataclass(slots=True, frozen=True)
class AuthSessionResult:
    tokens: TokenPair
    claims: AuthPrincipalClaims


@dataclass(slots=True, frozen=True)
class AuthConfigView:
    auth_mode: str
    brokers: tuple[str, ...] = ()
    features: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "auth_mode": self.auth_mode,
            "brokers": list(self.brokers),
            "features": dict(self.features),
        }


@dataclass(slots=True, frozen=True)
class BrokerStart:
    redirect_url: str
    idp: str
