"""Keycloak Admin HTTP client — disable / password (registration via Auth UserAdmin)."""

from __future__ import annotations

import logging
import time

import httpx

from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_TOKEN_SKEW_SECONDS = 30.0


class HttpKeycloakAdminClient:
    """Live Keycloak Admin API — client credentials (disable / set password)."""

    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _admin_token(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at - _TOKEN_SKEW_SECONDS:
            return self._token

        token_url = f"{self._base}/realms/{self._realm}/protocol/openid-connect/token"
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak admin auth failed",
                status=502,
                detail=f"token endpoint returned {resp.status_code}",
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak admin auth failed",
                status=502,
                detail="no access_token in response",
            )
        expires_in = float(body.get("expires_in") or 60)
        self._token = str(token)
        self._token_expires_at = now + max(expires_in, 10.0)
        return self._token

    def _users_url(self) -> str:
        return f"{self._base}/admin/realms/{self._realm}/users"

    async def _lookup_user_id(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        users_url: str,
        params: dict[str, str],
    ) -> str | None:
        lookup = await client.get(users_url, params=params, headers=headers)
        if lookup.status_code >= 400 or not lookup.json():
            return None
        return str(lookup.json()[0]["id"])

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._admin_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            users_url = self._users_url()
            user_id = keycloak_user_id
            if not user_id:
                user_id = await self._lookup_user_id(
                    client,
                    headers=headers,
                    users_url=users_url,
                    params={"email": email.lower(), "exact": "true"},
                )
                if not user_id:
                    return
            await self._disable_id(client, headers=headers, users_url=users_url, user_id=str(user_id))

    async def disable_username(self, *, username: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._admin_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            users_url = self._users_url()
            user_id = await self._lookup_user_id(
                client,
                headers=headers,
                users_url=users_url,
                params={"username": username, "exact": "true"},
            )
            if not user_id:
                return
            await self._disable_id(client, headers=headers, users_url=users_url, user_id=user_id)

    async def set_company_password(self, *, username: str, password: str) -> None:
        uname = username.strip()
        if not uname or len(password) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company username and password (min 8) required",
            )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                token = await self._admin_token(client)
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                users_url = self._users_url()
                user_id = await self._lookup_user_id(
                    client,
                    headers=headers,
                    users_url=users_url,
                    params={"username": uname, "exact": "true"},
                )
                if not user_id:
                    raise AppError(
                        code="NOT_FOUND",
                        title="Not Found",
                        status=404,
                        detail=f"Keycloak user {uname} not found",
                    )
                reset = await client.put(
                    f"{users_url}/{user_id}/reset-password",
                    json={"type": "password", "value": password, "temporary": False},
                    headers=headers,
                )
                if reset.status_code >= 400:
                    raise AppError(
                        code="KEYCLOAK_ADMIN",
                        title="Keycloak password reset failed",
                        status=502,
                        detail=f"reset-password returned {reset.status_code}",
                    )
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak timeout",
                status=502,
                detail="Keycloak password reset timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak unavailable",
                status=502,
                detail=f"Keycloak request failed: {exc.__class__.__name__}",
            ) from exc

    async def _disable_id(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        users_url: str,
        user_id: str,
    ) -> None:
        resp = await client.put(f"{users_url}/{user_id}", json={"enabled": False}, headers=headers)
        if resp.status_code >= 400:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak disable failed",
                status=502,
                detail=f"disable user returned {resp.status_code}",
            )


# Back-compat alias
HttpKeycloakInviteClient = HttpKeycloakAdminClient
