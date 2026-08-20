# M05 — UI

Экраны модуля интеграций в админке кабинета. Маршрут: `/cabinet/{id}/settings/integrations`.

## Навигация

```text
Настройки кабинета
  └── Интеграции
        ├── Обзор
        ├── S4B
        ├── Веб-магазины
        └── Лимиты запросов
```

## Экран «Обзор»

### Компоненты

- **Карточки статуса:** S4B (вкл/выкл, creds ok), Web shops (N активных)
- **Rate limit gauge:** текущие RPM / лимит
- **Последние ошибки:** 5 записей из `integration_call_log`

### Действия

| кнопка | роль | API |
| --- | --- | --- |
| «Проверить S4B» | `cabinet.admin` | POST `/s4b/test` |
| «Открыть логи» | `cabinet.viewer` | → M09 audit filter |

## Экран «S4B»

### Секции

1. **Переключатели**
   - S4B включён
   - Только доверенные продавцы
   - Только электроника

2. **Учётные данные**
   - Форма login/password (masked)
   - Кнопки: Сохранить, Удалить, Проверить

3. **Доверенные продавцы**
   - Таблица: ID S4B, название, вкл/выкл, удалить
   - Добавить: modal с `s4b_seller_id` + autocomplete имени (из API S4B search)

### UX-правила

- При выключении S4B — confirm dialog («агент потеряет доступ к S4B»)
- Password field — never pre-filled
- Badge «Под заказ не используется» — info tooltip

## Экран «Веб-магазины»

### Таблица allowlist

| колонка | описание |
| --- | --- |
| Магазин | display_name + shop_id |
| Статус | enabled toggle |
| Приоритет | drag-sort или number input |
| RPM | override или «по умолчанию» |
| Адаптер | healthy / degraded / down |

### Добавление магазина

Dropdown из **глобального справочника** адаптеров (не free text URL).

Недоступные в тарифе — disabled с tooltip «Обратитесь к администратору tenant».

## Экран «Лимиты запросов»

- Slider `default_rate_limit_rpm` (1–1000)
- Таблица per-shop overrides
- График потребления за 24h (M09 metrics)

## Состояния и ошибки

| состояние | UI |
| --- | --- |
| `S4B_CREDENTIALS_MISSING` | Banner warning на обзоре |
| `RATE_LIMIT_EXCEEDED` (live) | Toast «Лимит исчерпан, повтор через N сек» |
| 403 integration | Empty state «Интеграции отключены администратором» |

## Доступность

- Все toggles — keyboard accessible
- Цвет статуса дублируется текстом (не только green/red)
- Формы credentials — autocomplete off

## Mobile

Read-only обзор + toggles; редактирование creds — desktop recommended.

## Связь с агентом (M07)

В чате проекта badge «S4B ✓ | DNS, Ozon» — read-only, ссылка на экран интеграций.
