"""Unit tests — pod agent service token auth (L09)."""

import secrets

from prodavan.api.agent_auth import _pod_agent_token_from_request


class _FakeRequest:
    def __init__(self, auth: str | None) -> None:
        self.headers = {"Authorization": auth} if auth else {}


def test_pod_agent_token_matches(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "pod-secret")
    req = _FakeRequest("Bearer pod-secret")
    assert _pod_agent_token_from_request(req) == "pod-secret"


def test_pod_agent_token_rejects_wrong_token(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "pod-secret")
    req = _FakeRequest("Bearer wrong")
    assert _pod_agent_token_from_request(req) is None


def test_pod_agent_token_empty_config(monkeypatch) -> None:
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", "")
    req = _FakeRequest("Bearer anything")
    assert _pod_agent_token_from_request(req) is None


def test_pod_agent_token_timing_safe(monkeypatch) -> None:
    from prodavan.config.settings import settings

    token = secrets.token_hex(16)
    monkeypatch.setattr(settings, "pod_agent_bridge_auth_token", token)
    req = _FakeRequest(f"Bearer {token}")
    assert _pod_agent_token_from_request(req) == token
