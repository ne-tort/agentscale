"""Unit tests for ProviderResolver catalog_entry_id priority + pod probe helpers (PROBE-P3)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from prodavan.application.ai_keys.probe.pod_probe_service import _normalize_models
from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver
from prodavan.config.settings import settings
from prodavan.domain.ai_keys import ApiKind, ProbeStatus


def _catalog_row(
    rid: str,
    *,
    api_kind: str = "custom",
    agent_provider: str = "codex",
    base_url: str = "http://example/v1",
    supports_models_list: bool = True,
    auth_scheme: str = "bearer",
):
    """Build a ReferenceCatalogEntryRow-like mock with a `.payload` dict."""
    row = MagicMock()
    row.id = rid
    row.title = rid
    row.sort_order = 0
    row.payload = {
        "api_kind": api_kind,
        "agent_provider": agent_provider,
        "base_url": base_url,
        "auth_scheme": auth_scheme,
        "chat_completions_path": "/v1/chat/completions",
        "models_path": "/v1/models",
        "openai_compatible": True,
        "supports_models_list": supports_models_list,
    }
    return row


class _FakeSession:
    """Minimal AsyncSession that returns a canned catalog entry list via .execute()."""

    def __init__(self, rows: list) -> None:
        result = MagicMock()
        scalars = MagicMock()
        scalars.all = lambda: rows
        result.scalars.return_value = scalars
        result.all = lambda: rows
        self._result = result

    async def execute(self, *_args, **_kwargs):
        return self._result


def test_resolver_prefers_catalog_entry_id_over_legacy_match() -> None:
    """Regression: a custom+codex key must resolve to its explicit catalog entry,
    not the first custom+codex match (ollama vs a user-added cheapai endpoint)."""
    ollama = _catalog_row("ollama", base_url="http://127.0.0.1:11434/v1")
    cheapai = _catalog_row("cheapai", base_url="https://cheapai.lol")
    session = _FakeSession([ollama, cheapai])
    resolver = ProviderResolver(session)  # type: ignore[arg-type]

    async def run() -> None:
        # Without catalog_entry_id → legacy match returns ollama (the regression).
        legacy = await resolver.resolve(
            api_kind="custom",
            provider="codex",
            secret="sk-abc",
        )
        assert legacy is not None
        assert legacy.catalog_id == "ollama"

        # With catalog_entry_id → explicit entry wins even though api_kind+provider
        # would otherwise match ollama first.
        explicit = await resolver.resolve(
            api_kind="custom",
            provider="codex",
            secret="sk-abc",
            catalog_entry_id="cheapai",
        )
        assert explicit is not None
        assert explicit.catalog_id == "cheapai"
        assert explicit.base_url == "https://cheapai.lol"

    asyncio.run(run())


def test_resolver_falls_back_when_catalog_entry_id_unknown() -> None:
    ollama = _catalog_row("ollama")
    session = _FakeSession([ollama])
    resolver = ProviderResolver(session)  # type: ignore[arg-type]

    async def run() -> None:
        res = await resolver.resolve(
            api_kind="custom",
            provider="codex",
            catalog_entry_id="does-not-exist",
        )
        # Falls back to legacy api_kind+provider match → ollama.
        assert res is not None
        assert res.catalog_id == "ollama"

    asyncio.run(run())


def test_normalize_models_pod_probe_shapes() -> None:
    # OpenAI-style {data:[...]}
    assert _normalize_models({"data": [{"id": "gpt-5.1"}, {"id": "claude-opus-4.8"}]}) == [
        "gpt-5.1",
        "claude-opus-4.8",
    ]
    # {models:[...]}
    assert _normalize_models({"models": ["a", "b", "a"]}) == ["a", "b"]
    # plain list
    assert _normalize_models(["x", "y"]) == ["x", "y"]
    # object items with name
    assert _normalize_models({"models": [{"name": "z"}]}) == ["z"]
    # empty / weird
    assert _normalize_models(None) == []
    assert _normalize_models({"unexpected": 1}) == []
    assert _normalize_models({"models": "not-a-list"}) == []


def test_pod_probe_disabled_returns_unavailable() -> None:
    """When pod_probe_enabled is False, ProbePodService returns UNAVAILABLE with
    POD_PROBE_DISABLED so the caller can fall back to http_probe."""
    from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

    row = MagicMock()
    row.api_kind = ApiKind.OPENAI_API
    row.provider = "codex"

    original = settings.pod_probe_enabled
    settings.pod_probe_enabled = False
    try:
        svc = ProbePodService(MagicMock())  # type: ignore[arg-type]
        result = asyncio.run(svc.probe_key(row))  # type: ignore[arg-type]
        assert result.status == ProbeStatus.UNAVAILABLE
        assert result.error_code == "POD_PROBE_DISABLED"
    finally:
        settings.pod_probe_enabled = original


def test_pod_probe_cli_subscription_unavailable() -> None:
    from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

    row = MagicMock()
    row.api_kind = ApiKind.CLI_SUBSCRIPTION
    row.provider = "cursor"

    original = settings.pod_probe_enabled
    settings.pod_probe_enabled = True
    try:
        svc = ProbePodService(MagicMock())  # type: ignore[arg-type]
        result = asyncio.run(svc.probe_key(row))  # type: ignore[arg-type]
        assert result.status == ProbeStatus.UNAVAILABLE
        assert result.error_code == "CLI_SUBSCRIPTION_NOT_PROBEABLE"
    finally:
        settings.pod_probe_enabled = original


def test_pod_probe_push_unreachable_returns_probe_pod_unreachable() -> None:
    """If the probe pod does not accept the lease, the result is UNAVAILABLE
    with PROBE_POD_UNREACHABLE so the caller falls back to http_probe."""
    from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

    row = MagicMock()
    row.id = "aik_x"
    row.api_kind = ApiKind.OPENAI_API
    row.provider = "codex"
    row.secret_ref = "vault://ai_keys/aik_x"
    row.catalog_entry_id = None

    secrets = MagicMock()
    secrets.get = MagicMock(return_value="sk-test")

    # Session must behave like AsyncSession (await .execute()). Resolver reads
    # catalog entries from it; return an empty list so resolve() yields None
    # (probe_key still proceeds to push the lease before fetching models).
    session = _FakeSession([])

    # http client whose post returns 503 (pod down). Must be async-context-manager.
    push_response = MagicMock()
    push_response.status_code = 503
    push_response.text = "pod down"

    client_cm = AsyncMock()
    client_cm.post = AsyncMock(return_value=push_response)
    client_cm.delete = AsyncMock(return_value=MagicMock(status_code=204))
    client_cm.__aenter__ = AsyncMock(return_value=client_cm)
    client_cm.__aexit__ = AsyncMock(return_value=None)

    client_cls = MagicMock(return_value=client_cm)

    original = settings.pod_probe_enabled
    settings.pod_probe_enabled = True
    try:
        svc = ProbePodService(session, secrets=secrets, http_client=client_cls)  # type: ignore[arg-type]
        result = asyncio.run(svc.probe_key(row))  # type: ignore[arg-type]
        assert result.status == ProbeStatus.UNAVAILABLE
        assert result.error_code == "PROBE_POD_UNREACHABLE"
    finally:
        settings.pod_probe_enabled = original
