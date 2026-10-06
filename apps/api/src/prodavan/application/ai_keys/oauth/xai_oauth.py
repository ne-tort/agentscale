"""xAI (Grok) OAuth — device-code flow (RFC 8628) + серверное хранение токенов.

Публичный OAuth-клиент Grok-CLI (тот же, что использует goose xai_oauth):
loopback-редирект жёстко зарегистрирован на 127.0.0.1:56121 и для серверной
платформы не годится, поэтому авторизация идёт device-code потоком:

1. сервер запрашивает device_code/user_code/verification_uri и кладёт стейт
   в Redis (TTL = время жизни кода);
2. UI показывает ссылку «Авторизовать» и код — пользователь открывает её в
   своём браузере и подтверждает доступ (SuperGrok-подписка);
3. сервер поллит token-эндпоинт (с учётом interval/slow_down) и на успехе
   сохраняет {access_token, refresh_token, expires_at} JSON-блобом в secret
   хранилище ключа (Vault/file — как обычные секреты);
4. access_token выдаётся через ``resolve_secret_for_key`` (авто-refresh за
   120s до истечения, refresh_token ротацией, single-flight); lease-push
   перед каждым send'ом гарантирует, что под всегда получает свежий токен.

При отказе refresh (invalid_grant — сессия отозвана/истекла) ключ гасится
в disabled + аудит-событие: UI показывает «приостановлен», пользователь
повторно авторизуется тем же device-flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.ai_keys.audit_service import AiKeyAuditService
from prodavan.core.infra.cache import cache_delete, cache_get, cache_key, cache_set
from prodavan.domain.ai_keys.types import ApiKind, KeyStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store

logger = logging.getLogger(__name__)

# Публичный OAuth-клиент Grok-CLI (десктопные флоу xAI);loopback от
# не-allowlisted клиентов auth-сервер xAI отклоняет, поэтому device-code.
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_DEVICE_AUTHORIZATION_URL = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
XAI_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "grok-cli:access",
    "api:access",
)

#: скользящий запас refresh: не ловить 401 посреди длинного turn'а
ACCESS_TOKEN_REFRESH_SKEW_SEC = 120
DEFAULT_TOKEN_TTL_SEC = 3600
DEVICE_DEFAULT_INTERVAL_SEC = 5
DEVICE_MIN_INTERVAL_SEC = 1
DEVICE_SLOW_DOWN_INCREMENT_SEC = 5
DEVICE_DEFAULT_EXPIRES_SEC = 300
_TERMINAL_STATE_TTL_SEC = 600
_HTTP_TIMEOUT_SEC = 20.0

BLOB_VERSION = 1

#: single-flight refresh: параллельные send'и не должны проигрывать
#: ротацию refresh_token несколькими одновременными обменами
_refresh_locks: dict[str, asyncio.Lock] = {}


def _redis_key(key_id: str) -> str:
    return cache_key("aik-xai-oauth", key_id)


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _post_form(url: str, data: dict[str, str]) -> tuple[int, dict[str, Any], str]:
    """POST x-www-form-urlencoded → (status, json-or-empty, raw-text)."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = await client.post(
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
    text = resp.text or ""
    try:
        body = resp.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    return resp.status_code, body, text


def token_blob(
    *,
    access_token: str,
    refresh_token: str,
    id_token: str | None = None,
    expires_in: int | None = None,
    now: datetime | None = None,
) -> str:
    """JSON-блоб секрета: токены + абсолютный срок access_token."""
    now = now or datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=int(expires_in or DEFAULT_TOKEN_TTL_SEC))
    return json.dumps(
        {
            "v": BLOB_VERSION,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "expires_at": expires_at.isoformat(),
        },
        ensure_ascii=False,
    )


def parse_token_blob(raw: str) -> dict[str, Any] | None:
    """None, когда секрет не похож на наш блоб (legacy/ручной секрет)."""
    try:
        blob = json.loads(raw)
    except Exception:
        return None
    if not isinstance(blob, dict) or not blob.get("access_token"):
        return None
    return blob


class XaiOAuthService:
    """Device-code авторизация + жизненный цикл токенов xAI-ключа."""

    def __init__(
        self,
        session: AsyncSession,
        secrets: SecretStore | None = None,
        audit: AiKeyAuditService | None = None,
    ) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()
        self._audit = audit or AiKeyAuditService(session)

    # ------------------------------------------------------------ device flow

    async def start_device_flow(self, key_id: str, *, principal: Principal) -> dict[str, Any]:
        row = await self._require_xai_key(key_id)
        try:
            status, body, text = await _post_form(
                XAI_DEVICE_AUTHORIZATION_URL,
                {"client_id": XAI_CLIENT_ID, "scope": " ".join(XAI_SCOPES)},
            )
        except httpx.HTTPError as exc:
            raise AppError(
                code="OAUTH_START_FAILED",
                title="xAI authorization start failed",
                status=502,
                detail=f"device code request failed: {exc}",
            ) from exc
        if status != 200 or not body.get("device_code"):
            raise AppError(
                code="OAUTH_START_FAILED",
                title="xAI authorization start failed",
                status=502,
                detail=f"device code request rejected ({status}): {text[:300]}",
            )
        expires_in = int(body.get("expires_in") or DEVICE_DEFAULT_EXPIRES_SEC)
        interval = max(DEVICE_MIN_INTERVAL_SEC, int(body.get("interval") or DEVICE_DEFAULT_INTERVAL_SEC))
        verification_uri = str(body.get("verification_uri") or "")
        state = {
            "status": "pending",
            "user_code": str(body.get("user_code") or ""),
            "verification_uri": verification_uri,
            "verification_uri_complete": str(body.get("verification_uri_complete") or verification_uri),
            "device_code": str(body["device_code"]),
            "interval_sec": interval,
            "expires_at_epoch": time.time() + expires_in,
            "next_poll_epoch": 0.0,
            "error": None,
        }
        if not await cache_set(_redis_key(key_id), json.dumps(state), ttl_sec=expires_in + 120):
            raise AppError(
                code="OAUTH_STATE_UNAVAILABLE",
                title="OAuth state store unavailable",
                status=503,
                detail="redis is required for the device authorization flow",
            )
        await self._audit.record(
            event_type="ai_key.oauth_started",
            key_id=key_id,
            principal=principal,
            detail={"provider": "xai", "flow": "device_code"},
        )
        _ = row  # ключ проверен; сам стейт живёт в Redis
        return self._public_state(state)

    async def device_flow_status(
        self,
        key_id: str,
        *,
        principal: Principal,
        poll: bool = True,
    ) -> dict[str, Any]:
        await self._require_xai_key(key_id)
        raw = await cache_get(_redis_key(key_id))
        if raw is None:
            return {"status": "none"}
        try:
            state = json.loads(raw)
        except Exception:
            await cache_delete(_redis_key(key_id))
            return {"status": "none"}
        if state.get("status") != "pending" or not poll:
            return self._public_state(state)

        now = time.time()
        if float(state.get("expires_at_epoch") or 0) <= now:
            return self._public_state(await self._settle(key_id, state, "expired"))
        if now < float(state.get("next_poll_epoch") or 0):
            return self._public_state(state)

        try:
            status, body, text = await _post_form(
                XAI_TOKEN_URL,
                {
                    "grant_type": XAI_DEVICE_CODE_GRANT,
                    "client_id": XAI_CLIENT_ID,
                    "device_code": str(state.get("device_code") or ""),
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("xAI device poll transport error key=%s: %s", key_id, exc)
            state["next_poll_epoch"] = now + float(state.get("interval_sec") or DEVICE_DEFAULT_INTERVAL_SEC)
            await cache_set(_redis_key(key_id), json.dumps(state), ttl_sec=_TERMINAL_STATE_TTL_SEC)
            return self._public_state(state)

        if status == 200 and body.get("access_token"):
            await self._store_tokens(key_id, body, principal=principal)
            await cache_delete(_redis_key(key_id))
            return {"status": "authorized"}

        err = str(body.get("error") or "")
        interval = max(DEVICE_MIN_INTERVAL_SEC, int(state.get("interval_sec") or DEVICE_DEFAULT_INTERVAL_SEC))
        if err == "authorization_pending":
            state["next_poll_epoch"] = now + interval
            await cache_set(_redis_key(key_id), json.dumps(state), ttl_sec=_TERMINAL_STATE_TTL_SEC)
            return self._public_state(state)
        if err == "slow_down":
            state["interval_sec"] = interval + DEVICE_SLOW_DOWN_INCREMENT_SEC
            state["next_poll_epoch"] = now + state["interval_sec"]
            await cache_set(_redis_key(key_id), json.dumps(state), ttl_sec=_TERMINAL_STATE_TTL_SEC)
            return self._public_state(state)
        if err in {"access_denied", "authorization_denied"}:
            return self._public_state(await self._settle(key_id, state, "denied"))
        if err == "expired_token":
            return self._public_state(await self._settle(key_id, state, "expired"))
        state["error"] = err or f"token exchange failed ({status}): {text[:200]}"
        return self._public_state(await self._settle(key_id, state, "error"))

    async def cancel_device_flow(self, key_id: str) -> None:
        await cache_delete(_redis_key(key_id))

    async def _settle(self, key_id: str, state: dict[str, Any], status: str) -> dict[str, Any]:
        state["status"] = status
        await cache_set(_redis_key(key_id), json.dumps(state), ttl_sec=_TERMINAL_STATE_TTL_SEC)
        return state

    @staticmethod
    def _public_state(state: dict[str, Any]) -> dict[str, Any]:
        """Стейт без device_code (он не должен покидать сервер)."""
        return {
            "status": state.get("status"),
            "user_code": state.get("user_code"),
            "verification_uri": state.get("verification_uri"),
            "verification_uri_complete": state.get("verification_uri_complete"),
            "interval_sec": state.get("interval_sec"),
            "error": state.get("error"),
        }

    # ------------------------------------------------------------- токены

    async def _store_tokens(
        self,
        key_id: str,
        tokens: dict[str, Any],
        *,
        principal: Principal,
    ) -> None:
        blob = token_blob(
            access_token=str(tokens.get("access_token") or ""),
            refresh_token=str(tokens.get("refresh_token") or ""),
            id_token=tokens.get("id_token") if isinstance(tokens.get("id_token"), str) else None,
            expires_in=tokens.get("expires_in") if isinstance(tokens.get("expires_in"), int | float) else None,
        )
        row = await self._require_xai_key(key_id)
        ref = self._secrets.put(key_id, blob)
        if not (row.secret_ref or "").strip():
            row.secret_ref = ref
        # первичная авторизация активирует ключ (это не ручной activate:
        # секрет только что появился)
        row.status = KeyStatus.ACTIVE.value
        await self._session.commit()
        await self._audit.record(
            event_type="ai_key.oauth_authorized",
            key_id=key_id,
            principal=principal,
            detail={"provider": "xai", "flow": "device_code"},
        )

    async def ensure_fresh_access_token(self, row: AiProviderKeyRow, raw_secret: str) -> str:
        """Свежий access_token для lease/probe (вызывается из resolve_secret_for_key).

        Не-блоб (legacy ручной секрет) возвращается как есть — ключ продолжает
        работать статическим токеном.
        """
        blob = parse_token_blob(raw_secret)
        if blob is None:
            return raw_secret
        expires_at = _parse_iso(blob.get("expires_at"))
        now = datetime.now(tz=UTC)
        if expires_at is not None and expires_at - timedelta(seconds=ACCESS_TOKEN_REFRESH_SKEW_SEC) > now:
            return str(blob["access_token"])

        lock = _refresh_locks.setdefault(str(row.id), asyncio.Lock())
        async with lock:
            # double-check после single-flight: другой send уже обновил токен
            refreshed_raw = self._secrets.get(row.secret_ref)
            refreshed = parse_token_blob(refreshed_raw)
            if refreshed is not None:
                refreshed_expiry = _parse_iso(refreshed.get("expires_at"))
                if (
                    refreshed_expiry is not None
                    and refreshed_expiry - timedelta(seconds=ACCESS_TOKEN_REFRESH_SKEW_SEC) > now
                ):
                    return str(refreshed["access_token"])
                blob = refreshed

            refresh_token = str(blob.get("refresh_token") or "")
            if not refresh_token:
                raise self._reauth_required("refresh token missing")
            try:
                status, body, text = await _post_form(
                    XAI_TOKEN_URL,
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": XAI_CLIENT_ID,
                    },
                )
            except httpx.HTTPError as exc:
                if expires_at is not None and expires_at > now:
                    logger.warning("xAI refresh transport error, serving unexpired token key=%s: %s", row.id, exc)
                    return str(blob["access_token"])
                raise AppError(
                    code="OAUTH_REFRESH_FAILED",
                    title="xAI token refresh failed",
                    status=502,
                    detail=f"refresh request failed: {exc}",
                ) from exc

            if status == 200 and body.get("access_token"):
                new_blob = token_blob(
                    access_token=str(body["access_token"]),
                    # refresh_token ротацией: пустой ответ = старый ещё жив
                    refresh_token=str(body.get("refresh_token") or "") or refresh_token,
                    id_token=body.get("id_token") if isinstance(body.get("id_token"), str) else blob.get("id_token"),
                    expires_in=body.get("expires_in") if isinstance(body.get("expires_in"), int | float) else None,
                )
                self._secrets.put(str(row.id), new_blob)
                return str(json.loads(new_blob)["access_token"])

            oauth_error = str(body.get("error") or "")
            if oauth_error == "invalid_grant" or status in (400, 401):
                await self._disable_key(row, reason=oauth_error or f"refresh rejected ({status})")
                raise self._reauth_required(f"refresh rejected: {oauth_error or status} {text[:200]}")

            if expires_at is not None and expires_at > now:
                # транзиентная ошибка (429/5xx) и токен ещё жив — отдаём старый
                logger.warning("xAI refresh transient failure key=%s status=%s", row.id, status)
                return str(blob["access_token"])
            raise AppError(
                code="OAUTH_REFRESH_FAILED",
                title="xAI token refresh failed",
                status=502,
                detail=f"refresh failed ({status}): {text[:200]}",
            )

    async def _disable_key(self, row: AiProviderKeyRow, *, reason: str) -> None:
        row.status = KeyStatus.DISABLED.value
        await self._session.commit()
        await self._audit.record(
            event_type="ai_key.oauth_revoked",
            key_id=str(row.id),
            principal=Principal(sub="system:xai-oauth"),
            detail={"provider": "xai", "reason": reason[:300]},
        )

    @staticmethod
    def _reauth_required(detail: str) -> AppError:
        return AppError(
            code="OAUTH_REAUTH_REQUIRED",
            title="Grok authorization expired",
            status=409,
            detail=f"xAI session is no longer valid ({detail}); re-authorize the key",
        )

    # ------------------------------------------------------------- прочее

    async def _require_xai_key(self, key_id: str) -> AiProviderKeyRow:
        row = await self._session.get(AiProviderKeyRow, key_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="AI key not found")
        if row.api_kind != ApiKind.XAI_OAUTH.value:
            raise AppError(
                code="OAUTH_NOT_SUPPORTED",
                title="OAuth not supported",
                status=422,
                detail="key is not an xAI (Grok) OAuth key",
            )
        return row
