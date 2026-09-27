"""SandboxClientResource — agent-sandbox SDK lifecycle (Wave 1, PR-3).

Canonical migration module: docs/migration/04-backend.md (§4.1), PR-3 in
docs/migration/07-rollout.md.

The resource owns an ``AsyncSandboxClient`` (pip: k8s-agent-sandbox[async])
talking to the sandbox-router over ``SandboxDirectConnectionConfig``.
It is INERT unless ``settings.pod_runtime_mode == "sandbox"`` — the k8s
adapter (K8sManager) remains the active runtime until the Wave 1 flip.

Wave 2 (feat/sandbox-runtime-adapter) carries this module forward so the
runtime adapter has a client to resolve.
"""

from __future__ import annotations

import logging

from prodavan.config.settings import settings
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency guard (unit tests mock it)
    from k8s_agent_sandbox import AsyncSandboxClient
    from k8s_agent_sandbox.models import SandboxDirectConnectionConfig

    _SDK_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover
    AsyncSandboxClient = None  # type: ignore[assignment]
    SandboxDirectConnectionConfig = None  # type: ignore[assignment]
    _SDK_IMPORT_ERROR = exc

_manager: SandboxClientResource | None = None


def get_sandbox_client_manager() -> SandboxClientResource | None:
    return _manager


def set_sandbox_client_manager(manager: SandboxClientResource | None) -> None:
    global _manager
    _manager = manager


class SandboxClientResource(LifespanResource):
    """Optional agent-sandbox SDK client for pod_service sandbox mode."""

    def __init__(
        self,
        *,
        enabled: bool,
        router_url: str,
        namespace: str,
        warmpool: str,
        shutdown_ttl_sec: int,
    ) -> None:
        self._enabled = enabled
        self._router_url = router_url
        self._namespace = namespace
        self._warmpool = warmpool
        self._shutdown_ttl_sec = shutdown_ttl_sec
        self._client: AsyncSandboxClient | None = None

    @property
    def name(self) -> str:
        return "sandbox"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client(self) -> AsyncSandboxClient | None:
        return self._client

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def warmpool(self) -> str:
        return self._warmpool

    @property
    def shutdown_ttl_sec(self) -> int:
        return self._shutdown_ttl_sec

    @property
    def router_url(self) -> str:
        return self._router_url

    async def startup(self) -> None:
        set_sandbox_client_manager(self)
        if not self._enabled:
            logger.info("sandbox: disabled (pod_runtime_mode=%s)", settings.pod_runtime_mode)
            return
        if _SDK_IMPORT_ERROR is not None:
            raise RuntimeError(
                "pod_runtime_mode=sandbox requires k8s-agent-sandbox[async]"
            ) from _SDK_IMPORT_ERROR
        config = SandboxDirectConnectionConfig(api_url=self._router_url)
        self._client = AsyncSandboxClient(connection_config=config, cleanup=False)
        logger.info(
            "sandbox: SDK client ready router=%s namespace=%s warmpool=%s",
            self._router_url,
            self._namespace,
            self._warmpool,
        )

    async def shutdown(self) -> None:
        set_sandbox_client_manager(None)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.warning("sandbox: client close failed", exc_info=True)
        self._client = None

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        return self._client is not None


def sandbox_client_resource_from_settings() -> SandboxClientResource:
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    return SandboxClientResource(
        enabled=mode == "sandbox",
        router_url=settings.pod_sandbox_router_url,
        namespace=settings.pod_sandbox_namespace,
        warmpool=settings.pod_sandbox_warmpool,
        shutdown_ttl_sec=settings.pod_sandbox_shutdown_ttl_sec,
    )
