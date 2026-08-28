"""K8sManager — sandbox client lifecycle (P2 pod_service)."""

from __future__ import annotations

import logging

from prodavan.config.settings import settings
from prodavan.core.lifespan.resource import LifespanResource
from prodavan.infrastructure.k8s.auth import InClusterAuth
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

logger = logging.getLogger(__name__)

_manager: K8sManager | None = None


def get_k8s_manager() -> K8sManager | None:
    return _manager


def set_k8s_manager(manager: K8sManager | None) -> None:
    global _manager
    _manager = manager


class K8sManager(LifespanResource):
    """Optional in-cluster client for pod_service k8s mode."""

    def __init__(
        self,
        *,
        enabled: bool,
        namespace: str,
        required: bool = False,
        auth: InClusterAuth | None = None,
    ) -> None:
        self._enabled = enabled
        self._namespace = namespace
        self._required = required
        self._auth = auth or InClusterAuth()
        self._client: K8sSandboxClient | None = None

    @property
    def name(self) -> str:
        return "k8s"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client(self) -> K8sSandboxClient | None:
        return self._client

    async def startup(self) -> None:
        set_k8s_manager(self)
        if not self._enabled:
            logger.info("k8s: disabled (pod_runtime_mode=stub)")
            return
        self._client = K8sSandboxClient(namespace=self._namespace, auth=self._auth)
        if self._client.available():
            logger.info("k8s: in-cluster API available namespace=%s", self._namespace)
        elif self._required:
            raise RuntimeError("k8s required but in-cluster credentials unavailable")
        else:
            logger.warning("k8s: in-cluster credentials unavailable (dev kubeconfig not wired)")

    async def shutdown(self) -> None:
        set_k8s_manager(None)
        self._client = None

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        if self._client is None:
            return False if self._required else None
        return self._client.available()


def k8s_manager_from_settings() -> K8sManager:
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    return K8sManager(
        enabled=mode == "k8s",
        namespace=settings.pod_sandbox_namespace,
        required=settings.pod_k8s_required,
    )
