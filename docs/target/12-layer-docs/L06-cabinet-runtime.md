# L06 — Cabinet Runtime

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L06](../11-implementation-plan/L06-cabinet-runtime.md) |
| Canon | [05-cabinets](../05-cabinets/) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

CabinetInstance: schema-per-instance, meta→UI, `cabinet.*`, MCP packages, bundles. Ownership Employee+Company+Admin; peers isolated.

**Не** container (L07); не AgentPort (L08); не static pack.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-INSTANCE | Instance CRUD/ACL | planned |
| C-META-DATA | Meta + rows API | planned |
| C-CABINET-MCP | `cabinet.*` tools | planned |
| C-MCP-PKG | package deploy/bindings | planned |
| C-BUNDLE | export/import zip | planned |
| C-MATERIALIZE | materialize hook | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-MEMBERSHIP / ACL | L01 | planned |
| C-QUOTA | L04 | planned |
| C-UI-COLLECTION | L02 | planned |

## Связи

L05 host; L07 materialize; L08 cabinet MCP.

## Инварианты

- Нет cross-schema SQL; нет raw SQL MCP.
- Bundle import → новая schema.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L06 | todo | |

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
