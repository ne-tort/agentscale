"""Short-lived Kubernetes ServiceAccount identity for plugin lifecycle calls.

The current Pod ServiceAccount is used *only* by the injected/default TokenRequest
broker client. DeploymentAdmin and SecretProjection receive a different
K8sApiClient configured with this provider; mounted/static lifecycle tokens are
never accepted here. Tokens live only in a private, repr-redacted cache.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .k8s_scale import CORE_API, K8sApiClient, K8sApiPort

#: Kubernetes API server audience for the standard kubeadm/k3s issuer.
#: A bare service DNS name is NOT a valid API audience: the resulting token is
#: unusable for API calls. The real K8s acceptance caught this as a 401 once
#: administrator TLS identity stopped shadowing the Bearer identity.
TOKEN_AUDIENCE = "https://kubernetes.default.svc.cluster.local"
LIFECYCLE_SERVICE_ACCOUNT = "plugin-deployment-admin"
MAX_EXPIRATION_SECONDS = 3600
DEFAULT_REFRESH_BEFORE_SECONDS = 300.0
_MAX_TOKEN_LENGTH = 16384
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class ServiceAccountTokenError(RuntimeError):
    """Base class for deliberately sanitized token identity failures."""


class TokenProviderConfigurationError(ServiceAccountTokenError):
    """The fixed lifecycle identity or refresh configuration is invalid."""


class TokenRequestError(ServiceAccountTokenError):
    """TokenRequest failed or returned an unsafe/incomplete identity."""


class TokenProviderClosedError(ServiceAccountTokenError):
    """A token was requested after provider shutdown."""


@runtime_checkable
class ServiceAccountTokenProviderPort(Protocol):
    async def get_token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _CachedToken:
    value: str = field(repr=False)
    refresh_at: datetime


class ServiceAccountTokenProvider:
    """Mint and cache one audience-bound TokenRequest identity.

    Refresh is proactive on access: a cached token stops being eligible before
    its server expiry. A shared in-flight task coalesces concurrent cache
    misses/refreshes, including failures. No refresh task survives shutdown.
    """

    def __init__(
        self,
        *,
        namespace: str,
        service_account: str = LIFECYCLE_SERVICE_ACCOUNT,
        api: K8sApiPort | None = None,
        expiration_seconds: int = MAX_EXPIRATION_SECONDS,
        refresh_before_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(namespace, str)
            or len(namespace) > 63
            or not _DNS_LABEL.fullmatch(namespace)
        ):
            raise TokenProviderConfigurationError("lifecycle namespace is invalid")
        if service_account != LIFECYCLE_SERVICE_ACCOUNT:
            raise TokenProviderConfigurationError("lifecycle ServiceAccount is not allowed")
        if (
            not isinstance(expiration_seconds, int)
            or isinstance(expiration_seconds, bool)
            or not 1 <= expiration_seconds <= MAX_EXPIRATION_SECONDS
        ):
            raise TokenProviderConfigurationError(
                "TokenRequest expiration must be between 1 and 3600 seconds"
            )
        if refresh_before_seconds is None:
            refresh_before = min(
                DEFAULT_REFRESH_BEFORE_SECONDS, expiration_seconds / 5
            )
        else:
            try:
                refresh_before = float(refresh_before_seconds)
            except (TypeError, ValueError):
                raise TokenProviderConfigurationError("token refresh skew is invalid") from None
        if (
            refresh_before != refresh_before
            or refresh_before < 0
            or refresh_before >= expiration_seconds
        ):
            raise TokenProviderConfigurationError(
                "token refresh skew must be non-negative and shorter than token TTL"
            )

        self._namespace = namespace
        self._service_account = service_account
        self._api: K8sApiPort = api or K8sApiClient()
        self._expiration_seconds = expiration_seconds
        self._refresh_before = refresh_before
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: _CachedToken | None = None
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[_CachedToken] | None = None
        self._closed = False

    @property
    def token_path(self) -> str:
        return (
            f"{CORE_API}/namespaces/{self._namespace}/serviceaccounts/"
            f"{self._service_account}/token"
        )

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception:
            raise TokenRequestError("lifecycle token clock failed") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise TokenRequestError("lifecycle token clock returned an invalid time")
        return value.astimezone(UTC)

    def _request_body(self) -> dict[str, Any]:
        return {
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenRequest",
            "spec": {
                "audiences": [TOKEN_AUDIENCE],
                "expirationSeconds": self._expiration_seconds,
            },
        }

    @staticmethod
    def _safe_token(value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= _MAX_TOKEN_LENGTH
            and all(33 <= ord(character) <= 126 for character in value)
        )

    def _parse_response(self, response: Any, now: datetime) -> _CachedToken:
        try:
            payload = response.body
            status = payload.get("status") if isinstance(payload, dict) else None
            token = status.get("token") if isinstance(status, dict) else None
            raw_expiry = (
                status.get("expirationTimestamp") if isinstance(status, dict) else None
            )
            if not self._safe_token(token) or not isinstance(raw_expiry, str):
                raise ValueError("incomplete TokenRequest")
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("naive TokenRequest expiry")
            expiry = expiry.astimezone(UTC)
            if expiry <= now:
                raise ValueError("expired TokenRequest")
        except (TypeError, ValueError, AttributeError):
            raise TokenRequestError(
                "Kubernetes TokenRequest returned an invalid identity"
            ) from None

        # Enforce the local one-hour ceiling even if an API server/proxy returns
        # a longer expiry than requested. A shorter server TTL is respected.
        local_ceiling = datetime.fromtimestamp(
            now.timestamp() + self._expiration_seconds, tz=UTC
        )
        effective_expiry = min(expiry, local_ceiling)
        actual_ttl = effective_expiry.timestamp() - now.timestamp()
        refresh_before = min(self._refresh_before, actual_ttl / 5)
        refresh_at = datetime.fromtimestamp(
            effective_expiry.timestamp() - refresh_before, tz=UTC
        )
        if refresh_at <= now:
            # Very short server-side TTLs must not turn into a tight refresh loop
            # or a token reused until expiry. Such a response is unusable.
            raise TokenRequestError("Kubernetes TokenRequest TTL is too short")
        return _CachedToken(value=token, refresh_at=refresh_at)

    async def _mint(self, now: datetime) -> _CachedToken:
        try:
            response = await self._api.request(
                "POST",
                self.token_path,
                body=self._request_body(),
                content_type="application/json",
            )
        except Exception:
            # API exceptions can contain request headers, response bodies or the
            # mounted API token. Neither their text nor their traceback escapes.
            raise TokenRequestError("Kubernetes TokenRequest failed") from None
        return self._parse_response(response, now)

    async def _refresh(self, now: datetime) -> _CachedToken:
        task = asyncio.current_task()
        try:
            minted = await self._mint(now)
            async with self._lock:
                if not self._closed:
                    self._cached = minted
            return minted
        finally:
            async with self._lock:
                if self._inflight is task:
                    self._inflight = None

    async def get_token(self) -> str:
        now = self._now()
        cached = self._cached
        if not self._closed and cached is not None and now < cached.refresh_at:
            return cached.value
        async with self._lock:
            now = self._now()
            if self._closed:
                raise TokenProviderClosedError("lifecycle token provider is closed")
            cached = self._cached
            if cached is not None and now < cached.refresh_at:
                return cached.value
            task = self._inflight
            if task is None:
                task = asyncio.create_task(self._refresh(now))
                self._inflight = task
        try:
            minted = await asyncio.shield(task)
        except asyncio.CancelledError:
            if self._closed:
                raise TokenProviderClosedError("lifecycle token provider is closed") from None
            raise
        if self._closed:
            raise TokenProviderClosedError("lifecycle token provider is closed")
        return minted.value

    async def aclose(self) -> None:
        """Drop the cached secret and close the TokenRequest transport once."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cached = None
            task = self._inflight
            if task is not None:
                task.cancel()
            close = getattr(self._api, "aclose", None)
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The shared TokenRequest error is already sanitized for callers.
                pass
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            raise ServiceAccountTokenError(
                "lifecycle TokenRequest transport shutdown failed"
            ) from None

    async def __aenter__(self) -> ServiceAccountTokenProvider:
        if self._closed:
            raise TokenProviderClosedError("lifecycle token provider is closed")
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


__all__ = [
    "DEFAULT_REFRESH_BEFORE_SECONDS",
    "LIFECYCLE_SERVICE_ACCOUNT",
    "MAX_EXPIRATION_SECONDS",
    "TOKEN_AUDIENCE",
    "ServiceAccountTokenError",
    "ServiceAccountTokenProvider",
    "ServiceAccountTokenProviderPort",
    "TokenProviderClosedError",
    "TokenProviderConfigurationError",
    "TokenRequestError",
]
