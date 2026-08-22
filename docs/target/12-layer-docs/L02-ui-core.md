# L02 — UI mobile core

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L02](../11-implementation-plan/L02-ui-core.md) |
| Canon | [07-ui-mobile-core](../07-ui-mobile-core/) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Единственный набор управляющих виджетов и UX-паттернов платформы.  
Feature-контуры **собирают** экраны из core. Mobile-first, без модалок для выбора сущностей; `AppSelectorPage` / `AppEntityCollection`.

**Не** доменные экраны кабинетов; не бизнес-логика API.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-UI-COLLECTION | AppEntityCollection | planned |
| C-UI-SELECTOR | AppSelectorPage | planned |
| C-UI-CONFIRM | DangerConfirmPage | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| — | L00 Flutter layout | planned |

## Связи

Потребители: L04, L05, L06 interpreters, L07/L09. Обратных зависимостей на API нет.

## Инварианты

- Нет AlertDialog / BottomSheet / entity Dropdown в features для выбора сущностей.
- Новые контролы сначала в `lib/core`.
- EntityCollection: один каркас list|table.

## Карта кода

```text
apps/flutter/lib/core/
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Principles 07 §1–6 | todo | |

## Проверка

```text
# gallery / widget tests — после поставки
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
