# L08 — Agent providers

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L08](../11-implementation-plan/L08-agent-providers.md) |
| Canon | [08-agent-providers](../08-agent-providers/) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

AgentProviderPort + AgentEvent; adapters; credentials из L03; usage → metrics.

**Не** GLM/OpenClaw; не Telegram Commerce bot.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-AGENT-PORT | AgentProviderPort | planned |
| C-AGENT-EVENT | frozen AgentEvent | planned |
| C-USAGE | usage records | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-KEY-RESOLVE | L03 | planned |
| C-PROJECT | L07 | planned |
| C-CABINET-MCP | L06 | planned |
| C-ADMIN-COMPANY policy | L04 | planned |

## Связи

L09 chat ← stream; HITL → L02; metrics → L04.

## Инварианты

- `cli_subscription` не в apiKey; MCP = materialize ∩ policy.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L08 | todo | |

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
