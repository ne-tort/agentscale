# M05 — Интеграции (веб-магазины, S4B, rate limits)

Модуль управляет **внешними источниками цен и наличия** на уровне **кабинета** (cabinet): allowlist веб-магазинов, доверенные продавцы S4B, лимиты запросов. Не содержит логики агента и MCP-реестра — только политики и адаптеры.

## Назначение

| Задача | Решение в M05 |
| --- | --- |
| Какие витрины доступны агенту | Allowlist `web_shops` per cabinet |
| Кому доверять в S4B | Trusted sellers + фильтр «только электроника» |
| Защита от DDoS и перерасхода | Rate limits per cabinet / per integration |
| Аудит обращений | Запись в `integration_calls` → M09 |

## Границы модуля

**Входит:**

- CRUD политик интеграций кабинета
- Адаптеры: `web_shop.*`, `s4b.*` (вызываются через MCP M06)
- Rate limiter (Redis / in-memory fallback)
- Валидация категории «электроника» для S4B

**Не входит:**

- Регистрация MCP-серверов (M06)
- Хранение офферов и прогонов (M04 pipeline / commerce.sqlite)
- JWT и RBAC (M08) — M05 только проверяет права через middleware

## Зависимости

```text
M08-tenants (cabinet_id, RBAC)
    ↓
M05-integrations (политики)
    ↓
M06-mcp (catalog.*, s4b.* tools)
    ↓
M07-agent (агент вызывает tools)
```

## Артефакты документации

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | Сущности, инварианты, JSON-схемы |
| [api.md](api.md) | REST API политик и статусов |
| [persistence.md](persistence.md) | Таблицы PostgreSQL |
| [storage.md](storage.md) | Кэши S4B, файлы прайсов |
| [mcp-tools.md](mcp-tools.md) | Обёртки tools для агента |
| [ui.md](ui.md) | Экран «Интеграции» |
| [security.md](security.md) | Секреты API, изоляция |
| [checklist-implementation.md](checklist-implementation.md) | Чеклист разработки (1–10) |
| [checklist-review.md](checklist-review.md) | Чеклист ревью (1–10) |

## Ключевые инварианты

1. **Нет глобального allowlist** — только per cabinet.
2. **S4B «под заказ»** (`on_order`, `listNoStock`) не попадает в офферы.
3. **Trusted seller** влияет на ранжирование (M04), но список хранится в M05.
4. **Rate limit** срабатывает до HTTP-запроса к внешнему API.
5. Категории вне «электроника» отсекаются на уровне адаптера S4B.

## Статус

Спецификация. Реализация — фаза после M08 (tenants) и M06 (MCP registry).
