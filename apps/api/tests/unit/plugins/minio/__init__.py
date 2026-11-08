"""Маркер пакета unit-тестов MinIO-адаптеров плагинов.

Тесты НАПИСАНЫ, НЕ ЗАПУСКАЮТСЯ: verification freeze (план миграции §0.2,
архитектура v4 §11). Ни одного обращения к реальному MinIO / сети / БД —
все клиенты in-memory fakes из conftest.py.
"""

from __future__ import annotations

__all__: list[str] = []
