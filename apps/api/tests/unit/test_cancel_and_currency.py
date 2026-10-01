"""Regression tests: stop-with-pod-runtime + currency detection chain."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.modules import equipment_catalog_search as cs
from prodavan.config.settings import settings


def _session_row(api_kind: str = "openai_api") -> Any:
    row = MagicMock()
    row.api_kind = api_kind
    row.status = "active"
    row.vendor_agent_id = "ags_1"
    row.provider = "openai"
    row.cwd = "/tmp"
    row.model = "gpt"
    row.project_id = "proj_1"
    return row


@pytest.mark.asyncio
async def test_cancel_with_pod_runtime_does_not_501(monkeypatch) -> None:
    """Stopping a chat must not 501 when pod runtime owns the session."""
    monkeypatch.setattr(settings, "pod_agent_runtime_enabled", True, raising=False)
    monkeypatch.setattr(settings, "agent_inprocess_adapters_enabled", False, raising=False)

    svc = AgentSessionService(MagicMock())
    row = _session_row()

    async def fake_get(**kwargs: Any) -> Any:  # noqa: ANN401
        return row

    projects = MagicMock()
    projects.require_access = AsyncMock()
    monkeypatch.setattr(svc, "get_session", fake_get)
    monkeypatch.setattr(svc, "_projects", projects)
    monkeypatch.setattr(svc._session, "commit", AsyncMock())
    monkeypatch.setattr(svc._session, "refresh", AsyncMock())

    # The bug: get_agent_adapter raised agent_adapter_disabled (501) here.
    out = await svc.cancel_session(
        project_id="proj_1",
        session_id="ags_1",
        principal=MagicMock(),
        employee=MagicMock(),
    )
    assert out is not None
    assert row.status == "cancelled"


def test_detect_currency_value_variants() -> None:
    assert cs.detect_currency_value("USD") == "USD"
    assert cs.detect_currency_value("$") == "USD"
    assert cs.detect_currency_value("долл") == "USD"
    assert cs.detect_currency_value("у.е.") == "USD"
    assert cs.detect_currency_value("EUR") == "EUR"
    assert cs.detect_currency_value("евро") == "EUR"
    assert cs.detect_currency_value("руб") == "RUB"
    assert cs.detect_currency_value("Р") == "RUB"
    assert cs.detect_currency_value("") == ""
    assert cs.detect_currency_value("abc") == ""


def test_apply_column_map_heals_unmapped_currency() -> None:
    """Legacy column map without currency: header synonyms feed the value."""
    cmap = {"title": "Наименование", "price": "Цена"}
    raw = {
        "Наименование": "SSD",
        "Цена": "100",
        "Валюта": "долл",
        "Поставщик": "OCS",
    }
    out = cs.apply_column_map(raw, cmap)
    assert out["title"] == "SSD"
    assert out["currency"] == "USD"


def test_apply_column_map_mapped_currency_normalized() -> None:
    cmap = {"title": "t", "price": "p", "currency": "cur"}
    raw = {"t": "SSD", "p": "1", "cur": "$"}
    out = cs.apply_column_map(raw, cmap)
    assert out["currency"] == "USD"
