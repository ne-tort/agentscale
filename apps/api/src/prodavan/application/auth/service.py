"""In-process Auth Service — BFF over Keycloak (no FE→KC, no own DB)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from prodavan.application.auth.errors import auth_misconfigured
from prodavan.application.auth.events import publish_auth_event
from prodavan.application.auth.models import (
    AuthConfigView,
    AuthPrincipalClaims,
    AuthSessionResult,
    BrokerStart,
    TokenPair,
)
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.keycloak.token_client import KeycloakTokenClient

logger = logging.getLogger(__name__)

SUPPORTED_BROKERS = ("vk", "yandex")
_ACTIVATED_ATTR = "prodavan.activated_at"
_STATE_TTL_SEC = 600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def decode_access_claims(access_token: str) -> AuthPrincipalClaims:
    parts = access_token.split(".")
    if len(parts) < 2:
        raise AppError(
            code="IDENTITY_PROVIDER",
            title="Invalid token",
            status=502,
            detail="access_token is not a JWT",
        )
    raw = json.loads(_b64url_decode(parts[1]))
    if not isinstance(raw, dict):
        raise AppError(
            code="IDENTITY_PROVIDER",
            title="Invalid token",
            status=502,
            detail="JWT payload is not an object",
        )
    sub = str(raw.get("sub") or "").strip()
    if not sub:
        raise AppError(
            code="IDENTITY_PROVIDER",
            title="Invalid token",
            status=502,
            detail="JWT missing sub",
        )
    roles: list[str] = []
    realm = raw.get("realm_access") or {}
    if isinstance(realm, dict):
        for r in realm.get("roles") or []:
            roles.append(str(r))
    for r in raw.get("roles") or []:
        roles.append(str(r))
    email = raw.get("email")
    username = raw.get("preferred_username")
    return AuthPrincipalClaims(
        sub=sub,
        roles=tuple(dict.fromkeys(roles)),
        email=str(email) if email else None,
        username=str(username) if username else None,
    )


def session_result_to_dict(result: AuthSessionResult) -> dict[str, Any]:
    t = result.tokens
    c = result.claims
    return {
        "access_token": t.access_token,
        "refresh_token": t.refresh_token,
        "id_token": t.id_token,
        "expires_in": t.expires_in,
        "token_type": t.token_type,
        "sub": c.sub,
        "roles": list(c.roles),
        "email": c.email,
        "username": c.username,
    }


class AuthService:
    """Facade: password/refresh/logout/health/broker over KeycloakTokenClient."""

    def __init__(self, token_client: KeycloakTokenClient | None = None) -> None:
        self._token_client = token_client

    def _require_oidc(self) -> None:
        if settings.auth_mode.strip().lower() != "oidc":
            raise auth_misconfigured("AUTH_MODE is not oidc")

    def _client(self) -> KeycloakTokenClient:
        if self._token_client is not None:
            return self._token_client
        url = (settings.keycloak_url or "").strip()
        if not url:
            raise auth_misconfigured("KEYCLOAK_URL is not configured")
        return KeycloakTokenClient(
            base_url=url,
            realm=settings.keycloak_realm,
            client_id=settings.oidc_flutter_client_id,
        )

    def _state_secret(self) -> bytes:
        secret = (settings.auth_test_secret or "").encode("utf-8")
        if not secret:
            raise auth_misconfigured("AUTH_TEST_SECRET required to sign broker state")
        return secret

    def public_config(self) -> AuthConfigView:
        mode = settings.auth_mode.strip().lower()
        brokers = SUPPORTED_BROKERS if mode == "oidc" else ()
        return AuthConfigView(
            auth_mode=mode,
            brokers=brokers,
            features={
                "password_login": mode == "oidc",
                "refresh": mode == "oidc",
                "logout": mode == "oidc",
                "broker": mode == "oidc",
            },
        )

    async def health(self) -> dict[str, Any]:
        mode = settings.auth_mode.strip().lower()
        if mode != "oidc":
            return {"status": "skip", "auth_mode": mode, "keycloak": False}
        try:
            ok = await self._client().health()
        except AppError as exc:
            return {
                "status": "error",
                "auth_mode": mode,
                "keycloak": False,
                "detail": exc.detail or exc.title,
            }
        return {
            "status": "ok" if ok else "error",
            "auth_mode": mode,
            "keycloak": ok,
        }

    async def login(self, *, username: str, password: str) -> AuthSessionResult:
        self._require_oidc()
        user = username.strip()
        if not user or not password:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="username and password required",
            )
        try:
            tokens = await self._client().password_grant(username=user, password=password)
        except AppError as exc:
            await publish_auth_event(
                "auth.login_failed",
                {"reason": exc.code, "username": user},
            )
            raise
        return await self._finalize_success(tokens, event="auth.login")

    async def refresh(self, *, refresh_token: str) -> AuthSessionResult:
        self._require_oidc()
        token = refresh_token.strip()
        if not token:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="refresh_token required",
            )
        tokens = await self._client().refresh_grant(refresh_token=token)
        result = await self._finalize_success(tokens, event="auth.token_refreshed")
        return result

    async def logout(
        self,
        *,
        refresh_token: str | None = None,
        access_token: str | None = None,
        id_token: str | None = None,
    ) -> None:
        self._require_oidc()
        client = self._client()
        logout_payload: dict[str, Any] = {}
        if access_token:
            try:
                claims = decode_access_claims(access_token)
                logout_payload = {
                    "sub": claims.sub,
                    "roles": list(claims.roles or ()),
                    "username": claims.username,
                }
            except AppError:
                logout_payload = {}
        if refresh_token:
            await client.revoke(token=refresh_token, token_type_hint="refresh_token")
        if access_token:
            await client.revoke(token=access_token, token_type_hint="access_token")
        await client.end_session(
            id_token=id_token,
            post_logout_redirect_uri=settings.oidc_flutter_redirect_uri_desktop,
        )
        if logout_payload:
            await publish_auth_event("auth.logout", logout_payload)

    def start_broker(
        self,
        *,
        idp: str,
        callback_uri: str,
        response_mode: str = "redirect",
    ) -> BrokerStart:
        self._require_oidc()
        hint = idp.strip().lower()
        if hint not in SUPPORTED_BROKERS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Unknown broker",
                status=422,
                detail=f"supported: {', '.join(SUPPORTED_BROKERS)}",
            )
        verifier = secrets.token_urlsafe(32)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = self._sign_state(
            {
                "idp": hint,
                "verifier": verifier,
                "callback": callback_uri,
                "response_mode": response_mode,
                "exp": int(time.time()) + _STATE_TTL_SEC,
            }
        )
        url = self._client().build_authorize_url(
            redirect_uri=callback_uri,
            state=state,
            code_challenge=challenge,
            kc_idp_hint=hint,
        )
        return BrokerStart(redirect_url=url, idp=hint)

    async def complete_broker(
        self,
        *,
        code: str,
        state: str,
        callback_uri: str,
    ) -> AuthSessionResult:
        self._require_oidc()
        payload = self._verify_state(state)
        if payload.get("callback") != callback_uri:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Invalid state",
                status=400,
                detail="callback mismatch",
            )
        verifier = str(payload.get("verifier") or "")
        tokens = await self._client().authorization_code_grant(
            code=code,
            redirect_uri=callback_uri,
            code_verifier=verifier or None,
        )
        return await self._finalize_success(tokens, event="auth.login")

    async def _finalize_success(
        self,
        tokens: TokenPair,
        *,
        event: str,
    ) -> AuthSessionResult:
        claims = decode_access_claims(tokens.access_token)
        result = AuthSessionResult(tokens=tokens, claims=claims)
        await publish_auth_event(
            event,
            {
                "sub": claims.sub,
                "roles": list(claims.roles),
                "email": claims.email,
                "username": claims.username,
            },
        )
        if event == "auth.login":
            await self._maybe_first_login(claims)
        return result

    async def _maybe_first_login(self, claims: AuthPrincipalClaims) -> None:
        """Best-effort: mark first activation via Keycloak user attribute (KC is SoT)."""
        admin_id = (settings.keycloak_admin_client_id or "").strip()
        admin_secret = (settings.keycloak_admin_client_secret or "").strip()
        base = (settings.keycloak_url or "").strip()
        if not admin_id or not admin_secret or not base:
            return
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                token_resp = await client.post(
                    f"{base.rstrip('/')}/realms/{settings.keycloak_realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": admin_id,
                        "client_secret": admin_secret,
                    },
                )
                if token_resp.status_code >= 400:
                    return
                admin_token = token_resp.json().get("access_token")
                if not admin_token:
                    return
                headers = {"Authorization": f"Bearer {admin_token}"}
                user_url = (
                    f"{base.rstrip('/')}/admin/realms/{settings.keycloak_realm}/users/{claims.sub}"
                )
                user_resp = await client.get(user_url, headers=headers)
                if user_resp.status_code >= 400:
                    return
                user = user_resp.json()
                attrs = user.get("attributes") or {}
                if isinstance(attrs, dict) and attrs.get(_ACTIVATED_ATTR):
                    return
                attrs = dict(attrs) if isinstance(attrs, dict) else {}
                attrs[_ACTIVATED_ATTR] = [str(int(time.time()))]
                user["attributes"] = attrs
                await client.put(user_url, headers=headers, json=user)
            await publish_auth_event(
                "auth.first_login",
                {
                    "sub": claims.sub,
                    "roles": list(claims.roles),
                    "email": claims.email,
                    "username": claims.username,
                },
            )
        except Exception:
            logger.exception("first_login attribute update failed for sub=%s", claims.sub)

    def _sign_state(self, payload: dict[str, Any]) -> str:
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = _b64url(hmac.new(self._state_secret(), body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def _verify_state(self, state: str) -> dict[str, Any]:
        try:
            body, sig = state.split(".", 1)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Invalid state",
                status=400,
                detail="malformed state",
            ) from exc
        expected = _b64url(
            hmac.new(self._state_secret(), body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, sig):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Invalid state",
                status=400,
                detail="bad state signature",
            )
        payload = json.loads(_b64url_decode(body))
        if not isinstance(payload, dict):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Invalid state",
                status=400,
                detail="state payload",
            )
        exp = int(payload.get("exp") or 0)
        if exp < int(time.time()):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Expired state",
                status=400,
                detail="broker state expired",
            )
        return payload


def get_auth_service() -> AuthService:
    return AuthService()


def broker_app_redirect(result: AuthSessionResult, *, app_redirect: str) -> str:
    """Redirect browser back to the app with tokens in the query (BFF handoff)."""
    q = urlencode(
        {
            "access_token": result.tokens.access_token,
            **(
                {"refresh_token": result.tokens.refresh_token}
                if result.tokens.refresh_token
                else {}
            ),
            **({"id_token": result.tokens.id_token} if result.tokens.id_token else {}),
            **(
                {"expires_in": str(result.tokens.expires_in)}
                if result.tokens.expires_in is not None
                else {}
            ),
            "sub": result.claims.sub,
        }
    )
    sep = "&" if "?" in app_redirect else "?"
    return f"{app_redirect}{sep}{q}"
