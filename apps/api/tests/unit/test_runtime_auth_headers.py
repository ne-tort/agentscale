"""Unit — runtime auth headers (router-safe bridge token delivery)."""

from __future__ import annotations

from prodavan.application.agent.runtime_auth import (
    BRIDGE_TOKEN_HEADER,
    runtime_auth_headers,
)
from prodavan.config.settings import settings


def test_runtime_auth_headers_sends_both_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_token", "tok-1  ")
    headers = runtime_auth_headers()
    # Authorization keeps working for direct (non-router) addressing...
    assert headers["Authorization"] == "Bearer tok-1"
    # ...while the router-safe custom header survives the sandbox-router
    # (which strips Authorization before forwarding).
    assert headers[BRIDGE_TOKEN_HEADER] == "tok-1"


def test_runtime_auth_headers_empty_without_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pod_agent_runtime_token", "")
    assert runtime_auth_headers() == {}
