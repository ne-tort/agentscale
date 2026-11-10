"""TokenRequest identity and lifecycle REST authentication tests (not run: freeze)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiClient,
    K8sRawResponse,
)
from prodavan.infrastructure.plugins.runtime.token_provider import (
    TOKEN_AUDIENCE,
    ServiceAccountTokenProvider,
    TokenProviderClosedError,
    TokenProviderConfigurationError,
    TokenRequestError,
)

NAMESPACE = "agentscale-dev"
TOKEN_PATH = (
    "/api/v1/namespaces/agentscale-dev/serviceaccounts/"
    "plugin-deployment-admin/token"
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class TokenApi:
    def __init__(self, clock: Clock, *, ttl_seconds: int = 3600) -> None:
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.calls: list[dict[str, Any]] = []
        self.tokens = ["first-token", "second-token"]
        self.error: Exception | None = None
        self.close_calls = 0

    async def request(self, method: str, path: str, **kwargs: Any) -> K8sRawResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self.error is not None:
            raise self.error
        index = min(len(self.calls) - 1, len(self.tokens) - 1)
        expiry = self.clock.now + timedelta(seconds=self.ttl_seconds)
        return K8sRawResponse(
            status_code=201,
            body={
                "kind": "TokenRequest",
                "status": {
                    "token": self.tokens[index],
                    "expirationTimestamp": expiry.isoformat().replace("+00:00", "Z"),
                },
            },
        )

    async def aclose(self) -> None:
        self.close_calls += 1


async def test_token_request_payload_cache_and_proactive_refresh() -> None:
    clock = Clock()
    api = TokenApi(clock, ttl_seconds=60)
    provider = ServiceAccountTokenProvider(
        namespace=NAMESPACE,
        api=api,
        expiration_seconds=60,
        refresh_before_seconds=10,
        clock=clock,
    )

    assert await provider.get_token() == "first-token"
    assert api.calls == [
        {
            "method": "POST",
            "path": TOKEN_PATH,
            "body": {
                "apiVersion": "authentication.k8s.io/v1",
                "kind": "TokenRequest",
                "spec": {
                    "audiences": [TOKEN_AUDIENCE],
                    "expirationSeconds": 60,
                },
            },
            "content_type": "application/json",
        }
    ]

    clock.advance(49.9)
    assert await provider.get_token() == "first-token"
    assert len(api.calls) == 1
    clock.advance(0.1)
    assert await provider.get_token() == "second-token"
    assert len(api.calls) == 2


async def test_default_token_request_is_audience_bound_and_capped() -> None:
    clock = Clock()
    api = TokenApi(clock)
    provider = ServiceAccountTokenProvider(
        namespace=NAMESPACE, api=api, clock=clock
    )
    await provider.get_token()
    spec = api.calls[0]["body"]["spec"]
    assert spec == {
        "audiences": [TOKEN_AUDIENCE],
        "expirationSeconds": 3600,
    }
    with pytest.raises(TokenProviderConfigurationError):
        ServiceAccountTokenProvider(
            namespace=NAMESPACE, api=api, expiration_seconds=3601
        )
    with pytest.raises(TokenProviderConfigurationError):
        ServiceAccountTokenProvider(
            namespace=NAMESPACE, api=api, service_account="another-sa"
        )


async def test_cache_miss_is_singleflight() -> None:
    clock = Clock()

    class BlockingApi(TokenApi):
        def __init__(self) -> None:
            super().__init__(clock)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, method: str, path: str, **kwargs: Any) -> K8sRawResponse:
            self.started.set()
            await self.release.wait()
            return await super().request(method, path, **kwargs)

    api = BlockingApi()
    provider = ServiceAccountTokenProvider(namespace=NAMESPACE, api=api, clock=clock)
    pending = [asyncio.create_task(provider.get_token()) for _ in range(8)]
    await api.started.wait()
    api.release.set()
    assert set(await asyncio.gather(*pending)) == {"first-token"}
    assert len(api.calls) == 1


async def test_token_request_errors_and_invalid_responses_are_sanitized() -> None:
    clock = Clock()
    api = TokenApi(clock)
    api.error = RuntimeError("Bearer mounted-secret must not escape")
    provider = ServiceAccountTokenProvider(namespace=NAMESPACE, api=api, clock=clock)
    with pytest.raises(TokenRequestError) as caught:
        await provider.get_token()
    assert "mounted-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True

    class InvalidApi(TokenApi):
        async def request(self, method: str, path: str, **kwargs: Any) -> K8sRawResponse:
            return K8sRawResponse(
                status_code=201,
                body={"status": {"token": "unsafe\nToken", "expirationTimestamp": "bad"}},
            )

    invalid = ServiceAccountTokenProvider(
        namespace=NAMESPACE, api=InvalidApi(clock), clock=clock
    )
    with pytest.raises(TokenRequestError) as invalid_caught:
        await invalid.get_token()
    assert "unsafe" not in str(invalid_caught.value)
    assert "TokenRequest returned an invalid identity" in str(invalid_caught.value)


async def test_provider_close_drops_cache_and_closes_broker_once() -> None:
    clock = Clock()
    api = TokenApi(clock)
    provider = ServiceAccountTokenProvider(namespace=NAMESPACE, api=api, clock=clock)
    await provider.get_token()
    assert provider._cached is not None
    await provider.aclose()
    await provider.aclose()
    assert api.close_calls == 1
    assert provider._cached is None
    with pytest.raises(TokenProviderClosedError):
        await provider.get_token()


async def test_lifecycle_client_uses_only_short_lived_bearer_and_closes_once() -> None:
    class ClusterOnlyAuth:
        def api_base(self) -> str:
            return "https://kubernetes.default.svc"

        def available(self) -> bool:
            raise AssertionError("mounted token availability must not be consulted")

        def headers(self) -> dict[str, str]:
            raise AssertionError("mounted/static token headers must not be read")

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def get_token(self) -> str:
            self.calls += 1
            return "short-lived-lifecycle-token"

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"kind": "Status", "status": "Success"}

    class HttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.close_calls = 0

        async def request(self, method: str, url: str, **kwargs: Any) -> Response:
            self.calls.append({"method": method, "url": url, **kwargs})
            return Response()

        async def aclose(self) -> None:
            self.close_calls += 1

    provider = Provider()
    http = HttpClient()
    client = K8sApiClient(
        auth=ClusterOnlyAuth(),  # type: ignore[arg-type]
        client=http,
        token_provider=provider,
    )
    await client.request("GET", "/api/v1/namespaces/agentscale-dev/secrets/example")
    assert provider.calls == 1
    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer short-lived-lifecycle-token"
    assert "mounted" not in str(headers)
    await client.aclose()
    await client.aclose()
    assert http.close_calls == 1


def _manifest(name: str) -> list[dict[str, Any]]:
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "infra/k3s/overlays/dev/plugins" / name).is_file()
    )
    path = root / "infra/k3s/overlays/dev/plugins" / name
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]


def test_dev_main_and_gateway_identities_have_exact_token_request_only_role() -> None:
    documents = _manifest("plugin-deployment-admin-token-request-rbac.yaml")
    role = next(item for item in documents if item["kind"] == "Role")
    binding = next(item for item in documents if item["kind"] == "RoleBinding")
    assert role["rules"] == [
        {
            "apiGroups": [""],
            "resources": ["serviceaccounts/token"],
            "resourceNames": ["plugin-deployment-admin"],
            "verbs": ["create"],
        }
    ]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "agentscale-api",
            "namespace": "agentscale-dev",
        },
        {
            "kind": "ServiceAccount",
            "name": "plugin-gateway",
            "namespace": "agentscale-dev",
        },
    ]
    subject_names = {subject["name"] for subject in binding["subjects"]}
    assert {
        "plugin-runtime",
        "agentscale-project-pod",
        "plugin-deployment-admin",
        "unrelated-service-account",
    }.isdisjoint(subject_names)


def test_dev_lifecycle_role_is_bound_only_to_dedicated_non_automounted_sa() -> None:
    documents = _manifest("plugin-lifecycle-rbac.yaml")
    account = next(item for item in documents if item["kind"] == "ServiceAccount")
    role = next(item for item in documents if item["kind"] == "Role")
    binding = next(item for item in documents if item["kind"] == "RoleBinding")
    assert account["metadata"]["name"] == "plugin-deployment-admin"
    assert account["automountServiceAccountToken"] is False
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "plugin-deployment-admin",
            "namespace": "agentscale-dev",
        }
    ]
    assert role["metadata"]["name"] == "plugin-deployment-admin"
    assert role["rules"] == [
        {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "create", "patch", "delete"]},
        {"apiGroups": ["apps"], "resources": ["deployments/scale"], "verbs": ["get", "patch"]},
        {"apiGroups": [""], "resources": ["services"], "verbs": ["get", "create", "patch", "delete"]},
        {"apiGroups": [""], "resources": ["services/proxy"], "verbs": ["get", "create"]},
        {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "create", "patch", "delete"]},
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["plugin-activation-state"],
            "verbs": ["get", "patch"],
        },
        {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["create"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
    ]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert "serviceaccounts/token" not in str(role["rules"])
    assert binding["subjects"][0]["name"] != "agentscale-api"


def test_dev_overlays_share_explicit_internal_gateway_url_and_register_patch() -> None:
    expected = "http://plugin-gateway.agentscale-dev.svc:8080"
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "infra/k3s/overlays/dev/patch-dev-config.yaml").is_file()
    )
    api_patch = yaml.safe_load(
        (root / "infra/k3s/overlays/dev/patch-dev-config.yaml").read_text(
            encoding="utf-8"
        )
    )
    gateway_patch = _manifest("patch-plugin-gateway-config.yaml")[0]
    kustomization = _manifest("kustomization.yaml")[0]
    assert api_patch["data"]["PLUGIN_RUNTIME_GATEWAY_URL"] == expected
    assert gateway_patch["data"]["PLUGIN_RUNTIME_GATEWAY_URL"] == expected
    assert gateway_patch["metadata"]["name"] == "plugin-gateway-config"
    assert {"path": "patch-plugin-gateway-config.yaml"} in kustomization["patches"]
