# AI Provider Keys — API

Два префикса, **один** домен ([domain](domain.md) `owner_scope`).

## Admin — `/api/v1/admin/ai-keys`

Только `platform.admin`. Видит все keys; создаёт обычно `owner_scope=platform` (может создавать company-owned от имени org).

| Method | Path | Описание |
|--------|------|----------|
| GET | `/admin/ai-keys` | Список (без секрета) |
| POST | `/admin/ai-keys` | Создать (`secret` один раз; default `owner_scope=platform`) |
| GET | `/admin/ai-keys/{id}` | Detail + bindings |
| PATCH | `/admin/ai-keys/{id}` | Имя, цена, notes, status, next_renewal_at |
| POST | `/admin/ai-keys/{id}/renew` | `{ "months": 1..12 }` |
| POST | `/admin/ai-keys/{id}/rotate-secret` | Новый secret |
| PUT | `/admin/ai-keys/{id}/companies` | Bindings (только platform keys) |
| DELETE | `/admin/ai-keys/{id}` | Удалить (если политика позволяет) |
| GET | `/admin/ai-keys/audit-events` | Audit log (`?key_id=` optional) |

## Company — `/api/v1/companies/{company_id}/ai-keys`

Company principal (или interim `company.admin`).

| Method | Path | Описание |
|--------|------|----------|
| GET | `.../ai-keys` | Local company-owned **∪** Admin-bound platform (флаг `writable` / `source`) |
| POST | `.../ai-keys` | Создать **только** `owner_scope=company` для этого `company_id` |
| GET | `.../ai-keys/{id}` | Detail если в scope list |
| PATCH / renew / rotate / DELETE | `.../ai-keys/{id}` | **Только** local company-owned; bound platform → **403** |

Company **не** вызывает `PUT .../companies` (bindings — только Admin).

## Audit events

| event_type | When |
|------------|------|
| `ai_key.created` | POST create |
| `ai_key.updated` | PATCH metadata |
| `ai_key.disabled` | PATCH status → disabled |
| `ai_key.renewed` | POST renew |
| `ai_key.rotated` | POST rotate-secret (secret never in detail) |
| `ai_key.companies_set` | PUT companies |
| `ai_key.deleted` | DELETE |
| `ai_key.expired` | Lazy expire on resolve when `next_renewal_at` past |

## Platform fallback pool

Keys with **no** `company_ai_key_bindings` rows are the platform pool.  
Used only when `resolve_credentials(..., platform_fallback=True)` and company has no matching runtime key.

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
