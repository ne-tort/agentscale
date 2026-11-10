"""Offline production factory tests; deliberately independent of jobs/conftest."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.activation.state import WakeResult
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import (
    VerificationEvidence,
    manifest_fingerprint,
)
from prodavan.application.plugins.capabilities.payloads import DigestPayloadStore
from prodavan.application.plugins.gateway.ports import BackendResponse
from prodavan.application.plugins.jobs.declarations import (
    PluginJobComposition,
    PluginJobDeclaration,
    ScopedManifestOperationExecutor,
    ScopedManifestOperationHandler,
    build_job_task_specs,
    parse_plugin_job_declarations,
)
from prodavan.core.jobs.plugin_worker import PluginWorkerRegistration
from prodavan.application.plugins.jobs.tasks import (
    ExecutionClaim,
    JobTaskConfigurationError,
    JobTaskContext,
    ManifestJobTasks,
    StaleJobTaskDigest,
)
from prodavan.application.plugins.jobs import declarations as declarations_module
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackend,
    CanonicalBackendResolver,
)
from prodavan.infrastructure.plugins.runtime.proxy import HttpProxyPort

ARTIFACT = "sha256:" + "a" * 64
BACKEND = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64
EMPTY = "sha256:" + "0" * 64
OPERATION = "hello.greetings@1.0:list"
GREET = "hello.greetings@1.0:greet"
DECLARATION = PluginJobDeclaration(
    plugin_id="hello_plugin", version="0.1.0", artifact_digest=ARTIFACT
)


def hello_manifest() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[6]
    return json.loads(
        (root / "packages/plugins/hello-plugin/manifest.json").read_text(encoding="utf-8")
    )


def publication(*, manifest: dict[str, Any] | None = None, backend: str = BACKEND) -> PluginArtifactRecord:
    manifest = manifest or hello_manifest()
    now = datetime.now(UTC)
    bundle_ref = "ghcr.io/ne-tort/plugins/hello_plugin/bundle@" + ARTIFACT
    return PluginArtifactRecord(
        plugin_id="hello_plugin",
        version="0.1.0",
        digest=ARTIFACT,
        image_ref=bundle_ref,
        manifest=manifest,
        manifest_fingerprint=manifest_fingerprint(manifest),
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=now,
        created_at=now,
        verification=VerificationEvidence(
            image_ref=bundle_ref,
            artifact_digest=ARTIFACT,
            manifest_fingerprint=manifest_fingerprint(manifest),
            identity="trusted-workflow",
            issuer="trusted-issuer",
            sbom_verified=True,
            provenance_verified=True,
            observed_at=now,
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            seal=object(),
            scan_verified=True,
            csp_verified=True,
        ),
        publication=PublicationMetadata(
            source_id="hello",
            commit_sha="d" * 40,
            backend_digest=backend,
            backend_image_ref="ghcr.io/ne-tort/plugins/hello_plugin/backend@" + backend,
            run_url="https://example.invalid/runs/1",
        ),
    )


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, **kwargs: Any) -> bool:
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def eval(self, *_args: Any) -> int:
        return 1


class Manager:
    def __init__(self) -> None:
        self.client = Redis()
        self.close_calls = 0

    async def shutdown(self) -> None:
        self.close_calls += 1


class Grants:
    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        raise AssertionError("not used in factory tests")


class Artifacts:
    async def by_digest_async(self, digest: str) -> Any:
        return publication() if digest == ARTIFACT else None


class ExecutionStore:
    renew_interval_seconds = 60.0

    async def begin(self, identity: Any) -> ExecutionClaim:
        return ExecutionClaim(job_id=identity["job_id"], owner="owner", binding_digest="sha256:" + "d" * 64)

    async def renew(self, claim: ExecutionClaim) -> None:
        return None

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        return None

    async def release(self, claim: ExecutionClaim) -> None:
        return None


class Closable:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class Executor:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, context: JobTaskContext, declaration: Any) -> str:
        self.calls.append((context, declaration))
        return "sha256:" + hashlib.sha256(context.job_id.encode()).hexdigest()


def composition(mode: str, *, executor: Any = None) -> tuple[PluginJobComposition, Manager, Any]:
    manager = Manager()
    store = DigestPayloadStore(client=manager.client)
    runtime = Closable()
    repos = SimpleNamespace(grant_store=Grants(), artifact_store=Artifacts())
    return (
        PluginJobComposition(
            mode=mode,
            declarations=(DECLARATION,),
            records=(publication(),),
            repositories=repos,
            session_factory=object(),
            redis=manager,
            payload_store=store,
            executor=executor,
            runtime=runtime,
            pod_executor=None,
            owns_resources=mode == "standalone",
        ),
        manager,
        runtime,
    )


def specs(executor: Any, manager: Manager | None = None) -> dict[str, Any]:
    manager = manager or Manager()
    return build_job_task_specs(
        (publication(),),
        (DECLARATION,),
        executor_provider=lambda: executor,
        payload_store=DigestPayloadStore(client=manager.client),
    )


def test_manifest_mapping_uses_canonical_refs_and_verified_backend_digest() -> None:
    executor = Executor()
    mapped = specs(executor)
    assert set(mapped) == {OPERATION, GREET}
    for operation, spec in mapped.items():
        assert spec.operation == operation
        assert spec.backend_digest == BACKEND
        assert spec.task_name.startswith("prodavan.plugin.job.hello_plugin.")
        assert spec.task_name.endswith(BACKEND.removeprefix("sha256:")[:20])
        assert isinstance(spec.handler, ScopedManifestOperationHandler)
        assert "backend" not in inspect.signature(type(spec.handler).__call__).parameters


def test_invalid_missing_stale_and_unwired_declarations_fail_closed() -> None:
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_declarations_required"):
        parse_plugin_job_declarations("")
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_declarations_invalid"):
        parse_plugin_job_declarations('[{"plugin_id":"hello_plugin","version":"0.1.0"}]')
    stale = PluginJobDeclaration(
        plugin_id="hello_plugin", version="0.1.0", artifact_digest=OTHER
    )
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_publication_binding_stale"):
        build_job_task_specs(
            (publication(),),
            (stale,),
            executor_provider=lambda: Executor(),
            payload_store=DigestPayloadStore(client=Manager().client),
        )
    malformed = hello_manifest()
    malformed["rpc_operations"] = [{"operation": "list"}]
    with pytest.raises(JobTaskConfigurationError):
        build_job_task_specs(
            (publication(manifest=malformed),),
            (DECLARATION,),
            executor_provider=lambda: Executor(),
            payload_store=DigestPayloadStore(client=Manager().client),
        )
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_publication_missing"):
        build_job_task_specs(
            (),
            (),
            executor_provider=lambda: Executor(),
            payload_store=DigestPayloadStore(client=Manager().client),
        )


def test_api_and_cli_share_contract_without_fastapi_or_global_fallback() -> None:
    api, _, _ = composition("api")
    cli, _, _ = composition("standalone", executor=Executor())
    assert api.task_specs.keys() == cli.task_specs.keys()
    for key in api.task_specs:
        left, right = api.task_specs[key], cli.task_specs[key]
        assert left.task_name == right.task_name
        assert left.backend_digest == right.backend_digest == BACKEND
        assert type(left.handler) is type(right.handler)
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_executor_unwired"):
        api._require_executor()
    source = inspect.getsource(declarations_module)
    # Design comments may mention app.state; executable fallback may not use it.
    app_state_reads = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and node.attr == "state"
        and isinstance(node.value, ast.Name)
        and node.value.id == "app"
    ]
    assert app_state_reads == []
    assert "get_plugin_platform" not in source
    assert "get_worker_manager" not in source


def test_api_override_binds_same_manifest_tasks_before_platform() -> None:
    api, _, _ = composition("api")
    registered: list[ManifestJobTasks] = []
    overrides = api.api_overrides(registered.append)
    tasks = ManifestJobTasks(
        tasks=overrides["job_tasks"],
        grant_store=Grants(),
        artifact_registry=Artifacts(),
        execution_store=ExecutionStore(),
        async_runner=lambda _: "sha256:" + "d" * 64,
    )
    overrides["job_worker_registration"](tasks)
    assert registered == [tasks]
    assert api._tasks is tasks
    assert overrides["repositories"].artifact_store is not None
    assert overrides["digest_payload_store"] is api._payload_store


def test_api_binds_platform_owned_scoped_runtime() -> None:
    api, _, _ = composition("api")
    registered: list[ManifestJobTasks] = []
    overrides = api.api_overrides(registered.append)
    tasks = ManifestJobTasks(
        tasks=overrides["job_tasks"],
        grant_store=Grants(),
        artifact_registry=Artifacts(),
        execution_store=ExecutionStore(),
        async_runner=lambda _: "sha256:" + "d" * 64,
    )
    overrides["job_worker_registration"](tasks)
    ctx = context()
    resolver, activator, _ = runtime_for(ctx, Sender())
    pod_executor = SimpleNamespace(
        _resolver=resolver,
        scoped_identity=True,
        digest_bound_channel=True,
    )
    registry = SimpleNamespace(resolve=lambda name: SimpleNamespace(_executor=pod_executor))
    api.bind_platform(SimpleNamespace(activator=activator, registry=registry))
    assert isinstance(api._require_executor(), ScopedManifestOperationExecutor)
    assert api._require_executor()._resolver is resolver
    assert api._require_executor()._proxy is activator._proxy


class Sender:
    def __init__(self, *, status: int = 200, body: bytes = b'["real"]') -> None:
        self.status = status
        self.body = body
        self.requests: list[Any] = []

    async def send(self, request: Any) -> BackendResponse:
        self.requests.append(request)
        return BackendResponse(status_code=self.status, body=self.body)


class Activator:
    def __init__(self, context: JobTaskContext, proxy: HttpProxyPort) -> None:
        self._proxy = proxy
        self.context = context
        self.wake_calls = 0
        self.register_calls = 0
        self.complete_calls = 0

    async def register_job(self, instance_id: str, job_id: str) -> Any:
        self.register_calls += 1
        return SimpleNamespace(instance_id=instance_id, job_id=job_id)

    async def complete_job(self, instance_id: str, job_id: str) -> None:
        self.complete_calls += 1

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> WakeResult:
        self.wake_calls += 1
        now = datetime.now(UTC)
        return WakeResult(
            plugin_instance_id=instance_id,
            token="fresh-token",
            jti="jti",
            claims={
                "company_id": self.context.company_id,
                "plugin_id": self.context.plugin_id,
                "plugin_instance_id": instance_id,
                "artifact_digest": self.context.artifact_digest,
                "grant_id": self.context.grant_id,
                "sub": f"plugin:{self.context.plugin_id}:{self.context.company_id}:{instance_id}",
                "scopes": ["jobs"],
            },
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            ready_at=now,
            waited_seconds=0.0,
            previous_replicas=1,
        )


def context(*, operation: str = OPERATION, arguments_digest: str = EMPTY) -> JobTaskContext:
    return JobTaskContext(
        job_id="job_1",
        operation=operation,
        task_name="prodavan.plugin.job.test",
        company_id="company",
        plugin_id="hello_plugin",
        plugin_instance_id="instance",
        grant_id="grant",
        run_id="run",
        arguments_digest=arguments_digest,
        artifact_digest=ARTIFACT,
        backend_digest=BACKEND,
    )


def runtime_for(context: JobTaskContext, sender: Sender) -> tuple[Any, Activator, HttpProxyPort]:
    config = object.__new__(RuntimeConfig)
    object.__setattr__(config, "backend_timeout_seconds", 30.0)
    object.__setattr__(config, "stream_timeout_seconds", 300.0)
    proxy = HttpProxyPort(config=config, backend_http=sender)
    resolver = object.__new__(CanonicalBackendResolver)
    backend = CanonicalBackend(
        company_id=context.company_id,
        plugin_id=context.plugin_id,
        plugin_instance_id=context.plugin_instance_id,
        artifact_digest=context.artifact_digest,
        backend_digest=context.backend_digest,
        resource_name="plugin-hello-instance",
        namespace="plugins",
        service_port=8080,
        manifest=hello_manifest(),
    )
    calls: list[Any] = []

    async def resolve(identity: Any) -> CanonicalBackend:
        calls.append(identity)
        return backend

    resolver.resolve = resolve
    resolver.calls = calls
    return resolver, Activator(context, proxy), proxy


@pytest.mark.asyncio
async def test_scoped_executor_calls_real_route_twice_fenced_and_digests_response() -> None:
    ctx = context()
    sender = Sender()
    resolver, activator, proxy = runtime_for(ctx, sender)
    store = DigestPayloadStore(client=Manager().client)
    executor = ScopedManifestOperationExecutor(
        resolver=resolver, activator=activator, proxy=proxy, payload_store=store
    )
    declaration = specs(executor)[OPERATION]
    result = await declaration.handler(ctx)
    assert result.startswith("sha256:")
    assert len(resolver.calls) == 2
    assert activator.wake_calls == 1
    assert activator.register_calls == 1
    assert activator.complete_calls == 1
    assert len(sender.requests) == 1
    request = sender.requests[0]
    assert request.method == "GET"
    assert request.path == "/api/greetings/list"
    assert request.body == b""
    assert request.headers["X-Plugin-ID"] == "hello_plugin"
    assert request.headers["X-Plugin-Instance-ID"] == "instance"
    assert request.headers["X-Plugin-Artifact-Digest"] == ARTIFACT


@pytest.mark.asyncio
async def test_scoped_executor_uses_digest_payload_and_denies_stale_backend() -> None:
    body = b'{"company_id":"company","message":"real"}'
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    ctx = context(operation=GREET, arguments_digest=digest)
    sender = Sender(status=201, body=b'{"id":"real"}')
    resolver, activator, proxy = runtime_for(ctx, sender)
    manager = Manager()
    store = DigestPayloadStore(client=manager.client)
    assert await store.put(
        company_id=ctx.company_id,
        plugin_id=ctx.plugin_id,
        plugin_instance_id=ctx.plugin_instance_id,
        payload=body,
    ) == digest
    executor = ScopedManifestOperationExecutor(
        resolver=resolver, activator=activator, proxy=proxy, payload_store=store
    )
    declaration = specs(executor, manager)[GREET]
    assert (await declaration.handler(ctx)).startswith("sha256:")
    assert sender.requests[0].method == "POST"
    assert sender.requests[0].path == "/api/greetings"
    assert sender.requests[0].body == body

    stale_ctx = context()
    stale_sender = Sender()
    stale_resolver, stale_activator, stale_proxy = runtime_for(stale_ctx, stale_sender)
    backend = await stale_resolver.resolve({})
    object.__setattr__(backend, "backend_digest", OTHER)
    stale_executor = ScopedManifestOperationExecutor(
        resolver=stale_resolver,
        activator=stale_activator,
        proxy=stale_proxy,
        payload_store=DigestPayloadStore(client=Manager().client),
    )
    stale_declaration = specs(stale_executor)[OPERATION]
    with pytest.raises(StaleJobTaskDigest):
        await stale_declaration.handler(stale_ctx)
    assert stale_sender.requests == []
    assert stale_activator.wake_calls == 0


@pytest.mark.asyncio
async def test_post_side_effect_digest_change_is_not_reported_as_success() -> None:
    ctx = context()
    sender = Sender()
    resolver, activator, proxy = runtime_for(ctx, sender)
    original_resolve = resolver.resolve
    backend = await original_resolve({})
    resolver.calls.clear()

    async def changing_resolve(identity: Any) -> CanonicalBackend:
        current = await original_resolve(identity)
        if len(resolver.calls) == 2:
            object.__setattr__(backend, "backend_digest", OTHER)
        return current

    resolver.resolve = changing_resolve
    executor = ScopedManifestOperationExecutor(
        resolver=resolver,
        activator=activator,
        proxy=proxy,
        payload_store=DigestPayloadStore(client=Manager().client),
    )
    declaration = specs(executor)[OPERATION]
    with pytest.raises(StaleJobTaskDigest):
        await declaration.handler(ctx)
    assert len(sender.requests) == 1
    assert activator.complete_calls == 1


@pytest.mark.asyncio
async def test_close_once_and_api_does_not_close_platform_resources(monkeypatch: Any) -> None:
    disposed = 0

    async def dispose() -> None:
        nonlocal disposed
        disposed += 1

    from prodavan.infrastructure.persistence import database

    monkeypatch.setattr(database, "dispose_engine", dispose)
    cli, manager, runtime = composition("standalone", executor=Executor())
    tasks = cli()
    assert cli() is tasks
    await cli.aclose()
    await cli.aclose()
    assert tasks._closed is True
    assert runtime.close_calls == 1
    assert manager.close_calls == 1
    assert disposed == 1

    api, _, _ = composition("api")
    await api.aclose()
    assert api._closed is False


def test_platform_disabled_does_not_parse_or_allocate_declarations() -> None:
    with pytest.raises(JobTaskConfigurationError, match="plugin_job_platform_disabled"):
        declarations_module._settings_declarations(
            SimpleNamespace(
                plugin_platform_enabled=False,
                plugin_job_declarations="not-json-and-deliberately-unparsed",
            )
        )


def test_deferred_worker_bootstrap_has_no_import_time_factory_or_disabled_work() -> None:
    calls = 0

    def factory() -> ManifestJobTasks:
        nonlocal calls
        calls += 1
        raise AssertionError("factory must not run before worker_process_init")

    deferred = PluginWorkerRegistration(
        enabled=True,
        tasks_factory=factory,
        defer_until_worker_init=True,
    )
    deferred.register(object())
    assert calls == 0

    disabled = PluginWorkerRegistration(
        enabled=False,
        tasks_factory=factory,
        defer_until_worker_init=True,
    )
    disabled.register(object())
    assert calls == 0
