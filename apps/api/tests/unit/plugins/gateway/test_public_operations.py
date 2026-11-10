"""Regressions для узкого anonymous read-only public operation MVP.

Public bootstrap не является auth proof: declarations приходят через GrantPort
из signed manifest. Anonymous получает только exact public GET/HEAD без body;
webhook HMAC остаётся независимым fail-closed контуром. Тесты не запускаются
во время migration verification freeze.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from prodavan.application.plugins.gateway.auth import (
    HEADER_DELIVERY,
    HEADER_EVENT,
    HEADER_SIGNATURE,
)
from prodavan.application.plugins.gateway.errors import GatewayError, GatewayErrorCode
from prodavan.application.plugins.gateway.ports import ANON_SCOPES, GatewayOperation

from .conftest import WEBHOOK_SECRET, build_harness

PLUGIN = "mod_equipment"
COMPANY = "co-123"
PUBLIC_ROUTE = "/public"
PUBLIC_GET = GatewayOperation(
    capability_id="hello.greetings",
    capability_version="1.0",
    operation="list",
    http_method="GET",
    backend_path="/api/greetings",
    public=True,
)
PUBLIC_POST = GatewayOperation(
    capability_id="hello.greetings",
    capability_version="1.0",
    operation="create",
    http_method="POST",
    backend_path="/api/greetings/create",
    public=True,
)
PRIVATE_GET = GatewayOperation(
    capability_id="hello.greetings",
    capability_version="1.0",
    operation="private",
    http_method="GET",
    backend_path="/api/greetings/private",
    public=False,
)
OPERATIONS = (PUBLIC_GET, PUBLIC_POST, PRIVATE_GET)


def _harness(*, public_pages: frozenset[str] = frozenset({PUBLIC_ROUTE})):
    return build_harness(public_pages=public_pages, rpc_operations=OPERATIONS)


async def _authorize(
    h,
    *,
    path: str = PUBLIC_GET.backend_path,
    method: str = "GET",
    route_path: str | None = PUBLIC_ROUTE,
    body: bytes = b"",
    query: bytes = b"",
    headers: dict[str, str] | None = None,
):
    return await h.auth.authorize_page(
        plugin_id=PLUGIN,
        company_id=COMPANY,
        path=path,
        headers=headers or {},
        is_api=True,
        method=method,
        raw_body=body,
        raw_query=query,
        route_path=route_path,
    )


def _webhook_signature(body: bytes) -> str:
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_public_read_only_operation_allows_anonymous_without_private_token(
    method: str,
) -> None:
    h = _harness()

    result = await _authorize(h, method=method, query=b"limit=10")

    assert result.public is True
    assert result.session is None
    assert result.claims.anonymous is True
    assert result.claims.scopes == ANON_SCOPES
    assert result.claims.employee_id is None
    assert result.claims.project_id is None
    assert result.grant.capability_token == ""
    assert result.grant.operations == frozenset()
    assert h.sessions.resolve_calls == []
    assert h.secrets.calls == []
    assert any(":public_page:" in call["bucket"] for call in h.rate_limit.allow_calls)
    opened = next(event for event in h.audit.events if event.decision == "allow")
    assert opened.metadata["operation"] == "list"
    assert opened.metadata["capability"] == "hello.greetings@1.0"


@pytest.mark.asyncio
async def test_public_operation_forward_never_leaks_private_lease_token() -> None:
    h = _harness()
    result = await _authorize(h, query=b"limit=10")

    await h.proxy.forward(
        path=PUBLIC_GET.backend_path,
        method="GET",
        body=b"",
        headers={},
        claims=result.claims,
        grant=result.grant,
        query_string=b"limit=10",
    )

    assert len(h.backend_http.requests) == 1
    sent = h.backend_http.requests[0]
    assert sent.query_string == b"limit=10"
    assert sent.headers["X-Plugin-ID"] == PLUGIN
    assert sent.headers["X-Company-ID"] == COMPANY
    assert "X-Plugin-Capability-Token" not in sent.headers
    assert "Authorization" not in sent.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {HEADER_SIGNATURE: ""},
        {HEADER_SIGNATURE: "sha256=" + "0" * 64},
    ],
)
async def test_side_effecting_operation_is_denied_with_any_signature(
    method: str, headers: dict[str, str],
) -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(
            h,
            path=PUBLIC_POST.backend_path,
            method=method,
            body=b'{"name":"alice"}',
            headers=headers,
        )

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.sessions.resolve_calls == []
    assert h.secrets.calls == []
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_anonymous_get_with_body_is_denied() -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, body=b'{"unexpected":true}')

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.sessions.resolve_calls == []


@pytest.mark.asyncio
async def test_altered_post_payload_cannot_use_webhook_signature_as_bypass() -> None:
    h = _harness()
    original = b'{"name":"before"}'
    altered = b'{"name":"after"}'

    with pytest.raises(GatewayError) as exc:
        await _authorize(
            h,
            path=PUBLIC_POST.backend_path,
            method="POST",
            body=altered,
            headers={HEADER_SIGNATURE: _webhook_signature(original)},
        )

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.secrets.calls == []


@pytest.mark.asyncio
async def test_unknown_operation_is_denied() -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, path="/api/not-declared")

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.activation.wake_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route_path", ["public", "///public"])
async def test_noncanonical_public_route_is_not_normalized_into_allowance(
    route_path: str,
) -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, route_path=route_path)

    assert exc.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_public_operation_without_explicit_request_evidence_is_denied() -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await h.auth.authorize_page(
            plugin_id=PLUGIN,
            company_id=COMPANY,
            path=PUBLIC_GET.backend_path,
            headers={},
            is_api=True,
            route_path=PUBLIC_ROUTE,
        )

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.sessions.resolve_calls == []


@pytest.mark.asyncio
async def test_missing_manifest_registry_does_not_turn_bootstrap_into_auth_proof() -> None:
    h = _harness()

    async def _unavailable(*, plugin_id: str, company_id: str):
        return None

    h.grants.rpc_operations = _unavailable  # type: ignore[method-assign]

    with pytest.raises(GatewayError) as exc:
        await _authorize(h)

    assert exc.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED
    assert exc.value.status == 401
    assert h.sessions.resolve_calls == [None]


@pytest.mark.asyncio
async def test_valid_session_is_not_downgraded_to_anonymous() -> None:
    h = _harness()

    result = await _authorize(
        h, headers={"Authorization": "Bearer good-session"}
    )

    assert result.public is False
    assert result.claims.anonymous is False
    assert result.session is not None
    assert result.claims.employee_id == "emp-1"
    assert h.sessions.resolve_calls == ["good-session"]


@pytest.mark.asyncio
async def test_non_public_route_does_not_use_public_operation_exception() -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, route_path="/private")

    assert exc.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED
    assert exc.value.status == 401
    assert h.sessions.resolve_calls == [None]


@pytest.mark.asyncio
async def test_public_page_prefix_is_not_an_exact_route_binding() -> None:
    h = _harness(public_pages=frozenset({"/public/"}))

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, route_path="/public/child")

    assert exc.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_private_operation_without_session_is_denied() -> None:
    h = _harness()

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, path=PRIVATE_GET.backend_path)

    assert exc.value.gateway_code is GatewayErrorCode.OPERATION_NOT_ALLOWED
    assert exc.value.status == 403
    assert h.sessions.resolve_calls == []
    assert h.activation.wake_calls == []


@pytest.mark.asyncio
async def test_generic_api_remains_session_protected() -> None:
    h = _harness(public_pages=frozenset({"/api"}))

    with pytest.raises(GatewayError) as exc:
        await _authorize(h, path="/api/items", route_path=None)

    assert exc.value.gateway_code is GatewayErrorCode.SESSION_REQUIRED
    assert exc.value.status == 401
    assert h.sessions.resolve_calls == [None]
    assert h.activation.wake_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        (None, GatewayErrorCode.WEBHOOK_HEADERS_MISSING),
        ("sha256=" + "0" * 64, GatewayErrorCode.INVALID_SIGNATURE),
    ],
)
async def test_webhook_missing_or_invalid_signature_remains_fail_closed(
    signature: str | None, expected: GatewayErrorCode
) -> None:
    h = _harness()
    headers = {
        HEADER_EVENT: "chat.message.completed",
        HEADER_DELIVERY: "delivery-public-op-regression",
    }
    if signature is not None:
        headers[HEADER_SIGNATURE] = signature

    with pytest.raises(GatewayError) as exc:
        await h.auth.authorize_webhook(
            plugin_id=PLUGIN,
            company_id=COMPANY,
            path="/webhook/events",
            method="POST",
            raw_body=b'{"event":"x"}',
            headers=headers,
        )

    assert exc.value.gateway_code is expected
    assert exc.value.status == 401
    assert h.rate_limit.claim_calls == []


@pytest.mark.asyncio
async def test_webhook_altered_payload_still_invalidates_signature() -> None:
    h = _harness()
    original = b'{"event":"before"}'

    with pytest.raises(GatewayError) as exc:
        await h.auth.authorize_webhook(
            plugin_id=PLUGIN,
            company_id=COMPANY,
            path="/webhook/events",
            method="POST",
            raw_body=b'{"event":"after"}',
            headers={
                HEADER_SIGNATURE: _webhook_signature(original),
                HEADER_EVENT: "chat.message.completed",
                HEADER_DELIVERY: "delivery-public-op-altered",
            },
        )

    assert exc.value.gateway_code is GatewayErrorCode.INVALID_SIGNATURE
    assert exc.value.status == 401
    assert h.rate_limit.claim_calls == []
