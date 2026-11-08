"""Unit-тесты объектной capability плагинов (v4 §5.4, §11 кр. 19, §4.5 кр. 11).

Тесты НАПИСАНЫ, НЕ ЗАПУСКАЮТСЯ: verification freeze (план миграции §0.2,
архитектура §9). Ни одного обращения к реальному MinIO / БД / сети; все порты —
in-memory fakes, время — фиксированный clock.
"""

from __future__ import annotations
