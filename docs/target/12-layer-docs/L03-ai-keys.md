# L03 — AI Provider Keys

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L03](../11-implementation-plan/L03-ai-keys.md) |
| Canon | [02-ai-provider-keys](../02-ai-provider-keys/) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Инвентарь credentials для agent backends. Секрет только `secret_ref`. `resolve_credentials` — единственный runtime-путь. `cli_subscription` не credential.

**Не** IdP; не cabinet MCP.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-KEY-ENTITY | AiProviderKey API (no secret) | planned |
| C-KEY-RESOLVE | resolve_credentials | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-MEMBERSHIP | L01 (`company_id`) | planned |

## Связи

L04 управляет ключами; L08 только resolve.

## Инварианты

- Secret не в API response / логах.
- Resolve не отдаёт `cli_subscription` как credential.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L03 | todo | |

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
