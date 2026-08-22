# L07 — Projects & runtime (container)

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L07](../11-implementation-plan/L07-projects-runtime.md) |
| Canon | [06-projects-runtime](../06-projects-runtime/), [workspace-context](../08-agent-providers/workspace-context.md) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Project = workspace + container lifecycle; materialize из cabinet; triggers; attachments.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-PROJECT | Project entity + lifecycle | planned |
| C-MATERIALIZE | FS layout result | planned |
| C-TRIGGERS | trigger ingress | planned |
| C-ATTACH | attachment_refs | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-INSTANCE / C-MCP-PKG | L06 | planned |
| C-HEADERS | L01 | planned |

## Связи

cwd → L08; chat → triggers (L09).

## Инварианты

- Project только в accessible cabinet; materialize идемпотентен.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L07 | todo | |

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
