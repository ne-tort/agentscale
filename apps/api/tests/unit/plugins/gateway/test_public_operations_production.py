"""Production-adapter regressions for anonymous read-only public operations.

Tests are intentionally written, not executed, during the migration verification
freeze. No database, network, cluster, build or git operation is required.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
)
from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import (
    VerificationEvidence,
    manifest_fingerprint,
)
from prodavan.application.plugins.gateway.auth import (
    HEADER_DELIVERY,
    HEADER_EVENT,
    HEADER_SIGNATURE,
)
from prodavan.application.plugins.gateway.errors import GatewayError, GatewayErrorCode
from prodavan.application.plugins.gateway.ports import GatewayOperation
from prodavan.application.plugins.wiring.install import _gateway_grants
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
    GrantAdapter,
    GrantRecord,
    InMemoryGrantStore,
)

from .conftest import WEBHOOK_SECRET, build_harness

PLUGIN = "mod_equipment"
COMPANY = "co-123"
INSTANCE = "inst-alpha-1"
VERSION = "1.2.0"
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 11, 9, tzinfo=UTC)
PUBLIC_ROUTES = (
    "/api/greetings",
    "/api/greetings/private",
    "/api/greetings/create",
)


def _manifest() -> dict[str, Any]:
    return {
        "plugin_id": PLUGIN,
        "version": VERSION,
        "protocol": "plugin-rpc/1",
        "public_pages": list(PUBLIC_ROUTES),
        "webhooks": ["/webhook/events"],
        "rpc_operations": [
            {
                "capability": {"id": "hello.greetings", "version": "1.0"},
                "operation": "list",
                "http_method": "GET",
                "backend_path": "/api/greetings",
                "public": True,
            },
            {
                "capability": {"id": "hello.greetings", "version": "1.0"},
                "operation": "private",
                "http_method": "GET",
                "backend_path": "/api/greetings/private",
                "public": False,
            },
            {
                "capability": {"id": "hello.greetings", "version": "1.0"},
                "operation": "create",
                "http_method": "POST",
                "backend_path": "/api/greetings/create",
                "public": True,
            },
        ],
    }


def _artifact(
    *,
    manifest: dict[str, Any] | None = None,
    digest: str = DIGEST,
    status: AppLifecycleStatus = AppLifecycleStatus.PUBLISHED,
    outcome: RegistryOutcome = RegistryOutcome.APPLIED,
    quarantine: QuarantineEvidence | None = None,
    published_at: datetime | None = NOW,
) -> PluginArtifactRecord:
    signed = manifest or _manifest()
    fingerprint = manifest_fingerprint(signed)
    image_ref = f"ghcr.io/ne-tort/plugins/{PLUGIN}@{digest}"
    evidence = VerificationEvidence(
        image_ref=image_ref,
        artifact_digest=digest,
        manifest_fingerprint=fingerprint,
        identity="https://github.com/ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main",
        issuer="https://token.actions.githubusercontent.com",
        sbom_verified=True,
        provenance_verified=True,
        observed_at=NOW,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        seal=object(),
        scan_verified=True,
        csp_verified=True,
    )
    return PluginArtifactRecord(
        plugin_id=PLUGIN,
        version=VERSION,
        digest=digest,
        image_ref=image_ref,
        manifest=signed,
        manifest_fingerprint=fingerprint,
        status=status,
        outcome=outcome,
        quarantine=quarantine,
        published_at=published_at,
        created_at=NOW,
        verification=evidence,
        publication=PublicationMetadata(
            source_id="ne-tort/agentscale",
            commit_sha="a" * 40,
            backend_digest=OTHER_DIGEST,
            backend_image_ref=f"ghcr.io/ne-tort/plugins/{PLUGIN}-backend@{OTHER_DIGEST}",
            run_url="https://github.com/ne-tort/agentscale/actions/runs/42",
        ),
    )


class _Registry:
    def __init__(self, record: PluginArtifactRecord | None) -> None:
        self.record = record
        self.calls: list[tuple[str, str]] = []

    async def get_async(self, *, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        self.calls.append((plugin_id, version))
        return self.record


class _SqlStore:
    def __init__(self, record: CapabilityGrantRecord) -> None:
        self.record = record

    async def get(self, *, company_id: str, plugin_id: str) -> CapabilityGrantRecord:
        return self.record


def _sql_grant() -> CapabilityGrantRecord:
    return CapabilityGrantRecord(
        grant_id="grant-1",
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        manifest_version=VERSION,
        artifact_digest=DIGEST,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=GrantStatus.ACTIVE,
        created_at=NOW,
    )


def _grant_record() -> GrantRecord:
    return GrantRecord(
        grant_id="grant-1",
        plugin_id=PLUGIN,
        company_id=COMPANY,
        plugin_instance_id=INSTANCE,
        operations=frozenset({"read", "write"}),
        public_pages=frozenset(PUBLIC_ROUTES),
        artifact_digest=DIGEST,
        manifest_version=VERSION,
    )


def _production_adapter(
    artifact: PluginArtifactRecord | None = None,
) -> GrantAdapter:
    adapter = _gateway_grants(
        SimpleNamespace(grant_store=_SqlStore(_sql_grant())),
        {"artifact_registry": _Registry(artifact or _artifact())},
    )
    assert isinstance(adapter, GrantAdapter)
    return adapter


def _production_harness():
    harness = build_harness()
    harness.auth.grants = _production_adapter()
    return harness


def _production_static_harness(
    artifact: PluginArtifactRecord | None,
    *,
    grant_record: GrantRecord | None = None,
):
    harness = build_harness()
    record = grant_record or replace(
        _grant_record(), public_pages=frozenset({"/public/info"})
    )
    harness.auth.grants = GrantAdapter(
        InMemoryGrantStore([record]), artifact_registry=_Registry(artifact)
    )
    return harness


@pytest.mark.asyncio
async def test_active_grant_binds_static_digest_to_published_bundle_not_backend() -> None:
    adapter = _production_adapter()

    grant = await adapter.active_grant(plugin_id=PLUGIN, company_id=COMPANY)

    assert grant is not None
    assert grant.artifact_digest == DIGEST
    assert grant.published_artifact_digest == DIGEST
    assert grant.published_artifact_digest != OTHER_DIGEST


@pytest.mark.asyncio
async def test_production_static_response_uses_actual_bundle_digest_not_backend_or_client() -> None:
    harness = _production_static_harness(_artifact())
    spoofed = "sha256:" + "f" * 64

    async with harness.client as client:
        response = await client.get(
            f"/pluginpage/{PLUGIN}/{COMPANY}/public/info"
            f"?bundle_digest={spoofed}&backend_digest={OTHER_DIGEST}",
            headers={"X-AgentScale-Bundle-Digest": spoofed},
        )

    assert response.status_code == 200
    assert response.headers["X-AgentScale-Bundle-Digest"] == DIGEST
    assert response.headers["X-AgentScale-Bundle-Digest"] != OTHER_DIGEST
    assert response.headers["X-AgentScale-Bundle-Digest"] != spoofed
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors https://agentscale.example.test"
    )
    assert harness.static_http.requests[0].query_string == (
        f"bundle_digest={spoofed}&backend_digest={OTHER_DIGEST}".encode("ascii")
    )


@pytest.mark.asyncio
async def test_production_static_missing_or_mismatched_artifact_is_unavailable() -> None:
    for artifact in (None, _artifact(digest=OTHER_DIGEST)):
        harness = _production_static_harness(artifact)

        async with harness.client as client:
            response = await client.get(
                f"/pluginpage/{PLUGIN}/{COMPANY}/public/info"
            )

        assert response.status_code == 503
        assert response.json()["code"] == GatewayErrorCode.PORT_UNAVAILABLE.value
        assert "X-AgentScale-Bundle-Digest" not in response.headers
        assert harness.static_http.requests == []


@pytest.mark.asyncio
async def test_signed_operations_map_exact_method_path_public_flag_and_digest() -> None:
    registry = _Registry(_artifact())
    adapter = _gateway_grants(
        SimpleNamespace(grant_store=_SqlStore(_sql_grant())),
        {"artifact_registry": registry},
    )
    assert isinstance(adapter, GrantAdapter)

    operations = await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY)

    assert operations is not None
    assert all(isinstance(item, GatewayOperation) for item in operations)
    assert [
        (item.operation, item.http_method, item.backend_path, item.public)
        for item in operations
    ] == [
        ("list", "GET", "/api/greetings", True),
        ("private", "GET", "/api/greetings/private", False),
        ("create", "POST", "/api/greetings/create", True),
    ]
    assert {item.capability_id for item in operations} == {"hello.greetings"}
    assert {item.capability_version for item in operations} == {"1.0"}
    assert registry.calls == [(PLUGIN, VERSION), (PLUGIN, VERSION)]


@pytest.mark.asyncio
async def test_digest_mismatch_returns_no_operations() -> None:
    adapter = _production_adapter(_artifact(digest=OTHER_DIGEST))

    assert await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) is None


@pytest.mark.asyncio
async def test_unpublished_artifact_returns_no_operations() -> None:
    artifact = _artifact(status=AppLifecycleStatus.DRAFT, published_at=None)
    adapter = _production_adapter(artifact)

    assert await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) is None


@pytest.mark.asyncio
async def test_quarantined_artifact_returns_no_operations() -> None:
    quarantine = QuarantineEvidence(
        reason=RegistryReason.DEFINITION_MISSING,
        source_kind="unit-test",
        source_id="quarantined-artifact",
        source_digest=DIGEST,
        observed_at=NOW,
    )
    artifact = _artifact(
        status=AppLifecycleStatus.REVOKED,
        outcome=RegistryOutcome.QUARANTINED,
        quarantine=quarantine,
        published_at=None,
    )
    adapter = _production_adapter(artifact)

    assert await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) is None


@pytest.mark.asyncio
async def test_missing_registry_and_inactive_grant_return_no_operations() -> None:
    store = InMemoryGrantStore([_grant_record()])
    without_registry = GrantAdapter(store)
    assert await without_registry.rpc_operations(
        plugin_id=PLUGIN, company_id=COMPANY
    ) is None

    store.revoke(plugin_id=PLUGIN, company_id=COMPANY)
    registry = _Registry(_artifact())
    with_registry = GrantAdapter(store, artifact_registry=registry)
    assert await with_registry.rpc_operations(
        plugin_id=PLUGIN, company_id=COMPANY
    ) is None
    assert registry.calls == []


@pytest.mark.asyncio
async def test_manifest_fingerprint_or_identity_mismatch_returns_no_operations() -> None:
    artifact = _artifact()
    tampered = replace(artifact, manifest_fingerprint="0" * 64)
    adapter = GrantAdapter(
        InMemoryGrantStore([_grant_record()]),
        artifact_registry=_Registry(tampered),
    )
    assert await adapter.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) is None

    wrong_company = replace(_grant_record(), company_id="co-other")

    class ForeignStore:
        async def active_grant(self, *, plugin_id: str, company_id: str):
            return wrong_company

    foreign = GrantAdapter(ForeignStore(), artifact_registry=_Registry(_artifact()))
    assert await foreign.rpc_operations(plugin_id=PLUGIN, company_id=COMPANY) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_full_public_get_head_transport_uses_production_adapter(method: str) -> None:
    harness = _production_harness()

    async with harness.client as client:
        response = await client.request(
            method,
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings?limit=2&tag=a&tag=b",
        )

    assert response.status_code == 200
    assert harness.sessions.resolve_calls == []
    assert len(harness.activation.wake_calls) == 1
    assert len(harness.backend_http.requests) == 1
    sent = harness.backend_http.requests[0]
    assert sent.method == method
    assert sent.path == "/api/greetings"
    assert sent.body == b""
    assert sent.query_string == b"limit=2&tag=a&tag=b"
    assert sent.headers["X-Plugin-Instance-ID"] == INSTANCE
    assert sent.headers["X-Plugin-Artifact-Digest"] == DIGEST
    assert "X-Plugin-Capability-Token" not in sent.headers
    assert "Authorization" not in sent.headers
    opened = next(
        event for event in harness.audit.events
        if event.event == "plugin_page_opened" and event.decision == "allow"
    )
    assert opened.metadata["public"] is True
    assert opened.metadata["operation"] == "list"


@pytest.mark.asyncio
async def test_full_private_operation_is_denied_before_wake_or_session_downgrade() -> None:
    harness = _production_harness()

    async with harness.client as client:
        response = await client.get(
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings/private",
        )

    assert response.status_code == 403
    assert response.json()["code"] == GatewayErrorCode.OPERATION_NOT_ALLOWED.value
    assert harness.sessions.resolve_calls == []
    assert harness.activation.wake_calls == []
    assert harness.backend_http.requests == []


@pytest.mark.asyncio
async def test_full_public_side_effect_is_denied_even_with_webhook_signature() -> None:
    harness = _production_harness()
    body = b'{"name":"alice"}'
    forged_proof = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), b'{"name":"before"}', hashlib.sha256
    ).hexdigest()

    async with harness.client as client:
        response = await client.post(
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings/create",
            content=body,
            headers={HEADER_SIGNATURE: forged_proof},
        )

    assert response.status_code == 403
    assert response.json()["code"] == GatewayErrorCode.OPERATION_NOT_ALLOWED.value
    assert harness.secrets.calls == []
    assert harness.sessions.resolve_calls == []
    assert harness.activation.wake_calls == []
    assert harness.backend_http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_full_public_read_with_body_is_denied(method: str) -> None:
    harness = _production_harness()

    async with harness.client as client:
        response = await client.request(
            method,
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings",
            content=b'{"unexpected":true}',
        )

    assert response.status_code == 403
    if method == "GET":
        assert response.json()["code"] == GatewayErrorCode.OPERATION_NOT_ALLOWED.value
    assert harness.activation.wake_calls == []
    assert harness.backend_http.requests == []


@pytest.mark.asyncio
async def test_missing_registry_does_not_downgrade_auth_to_anonymous() -> None:
    harness = build_harness()
    harness.auth.grants = GrantAdapter(InMemoryGrantStore([_grant_record()]))

    async with harness.client as client:
        response = await client.get(
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings",
        )

    assert response.status_code == 401
    assert response.json()["code"] == GatewayErrorCode.SESSION_REQUIRED.value
    assert harness.sessions.resolve_calls == [None]
    assert harness.activation.wake_calls == []
    assert harness.backend_http.requests == []


@pytest.mark.asyncio
async def test_client_route_header_and_query_do_not_change_server_derived_allowance() -> None:
    harness = _production_harness()

    async with harness.client as client:
        response = await client.get(
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings?route_path=/not-public",
            headers={"X-Plugin-Route-Path": "/not-public"},
        )

    assert response.status_code == 200
    assert harness.sessions.resolve_calls == []
    assert len(harness.backend_http.requests) == 1
    sent = harness.backend_http.requests[0]
    assert sent.path == "/api/greetings"
    assert sent.query_string == b"route_path=/not-public"
    assert "X-Plugin-Route-Path" not in sent.headers


@pytest.mark.asyncio
async def test_bad_session_is_not_downgraded() -> None:
    harness = _production_harness()

    async with harness.client as client:
        response = await client.get(
            f"/pluginpage/{PLUGIN}/{COMPANY}/api/greetings",
            headers={"Authorization": "Bearer bad-session"},
        )

    assert response.status_code == 401
    assert harness.sessions.resolve_calls == ["bad-session"]
    assert harness.activation.wake_calls == []
    assert harness.backend_http.requests == []


@pytest.mark.asyncio
async def test_webhook_hmac_and_raw_body_remain_unchanged_with_production_adapter() -> None:
    harness = build_harness()
    harness.auth.grants = GrantAdapter(InMemoryGrantStore([_grant_record()]))
    body = b'{"event":"before"}'
    signature = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()

    result = await harness.auth.authorize_webhook(
        plugin_id=PLUGIN,
        company_id=COMPANY,
        path="/webhook/events",
        method="POST",
        raw_body=body,
        headers={
            HEADER_SIGNATURE: signature,
            HEADER_EVENT: "chat.message.completed",
            HEADER_DELIVERY: "delivery-production-public-operations",
        },
    )

    assert result.duplicate is False
    assert result.verified is not None
    assert result.verified.signature == signature
    assert result.verified.delivery_id == "delivery-production-public-operations"
    with pytest.raises(GatewayError) as exc:
        await harness.auth.authorize_webhook(
            plugin_id=PLUGIN,
            company_id=COMPANY,
            path="/webhook/events",
            method="POST",
            raw_body=b'{"event":"after"}',
            headers={
                HEADER_SIGNATURE: signature,
                HEADER_EVENT: "chat.message.completed",
                HEADER_DELIVERY: "delivery-production-public-operations-altered",
            },
        )
    assert exc.value.gateway_code is GatewayErrorCode.INVALID_SIGNATURE
