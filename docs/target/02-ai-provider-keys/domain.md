# AI Provider Keys — domain

## Семантика слоя

**Credential inventory** для agent backends. Не identity пользователей. Не IdP.

Провайдеры в scope: `cursor` \| `codex` \| `claude_code`.  
**Вне scope:** GLM / Z.ai.

## Сущность `AiProviderKey`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | string | `aik_*` |
| `name` | string | Человекочитаемое имя профиля |
| `provider` | enum | `cursor` \| `codex` \| `claude_code` |
| `api_kind` | enum | См. ниже |
| `secret_ref` | string | **Единственный** способ хранения секрета (vault/KMS). Нет `secret_ciphertext` в API/каноне |
| `next_renewal_at` | datetime? | Дата следующего продления |
| `renewal_price` | money? | Учётная цена |
| `currency` | string? | ISO |
| `notes` | text? | |
| `status` | enum | `active` \| `expired` \| `disabled` |
| `created_at` / `updated_at` | datetime | |

### `api_kind`

| kind | Runtime? | Смысл |
|------|----------|-------|
| `cursor_sdk` | **Да** | `@cursor/sdk` / Cursor API key |
| `codex_sdk` | **Да** | Codex SDK (обычно OpenAI API key) |
| `openai_api` | Extension | Сырой OpenAI / Responses |
| `claude_agent_sdk` | **Да** | Anthropic API key для Agent SDK |
| `anthropic_api` | Extension | Messages API без Agent SDK |
| `openrouter` | Extension | OpenRouter |
| `cli_subscription` | **Нет** | Только **учётная метка биллинга** (Max/Pro и т.п.). **Никогда** не передавать в AgentProviderPort как credential |
| `custom` | По решению | |

Один `provider` может иметь несколько ключей с разными `api_kind`.

## Runtime resolve (законченная политика)

Порядок выбора секрета для agent session:

```text
1. Active CompanyAiKeyBinding для company_id
2. Filter: status=active, api_kind is runtime-capable (не cli_subscription)
3. Match: preferred_provider (CompanyAgentPolicy или Project.agent_provider)
4. Else: first binding for that provider by Admin priority / created_at
5. Else: platform default key (только если Admin явно разрешил platform_fallback) — **unbound keys** (без bindings)
6. Else: fail session start with NO_AI_KEY
```

| Поле политики | Где | Описание |
|---------------|-----|----------|
| `preferred_provider` | Company (default) или Project override | `cursor` / `codex` / `claude_code` |
| `platform_fallback` | Company flag, default false | Разрешить platform-owned key |

Rotate / disable → существующие сессии дорабатывают или cancel по политике; **новые** сессии ключ не получают. Audit: `ai_key.rotated` / `ai_key.disabled`.

## Продление

`renew(months)` ∈ {1..12} через `AppSelectorPage`.  
`next_renewal_at = max(now, current) + months`. Audit `ai_key.renewed`.

## Привязка

`CompanyAiKeyBinding` M:N. UI: Admin company detail → multi `AppSelectorPage`.

## Инварианты

- После create секрет не возвращается (mask / `secret_ref` prefix only).
- `cli_subscription` нельзя выбрать в resolve и нельзя передать в adapter.
- Disabled/expired — не для новых сессий.
- Delete с bindings — DangerConfirmPage + detach или cascade (явный выбор на странице).

См. [08-agent-providers](../08-agent-providers/), [api.md](api.md), [persistence.md](persistence.md).
