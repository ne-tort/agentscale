# 03 — Cabinet overview

Страница по logo / overview index 0.

## Данные

- `GET /cabinets/{id}/metrics` — scoped метрики кабинета

## Метрики (без модулей)

- сотрудники кабинета (assignments), онлайн
- проекты
- токены агентов, сообщения
- storage workspace проектов
- last activity (optional)

## UI

- `CabinetMetricsWrap` / `StatTile` по образцу `CompanyOverviewPage`
- `AppAutoRefreshBinder` для фонового обновления
- Без редактирования кабинета (rename — company admin)
