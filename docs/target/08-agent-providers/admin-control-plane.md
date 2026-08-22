# Admin control plane — agent SDK capabilities

Platform Admin должен иметь **полный продуктовый контроль** над тем, как агенты бегают на платформе (не заменяя vendor dashboards, а оркестрируя runtime).

## Поверхности Admin UI

| Раздел | Управление |
|--------|------------|
| AI Provider Keys | CRUD keys, bindings, renewals ([02](../02-ai-provider-keys/)) |
| Model catalog | Refresh from Cursor.list / curated lists; enable/disable ids |
| Model allowlists | Platform defaults; per-company narrow |
| Tool policy presets | `chat_readonly`, `workspace_dev`, … ([permissions-policy](permissions-policy.md)) |
| Company policy | Assign preset, network allowlist, quotas, preferred provider, platform_fallback |
| Usage & quotas | Tokens/$, concurrent runs, alerts ([usage-metrics](usage-metrics.md)) |
| Runtime health | Adapter versions, sandbox helper presence, error rates |
| Break-glass | Temporary widen policy + mandatory audit |

Employee **не** обходит Admin policy. Company admin может только **сужать** (если Admin делегировал).

## Effective policy resolve

```text
Platform defaults
  → Company AgentRuntimePolicy (⊆)
    → Project overrides (⊆)
      → CreateOpts for adapter
```

Любая попытка расширить на нижнем уровне → reject.

## Полный контроль = эти рычаги

1. **Какой provider/key** — bindings + resolve ([02 domain](../02-ai-provider-keys/domain.md)).
2. **Какая model** — allowlist + default ([models-and-routing](models-and-routing.md)).
3. **Что умеет tool-ом** — AgentToolPolicy → SDK mapping ([permissions-policy](permissions-policy.md)).
4. **Сколько тратит** — quotas + usage ingest ([usage-metrics](usage-metrics.md)).
5. **Где бежит** — always project container; no host ambient settings.
6. **Какой MCP** — cabinet manifest ∩ company policy allowlist.
7. **Audit** — tool deny/allow, policy changes, break-glass.

## API (логический)

```http
GET/PUT  /api/v1/admin/agent-policies/defaults
GET/PUT  /api/v1/admin/companies/{id}/agent-policy
POST     /api/v1/admin/models/sync?provider=cursor
GET      /api/v1/admin/models
PUT      /api/v1/admin/companies/{id}/model-allowlist
GET      /api/v1/admin/usage?from&to&group_by=
POST     /api/v1/admin/companies/{id}/break-glass-policy
```

## Связь с кабинетом

Кабинет управляет **контентом** workspace (prompts/skills/MCP configs).  
Admin управляет **безопасностью и квотами** runtime.  
Пересечение MCP: `cabinet_manifest ∩ admin_mcp_allowlist`.

## Definition of done

Admin без SSH/kubectl может: сменить preset компании на readonly, урезать модели, увидеть spend, остановить превышение квоты — и это применяется к Cursor, Codex и Claude через один policy object.
