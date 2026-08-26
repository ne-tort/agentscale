# 02 — AI Provider Keys

Инвентарь ключей ИИ: **platform-owned** (Admin) и **company-owned** (Company CRUD).  
Company в одном list видит свои + Admin-bound (RO).

| Документ | Содержание |
|----------|------------|
| [domain.md](domain.md) | `owner_scope`, resolve, api_kind |
| [api.md](api.md) | Admin + Company HTTP |
| [persistence.md](persistence.md) | Таблицы и секреты |

Минимальный `provider`: **cursor**, **codex**, **claude_code**.  
Связано: [03 Companies](../03-companies/), [01 Admin](../01-platform-admin/).
