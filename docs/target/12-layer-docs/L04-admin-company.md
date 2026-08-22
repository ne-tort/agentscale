# L04 — Admin + Company control plane

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L04](../11-implementation-plan/L04-admin-company.md) |
| Canon | [01-platform-admin](../01-platform-admin/), [03-companies](../03-companies/) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Platform Admin — компании, keys, квоты/policy, metrics. Company — invite/disable сотрудников, org-вид кабинетов; без static `profile_id` grants.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-ADMIN-COMPANY | Company CRUD, policy, key bind | planned |
| C-QUOTA | CompanyCabinetQuota | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-PRINCIPAL / C-INVITE | L01 | planned |
| C-UI-* | L02 | planned |
| C-KEY-ENTITY | L03 | planned |

## Связи

Квоты → L06; policy → L08; metrics ← L08 usage.

## Инварианты

- Нет static cabinet grants UX; нет password в invite.
- Org cabinets list — metadata only.

## Карта кода

```text
—
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| UX contracts 01/03 | todo | |

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
