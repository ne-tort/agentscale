"""Server-owned manifest job declarations and scoped backend execution.

The composition root selects exact verified artifact publications. This module
turns their signed rpc_operations into concrete Celery specs; no request can
supply a task name, backend digest, route, or handler. Execution re-resolves the
canonical instance/deployment and uses the platform activator and HTTP proxy.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from prodavan.core.lifespan.manager import LifespanManager

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.activation.ports import MintedClaims
from prodavan.application.plugins.activation.state import IdlePolicy, WakeResult
from prodavan.application.plugins.artifacts.oci import is_valid_digest, parse_oci_ref
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord, PublicationMetadata
from prodavan.application.plugins.artifacts.verification import manifest_fingerprint
from prodavan.application.plugins.capabilities.payloads import DigestPayloadStore
from prodavan.application.plugins.gateway.ports import canonical_backend_path
from prodavan.application.plugins.jobs.tasks import (
    JobTaskAccessDenied,
    JobTaskConfigurationError,
    JobTaskContext,
    JobTaskError,
    JobTaskExecutionError,
    JobTaskSpec,
    ManifestJobTasks,
    RedisJobExecutionStore,
    StaleJobTaskDigest,
    job_operation_key,
)
from prodavan.application.plugins.wiring.config import PluginPlatformConfig
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus
from prodavan.domain.plugins.validation import validate_manifest
from prodavan.infrastructure.plugins.runtime.pod_executor import (
    CanonicalBackend,
    CanonicalBackendResolver,
    K8sPodExecutor,
)
from prodavan.infrastructure.plugins.runtime.proxy import HeldRequest, HttpProxyPort

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_TASK_PART = re.compile(r"[^A-Za-z0-9_.:-]+")
_EMPTY_DIGEST = "sha256:" + "0" * 64
_MAX_DECLARATIONS = 32
_MAX_JSON_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class PluginJobDeclaration:
    """Exact server-selected artifact, never a backend/task/digest override."""

    plugin_id: str
    version: str
    artifact_digest: str

    def __post_init__(self) -> None:
        if _PLUGIN_ID.fullmatch(self.plugin_id) is None:
            raise JobTaskConfigurationError("plugin_job_declaration_plugin_invalid")
        if _VERSION.fullmatch(self.version) is None:
            raise JobTaskConfigurationError("plugin_job_declaration_version_invalid")
        if _DIGEST.fullmatch(self.artifact_digest) is None:
            raise JobTaskConfigurationError("plugin_job_declaration_artifact_invalid")


@dataclass(frozen=True, slots=True)
class ManifestOperation:
    key: str
    capability_id: str
    capability_version: str
    operation: str
    method: str
    backend_path: str


@dataclass(frozen=True, slots=True)
class PublishedOperation:
    artifact: PluginArtifactRecord
    declaration: ManifestOperation


class ScopedOperationExecutor(Protocol):
    async def execute(self, context: JobTaskContext, declaration: ManifestOperation) -> str: ...


class ExecutorProvider(Protocol):
    def __call__(self) -> ScopedOperationExecutor: ...


def parse_plugin_job_declarations(raw: str) -> tuple[PluginJobDeclaration, ...]:
    """Parse the typed JSON setting only when the platform is enabled."""

    if not isinstance(raw, str) or not raw.strip():
        raise JobTaskConfigurationError("plugin_job_declarations_required")
    if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
        raise JobTaskConfigurationError("plugin_job_declarations_invalid")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise JobTaskConfigurationError("plugin_job_declarations_invalid") from None
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_DECLARATIONS:
        raise JobTaskConfigurationError("plugin_job_declarations_invalid")
    result: list[PluginJobDeclaration] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"plugin_id", "version", "artifact_digest"}:
            raise JobTaskConfigurationError("plugin_job_declarations_invalid")
        try:
            declaration = PluginJobDeclaration(
                plugin_id=item["plugin_id"],
                version=item["version"],
                artifact_digest=item["artifact_digest"],
            )
        except (TypeError, ValueError):
            raise JobTaskConfigurationError("plugin_job_declarations_invalid") from None
        key = (declaration.plugin_id, declaration.version, declaration.artifact_digest)
        if key in seen:
            raise JobTaskConfigurationError("plugin_job_declaration_duplicate")
        seen.add(key)
        result.append(declaration)
    return tuple(result)


def _manifest_operation(raw: Any) -> ManifestOperation:
    if not isinstance(raw, Mapping) or set(raw) != {
        "capability", "operation", "http_method", "backend_path", "public",
    }:
        raise JobTaskConfigurationError("plugin_job_manifest_operation_invalid")
    capability = raw.get("capability")
    if not isinstance(capability, Mapping) or set(capability) != {"id", "version"}:
        raise JobTaskConfigurationError("plugin_job_manifest_operation_invalid")
    capability_id = capability.get("id")
    version = capability.get("version")
    operation = raw.get("operation")
    method = raw.get("http_method")
    path = raw.get("backend_path")
    if (
        not isinstance(capability_id, str)
        or not isinstance(version, str)
        or not isinstance(operation, str)
        or method not in {"GET", "POST"}
        or not isinstance(path, str)
        or not path.startswith("/api/")
        or "?" in path
        or "#" in path
        or ".." in path.split("/")
        or raw.get("public") is not False
    ):
        raise JobTaskConfigurationError("plugin_job_manifest_operation_invalid")
    try:
        if canonical_backend_path(path) != path:
            raise ValueError("non-canonical backend path")
    except (TypeError, ValueError):
        raise JobTaskConfigurationError("plugin_job_manifest_operation_invalid") from None
    key = job_operation_key(capability_id, version, operation)
    return ManifestOperation(
        key=key,
        capability_id=capability_id,
        capability_version=version,
        operation=operation,
        method=method,
        backend_path=path,
    )


def _verified_record(record: Any, selected: PluginJobDeclaration) -> PluginArtifactRecord:
    if not isinstance(record, PluginArtifactRecord):
        raise JobTaskConfigurationError("plugin_job_publication_missing")
    if (
        record.plugin_id != selected.plugin_id
        or record.version != selected.version
        or record.digest != selected.artifact_digest
    ):
        raise JobTaskConfigurationError("plugin_job_publication_binding_stale")
    evidence = record.verification
    publication = record.publication
    if (
        record.status is not AppLifecycleStatus.PUBLISHED
        or record.outcome is not RegistryOutcome.APPLIED
        or record.quarantine is not None
        or record.published_at is None
        or evidence is None
        or not evidence.verified
        or not evidence.identity.strip()
        or not evidence.issuer.strip()
        or evidence.artifact_digest != record.digest
        or evidence.image_ref != record.image_ref
        or evidence.manifest_fingerprint != record.manifest_fingerprint
        or not all((evidence.sbom_verified, evidence.provenance_verified, evidence.scan_verified, evidence.csp_verified))
        or not isinstance(publication, PublicationMetadata)
    ):
        raise JobTaskConfigurationError("plugin_job_publication_unverified")
    backend_digest = publication.backend_digest
    if not is_valid_digest(backend_digest) or backend_digest == record.digest:
        raise JobTaskConfigurationError("plugin_job_backend_digest_invalid")
    try:
        backend_ref = parse_oci_ref(publication.backend_image_ref)
        manifest = validate_manifest(dict(record.manifest))
    except Exception:
        raise JobTaskConfigurationError("plugin_job_manifest_invalid") from None
    if backend_ref.digest != backend_digest:
        raise JobTaskConfigurationError("plugin_job_backend_binding_invalid")
    if manifest_fingerprint(record.manifest) != record.manifest_fingerprint:
        raise JobTaskConfigurationError("plugin_job_manifest_fingerprint_stale")
    if manifest.plugin_id != record.plugin_id or manifest.version != record.version:
        raise JobTaskConfigurationError("plugin_job_manifest_binding_invalid")
    return record


def published_operations(
    records: Sequence[PluginArtifactRecord],
    declarations: Sequence[PluginJobDeclaration],
) -> tuple[PublishedOperation, ...]:
    """Resolve exact verified publications and validate every signed operation."""

    if not declarations or len(records) != len(declarations):
        raise JobTaskConfigurationError("plugin_job_publication_missing")
    if any(not isinstance(item, PluginJobDeclaration) for item in declarations):
        raise JobTaskConfigurationError("plugin_job_declarations_invalid")
    if any(not isinstance(item, PluginArtifactRecord) for item in records):
        raise JobTaskConfigurationError("plugin_job_publication_missing")
    by_key = {(item.plugin_id, item.version): item for item in records}
    if len(by_key) != len(records):
        raise JobTaskConfigurationError("plugin_job_publication_duplicate")
    result: list[PublishedOperation] = []
    operations: set[str] = set()
    paths: set[str] = set()
    for selected in declarations:
        record = _verified_record(by_key.get((selected.plugin_id, selected.version)), selected)
        raw_operations = record.manifest.get("rpc_operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise JobTaskConfigurationError("plugin_job_manifest_operations_required")
        for raw in raw_operations:
            declaration = _manifest_operation(raw)
            if declaration.key in operations:
                raise JobTaskConfigurationError("plugin_job_operation_duplicate")
            if declaration.backend_path in paths:
                raise JobTaskConfigurationError("plugin_job_backend_path_duplicate")
            operations.add(declaration.key)
            paths.add(declaration.backend_path)
            result.append(PublishedOperation(record, declaration))
    if not result:
        raise JobTaskConfigurationError("plugin_job_task_mapping_required")
    return tuple(result)


def _task_name(item: PublishedOperation) -> str:
    publication = item.artifact.publication
    assert publication is not None
    raw_plugin = _TASK_PART.sub("_", item.artifact.plugin_id)
    raw_operation = _TASK_PART.sub("_", item.declaration.key)
    suffix = publication.backend_digest.removeprefix("sha256:")[:20]
    name = f"prodavan.plugin.job.{raw_plugin}.{raw_operation}.{suffix}"
    if len(name) > 255:
        raise JobTaskConfigurationError("plugin_job_task_name_invalid")
    return name


def build_job_task_specs(
    records: Sequence[PluginArtifactRecord],
    declarations: Sequence[PluginJobDeclaration],
    *,
    executor_provider: ExecutorProvider,
    payload_store: DigestPayloadStore,
) -> dict[str, JobTaskSpec]:
    """Build the non-empty server allowlist consumed by ManifestJobTasks."""

    if not callable(executor_provider) or not isinstance(payload_store, DigestPayloadStore):
        raise JobTaskConfigurationError("plugin_job_executor_required")
    specs: dict[str, JobTaskSpec] = {}
    for item in published_operations(records, declarations):
        handler = ScopedManifestOperationHandler(
            item.declaration,
            executor_provider=executor_provider,
            payload_store=payload_store,
        )
        specs[item.declaration.key] = JobTaskSpec(
            operation=item.declaration.key,
            task_name=_task_name(item),
            backend_digest=item.artifact.publication.backend_digest,  # type: ignore[union-attr]
            handler=handler,
        )
    if not specs:
        raise JobTaskConfigurationError("plugin_job_task_mapping_required")
    return specs


class ScopedManifestOperationHandler:
    """One real signed operation bound to a scoped executor and payload store."""

    def __init__(
        self,
        declaration: ManifestOperation,
        *,
        executor_provider: ExecutorProvider,
        payload_store: DigestPayloadStore,
    ) -> None:
        self._declaration = declaration
        self._executor_provider = executor_provider
        self._payload_store = payload_store

    async def __call__(self, context: JobTaskContext) -> str:
        if context.operation != self._declaration.key:
            raise JobTaskAccessDenied("plugin_job_operation_binding_mismatch")
        executor = self._executor_provider()
        if not callable(getattr(executor, "execute", None)):
            raise JobTaskConfigurationError("plugin_job_executor_unwired")
        return await executor.execute(context, self._declaration)


class ScopedManifestOperationExecutor:
    """Execute signed routes through canonical resolver + activator + HTTP proxy."""

    def __init__(
        self,
        *,
        resolver: CanonicalBackendResolver,
        activator: Any,
        proxy: HttpProxyPort,
        payload_store: DigestPayloadStore,
    ) -> None:
        if not isinstance(resolver, CanonicalBackendResolver):
            raise JobTaskConfigurationError("plugin_job_scoped_resolver_required")
        if not all(
            callable(getattr(activator, method, None))
            for method in ("wake", "register_job", "complete_job")
        ):
            raise JobTaskConfigurationError("plugin_job_activator_required")
        if not isinstance(proxy, HttpProxyPort):
            raise JobTaskConfigurationError("plugin_job_scoped_proxy_required")
        if not isinstance(payload_store, DigestPayloadStore):
            raise JobTaskConfigurationError("plugin_job_payload_store_required")
        self._resolver = resolver
        self._activator = activator
        self._proxy = proxy
        self._payload_store = payload_store

    @staticmethod
    def _identity(context: JobTaskContext) -> dict[str, str]:
        return {
            "company_id": context.company_id,
            "plugin_id": context.plugin_id,
            "plugin_instance_id": context.plugin_instance_id,
            "artifact_digest": context.artifact_digest,
        }

    @staticmethod
    def _check_backend(
        backend: CanonicalBackend,
        context: JobTaskContext,
        declaration: ManifestOperation,
    ) -> None:
        if (
            backend.company_id != context.company_id
            or backend.plugin_id != context.plugin_id
            or backend.plugin_instance_id != context.plugin_instance_id
            or backend.artifact_digest != context.artifact_digest
        ):
            raise JobTaskAccessDenied("plugin_job_backend_identity_mismatch")
        if backend.backend_digest != context.backend_digest:
            raise StaleJobTaskDigest()
        raw_operations = backend.manifest.get("rpc_operations")
        if not isinstance(raw_operations, list):
            raise JobTaskAccessDenied("plugin_job_operation_not_declared")
        declarations = [_manifest_operation(raw) for raw in raw_operations]
        if declaration not in declarations:
            raise JobTaskAccessDenied("plugin_job_operation_not_declared")

    async def _lease(self, context: JobTaskContext) -> WakeResult:
        try:
            lease = await self._activator.wake(context.plugin_instance_id, timeout=None)
        except Exception:
            raise JobTaskExecutionError("plugin_job_backend_activation_failed") from None
        if not isinstance(lease, WakeResult):
            raise JobTaskExecutionError("plugin_job_backend_activation_invalid")
        expected = {
            "company_id": context.company_id,
            "plugin_id": context.plugin_id,
            "plugin_instance_id": context.plugin_instance_id,
            "artifact_digest": context.artifact_digest,
            "grant_id": context.grant_id,
            "sub": f"plugin:{context.plugin_id}:{context.company_id}:{context.plugin_instance_id}",
        }
        scopes = lease.claims.get("scopes") if isinstance(lease.claims, Mapping) else None
        if (
            lease.plugin_instance_id != context.plugin_instance_id
            or not isinstance(lease.claims, Mapping)
            or any(lease.claims.get(key) != value for key, value in expected.items())
            or not isinstance(scopes, (list, tuple))
            or "jobs" not in scopes
            or any(not isinstance(scope, str) for scope in scopes)
        ):
            raise JobTaskAccessDenied("plugin_job_claims_binding_mismatch")
        return lease

    async def _body(self, context: JobTaskContext, declaration: ManifestOperation) -> bytes:
        if context.arguments_digest == _EMPTY_DIGEST:
            return b""
        if declaration.method != "POST":
            raise JobTaskAccessDenied("plugin_job_get_arguments_unsupported")
        body = await self._payload_store.get(
            company_id=context.company_id,
            plugin_id=context.plugin_id,
            plugin_instance_id=context.plugin_instance_id,
            digest=context.arguments_digest,
        )
        if body is None:
            raise JobTaskAccessDenied("plugin_job_arguments_payload_missing")
        if hashlib.sha256(body).hexdigest() != context.arguments_digest.removeprefix("sha256:"):
            raise JobTaskAccessDenied("plugin_job_arguments_digest_mismatch")
        return body

    @staticmethod
    def _validate_body_scope(body: bytes, lease: WakeResult) -> None:
        if not body:
            return
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise JobTaskAccessDenied("plugin_job_arguments_json_invalid") from None
        if not isinstance(value, Mapping):
            raise JobTaskAccessDenied("plugin_job_arguments_object_required")
        for name in ("company_id", "cabinet_id", "project_id"):
            if name in value and value[name] != lease.claims.get(name):
                raise JobTaskAccessDenied("plugin_job_arguments_scope_mismatch")

    async def execute(self, context: JobTaskContext, declaration: ManifestOperation) -> str:
        try:
            backend = await self._resolver.resolve(self._identity(context))
            self._check_backend(backend, context, declaration)
            lease = await self._lease(context)
            body = await self._body(context, declaration)
            self._validate_body_scope(body, lease)
            claims = MintedClaims(
                token=lease.token,
                jti=lease.jti,
                issued_at=lease.issued_at,
                expires_at=lease.expires_at,
                claims=dict(lease.claims),
            )
            await self._activator.register_job(context.plugin_instance_id, context.job_id)
            try:
                response = await self._proxy.forward(
                    plugin_instance_id=context.plugin_instance_id,
                    request=HeldRequest(
                        method=declaration.method,
                        path=declaration.backend_path,
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        body=body,
                        stream=False,
                    ),
                    claims=claims,
                )
                # Fence the running digest after the side effect as well. A post-side-
                # effect mismatch is an explicit failure, never a manufactured result.
                current = await self._resolver.resolve(self._identity(context))
                self._check_backend(current, context, declaration)
            finally:
                await self._activator.complete_job(context.plugin_instance_id, context.job_id)
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskExecutionError("plugin_job_backend_execution_failed") from None
        status = getattr(response, "status_code", None)
        response_body = getattr(response, "body", None)
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 200 <= status < 300
            or not isinstance(response_body, bytes)
            or getattr(response, "stream", None) is not None
        ):
            raise JobTaskExecutionError("plugin_job_backend_response_invalid")
        response_digest = hashlib.sha256(response_body).hexdigest()
        canonical = json.dumps(
            {
                "job_id": context.job_id,
                "operation": context.operation,
                "backend_digest": context.backend_digest,
                "status_code": status,
                "response_sha256": response_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(canonical).hexdigest()


class PluginJobComposition:
    """API override provider or standalone owned ManifestJobTasks factory."""

    def __init__(
        self,
        *,
        mode: str,
        declarations: Sequence[PluginJobDeclaration],
        records: Sequence[PluginArtifactRecord],
        repositories: Any,
        session_factory: Any,
        redis: Any,
        payload_store: DigestPayloadStore,
        executor: ScopedOperationExecutor | None,
        runtime: Any = None,
        pod_executor: K8sPodExecutor | None,
        owns_resources: bool,
    ) -> None:
        if mode not in {"api", "standalone"}:
            raise JobTaskConfigurationError("plugin_job_composition_mode_invalid")
        self._mode = mode
        self._repositories = repositories
        self._session_factory = session_factory
        self._redis = redis
        self._payload_store = payload_store
        self._executor = executor
        self._runtime = runtime
        self._pod_executor = pod_executor
        self._owns_resources = owns_resources
        self._closed = False
        self._tasks: ManifestJobTasks | None = None
        self._specs = build_job_task_specs(
            records,
            declarations,
            executor_provider=self._require_executor,
            payload_store=payload_store,
        )

    @property
    def session_factory(self) -> Any:
        return self._session_factory

    @property
    def task_specs(self) -> Mapping[str, JobTaskSpec]:
        return dict(self._specs)

    def _require_executor(self) -> ScopedOperationExecutor:
        if self._closed or self._executor is None:
            raise JobTaskConfigurationError("plugin_job_executor_unwired")
        return self._executor

    def api_overrides(self, registration_hook: Callable[[ManifestJobTasks], Any]) -> dict[str, Any]:
        if self._mode != "api" or not callable(registration_hook):
            raise JobTaskConfigurationError("plugin_job_api_registration_required")

        def register(tasks: ManifestJobTasks) -> None:
            if self._tasks is not None and self._tasks is not tasks:
                raise JobTaskConfigurationError("plugin_job_api_tasks_conflict")
            registration_hook(tasks)
            self._tasks = tasks

        return {
            "repositories": self._repositories,
            "digest_payload_store": self._payload_store,
            "job_tasks": self._specs,
            "job_worker_registration": register,
        }

    def bind_platform(self, platform: Any) -> None:
        """Bind the exact platform-owned activator/proxy, never FastAPI state."""

        if self._mode != "api" or self._closed or self._tasks is None:
            raise JobTaskConfigurationError("plugin_job_api_binding_invalid")
        try:
            activator = platform.activator
            pods = platform.registry.resolve("pods")
            pod_executor = pods._executor
            proxy = activator._proxy
            resolver = pod_executor._resolver
        except Exception:
            raise JobTaskConfigurationError("plugin_job_scoped_runtime_unwired") from None
        if getattr(pod_executor, "scoped_identity", False) is not True or getattr(
            pod_executor, "digest_bound_channel", False
        ) is not True:
            raise JobTaskConfigurationError("plugin_job_scoped_runtime_unwired")
        self._executor = ScopedManifestOperationExecutor(
            resolver=resolver,
            activator=activator,
            proxy=proxy,
            payload_store=self._payload_store,
        )

    def __call__(self) -> ManifestJobTasks:
        if self._mode != "standalone" or self._closed:
            raise JobTaskConfigurationError("plugin_job_standalone_factory_invalid")
        self._require_executor()
        if self._tasks is None:
            from prodavan.core.jobs.async_runner import run_async
            from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

            self._tasks = ManifestJobTasks(
                tasks=self._specs,
                grant_store=self._repositories.grant_store,
                artifact_registry=self._repositories.artifact_store,
                execution_store=RedisJobExecutionStore(redis=ManagerRedisClient(self._redis)),
                async_runner=run_async,
            )
        return self._tasks

    async def aclose(self) -> None:
        """Standalone owner closes once; API resources remain platform-owned."""

        if self._closed or not self._owns_resources:
            return
        self._closed = True
        resources = (self._tasks, self._pod_executor, self._runtime, self._payload_store, self._redis)
        seen: set[int] = set()
        for resource in resources:
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "aclose", None)
            if not callable(close):
                close = getattr(resource, "shutdown", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        from prodavan.infrastructure.persistence.database import dispose_engine

        await dispose_engine()


def _load_selected_records(declarations: Sequence[PluginJobDeclaration]) -> list[PluginArtifactRecord]:
    """Read selectors before the long-lived session factory is captured."""

    from prodavan.core.jobs.async_runner import run_async

    async def load() -> list[PluginArtifactRecord]:
        from prodavan.infrastructure.persistence.database import get_session_factory
        from prodavan.infrastructure.persistence.repositories.plugins.artifacts import SqlArtifactRegistryStore

        store = SqlArtifactRegistryStore(session_factory=get_session_factory())
        records: list[PluginArtifactRecord] = []
        for selected in declarations:
            record = await store.get_async(selected.plugin_id, selected.version)
            if record is None:
                raise JobTaskConfigurationError("plugin_job_publication_missing")
            records.append(record)
        return records

    try:
        return run_async(load())
    except JobTaskError:
        raise
    except Exception:
        raise JobTaskConfigurationError("plugin_job_publication_load_failed") from None


def _settings_declarations(settings: Any) -> tuple[PluginJobDeclaration, ...]:
    if not bool(getattr(settings, "plugin_platform_enabled", False)):
        raise JobTaskConfigurationError("plugin_job_platform_disabled")
    return parse_plugin_job_declarations(getattr(settings, "plugin_job_declarations", ""))


def build_api_plugin_job_composition(settings: Any, *, redis: Any) -> PluginJobComposition:
    """Build API specs/shared stores before install_plugin_platform."""

    declarations = _settings_declarations(settings)
    records = _load_selected_records(declarations)
    from prodavan.infrastructure.persistence.database import get_session_factory
    from prodavan.infrastructure.persistence.repositories.plugins.build import build_plugin_repositories
    from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

    session_factory = get_session_factory()
    repositories = build_plugin_repositories(session_factory, redis)
    payload_store = DigestPayloadStore(client=ManagerRedisClient(redis))
    try:
        return PluginJobComposition(
            mode="api",
            declarations=declarations,
            records=records,
            repositories=repositories,
            session_factory=session_factory,
            redis=redis,
            payload_store=payload_store,
            executor=None,
            pod_executor=None,
            owns_resources=False,
        )
    except Exception:
        from prodavan.core.jobs.async_runner import run_async
        from prodavan.infrastructure.persistence.database import dispose_engine

        async def cleanup() -> None:
            await payload_store.aclose()
            await dispose_engine()

        run_async(cleanup())
        raise


def install_api_plugin_platform(
    app: Any,
    settings: Any,
    *,
    lifespan_manager: LifespanManager,
) -> PluginJobComposition:
    """Compose the enabled HTTP platform once for both API entrypoints.

    The returned composition owns the server-selected declarations and exposes
    the session factory used by the main API's independent source composition.
    Job handlers are bound only to the ``PluginPlatform`` returned by the real
    installer. Worker registration comes from the lifespan-managed WorkerManager;
    neither this helper nor the standalone gateway resolves jobs through app.state.
    """

    from prodavan.application.plugins.wiring.install import (
        LazyRedisManager,
        install_plugin_platform,
    )
    from prodavan.core.infra.worker_manager import WorkerManager

    # create_app() runs before lifespan startup. LazyRedisManager resolves the
    # real RedisManager on first operation, after startup; it is not an
    # in-memory replacement or a second Redis owner.
    redis = LazyRedisManager()
    plugin_jobs = build_api_plugin_job_composition(settings, redis=redis)
    worker = lifespan_manager.get("worker")
    if not isinstance(worker, WorkerManager):
        raise JobTaskConfigurationError("plugin_job_worker_registration_required")
    registration_hook = worker.plugin_job_registration
    if not callable(registration_hook):
        raise JobTaskConfigurationError("plugin_job_worker_registration_required")

    # Explicit per-app manager hand-off for BOTH entrypoints. The standalone
    # gateway deliberately does not publish app.state.lifespan_manager (legacy
    # disabled-surface contract), so the installer discovers the manager from
    # these overrides; main.py additionally publishes it on app.state. Plugin
    # lifecycle is registered only through LifespanManager.register: Starlette
    # on_startup/on_shutdown never runs on this custom lifespan (and is removed
    # on the pinned Starlette), so no add_event_handler fallback exists.
    overrides = dict(plugin_jobs.api_overrides(registration_hook))
    overrides.setdefault("lifespan_manager", lifespan_manager)
    platform = install_plugin_platform(
        app,
        settings,
        session=plugin_jobs.session_factory,
        redis=redis,
        overrides=overrides,
    )
    if platform is None:
        raise JobTaskConfigurationError("plugin_job_platform_binding_required")
    # Handlers borrow only the returned platform's scoped runtime. They never
    # resolve FastAPI state and share the install-created ManifestJobTasks.
    plugin_jobs.bind_platform(platform)
    return plugin_jobs


def build_standalone_plugin_job_composition(settings: Any) -> PluginJobComposition:
    """Build an equivalent CLI factory with one explicit resource owner."""

    declarations = _settings_declarations(settings)
    records = _load_selected_records(declarations)
    from prodavan.core.infra.redis_manager import RedisManager
    from prodavan.core.jobs.async_runner import run_async
    from prodavan.infrastructure.persistence.database import get_session_factory
    from prodavan.infrastructure.persistence.repositories.plugins.build import build_plugin_repositories
    from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient
    from prodavan.infrastructure.plugins.runtime.build import build_runtime_ports

    redis = RedisManager(url=getattr(settings, "redis_url", None), required=True)
    runtime: Any = None
    pod_executor: K8sPodExecutor | None = None
    payload_store: DigestPayloadStore | None = None
    try:
        run_async(redis.startup())
        session_factory = get_session_factory()
        repositories = build_plugin_repositories(session_factory, redis)
        runtime = build_runtime_ports(settings, instance_store=repositories.instance_store)
        from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
        from prodavan.application.plugins.wiring.install import _activation_mint

        config = PluginPlatformConfig.from_settings(settings)
        mint = _activation_mint(repositories, config)
        if mint is None:
            raise JobTaskConfigurationError("plugin_job_activation_mint_required")
        activator = PluginActivator(
            scale=runtime.scale,
            readiness=runtime.readiness,
            clock=SystemClock(),
            mint=mint,
            audit=repositories.audit,
            policy=IdlePolicy(
                idle_ttl_seconds=float(config.idle_ttl_seconds),
                ready_timeout_seconds=float(config.ready_timeout_seconds),
                max_concurrent_wakes=int(config.max_concurrent_wakes),
            ),
            state_store=runtime.state_store,
            proxy=runtime.proxy,
        )
        pod_executor = K8sPodExecutor(
            config=runtime.config,
            instance_store=repositories.instance_store,
            artifact_store=repositories.artifact_store,
            api=runtime.lifecycle_api,
            token_provider=runtime.token_provider,
            activator=activator,
        )
        payload_store = DigestPayloadStore(client=ManagerRedisClient(redis))
        executor = ScopedManifestOperationExecutor(
            resolver=pod_executor._resolver,
            activator=activator,
            proxy=runtime.proxy,
            payload_store=payload_store,
        )
        return PluginJobComposition(
            mode="standalone",
            declarations=declarations,
            records=records,
            repositories=repositories,
            session_factory=session_factory,
            redis=redis,
            payload_store=payload_store,
            executor=executor,
            runtime=runtime,
            pod_executor=pod_executor,
            owns_resources=True,
        )
    except Exception:
        async def cleanup() -> None:
            if pod_executor is not None:
                await pod_executor.aclose()
            if runtime is not None:
                await runtime.aclose()
            if payload_store is not None:
                await payload_store.aclose()
            await redis.shutdown()
            from prodavan.infrastructure.persistence.database import dispose_engine

            await dispose_engine()

        run_async(cleanup())
        raise


class DeferredStandalonePluginJobComposition:
    """Lazy CLI-only factory; importing the Celery app allocates no resources."""

    def __init__(self, settings: Any) -> None:
        import threading

        self._settings = settings
        self._composition: PluginJobComposition | None = None
        self._lock = threading.Lock()

    def __call__(self) -> ManifestJobTasks:
        with self._lock:
            if self._composition is None:
                self._composition = build_standalone_plugin_job_composition(self._settings)
            return self._composition()

    async def aclose(self) -> None:
        with self._lock:
            composition = self._composition
        if composition is not None:
            await composition.aclose()


__all__ = [
    "DeferredStandalonePluginJobComposition",
    "ManifestOperation",
    "PluginJobComposition",
    "PluginJobDeclaration",
    "PublishedOperation",
    "ScopedManifestOperationExecutor",
    "ScopedManifestOperationHandler",
    "build_api_plugin_job_composition",
    "build_job_task_specs",
    "build_standalone_plugin_job_composition",
    "install_api_plugin_platform",
    "parse_plugin_job_declarations",
    "published_operations",
]
