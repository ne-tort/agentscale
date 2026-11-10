"""Explicit disposable-kubeconfig loading for the acceptance harness.

Only the file named by PLUGIN_ACCEPTANCE_KUBECONFIG is read. The default
~/.kube/config, in-cluster ServiceAccount files, KUBECONFIG and any dev/prod
context are never consulted. Known shared/product contexts, namespaces and the
shared dev/prod API host are refused before any network call happens.

Auth adapters below plug into the production K8sApiClient without changing it:
* KubeconfigAuth  - bootstrap identity (namespace/RBAC setup, reads, cleanup);
* StaticTokenAuth - a control-plane "mounted" token, used ONLY by the
  TokenRequest broker client, exactly like the production API/Gateway pods;
* NoMountedTokenAuth - lifecycle data path proof: headers()/available() raise
  if anything ever tries to read a mounted API token in provider mode.

Temporary client-certificate files (when the kubeconfig embeds them) live in a
private 0700 directory outside the repository and are removed on cleanup().
No token or key material is ever logged or included in exception messages.
"""

from __future__ import annotations

import base64
import binascii
import os
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .acceptance_config import (
    FORBIDDEN_NAMESPACES,
    AcceptanceConfig,
    looks_like_shared_contour,
)

#: The shared dev/prod VM documented in AGENTS.md must never be an acceptance target.
_FORBIDDEN_SERVER_HOSTS = frozenset({"172.31.156.203"})


class AcceptanceKubeconfigError(RuntimeError):
    """Unsafe/unusable kubeconfig; messages never contain credential material."""


def _entries(document: Any, key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(document, dict):
        raise AcceptanceKubeconfigError("kubeconfig is not a mapping")
    for entry in document.get(key) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        payload = entry.get(key[:-1])
        if isinstance(name, str) and isinstance(payload, dict):
            result[name] = payload
    return result


def _guard_name(kind: str, name: str) -> None:
    # Exact + whole-token default/dev/prod/shared/live denial (plus the
    # prod/shared/agentscale substrings) from the shared acceptance rule.
    if looks_like_shared_contour(name):
        raise AcceptanceKubeconfigError(
            f"kubeconfig {kind} {name!r} looks like a default/dev/prod/shared/live contour; "
            "use a dedicated disposable non-prod cluster"
        )


def _guard_server(server: str) -> str:
    if not isinstance(server, str) or not server.startswith("https://"):
        raise AcceptanceKubeconfigError("kubeconfig cluster server must be an https:// URL")
    parsed = urlsplit(server)
    host = (parsed.hostname or "").lower()
    if not host:
        raise AcceptanceKubeconfigError("kubeconfig cluster server has no host")
    if host in _FORBIDDEN_SERVER_HOSTS or looks_like_shared_contour(host):
        raise AcceptanceKubeconfigError(
            "kubeconfig points at a default/dev/prod/shared/live API host; "
            "acceptance requires a disposable non-prod cluster"
        )
    if parsed.username or parsed.password:
        raise AcceptanceKubeconfigError("kubeconfig server URL must not embed credentials")
    return server.rstrip("/")


def _b64(value: str, *, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise AcceptanceKubeconfigError(f"kubeconfig {what} is not valid base64") from None


@dataclass(slots=True)
class KubeconfigIdentity:
    """Loop-independent connection facts; no httpx client is kept here."""

    api_base: str
    ssl_context: ssl.SSLContext = field(repr=False)
    token_ssl_context: ssl.SSLContext | None = field(default=None, repr=False)
    token: str | None = field(default=None, repr=False)
    _tempdir: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False)

    def cleanup(self) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None


class KubeconfigAuth:
    """Bootstrap identity in the InClusterAuth shape expected by K8sApiClient."""

    def __init__(self, identity: KubeconfigIdentity) -> None:
        self._identity = identity

    def available(self) -> bool:
        return True

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._identity.token:
            headers["Authorization"] = f"Bearer {self._identity.token}"
        return headers

    def client_kwargs(self) -> dict[str, Any]:
        return {
            "verify": self._identity.ssl_context,
            "timeout": 30.0,
            "trust_env": False,
        }

    def api_base(self) -> str:
        return self._identity.api_base


class StaticTokenAuth:
    """A mounted control-plane token; used only by the TokenRequest broker."""

    def __init__(self, identity: KubeconfigIdentity, token: str) -> None:
        self._identity = identity
        self._token = token

    def available(self) -> bool:
        return True

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def client_kwargs(self) -> dict[str, Any]:
        # Never send the bootstrap client certificate alongside the Bearer
        # token: it would override the ServiceAccount identity on the API server.
        return {
            "verify": self._identity.token_ssl_context or self._identity.ssl_context,
            "timeout": 30.0,
            "trust_env": False,
        }

    def api_base(self) -> str:
        return self._identity.api_base


class NoMountedTokenAuth:
    """Fail the test if the lifecycle data path ever reads a mounted token."""

    def __init__(self, identity: KubeconfigIdentity) -> None:
        self._identity = identity
        self.mounted_token_reads = 0

    def available(self) -> bool:
        self.mounted_token_reads += 1
        raise AssertionError("lifecycle data path must not check/read the mounted API token")

    def headers(self) -> dict[str, str]:
        self.mounted_token_reads += 1
        raise AssertionError("lifecycle data path must not use the mounted API token")

    def client_kwargs(self) -> dict[str, Any]:
        # Token-provider data path: bootstrap client certificates are forbidden.
        return {
            "verify": self._identity.token_ssl_context or self._identity.ssl_context,
            "timeout": 30.0,
            "trust_env": False,
        }

    def api_base(self) -> str:
        return self._identity.api_base


def _tls_context(ca_file: str | None, *, insecure: bool) -> ssl.SSLContext:
    """Build one TLS context for either the bootstrap or bearer-token identity.

    Token contexts are always built separately from the bootstrap context so a
    bootstrap client certificate can never be sent with a Bearer token. The
    disposable kubeconfig may carry an administrator certificate: presenting it
    together with a ServiceAccount token authenticates the certificate subject
    instead of the intended identity and would silently bypass RBAC assertions.
    """
    context = (
        ssl.create_default_context(cafile=ca_file)
        if ca_file
        else ssl.create_default_context()
    )
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def load_identity(config: AcceptanceConfig) -> KubeconfigIdentity:
    """Parse and guard the explicit disposable kubeconfig (no network I/O)."""
    path = config.kubeconfig_path
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise AcceptanceKubeconfigError("kubeconfig is not valid YAML") from None
    except OSError as exc:
        raise AcceptanceKubeconfigError(f"kubeconfig cannot be read: {type(exc).__name__}") from None

    contexts = _entries(document, "contexts")
    clusters = _entries(document, "clusters")
    users = _entries(document, "users")
    context_name = config.context or document.get("current-context")
    if not isinstance(context_name, str) or not context_name:
        raise AcceptanceKubeconfigError("kubeconfig has no current-context; set PLUGIN_ACCEPTANCE_KUBE_CONTEXT")
    context = contexts.get(context_name)
    if context is None:
        raise AcceptanceKubeconfigError(f"kubeconfig context {context_name!r} is absent")
    _guard_name("context", context_name)
    context_namespace = context.get("namespace")
    if isinstance(context_namespace, str) and context_namespace in FORBIDDEN_NAMESPACES:
        raise AcceptanceKubeconfigError(
            f"kubeconfig context namespace {context_namespace!r} is a reserved shared namespace"
        )

    cluster_name = context.get("cluster")
    cluster = clusters.get(cluster_name) if isinstance(cluster_name, str) else None
    if cluster is None:
        raise AcceptanceKubeconfigError("kubeconfig context references an unknown cluster")
    _guard_name("cluster", str(cluster_name))
    api_base = _guard_server(cluster.get("server"))

    user_name = context.get("user")
    user = users.get(user_name) if isinstance(user_name, str) else None
    if user is None:
        raise AcceptanceKubeconfigError("kubeconfig context references an unknown user")

    tempdir: tempfile.TemporaryDirectory[str] | None = None
    try:
        ca_file = cluster.get("certificate-authority")
        ca_data = cluster.get("certificate-authority-data")
        insecure = cluster.get("insecure-skip-tls-verify") is True
        if isinstance(ca_data, str) and ca_data:
            tempdir = tempdir or tempfile.TemporaryDirectory(prefix="plugin-acceptance-")
            ca_path = Path(tempdir.name) / "ca.crt"
            _write_private(ca_path, _b64(ca_data, what="certificate-authority-data"))
            ca_file = str(ca_path)
        if ca_file:
            ca_path = Path(str(ca_file)).expanduser()
            if not ca_path.is_absolute():
                ca_path = (path.parent / ca_path).resolve()
            ca_file = str(ca_path)
        context_ssl = _tls_context(ca_file, insecure=insecure)
        token_ssl_context = _tls_context(ca_file, insecure=insecure)

        token = user.get("token")
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise AcceptanceKubeconfigError("kubeconfig user token is empty")
        if isinstance(token, str):
            token = token.strip()
        cert_pem = user.get("client-certificate-data")
        key_pem = user.get("client-key-data")
        cert_file = user.get("client-certificate")
        key_file = user.get("client-key")
        if cert_pem or key_pem:
            if not cert_pem or not key_pem:
                raise AcceptanceKubeconfigError("kubeconfig client cert/key data must be provided together")
            tempdir = tempdir or tempfile.TemporaryDirectory(prefix="plugin-acceptance-")
            cert_path = Path(tempdir.name) / "client.crt"
            key_path = Path(tempdir.name) / "client.key"
            _write_private(cert_path, _b64(cert_pem, what="client-certificate-data"))
            _write_private(key_path, _b64(key_pem, what="client-key-data"))
            context_ssl.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        elif cert_file or key_file:
            if not cert_file or not key_file:
                raise AcceptanceKubeconfigError("kubeconfig client cert/key files must be provided together")
            cert_path = Path(str(cert_file)).expanduser()
            key_path = Path(str(key_file)).expanduser()
            if not cert_path.is_absolute():
                cert_path = (path.parent / cert_path).resolve()
            if not key_path.is_absolute():
                key_path = (path.parent / key_path).resolve()
            context_ssl.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        elif not token:
            raise AcceptanceKubeconfigError(
                "kubeconfig user must use a bearer token or client certificate/key "
                "(exec plugins and auth providers are intentionally unsupported)"
            )
    except AcceptanceKubeconfigError:
        if tempdir is not None:
            tempdir.cleanup()
        raise
    except (ssl.SSLError, OSError) as exc:
        if tempdir is not None:
            tempdir.cleanup()
        raise AcceptanceKubeconfigError(
            f"kubeconfig TLS/client credentials are unusable: {type(exc).__name__}"
        ) from None
    return KubeconfigIdentity(
        api_base=api_base,
        ssl_context=context_ssl,
        token_ssl_context=token_ssl_context,
        token=token,
        _tempdir=tempdir,
    )


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


__all__ = [
    "AcceptanceKubeconfigError",
    "KubeconfigAuth",
    "KubeconfigIdentity",
    "NoMountedTokenAuth",
    "StaticTokenAuth",
    "load_identity",
]
