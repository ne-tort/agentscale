"""Typed platform provider contracts; all durable/K8s/OCI/secret IO is faked."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressMode,
    EgressPolicy,
    GrantStatus,
    QuotaSet,
)
from prodavan.application.plugins.capabilities.adapters.secrets import (
    SecretStoreSecretsAdapter,
)
from prodavan.application.plugins.identity.types import PluginClaims
from prodavan.application.plugins.runtime.provider_api import (
    ArtifactMetadataRequest,
    EgressDeclarationRequest,
    PodActionRequest,
    ProviderError,
    SecretMetadataRequest,
)
from prodavan.api.v1.plugin_runtime import install_plugin_runtime_routes
from prodavan.application.plugins.runtime.providers import ScopedRuntimeProvider
from prodavan.infrastructure.plugins.runtime.egress_transport import DurableGrantEgressPolicy
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackendResolver,
    K8sPodExecutor,
)
from tests.unit.plugins.runtime.canonical_backend_fixtures import (
    ARTIFACT_DIGEST,
    ARTIFACT_METADATA,
    BACKEND_DIGEST,
    BACKEND_METADATA,
    COMPANY,
    EMPTY_DIGEST,
    INSTANCE,
    JOB,
    PLUGIN,
    PROJECT,
    FakeArtifacts,
    FakeInstances,
    FakeK8sApi,
    FakeTokenProvider,
    artifact_record,
    runtime_config,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
HOST = "api.example.com"
PATH = "/v1/allowed"
SECRET_REF = (
    "vault://ai_keys/plugins/capabilities/secrets/"
    + COMPANY + "/" + PLUGIN + "/" + INSTANCE + "/test.secret"
)
PLAINTEXT = "super-secret-test-value"
SECRET_DIGEST = "sha256:" + hashlib.sha256(PLAINTEXT.encode("utf-8")).hexdigest()


class GrantStore:
    def __init__(self, record: CapabilityGrantRecord) -> None:
        self.record = record

    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None:
        return self.record if self.record.grant_id == grant_id else None


class FakeSecretStore:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get(self, secret_ref: str) -> str:
        self.reads.append(secret_ref)
        return PLAINTEXT


class Loader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.refs: list[Any] = []

    async def load_manifest(self, ref: Any) -> SimpleNamespace:
        self.refs.append(ref)
        if self.fail:
            raise RuntimeError("external OCI unavailable")
        payloads = {
            ARTIFACT_DIGEST: ARTIFACT_METADATA,
            BACKEND_DIGEST: BACKEND_METADATA,
        }
        try:
            data = payloads[ref.digest]
        except KeyError:
            raise RuntimeError("unknown test OCI digest") from None
        # The fake is digest-bound exactly like an OCI registry: both the
        # returned reference and sha256(data) match the requested identity.
        return SimpleNamespace(data=data, digest=ref.digest, size=len(data))


def grant_record(**changes: Any) -> CapabilityGrantRecord:
    values = {
        "grant_id": "grant_1",
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "manifest_version": "1.2.3",
        "artifact_digest": ARTIFACT_DIGEST,
        "capabilities": ("network", "pods", "secrets"),
        "egress_policy": EgressPolicy(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=(HOST,),
            allowed_paths=(PATH,),
        ),
        "quotas": QuotaSet(max_egress_bytes=4096),
        "status": GrantStatus.ACTIVE,
        "created_at": NOW,
    }
    values.update(changes)
    return CapabilityGrantRecord(**values)


def provider(
    *,
    record: CapabilityGrantRecord | None = None,
    loader: Loader | None = None,
    secret_manifest: bool = False,
) -> tuple[ScopedRuntimeProvider, FakeK8sApi, Loader]:
    tokens = FakeTokenProvider()
    api = FakeK8sApi(tokens)
    artifact = artifact_record()
    if secret_manifest:
        artifact = replace(
            artifact,
            manifest={
                **artifact.manifest,
                "secret_delivery": {
                    "mode": "k8s_secret",
                    "allowlist": [SECRET_REF],
                },
            },
        )
    resolver = CanonicalBackendResolver(
        config=runtime_config(),
        instance_store=FakeInstances(),
        artifact_store=FakeArtifacts(artifact),
        api=api,
        token_provider=tokens,
    )
    executor = K8sPodExecutor(
        resolver=resolver,
        activator=SimpleNamespace(wake=lambda *a, **k: None),
        max_result_bytes=1024,
    )
    secret_reader = SecretStoreSecretsAdapter(store=FakeSecretStore())
    real_loader = loader or Loader()
    service = ScopedRuntimeProvider(
        resolver=resolver,
        declarations=DurableGrantEgressPolicy(GrantStore(record or grant_record())),
        pod_executor=executor,
        secret_reader=secret_reader,
        artifact_loader=real_loader,
    )
    return service, api, real_loader


def claims(**changes: Any) -> PluginClaims:
    values = {
        "sub": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": INSTANCE,
        "manifest_version": "1.2.3",
        "artifact_digest": ARTIFACT_DIGEST,
        "grant_id": "grant_1",
        "scopes": ("network", "pods", "secrets"),
        "quota_ref": "quota_1",
        "jti": "jti_1",
        "iat": 1,
        "exp": 4_102_444_800,
        "project_id": PROJECT,
    }
    values.update(changes)
    return PluginClaims(**values)


def pod_request(action: str, **changes: Any) -> ArtifactMetadataRequest:
    value = {
        "backend_digest": BACKEND_DIGEST,
        "request_id": "req-provider-1",
        "job_id": JOB,
        "target_ref": ARTIFACT_DIGEST if action == "read_artifact" else JOB,
        "arguments_digest": EMPTY_DIGEST,
        "max_result_bytes": 1024,
    }
    value.update(changes)
    if action == "read_artifact":
        return ArtifactMetadataRequest.model_validate(value)
    return PodActionRequest.model_validate(value)


def api_app(service: ScopedRuntimeProvider, claim: PluginClaims) -> TestClient:
    from prodavan.application.plugins.transport.deps import get_plugin_claims
    from prodavan.application.plugins.runtime.providers import get_scoped_runtime_provider

    app = FastAPI()
    app.dependency_overrides[get_plugin_claims] = lambda: claim
    app.dependency_overrides[get_scoped_runtime_provider] = lambda: service
    install_plugin_runtime_routes(app.router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_declaration_returns_real_durable_policy_bound_to_backend() -> None:
    service, api, _ = provider()
    result = await service.egress_declaration(
        claims=claims(), request=EgressDeclarationRequest.model_validate({
            "backend_digest": BACKEND_DIGEST,
            "request_id": "req-provider-1",
        })
    )
    assert result.company_id == COMPANY
    assert result.plugin_id == PLUGIN
    assert result.plugin_instance_id == INSTANCE
    assert result.artifact_digest == ARTIFACT_DIGEST
    assert result.backend_digest == BACKEND_DIGEST
    assert result.grant_id == "grant_1"
    assert result.max_egress_bytes == 4096
    assert result.secret_refs == []
    assert (HOST, PATH, "GET") in {(item.host, item.path_prefix, item.method) for item in result.rules}
    # OPTIONS exists in the durable policy but is outside the fixed provider contract.
    assert all(item.method != "OPTIONS" for item in result.rules)
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_declaration_rejects_foreign_grant_and_digest() -> None:
    service, api, _ = provider(record=grant_record(artifact_digest="sha256:" + "f" * 64))
    with pytest.raises(ProviderError) as denied:
        await service.egress_declaration(
            claims=claims(), request=EgressDeclarationRequest.model_validate({
                "backend_digest": BACKEND_DIGEST,
                "request_id": "req-provider-1",
            })
        )
    assert denied.value.status_code == 403
    assert api.proxy_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_changes",
    ({"company_id": "other-company"}, {"plugin_instance_id": "other-instance"}),
)
async def test_tenant_claim_mismatch_is_forbidden(claim_changes: dict[str, Any]) -> None:
    service, api, _ = provider()
    with pytest.raises(ProviderError) as denied:
        await service.egress_declaration(
            claims=claims(**claim_changes),
            request=EgressDeclarationRequest.model_validate({
                "backend_digest": BACKEND_DIGEST,
                "request_id": "req-provider-1",
            }),
        )
    assert denied.value.status_code == 403
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_backend_digest_assertion_mismatch_is_forbidden() -> None:
    service, api, _ = provider()
    with pytest.raises(ProviderError) as denied:
        await service.egress_declaration(
            claims=claims(),
            request=EgressDeclarationRequest.model_validate({
                "backend_digest": "sha256:" + "c" * 64,
                "request_id": "req-provider-1",
            }),
        )
    assert denied.value.status_code == 403
    assert denied.value.code == "RUNTIME_BACKEND_DIGEST_MISMATCH"
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_artifact_metadata_uses_oci_digest_verified_bytes() -> None:
    service, api, loader = provider()
    result = await service.artifact_metadata(
        claims=claims(), request=pod_request("read_artifact")
    )
    assert result.action == "read_artifact"
    assert result.byte_count == len(b'{"verified":"metadata"}')
    assert loader.refs[0].digest == ARTIFACT_DIGEST
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_backend_artifact_metadata_uses_publication_backend_ref() -> None:
    service, api, loader = provider()
    result = await service.artifact_metadata(
        claims=claims(),
        request=pod_request("read_artifact", target_ref=BACKEND_DIGEST),
    )
    assert result.artifact_digest == ARTIFACT_DIGEST
    assert result.backend_digest == BACKEND_DIGEST
    assert result.byte_count == len(b'{"verified":"metadata"}')
    assert loader.refs[0].digest == BACKEND_DIGEST
    assert api.proxy_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_changes", "status", "code"),
    (
        ({"target_ref": "sha256:" + "c" * 64}, 404, "ARTIFACT_METADATA_NOT_FOUND"),
        ({"arguments_digest": "sha256:" + "c" * 64}, 403, "POD_ARTIFACT_ARGUMENTS_NOT_ALLOWED"),
        ({"job_id": "job_other"}, 403, "POD_JOB_NOT_ALLOWLISTED"),
    ),
)
async def test_artifact_binding_and_absence_are_typed(
    request_changes: dict[str, Any], status: int, code: str
) -> None:
    service, api, _ = provider()
    with pytest.raises(ProviderError) as denied:
        await service.artifact_metadata(
            claims=claims(), request=pod_request("read_artifact", **request_changes)
        )
    assert (denied.value.status_code, denied.value.code) == (status, code)
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_oci_transport_failure_is_503_not_fabricated_bytes() -> None:
    service, _, _ = provider(loader=Loader(fail=True))
    with pytest.raises(ProviderError) as denied:
        await service.artifact_metadata(
            claims=claims(), request=pod_request("read_artifact")
        )
    assert denied.value.status_code == 503


@pytest.mark.asyncio
async def test_artifact_fetch_and_response_share_request_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prodavan.application.plugins.runtime.providers as composition

    service, _, _ = provider()
    service._artifact_loader = None
    caps: list[int] = []

    class BoundedLoader:
        def __init__(self, **options: Any) -> None:
            caps.append(options["max_manifest_bytes"])

        async def load_manifest(self, ref: Any) -> SimpleNamespace:
            raise RuntimeError("OCI object size cap exceeded")

    monkeypatch.setattr(composition, "HttpxOciArtifactLoader", BoundedLoader)
    with pytest.raises(ProviderError) as denied:
        await service.artifact_metadata(
            claims=claims(),
            request=pod_request("read_artifact", max_result_bytes=1),
        )
    assert denied.value.status_code == 413
    assert denied.value.code == "ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED"
    assert caps == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ("read_status", "read_logs", "send_agent_message"))
async def test_pod_data_sources_without_real_platform_store_stay_unwired(method: str) -> None:
    service, api, _ = provider()
    bound = pod_request(method, target_ref=JOB)
    if method == "send_agent_message":
        bound = pod_request(method, target_ref=JOB, arguments_digest="sha256:" + "c" * 64)
    callable_method = getattr(service, method)
    with pytest.raises(ProviderError) as denied:
        await callable_method(claims=claims(), request=bound)
    assert denied.value.status_code == 503
    assert api.proxy_calls == []


@pytest.mark.asyncio
async def test_secret_metadata_is_digest_only_and_never_exposes_plaintext() -> None:
    service, _, _ = provider(secret_manifest=True)
    result = await service.secret_metadata(
        claims=claims(),
        request=SecretMetadataRequest.model_validate({
            "backend_digest": BACKEND_DIGEST,
            "request_id": "req-provider-1",
            "secret_ref": SECRET_REF,
        }),
    )
    assert result.value_digest == SECRET_DIGEST
    assert result.metadata_digest.startswith("sha256:")
    dumped = result.model_dump()
    assert PLAINTEXT not in str(dumped)
    assert "value" not in dumped and "plaintext" not in dumped


@pytest.mark.asyncio
async def test_foreign_or_undeclared_secret_ref_is_forbidden() -> None:
    service, _, _ = provider()
    with pytest.raises(ProviderError) as denied:
        await service.secret_metadata(
            claims=claims(),
            request=SecretMetadataRequest.model_validate({
                "backend_digest": BACKEND_DIGEST,
                "request_id": "req-provider-1",
                "secret_ref": SECRET_REF,
            }),
        )
    assert denied.value.status_code == 403


def test_routes_are_typed_bounded_and_have_no_generic_exec() -> None:
    service, _, _ = provider()
    client = api_app(service, claims())

    response = client.post(
        "/capabilities/v1/runtime/declarations/egress?company_id=" + COMPANY,
        json={"backend_digest": BACKEND_DIGEST, "request_id": "req-provider-1"},
    )
    assert response.status_code == 422

    spoofed = {
        "company_id": "other-company",
        "plugin_id": "other-plugin",
        "backend_digest": BACKEND_DIGEST,
        "request_id": "req-provider-1",
    }
    assert client.post(
        "/capabilities/v1/runtime/declarations/egress", json=spoofed
    ).status_code == 422

    allowed = client.post(
        "/capabilities/v1/runtime/declarations/egress",
        json={"backend_digest": BACKEND_DIGEST, "request_id": "req-provider-1"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["company_id"] == COMPANY

    missing = client.post(
        "/capabilities/v1/runtime/artifacts/metadata",
        json=pod_request("read_artifact").model_dump(mode="json")
        | {"target_ref": "sha256:" + "c" * 64},
    )
    assert missing.status_code == 404

    too_large = client.post(
        "/capabilities/v1/runtime/artifacts/metadata",
        json=pod_request("read_artifact", max_result_bytes=1).model_dump(mode="json"),
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "ARTIFACT_RESULT_BYTE_LIMIT_EXCEEDED"

    for path, body in (
        ("/capabilities/v1/runtime/pods/status", pod_request("read_status")),
        ("/capabilities/v1/runtime/pods/logs", pod_request("read_logs")),
        (
            "/capabilities/v1/runtime/pods/messages",
            pod_request("send_agent_message", arguments_digest="sha256:" + "c" * 64),
        ),
    ):
        assert client.post(path, json=body.model_dump(mode="json")).status_code == 503

    assert client.post("/capabilities/v1/runtime/exec", json={}).status_code == 404
    assert client.post("/capabilities/v1/runtime/proxy", json={}).status_code == 404



def test_shared_gateway_router_mounts_real_provider_once() -> None:
    from prodavan.api.v1 import plugin_gateway
    from prodavan.application.plugins.identity.keys import keyring_from_secret
    from prodavan.application.plugins.identity.mint import mint_plugin_claims
    from prodavan.application.plugins.transport.deps import get_plugin_claims

    path = "/capabilities/v1/runtime/declarations/egress"
    install_plugin_runtime_routes(plugin_gateway.router)
    assert [route.path for route in plugin_gateway.router.routes].count(path) == 1

    service, _, _ = provider()
    adapters = {
        "pods": SimpleNamespace(_executor=service._pod_executor),
        "network": SimpleNamespace(
            _transport=SimpleNamespace(_declarations=service._declarations)
        ),
        "secrets": service._secret_reader,
    }
    registry = SimpleNamespace(resolve=lambda name: adapters[name])

    class Revocation:
        async def check(self, claim: PluginClaims) -> PluginClaims:
            return claim

    secret = "provider-route-test-signing-secret"
    app = FastAPI()
    app.include_router(plugin_gateway.router)
    app.state.plugin_platform = SimpleNamespace(registry=registry)
    app.state.plugin_keyring = keyring_from_secret(secret)
    app.state.plugin_revocation = Revocation()
    plugin_gateway.install_plugin_gateway_runtime(
        app, plugin_gateway.PluginGatewayRuntime(auth=object(), proxy=object())
    )
    # The test installs no auth/provider override itself: production composition
    # registers only the scoped provider, while real signed claims authenticate.
    assert get_plugin_claims not in app.dependency_overrides
    token = mint_plugin_claims(
        grant=SimpleNamespace(
            grant_id="grant_1",
            plugin_id=PLUGIN,
            company_id=COMPANY,
            manifest_version="1.2.3",
            artifact_digest=ARTIFACT_DIGEST,
            scopes=("network", "pods", "secrets"),
            quota_ref=COMPANY + "/plan_pro",
        ),
        instance=SimpleNamespace(
            plugin_instance_id=INSTANCE,
            cabinet_id=None,
            project_id=PROJECT,
        ),
        secret=secret,
    )
    client = TestClient(app)
    response = client.post(
        path,
        json={"backend_digest": BACKEND_DIGEST, "request_id": "req-provider-1"},
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 200
    assert response.json()["backend_digest"] == BACKEND_DIGEST


def test_both_entrypoints_include_shared_router_before_capability_catchall() -> None:
    from pathlib import Path
    import prodavan

    package = Path(prodavan.__file__).resolve().parent
    for filename in ("main.py", "main_plugin_gateway.py"):
        source = (package / filename).read_text(encoding="utf-8")
        shared = "app.include_router(plugin_gateway.router)"
        catchall = "app.include_router(plugin_capabilities.router)"
        assert shared in source
        assert catchall in source
        assert source.index(shared) < source.index(catchall)


def test_production_provider_construction_reuses_scoped_platform_ports() -> None:
    import prodavan.application.plugins.runtime.providers as composition

    service, _, _ = provider()
    adapters = {
        "pods": SimpleNamespace(_executor=service._pod_executor),
        "network": SimpleNamespace(
            _transport=SimpleNamespace(_declarations=service._declarations)
        ),
        "secrets": service._secret_reader,
    }
    registry = SimpleNamespace(resolve=lambda name: adapters[name])
    platform = SimpleNamespace(registry=registry)

    constructed = composition.build_scoped_runtime_provider(platform)
    assert isinstance(constructed, ScopedRuntimeProvider)
    assert constructed._resolver is service._resolver
    assert constructed._declarations is service._declarations
    assert constructed._secret_reader is service._secret_reader
    assert constructed._pod_executor is service._pod_executor

    app = FastAPI()
    composition.install_scoped_runtime_provider(app, constructed)
    assert app.dependency_overrides[
        composition.get_scoped_runtime_provider
    ]() is constructed
    # Reinstalling the exact same provider is idempotent; no duplicate routes.
    composition.install_scoped_runtime_provider(app, constructed)
