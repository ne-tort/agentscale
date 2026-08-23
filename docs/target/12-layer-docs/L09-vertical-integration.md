# L09 — Vertical integration

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 6 |
| Quality note | API E2E + release_gate_check + suspend; Widget E2E — gap |
| Plan | [L09](../11-implementation-plan/L09-vertical-integration.md) |
| Last updated | 2026-08-23 — rematerialize-on-deploy + idle worker opt-in |
| Owners | — |

---

## Семантика

Сшивка слоёв: E2E smoke, metrics end-to-end. Не замена DoD L01…L08.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `test_e2e_smoke.py` — Admin→Company→Key→Cabinet→Project→Agent→metrics | Flutter widget/integration test |
| `POST /chat` + `GET .../chat/transcript` | |
| Metrics: `storage_bytes`, `last_activity_at` asserted in smoke | |
| Disabled employee 403 on chat | |
| `employee.disabled` platform event on disable | |
| Disabled AI key → `NO_AI_KEY` 404 | expired-by-date key path — done |
| AGENT_BUDGET 429 on chat follow-up | done | test_e2e_agent_budget_blocks_followup |
| Admin metrics list in smoke | done | GET /admin/metrics/companies |
| SSE chat stream in smoke | done | POST /chat/stream + meta/tabs + bundle import |
| `GET meta/tables/{slug}` in smoke | done | columns on table detail |
| CI nightly workflow | done | `.github/workflows/ci-nightly.yml` |
| Starter bundle import E2E | done | equipment-procurement → line_items + tab |
| USD cost cap E2E | done | max_cost_usd_month → AGENT_BUDGET |
| HITL tool approval E2E | done | dangerous: → deny/approve |
| `employees_active` in metrics | done | excludes disabled |
| Admin metrics: agent_tokens_used, agent_messages, projects_total | |
| Peer cabinet 403 in smoke | |
| Flutter ProjectWorkspacePage → SSE chat + transcript reload | |
| SSE cancel mid-stream → `(cancelled)` bubble + session cancel API | |
| Release gate checklist | live (subset) | `tools/release_gate_check.py` wired in ci-api |
| Company suspend E2E → COMPANY_SUSPENDED + platform_events | |

## Карта кода

```text
apps/api/tests/integration/test_e2e_smoke.py
apps/api/src/prodavan/api/v1/agent.py (chat + transcript)
apps/flutter/lib/features/employee/project_workspace_page.dart
apps/api/src/prodavan/application/admin/company_service.py (metrics)
apps/api/src/prodavan/api/v1/admin_metrics.py
tools/release_gate_check.py
.github/workflows/{ci-api,ci-nightly}.yml
```

## Gaps

| Требование | Статус |
|------------|--------|
| CI nightly | live | `.github/workflows/ci-nightly.yml` |
| Release gate checklist automation | live (subset) | `tools/release_gate_check.py` in ci-api |
| Widget E2E | hole | |
| Idle pause policy | live (subset) | policy + admin sweep + opt-in `IDLE_PAUSE_WORKER_ENABLED` |
| Rematerialize after MCP deploy | live | deploy/disable returns `rematerialized`; Flutter settings button |

## Quality | **6** | doing — E2E + release_gate_check; Widget E2E remains hole |
