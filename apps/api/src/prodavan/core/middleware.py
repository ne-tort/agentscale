"""Middleware registration helpers (P0 core register pattern)."""

from __future__ import annotations

import re
import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from prodavan.application.pod_identity.bridge import peek_pod_bridge_token
from prodavan.config.settings import settings

# Deny-by-default for Bridge JWT (and any residual shared Bearer).
# Real agent surface is /projects/{id}/agent/... (not /api/v1/agent/...).
_POD_SURFACE_RE = re.compile(
    r"^/api/v1/(?:internal/pods(?:/|$)|projects/[^/]+/(?:infra|modules|agent)(?:/|$))"
)


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


def register_cors(app: FastAPI, *, allow_origins: list[str]) -> None:
    """Register CORS in one place (main.py must not scatter add_middleware)."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_pod_surface_allowlist(app: FastAPI) -> None:
    app.add_middleware(PodSurfaceAllowlistMiddleware)
