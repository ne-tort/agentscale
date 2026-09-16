"""Middleware registration helpers (P0 core register pattern)."""

from __future__ import annotations

import logging
import re
import secrets
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from prodavan.application.pod_identity.bridge import peek_pod_bridge_token
from prodavan.config.settings import settings
from prodavan.core.trace_context import set_trace_id

logger = logging.getLogger(__name__)

# Request-scoped trace id (audit XCUT-P2a). Generated per request when absent,
# propagated to request.state + response header + structured log context. The
# trace id ties an HTTP request to downstream agent/pod/event flows for
# observability without a separate distributed-tracing backend.
_TRACE_ID_HEADER = "X-Trace-Id"
# Accept caller-supplied trace ids that look like a uuid hex, a short token,
# or a dotted dotted name — reject anything with whitespace / control chars /
# unreasonable length so a hostile caller cannot inject log noise.
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")

# Deny-by-default for Bridge JWT (and any residual shared Bearer).
# Real agent surface is /projects/{id}/agent/... (not /api/v1/agent/...).
_POD_SURFACE_RE = re.compile(
    r"^/api/v1/(?:internal/pods(?:/|$)|projects/[^/]+/(?:infra|modules|agent)(?:/|$))"
)

# Explicit CORS methods/headers (audit API-P1b). Wildcards with
# allow_credentials=True were a credential-leak risk; the API uses Bearer JWT
# (no cookie auth by default), so credentials are opt-in and the headers list
# is the exact set the client + bridge send.
_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Cabinet-Id",
    "X-Project-Id",
    "X-Prodavan-Session-Id",
    "X-Prodavan-Signature",
    "X-Prodavan-Events-Owner",
    # X-Trace-Id is both a request header (caller propagates) and a response
    # header (server returns) — listed in allow_headers and expose_headers.
    "X-Trace-Id",
]
# Response headers exposed to cross-origin callers (audit XCUT-P2a). Without
# this, a browser SPA cannot read X-Trace-Id from the response to quote it
# when debugging a 4xx/5xx.
_CORS_EXPOSE_HEADERS = ["X-Trace-Id"]


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    return token or None


def _is_shared_pod_token(token: str) -> bool:
    expected = (settings.pod_agent_bridge_auth_token or "").strip()
    if not expected or not token:
        return False
    try:
        return secrets.compare_digest(token, expected)
    except ValueError:
        return False


def _is_pod_bridge_token(token: str) -> bool:
    peeked = peek_pod_bridge_token(token)
    return bool(peeked and peeked.get("typ") == "pod_bridge")


class PodSurfaceAllowlistMiddleware(BaseHTTPMiddleware):
    """If request uses pod credentials, allow only Pod API surface paths."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        token = _bearer(request)
        if token and (_is_shared_pod_token(token) or _is_pod_bridge_token(token)):
            path = request.url.path or ""
            if path.startswith("/health"):
                return await call_next(request)
            if not _POD_SURFACE_RE.match(path):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "FORBIDDEN",
                        "title": "Forbidden",
                        "detail": "pod credentials limited to Pod API surface",
                    },
                )
        return await call_next(request)


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assign a per-request trace id (audit XCUT-P2a).

    Reuses an incoming ``X-Trace-Id`` when present (and well-formed) so
    callers can propagate a correlation id, and generates one otherwise. The
    id is exposed on ``request.state.trace_id``, bound to the structured log
    context via ``core.trace_context`` (so every log record carries
    ``trace_id``), and returned as ``X-Trace-Id`` on the response.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = (request.headers.get(_TRACE_ID_HEADER) or "").strip()
        trace_id = incoming if (incoming and _TRACE_ID_RE.match(incoming)) else uuid.uuid4().hex
        request.state.trace_id = trace_id
        token = set_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            # Contextvar reset is scoped to this request's async context.
            from prodavan.core.trace_context import reset_trace_id

            reset_trace_id(token)
        response.headers[_TRACE_ID_HEADER] = trace_id
        return response


def register_cors(app: FastAPI, *, allow_origins: list[str]) -> None:
    """Register CORS in one place (main.py must not scatter add_middleware).

    Audit API-P1b: explicit methods/headers instead of wildcards; credentials
    are opt-in (default False — auth is Bearer JWT, no cookie flow). A wildcard
    ``*`` origin is rejected when credentials are enabled or
    ``cors_forbid_wildcard_origin`` is set, so a misconfigured prod origins
    string cannot open the API to arbitrary origins with credentials.
    """
    origins = list(allow_origins)
    allow_credentials = bool(settings.cors_allow_credentials)
    forbid_wildcard = bool(settings.cors_forbid_wildcard_origin) or allow_credentials
    if forbid_wildcard and "*" in origins:
        # With credentials, a wildcard origin is rejected by browsers anyway, but
        # fail loudly at startup so prod misconfiguration is caught early.
        raise RuntimeError(
            "CORS wildcard origin '*' is forbidden when credentials are enabled "
            "or cors_forbid_wildcard_origin is set; configure explicit origins"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=_CORS_METHODS,
        allow_headers=_CORS_HEADERS,
        expose_headers=_CORS_EXPOSE_HEADERS,
    )


def register_pod_surface_allowlist(app: FastAPI) -> None:
    app.add_middleware(PodSurfaceAllowlistMiddleware)


def register_trace_id(app: FastAPI) -> None:
    """Register trace id middleware (audit XCUT-P2a)."""
    app.add_middleware(TraceIdMiddleware)
