"""Unit tests — AI model resolution service."""

from unittest.mock import AsyncMock

import pytest

from prodavan.application.ai_models.resolution import AiModelResolutionService, resolve_ui_default


def test_resolve_ui_default_prefers_catalog_when_in_effective() -> None:
    assert resolve_ui_default(["gpt-5", "default"], "gpt-5") == "gpt-5"


@pytest.mark.asyncio
async def test_resolve_send_default_delegates_to_live_service(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    svc = AiModelResolutionService(session)

    class _FakeLive:
        list_live_for_key = AsyncMock(return_value={"default_model": "composer-2.5", "models": []})

    monkeypatch.setattr(
        "prodavan.application.ai_models.live_service.AiModelsLiveService",
        lambda _session: _FakeLive(),
    )
    out = await svc.resolve_send_default(
        company_id="co_1",
        key_id="key_1",
        project_id="proj_1",
    )
    assert out == "composer-2.5"
    _FakeLive.list_live_for_key.assert_awaited_once_with(
        company_id="co_1",
        key_id="key_1",
        project_id="proj_1",
    )
