# AI Provider Keys — API

Префикс: `/api/v1/admin/ai-keys`. Только `platform.admin`.

## Endpoints

| Method | Path | Описание |
|--------|------|----------|
| GET | `/admin/ai-keys` | Список (без секрета) |
| POST | `/admin/ai-keys` | Создать (body включает `secret` один раз) |
| GET | `/admin/ai-keys/{id}` | Detail + bindings |
| PATCH | `/admin/ai-keys/{id}` | Имя, цена, notes, status, next_renewal_at |
| POST | `/admin/ai-keys/{id}/renew` | `{ "months": 1..12 }` |
| POST | `/admin/ai-keys/{id}/rotate-secret` | Новый secret |
| PUT | `/admin/ai-keys/{id}/companies` | Заменить набор company ids |
| DELETE | `/admin/ai-keys/{id}` | Удалить (если политика позволяет) |

## Пример create

```json
{
  "name": "Cursor prod pool A",
  "provider": "cursor",
  "api_kind": "cursor_sdk",
  "secret": "key_…",
  "next_renewal_at": "2026-09-01T00:00:00Z",
  "renewal_price": "200.00",
  "currency": "USD",
  "company_ids": ["co_…", "co_…"]
}
```

## Пример renew

```json
{ "months": 3 }
```

Ответ: обновлённый `next_renewal_at`.
