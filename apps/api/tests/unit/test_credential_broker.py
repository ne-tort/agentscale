"""Unit tests for AgentCredentialBroker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


@pytest.mark.asyncio
async def test_push_lease_skips_when_runtime_disabled() -> None:
    broker = AgentCredentialBroker(MagicMock())
    with patch("prodavan.application.agent.credential_broker.settings") as mock_settings:
        mock_settings.pod_agent_runtime_enabled = False
        ok = await broker.push_lease_to_runtime(project_id="prj_1", key_id="key_1")
    assert ok is False


@pytest.mark.asyncio
async def test_push_lease_posts_to_runtime() -> None:
    session = MagicMock()
    project = MagicMock()
    session.get = AsyncMock(return_value=project)

    q = MagicMock()
    q.first.return_value = ("pod-wk-demo", None)
    session.execute = AsyncMock(return_value=q)

    keys = MagicMock()
    keys.require_key_available_for_project = AsyncMock()
    keys.resolve_secret_for_key = AsyncMock(return_value="sk-test")

    k8s = MagicMock()
    k8s.get_pod = AsyncMock(
        return_value=PodSnapshot(
            name="pod-wk-demo",
            uid="u1",
            phase="Running",
            restarts=0,
            ready=True,
            labels={},
            pod_ip="10.42.0.88",
        ),
    )

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.text = ""

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.post = AsyncMock(return_value=mock_response)

    broker = AgentCredentialBroker(session, k8s_client=k8s, http_client=lambda **_: mock_http)
    broker._keys = keys

    with patch("prodavan.application.agent.credential_broker.settings") as mock_settings:
        mock_settings.pod_agent_runtime_enabled = True
        mock_settings.pod_agent_runtime_port = 3921
        mock_settings.pod_agent_runtime_token = "rt-token"
        ok = await broker.push_lease_to_runtime(project_id="prj_1", key_id="key_abc")

    assert ok is True
    call = mock_http.post.await_args
    assert call.args[0] == "http://10.42.0.88:3921/v1/credentials/leases"
    assert call.kwargs["json"]["key_id"] == "key_abc"
    assert call.kwargs["json"]["secret"] == "sk-test"
    assert call.kwargs["headers"]["Authorization"] == "Bearer rt-token"
