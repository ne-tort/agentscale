"""Общие фикстуры: NP gen + эталонные манифесты задачи 14.

Тесты проходят в текущем прогоне (pytest tests/unit/plugins).
"""

from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.egress.generator import EgressConfigGenerator, EgressScope

from .helpers import MANIFEST_AGENT, MANIFEST_PLUGIN, REPO_ROOT, generated_np  # noqa: F401


@pytest.fixture
def plugin_np() -> dict[str, Any]:
    """Реальный вывод `build_network_policy('plugin')` задачи 08."""
    return generated_np("plugin")


@pytest.fixture
def agent_np() -> dict[str, Any]:
    """Реальный вывод `build_network_policy('agent')` задачи 08."""
    return generated_np("agent")


@pytest.fixture
def gen() -> EgressConfigGenerator:
    return EgressConfigGenerator()
