"""Security regressions Plugin Gateway. Написаны, но НЕ запускаются (freeze)."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import Request

from prodavan.api.v1.plugin_gateway import _client_ip, _read_bounded_body
from prodavan.application.plugins.gateway.auth import (
    HEADER_DELIVERY,
    HEADER_EVENT,
    HEADER_SIGNATURE,
    HEADER_SIGNATURE_VERSION,
    HEADER_TIMESTAMP,
    GatewayLimits,
)
from prodavan.application.plugins.gateway.canonical import (
    webhook_external_path,
    webhook_v2_signing_bytes,
)
from prodavan.application.plugins.gateway.errors import GatewayError, GatewayErrorCode
from prodavan.application.plugins.gateway.ports import BackendRequest, BackendResponse

from .conftest import WEBHOOK_SECRET, RecordingHttpPort, build_harness

NOW = 1_700_000_000


def _signature(secret: str, signing_bytes: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), signing_bytes, hashlib.sha256).hexdigest()


def _v1_headers(body: bytes, *, delivery: str = "del-security") -> dict[str, str]:
    return {
        HEADER_SIGNATURE: _signature(WEBHOOK_SECRET, body),
        HEADER_EVENT: "offer.created",
        HEADER_DELIVERY: delivery,
        "Content-Type": "application/json",
    }


def _v2_headers(
    body: bytes,
    *,
    delivery: str = "del-v2",
    timestamp: str = str(NOW),
    method: str = "POST",
    path: str = "events",
    raw_query: bytes = b"b=2&a=1&a=0",
) -> dict[str, str]:
    external = webhook_external_path(
        plugin_id="mod_equipment", company_id="co-123", path=path
    )
    canonical = webhook_v2_signing_bytes(
        timestamp=timestamp,
        delivery_id=delivery,
        event="offer.created",
        method=method,
        canonical_path=external,
        raw_query=raw_query,
        raw_body=body,
    )
    return {
        HEADER_SIGNATURE: _signature(WEBHOOK_SECRET, canonical),
        HEADER_SIGNATURE_VERSION: "2",
        HEADER_TIMESTAMP: timestamp,
        HEADER_EVENT: "offer.created",
        HEADER_DELIVERY: delivery,
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_chunked_body_over_limit_stops_before_auth_and_next_chunk() -> None:
    h = build_harness(limits=GatewayLimits(webhook_body_limit_bytes=8))
    chunks = [b"1234", b"5678", b"9", b"unreachable"]
    yielded: list[bytes] = []

    async def body_stream():
        for chunk in chunks:
            yielded.append(chunk)
            yield chunk

    body = b"".join(chunks)
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body_stream(),
            headers=_v1_headers(body),
        )
    assert response.status_code == 413
    assert response.json()["code"] == GatewayErrorCode.PAYLOAD_TOO_LARGE.value
    assert yielded == chunks[:2]
    assert h.secrets.calls == []
    assert h.rate_limit.allow_calls == []
    assert h.backend_http.requests == []


@pytest.mark.asyncio
async def test_content_length_over_limit_does_not_read_stream() -> None:
    reads = 0

    async def receive() -> dict[str, object]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"123456789", "more_body": False}

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/webhook/p/c/events",
            "raw_path": b"/webhook/p/c/events",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-length", b"9")],
            "client": ("192.0.2.1", 1234),
            "server": ("test", 80),
        },
        receive,
    )
    with pytest.raises(GatewayError) as exc:
        await _read_bounded_body(request, limit_bytes=8)
    assert exc.value.status == 413
    assert reads == 0


@pytest.mark.asyncio
async def test_exact_body_cap_in_final_asgi_message_is_accepted() -> None:
    reads = 0

    async def receive() -> dict[str, object]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"12345678", "more_body": False}

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/webhook/p/c/events",
            "raw_path": b"/webhook/p/c/events",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("192.0.2.1", 1234),
            "server": ("test", 80),
        },
        receive,
    )
    assert await _read_bounded_body(request, limit_bytes=8) == b"12345678"
    assert reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("is_api", [False, True])
async def test_page_and_typed_payload_have_independent_caps(is_api: bool) -> None:
    limits = GatewayLimits(page_body_limit_bytes=4, typed_payload_limit_bytes=8)
    h = build_harness(
        limits=limits,
        public_pages=frozenset() if is_api else frozenset({"/public/info"}),
    )
    path = "api/items" if is_api else "public/info"
    body = b"123456789"
    async with h.client as client:
        response = await client.post(
            f"/pluginpage/mod_equipment/co-123/{path}",
            content=body,
            headers={"Authorization": "Bearer good-session"},
        )
    assert response.status_code == 413
    assert h.sessions.resolve_calls == []
    assert h.backend_http.requests == []
    assert h.static_http.requests == []


@pytest.mark.asyncio
async def test_raw_query_trailing_path_and_verified_signature_reach_backend() -> None:
    h = build_harness()
    body = b"{}"
    query = b"b=2&a=1&a=0&company_id=evil&tenant=evil"
    signature_headers = _v1_headers(body)
    signature_headers["Host"] = "evil.example"
    signature_headers["X-Forwarded-Host"] = "evil.example"
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events/?" + query.decode(),
            content=body,
            headers=signature_headers,
        )
    assert response.status_code == 200
    sent = h.backend_http.requests[0]
    assert sent.path == "/events/"
    assert sent.query_string == query
    assert sent.headers["X-Company-ID"] == "co-123"
    assert sent.headers[HEADER_SIGNATURE] == signature_headers[HEADER_SIGNATURE]
    assert sent.headers[HEADER_EVENT] == "offer.created"
    assert sent.headers[HEADER_DELIVERY] == "del-security"
    assert "Host" not in sent.headers
    assert "X-Forwarded-Host" not in sent.headers


@pytest.mark.asyncio
async def test_static_proxy_preserves_raw_query() -> None:
    h = build_harness(public_pages=frozenset({"/public/info"}))
    async with h.client as client:
        response = await client.get(
            "/pluginpage/mod_equipment/co-123/public/info?x=%2F&x=+&company=evil"
        )
    assert response.status_code == 200
    assert h.static_http.requests[0].query_string == b"x=%2F&x=+&company=evil"


@pytest.mark.asyncio
async def test_v2_valid_signature_is_readded_with_version_and_timestamp() -> None:
    h = build_harness()
    h.auth.clock = lambda: float(NOW)
    body = b'{"ok":true}'
    query = b"b=2&a=1&a=0"
    headers = _v2_headers(body, raw_query=query)
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events?" + query.decode(),
            content=body,
            headers=headers,
        )
    assert response.status_code == 200
    sent = h.backend_http.requests[0]
    assert sent.query_string == query
    assert sent.headers[HEADER_SIGNATURE_VERSION] == "2"
    assert sent.headers[HEADER_TIMESTAMP] == str(NOW)
    assert sent.headers[HEADER_SIGNATURE] == headers[HEADER_SIGNATURE]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["stale", "path", "method"])
async def test_v2_rejects_bad_timestamp_path_and_method(mutation: str) -> None:
    h = build_harness()
    h.auth.clock = lambda: float(NOW)
    body = b"{}"
    query = b"b=2&a=1&a=0"
    if mutation == "stale":
        headers = _v2_headers(body, timestamp=str(NOW - 3600), raw_query=query)
        expected = GatewayErrorCode.INVALID_TIMESTAMP
    elif mutation == "path":
        headers = _v2_headers(body, path="other", raw_query=query)
        expected = GatewayErrorCode.INVALID_SIGNATURE
    else:
        headers = _v2_headers(body, method="GET", raw_query=query)
        expected = GatewayErrorCode.INVALID_SIGNATURE
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events?" + query.decode(),
            content=body,
            headers=headers,
        )
    assert response.status_code == 401
    assert response.json()["code"] == expected.value
    assert h.backend_http.requests == []


class _SequenceHttp(RecordingHttpPort):
    def __init__(self, *statuses: int) -> None:
        super().__init__()
        self._statuses = list(statuses)

    async def send(self, request: BackendRequest) -> BackendResponse:
        self.requests.append(request)
        status = self._statuses.pop(0)
        return BackendResponse(status_code=status, body=str(status).encode())


@pytest.mark.asyncio
async def test_backend_502_releases_lease_and_retry_is_forwarded() -> None:
    h = build_harness()
    sequence = _SequenceHttp(502, 200)
    h.proxy.backend_http = sequence
    body = b"{}"
    headers = _v1_headers(body, delivery="del-retry-502")
    async with h.client as client:
        first = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
        second = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
        third = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
    assert first.status_code == 502
    assert second.status_code == 200
    assert third.json()["status"] == "duplicate"
    assert len(sequence.requests) == 2
    assert len(h.rate_limit.release_calls) == 1
    assert len(h.rate_limit.commit_calls) == 1


@pytest.mark.asyncio
async def test_backend_202_is_not_committed_as_processed() -> None:
    h = build_harness()
    sequence = _SequenceHttp(202, 200)
    h.proxy.backend_http = sequence
    body = b"{}"
    headers = _v1_headers(body, delivery="del-retry-202")
    async with h.client as client:
        first = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
        second = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
    assert first.status_code == 202
    assert second.status_code == 200
    assert len(sequence.requests) == 2
    assert len(h.rate_limit.release_calls) == 1
    assert len(h.rate_limit.commit_calls) == 1


@pytest.mark.asyncio
async def test_in_progress_delivery_is_503_not_duplicate_200() -> None:
    h = build_harness()
    body = b"{}"
    kwargs = {
        "plugin_id": "mod_equipment",
        "company_id": "co-123",
        "path": "events",
        "method": "POST",
        "raw_body": body,
        "headers": _v1_headers(body, delivery="del-in-progress"),
    }
    first = await h.auth.authorize_webhook(**kwargs)
    with pytest.raises(GatewayError) as exc:
        await h.auth.authorize_webhook(**kwargs)
    assert exc.value.gateway_code is GatewayErrorCode.DELIVERY_IN_PROGRESS
    assert exc.value.status == 503
    assert int(exc.value.retry_after_seconds or 0) > 0
    assert h.backend_http.requests == []
    await h.auth.release_webhook(first)


@pytest.mark.asyncio
async def test_bad_hmac_consumes_preauth_but_not_installation_quota() -> None:
    h = build_harness()
    body = b"{}"
    headers = _v1_headers(body)
    headers[HEADER_SIGNATURE] = "sha256=" + "0" * 64
    async with h.client as client:
        response = await client.post(
            "/webhook/mod_equipment/co-123/events", content=body, headers=headers
        )
    assert response.status_code == 401
    buckets = [call["bucket"] for call in h.rate_limit.allow_calls]
    assert any("webhook_preauth_ip" in bucket for bucket in buckets)
    assert any("webhook_preauth_global" in bucket for bucket in buckets)
    assert not any(":webhook:mod_equipment:co-123" in bucket for bucket in buckets)


@pytest.mark.asyncio
async def test_untrusted_xff_cannot_select_preauth_bucket() -> None:
    h = build_harness()
    body = b"{}"
    buckets: list[str] = []
    async with h.client as client:
        for forwarded in ("198.51.100.1", "203.0.113.99"):
            headers = _v1_headers(body, delivery=forwarded)
            headers[HEADER_SIGNATURE] = "sha256=" + "0" * 64
            headers["X-Forwarded-For"] = forwarded
            await client.post(
                "/webhook/mod_equipment/co-123/events", content=body, headers=headers
            )
            call = next(
                item for item in reversed(h.rate_limit.allow_calls)
                if "webhook_preauth_ip" in item["bucket"]
            )
            buckets.append(call["bucket"])
    assert buckets[0] == buckets[1]
    assert "198.51.100.1" not in buckets[0]
    assert "203.0.113.99" not in buckets[0]


def test_xff_is_used_only_for_configured_trusted_peer() -> None:
    limits = GatewayLimits(trusted_proxy_cidrs=("10.0.0.0/8",))
    h = build_harness(limits=limits)

    def request(peer: str) -> Request:
        return Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/webhook/p/c/events",
                "raw_path": b"/webhook/p/c/events",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"x-forwarded-for", b"198.51.100.8, 10.0.0.3")],
                "client": (peer, 1234),
                "server": ("test", 80),
                "app": h.app,
            }
        )

    assert _client_ip(request("192.0.2.10")) == "192.0.2.10"
    assert _client_ip(request("10.0.0.2")) == "198.51.100.8"


@pytest.mark.asyncio
async def test_manifest_webhook_allowlist_is_enforced_when_available() -> None:
    h = build_harness(webhook_routes=frozenset({"/events"}))
    body = b"{}"
    async with h.client as client:
        denied = await client.post(
            "/webhook/mod_equipment/co-123/other",
            content=body,
            headers=_v1_headers(body, delivery="del-route-denied"),
        )
        allowed = await client.post(
            "/webhook/mod_equipment/co-123/events",
            content=body,
            headers=_v1_headers(body, delivery="del-route-allowed"),
        )
    assert denied.status_code == 403
    assert denied.json()["code"] == GatewayErrorCode.WEBHOOK_ROUTE_NOT_ALLOWED.value
    assert allowed.status_code == 200
    assert len(h.backend_http.requests) == 1
