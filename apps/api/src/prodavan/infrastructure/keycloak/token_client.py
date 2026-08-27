"""Keycloak OIDC token / revoke / authorize helpers (in-cluster URL only)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from prodavan.application.auth.errors import (
    auth_misconfigured,
    identity_unreachable,
    raise_for_keycloak_token_response,
)
from prodavan.application.auth.models import TokenPair

logger = logging.getLogger(__name__)


class KeycloakTokenClient:
    """Low-level Keycloak protocol client — never exposed to Flutter."""

    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise auth_misconfigured("KEYCLOAK_URL is required")
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._timeout = timeout

    @property
    def realm_root(self) -> str:
        return f"{self._base}/realms/{self._realm}"

    @property
    def token_url(self) -> str:
        return f"{self.realm_root}/protocol/openid-connect/token"

    @property
    def revoke_url(self) -> str:
        return f"{self.realm_root}/protocol/openid-connect/revoke"

    @property
    def logout_url(self) -> str:
        return f"{self.realm_root}/protocol/openid-connect/logout"

    @property
    def authorize_url(self) -> str:
        return f"{self.realm_root}/protocol/openid-connect/auth"

    @property
    def health_url(self) -> str:
        return f"{self.realm_root}"

    async def password_grant(self, *, username: str, password: str) -> TokenPair:
        return await self._token_form(
            {
                "grant_type": "password",
                "client_id": self._client_id,
                "username": username,
                "password": password,
                "scope": "openid profile email offline_access",
            }
        )

    async def refresh_grant(self, *, refresh_token: str) -> TokenPair:
        return await self._token_form(
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": refresh_token,
            }
        )

    async def authorization_code_grant(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenPair:
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        return await self._token_form(data)

    async def revoke(self, *, token: str, token_type_hint: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await client.post(
                    self.revoke_url,
                    data={
                        "client_id": self._client_id,
                        "token": token,
                        "token_type_hint": token_type_hint,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            logger.warning("keycloak revoke failed: %s", type(exc).__name__)

    async def end_session(self, *, id_token: str | None, post_logout_redirect_uri: str | None) -> None:
        if not id_token:
            return
        params: dict[str, str] = {
            "client_id": self._client_id,
            "id_token_hint": id_token,
        }
        if post_logout_redirect_uri:
            params["post_logout_redirect_uri"] = post_logout_redirect_uri
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await client.get(self.logout_url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("keycloak end_session failed: %s", type(exc).__name__)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=min(self._timeout, 5.0)) as client:
                resp = await client.get(self.health_url)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    def build_authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        kc_idp_hint: str | None = None,
        scopes: str = "openid profile email offline_access",
    ) -> str:
        query: dict[str, str] = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if kc_idp_hint:
            query["kc_idp_hint"] = kc_idp_hint
        return f"{self.authorize_url}?{urlencode(query)}"

    async def _token_form(self, data: dict[str, str]) -> TokenPair:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException as exc:
            raise identity_unreachable("Keycloak token request timed out", status=504) from exc
        except httpx.HTTPError as exc:
            raise identity_unreachable(f"Keycloak unreachable: {type(exc).__name__}") from exc

        if resp.status_code >= 400:
            raise_for_keycloak_token_response(resp.status_code, resp.text)

        body: dict[str, Any] = resp.json()
        access = body.get("access_token")
        if not access:
            raise identity_unreachable("Keycloak token response missing access_token")
        expires = body.get("expires_in")
        return TokenPair(
            access_token=str(access),
            refresh_token=(str(body["refresh_token"]) if body.get("refresh_token") else None),
            id_token=(str(body["id_token"]) if body.get("id_token") else None),
            expires_in=int(expires) if expires is not None else None,
            token_type=str(body.get("token_type") or "Bearer"),
        )
