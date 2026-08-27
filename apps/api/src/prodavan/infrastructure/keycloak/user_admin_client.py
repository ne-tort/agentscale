"""Keycloak Admin — Auth Service user registration (domain-agnostic)."""

from __future__ import annotations

import logging
import time

import httpx

from prodavan.application.auth.user_admin import RegisterResult
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_TOKEN_SKEW_SECONDS = 30.0


class HttpUserAdminClient:
    """Live Keycloak Admin API — create/reuse users + realm roles (ROPC-ready)."""

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
        roles = list(realm_roles)
        if not uname or not email_l:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="username and email required",
            )
        if password is not None and not str(password).strip():
            password = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._admin_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            role_payload = await self._resolve_realm_roles(
                client, headers=headers, role_names=roles, fail_fast=True
            )
            users_url = self._users_url()

            if password:
                first_name = (display_name or "Company").strip()[:100] or "Company"
                last_name = "Org"
                payload: dict[str, object] = {
                    "username": uname,
                    "email": email_l,
                    "enabled": True,
                    "emailVerified": True,
                    "firstName": first_name,
                    "lastName": last_name,
                    "requiredActions": [],
                    "credentials": [
                        {"type": "password", "value": password, "temporary": False},
                    ],
                }
                lookup = {"username": uname, "exact": "true"}
                user_id, reused = await self._create_or_reuse_user(
                    client,
                    headers=headers,
                    users_url=users_url,
                    payload=payload,
                    lookup=lookup,
                    return_reused=True,
                )
                if reused:
                    await client.put(
                        f"{users_url}/{user_id}",
                        json={
                            "id": user_id,
                            "username": uname,
                            "email": email_l,
                            "enabled": True,
                            "emailVerified": True,
                            "firstName": first_name,
                            "lastName": last_name,
                            "requiredActions": [],
                        },
                        headers=headers,
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
            else:
                first_name = "Employee"
                last_name = "User"
                if display_name:
                    parts = display_name.strip().split(None, 1)
                    first_name = parts[0][:100] or first_name
                    if len(parts) > 1:
                        last_name = parts[1][:100] or last_name
                else:
                    local = email_l.split("@", 1)[0].strip()
                    if local:
                        first_name = local[:100]
                payload = {
                    "username": uname,
                    "email": email_l,
                    "enabled": True,
                    "emailVerified": True,
                    "firstName": first_name,
                    "lastName": last_name,
                    "requiredActions": [],
                }
                lookup = {"email": email_l, "exact": "true"}
                user_id = await self._create_or_reuse_user(
                    client,
                    headers=headers,
                    users_url=users_url,
                    payload=payload,
                    lookup=lookup,
                )
                await client.put(
                    f"{users_url}/{user_id}",
                    json={
                        "id": user_id,
                        "username": uname,
                        "email": email_l,
                        "enabled": True,
                        "emailVerified": True,
                        "firstName": first_name,
                        "lastName": last_name,
                        "requiredActions": [],
                    },
                    headers=headers,
                )

            await self._map_realm_roles(client, headers=headers, user_id=user_id, roles=role_payload)
            return RegisterResult(
                keycloak_user_id=user_id,
                username=uname,
                email=email_l,
                realm_roles=roles,
            )

    async def _create_or_reuse_user(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        users_url: str,
        payload: dict[str, object],
        lookup: dict[str, str],
        return_reused: bool = False,
    ) -> str | tuple[str, bool]:
        create = await client.post(users_url, json=payload, headers=headers)
        username = str(payload.get("username") or "")
        if create.status_code == 409:
            user_id = await self._lookup_user_id(client, headers=headers, users_url=users_url, params=lookup)
            if user_id is None:
                raise AppError(
                    code="INVITE_EXISTS",
                    title="User already exists",
                    status=409,
                    detail=f"Keycloak user {username} already exists but could not be resolved",
                )
            return (user_id, True) if return_reused else user_id
        if create.status_code not in (201, 204):
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak user create failed",
                status=502,
                detail=f"create user returned {create.status_code}",
            )

        user_id = self._extract_user_id(create)
        if user_id is None:
            user_id = await self._lookup_user_id(client, headers=headers, users_url=users_url, params=lookup)
        if user_id is None:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak user create failed",
                status=502,
                detail="could not resolve created user id",
            )
        return (user_id, False) if return_reused else user_id

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

    async def _resolve_realm_roles(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        role_names: list[str],
        fail_fast: bool = True,
    ) -> list[dict[str, object]]:
        if not role_names:
            return []
        roles_url = f"{self._base}/admin/realms/{self._realm}/roles"
        payload: list[dict[str, object]] = []
        missing: list[str] = []
        for name in role_names:
            resp = await client.get(f"{roles_url}/{name}", headers=headers)
            if resp.status_code >= 400:
                missing.append(name)
                continue
            body = resp.json()
            payload.append({"id": body["id"], "name": body["name"]})
        if missing:
            detail = f"realm roles missing: {', '.join(missing)}"
            if fail_fast:
                raise AppError(
                    code="KEYCLOAK_ADMIN",
                    title="Keycloak role missing",
                    status=502,
                    detail=detail,
                )
            logger.warning("%s", detail)
        return payload

    async def _map_realm_roles(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        user_id: str,
        roles: list[dict[str, object]],
    ) -> None:
        if not roles:
            return
        map_url = f"{self._base}/admin/realms/{self._realm}/users/{user_id}/role-mappings/realm"
        mapped = await client.post(map_url, json=roles, headers=headers)
        if mapped.status_code >= 400:
            raise AppError(
                code="KEYCLOAK_ADMIN",
                title="Keycloak role assign failed",
                status=502,
                detail=f"role-mappings returned {mapped.status_code}",
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
