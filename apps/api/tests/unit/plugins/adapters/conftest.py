"""Общие фикстуры тестов адаптеров (проходят в текущем прогоне)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.objects.service import PluginObjectStore

from .fakes import FakeBucketAdmin, FakeObjectAudit

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    """Детерминированные часы (не реальное время)."""

    return NOW


@pytest.fixture
def object_store() -> PluginObjectStore:
    """Настоящий ``PluginObjectStore`` поверх in-memory BucketAdminPort."""

    return PluginObjectStore(
        buckets=FakeBucketAdmin(),
        audit=FakeObjectAudit(),
        quota_bytes=1024 * 1024 * 1024,
    )
