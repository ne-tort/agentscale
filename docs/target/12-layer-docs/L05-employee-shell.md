# L05 — Employee shell + cabinet entry

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L05](../11-implementation-plan/L05-employee-shell.md) |
| Canon | [04-employees](../04-employees/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Контур сотрудника: cabinets → DynamicCabinetShell (meta L06). Create Base / import bundle. Peers isolated.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-EMP-SHELL | enter cabinet + shell host | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-PRINCIPAL / C-HEADERS | L01 | planned |
| C-UI-* | L02 | planned |
| C-INSTANCE / C-META-DATA | L06 | planned |

## Связи

Host ← L06 meta; projects → L07.

## Инварианты

- Чужой `X-Cabinet-Id` → 403.
- Вкладки только из meta.tabs.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L05 | todo | |

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
