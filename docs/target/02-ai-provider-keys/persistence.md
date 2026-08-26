# AI Provider Keys — persistence

## Таблицы (platform DB)

### `ai_provider_keys`

| Column | Notes |
|--------|-------|
| `id` PK | `aik_*` |
| `name` | |
| `owner_scope` | `platform` \| `company` |
| `owner_company_id` | FK nullable; required if company-owned |
| `provider` | indexed |
| `api_kind` | indexed |
| `secret_ciphertext` / `secret_ref` | не логировать |
| `next_renewal_at` | nullable |
| `renewal_price` | numeric nullable |
| `currency` | char(3) nullable |
| `notes` | text |
| `status` | |
| `created_at`, `updated_at` | |

### `company_ai_key_bindings`

Привязка **platform** key → company (не нужна для `owner_scope=company`).

| Column | Notes |
|--------|-------|
| `company_id` FK | |
| `ai_provider_key_id` FK | platform-owned key |
| PK | (`company_id`, `ai_provider_key_id`) |

## Секреты

- Предпочтительно external vault (как S4B vault pattern в legacy).
- В API и audit — только mask (`****abcd`).
- Env `CURSOR_API_KEY` (legacy) — bootstrap/fallback до миграции на таблицу; после миграции не канон.

## Индексы

- `(provider, status)`
- `(next_renewal_at)` для алертов Admin
