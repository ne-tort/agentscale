"""Контракт ``invoke_capability``: единственная точка вызова сервиса.

``invoke_capability`` — ровно тот код, через который проходит КАЖДЫЙ
capability-запрос роутера, но он не покрывался ни одним тестом: HTTP-тесты
подменяли сервис, а юнит-тесты сервиса не доходили до транспорта. Значит
маппинг ``decision/code`` в problem-ответ и fail-closed поведение при
непредвиденном исключении не были закреплены нигде.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from prodavan.application.plugins.transport.bridge import invoke_capability
from prodavan.application.plugins.transport.errors import PluginTransportError

pytestmark = pytest.mark.asyncio


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/capabilities/v1/cache/get",
                    "headers": [], "query_string": b""})


class _Service:
    def __init__(self, result: Any = None, exc: BaseException | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls = 0

    async def invoke(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


async def _run(service: _Service) -> dict[str, Any]:
    return await invoke_capability(
        request=_request(),
        claims=SimpleNamespace(),  # type: ignore[arg-type]
        service=service,  # type: ignore[arg-type]
        capability="cache",
        operation="cache.get",
        domain_request=object(),
    )


async def test_allow_decision_returns_the_canonical_envelope() -> None:
    service = _Service(SimpleNamespace(
        decision="allow", code="allowed", data={"found": True},
    ))
    payload = await _run(service)
    assert payload == {"decision": "allow", "data": {"found": True}}
    assert service.calls == 1


async def test_deny_decision_and_code_map_to_a_typed_problem() -> None:
    service = _Service(SimpleNamespace(
        decision="deny", code="grant_revoked", reason="grant revoked", data=None,
    ))
    with pytest.raises(PluginTransportError) as caught:
        await _run(service)
    # Штатный deny — не исключение сервиса, а типизированный problem-ответ.
    assert caught.value.problem.status >= 400


async def test_unexpected_service_exception_is_fail_closed() -> None:
    service = _Service(exc=RuntimeError("adapter exploded"))
    with pytest.raises(PluginTransportError) as caught:
        await _run(service)
    assert caught.value.problem.status == 503
    assert caught.value.problem.code == "ADAPTER_FAILED"


async def test_exception_text_never_reaches_the_problem_detail() -> None:
    secret_text = "postgresql://user:password@internal-db:5432/prod"
    service = _Service(exc=RuntimeError(secret_text))
    with pytest.raises(PluginTransportError) as caught:
        await _run(service)
    assert secret_text not in str(caught.value.problem.detail)
