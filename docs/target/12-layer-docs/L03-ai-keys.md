# L03 — AI Provider Keys

| Поле | Значение |
|------|----------|
| Status | done |
| Quality | 8 |
| Quality note | CRUD+bindings+resolve+audit+platform pool+vault:// routing; live Vault ops — gap |
| Plan | [L03](../11-implementation-plan/L03-ai-keys.md) |
| Canon | [02-ai-provider-keys](../02-ai-provider-keys/) |
| Last updated | 2026-08-23 — project agent_provider override wired |
| Owners | — |

---

## Семантика

Инвентарь credentials для agent backends. Секрет только secret_ref. `resolve_credentials` — единственный runtime-путь. cli_subscription не credential.

## Что сделано

| Сделано | Не сделано / Gaps |
|---------|-------------------|
| Таблицы ai_provider_keys, company_ai_key_bindings + Alembic ai_keys_001 | live Vault token/ops hardening |
| Admin CRUD /api/v1/admin/ai-keys без raw secret в response | — |
| list_keys returns company_ids per key | — |
| FileSecretStore → SECRETS_DIR/ai_keys/*.secret | — |
| Vault KV v2 backend (`vault://ai_keys/…`) via RoutingSecretStore | — |
| Admin Flutter: list/create/bind/disable/renew/rotate (L04) | |
| AiKeysService.resolve_credentials + ban cli_subscription | |
| Lazy expire: next_renewal_at past → status expired on resolve + audit | |
| platform_fallback → unbound keys pool on resolve | L08 create_session reads company policy |
| Project.agent_provider override on resolve (via L07 create/PATCH) | — |
| company_key_metrics(company_id) for L04 alerts | |
| Bind/unbind companies; renew months 1..12; rotate-secret | |
| Disable/delete cascade: cancel sessions by `resolved_key_id`; pause projects that lose last runtime binding for preferred_provider | |
| Lazy past `next_renewal_at` → `disabled` + cascade; renew/rotate never auto-activate | |
| Project resume gated on `resolve_credentials` (manual only) | |

## Как сделано

1. Domain enums AiProvider / ApiKind / RUNTIME_API_KINDS.
2. ORM + FK на companies.id (L01).
3. Create: secret → RoutingSecretStore (file:// or vault://) → DB только secret_ref; API отдаёт secret_ref_prefix.
4. Resolve: active bindings → lazy expire by next_renewal_at → filter runtime kinds → preferred_provider (project.agent_provider or company policy) → else first by created_at → else NO_AI_KEY.
5. Renew: extends next_renewal_at; reactivates status expired → active.
6. Тесты: unit (file store + kind ban); integration (CRUD+resolve+lazy expire) при Postgres.

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-KEY-ENTITY | Admin AI keys API без secret + Admin Flutter subset | **live** |
| C-KEY-RESOLVE | AiKeysService.resolve_credentials → ResolvedCredential | **live** |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-MEMBERSHIP | L01 company_id | live |
| C-API-HEALTH | L00 | live |

## Связи

→ L04 Admin UI, L08 AgentProviderPort. ← L01 companies.  
Секрет не уходит в HTTP list/detail (только create/rotate принимают secret).

## Инварианты

- Нет plaintext secret в Postgres / JSON list/get.
- cli_subscription не проходит resolve.
- Disabled/expired не для новых сессий (resolve фильтрует active; lazy expire по дате).
- Delete каскадит bindings; файл секрета удаляется.

## Карта кода

```text
apps/api/src/prodavan/
  domain/ai_keys/
  application/ai_keys/service.py
  api/v1/ai_keys.py
  infrastructure/secrets/file_store.py
  infrastructure/persistence/models/ai_keys.py
apps/api/alembic/versions/2026082303_ai_keys.py
apps/api/tests/unit/test_ai_keys_domain.py
apps/api/tests/integration/test_ai_keys.py
apps/flutter/lib/features/admin/ai_key_{list,create,detail,rotate}_page.dart
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| CRUD без secret в list | done | |
| api_kind enum + runtime filter | done | |
| Bind/unbind + resolve active | done | |
| Lazy expire next_renewal_at | done | on resolve; renew reactivates expired |
| Test cli_subscription → NO_AI_KEY | done | |
| secret_ref only | done | file:// + vault:// routing |
| Vault production backend | live (subset) | VaultSecretStore when VAULT_ADDR set; live ops gap |
| platform_fallback keys | done | unbound keys (empty company_ids) |
| Audit ai_key.* | done | incl. ai_key.expired on lazy expire |
| HTTP resolve endpoint | n/a | in-process для L08 (секрет не светить в admin HTTP) |
| Product blobs не через secrets_dir / local FS | **P0 note** | `file://` для **secret material** ок; workspace/attachments/packages — только MinIO ([13](../13-platform-infra/)); не расширять secrets_dir под product files |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_ai_keys_domain.py tests/integration/test_ai_keys.py -q
# with Postgres: alembic upgrade head && full CRUD/resolve suite
```

## Оценка качества

Рубрика: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 2 | DoD CRUD+resolve закрыт |
| B. Контракты | 2 | C-KEY-ENTITY / C-KEY-RESOLVE live |
| C. Инварианты и проверки | 2 | no secret leak; cli ban tested |
| D. As-built ясность | 2 | эта карточка |
| **Quality (итог)** | **8** | live Vault token/ops = gap |
