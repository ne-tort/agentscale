# L03 — AI Provider Keys

## Цель

Инвентарь ключей провайдеров, безопасное хранение (`secret_ref`), bindings к Company, **resolve policy** для runtime. Не identity. Не IdP.

## Канон

- [02-ai-provider-keys/](../02-ai-provider-keys/) — [domain](../02-ai-provider-keys/domain.md), [api](../02-ai-provider-keys/api.md), [persistence](../02-ai-provider-keys/persistence.md)
- [08 admin-control-plane](../08-agent-providers/admin-control-plane.md) (policy пересечения)
- [00-principles](../00-principles.md) §1

## Зависимости

| Нужно | Даёт |
|-------|------|
| L00; `company_id` как opaque FK (мок/реальный L01) | `AiProviderKey` CRUD; `resolve(company, project?) → runtime credential` |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `AiProviderKey` entity | поля domain.md; API **без** raw secret |
| `secret_ref` | Vault/KMS only |
| `CompanyKeyBinding` | M:N key↔company |
| `resolve_credentials` | application service → `{ api_kind, secret, provider, model_defaults? }` |
| Ban | `cli_subscription` **не** возвращается как runtime credential |
| Admin metrics hooks | renewal dates, missing bindings alerts (данные для L04) |

## Изоляция

Полный CRUD+resolve с тестовым `company_id` **до** Admin UI. Vault можно local-dev (file/sops) при том же `secret_ref` контракте.

## DoD

- [ ] CRUD ключей по api.md; list не светит secret.
- [ ] `api_kind` enum полный; runtime kinds отделены от `cli_subscription`.
- [ ] Bind/unbind company; resolve учитывает status `active`.
- [ ] Тест: resolve с `cli_subscription`-only → error / no credential.
- [ ] Persistence: secret только ref; ротация ref документирована.
- [ ] Contract test `resolve_credentials` для Cursor/Codex/Claude kinds (хотя бы один real kind + fake secret backend).

## Не считать готовым, если…

- Secret в JSON response или в Postgres plaintext «на время».
- Resolve захардкожен env `CURSOR_API_KEY` без сущности Key.
- UI Admin «есть», а domain/resolve нет.
- `cli_subscription` уходит в AgentProviderPort.

## Exit gate

API contract tests зелёные; L04 может строить AdminAiKeysPage; L08 может звать resolve.
