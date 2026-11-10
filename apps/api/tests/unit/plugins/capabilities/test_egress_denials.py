"""Реальные отказы egress-адаптера, а не самопроверка его же kwargs.

Существующий тест объявлял ``Egress.check`` с assert-ами на собственные
аргументы и возвращал ``allowed=True``: он проверял, что адаптер передал
ожидаемые kwargs, но НЕ проверял отказ. Здесь решение приходит из политики,
и адаптер обязан бросить отказ до возврата ответа плагину.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters.network import K8sEgressNetworkAdapter
from prodavan.application.plugins.capabilities.contracts import (
    NetworkEgressRequest,
    NetworkHttpMethod,
)

from test_service import envelope  # noqa: E402 - same-directory test module

TEST_DIGEST = "sha256:" + "a" * 64

pytestmark = pytest.mark.asyncio


class _Transport:
    scoped_identity = True

    def __init__(self, *, resolved_host: str = "api.example.com") -> None:
        self.resolved_host = resolved_host
        self.sent = 0

    async def execute(self, *, identity: dict[str, Any]) -> dict[str, Any]:
        self.sent += 1
        return {
            **identity,
            "status_code": 200,
            "response_digest": TEST_DIGEST,
            "bytes_sent": 0,
            "bytes_received": 42,
            "resolved_host": self.resolved_host,
            "hops": (self.resolved_host,),
            "redirect_checked": True,
            "tls_verified": True,
        }


class _AllowPolicy:
    async def allows(self, **kwargs: Any) -> bool:
        return True


class _DenyingEgress:
    """Политика, которая разрешает pre-flight и ЗАПРЕЩАЕТ по фактам хопа."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def check(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # До запроса R-10 фактов нет: pre-flight обязан пропустить.
        if "resolved_host" not in kwargs:
            return SimpleNamespace(allowed=True)
        return SimpleNamespace(allowed=False)


class _RaisingEgress:
    def check(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("policy backend down")


def _request() -> NetworkEgressRequest:
    return NetworkEgressRequest(
        host="api.example.com", port=443, path="/v1/status",
        method=NetworkHttpMethod.GET, envelope=envelope(),
    )


async def test_per_hop_denial_is_a_typed_denial_not_a_success() -> None:
    egress = _DenyingEgress()
    adapter = K8sEgressNetworkAdapter(
        transport=_Transport(), egress=egress, initial_policy=_AllowPolicy(),
        max_response_bytes=1024,
    )
    with pytest.raises(Exception, match="network_per_hop_policy_denied"):
        await adapter.egress(_request())
    # Два вызова: pre-flight до запроса и итоговая проверка с фактами хопа.
    assert len(egress.calls) == 2
    assert "resolved_host" not in egress.calls[0]
    assert egress.calls[1]["resolved_host"] == "api.example.com"


async def test_foreign_resolved_host_is_reported_to_the_policy_not_blocked_by_the_adapter() -> None:
    """Документирует ФАКТИЧЕСКИЙ контракт: решение принимает политика.

    Адаптер обязан передать настоящий resolved_host (а не запрошенный host)
    в egress-политику. Он не сравнивает хост сам: SSRF-защита живёт в
    политике, и именно там redirect на metadata должен получить отказ.
    Этот тест фиксирует границу, чтобы её нельзя было молча перенести.
    """
    seen: list[Any] = []

    class _Recording:
        def check(self, *args: Any, **kwargs: Any) -> Any:
            # Pre-flight не несёт R-10 фактов; фиксируем только итоговую.
            if "resolved_host" in kwargs:
                seen.append(kwargs.get("resolved_host"))
            return SimpleNamespace(allowed=True)

    adapter = K8sEgressNetworkAdapter(
        transport=_Transport(resolved_host="169.254.169.254"),
        egress=_Recording(), initial_policy=_AllowPolicy(), max_response_bytes=1024,
    )
    await adapter.egress(_request())
    assert seen == ["169.254.169.254"]


async def test_preflight_denial_stops_the_request_before_it_is_sent() -> None:
    """Отказ политики до запроса = запрос НЕ уходит (а не «пометить после»)."""
    transport = _Transport()
    calls: list[int] = []

    class _DenyBeforeRequest:
        def check(self, *args: Any, **kwargs: Any) -> Any:
            if "resolved_host" not in kwargs:
                return SimpleNamespace(allowed=False)
            calls.append(1)
            return SimpleNamespace(allowed=True)

    adapter = K8sEgressNetworkAdapter(
        transport=transport, egress=_DenyBeforeRequest(),
        initial_policy=_AllowPolicy(), max_response_bytes=1024,
    )
    with pytest.raises(Exception, match="network_preflight_policy_denied"):
        await adapter.egress(_request())
    # Транспорт не вызывался вовсе: побочного egress-запроса не было.
    assert transport.sent == 0
    assert calls == []


async def test_policy_backend_failure_is_fail_closed() -> None:
    adapter = K8sEgressNetworkAdapter(
        transport=_Transport(), egress=_RaisingEgress(), initial_policy=_AllowPolicy(),
        max_response_bytes=1024,
    )
    with pytest.raises(Exception):
        await adapter.egress(_request())
