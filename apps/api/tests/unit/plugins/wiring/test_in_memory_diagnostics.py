"""Диагностика in-memory портов композиции (``plugin_in_memory_ports``).

Урок аудита: список ``in_memory`` объявлялся и публиковался в ``app.state``,
но нигде не наполнялся, поэтому ключ ВСЕГДА был пустым, а проверки
«нет ли in-memory подмены» проходили вхолостую. Здесь фиксируется, что
обнаружение идёт по фактическому классу привязанного порта.
"""

from __future__ import annotations

import inspect
from typing import Any

from prodavan.application.plugins.wiring import install


class InMemoryQuotaStub:
    """Минимальная заглушка: имя класса повторяет продовый in-memory порт."""


class SqlQuotaStub:
    """Долговременный порт: имя класса НЕ означает in-memory."""


def test_in_memory_ports_are_detected_by_actual_class() -> None:
    names = install._in_memory_port_names({
        "grant_store": InMemoryQuotaStub(),
        "quota": InMemoryQuotaStub(),
        "audit": SqlQuotaStub(),
        "artifact_store": None,
    })
    assert names == ["grant_store", "quota"]


def test_durable_ports_are_never_reported_as_in_memory() -> None:
    names = install._in_memory_port_names({
        "grant_store": SqlQuotaStub(),
        "quota": SqlQuotaStub(),
        "audit": SqlQuotaStub(),
    })
    assert names == []


def test_composition_actually_feeds_the_in_memory_diagnostic() -> None:
    """Список обязан наполняться из реальных портов, а не оставаться пустым."""
    source = inspect.getsource(install.install_plugin_platform)
    assert "_in_memory_port_names(" in source
    assert "in_memory.extend(" in source
    assert "IN_MEMORY_PORTS_STATE_KEY" in source
