from __future__ import annotations

import dataclasses
import inspect
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import ssl
import pytest
from fastapi import FastAPI

from prodavan.api.internal.plugin_artifacts import PLUGIN_CI_PUBLICATION_SERVICE_STATE
from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import EgressPolicy, PluginPublicationApproval, QuotaSet
from prodavan.application.plugins.artifacts.oci import OciArtifactRef
from prodavan.application.plugins.artifacts.oidc import GitHubActionsOidcValidator
from prodavan.application.plugins.artifacts.publication import (
    HttpxOciArtifactLoader,
    PluginCiPublicationService,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry, PublicationMetadata
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier, CompatibilityGate
from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner
from prodavan.application.plugins.jobs.declarations import (
    PluginJobDeclaration,
    ScopedManifestOperationExecutor,
    ScopedManifestOperationHandler,
)
from prodavan.application.plugins.jobs.tasks import ManifestJobTasks
from prodavan.application.plugins.sources.service import ModuleSourceService
from prodavan.application.plugins.wiring.install import (
    PLUGIN_CI_OIDC_AUDIENCE_ENV,
    PLUGIN_CI_PUBLICATION_ENABLED_ENV,
    UNWIRED_PORTS_STATE_KEY,
)
from prodavan.application.plugins.wiring.module_sources import (
    MODULE_SOURCE_COMPOSITION_STATE,
    MODULE_SOURCE_GITHUB_APP_STATE,
    MODULE_SOURCE_LIFESPAN_NAME,
    ModuleSourceComposition,
)
from prodavan.application.plugins.wiring.adapters import WIRED_CAPABILITIES, adapter_report
from prodavan.core.infra.worker_manager import WorkerManager
from prodavan.core.jobs.plugin_worker import PluginWorkerRegistration
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.infrastructure.persistence import database
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import SqlArtifactRegistryStore
from prodavan.infrastructure.persistence.models.plugin_platform import ModuleSourceRow
from prodavan.infrastructure.plugins.adapters.redis_event_claims import RedisDeliveryClaimStore
from prodavan.infrastructure.plugins.minio.adapters import MinioBucketAdmin
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig
from prodavan.infrastructure.plugins.capabilities.kafka import KafkaSecurityConfig
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.secret_projection import K8sSecretProjection, SecretReference
from prodavan.infrastructure.plugins.sources.github_app import GitHubAppClient
from prodavan.infrastructure.plugins.tenantdb.asyncpg_admin import AsyncpgAdminConnection

from .production_boundaries import (
    BACKEND_DIGEST,
    BACKEND_REF,
    CAPABILITIES,
    COMMIT_SHA,
    FIXED_TIME,
    GATEWAY_URL,
    ISSUER,
    NAMESPACE,
    NOW,
    QUOTAS,
    SIGNER_IDENTITY,
    VERSION,
    RsaBoundary,
    RelationalBoundary,
    StatefulAsyncpgAdmin,
    StatefulAsyncpgPool,
    StatefulGhcrBoundary,
    StatefulK8sApi,
    StatefulKafkaConsumer,
    StatefulOidcJwks,
    StatefulRedisBoundary,
    StatefulS3Transport,
    StatefulVaultBoundary,
    TRUSTED_WORKFLOW_REF,
    app_settings,
    github_client,
    publication_fixture,
    source_config,
)

pytestmark = pytest.mark.integration

_JOB_PLUGIN_ID = "job_composition_fixture"
_JOB_ARTIFACT_DIGEST = "sha256:" + "d" * 64
_EVENT_SIGNING_REF = "vault://ai_keys/composition/plugin-event-signing"


class _EventSecretStore:
    """Read-only sync SecretStore view over the stateful test boundary."""

    def __init__(self, vault: StatefulVaultBoundary) -> None:
        self._vault = vault

    def get(self, secret_ref: str) -> str:
        return self._vault._read_reference(secret_ref)


class _ControlPlaneSecretStore(_EventSecretStore):
    """Full synchronous control-plane contract over the same stateful boundary."""

    def put(self, key: str, value: str) -> str:
        reference = self._vault._refs.get(key, self._vault._file_ref(key))
        self._vault._refs[key] = reference
        self._vault._values[reference] = value
        return reference

    def delete(self, secret_ref: str) -> None:
        self._vault._values.pop(secret_ref, None)


async def _seed_job_declaration(
    relational: RelationalBoundary,
    ghcr: StatefulGhcrBoundary,
) -> PluginJobDeclaration:
    """Publish only the independent worker declaration, never a grant/ACTIVE state."""

    ref = OciArtifactRef(
        registry="ghcr.io",
        repository=f"managed-plugins/{_JOB_PLUGIN_ID}",
        digest=_JOB_ARTIFACT_DIGEST,
    )
    manifest = json.loads(publication_fixture().canonical_bytes)
    manifest["plugin_id"] = _JOB_PLUGIN_ID
    approval = PluginPublicationApproval(
        approval_id="ppr_job_composition_fixture",
        company_id="co_job_composition_fixture",
        plugin_id=_JOB_PLUGIN_ID,
        manifest_version=VERSION,
        artifact_digest=_JOB_ARTIFACT_DIGEST,
        capabilities=CAPABILITIES,
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(**QUOTAS),
        created_by="production-composition-fixture",
        created_at=NOW,
    )
    # The cosign boundary trusts only digest-addressed registered artifacts.
    ghcr.manifests[_JOB_ARTIFACT_DIGEST] = b"{}"
    verifier = ArtifactVerifier(
        ghcr,
        identity=SIGNER_IDENTITY,
        issuer=ISSUER,
        compatibility=CompatibilityGate(platform_version="4.0.0"),
        clock=lambda: NOW,
    )
    outcome = verifier.verify(
        ref,
        manifest,
        grant=approval,
        scan_verified=True,
        csp_verified=True,
    )
    registry = PluginArtifactRegistry(
        store=SqlArtifactRegistryStore(session_factory=relational.session_factory),
        verifier=verifier,
        clock=lambda: NOW,
    )
    async with relational.session_factory() as session:
        session.add(
            ModuleSourceRow(
                id="src_job_composition_fixture",
                company_id="co_job_composition_fixture",
                plugin_id=_JOB_PLUGIN_ID,
                provider="github_app",
                repo_id="7654321",
                full_name="managed-plugins/job-composition-fixture",
                private=True,
                default_branch="main",
                installation_credential_ref="vault://ai_keys/composition/github-app",
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                status="ready",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    published = await registry.publish_async(
        ref,
        manifest,
        outcome,
        actor="production-composition-fixture",
        grant=approval,
        metadata=PublicationMetadata(
            source_id="src_job_composition_fixture",
            commit_sha=COMMIT_SHA,
            backend_digest=BACKEND_DIGEST,
            backend_image_ref=BACKEND_REF,
            run_url="https://github.com/managed-plugins/job-composition/actions/runs/1",
        ),
    )
    assert published.outcome is RegistryOutcome.APPLIED
    return PluginJobDeclaration(
        plugin_id=_JOB_PLUGIN_ID,
        version=VERSION,
        artifact_digest=_JOB_ARTIFACT_DIGEST,
    )


@dataclasses.dataclass
class CompositionWorld:
    app: FastAPI
    relational: RelationalBoundary
    lifespan: LifespanManager
    vault: StatefulVaultBoundary
    redis: StatefulRedisBoundary
    k8s: StatefulK8sApi
    s3: StatefulS3Transport
    asyncpg: AsyncpgAdminConnection
    ghcr: StatefulGhcrBoundary
    github: GitHubAppClient | None
    rsa: RsaBoundary
    jwks: StatefulOidcJwks
    plugin_worker: PluginWorkerRegistration
    job_declaration: PluginJobDeclaration | None


def _no_network_transport() -> httpx.MockTransport:
    def denied(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected live HTTP request: {request.url.host}")

    return httpx.MockTransport(denied)


@asynccontextmanager
async def composition_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: bool,
    ci: bool,
    platform: bool,
) -> AsyncIterator[CompositionWorld]:
    """Cold create_app with production factories and only IO boundaries replaced."""
    relational = await RelationalBoundary().open()
    vault = StatefulVaultBoundary()
    redis = StatefulRedisBoundary()
    k8s = StatefulK8sApi(scoped_identity=True)
    assert k8s.scoped_identity is True
    s3 = StatefulS3Transport()
    asyncpg_driver = StatefulAsyncpgAdmin()
    ghcr = StatefulGhcrBoundary()
    rsa = RsaBoundary.create()
    github = github_client(vault, rsa) if source or ci else None
    shared_config = source_config()
    jwks = StatefulOidcJwks(rsa)

    import prodavan.main as main_module
    import prodavan.application.plugins.wiring.install as install_module
    import prodavan.application.plugins.wiring.module_sources as source_module
    import prodavan.application.plugins.artifacts.publication as publication_module
    import prodavan.config.settings as settings_module

    test_settings = app_settings(source=source, platform=platform)

    async def pool_factory(**kwargs: Any) -> StatefulAsyncpgPool:
        return StatefulAsyncpgPool(asyncpg_driver, str(kwargs["database"]))

    asyncpg = AsyncpgAdminConnection.from_settings(
        test_settings,
        pool_factory=pool_factory,
    )
    monkeypatch.setattr(main_module, "settings", test_settings)
    # WorkerManager reads the process settings directly. Keep its real production
    # factory and change only the same kill switch consumed by main.create_app().
    monkeypatch.setattr(settings_module.settings, "plugin_platform_enabled", platform)
    monkeypatch.setattr(database, "get_session_factory", lambda: relational.session_factory)

    event_signer: HmacPluginEventSigner | None = None
    job_declaration: PluginJobDeclaration | None = None
    if platform:
        vault.seed(
            _EVENT_SIGNING_REF,
            "composition-test-event-signing-key-32-bytes-minimum",
        )
        event_signer = HmacPluginEventSigner(
            secret_store=_EventSecretStore(vault),
            secret_ref=_EVENT_SIGNING_REF,
        )
        # main.create_app() consumes this declaration through the real API job
        # factory before platform_with_io; the target hello_plugin stays absent.
        job_declaration = await _seed_job_declaration(relational, ghcr)
        test_settings.plugin_job_declarations = json.dumps([
            {
                "plugin_id": job_declaration.plugin_id,
                "version": job_declaration.version,
                "artifact_digest": job_declaration.artifact_digest,
            }
        ])

    monkeypatch.setenv(PLUGIN_CI_OIDC_AUDIENCE_ENV, "agentscale-plugin-ci")
    monkeypatch.setenv("PLUGIN_RUNTIME_NAMESPACE", NAMESPACE)
    monkeypatch.setenv("PLUGIN_RUNTIME_GATEWAY_URL", GATEWAY_URL)
    if ci:
        monkeypatch.setenv(PLUGIN_CI_PUBLICATION_ENABLED_ENV, "true")
    else:
        monkeypatch.delenv(PLUGIN_CI_PUBLICATION_ENABLED_ENV, raising=False)

    real_source_install = source_module.install_module_source_composition

    def source_with_io(app: FastAPI, settings: Any, **kwargs: Any) -> ModuleSourceComposition:
        assert github is not None
        return real_source_install(
            app,
            settings,
            overrides={"source_config": shared_config, "github_app": github},
            **kwargs,
        )

    monkeypatch.setattr(source_module, "install_module_source_composition", source_with_io)

    real_ci_install = install_module.install_plugin_ci_publication
    oidc = GitHubActionsOidcValidator(
        audience="agentscale-plugin-ci",
        jwks_fetcher=jwks.fetch,
        clock=lambda: FIXED_TIME,
    )

    def ci_with_io(app: FastAPI, settings: Any, **kwargs: Any) -> PluginCiPublicationService | None:
        return real_ci_install(
            app,
            settings,
            overrides={"oidc_validator": oidc, "cosign": ghcr},
            **kwargs,
        )

    monkeypatch.setattr(install_module, "install_plugin_ci_publication", ci_with_io)

    real_loader = publication_module.HttpxOciArtifactLoader

    def loader_with_io(**kwargs: Any) -> HttpxOciArtifactLoader:
        return real_loader(client_factory=ghcr.client_factory, **kwargs)

    monkeypatch.setattr(publication_module, "HttpxOciArtifactLoader", loader_with_io)

    real_platform_install = install_module.install_plugin_platform
    runtime_config = RuntimeConfig(namespace=NAMESPACE, gateway_url=GATEWAY_URL)
    buckets = MinioBucketAdmin(client=s3, quota_admin=s3, owns_bucket=True)
    minio_config = MinioPluginConfig(
        endpoint="http://minio.acceptance.test",
        admin_access_key="composition-admin",
        admin_secret_key="composition-admin-secret",
        presign_access_key="composition-presign",
        presign_secret_key="composition-presign-secret",
    )
    buckets.config = minio_config
    kafka_ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    kafka_ssl_context.check_hostname = True
    kafka_ssl_context.verify_mode = ssl.CERT_REQUIRED
    kafka_security = KafkaSecurityConfig(
        sasl_username="composition-kafka-user",
        sasl_password="composition-kafka-password",
        ssl_context=kafka_ssl_context,
    )

    def platform_with_io(app: FastAPI, settings: Any, **kwargs: Any) -> Any:
        kwargs["redis"] = redis
        overrides = dict(kwargs.pop("overrides", {}) or {})
        if event_signer is None:
            raise AssertionError("enabled platform requires the explicit event signer")
        job_tasks = overrides.get("job_tasks")
        if not isinstance(job_tasks, dict) or not job_tasks:
            raise AssertionError("real API job factory did not provide job_tasks")
        if not callable(overrides.get("job_worker_registration")):
            raise AssertionError("shared API composition must provide the real worker hook")
        overrides.update(
            {
                "event_signer": event_signer,
                # Preserve install_api_plugin_platform's registration wrapper:
                # it binds the install-created tasks to the real WorkerManager
                # and records the same registry for PluginJobComposition.bind_platform.
                "tenant_db_admin": asyncpg,
                "tenant_db_secrets": vault,
                "webhook_secrets": vault,
                "secret_store": _ControlPlaneSecretStore(vault),
                "object_buckets": buckets,
                "minio_config": minio_config,
                "kafka_security": kafka_security,
                "kafka_bootstrap_servers": "localhost:9092",
                "runtime_kwargs": {
                    "config": runtime_config,
                    "k8s_api": k8s,
                    "token_request_api": k8s,
                    "lifecycle_api": k8s,
                    "cosign": ghcr,
                    "client": _no_network_transport(),
                },
            }
        )
        return real_platform_install(app, settings, overrides=overrides, **kwargs)

    monkeypatch.setattr(install_module, "install_plugin_platform", platform_with_io)

    app: FastAPI | None = None
    try:
        # A new FastAPI object is built by the real composition root, never by
        # install_plugin_platform_for_tests or by manually attaching app.state.
        app = main_module.create_app()
        lifespan = app.state.lifespan_manager
        assert isinstance(lifespan, LifespanManager)
        worker = lifespan.get("worker")
        assert isinstance(worker, WorkerManager)
        plugin_worker = worker._plugin_worker
        assert isinstance(plugin_worker, PluginWorkerRegistration)
        world = CompositionWorld(
            app=app,
            relational=relational,
            lifespan=lifespan,
            vault=vault,
            redis=redis,
            k8s=k8s,
            s3=s3,
            asyncpg=asyncpg,
            ghcr=ghcr,
            github=github,
            rsa=rsa,
            jwks=jwks,
            plugin_worker=plugin_worker,
            job_declaration=job_declaration,
        )
        yield world
    finally:
        if app is not None:
            runtime = getattr(app.state, "plugin_runtime_ports", None)
            if runtime is not None:
                await runtime.aclose()
            source_resource = app.state.lifespan_manager.get(MODULE_SOURCE_LIFESPAN_NAME)
            if source_resource is not None:
                await source_resource.shutdown()
                await source_resource.shutdown()  # lifecycle close is idempotent
            ci_resource = app.state.lifespan_manager.get("plugin-ci-publication-artifacts")
            if ci_resource is not None:
                await ci_resource.shutdown()
            tenant_resource = app.state.lifespan_manager.get("plugin-tenant-db-admin")
            if tenant_resource is not None:
                await tenant_resource.shutdown()
        await relational.close()


def _route_paths(app: FastAPI) -> list[str]:
    """Flatten FastAPI 0.141 lazy router includes without losing route order."""
    paths: list[str] = []

    def visit(routes: Any) -> None:
        for route in routes:
            effective_candidates = getattr(route, "effective_candidates", None)
            if callable(effective_candidates):
                visit(effective_candidates())
            else:
                paths.append(getattr(route, "path", ""))

    visit(app.routes)
    return paths


def _paths(app: FastAPI) -> set[str]:
    return set(_route_paths(app))


def _assert_event_delivery_claims_composition(app: FastAPI, redis: Any) -> None:
    """Pin durable claims for publish and per-subscription dispatchers."""

    dispatcher = app.state.plugin_event_dispatcher
    claims = dispatcher._delivery_claims
    assert isinstance(claims, RedisDeliveryClaimStore)

    events = app.state.plugin_adapter_registry.resolve("events")
    assert events._dispatcher is dispatcher
    factory = events._runtime._dispatcher_factory
    assert callable(factory)
    subscription_consumer = StatefulKafkaConsumer()
    subscription_dispatcher = factory(subscription_consumer, object())
    assert subscription_dispatcher is not dispatcher
    assert subscription_dispatcher._consumer is subscription_consumer
    assert subscription_dispatcher._raw_dlq is dispatcher._raw_dlq
    shared_redis = claims._redis
    assert shared_redis is dispatcher._raw_dlq._redis
    assert shared_redis._manager is redis
    assert shared_redis._client() is redis

    for candidate in (dispatcher, subscription_dispatcher):
        candidate_claims = candidate._delivery_claims
        assert candidate_claims is claims
        assert isinstance(candidate_claims, RedisDeliveryClaimStore)
        assert candidate_claims._redis is shared_redis
        assert candidate_claims._prefix == "agentscale:plugin:events:delivery-claims:"
        assert candidate_claims.owns_client is False


def _assert_source(world: CompositionWorld) -> None:
    app = world.app
    composition = app.state.module_source_composition
    assert isinstance(composition, ModuleSourceComposition)
    assert app.state.module_source_github_app is world.github
    assert composition.github_app is world.github
    assert isinstance(world.github, GitHubAppClient)
    assert app.state.module_source_managed_config.organization is composition.managed_organization
    factory = app.state.module_source_service_factory
    assert callable(factory)
    assert factory.__name__ == "factory"
    owner = world.lifespan.get(MODULE_SOURCE_LIFESPAN_NAME)
    assert owner is not None
    assert owner._client is world.github
    owners = [
        resource
        for resource in world.lifespan.resources
        if getattr(resource, "_client", None) is world.github
    ]
    assert owners == [owner], "exactly one lifecycle owner for the shared GitHubAppClient"


def _assert_ci(world: CompositionWorld) -> None:
    app = world.app
    service = getattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
    assert isinstance(service, PluginCiPublicationService)
    assert isinstance(service._oidc, GitHubActionsOidcValidator)
    assert isinstance(service._loader, HttpxOciArtifactLoader)
    assert service._loader._authorizer._token_provider is world.github
    assert service._approvals._session_factory is world.relational.session_factory
    assert service._sources._session_factory is world.relational.session_factory
    assert service._registry._store._session_factory is world.relational.session_factory
    verifier = app.state.plugin_artifact_verifier
    assert isinstance(verifier, ArtifactVerifier)
    assert service._verifier is verifier
    assert app.state.plugin_artifact_registry is service._registry
    assert service._registry._verifier is verifier
    assert service._registry._verifier._seal is verifier._seal
    assert isinstance(service._registry, PluginArtifactRegistry)
    assert not hasattr(service, "install")
    assert not hasattr(service, "deploy")
    assert not hasattr(service, "create_grant")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "ci", "expected_resources"),
    [
        pytest.param(True, False, 1, id="source-only"),
        pytest.param(False, True, 2, id="ci-only-composes-required-source-primitives"),
        pytest.param(True, True, 2, id="source-plus-ci"),
    ],
)
async def test_cold_create_app_source_ci_matrix(
    monkeypatch: pytest.MonkeyPatch,
    source: bool,
    ci: bool,
    expected_resources: int,
) -> None:
    async with composition_world(monkeypatch, source=source, ci=ci, platform=False) as world:
        app = world.app
        _assert_source(world)
        if ci:
            _assert_ci(world)
        else:
            assert not hasattr(app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
            assert not hasattr(app.state, "plugin_artifact_verifier")
        paths = _paths(app)
        assert "/internal/plugin-artifacts" in paths
        assert "/api/v1/companies/{company_id}/plugins/{plugin_id}/source" in paths
        assert "/api/v1/admin/plugins/grant" in paths
        assert not hasattr(app.state, "plugin_platform")
        assert not hasattr(app.state, "plugin_gateway_runtime")
        assert not hasattr(app.state, "plugin_activator")
        plugin_resources = [
            resource
            for resource in world.lifespan.resources
            if resource.name in {MODULE_SOURCE_LIFESPAN_NAME, "plugin-ci-publication-artifacts"}
        ]
        assert len(plugin_resources) == expected_resources
        async with world.relational.session_factory() as session:
            service = app.state.module_source_service_factory(session)
            assert isinstance(service, ModuleSourceService)
            assert service._provider is world.github


@pytest.mark.asyncio
async def test_cold_create_app_platform_enabled_has_no_unwired_required_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=False, ci=False, platform=True) as world:
        app = world.app
        paths = _paths(app)
        assert any(path.startswith("/webhook/") for path in paths)
        assert any(path.startswith("/pluginpage/") for path in paths)
        assert "/capabilities/v1/health" in paths
        assert "/api/v1/admin/plugins/grant" in paths
        assert not hasattr(app.state, MODULE_SOURCE_COMPOSITION_STATE)

        platform = app.state.plugin_platform
        assert app.state.plugin_capability_service is platform.capability_service
        assert app.state.plugin_capability_service_factory() is platform.capability_service
        assert app.state.plugin_artifact_verifier is platform.artifact_verifier
        assert platform.artifact_registry._verifier is platform.artifact_verifier
        assert platform.artifact_registry._verifier._seal is platform.artifact_verifier._seal

        ports = app.state.plugin_admin_ports
        assert ports.publication_approvals is not None
        assert type(ports.publication_approvals).__name__ == "SqlPluginPublicationApprovalRepository"
        assert ports.webhook_secrets is world.vault
        activation = ports.activation
        assert isinstance(activation.deployments.secret_projection, K8sSecretProjection)
        assert isinstance(world.asyncpg, AsyncpgAdminConnection)
        assert app.state.plugin_tenant_db_admin_connection is world.asyncpg
        assert app.state.plugin_tenant_db_admin_lifecycle.admin is world.asyncpg

        blockers: list[str] = []
        unwired = tuple(getattr(app.state, UNWIRED_PORTS_STATE_KEY, ()))
        if unwired:
            blockers.append(f"unwired required ports: {unwired}")
        report = adapter_report(app.state.plugin_adapter_registry)
        if report["stub_adapters"]:
            blockers.append(f"UnavailableAdapter on enabled path: {report['stub_adapters']}")
        if set(report["real_adapters"]) != set(WIRED_CAPABILITIES):
            blockers.append("real adapter registry is incomplete")
        if getattr(app.state, "plugin_in_memory_ports", ()):
            blockers.append(f"non-durable in-memory ports: {app.state.plugin_in_memory_ports}")
        dispatcher = getattr(app.state, "plugin_event_dispatcher", None)
        # The shared dispatcher is publish-only. Consumer injection is checked
        # on the per-subscription dispatcher above, matching the runtime factory.
        event_dependencies = (
            "_producer",
            "_topic_admin",
            "_dlq",
            "_raw_dlq",
            "_delivery_claims",
            "_signer",
            "_subscriptions",
            "_published_events",
            "_idempotency",
            "_audit",
            "_handler",
            "_wake_hook",
        )
        missing_event_ports = tuple(
            name for name in event_dependencies if getattr(dispatcher, name, None) is None
        )
        if missing_event_ports:
            blockers.append(f"event dispatcher is not production-wired: {missing_event_ports}")
        assert isinstance(dispatcher._signer, HmacPluginEventSigner)
        _assert_event_delivery_claims_composition(app, world.redis)

        jobs = platform.registry.resolve("jobs")
        assert isinstance(jobs._allowed_tasks, ManifestJobTasks)
        assert world.plugin_worker.enabled is True
        assert world.plugin_worker.wired is True
        assert world.plugin_worker._tasks is jobs._allowed_tasks
        specs = jobs._allowed_tasks.specs
        assert {spec.operation for spec in specs} == {
            "hello.greetings@1.0:list",
            "hello.greetings@1.0:greet",
        }
        assert all(isinstance(spec.handler, ScopedManifestOperationHandler) for spec in specs)
        executors = [spec.handler._executor_provider() for spec in specs]
        assert all(isinstance(item, ScopedManifestOperationExecutor) for item in executors)
        assert all(item._activator is platform.activator for item in executors)
        assert all(item._proxy is platform.activator._proxy for item in executors)
        assert world.job_declaration is not None
        assert world.job_declaration.plugin_id != "hello_plugin"

        # Prove the same API-owned registry can be registered by the real
        # standalone worker owner; no append-only callback or fabricated handler.
        from celery import Celery

        standalone = Celery("production-composition-standalone")
        world.plugin_worker.register(standalone)
        for spec in specs:
            assert standalone.tasks[spec.task_name]._plugin_job_registry is jobs._allowed_tasks

        forbidden = _find_forbidden_objects(app.state)
        if forbidden:
            blockers.append(f"forbidden fail-closed placeholders in app.state: {forbidden}")
        assert not blockers, "PRODUCTION COMPOSITION BLOCKER: " + "; ".join(blockers)


def _find_forbidden_objects(state: Any) -> tuple[str, ...]:
    from prodavan.application.plugins.wiring.adapters import UnavailableAdapter
    from prodavan.application.plugins.wiring.install import _UnwiredPort
    from prodavan.application.plugins.wiring.platform import _MissingPort

    found: list[str] = []
    seen: set[int] = set()

    def walk(value: Any, path: str, depth: int) -> None:
        if depth > 5 or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, (UnavailableAdapter, _UnwiredPort, _MissingPort)):
            found.append(f"{path}:{type(value).__name__}")
            return
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            for item in dataclasses.fields(value):
                walk(getattr(value, item.name), f"{path}.{item.name}", depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}[{key!r}]", depth + 1)
        elif isinstance(value, (tuple, list, set, frozenset)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", depth + 1)

    for key in (
        "plugin_platform",
        "plugin_admin_ports",
        "plugin_adapter_registry",
        "plugin_event_dispatcher",
        "plugin_activator",
    ):
        if hasattr(state, key):
            walk(getattr(state, key), f"app.state.{key}", 0)
    return tuple(found)


@pytest.mark.asyncio
async def test_source_ci_and_platform_share_one_verifier_and_github_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with composition_world(monkeypatch, source=True, ci=True, platform=True) as world:
        _assert_source(world)
        _assert_ci(world)
        service = getattr(world.app.state, PLUGIN_CI_PUBLICATION_SERVICE_STATE)
        platform = world.app.state.plugin_platform
        assert service._verifier is platform.artifact_verifier
        assert service._registry is platform.artifact_registry
        owners = [
            resource
            for resource in world.lifespan.resources
            if getattr(resource, "_client", None) is world.github
        ]
        assert len(owners) == 1


def test_publication_metadata_and_secret_reference_shapes_cannot_go_stale() -> None:
    assert {field.name for field in dataclasses.fields(PublicationMetadata)} == {
        "source_id",
        "commit_sha",
        "backend_digest",
        "backend_image_ref",
        "run_url",
    }
    assert {field.name for field in dataclasses.fields(SecretReference)} == {
        "name",
        "namespace",
        "labels",
        "dsn_ref_sha256",
    }
    parameters = inspect.signature(K8sSecretProjection.ensure_instance_secret).parameters
    assert tuple(parameters) == (
        "self",
        "company_id",
        "plugin_id",
        "plugin_instance_id",
        "dsn_ref",
        "gateway_url",
        "webhook_secret_ref",
    )
    assert not hasattr(SecretReference, "secret_name")
    assert not hasattr(SecretReference, "value")
