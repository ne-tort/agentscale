"""Cold HTTP-entrypoint composition contracts (not executed during freeze).

Only external IO is replaced by stateful offline boundaries. Application
services, repositories, declarations, adapters and worker registration are
constructed by the real production composition for both FastAPI entrypoints.
"""
from __future__ import annotations

import importlib
import json
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner
from prodavan.application.plugins.jobs.declarations import (
    ScopedManifestOperationExecutor,
    ScopedManifestOperationHandler,
)
from prodavan.application.plugins.jobs.tasks import ManifestJobTasks
from prodavan.application.plugins.wiring.adapters import (
    WIRED_CAPABILITIES,
    adapter_report,
)
from prodavan.application.plugins.wiring.install import (
    PLUGIN_CI_PUBLICATION_ENABLED_ENV,
    UNWIRED_PORTS_STATE_KEY,
)
from prodavan.core.infra.worker_manager import WorkerManager
from prodavan.core.jobs.plugin_worker import PluginWorkerRegistration
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.infrastructure.persistence import database
from prodavan.infrastructure.plugins.capabilities.kafka import KafkaSecurityConfig
from prodavan.infrastructure.plugins.minio.adapters import MinioBucketAdmin
from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.tenantdb.asyncpg_admin import AsyncpgAdminConnection
from tests.integration.plugins.production_boundaries import (
    GATEWAY_URL,
    NAMESPACE,
    RelationalBoundary,
    StatefulAsyncpgAdmin,
    StatefulAsyncpgPool,
    StatefulGhcrBoundary,
    StatefulK8sApi,
    StatefulRedisBoundary,
    StatefulS3Transport,
    StatefulVaultBoundary,
    app_settings,
)
from tests.integration.plugins.test_production_composition import (
    _assert_event_delivery_claims_composition,
    _find_forbidden_objects,
    _route_paths,
    _seed_job_declaration,
)

_EVENT_SIGNING_REF = "vault://ai_keys/composition/plugin-event-signing"


@dataclass
class _EntrypointWorld:
    app: FastAPI
    lifespan: LifespanManager
    plugin_worker: PluginWorkerRegistration
    redis: StatefulRedisBoundary


class _EventSecretStore:
    """Read-only sync SecretStore view over the stateful test boundary."""

    def __init__(self, vault: StatefulVaultBoundary) -> None:
        self._vault = vault

    def get(self, secret_ref: str) -> str:
        return self._vault._read_reference(secret_ref)


def _no_network_transport() -> httpx.MockTransport:
    def denied(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected live HTTP request: {request.url.host}")

    return httpx.MockTransport(denied)


@asynccontextmanager
async def _entrypoint_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entrypoint: str,
    platform_enabled: bool,
) -> AsyncIterator[_EntrypointWorld]:
    relational = await RelationalBoundary().open()
    vault = StatefulVaultBoundary()
    redis = StatefulRedisBoundary()
    k8s = StatefulK8sApi(scoped_identity=True)
    s3 = StatefulS3Transport()
    asyncpg_driver = StatefulAsyncpgAdmin()
    ghcr = StatefulGhcrBoundary()

    if entrypoint not in {"main", "plugin_gateway"}:
        raise ValueError(f"unknown composition entrypoint: {entrypoint}")
    import prodavan.application.plugins.wiring.install as install_module
    import prodavan.config.settings as settings_module

    # Import-time app factories must not inherit an enabled ambient test env;
    # the explicit cold create_app below receives the requested flag afterward.
    monkeypatch.setattr(settings_module.settings, "plugin_platform_enabled", False)
    module_name = "prodavan.main" if entrypoint == "main" else "prodavan.main_plugin_gateway"
    entrypoint_module = importlib.import_module(module_name)
    test_settings = app_settings(source=False, platform=platform_enabled)
    monkeypatch.setattr(entrypoint_module, "settings", test_settings)
    # WorkerManager reads the process settings directly. Keep its real factory
    # and change only the same kill switch consumed by the selected entrypoint.
    monkeypatch.setattr(
        settings_module.settings,
        "plugin_platform_enabled",
        platform_enabled,
    )
    monkeypatch.setattr(database, "get_session_factory", lambda: relational.session_factory)
    monkeypatch.delenv(PLUGIN_CI_PUBLICATION_ENABLED_ENV, raising=False)
    monkeypatch.setenv("PLUGIN_RUNTIME_NAMESPACE", NAMESPACE)
    monkeypatch.setenv("PLUGIN_RUNTIME_GATEWAY_URL", GATEWAY_URL)

    asyncpg: AsyncpgAdminConnection | None = None
    event_signer: HmacPluginEventSigner | None = None
    if platform_enabled:
        async def pool_factory(**kwargs: Any) -> StatefulAsyncpgPool:
            return StatefulAsyncpgPool(asyncpg_driver, str(kwargs["database"]))

        asyncpg = AsyncpgAdminConnection.from_settings(
            test_settings,
            pool_factory=pool_factory,
        )
        vault.seed(
            _EVENT_SIGNING_REF,
            "composition-test-event-signing-key-32-bytes-minimum",
        )
        event_signer = HmacPluginEventSigner(
            secret_store=_EventSecretStore(vault),
            secret_ref=_EVENT_SIGNING_REF,
        )
        declaration = await _seed_job_declaration(relational, ghcr)
        test_settings.plugin_job_declarations = json.dumps([
            {
                "plugin_id": declaration.plugin_id,
                "version": declaration.version,
                "artifact_digest": declaration.artifact_digest,
            }
        ])

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
        if event_signer is None or asyncpg is None:
            raise AssertionError("enabled platform requires real IO-boundary configuration")
        if not isinstance(overrides.get("job_tasks"), dict) or not overrides["job_tasks"]:
            raise AssertionError("real API job factory did not provide job_tasks")
        if not callable(overrides.get("job_worker_registration")):
            raise AssertionError("real lifespan worker registration hook is required")
        overrides.update(
            {
                "event_signer": event_signer,
                # Do not replace helper-provided repositories, payload store,
                # job specs or the real WorkerManager registration hook.
                "tenant_db_admin": asyncpg,
                "tenant_db_secrets": vault,
                "webhook_secrets": vault,
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

    real_build_lifespan = entrypoint_module.build_lifespan_manager
    created_lifespans: list[LifespanManager] = []

    def build_and_capture_lifespan() -> LifespanManager:
        manager = real_build_lifespan()
        assert isinstance(manager, LifespanManager)
        created_lifespans.append(manager)
        return manager

    monkeypatch.setattr(
        entrypoint_module,
        "build_lifespan_manager",
        build_and_capture_lifespan,
    )

    app: FastAPI | None = None
    lifespan: LifespanManager | None = None
    try:
        # Cold production create_app: no install_plugin_platform_for_tests and
        # no manual app.state attachment anywhere in this fixture.
        app = entrypoint_module.create_app()
        assert len(created_lifespans) == 1
        lifespan = created_lifespans[0]
        worker = lifespan.get("worker")
        assert isinstance(worker, WorkerManager)
        plugin_worker = worker._plugin_worker
        assert isinstance(plugin_worker, PluginWorkerRegistration)
        yield _EntrypointWorld(
            app=app,
            lifespan=lifespan,
            plugin_worker=plugin_worker,
            redis=redis,
        )
    finally:
        if app is not None and lifespan is not None:
            platform = getattr(app.state, "plugin_platform", None)
            if platform is not None:
                await platform.aclose()
            runtime = getattr(app.state, "plugin_runtime_ports", None)
            if runtime is not None:
                await runtime.aclose()
            payload_store = getattr(app.state, "plugin_digest_payload_store", None)
            if payload_store is not None:
                await payload_store.aclose()
            tenant_resource = lifespan.get("plugin-tenant-db-admin")
            if tenant_resource is not None:
                await tenant_resource.shutdown()
        await relational.close()


def _paths(app: FastAPI) -> list[str]:
    return _route_paths(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["main", "plugin_gateway"])
async def test_cold_enabled_entrypoints_share_full_jobs_and_mcp_composition(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    async with _entrypoint_world(
        monkeypatch,
        entrypoint=entrypoint,
        platform_enabled=True,
    ) as world:
        app = world.app
        platform = app.state.plugin_platform

        report = adapter_report(app.state.plugin_adapter_registry)
        assert report["stub_count"] == 0
        assert len(report["real_adapters"]) == 9
        assert set(report["real_adapters"]) == set(WIRED_CAPABILITIES)
        assert tuple(getattr(app.state, UNWIRED_PORTS_STATE_KEY)) == ()
        assert tuple(getattr(app.state, "plugin_in_memory_ports")) == ()
        assert _find_forbidden_objects(app.state) == ()
        _assert_event_delivery_claims_composition(app, world.redis)

        worker = world.lifespan.get("worker")
        assert isinstance(worker, WorkerManager)
        assert worker._plugin_worker is world.plugin_worker
        assert world.plugin_worker.enabled is True
        assert world.plugin_worker.wired is True

        jobs = platform.registry.resolve("jobs")
        assert isinstance(jobs._allowed_tasks, ManifestJobTasks)
        assert world.plugin_worker._tasks is jobs._allowed_tasks
        specs = jobs._allowed_tasks.specs
        assert {spec.operation for spec in specs} == {
            "hello.greetings@1.0:list",
            "hello.greetings@1.0:greet",
        }
        assert all(
            isinstance(spec.handler, ScopedManifestOperationHandler)
            for spec in specs
        )
        executors = [spec.handler._executor_provider() for spec in specs]
        assert all(isinstance(item, ScopedManifestOperationExecutor) for item in executors)
        assert all(item._activator is platform.activator for item in executors)
        assert all(item._proxy is platform.activator._proxy for item in executors)
        assert all(
            spec.handler._payload_store is app.state.plugin_digest_payload_store
            for spec in specs
        )

        # Register the exact install-created registry through the real lifespan
        # worker hook; no append-only callback or substitute task mapping.
        from celery import Celery

        celery_app = Celery(f"cold-{entrypoint}-plugin-worker")
        world.plugin_worker.register(celery_app)
        for spec in specs:
            registered = celery_app.tasks[spec.task_name]
            assert registered._plugin_job_registry is jobs._allowed_tasks

        discovery = app.state.plugin_mcp_discovery
        assert discovery is not None
        assert platform.registry._mcp_discovery is discovery

        paths = _paths(app)
        mcp_path = "/capabilities/v1/mcp/servers/{server_id}"
        catch_all_path = "/capabilities/v1/{path:path}"
        assert mcp_path in paths
        assert catch_all_path in paths
        assert paths.index(mcp_path) < paths.index(catch_all_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["main", "plugin_gateway"])
async def test_cold_disabled_entrypoints_preserve_legacy_surface(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    async with _entrypoint_world(
        monkeypatch,
        entrypoint=entrypoint,
        platform_enabled=False,
    ) as world:
        app = world.app
        paths = _paths(app)

        assert not hasattr(app.state, "plugin_platform")
        assert not hasattr(app.state, "plugin_adapter_registry")
        assert not hasattr(app.state, "plugin_mcp_discovery")
        assert not hasattr(app.state, UNWIRED_PORTS_STATE_KEY)
        assert world.plugin_worker.enabled is False
        assert world.plugin_worker.wired is False
        assert "/capabilities/v1/mcp/servers/{server_id}" not in paths
        assert "/capabilities/v1/{path:path}" not in paths

        if entrypoint == "plugin_gateway":
            # Legacy standalone behavior: its gateway router remains mounted and
            # fail-closed while the platform is disabled; no new app.state API.
            assert not hasattr(app.state, "lifespan_manager")
            assert any(path.startswith("/webhook/") for path in paths)
            assert "/health/live" in paths
            assert "/health/ready" in paths
        else:
            assert not any(path.startswith("/webhook/") for path in paths)
