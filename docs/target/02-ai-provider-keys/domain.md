# AI Provider Keys — domain

## Семантика слоя

**Credential inventory** для agent backends. Не identity пользователей. Не IdP.

Провайдеры в scope: `cursor` \| `codex` \| `claude_code`.  
**Вне scope:** GLM / Z.ai.

## UI-ось «тип интеграции»

В Admin UI первое поле — **тип**, поверх хранения `provider` + `api_kind`:

| UI тип | `provider` | `api_kind` |
|--------|------------|------------|
| Cursor SDK | `cursor` | `cursor_sdk` |
| Codex SDK | `codex` | `codex_sdk` |
| Claude Agent SDK | `claude_code` | `claude_agent_sdk` |
| API key | из каталога `ai.http_providers` (`payload.agent_provider`) | из каталога (`payload.api_kind`) |

При любом SDK поле «Провайдер» скрыто. При **API key** — editable catalog picker (`AppCatalogSelectPage` + seed OpenAI / Anthropic / OpenRouter / Cursor).  
`cli_subscription` в Type UI не показывается.

**API key ≠ выбор runtime-агента в UI.** Каталог `ai.http_providers` описывает **HTTP endpoint** для **Platform OpenClaw** (OpenAPI-compatible / Anthropic Messages / custom).  
Ключи с `api_kind` из каталога (`openai_api`, `openrouter`, `custom`, …) резолвятся в **Platform OpenClaw** runtime (`platform_openclaw`), не в проприетарные SDK.  
Проприетарные SDK (`cursor_sdk`, `codex_sdk`, `claude_agent_sdk`) — отдельный класс адапterов; см. [platform-openclaw-runtime](../../06-agent-runtime/platform-openclaw-runtime.md).

Resolve-контракт и adapters **не меняются** — UI только маппит в существующие enums.

## Владение (owner_scope)

Одна инвентаризация ключей; разные владельцы:

| `owner_scope` | Кто создаёт / CRUD | Кто видит |
|---------------|--------------------|-----------|
| `platform` | **только** Platform Admin | Admin всегда; Company — **только** если есть `CompanyAiKeyBinding` → **RO** (без edit/rotate/delete) |
| `company` | Company (`owner_company_id`) или Admin от имени компании | Владелец-Company: полный CRUD; Admin: видит все (надзор) |

Company UI показывает **один list** = local company keys ∪ Admin-bound platform keys (с chip «платформа», write запрещён).  
Формы SDK / API key — **паритет** Admin ([03 ux](../03-companies/ux-contract.md)).

## Сущность `AiProviderKey`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | string | `aik_*` |
| `name` | string | Человекочитаемое имя профиля |
| `owner_scope` | enum | `platform` \| `company` |
| `owner_company_id` | string? | Обязателен при `owner_scope=company` |
| `provider` | enum | `cursor` \| `codex` \| `claude_code` |
| `api_kind` | enum | См. ниже |
| `secret_ref` | string | **Единственный** способ хранения секрета (vault/KMS). Нет `secret_ciphertext` в API/каноне |
| `next_renewal_at` | datetime? | Дата следующего продления (редактируется вручную или через renew) |
| `renewal_price` | money? | Учётная цена |
| `currency` | string? | ISO |
| `notes` | text? | |
| `status` | enum | `active` \| `expired` \| `disabled` (в detail UI не показывается как read-only поле) |
| `created_at` / `updated_at` | datetime | |

### `api_kind`

| kind | Runtime? | Смысл |
|------|----------|-------|
| `cursor_sdk` | **Да** | `@cursor/sdk` / Cursor API key |
| `codex_sdk` | **Да** | Codex SDK (обычно OpenAI API key) |
| `platform_openclaw` | **Да** | Platform OpenClaw — universal agent loop + HTTP LLM из каталога |
| `openai_api` | **Да** (via Platform OpenClaw) | OpenAI Chat/Responses endpoint |
| `claude_agent_sdk` | **Да** | Anthropic API key для Agent SDK |
| `anthropic_api` | **Да** (via Platform OpenClaw) | Anthropic Messages endpoint |
| `openrouter` | **Да** (via Platform OpenClaw) | OpenRouter |
| `cli_subscription` | **Нет** | Только **учётная метка биллинга** (Max/Pro и т.п.). **Никогда** не передавать в AgentProviderPort как credential |
| `custom` | По решению | |

Один `provider` может иметь несколько ключей с разными `api_kind`.

## Runtime resolve (законченная политика)

Порядок выбора секрета для agent session:

```text
1. Candidate set for company_id:
   a. AiProviderKey where owner_scope=company AND owner_company_id=company_id
   b. PLUS platform keys with active CompanyAiKeyBinding
2. Filter: status=active, api_kind runtime-capable (не cli_subscription)
3. Match: preferred_provider (CompanyAgentPolicy или Project.agent_provider)
4. Else: first candidate by priority / created_at (company-owned before platform-bound, unless policy says otherwise)
5. Else: platform unbound pool (только если Admin разрешил platform_fallback)
6. Else: fail session start with NO_AI_KEY
```

| Поле политики | Где | Описание |
|---------------|-----|----------|
| `preferred_provider` | Company (default) или Project override | `cursor` / `codex` / `claude_code` |
| `platform_fallback` | Company flag, default false | Разрешить platform-owned key |

Rotate / pause (`status=disabled`) → **cancel** ACTIVE `AgentSession` with `resolved_key_id` = this key;
pause ACTIVE projects of bound companies when the key was the **last ACTIVE runtime-capable
binding** for the company's `preferred_provider` (or any provider if preferred is unset).
`platform_fallback` does **not** prevent that pause.
**Resume key** is an explicit PATCH `status=active` only — projects stay paused (manual resume).
**Empty secret** is treated like paused for runtime/UI (create without secret → `disabled`).

### Expiry (`next_renewal_at`)

When the date is past, lazy path sets **`disabled`** (not `expired`) and runs the same cascade.
`renew` only extends the date — **never** auto-activates.
`rotate_secret` writes the vault secret — **never** auto-activates.

### Session snapshot

On session create, `agent_sessions.resolved_key_id` stores the credential key id from resolve.
Key module does **not** own Project; cascade uses binding membership + session snapshot.

### Project resume

Manual only. Requires a valid runtime key (`resolve_credentials`); otherwise `NO_AI_KEY`.
Re-enabling a key does **not** resume projects.

## Продление

- Ручной PATCH `next_renewal_at` (ISO) из UI (формат даты `DD.MM.YY` / `DD.MM.YYYY`).
- `renew(months)` ∈ {1..12}: `next_renewal_at = max(now, current) + months`. Audit `ai_key.renewed`.

## Привязка (только platform → company)

`CompanyAiKeyBinding` M:N: Admin привязывает **platform** key к компаниям.  
Company-owned keys **не** требуют binding (они уже принадлежат компании).  
UI Admin: multi `AppCatalogSelectPage` на detail platform-ключа.

## Каталог HTTP-провайдеров

Таблица `reference_catalog_entries`, `catalog_id = ai.http_providers`.  
Admin CRUD: `/admin/catalogs/{catalog_id}/entries`. Seed idempotent при первом list. Seeded entries редактируемы/удаляемы.

### Payload (`ai.http_providers` / Platform OpenClaw)

Платформа работает только с **OpenAPI-совместимыми** HTTP endpoints (Chat Completions / Models).  
Отдельный UI-чекбокс `openai_compatible` не нужен: для seed-пресетов значение фиксировано; для `custom` всегда `true` (Anthropic seed — Messages API, `openai_compatible=false` только в payload seed).

| Ключ | Тип | Смысл |
|------|-----|--------|
| `api_kind` | string | `openai_api` \| `anthropic_api` \| `openrouter` \| `custom` |
| `agent_provider` | string | `codex` \| `claude_code` \| `cursor` — для resolve SDK-ключа; **не** поле UI редактора каталога |
| `base_url` | string | Origin + prefix (Ollama: `http://127.0.0.1:11434/v1`) |
| `openai_compatible` | bool | Хранится в payload; UI не редактирует |
| `auth_scheme` | string | `bearer` \| `x-api-key` \| `none` |
| `chat_completions_path` | string | Относительный path |
| `models_path` | string | Относительный path для list models |

Seed: OpenAI, Anthropic, OpenRouter, Cursor, Ollama.  
**UI edit:** `api_kind` ≠ `custom` (облачные пресеты) — только имя; `custom` (Ollama/Cursor/свой endpoint) — base URL, auth, paths. Без выбора `agent_provider` и без openai-compatible switch.

## Инварианты

- После create секрет не возвращается (mask / `secret_ref` prefix only).
- `cli_subscription` нельзя выбрать в resolve и нельзя передать в adapter.
- Disabled/expired — не для новых сессий.
- Delete с bindings — AppConfirmPage + cascade runtime stop (sessions + selective project pause), then vault delete.

См. [08-agent-providers](../08-agent-providers/), [api.md](api.md), [persistence.md](persistence.md).
