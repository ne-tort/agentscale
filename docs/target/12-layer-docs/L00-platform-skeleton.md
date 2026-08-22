# L00 — Platform skeleton

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 1 |
| Quality note | Stub health/empty UI есть; DoD L00 не начат → потолок прототипа |
| Plan | [L00](../11-implementation-plan/L00-platform-skeleton.md) |
| Canon | [STUB](../../../STUB.md), [AGENTS](../../../AGENTS.md) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Фундамент репозитория приложений: процесс API, цепочка Alembic, каркас Flutter, единый error envelope, CI smoke.  
**Не** identity, не домен кабинетов, не agent. Точка, с которой остальные слои добавляют модули, не таща legacy.

## Что сделано

| Сделано | Не сделано |
|---------|------------|
| Stub health / empty UI (исторический stub) | Чистый layout модулей под L01+ по DoD L00 |
| | Versioned error envelope + contract tests |
| | CI migrate+health+analyze как gate слоя |

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-API-HEALTH | `GET /api/v1/health` + error envelope | partial (stub health) |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| — | — | — |

## Связи

Все слои опираются на L00. Обратных доменных зависимостей нет.

## Инварианты

- Нет password-login / HS256 issuer / procurement domain в skeleton.
- Миграции не воскрешают legacy pack tables «для удобства».

## Карта кода

```text
apps/api/                     # FastAPI (или актуальный) stub
apps/flutter/                 # Flutter stub home
# alembic / migrations — уточнить путь при реализации L00
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L00 целиком | todo | см. план L00 |

## Проверка

```text
# после реализации L00:
# migrate + health + flutter analyze в CI
```

## Оценка качества

Рубрика: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 0 | DoD не закрыт |
| B. Контракты | 1 | stub health only |
| C. Инварианты и проверки | 0 | нет gate слоя |
| D. As-built ясность | 1 | семантика есть, факта реализации мало |
| **Quality (итог)** | **1** | заготовка / stub |
