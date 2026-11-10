"""Disposable-namespace lifecycle for the real-K8s acceptance harness.

The harness creates exactly one namespace per test module
(prefix + unique run suffix + module tag), the four ServiceAccounts and the
two Roles/RoleBindings mirroring the production plugin-lifecycle RBAC, and
short-lived control-plane tokens used by the TokenRequest broker clients.

Cleanup is layered:
* teardown deletes only this run's own namespace after re-checking its
  acceptance labels/annotations (foreign namespaces are never adopted);
* a TTL annotation plus a start-of-run janitor deletes expired leftovers of the
  same prefix, and only when they carry this harness's managed-by labels;
* PLUGIN_ACCEPTANCE_KEEP_NAMESPACE=1 keeps a failed run for debugging, still
  bounded by the TTL janitor on a later run.

Nothing here touches sandboxes, dev, prod, or any pre-existing namespace.
No kubectl, no SDK, no subprocess: only the production K8sApiClient REST
transport. Log lines contain names and digests, never tokens/secret values.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator
from urllib.parse import urlencode

from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    APPS_API,
    CORE_API,
    K8sApiClient,
    K8sApiError,
    K8sApiNotFound,
)
from prodavan.infrastructure.plugins.runtime.token_provider import (
    TOKEN_AUDIENCE,
    ServiceAccountTokenProvider,
)

from .acceptance_config import AcceptanceConfig
from .kubeconfig import (
    KubeconfigAuth,
    KubeconfigIdentity,
    NoMountedTokenAuth,
    StaticTokenAuth,
    load_identity,
)

logger = logging.getLogger(__name__)

RBAC_API = "/apis/rbac.authorization.k8s.io/v1"

ADMIN_SA = "plugin-deployment-admin"
API_SA = "agentscale-api"
GATEWAY_SA = "plugin-gateway"
UNRELATED_SA = "acceptance-unrelated"
CONTROL_PLANE_SAS = (API_SA, GATEWAY_SA)
ALL_SAS = (ADMIN_SA, API_SA, GATEWAY_SA, UNRELATED_SA)

ACCEPTANCE_LABEL = "agentscale.ne-tort/acceptance"
ACCEPTANCE_LABEL_VALUE = "plugin-k8s"
PREFIX_LABEL = "agentscale.ne-tort/acceptance-prefix"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "agentscale-plugin-acceptance"
EXPIRES_ANNOTATION = "agentscale.ne-tort/acceptance-expires-at"
RUN_ID_ANNOTATION = "agentscale.ne-tort/acceptance-run-id"

#: Control-plane tokens minted once per module for broker/mounted-token use.
#: Same 3600s ceiling as the lifecycle TokenRequest contract; the broker only
#: ever holds these in memory and never persists or logs them.
BOOTSTRAP_TOKEN_TTL_SECONDS = 3600


class AcceptanceClusterError(RuntimeError):
    """Disposable-cluster setup/teardown failure (sanitized by construction)."""


@dataclass(slots=True)
class LifecycleTransport:
    """One scoped lifecycle identity: provider + data-path client + broker."""

    provider: ServiceAccountTokenProvider = field(repr=False)
    api: K8sApiClient = field(repr=False)
    broker: K8sApiClient = field(repr=False)
    no_mounted_auth: NoMountedTokenAuth = field(repr=False)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def namespace_manifest(config: AcceptanceConfig, namespace: str) -> dict[str, Any]:
    expires = datetime.now(UTC) + timedelta(seconds=config.ttl_seconds)
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                ACCEPTANCE_LABEL: ACCEPTANCE_LABEL_VALUE,
                PREFIX_LABEL: config.namespace_prefix,
                MANAGED_BY_LABEL: MANAGED_BY_VALUE,
            },
            "annotations": {
                EXPIRES_ANNOTATION: _rfc3339(expires),
                RUN_ID_ANNOTATION: config.run_id,
            },
        },
    }


def _acceptance_object_labels(config: AcceptanceConfig) -> dict[str, str]:
    return {
        ACCEPTANCE_LABEL: ACCEPTANCE_LABEL_VALUE,
        PREFIX_LABEL: config.namespace_prefix,
        MANAGED_BY_LABEL: MANAGED_BY_VALUE,
    }


def _service_account(config: AcceptanceConfig, namespace: str, name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": name, "namespace": namespace, "labels": _acceptance_object_labels(config)},
        # The powerful lifecycle identity is never automounted into a pod.
        "automountServiceAccountToken": False,
    }


def _lifecycle_role(config: AcceptanceConfig, namespace: str) -> dict[str, Any]:
    # Mirrors infra/k3s/overlays/dev/plugins/plugin-lifecycle-rbac.yaml, scoped
    # to the disposable namespace instead of a shared one.
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": ADMIN_SA,
            "namespace": namespace,
            "labels": _acceptance_object_labels(config),
        },
        "rules": [
            {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "create", "patch", "delete"]},
            {"apiGroups": ["apps"], "resources": ["deployments/scale"], "verbs": ["get", "patch"]},
            {"apiGroups": [""], "resources": ["services"], "verbs": ["get", "create", "patch", "delete"]},
            {"apiGroups": [""], "resources": ["services/proxy"], "verbs": ["get", "create"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "create", "patch", "delete"]},
            {
                "apiGroups": [""],
                "resources": ["configmaps"],
                "resourceNames": ["plugin-activation-state"],
                "verbs": ["get", "patch"],
            },
            {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["create"]},
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
        ],
    }


def _token_request_role(config: AcceptanceConfig, namespace: str) -> dict[str, Any]:
    # Mirrors plugin-deployment-admin-token-request-rbac.yaml: API/Gateway may
    # mint a token for the lifecycle admin SA and for NOTHING else.
    # The serviceaccounts/token subresource belongs to the core API group.
    # Its resourceNames=[plugin-deployment-admin] fence is enforced only after
    # the broker presents the actual ServiceAccount Bearer identity; the
    # earlier unexpected 201s were caused by the bootstrap client certificate
    # shadowing the token, not by this rule.
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": f"{ADMIN_SA}-token-request",
            "namespace": namespace,
            "labels": _acceptance_object_labels(config),
        },
        "rules": [
            {
                "apiGroups": [""],
                "resources": ["serviceaccounts/token"],
                "resourceNames": [ADMIN_SA],
                "verbs": ["create"],
            }
        ],
    }


def _role_binding(
    config: AcceptanceConfig,
    namespace: str,
    name: str,
    role_name: str,
    subjects: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": name, "namespace": namespace, "labels": _acceptance_object_labels(config)},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role_name},
        "subjects": [
            {"kind": "ServiceAccount", "name": subject, "namespace": namespace} for subject in subjects
        ],
    }


def rbac_manifests(config: AcceptanceConfig, namespace: str) -> list[dict[str, Any]]:
    return [
        *[_service_account(config, namespace, name) for name in ALL_SAS],
        _lifecycle_role(config, namespace),
        _role_binding(config, namespace, ADMIN_SA, ADMIN_SA, (ADMIN_SA,)),
        _token_request_role(config, namespace),
        _role_binding(
            config,
            namespace,
            f"{ADMIN_SA}-token-request",
            f"{ADMIN_SA}-token-request",
            CONTROL_PLANE_SAS,
        ),
    ]


def object_path(namespace: str, manifest: dict[str, Any]) -> str:
    kind = manifest["kind"]
    if kind == "ServiceAccount":
        return f"{CORE_API}/namespaces/{namespace}/serviceaccounts"
    if kind == "Role":
        return f"{RBAC_API}/namespaces/{namespace}/roles"
    if kind == "RoleBinding":
        return f"{RBAC_API}/namespaces/{namespace}/rolebindings"
    raise AcceptanceClusterError(f"unsupported acceptance manifest kind: {kind}")


async def _mint_token(api: K8sApiClient, namespace: str, service_account: str) -> str:
    response = await api.request(
        "POST",
        f"{CORE_API}/namespaces/{namespace}/serviceaccounts/{service_account}/token",
        body={
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenRequest",
            "spec": {
                "audiences": [TOKEN_AUDIENCE],
                "expirationSeconds": BOOTSTRAP_TOKEN_TTL_SECONDS,
            },
        },
        content_type="application/json",
    )
    status = response.body.get("status")
    token = status.get("token") if isinstance(status, dict) else None
    if not isinstance(token, str) or not token:
        raise AcceptanceClusterError(f"TokenRequest for {service_account} returned no token")
    return token


@dataclass(slots=True)
class AcceptanceCluster:
    """Live disposable namespace plus loop-independent identity facts."""

    config: AcceptanceConfig
    namespace: str
    identity: KubeconfigIdentity = field(repr=False)
    tokens: dict[str, str] = field(repr=False)

    @classmethod
    async def create(cls, config: AcceptanceConfig, module_tag: str) -> AcceptanceCluster:
        namespace = config.namespace(module_tag)
        identity = load_identity(config)
        api = K8sApiClient(
            auth=KubeconfigAuth(identity), timeout_seconds=config.api_timeout_seconds
        )
        tokens: dict[str, str] = {}
        created_by_me = False
        try:
            try:
                await api.request("POST", f"{CORE_API}/namespaces", body=namespace_manifest(config, namespace))
                created_by_me = True
            except K8sApiError as exc:
                raise AcceptanceClusterError(
                    f"cannot create disposable namespace {namespace!r}: {exc.reason}; "
                    "a unique suffix collision is never adopted - choose another run id"
                ) from None
            for manifest in rbac_manifests(config, namespace):
                await api.request("POST", object_path(namespace, manifest), body=manifest)
            for name in (*CONTROL_PLANE_SAS, UNRELATED_SA):
                tokens[name] = await _mint_token(api, namespace, name)
        except Exception:
            await api.aclose()
            # A 409 or any pre-create failure is never ours to roll back. After
            # a successful create, re-read markers before every delete attempt.
            if created_by_me:
                try:
                    rollback = K8sApiClient(
                        auth=KubeconfigAuth(identity), timeout_seconds=config.api_timeout_seconds
                    )
                    try:
                        candidate = cls(
                            config=config,
                            namespace=namespace,
                            identity=identity,
                            tokens=tokens,
                        )
                        if await candidate._own_namespace(rollback) is not None:
                            await rollback.request("DELETE", f"{CORE_API}/namespaces/{namespace}")
                    finally:
                        await rollback.aclose()
                except Exception:
                    logger.warning("acceptance rollback of %s failed; TTL janitor will collect it", namespace)
            identity.cleanup()
            raise
        await api.aclose()
        logger.info("acceptance namespace %s ready (TTL %ss)", namespace, config.ttl_seconds)
        return cls(config=config, namespace=namespace, identity=identity, tokens=tokens)

    async def _own_namespace(self, api: K8sApiClient) -> dict[str, Any] | None:
        try:
            response = await api.request("GET", f"{CORE_API}/namespaces/{self.namespace}")
        except K8sApiNotFound:
            return None
        body = response.body
        metadata = body.get("metadata") if isinstance(body, dict) else None
        labels = metadata.get("labels") if isinstance(metadata, dict) else None
        annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
        labels = labels if isinstance(labels, dict) else {}
        annotations = annotations if isinstance(annotations, dict) else {}
        if (
            labels.get(MANAGED_BY_LABEL) != MANAGED_BY_VALUE
            or labels.get(ACCEPTANCE_LABEL) != ACCEPTANCE_LABEL_VALUE
            or labels.get(PREFIX_LABEL) != self.config.namespace_prefix
            or annotations.get(RUN_ID_ANNOTATION) != self.config.run_id
        ):
            raise AcceptanceClusterError(
                f"namespace {self.namespace!r} no longer carries this run's ownership markers; "
                "refusing to delete it"
            )
        return body

    async def destroy(self) -> None:
        """Delete only this run's own namespace; tolerate an already-gone one."""
        if self.config.keep_namespace:
            logger.info(
                "acceptance namespace %s kept by request; TTL annotation and janitor still apply",
                self.namespace,
            )
            self.identity.cleanup()
            return
        api = K8sApiClient(
            auth=KubeconfigAuth(self.identity), timeout_seconds=self.config.api_timeout_seconds
        )
        try:
            if await self._own_namespace(api) is None:
                return
            await api.request("DELETE", f"{CORE_API}/namespaces/{self.namespace}")
            deadline = asyncio.get_running_loop().time() + 180.0
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await api.request("GET", f"{CORE_API}/namespaces/{self.namespace}")
                except K8sApiNotFound:
                    logger.info("acceptance namespace %s deleted", self.namespace)
                    return
                await asyncio.sleep(2.0)
            logger.warning("acceptance namespace %s is still terminating; TTL janitor remains", self.namespace)
        finally:
            await api.aclose()
            self.identity.cleanup()

    @asynccontextmanager
    async def bootstrap(self) -> AsyncIterator[K8sApiClient]:
        """Fresh bootstrap client for assertions/drift injection in this loop."""
        api = K8sApiClient(
            auth=KubeconfigAuth(self.identity), timeout_seconds=self.config.api_timeout_seconds
        )
        try:
            yield api
        finally:
            await api.aclose()

    @asynccontextmanager
    async def broker(self, service_account: str) -> AsyncIterator[K8sApiClient]:
        """Control-plane client whose mounted token is used ONLY for TokenRequest."""
        token = self.tokens.get(service_account)
        if token is None:
            raise AcceptanceClusterError(f"no control-plane token was minted for {service_account}")
        api = K8sApiClient(
            auth=StaticTokenAuth(self.identity, token),
            timeout_seconds=self.config.api_timeout_seconds,
        )
        try:
            yield api
        finally:
            await api.aclose()

    @asynccontextmanager
    async def lifecycle(
        self,
        service_account: str,
        *,
        expiration_seconds: int | None = None,
        refresh_before_seconds: float | None = None,
        clock: Any = None,
    ) -> AsyncIterator[LifecycleTransport]:
        """Production TokenProvider + K8sApiClient with a scoped identity."""
        async with self.broker(service_account) as broker:
            provider = ServiceAccountTokenProvider(
                namespace=self.namespace,
                api=broker,
                expiration_seconds=(
                    self.config.token_ttl_seconds if expiration_seconds is None else expiration_seconds
                ),
                refresh_before_seconds=refresh_before_seconds,
                clock=clock,
            )
            no_mounted = NoMountedTokenAuth(self.identity)
            api = K8sApiClient(
                auth=no_mounted,
                token_provider=provider,
                timeout_seconds=self.config.api_timeout_seconds,
            )
            try:
                yield LifecycleTransport(provider=provider, api=api, broker=broker, no_mounted_auth=no_mounted)
            finally:
                await api.aclose()
                # Also closes the broker transport; aclose() is idempotent.
                await provider.aclose()

    def path(self, plural: str, name: str | None = None, *, group: str = "core") -> str:
        base = APPS_API if group == "apps" else CORE_API
        path = f"{base}/namespaces/{self.namespace}/{plural}"
        return f"{path}/{name}" if name else path

    async def read(self, plural: str, name: str, *, group: str = "core") -> dict[str, Any] | None:
        async with self.bootstrap() as api:
            try:
                response = await api.request("GET", self.path(plural, name, group=group))
            except K8sApiNotFound:
                return None
            return response.body


async def cleanup_expired_namespaces(config: AcceptanceConfig) -> list[str]:
    """TTL janitor: delete only expired, harness-owned namespaces of this prefix."""
    identity = load_identity(config)
    api = K8sApiClient(auth=KubeconfigAuth(identity), timeout_seconds=config.api_timeout_seconds)
    deleted: list[str] = []
    try:
        selector = urlencode(
            {
                "labelSelector": (
                    f"{ACCEPTANCE_LABEL}={ACCEPTANCE_LABEL_VALUE},{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
                )
            }
        )
        response = await api.request("GET", f"{CORE_API}/namespaces?{selector}")
        items = response.body.get("items")
        if not isinstance(items, list):
            raise AcceptanceClusterError("namespace listing for the TTL janitor is malformed")
        now = datetime.now(UTC)
        for item in items:
            metadata = item.get("metadata") if isinstance(item, dict) else None
            if not isinstance(metadata, dict):
                continue
            name = metadata.get("name")
            labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
            annotations = metadata.get("annotations") if isinstance(metadata.get("annotations"), dict) else {}
            if not isinstance(name, str) or not name.startswith(f"{config.namespace_prefix}-"):
                continue
            if labels.get(PREFIX_LABEL) != config.namespace_prefix:
                continue
            raw_expiry = annotations.get(EXPIRES_ANNOTATION)
            if not isinstance(raw_expiry, str):
                continue
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None or expiry.astimezone(UTC) > now:
                continue
            await api.request("DELETE", f"{CORE_API}/namespaces/{name}")
            deleted.append(name)
            logger.info("TTL janitor deleted expired acceptance namespace %s", name)
        return deleted
    finally:
        await api.aclose()
        identity.cleanup()


__all__ = [
    "ADMIN_SA",
    "API_SA",
    "GATEWAY_SA",
    "UNRELATED_SA",
    "CONTROL_PLANE_SAS",
    "AcceptanceCluster",
    "AcceptanceClusterError",
    "LifecycleTransport",
    "cleanup_expired_namespaces",
    "namespace_manifest",
    "rbac_manifests",
]
