# 03 — Claw / SDK / MCP / токены

## Контекст

«Claw» в Prodavan — не отдельный репозиторий с именем claw, а **agent-control слой**: единый порт над Cursor / Codex / Claude / platform OpenClaw, bridge в Pod sidecar `prodavan-agent-runtime`, MCP-пакеты, usage/HITL. Аналог идей openclaw/cursor cloud agent loop, ориентированный на управление через API.

Канон-фрагменты: [`docs/06-agent-runtime/`](../../06-agent-runtime/), [`docs/target/08-agent-providers/`](../../target/08-agent-providers/).

## Текущая реализация (as-is)

### Порт и registry

- Protocol: [`domain/agent/port.py`](../../../apps/api/src/prodavan/domain/agent/port.py) — `create / resume / send / cancel / close`.
- Events: [`domain/agent/types.py`](../../../apps/api/src/prodavan/domain/agent/types.py) — frozen + stream event types.
- Registry: [`adapter_registry.py`](../../../apps/api/src/prodavan/application/agent/adapter_registry.py) — **фабрика if/elif**, не plugin registry. В prod при `pod_agent_runtime_enabled` in-process адаптеры disabled; реальная работа — HTTP bridge в Pod.
- In-repo adapters: только [`FakeAgentAdapter`](../../../apps/api/src/prodavan/infrastructure/agent/fake_adapter.py), [`FixtureCursorAdapter`](../../../apps/api/src/prodavan/infrastructure/agent/fixture_cursor_adapter.py).

`ApiKind` ([`domain/ai_keys/types.py`](../../../apps/api/src/prodavan/domain/ai_keys/types.py)): cursor_sdk, codex_sdk, claude_agent_sdk, openai_api, anthropic_api, openrouter, custom, …

Маппинг `api_kind → bridge_adapter` **дублирован**: [`openclaw_bridge.py`](../../../apps/api/src/prodavan/application/agent/openclaw_bridge.py) и [`openclaw_config_materializer.py`](../../../apps/api/src/prodavan/application/projects/openclaw_config_materializer.py).

### Bridge

[`OpenClawBridgeBootstrap`](../../../apps/api/src/prodavan/application/agent/openclaw_bridge.py):

- `register_session` → POST `/v1/sessions`
- `iter_send_events` → SSE send, **retry-once** на SESSION_NOT_FOUND / UNREACHABLE / EMPTY
- `_patch_adapter_state` — обход wipe undefined fields старыми runtime
- stub-detection по префиксам ответа

[`pod_session_bootstrap.py`](../../../apps/api/src/prodavan/application/agent/pod_session_bootstrap.py) — Redis lock на bootstrap после Running.

[`conversation_rehydrate.py`](../../../apps/api/src/prodavan/application/agent/conversation_rehydrate.py) — после wipe emptyDir: **text-prefix** истории (лимиты chars/turns), не native checkpoint.

### MCP

[`application/mcp/`](../../../apps/api/src/prodavan/application/mcp/):

- `prodavan_modules_mcp` — meta/data/actions через platform HTTP + Bridge JWT + `X-Prodavan-Session-Id`
- `prodavan_equipment_mcp` — catalogs / lines / offers
- `bridge_env.py` — единые env placeholders (`PRODAVAN_*`, `BRIDGE_AUTH_TOKEN`, …)
- Два рендера: `mcp.json` (SDK) и `.prodavan/config.yaml` servers (OpenClaw)

[`policy_service.filter_mcp_servers`](../../../apps/api/src/prodavan/application/agent/policy_service.py) фильтрует **mcp.json packages**, не openclaw `servers` map.

### Sessions / send

[`session_service.py`](../../../apps/api/src/prodavan/application/agent/session_service.py) — create/send/transcript/approvals/fork/…  
`_iter_send_events`: **две ветки** (bridge vs in-process adapter) с copy-paste USAGE/flush/checkpoint.  
Mid-turn flush каждые N events; user_message commit до vendor stream.  
Tool approval: forward в bridge; при fail — **синтез DONE** на платформе.

### Usage / budget

- Persist: `AgentUsageRow` — `input_tokens`, `output_tokens`, `cost_usd` (без cache_* / message_id).
- Enforce: [`budget_service.py`](../../../apps/api/src/prodavan/application/agent/budget_service.py) — month tokens, month USD, per-run tokens.
- Vendor notes Claude: [`cost-tracking.snapshot.md`](../../target/08-agent-providers/vendor-docs/claude/cost-tracking.snapshot.md) — placeholder output, cache tariffs, estimate USD.

Triggers: [`trigger_dispatcher.py`](../../../apps/api/src/prodavan/application/agent/trigger_dispatcher.py) — outbox-lite + advisory lock (не полный Kafka cutover).

## Проблемы

### CLAW-P0a — SDK вне репо / только fake

**Приоритет:** P0  
Нет `CursorSdkAdapter` / `CodexSdkAdapter` / `ClaudeAgentSdkAdapter` / `PlatformOpenClawAdapter` в prodavan. Ревью create/stream/cancel/token semantics невозможно в этом репо — только bridge HTTP контракт.

### CLAW-P0b — неоднородный подсчёт токенов

**Приоритет:** P0  

- Нет `cache_creation` / `cache_read`.
- Claude per-step `output_tokens` placeholder → риск двойного суммирования.
- Нет dedupe по `message_id`.
- `cost_usd` как estimate используется в hard budget.

### CLAW-P0c — rehydrate text-prefix

**Приоритет:** P0  
Теряются tool_use/tool_result bindings, thinking, subagent traces. Потолок chars/turns. Только при pod runtime.

### CLAW-P1a — дубли и copy-paste

**Приоритет:** P1  
Двойной `api_kind_to_bridge_adapter`; двойной send path в `session_service`.

### CLAW-P1b — MCP policy асимметрия

**Приоритет:** P1  
Allowlist/deny применяется к одному формату конфига.

### CLAW-P1c — bridge гонки и HITL fallback

**Приоритет:** P1  
Окно POST register → PATCH state; fire-and-forget register после commit; при bridge-down platform пишет DONE, Pod мог продолжить — расхождение состояний.

### CLAW-P2a — асимметрия test vs prod path

**Приоритет:** P2  
`adapter_state` / stub-detection / `vendor_agent_id` семантика различаются между in-process и bridge.

## Target-design

### Слои claw

```text
UI / Triggers → session_service / HITL
        ↓
   BridgeClient (единственный HTTP/SDK фасад)
        ↓
Pod agent-runtime (OpenClaw)
   ├ AdapterRegistry (plugins: cursor, codex, claude, platform)
   ├ SessionManager
   ├ CredentialStore (lease, no read-back)
   ├ McpManager (один McpConfig → N renders)
   ├ ToolApprovalBroker (ack-based)
   └ TokenNormalizer (per-adapter semantics)
```

### Контракты

1. **Один `AgentProviderPort`** с реализациями в claw/runtime-коде (лучше: submodule или `packages/claw` в монорепо), API только проксирует.
2. **Plugin registry** с metadata: vendor, capabilities, token-semantics — не if/elif.
3. **Один `adapter_kinds.py`**: `ApiKind` ↔ `AdapterKind` ↔ `ProviderDialect`, покрытый табличными тестами.
4. **`TokenNormalizer`:** Claude dedupe + cache fields; OpenAI passthrough; Cursor cumulative→delta; zeroed usage не затирает last non-zero; `cost_estimate_usd` soft limit.
5. **Расширить `AgentUsageRow`:** `cache_creation_tokens`, `cache_read_tokens`, `message_id`, `token_source`, rename cost → estimate.
6. **MCP:** `mcp_packages` → `McpConfig` → render mcp.json **и** openclaw; filter **до** render.
7. **Rehydrate:** native checkpoint в object store; text-prefix = `_emergency_text_rehydrate_fallback`.
8. **HITL ack-based:** нет синтетического DONE при bridge-down; turn → `interrupted` / TTL.
9. **Единый send path** через BridgeClient; in-process adapters только за mock Bridge в unit-тестах.
10. **Trigger outbox → Kafka** exactly-once claim (см. 05).

### Ответственность

| Компонент | SoT | Примечание |
|-----------|-----|------------|
| Transcript UI | Postgres events | Dual-write from bridge append |
| Conversation native state | Pod + object-store checkpoint | Не emptyDir alone |
| adapter_state | Pod runtime | API — readback cache |
| Usage | Normalized rows | Не raw vendor dump |
| MCP allowlist | Policy before render | Один объект конфига |

## Шаги рефакторинга

1. Вынести единый [`adapter_kinds`](../../../apps/api/src/prodavan/application/agent/) модуль; удалить дубли маппинга.
2. Схлопнуть `_iter_send_events` в один путь через BridgeClient facade.
3. Добавить `TokenNormalizer` + миграция `AgentUsageRow` (cache/message_id); обновить budget soft/hard.
4. Единый MCP materialize pipeline + policy на `McpConfig`.
5. Checkpoint conversation в object store на DONE / перед hydrate bump; сузить text-prefix.
6. HITL: требовать ack; убрать synthesize DONE (или только explicit `force_interrupt`).
7. Опубликовать/подтянуть код Pod runtime адаптеров в репозиторий (submodule) для ревью.
8. Свести docs в `docs/ARCHITECTURE` + один `claw-contract.md` (ссылка из 06-agent-runtime).

## Ссылки

- Adapter port notes: [`docs/target/08-agent-providers/`](../../target/08-agent-providers/)
- Platform openclaw: [`docs/06-agent-runtime/`](../../06-agent-runtime/)
- Isolation: [`docs/02-architecture/agent-isolation.md`](../../02-architecture/agent-isolation.md)
