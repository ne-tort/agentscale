"""Cluster-internal plugin static evidence API (Scope B1).

A dedicated uvicorn process behind a cluster-internal Service in
``agentscale-dev``: NO Ingress, NO write API, no cookies, no tenant
MinIO/DB/RBAC. The only evidence surface is

    GET/HEAD /internal/plugin-static/v1/bundles/{plugin_id}/{version}/
            {bundle_digest}/ready

which digest-verifies the exact OCI bundle manifest, its canonical config
JSON and the frontend ``tar.gz`` layer - quarantine GHCR repository first,
release repository ONLY after a proven 404 there - and answers:

* HEAD 200 empty body + server-owned ``StaticResponsePolicy`` headers;
* GET 200 minimal sanitized JSON metadata (no blobs, no secrets);
* other methods 405; failed verification/identity/proven double-404 typed
* sanitized 404; every non-not-found registry failure typed sanitized 503
* without release fallback or retries; caps exceeded typed 413.

Composition mirrors the CI publication contour of ``wiring/install.py``:
``managed_config_from_settings`` + ``github_app_client_from_settings`` +
``ManagedOrganization``/``GitHubAppClient``, with the shared client closed
on lifespan shutdown. GitHub App remains the preferred registry auth path.
Only when that managed composition is absent can an explicitly configured
``PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH`` select the read-only mounted
``ghcr-pull`` dockerconfigjson fallback. Without that path, liveness stays up
while readiness and evidence answer typed 503 fail-closed. No anonymous auth
or credential-in-env fallback exists; ``PLUGIN_OCI_REGISTRY`` and
``PLUGIN_SHELL_ORIGIN`` are reused.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Final

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from prodavan.application.plugins.gateway.ports import StaticResponsePolicy
from prodavan.application.plugins.static_origin import (
    PluginStaticEvidenceService,
    StaticEvidenceConfig,
    StaticEvidenceError,
    StaticEvidenceErrorCode,
)
from prodavan.config.settings import settings
from prodavan.core.lifespan.manager import LifespanManager
from prodavan.core.middleware import register_trace_id
from prodavan.core.trace_context import install_trace_id_log_filter

logger = logging.getLogger(__name__)

STATIC_EVIDENCE_READY_PATH: str = (
    "/internal/plugin-static/v1/bundles"
    "/{plugin_id}/{version}/{bundle_digest}/ready"
)
STATIC_EVIDENCE_SERVICE_STATE: str = "plugin_static_evidence_service"
STATIC_EVIDENCE_POLICY_STATE: str = "plugin_static_response_policy"
STATIC_EVIDENCE_GITHUB_LIFESPAN_NAME: str = "plugin-static-github-app"
PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV: str = (
    "PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH"
)

_STATUS_BY_CODE: dict[StaticEvidenceErrorCode, int] = {
    StaticEvidenceErrorCode.BUNDLE_NOT_READY: 404,
    StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE: 503,
    StaticEvidenceErrorCode.BUNDLE_TOO_LARGE: 413,
}


class _StaticGitHubAppLifecycle:
    """Own the registry authorizer and optional GitHub App client."""

    __slots__ = ("_client", "_authorizer")

    def __init__(self, client: Any, authorizer: Any = None) -> None:
        self._client = client
        self._authorizer = authorizer

    @property
    def name(self) -> str:
        return STATIC_EVIDENCE_GITHUB_LIFESPAN_NAME

    async def startup(self) -> None:
        # HTTP and secret resolution stay lazy; startup performs no live calls.
        return None

    async def shutdown(self) -> None:
        if self._authorizer is not None:
            try:
                await self._authorizer.aclose()
            except Exception:  # noqa: BLE001 - shutdown must reach the client
                logger.warning("plugin static GHCR authorizer close failed")
        if self._client is not None:
            await self._client.aclose()

    async def health(self) -> None:
        return None


def _close_client_best_effort(client: Any) -> None:
    """Best-effort async cleanup when synchronous composition fails."""
    try:
        result = client.aclose()
        if not inspect.isawaitable(result):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(result)
        else:
            loop.create_task(result)
    except Exception:  # noqa: BLE001 - preserve the sanitized failure
        return


class _ProvenNotFound(RuntimeError):
    """Marker for a proven registry HTTP 404 (never an OSError).

    Deliberately not a ``FileNotFoundError``: the shared loader converts
    ``OSError`` into a generic transport failure, so the proven-404 marker
    must cross that guard untouched and is re-raised as the port-level
    absence signal by :class:`NotFoundAwareOciLoader`.
    """


class _Stream404Filter:
    """Async CM translating one proven 404 response into the marker."""

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __aenter__(self) -> Any:
        response = await self._inner.__aenter__()
        if response.status_code == 404:
            url = str(response.request.url)
            await self._inner.__aexit__(None, None, None)
            raise _ProvenNotFound(url)
        return response

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._inner.__aexit__(exc_type, exc, tb)


class _Proven404AsyncClient(httpx.AsyncClient):
    """Loader client with identical security settings plus proven-404 marking.

    trust_env stays disabled and redirects stay forbidden; only the response
    status is observed. No retry, no fallback, no other status is special.
    """

    def stream(self, method: str, url: Any, **kwargs: Any) -> Any:
        return _Stream404Filter(super().stream(method, url, **kwargs))


def _proven404_client_factory() -> httpx.AsyncClient:
    """Mirror the loader default client, plus proven-404 marking."""
    return _Proven404AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(10.0),
    )


class NotFoundAwareOciLoader:
    """Adapter: expose proven 404s as the typed FileNotFoundError absence.

    The wrapped loader keeps full ownership of digest verification, size
    caps and authorization. Only a proven registry 404 becomes the typed
    absence signal that the evidence service may fall back on; every other
    failure propagates unchanged and fails closed without retries.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        for method in ("load_manifest", "load_blob"):
            if not callable(getattr(inner, method, None)):
                raise ValueError(f"inner loader must implement {method}()")
        self._inner = inner

    async def load_manifest(self, ref: object) -> Any:
        try:
            return await self._inner.load_manifest(ref)
        except _ProvenNotFound as exc:
            raise FileNotFoundError(str(exc)) from None

    async def load_blob(
        self,
        ref: object,
        *,
        digest: str,
        expected_size: int | None = None,
    ) -> Any:
        try:
            return await self._inner.load_blob(
                ref, digest=digest, expected_size=expected_size
            )
        except _ProvenNotFound as exc:
            raise FileNotFoundError(str(exc)) from None


def _static_loader(config: StaticEvidenceConfig, authorizer: Any) -> NotFoundAwareOciLoader:
    from prodavan.application.plugins.artifacts.publication import (
        HttpxOciArtifactLoader,
    )

    # Frontend tar.gz blobs exceed the default 1 MiB loader blob cap, so the
    # frontend cap feeds max_blob_bytes; per-descriptor caps stay enforced.
    # Only a proven registry 404 becomes an absence signal. Auth failures,
    # including 401, propagate without anonymous retry or release fallback.
    return NotFoundAwareOciLoader(
        HttpxOciArtifactLoader(
            allowed_registries=("ghcr.io",),
            max_manifest_bytes=config.max_manifest_bytes,
            max_blob_bytes=config.max_frontend_layer_bytes,
            authorizer=authorizer,
            client_factory=_proven404_client_factory,
        ),
    )


def _compose_docker_config_fallback(
    config: StaticEvidenceConfig,
    policy: StaticResponsePolicy,
) -> tuple[
    PluginStaticEvidenceService,
    StaticResponsePolicy,
    _StaticGitHubAppLifecycle,
]:
    from prodavan.infrastructure.plugins.artifacts.docker_config_auth import (
        GHCR_DOCKER_CONFIG_ORGANIZATION,
        GhcrDockerConfigAuthorizer,
    )
    from prodavan.infrastructure.plugins.artifacts.ghcr_auth import (
        GhcrAuthConfigurationError,
    )

    config_path = os.environ.get(PLUGIN_STATIC_GHCR_DOCKER_CONFIG_PATH_ENV)
    if config_path is None or not config_path.strip():
        raise GhcrAuthConfigurationError(
            "GitHub App is unavailable and the GHCR docker config path is not set"
        )
    prefix_parts = config.release_repository_prefix.split("/")
    if len(prefix_parts) < 3 or prefix_parts[1] != GHCR_DOCKER_CONFIG_ORGANIZATION:
        raise GhcrAuthConfigurationError(
            "GHCR docker config fallback requires the ne-tort repository owner"
        )

    authorizer = GhcrDockerConfigAuthorizer(config_path=config_path)
    service = PluginStaticEvidenceService(
        artifact_loader=_static_loader(config, authorizer),
        config=config,
    )
    return service, policy, _StaticGitHubAppLifecycle(None, authorizer)


_GITHUB_APP_TEXT_SETTINGS: Final = (
    "plugin_module_source_github_app_organization",
    "plugin_module_source_github_app_template_owner",
    "plugin_module_source_github_app_template_name",
    "plugin_module_source_github_app_template_agents_revision",
    "plugin_module_source_github_app_template_security_revision",
    "plugin_module_source_installation_credential_ref",
    "plugin_module_source_trusted_workflow_ref",
    "plugin_module_source_required_status_checks",
    "plugin_module_source_default_branch",
    "plugin_module_source_github_app_private_key_secret_ref",
)
_GITHUB_APP_INT_SETTINGS: Final = (
    "plugin_module_source_github_app_id",
    "plugin_module_source_github_app_installation_id",
    "plugin_module_source_required_approving_review_count",
)


def _github_app_managed_config_absent(settings: Any) -> bool:
    """True only when the full GitHub App contour has an unset component."""
    for name in _GITHUB_APP_TEXT_SETTINGS:
        value = getattr(settings, name, None)
        if not isinstance(value, str) or not value.strip():
            return True
    for name in _GITHUB_APP_INT_SETTINGS:
        value = getattr(settings, name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return True
    return False


def _compose_static_evidence(
    app_settings: Any,
) -> tuple[PluginStaticEvidenceService, StaticResponsePolicy, _StaticGitHubAppLifecycle | None]:
    """Compose preferred GitHub App auth, then explicit dev-only file fallback."""
    from prodavan.application.plugins.wiring.module_sources import (
        github_app_client_from_settings,
        managed_config_from_settings,
    )
    from prodavan.infrastructure.plugins.artifacts.ghcr_auth import (
        GhcrRegistryAuthorizer,
    )

    config = StaticEvidenceConfig.from_settings(app_settings)
    policy = StaticResponsePolicy(shell_origin=app_settings.plugin_shell_origin)
    if _github_app_managed_config_absent(app_settings):
        return _compose_docker_config_fallback(config, policy)

    source_config = managed_config_from_settings(app_settings)
    organization = source_config.organization
    github_app = github_app_client_from_settings(app_settings, source_config)
    try:
        authorizer = GhcrRegistryAuthorizer(
            organization=organization.name,
            installation_id=organization.installation_id,
            token_provider=github_app,
        )
        loader = _static_loader(config, authorizer)
    except Exception:
        _close_client_best_effort(github_app)
        raise
    service = PluginStaticEvidenceService(artifact_loader=loader, config=config)
    return service, policy, _StaticGitHubAppLifecycle(github_app, authorizer)


def create_app(
    app_settings: Any | None = None,
    *,
    service: PluginStaticEvidenceService | None = None,
    artifact_loader: Any | None = None,
) -> FastAPI:
    """Build the evidence app.

    Dependency injection seams for offline tests:

    * ``service``: a fully composed service (fake loader inside);
    * ``artifact_loader``: only the loader is faked, config comes from
      ``app_settings``.
    """
    resolved_settings = app_settings if app_settings is not None else settings
    install_trace_id_log_filter()
    manager = LifespanManager()
    app = FastAPI(
        title="Agentscale Plugin Static Evidence",
        version=resolved_settings.app_version,
        lifespan=manager.as_fastapi_lifespan(),
    )
    register_trace_id(app)
    # No CORS: cluster-internal Service, never a browser surface.

    composed_service: PluginStaticEvidenceService | None = service
    composed_policy: StaticResponsePolicy | None = None
    lifecycle: _StaticGitHubAppLifecycle | None = None
    if composed_service is not None:
        try:
            composed_policy = StaticResponsePolicy(
                shell_origin=resolved_settings.plugin_shell_origin,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed composition
            logger.warning(
                "plugin static evidence policy composition failed (%s); "
                "readiness stays unconfigured",
                type(exc).__name__,
            )
            composed_service = None
    elif artifact_loader is not None:
        try:
            config = StaticEvidenceConfig.from_settings(resolved_settings)
            composed_policy = StaticResponsePolicy(
                shell_origin=resolved_settings.plugin_shell_origin,
            )
            composed_service = PluginStaticEvidenceService(
                artifact_loader=artifact_loader,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed composition
            logger.warning(
                "plugin static evidence loader composition failed (%s); "
                "readiness stays unconfigured",
                type(exc).__name__,
            )
            composed_service = None
    else:
        try:
            (
                composed_service,
                composed_policy,
                lifecycle,
            ) = _compose_static_evidence(resolved_settings)
        except Exception as exc:  # noqa: BLE001 - fail-closed composition
            logger.warning(
                "plugin static evidence composition failed (%s); the process"
                " starts unconfigured and answers typed 503",
                type(exc).__name__,
            )
            composed_service = None
            composed_policy = None
            lifecycle = None

    if lifecycle is not None:
        manager.register(lifecycle)
    setattr(app.state, STATIC_EVIDENCE_SERVICE_STATE, composed_service)
    setattr(app.state, STATIC_EVIDENCE_POLICY_STATE, composed_policy)

    def _error_response(exc: StaticEvidenceError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_BY_CODE[exc.code],
            content={"code": exc.code.value},
        )

    def _is_configured() -> bool:
        return (
            getattr(app.state, STATIC_EVIDENCE_SERVICE_STATE, None) is not None
            and getattr(app.state, STATIC_EVIDENCE_POLICY_STATE, None) is not None
        )

    @app.get("/health/live")
    async def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> Response:
        if not _is_configured():
            return JSONResponse(status_code=503, content={"status": "unconfigured"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.api_route(
        STATIC_EVIDENCE_READY_PATH,
        methods=["GET", "HEAD"],
        name="plugin_static_bundle_ready",
    )
    async def bundle_ready(
        plugin_id: str,
        version: str,
        bundle_digest: str,
        request: Request,
    ) -> Response:
        evidence_service: PluginStaticEvidenceService | None = getattr(
            request.app.state, STATIC_EVIDENCE_SERVICE_STATE, None
        )
        policy: StaticResponsePolicy | None = getattr(
            request.app.state, STATIC_EVIDENCE_POLICY_STATE, None
        )
        if evidence_service is None or policy is None:
            return JSONResponse(
                status_code=503,
                content={
                    "code": StaticEvidenceErrorCode.EVIDENCE_UNAVAILABLE.value
                },
            )
        try:
            evidence = await evidence_service.verify_bundle(
                plugin_id=plugin_id,
                version=version,
                bundle_digest=bundle_digest,
            )
        except StaticEvidenceError as exc:
            return _error_response(exc)
        if request.method == "HEAD":
            return Response(
                status_code=200,
                headers=policy.response_headers(
                    artifact_digest=evidence.bundle_digest,
                ),
            )
        # GET carries the same server-owned policy headers as HEAD: the
        # metadata document is still a validated static-evidence response.
        return JSONResponse(
            status_code=200,
            headers=policy.response_headers(
                artifact_digest=evidence.bundle_digest,
            ),
            content={
                "status": "ready",
                "plugin_id": evidence.plugin_id,
                "version": evidence.version,
                "bundle_digest": evidence.bundle_digest,
                "frontend_entry": evidence.frontend_entry,
                "source": evidence.source,
            },
        )

    return app


app = create_app()
