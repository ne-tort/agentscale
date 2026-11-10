"""Real-cluster TokenRequest acceptance for the scoped plugin lifecycle identity.

Covers: API/Gateway may mint ONLY for plugin-deployment-admin, audience/TTL
(<=3600s), caching + proactive refresh, sanitized failures with retry, shutdown
semantics, unrelated ServiceAccount denial, and the proof that the lifecycle
data path never reads a mounted API token (NoMountedTokenAuth raises on any
such attempt while real scoped requests succeed).

Every negative RBAC request runs on an OPEN broker (a closed client raises
K8sApiUnavailable, which is never accepted as an RBAC 403). Tokens themselves
never appear in assertion output: comparisons go through safe helpers whose
failure messages carry only stable codes. No kubectl, no SDK, no Postgres.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiClient,
    K8sApiError,
    K8sApiUnavailable,
)
from prodavan.infrastructure.plugins.runtime.token_provider import (
    TOKEN_AUDIENCE,
    ServiceAccountTokenProvider,
    TokenProviderClosedError,
    TokenProviderConfigurationError,
    TokenRequestError,
)

from .cluster import (
    ADMIN_SA,
    CONTROL_PLANE_SAS,
    UNRELATED_SA,
    AcceptanceCluster,
)
from .kubeconfig import NoMountedTokenAuth, StaticTokenAuth
from .lifecycle import (
    assert_no_sensitive_material,
    assert_same_token,
    assert_token_rotated,
)

CLUSTER_TAG = "token"

pytestmark = pytest.mark.plugin_acceptance


def _claims(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_aud": False,
            "verify_iat": False,
            "verify_nbf": False,
            "verify_iss": False,
            "verify_sub": False,
            "verify_jti": False,
        },
    )


def _assert_scoped_claims(cluster: AcceptanceCluster, token: str) -> None:
    # Claims are non-secret metadata (audience, timestamps, SA identity); the
    # token value itself is never asserted on or printed.
    claims = _claims(token)
    audiences = claims.get("aud")
    assert audiences == [TOKEN_AUDIENCE], "TokenRequest audience must be exactly the API audience"
    issued_at = int(claims["iat"])
    expires_at = int(claims["exp"])
    assert 0 < expires_at - issued_at <= 3600, "scoped token TTL must be <= 3600 seconds"
    assert claims.get("sub") == f"system:serviceaccount:{cluster.namespace}:{ADMIN_SA}"
    kubernetes = claims.get("kubernetes.io") or {}
    assert kubernetes.get("namespace") == cluster.namespace
    service_account = kubernetes.get("serviceaccount") or {}
    assert service_account.get("name") == ADMIN_SA


def _token_request_body(seconds: int = 600) -> dict[str, Any]:
    return {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenRequest",
        "spec": {"audiences": [TOKEN_AUDIENCE], "expirationSeconds": seconds},
    }


def _assert_forbidden(error: K8sApiError) -> None:
    """Accept ONLY a real API-server 403, never a closed/client-side failure."""
    if "closed" in error.reason.lower():
        raise AssertionError("a closed transport must never count as an RBAC denial")
    if type(error) is not K8sApiError or "403" not in error.reason:
        raise AssertionError(
            f"expected a real RBAC 403, got {type(error).__name__}: {error.reason}"
        )


class _RecordingApi:
    """Delegates to a real client and records only method + path."""

    def __init__(self, api: Any) -> None:
        self._api = api
        self.paths: list[str] = []
        self.mints = 0

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.paths.append(f"{method.upper()} {path}")
        self.mints += 1
        return await self._api.request(method, path, **kwargs)

    async def aclose(self) -> None:
        await self._api.aclose()


async def test_control_plane_mints_only_for_lifecycle_admin(cluster: AcceptanceCluster) -> None:
    # A named read target: the lifecycle Role grants secrets get (not list).
    async with cluster.bootstrap() as bootstrap:
        await bootstrap.request(
            "POST",
            cluster.path("secrets"),
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "acceptance-token-probe", "namespace": cluster.namespace},
                "type": "Opaque",
                "data": {"probe": "cHJvYmU="},
            },
        )
    for service_account in CONTROL_PLANE_SAS:
        async with cluster.broker(service_account) as broker:
            recorder = _RecordingApi(broker)
            provider = ServiceAccountTokenProvider(
                namespace=cluster.namespace,
                api=recorder,
                expiration_seconds=cluster.config.token_ttl_seconds,
            )
            no_mounted = NoMountedTokenAuth(cluster.identity)
            data_path = K8sApiClient(
                auth=no_mounted,
                token_provider=provider,
                timeout_seconds=cluster.config.api_timeout_seconds,
            )
            try:
                token = await provider.get_token()
                _assert_scoped_claims(cluster, token)
                # Real authenticated data-path call with the scoped identity.
                response = await data_path.request(
                    "GET", cluster.path("secrets", "acceptance-token-probe")
                )
                assert response.body.get("kind") == "Secret"
                assert no_mounted.mounted_token_reads == 0
                # The broker was used for TokenRequest and for nothing else.
                assert recorder.paths == [f"POST {provider.token_path}"]

                # Negative RBAC requests BEFORE any shutdown: the broker is
                # still open, so these are real API-server 403 responses.
                with pytest.raises(K8sApiError) as other_sa:
                    await broker.request(
                        "POST",
                        cluster.path(f"serviceaccounts/{UNRELATED_SA}/token"),
                        body=_token_request_body(),
                        content_type="application/json",
                    )
                _assert_forbidden(other_sa.value)
                # The control plane has no lifecycle data-plane permissions.
                with pytest.raises(K8sApiError) as secrets_read:
                    await broker.request("GET", cluster.path("secrets"))
                _assert_forbidden(secrets_read.value)
            finally:
                await data_path.aclose()
                await provider.aclose()

            # Close semantics are checked SEPARATELY: a closed client fails
            # locally with K8sApiUnavailable, which is not an RBAC denial.
            with pytest.raises(K8sApiUnavailable):
                await broker.request("GET", cluster.path("secrets"))

    # An unrelated SA can neither mint for the lifecycle admin nor read Secrets.
    async with cluster.broker(UNRELATED_SA) as unrelated:
        with pytest.raises(K8sApiError) as mint_for_admin:
            await unrelated.request(
                "POST",
                cluster.path(f"serviceaccounts/{ADMIN_SA}/token"),
                body=_token_request_body(),
                content_type="application/json",
            )
        _assert_forbidden(mint_for_admin.value)
        with pytest.raises(K8sApiError) as unrelated_read:
            await unrelated.request("GET", cluster.path("secrets"))
        _assert_forbidden(unrelated_read.value)

    # The powerful lifecycle identity itself has no TokenRequest permission.
    async with cluster.lifecycle(CONTROL_PLANE_SAS[0]) as transport:
        admin_token = await transport.provider.get_token()
    admin_api = K8sApiClient(
        auth=StaticTokenAuth(cluster.identity, admin_token),
        timeout_seconds=cluster.config.api_timeout_seconds,
    )
    try:
        with pytest.raises(K8sApiError) as admin_mint:
            await admin_api.request(
                "POST",
                cluster.path(f"serviceaccounts/{ADMIN_SA}/token"),
                body=_token_request_body(),
                content_type="application/json",
            )
        _assert_forbidden(admin_mint.value)
    finally:
        await admin_api.aclose()


async def test_provider_rejects_over_one_hour_and_foreign_identity(cluster: AcceptanceCluster) -> None:
    with pytest.raises(TokenProviderConfigurationError):
        ServiceAccountTokenProvider(namespace=cluster.namespace, expiration_seconds=3601)
    with pytest.raises(TokenProviderConfigurationError):
        ServiceAccountTokenProvider(namespace=cluster.namespace, service_account=UNRELATED_SA)
    with pytest.raises(TokenProviderConfigurationError):
        ServiceAccountTokenProvider(namespace=cluster.namespace, expiration_seconds=0)
    async with cluster.lifecycle(CONTROL_PLANE_SAS[0], expiration_seconds=3600) as transport:
        token = await transport.provider.get_token()
        _assert_scoped_claims(cluster, token)
        claims = _claims(token)
        assert int(claims["exp"]) - int(claims["iat"]) <= 3600


async def test_refresh_mints_a_new_scoped_token(cluster: AcceptanceCluster) -> None:
    now = [datetime.now(UTC)]

    def clock() -> datetime:
        return now[0]

    async with cluster.broker(CONTROL_PLANE_SAS[0]) as broker:
        recorder = _RecordingApi(broker)
        provider = ServiceAccountTokenProvider(
            namespace=cluster.namespace,
            api=recorder,
            expiration_seconds=600,
            refresh_before_seconds=60.0,
            clock=clock,
        )
        try:
            first = await provider.get_token()
            assert_same_token(await provider.get_token(), first, code="cache-hit-initial")
            assert recorder.mints == 1, "a cached token must not trigger another TokenRequest"
            now[0] = now[0] + timedelta(seconds=120)
            assert_same_token(await provider.get_token(), first, code="cache-before-refresh-at")
            assert recorder.mints == 1
            # Past refresh_at, but still inside the 600s TTL: the second real
            # TokenRequest must return a token valid at this fake-clock instant.
            now[0] = now[0] + timedelta(seconds=450)
            refreshed = await provider.get_token()
            assert_token_rotated(first, refreshed, code="proactive-refresh")
            assert recorder.mints == 2
            _assert_scoped_claims(cluster, first)
            _assert_scoped_claims(cluster, refreshed)
            refreshed_claims = _claims(refreshed)
            assert 0 < int(refreshed_claims["exp"]) - int(refreshed_claims["iat"]) <= 600
            assert int(refreshed_claims["exp"]) > int(now[0].timestamp())
            assert_same_token(await provider.get_token(), refreshed, code="cache-after-refresh")
            assert recorder.mints == 2
        finally:
            await provider.aclose()


async def test_failure_is_sanitized_and_retry_succeeds(cluster: AcceptanceCluster) -> None:
    class FlakyApi:
        def __init__(self, api: Any) -> None:
            self._api = api
            self.calls = 0

        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise K8sApiUnavailable("injected broker outage")
            return await self._api.request(method, path, **kwargs)

        async def aclose(self) -> None:
            await self._api.aclose()

    async with cluster.broker(CONTROL_PLANE_SAS[0]) as broker:
        provider = ServiceAccountTokenProvider(
            namespace=cluster.namespace, api=FlakyApi(broker), expiration_seconds=600
        )
        try:
            with pytest.raises(TokenRequestError) as failure:
                await provider.get_token()
            assert "injected broker outage" not in str(failure.value)
            token = await provider.get_token()
            _assert_scoped_claims(cluster, token)
        finally:
            await provider.aclose()
        with pytest.raises(TokenProviderClosedError):
            await provider.get_token()

    # Concurrent callers share one sanitized failure; nothing token-shaped leaks.
    async with cluster.broker(UNRELATED_SA) as unrelated:
        provider = ServiceAccountTokenProvider(
            namespace=cluster.namespace, api=unrelated, expiration_seconds=600
        )
        try:
            results = await asyncio.gather(
                *(provider.get_token() for _ in range(4)), return_exceptions=True
            )
            assert all(isinstance(item, TokenRequestError) for item in results)
            for index, item in enumerate(results):
                assert_no_sensitive_material(
                    str(item), cluster.tokens[UNRELATED_SA], code=f"tokenrequest-error-{index}"
                )
        finally:
            await provider.aclose()


async def test_shutdown_cancels_inflight_refresh_and_transport(cluster: AcceptanceCluster) -> None:
    class SlowApi:
        def __init__(self, api: Any) -> None:
            self._api = api
            self.started = asyncio.Event()

        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            self.started.set()
            await asyncio.sleep(30)
            return await self._api.request(method, path, **kwargs)

        async def aclose(self) -> None:
            await self._api.aclose()

    async with cluster.broker(CONTROL_PLANE_SAS[0]) as broker:
        slow = SlowApi(broker)
        provider = ServiceAccountTokenProvider(namespace=cluster.namespace, api=slow, expiration_seconds=600)
        pending = asyncio.create_task(provider.get_token())
        await asyncio.wait_for(slow.started.wait(), timeout=10)
        await provider.aclose()
        try:
            await pending
        except (TokenProviderClosedError, asyncio.CancelledError):
            pass
        else:
            raise AssertionError("in-flight mint must not complete after provider shutdown")
        assert pending.done()
        with pytest.raises(TokenProviderClosedError):
            await provider.get_token()
        await provider.aclose()  # idempotent

    async with cluster.lifecycle(CONTROL_PLANE_SAS[1]) as transport:
        token = await transport.provider.get_token()
        _assert_scoped_claims(cluster, token)
        await transport.provider.aclose()
        with pytest.raises(K8sApiUnavailable):
            await transport.api.request("GET", cluster.path("secrets"))
        await transport.api.aclose()
        with pytest.raises(K8sApiUnavailable):
            await transport.api.request("GET", cluster.path("secrets"))
