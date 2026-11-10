"""Explicit, fail-closed environment contract for the real-K8s acceptance harness.

There is NO default kubeconfig, NO default dev/prod context and NO shared
namespace fallback. Every namespace the harness creates is derived from the
required prefix plus a unique run suffix and carries a TTL annotation used by
the start-of-run janitor.

Only the variables below are read. Nothing here contains a secret value: the
kubeconfig is referenced by path and is never copied into the repository.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_KUBECONFIG = "PLUGIN_ACCEPTANCE_KUBECONFIG"
ENV_CONTEXT = "PLUGIN_ACCEPTANCE_KUBE_CONTEXT"
ENV_NAMESPACE_PREFIX = "PLUGIN_ACCEPTANCE_NAMESPACE_PREFIX"
ENV_RUN_ID = "PLUGIN_ACCEPTANCE_RUN_ID"
ENV_TTL_SECONDS = "PLUGIN_ACCEPTANCE_TTL_SECONDS"
ENV_KEEP_NAMESPACE = "PLUGIN_ACCEPTANCE_KEEP_NAMESPACE"
ENV_TTL_JANITOR = "PLUGIN_ACCEPTANCE_TTL_JANITOR"
ENV_GATEWAY_URL = "PLUGIN_ACCEPTANCE_GATEWAY_URL"
ENV_TOKEN_TTL_SECONDS = "PLUGIN_ACCEPTANCE_TOKEN_TTL_SECONDS"
ENV_API_TIMEOUT_SECONDS = "PLUGIN_ACCEPTANCE_API_TIMEOUT_SECONDS"

ENV_NAMES = (
    ENV_KUBECONFIG,
    ENV_CONTEXT,
    ENV_NAMESPACE_PREFIX,
    ENV_RUN_ID,
    ENV_TTL_SECONDS,
    ENV_KEEP_NAMESPACE,
    ENV_TTL_JANITOR,
    ENV_GATEWAY_URL,
    ENV_TOKEN_TTL_SECONDS,
    ENV_API_TIMEOUT_SECONDS,
)

DEFAULT_TTL_SECONDS = 1800
DEFAULT_TOKEN_TTL_SECONDS = 3600
DEFAULT_API_TIMEOUT_SECONDS = 15.0

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_RUN_ID = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_MODULE_TAG = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")

#: Namespaces that must never be created, reused, or deleted by this harness.
FORBIDDEN_NAMESPACES = frozenset(
    {
        "default",
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "agentscale",
        "agentscale-dev",
        "agentscale-prod",
        "agentscale-sandboxes",
        "agentscale-dev-sandboxes",
        "agentscale-prod-sandboxes",
    }
)
#: Words marking a default/shared/product contour. They are denied as exact
#: matches AND as whole tokens (separators . - _ / :), while the distinctive
#: substrings prod/shared/agentscale are denied anywhere in the identifier.
#: "dev"/"default"/"live" intentionally use exact/token matching only, so a
#: legitimate random acceptance name (kind-*, k3d-*, random run ids) is never
#: rejected just because it happens to contain a letter sequence.
_CONTOUR_WORDS = frozenset({"default", "dev", "prod", "production", "shared", "live", "agentscale"})
_CONTOUR_SUBSTRINGS = ("prod", "shared", "agentscale")
_CONTOUR_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class AcceptanceConfigError(RuntimeError):
    """Unsafe or incomplete acceptance environment; never contains secrets."""


def looks_like_shared_contour(value: str) -> bool:
    """Fail-closed contour check for contexts/clusters/hosts/prefixes.

    Denied examples: default, dev, prod.example, dev.example, shared.example,
    kubernetes.default.svc, kind-dev, api.live.example, my-prod-cluster,
    agentscale-anything. Allowed examples: kind-kind, k3d-acceptance,
    acceptance-x7q2, plugin-acc, 127.0.0.1, kind.local.
    """
    lowered = value.strip().lower()
    if not lowered:
        return False
    if lowered in FORBIDDEN_NAMESPACES or lowered in _CONTOUR_WORDS:
        return True
    if any(part in lowered for part in _CONTOUR_SUBSTRINGS):
        return True
    return any(token in _CONTOUR_WORDS for token in _CONTOUR_TOKEN_SPLIT.split(lowered) if token)


def acceptance_requested(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return bool(str(source.get(ENV_KUBECONFIG, "") or "").strip())


def _bool(source: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = str(source.get(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise AcceptanceConfigError(f"{name} must be a boolean (1/0/true/false)")


def _int(source: Mapping[str, str], name: str, *, default: int, low: int, high: int) -> int:
    raw = str(source.get(name, "") or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise AcceptanceConfigError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise AcceptanceConfigError(f"{name} must be between {low} and {high}")
    return value


def _float(source: Mapping[str, str], name: str, *, default: float) -> float:
    raw = str(source.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise AcceptanceConfigError(f"{name} must be a number") from None
    if not 1.0 <= value <= 120.0 or value != value:
        raise AcceptanceConfigError(f"{name} must be between 1 and 120 seconds")
    return value


def _validate_prefix(prefix: str) -> str:
    if not prefix or len(prefix) > 24 or not _DNS_LABEL.fullmatch(prefix):
        raise AcceptanceConfigError(
            f"{ENV_NAMESPACE_PREFIX} must be a DNS-1123 label of 1..24 chars "
            "(for example 'plugin-acc')"
        )
    lowered = prefix.lower()
    if looks_like_shared_contour(lowered) or "sandbox" in lowered:
        raise AcceptanceConfigError(
            f"{ENV_NAMESPACE_PREFIX}={prefix!r} looks like a shared/product contour; "
            "use a dedicated disposable prefix such as 'plugin-acc'"
        )
    return lowered


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    """Validated immutable acceptance settings (secret-free by construction)."""

    kubeconfig_path: Path
    context: str | None
    namespace_prefix: str
    run_id: str
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    keep_namespace: bool = False
    ttl_janitor: bool = True
    gateway_url: str | None = None
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS
    api_timeout_seconds: float = DEFAULT_API_TIMEOUT_SECONDS

    def namespace(self, module_tag: str) -> str:
        """Unique per-module disposable namespace; never a shared name."""
        tag = module_tag.strip().lower()
        if not _MODULE_TAG.fullmatch(tag):
            raise AcceptanceConfigError("module tag must be a short DNS-1123 label")
        name = f"{self.namespace_prefix}-{self.run_id}-{tag}"
        if len(name) > 63 or not _DNS_LABEL.fullmatch(name):
            raise AcceptanceConfigError("derived acceptance namespace is not a valid DNS-1123 label")
        if name in FORBIDDEN_NAMESPACES:
            raise AcceptanceConfigError("derived acceptance namespace is a reserved name")
        return name

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AcceptanceConfig:
        source = os.environ if env is None else env
        raw_path = str(source.get(ENV_KUBECONFIG, "") or "").strip()
        if not raw_path:
            raise AcceptanceConfigError(
                f"{ENV_KUBECONFIG} is required; there is no default/in-cluster/dev/prod fallback"
            )
        path = Path(raw_path).expanduser()
        default_kubeconfig = (Path.home() / ".kube" / "config").resolve()
        try:
            resolved = path.resolve()
        except OSError:
            raise AcceptanceConfigError(f"{ENV_KUBECONFIG} cannot be resolved") from None
        if resolved == default_kubeconfig:
            raise AcceptanceConfigError(
                "the default ~/.kube/config is forbidden; export a dedicated disposable "
                "non-prod kubeconfig to a separate file outside the repository"
            )
        if not resolved.is_file():
            raise AcceptanceConfigError(f"{ENV_KUBECONFIG} does not point at an existing file")
        raw_prefix = str(source.get(ENV_NAMESPACE_PREFIX, "") or "").strip()
        if not raw_prefix:
            raise AcceptanceConfigError(
                f"{ENV_NAMESPACE_PREFIX} is required (explicit disposable namespace prefix)"
            )
        run_id = str(source.get(ENV_RUN_ID, "") or "").strip().lower() or uuid.uuid4().hex[:12]
        if len(run_id) > 20 or not _RUN_ID.fullmatch(run_id):
            raise AcceptanceConfigError(f"{ENV_RUN_ID} must be a DNS-1123 fragment of <=20 chars")
        context = str(source.get(ENV_CONTEXT, "") or "").strip() or None
        gateway = str(source.get(ENV_GATEWAY_URL, "") or "").strip() or None
        return cls(
            kubeconfig_path=resolved,
            context=context,
            namespace_prefix=_validate_prefix(raw_prefix),
            run_id=run_id,
            ttl_seconds=_int(source, ENV_TTL_SECONDS, default=DEFAULT_TTL_SECONDS, low=60, high=86400),
            keep_namespace=_bool(source, ENV_KEEP_NAMESPACE, default=False),
            ttl_janitor=_bool(source, ENV_TTL_JANITOR, default=True),
            gateway_url=gateway,
            token_ttl_seconds=_int(
                source,
                ENV_TOKEN_TTL_SECONDS,
                default=DEFAULT_TOKEN_TTL_SECONDS,
                low=60,
                high=3600,
            ),
            api_timeout_seconds=_float(
                source, ENV_API_TIMEOUT_SECONDS, default=DEFAULT_API_TIMEOUT_SECONDS
            ),
        )


__all__ = [
    "AcceptanceConfig",
    "AcceptanceConfigError",
    "ENV_NAMES",
    "ENV_API_TIMEOUT_SECONDS",
    "ENV_CONTEXT",
    "ENV_GATEWAY_URL",
    "ENV_KEEP_NAMESPACE",
    "ENV_KUBECONFIG",
    "ENV_NAMESPACE_PREFIX",
    "ENV_RUN_ID",
    "ENV_TOKEN_TTL_SECONDS",
    "ENV_TTL_JANITOR",
    "ENV_TTL_SECONDS",
    "FORBIDDEN_NAMESPACES",
    "acceptance_requested",
    "looks_like_shared_contour",
]
