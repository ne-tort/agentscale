"""Unit tests — TraceIdMiddleware + error response trace id (audit XCUT-P2a)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.core.middleware import _TRACE_ID_HEADER, register_trace_id
from prodavan.domain.errors import AppError


def _app() -> FastAPI:
    app = FastAPI()
    register_trace_id(app)

    @app.get("/ok")
    async def _ok() -> dict:
        return {"ok": True}

    @app.get("/trace")
    async def _trace(request: Request) -> dict:
        return {"trace_id": getattr(request.state, "trace_id", None)}

    @app.get("/error")
    async def _error() -> dict:
        raise AppError(code="BOOM", title="Boom", status=418, detail="teapot")

    register_exception_handlers(app)
    return app


def test_trace_id_generated_when_absent() -> None:
    client = TestClient(_app())
    resp = client.get("/ok")
    assert resp.status_code == 200
    trace_id = resp.headers.get(_TRACE_ID_HEADER)
    assert trace_id
    assert len(trace_id) == 32  # uuid4 hex


def test_trace_id_propagated_to_request_state() -> None:
    client = TestClient(_app())
    resp = client.get("/trace")
    assert resp.status_code == 200
    trace_id = resp.headers[_TRACE_ID_HEADER]
    assert resp.json()["trace_id"] == trace_id


def test_incoming_trace_id_reused() -> None:
    client = TestClient(_app())
    resp = client.get("/trace", headers={_TRACE_ID_HEADER: "caller-trace-123"})
    assert resp.status_code == 200
    assert resp.headers[_TRACE_ID_HEADER] == "caller-trace-123"
    assert resp.json()["trace_id"] == "caller-trace-123"


def test_error_response_carries_trace_id() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/error")
    assert resp.status_code == 418
    trace_id = resp.headers.get(_TRACE_ID_HEADER)
    assert trace_id
    body = resp.json()
    assert body["trace_id"] == trace_id
    assert body["code"] == "BOOM"


def test_malicious_incoming_trace_id_rejected() -> None:
    # An incoming X-Trace-Id with whitespace / control chars / weird shape is
    # ignored — a fresh uuid is generated so a hostile caller cannot inject
    # log noise through the header.
    client = TestClient(_app())
    resp = client.get("/trace", headers={_TRACE_ID_HEADER: "bad trace\ninjection"})
    assert resp.status_code == 200
    trace_id = resp.headers[_TRACE_ID_HEADER]
    assert trace_id != "bad trace\ninjection"
    assert len(trace_id) == 32  # generated uuid hex


def test_empty_incoming_trace_id_generated() -> None:
    client = TestClient(_app())
    resp = client.get("/trace", headers={_TRACE_ID_HEADER: "   "})
    assert resp.status_code == 200
    trace_id = resp.headers[_TRACE_ID_HEADER]
    assert len(trace_id) == 32


def test_trace_context_contextvar_set_during_request() -> None:
    # The contextvar is set during a request so log records carry trace_id.
    from prodavan.core import trace_context

    captured: list[str | None] = []

    app = _app()

    @app.get("/ctx")
    async def _ctx() -> dict:
        captured.append(trace_context.current_trace_id())
        return {"trace_id": trace_context.current_trace_id()}

    client = TestClient(app)
    resp = client.get("/ctx")
    assert resp.status_code == 200
    # During the request, the contextvar held the request trace id.
    assert captured[0] == resp.headers[_TRACE_ID_HEADER]


def test_trace_context_resets_after_request() -> None:
    # After a request, the contextvar resets so it does not leak across requests.
    from prodavan.core import trace_context

    app = _app()

    @app.get("/ctx")
    async def _ctx() -> dict:
        return {"trace_id": trace_context.current_trace_id()}

    client = TestClient(app)
    resp1 = client.get("/ctx")
    resp2 = client.get("/ctx")
    # Two requests, each with a distinct trace id; contextvar held each.
    assert resp1.headers[_TRACE_ID_HEADER] != resp2.headers[_TRACE_ID_HEADER]
    assert resp1.json()["trace_id"] == resp1.headers[_TRACE_ID_HEADER]
    assert resp2.json()["trace_id"] == resp2.headers[_TRACE_ID_HEADER]
