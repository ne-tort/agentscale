# Models and routing

## Цель

Admin (и при делегировании Company) задаёт **какие модели** можно выбирать Employee / Project, и default на провайдера.

## Сущности

| Сущность | Описание |
|----------|----------|
| `ModelCatalogEntry` | `{ provider, model_id, label, params_schema?, active }` — кэш/снимок |
| `ModelAllowlist` | Разрешённые `model_id` per provider (platform → company → project ⊆) |
| `ModelDefault` | Default model per provider / company |

Синхронизация catalog:

| Provider | Как обновлять |
|----------|----------------|
| Cursor | Периодический job: `Cursor.models.list({ apiKey })` под platform key → upsert catalog |
| Codex | Ручной/semiauto список актуальных API models + проверка ключом |
| Claude | Anthropic model list / documented ids; validate on session start |

## Cursor specifics

- Local agents: **model required**.
- Prefer ids from `Cursor.models.list()` — не хардкодить экзотику.
- `composer-2.5` — типичный default на момент research.
- Commerce bot: fallback `{ id: "default" }` (не `"auto"` — ConfigurationError в части версий).
- Router: model id `auto-smart` + param `optimize_for` (`balance` / `intelligence` …) — Teams/Enterprise; Admin может **запретить** Router в allowlist.
- Per-run override sticky on agent until next override.

## Codex / Claude

- Явный model id в CreateOpts.
- Allowlist режет UI selector и server-side session start (`MODEL_NOT_ALLOWED`).

## UI

| Contour | Поведение |
|---------|-----------|
| Admin | Catalog refresh, allowlist per key/provider, platform defaults |
| Company | Optional narrow allowlist ⊆ platform |
| Employee / Project | Selector только из effective allowlist; laconic |

См. [admin-control-plane.md](admin-control-plane.md).
