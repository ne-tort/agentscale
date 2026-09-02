# Models and routing

## Цель

Admin (и при делегировании Company) задаёт **какие модели** можно выбирать Employee / Project, и UI default на провайдера.

## As-built (Prodavan)

| Правило | Реализация |
|---------|------------|
| **Модель только через API** | `POST /chat/stream`, `POST .../agent/sessions`, bridge `POST /v1/sessions` и `.../send` — optional `model`. **Не** в `.prodavan/config.yaml`. |
| **Live list = UI** | `GET /projects/{id}/models/live` → pod bridge `GET /v1/models` → `Cursor.models.list()` (cursor_sdk). |
| **Allowed list = фильтр** | Key bindings `enabled` ∩ live list; пустой enabled = без ограничений. Company `model_allowlist` — optional ceiling. |
| **`is_default` = UI only** | `AiKeyModelBindingRow.is_default` → `default_model` в live response для preselect dropdown. **Не** подставляется в runtime без явного выбора пользователя. |
| **`"default"` passthrough** | Валидный model id для Cursor SDK передаётся как есть. |
| **Pod required** | Live list и chat send требуют running pod (`require_running_pod_runtime`). |

## Сущности

| Сущность | Описание |
|----------|----------|
| `ModelCatalogEntry` | Seed в `ai_models` — admin reference; **не** primary UI source для employee |
| `AiKeyModelBindingRow` | per key: `enabled`, `is_default` — фильтр + UI preselect |
| `ModelAllowlist` | `companies.model_allowlist` — optional company ceiling |

## Cursor specifics

- Prefer ids from live `Cursor.models.list()` — не хардкодить в config.
- `"default"` — валидный id для Cursor SDK (account default).
- Per-turn override: `model` в каждом `POST /chat/stream` обновляет session + bridge send.

## Codex / Claude

- Live list через bridge adapter (when wired); до этого — empty live → `MODELS_UNAVAILABLE`.
- Allowlist режет UI selector и server-side validation (`MODEL_NOT_ALLOWED`).

## UI

| Contour | Поведение |
|---------|-----------|
| Admin | Key model bindings (enabled/default) поверх live catalog |
| Company | Optional narrow allowlist ⊆ platform |
| Employee / Project | Dropdown из `/models/live`; send с `selectedModel` каждый turn |

См. [admin-control-plane.md](admin-control-plane.md).
