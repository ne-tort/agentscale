# L09 — Vertical integration

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L09](../11-implementation-plan/L09-vertical-integration.md) |
| Canon | [09-gap-map](../09-gap-map.md), [00-principles](../00-principles.md) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Сшивка слоёв: E2E, metrics e2e, release gate. Не место для недоделанного DoD L01…L08.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| E2E smoke suite | Admin→…→Agent | planned |
| Release gate | checklist-master | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| все C-* live | L01…L08 | planned |

## Связи

Замыкает [map.md](map.md).

## Инварианты

- Нет bypass auth в зелёном E2E.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L09 | todo | ждёт L01…L08 |

## Проверка

```text
—
```

## Оценка качества

Рубрика: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 0 | |
| B. Контракты | 0 | |
| C. Инварианты и проверки | 0 | |
| D. As-built ясность | 1 | карточка-заготовка |
| **Quality (итог)** | **0** | not_started |
