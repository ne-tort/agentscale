# Usage, limits, billing signals

## Цель

Считать потребление для Admin monitoring и лимитов Company; не путать **token counters**, **vendor $ estimates** и **наш биллинг**.

## Канонические метрики (platform)

Писать в `AgentUsageEvent` (или эквивалент) на каждый turn/run:

| Field | Обязательность | Источник |
|-------|----------------|----------|
| `company_id`, `project_id`, `employee_id` | yes | session |
| `provider`, `api_kind`, `model_id` | yes | CreateOpts |
| `input_tokens`, `output_tokens` | when available | adapter |
| `reasoning_tokens`, `cache_read`, `cache_write` | optional | Cursor TokenUsage |
| `cost_usd_vendor` | optional | Cursor `getUsage`; Claude estimate; OpenAI usage |
| `cost_usd_authoritative` | optional | Vendor billing API (async job) |
| `run_id`, `agent_id` | yes | SDK |
| `at` | yes | |

Адаптер **обязан** эмитить `AgentEvent.type=usage` когда vendor отдал counts ([adapter-port](adapter-port.md)).

## По провайдерам

### Cursor

| API | Что даёт |
|-----|----------|
| Stream `usage` / `run.usage` / `result.usage` | `TokenUsage` (tokens; **не** $) |
| `agent.getUsage()` / `Agent.getUsage(id)` | Billed tokens + **dollar cost** (+ per-run/per-turn breakdown) |
| Dashboard | Team usage under SDK tag |

Prodavan: на `done` вызывать `getUsage()` (best-effort); не блокировать UX при ошибке fetch.

### Codex / OpenAI

- Трекать usage из stream/API если SDK отдаёт; иначе периодический sync Usage API OpenAI по `api_key` org.
- Sandbox/approvals на usage не влияют.

### Claude Agent SDK

| Field | Надёжность |
|-------|------------|
| Per-message `usage` tokens | Хорошо для metering |
| `total_cost_usd` / `costUSD` | **Estimate** (client price table) — не для биллинга клиентов |
| `maxBudgetUsd` | Client stop vs estimate — можно как soft guard |
| Authoritative | [Usage and Cost API](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api) / Console |

## Лимиты (Admin)

| Limit | Scope | Enforcement |
|-------|-------|-------------|
| `max_tokens_month` | Company | Soft warn + hard block new runs |
| `max_cost_usd_month` | Company | Same; prefer authoritative when available |
| `max_concurrent_runs` | Company / Employee | Orchestrator gate |
| `max_budget_per_run` | Project | Pass to Claude `maxBudgetUsd`; Cursor/Codex — cancel when our counter exceeds |

При hard limit: session start → `QUOTA_EXCEEDED`; in-flight → cancel + event.

## Admin UI

- Overview: spend/tokens by company, provider, model.
- Drill-down: project / employee.
- Key health: last usage, errors, renewals ([02](../02-ai-provider-keys/)).

Не показывать vendor estimate как «факт $» без пометки.
