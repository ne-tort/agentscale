"""``K8sEgressProxyTransport``: единственный транспорт egress-запросов.

Модуль не покрывался ни одним тестом, хотя именно он доставляет egress-вызов
через service-proxy API и обязан: не принимать дефолтный mounted SA, требовать
scoped token provider, держать host/path в теле (а не в URL) и закрываться
идемпотентно.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.infrastructure.plugins.capabilities.kubernetes import K8sEgressProxyTransport



class _Api:
    def __init__(self, token_provider: Any, body: Any = None) -> None:
        self._token_provider = token_provider
        self.body = body if body is not None else {
            "status_code": 200, "resolved_host": "api.example.com",
            "hops": ["api.example.com"], "redirect_checked": True, "tls_verified": True,
        }
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    async def request(self, method: str, path: str, *, body: Any = None, content_type: Any = None):
        self.calls.append({"method": method, "path": path, "body": body})
        return SimpleNamespace(body=self.body)

    async def aclose(self) -> None:
        self.closed += 1


class _Token:
    async def get_token(self) -> str:
        return "scoped-token"


def _transport(api: _Api) -> K8sEgressProxyTransport:
    return K8sEgressProxyTransport(
        api=api, token_provider=api._token_provider,
        namespace="agentscale-dev", service_name="egress-proxy", service_port=8080,
    )


def test_requires_a_scoped_token_provider() -> None:
    api = _Api(token_provider=None)
    with pytest.raises(ValueError, match="scoped K8s token provider is required"):
        _transport(api)


def test_rejects_an_api_bound_to_a_different_token_provider() -> None:
    # Дефолтный mounted SA-клиент не должен проходить как scoped identity.
    api = _Api(token_provider=_Token())
    with pytest.raises(ValueError, match="egress API and scoped token provider differ"):
        K8sEgressProxyTransport(
            api=api, token_provider=_Token(),
            namespace="agentscale-dev", service_name="egress-proxy",
        )


def test_rejects_invalid_namespace_or_service_name() -> None:
    api = _Api(token_provider=_Token())
    for namespace, service in (("Bad_NS", "egress-proxy"), ("agentscale-dev", "bad name")):
        with pytest.raises(ValueError, match="egress namespace/service is invalid"):
            K8sEgressProxyTransport(
                api=api, token_provider=api._token_provider,
                namespace=namespace, service_name=service,
            )


async def test_host_and_path_travel_in_the_body_not_the_url() -> None:
    api = _Api(token_provider=_Token())
    transport = _transport(api)
    await transport.execute(identity={"host": "169.254.169.254", "path": "/latest/meta"})
    assert api.calls[0]["path"].endswith("/proxy/v1/egress")
    assert "169.254" not in api.calls[0]["path"]
    assert api.calls[0]["body"] == {"host": "169.254.169.254", "path": "/latest/meta"}


async def test_invalid_proxy_result_is_fail_closed() -> None:
    api = _Api(token_provider=_Token(), body=["not", "a", "mapping"])
    transport = _transport(api)
    with pytest.raises(RuntimeError, match="invalid result"):
        await transport.execute(identity={"host": "api.example.com"})


async def test_closed_transport_refuses_and_close_is_idempotent() -> None:
    api = _Api(token_provider=_Token())
    transport = _transport(api)
    await transport.aclose()
    await transport.aclose()
    assert api.closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        await transport.execute(identity={"host": "api.example.com"})
