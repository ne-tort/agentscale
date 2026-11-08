"""Общие фикстуры тестов PlatformAdmin-адаптеров.

Намеренно НЕ импортируем ``prodavan.main.create_app``: unit-тесты проверяют
``app.state`` на структурном ``FakeApp`` без БД и сети (правило freeze).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class FakeApp:
    """Минимальное приложение с ``.state`` (как у FastAPI/Starlette)."""

    def __init__(self) -> None:
        self.state = SimpleNamespace()


class FakeRequest:
    """Минимальный ``Request`` с ``app`` для ``require_admin_runtime``."""

    def __init__(self, app: FakeApp) -> None:
        self.app = app


@pytest.fixture
def fake_app() -> FakeApp:
    return FakeApp()


@pytest.fixture
def fake_request(fake_app: FakeApp) -> FakeRequest:
    return FakeRequest(fake_app)
