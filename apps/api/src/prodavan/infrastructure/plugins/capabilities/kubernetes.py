"""Scoped-Kubernetes-identity egress transport; no default mounted SA token."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_DNS = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class K8sEgressProxyTransport:
    """Call a constrained egress proxy through the K8s service proxy API.

    The proxy must return typed metadata plus per-hop evidence. No direct
    internet connection and no mounted/default ServiceAccount is used here.
    """

    scoped_identity = True

    def __init__(
        self,
        *,
        api: Any,
        token_provider: Any,
        namespace: str,
        service_name: str,
        service_port: int = 8080,
    ) -> None:
        if api is None or not callable(getattr(api, "request", None)):
            raise ValueError("K8s egress API is required")
        if token_provider is None or not callable(getattr(token_provider, "get_token", None)):
            raise ValueError("scoped K8s token provider is required")
        if getattr(api, "_token_provider", token_provider) is not token_provider:
            raise ValueError("egress API and scoped token provider differ")
        if not _DNS.fullmatch(namespace) or not _DNS.fullmatch(service_name):
            raise ValueError("egress namespace/service is invalid")
        if not isinstance(service_port, int) or not 1 <= service_port <= 65535:
            raise ValueError("egress service port is invalid")
        self._api = api
        self._closed = False
        self._path = (
            f"/api/v1/namespaces/{namespace}/services/"
            f"{service_name}:{service_port}/proxy/v1/egress"
        )

    async def execute(self, *, identity: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("egress transport is closed")
        # User-controlled host/path are JSON fields, never K8s URL path segments.
        response = await self._api.request(
            "POST",
            self._path,
            body=dict(identity),
            content_type="application/json",
        )
        body = getattr(response, "body", None)
        if not isinstance(body, Mapping):
            raise RuntimeError("egress proxy returned an invalid result")
        return body

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._api, "aclose", None)
        if callable(close):
            await close()


__all__ = ["K8sEgressProxyTransport"]
