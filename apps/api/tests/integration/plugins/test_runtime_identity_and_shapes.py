from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest
import yaml

from prodavan.api.v1.plugin_admin import EffectiveQuotasResponse, QuotaSetBody
from prodavan.application.plugins.admin.contracts import (
    QUOTA_KEYS,
    PluginPublicationApproval,
    QuotaSet,
)
from prodavan.application.plugins.admin.ports import PublicationApprovalReaderPort
from prodavan.application.plugins.gateway.ports import DeliveryStatus, RateLimitPort
from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
    SqlPluginPublicationApprovalRepository,
)
from prodavan.infrastructure.plugins.adapters.gateway_adapters import RedisRateLimitAdapter
from prodavan.infrastructure.plugins.runtime.token_provider import (
    LIFECYCLE_SERVICE_ACCOUNT,
    MAX_EXPIRATION_SECONDS,
    TOKEN_AUDIENCE,
    ServiceAccountTokenProvider,
)

from .production_boundaries import NOW, StatefulK8sApi, StatefulRedisBoundary
from .test_production_composition import composition_world

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_token_request_has_exact_scoped_identity_and_singleflight_cache() -> None:
    api = StatefulK8sApi(scoped_identity=True)
    assert api.scoped_identity is True
    provider = ServiceAccountTokenProvider(
        namespace="agentscale-dev",
        api=api,
        clock=lambda: NOW,
    )
    first = await provider.get_token()
    second = await provider.get_token()
    assert first == second == "scoped-lifecycle-token-test"
    assert len(api.store.token_requests) == 1
    request = api.store.token_requests[0]
    assert request == {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenRequest",
        "spec": {
            "audiences": [TOKEN_AUDIENCE],
            "expirationSeconds": MAX_EXPIRATION_SECONDS,
        },
    }
    assert api.calls[0][2].endswith(
        f"/serviceaccounts/{LIFECYCLE_SERVICE_ACCOUNT}/token"
    )
    await provider.aclose()
    await provider.aclose()


def test_dev_lifecycle_rbac_is_scoped_and_token_request_is_separate() -> None:
    root = Path(__file__).resolve().parents[5]
    lifecycle_path = (
        root
        / "infra/k3s/overlays/dev/plugins/plugin-lifecycle-rbac.yaml"
    )
    token_path = (
        root
        / "infra/k3s/overlays/dev/plugins/plugin-deployment-admin-token-request-rbac.yaml"
    )
    lifecycle = list(yaml.safe_load_all(lifecycle_path.read_text(encoding="utf-8")))
    token = list(yaml.safe_load_all(token_path.read_text(encoding="utf-8")))
    service_account = next(item for item in lifecycle if item["kind"] == "ServiceAccount")
    assert service_account["metadata"]["name"] == LIFECYCLE_SERVICE_ACCOUNT
    assert service_account["automountServiceAccountToken"] is False

    lifecycle_role = next(item for item in lifecycle if item["kind"] == "Role")
    assert lifecycle_role["metadata"]["namespace"] == "agentscale-dev"
    allowed = {
        (group, resource, tuple(verbs))
        for group, resource, verbs in (
            ("apps", "deployments", ("get", "create", "patch", "delete")),
            ("apps", "deployments/scale", ("get", "patch")),
            ("", "services", ("get", "create", "patch", "delete")),
            ("", "services/proxy", ("get", "create")),
            ("", "secrets", ("get", "create", "patch", "delete")),
            ("", "configmaps", ("get", "patch")),
            ("", "configmaps", ("create",)),
            ("", "pods", ("list",)),
        )
    }
    actual = {
        (rule["apiGroups"][0], rule["resources"][0], tuple(rule["verbs"]))
        for rule in lifecycle_role["rules"]
    }
    assert actual == allowed
    configmap_rules = [
        rule for rule in lifecycle_role["rules"] if rule["resources"] == ["configmaps"]
    ]
    assert configmap_rules == [
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["plugin-activation-state"],
            "verbs": ["get", "patch"],
        },
        {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["create"]},
    ]
    assert "resourceNames" not in configmap_rules[1]
    assert all("resourceNames" not in rule or rule["resourceNames"] for rule in lifecycle_role["rules"])
    binding = next(item for item in lifecycle if item["kind"] == "RoleBinding")
    assert binding["subjects"] == [{
        "kind": "ServiceAccount",
        "name": LIFECYCLE_SERVICE_ACCOUNT,
        "namespace": "agentscale-dev",
    }]

    token_role = next(item for item in token if item["kind"] == "Role")
    assert token_role["rules"] == [{
        "apiGroups": [""],
        "resources": ["serviceaccounts/token"],
        "resourceNames": [LIFECYCLE_SERVICE_ACCOUNT],
        "verbs": ["create"],
    }]
    token_binding = next(item for item in token if item["kind"] == "RoleBinding")
    assert token_binding["subjects"] == [
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


@pytest.mark.asyncio
async def test_app_state_and_boundary_shapes_do_not_accept_stale_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=False, ci=False, platform=True) as world:
        runtime = world.app.state.plugin_gateway_runtime
        rate_limit = runtime.auth.rate_limit
        assert isinstance(rate_limit, RedisRateLimitAdapter)
        assert rate_limit._client is world.redis

        for method in ("allow", "claim_delivery_lease", "commit_delivery", "release_delivery"):
            assert inspect.signature(getattr(RateLimitPort, method)) == inspect.signature(
                getattr(RedisRateLimitAdapter, method)
            )
        assert not hasattr(world, "seed_active_grant")
        assert not hasattr(world, "seed_published")

        first = await rate_limit.claim_delivery_lease(
            key="composition-delivery",
            ttl_seconds=60,
            lease_seconds=5,
            token="first-body-token",
        )
        assert first.status is DeliveryStatus.FIRST
        processing = await rate_limit.claim_delivery_lease(
            key="composition-delivery",
            ttl_seconds=60,
            lease_seconds=5,
            token="first-body-token",
        )
        assert processing.status is DeliveryStatus.IN_PROGRESS
        assert processing.retry_after_seconds is not None
        committed = await rate_limit.commit_delivery(
            key="composition-delivery",
            ttl_seconds=60,
            token="first-body-token",
        )
        assert committed is True
        duplicate = await rate_limit.claim_delivery_lease(
            key="composition-delivery",
            ttl_seconds=60,
            lease_seconds=5,
            token="first-body-token",
        )
        assert duplicate.status is DeliveryStatus.DUPLICATE
        assert duplicate.previous_token == "first-body-token"

        await rate_limit.release_delivery(
            key="composition-delivery",
            token="first-body-token",
        )
        # Done is not a processing lease and therefore is not releasable.
        assert (await rate_limit.claim_delivery_lease(
            key="composition-delivery",
            ttl_seconds=60,
            lease_seconds=5,
            token="first-body-token",
        )).status is DeliveryStatus.DUPLICATE

        ports = world.app.state.plugin_admin_ports
        assert isinstance(
            ports.publication_approvals,
            SqlPluginPublicationApprovalRepository,
        )
        assert inspect.signature(PublicationApprovalReaderPort.get) == inspect.signature(
            SqlPluginPublicationApprovalRepository.get
        )
        assert not hasattr(ports.publication_approvals, "seed_active_grant")
        assert not hasattr(ports.publication_approvals, "seed_published")
        assert not hasattr(ports.publication_approvals, "get_active_grant")
        assert "status" not in dataclasses.fields(PluginPublicationApproval)
        assert "is_active" not in dataclasses.fields(PluginPublicationApproval)

        assert set(QUOTA_KEYS) == {field.name for field in dataclasses.fields(QuotaSet)}
        assert set(QUOTA_KEYS) == set(EffectiveQuotasResponse.model_fields)
        assert set(QUOTA_KEYS) == set(QuotaSetBody.model_fields)


@pytest.mark.asyncio
async def test_release_processing_lease_allows_retry_but_done_is_not_released() -> None:
    redis = StatefulRedisBoundary()
    adapter = RedisRateLimitAdapter(redis)
    key = "retry-delivery"
    assert (await adapter.claim_delivery_lease(
        key=key,
        ttl_seconds=60,
        lease_seconds=5,
        token="body-token",
    )).status is DeliveryStatus.FIRST
    await adapter.release_delivery(key=key, token="body-token")
    assert (await adapter.claim_delivery_lease(
        key=key,
        ttl_seconds=60,
        lease_seconds=5,
        token="body-token",
    )).status is DeliveryStatus.FIRST
    assert await adapter.commit_delivery(key=key, ttl_seconds=60, token="body-token")
    await adapter.release_delivery(key=key, token="body-token")
    assert (await adapter.claim_delivery_lease(
        key=key,
        ttl_seconds=60,
        lease_seconds=5,
        token="body-token",
    )).status is DeliveryStatus.DUPLICATE
