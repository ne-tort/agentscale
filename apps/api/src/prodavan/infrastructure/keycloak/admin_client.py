"""Keycloak Admin HTTP client for invite/disable (L01)."""

from __future__ import annotations

import logging

import httpx

from prodavan.domain.errors import AppError
from prodavan.infrastructure.keycloak.invite import InviteResult

logger = logging.getLogger(__name__)

REQUIRED_ACTIONS = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]


class HttpKeycloakInviteClient:
    """Live Keycloak Admin API — client credentials + execute-actions-email."""

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

    async def _admin_token(self, client: httpx.AsyncClient) -> str:
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
        token = resp.json().get("access_token")
        if not token:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak admin auth failed",
                status=502,
                detail="no access_token in response",
            )
        return str(token)

    async def invite_user(self, *, email: str, display_name: str | None) -> InviteResult:
        normalized = email.lower().strip()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._admin_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            users_url = f"{self._base}/admin/realms/{self._realm}/users"
            payload: dict[str, object] = {
                "username": normalized,
                "email": normalized,
                "enabled": True,
                "emailVerified": False,
                "requiredActions": list(REQUIRED_ACTIONS),
            }
            if display_name:
                parts = display_name.strip().split(None, 1)
                payload["firstName"] = parts[0]
                if len(parts) > 1:
                    payload["lastName"] = parts[1]

            create = await client.post(users_url, json=payload, headers=headers)
            if create.status_code == 409:
                raise AppError(
                    code="INVITE_EXISTS",
                    title="User already exists",
                    status=409,
                    detail=f"Keycloak user {normalized} already exists",
                )
            if create.status_code not in (201, 204):
                raise AppError(
                    code="KEYCLOAK_ADMIN",
                    title="Keycloak invite failed",
                    status=502,
                    detail=f"create user returned {create.status_code}",
                )

            user_id = self._extract_user_id(create)
            if user_id is None:
                lookup = await client.get(users_url, params={"email": normalized, "exact": "true"}, headers=headers)
                if lookup.status_code >= 400:
                    raise AppError(
                        code="KEYCLOAK_ADMIN",
                        title="Keycloak invite failed",
                        status=502,
                        detail="could not resolve created user id",
                    )
                users = lookup.json()
                if not users:
                    raise AppError(
                        code="KEYCLOAK_ADMIN",
                        title="Keycloak invite failed",
                        status=502,
                        detail="created user not found by email",
                    )
                user_id = users[0]["id"]

            actions_url = f"{users_url}/{user_id}/execute-actions-email"
            actions = await client.put(actions_url, json=REQUIRED_ACTIONS, headers=headers)
            if actions.status_code >= 400:
                logger.warning("execute-actions-email failed: %s", actions.status_code)

            return InviteResult(keycloak_user_id=user_id, email=normalized, required_actions=list(REQUIRED_ACTIONS))

    async def disable_user(self, *, keycloak_user_id: str | None, email: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._admin_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            users_url = f"{self._base}/admin/realms/{self._realm}/users"
            user_id = keycloak_user_id
            if not user_id:
                lookup = await client.get(
                    users_url, params={"email": email.lower(), "exact": "true"}, headers=headers
                )
                if lookup.status_code >= 400 or not lookup.json():
                    return
                user_id = lookup.json()[0]["id"]

            disable_url = f"{users_url}/{user_id}"
            resp = await client.put(disable_url, json={"enabled": False}, headers=headers)
            if resp.status_code >= 400:
                raise AppError(
                    code="KEYCLOAK_ADMIN",
                    title="Keycloak disable failed",
                    status=502,
                    detail=f"disable user returned {resp.status_code}",
                )

    @staticmethod
    def _extract_user_id(resp: httpx.Response) -> str | None:
        location = resp.headers.get("Location") or resp.headers.get("location")
        if location:
            return location.rstrip("/").split("/")[-1]
        if resp.status_code == 201 and resp.text:
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("id"):
                    return str(body["id"])
            except Exception:
                pass
        return None
