# L08 — Agent providers

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 8 |
| Quality note | Port+events+fixture+budget+SSE+HITL; Node sidecar — gap |
| Plan | [L08](../11-implementation-plan/L08-agent-providers.md) |
| Canon | [08-agent-providers](../08-agent-providers/) |
| Last updated | 2026-08-24 — MODEL_NOT_ALLOWED + model_allowlist enforce |
| Owners | — |

---

## Семантика

AgentProviderPort + frozen AgentEvent; credentials только через L03 resolve; tool policy из L04 preset; cwd/mcp из L07 materialize.

**Два класса runtime:** проприетарные SDK + **Platform OpenClaw** ([spec](../../06-agent-runtime/platform-openclaw-runtime.md)).  
**Не** upstream OpenClaw / GLM; **не** cli_subscription в runtime.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `AgentProviderPort` + frozen `AgentEvent` types | Node sidecar (real Cursor SDK) |
| `FixtureCursorAdapter` (cursor_sdk) + `FakeAgentAdapter` | Codex/Claude real adapters; **Platform OpenClaw** |
| ORM agent_sessions / agent_events / agent_usage | Codex/Claude real adapters |
| `agent_sessions.resolved_key_id` set on create; key disable cancels by snapshot | |
| `POST /projects/{id}/chat` + `/chat/stream` (SSE) | Node sidecar (real Cursor SDK) |
| `GET .../chat/transcript` + list sessions; user + tool bubbles | |
| `AgentBudgetService` — monthly tokens + USD + per-run token hard-stop | Node sidecar (real Cursor SDK) |
| HITL `tool_approval_request` + approve/deny API + L05 ToolApprovePage | Codex/Claude real adapters |
| Trigger dispatch + drain (`?max=`) + regenerate/schedule/webhook/telegram + admin drain + opt-in worker (advisory lock + outbox lease); regenerate recovers text from cancelled sessions | External broker |
| Chat text optional when attachment_refs present | |
| `CompanySubscriptionGate` on session create/send → `COMPANY_SUSPENDED` | |
| Pause: create_session / chat_turn blocked (`PROJECT_PAUSED`); cancel_session allowed; pause cancels ACTIVE; transcript falls back to latest cancelled + `session_status`; chat ignores stale cancelled session_id | |
| Company suspend cancels ACTIVE sessions company-wide (`sessions_cancelled` on `company.suspended`) | |
| Unit + integration tests | Golden JSON fixtures |

## Как сделано

1. Domain `AgentEvent`, `CreateOpts`, `AgentToolPolicy` presets.
2. `AgentPolicyService` — company preset + mcp.json ∩ policy; `model_allowlist` default + `MODEL_NOT_ALLOWED` on override outside list.
3. `AgentSessionService` — create/send/chat_turn/transcript; create uses write gate (blocks pause); cancel uses `allow_paused`; chat/SSE ignore non-ACTIVE `session_id` (stale after pause) and open a fresh session; transcript returns `session_status`; `AgentBudgetService` before turns.
4. `AgentTriggerDispatcher` — dequeue trigger → session + send; regenerate falls back to latest/cancelled session text then opens a new ACTIVE session if needed; `drain_all` for worker/admin.
5. HTTP `/projects/{id}/agent/sessions`, `/chat`, `/chat/transcript`, `/triggers/dispatch`; admin `/admin/triggers/drain`.
6. Chat attachment refs validated against project DB before send (C-ATTACH).
7. Opt-in `TRIGGER_WORKER_ENABLED` asyncio loop in API lifespan + row lease/SKIP LOCKED (multi-replica: advisory lock + lease; not external broker).
8. `user_message` — platform envelope only (not in frozen adapter AgentEvent stream).
9. Lazy subscription SPI commit is inside `CompanySubscriptionGate.ensure_suspended_platform_event` (session has no auto-commit — SSE).

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-AGENT-PORT | create/send/cancel/close | **live** (fixture adapters) |
| C-AGENT-EVENT | frozen schema + persist | **live** (subset) |
| C-USAGE | agent_usage rows | **live** (subset) |
| C-PROJECT-CHAT | chat_turn + transcript | **live** (subset) |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-KEY-RESOLVE | L03 | live (incl. platform_fallback from L04 policy) |
| C-PROJECT / C-MATERIALIZE | L07 | live |
| C-ADMIN-COMPANY policy | L04 | live (preset + token budgets) |

## Карта кода

```text
apps/api/src/prodavan/
  domain/agent/{types,port}.py
  application/agent/{session_service,policy_service,budget_service,adapter_registry,trigger_dispatcher}.py
  infrastructure/agent/{fake_adapter,fixture_cursor_adapter}.py
  infrastructure/persistence/models/agent.py
  api/v1/agent.py
apps/api/alembic/versions/2026082307_agent.py
apps/api/alembic/versions/2026082308_agent_budget.py
apps/api/tests/unit/test_agent_*.py
apps/api/tests/integration/test_agent.py
```

## Gaps vs канon / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Port + contract tests | done | fake + fixture cursor |
| Cursor real SDK sidecar | hole | Node bridge in pod |
| Event types incl usage/done/error | done | fixture emits core set |
| HITL tool_approval_request | done | fixture `dangerous:` + API + ToolApprovePage; real SDK hooks — hole |
| MCP = materialize ∩ policy | done | filter in policy_service |
| Cancel path | done | cancel endpoint |
| Token budget enforce | done | L04 policy → AgentBudgetService |
| SSE chat stream | done | iter_chat_turn_sse + integration test |
| USD cost cap | done | max_cost_usd_month + fixture cost_usd |
| In-process trigger worker как prod executor | **P0 transitional** | skipped when Celery executor active; Kafka `dispatch` mode uses `claim_by_id` + `prodavan.jobs.dispatch_trigger` ([P0](../11-implementation-plan/P0-platform-infra.md)) |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_agent_*.py tests/integration/test_agent.py -q
```

## Оценка качества

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 1 | port+persist; real Cursor sidecar gap |
| B. Контракты | 1 | C-AGENT-* live subset |
| C. Инварианты | 1 | L03 resolve ban cli; MCP filter |
| D. As-built | 2 | эта карточка |
| **Quality (итог)** | **8** | doing |
