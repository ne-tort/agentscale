# L09 — Vertical integration

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | API E2E incl. pause/rematerialize/idle; Flutter thumbnails + widget subset; full Widget E2E — gap |
| Plan | [L09](../11-implementation-plan/L09-vertical-integration.md) |
| Last updated | 2026-08-24 — regenerate after cancel; release_gate Status=done⇒Q≥8 |
| Owners | — |

---

## Семантика

Сшивка слоёв: E2E smoke, metrics end-to-end. Не замена DoD L01…L08.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `test_e2e_smoke.py` — Admin→Company→Key→Cabinet→Project→Agent→metrics | full Flutter navigation E2E |
| `POST /chat` + `GET .../chat/transcript` | |
| Metrics: `storage_bytes`, `last_activity_at` asserted in smoke | |
| Disabled employee 403 on chat | |
| `employee.disabled` platform event on disable | |
| Disabled AI key → `NO_AI_KEY` 404 | expired-by-date key path — done |
| AGENT_BUDGET 429 on chat follow-up | done | test_e2e_agent_budget_blocks_followup |
| Admin metrics list in smoke | done | GET /admin/metrics/companies |
| SSE chat stream in smoke | done | POST /chat/stream + meta/tabs + bundle import |
| `GET meta/tables/{slug}` in smoke | done | columns on table detail |
| CI nightly workflow | done | `.github/workflows/ci-nightly.yml` — `pytest -m integration` |
| CI opt-in E2E (label `e2e`, `[e2e]` title) | done | `.github/workflows/ci-e2e.yml` — L2/L3a/L3b |
| Unified pytest markers (`integration`, `k8s`, `live`) | done | `apps/api/pyproject.toml`, `docs/07-infrastructure/e2e.md` |
| Starter bundle import E2E | done | equipment-procurement → line_items + tab |
| USD cost cap E2E | done | max_cost_usd_month → AGENT_BUDGET |
| HITL tool approval E2E | done | dangerous: → deny/approve |
| `employees_active` in metrics | done | excludes disabled |
| Admin metrics: agent_tokens_used, agent_messages, projects_total | |
| Peer cabinet 403 in smoke | |
| Flutter ProjectWorkspacePage → SSE chat + transcript reload | |
| Flutter widget tests — status banner + image/text/PDF preview | full navigation E2E — hole |
| Flutter admin widget tests — metrics alerts | full admin navigation — hole |
| SSE cancel mid-stream → `(cancelled)` bubble + session cancel API | |
| Release gate checklist | live (subset) | `tools/release_gate_check.py` — Quality drift + Status=done⇒Q≥8; wired in ci-api |
| Company suspend E2E → COMPANY_SUSPENDED + platform_events | |
| Project pause E2E → PROJECT_PAUSED + resume | |
| MCP deploy rematerialize E2E | |
| Idle pause sweep E2E (policy + admin sweep) | |
| Attachment content download E2E (incl. read while paused) | |
| Text/JSON attachment content-type + download E2E | |
| Platform idle-pause sweep-all E2E | |
| Paused blocks triggers / allows PATCH E2E | |
| Paused blocks session create; pause auto-cancels ACTIVE; cancel + leave-queued after resume | |

## Карта кода

```text
apps/api/tests/integration/test_e2e_smoke.py
apps/api/tests/e2e/k8s/test_pod_k8s_runtime.py
apps/api/tests/e2e/live/test_containers_live.py
apps/api/src/prodavan/api/v1/agent.py (chat + transcript)
apps/flutter/lib/features/employee/project_workspace_page.dart
apps/flutter/test/employee_widgets_test.dart
apps/api/src/prodavan/application/admin/company_service.py (metrics)
apps/api/src/prodavan/api/v1/admin_metrics.py
tools/release_gate_check.py
docs/target/12-layer-docs/ops-cron-hooks.md
.github/workflows/{ci-api,ci-nightly,ci-e2e}.yml
docs/07-infrastructure/e2e.md
```

## Gaps

| Требование | Статус |
|------------|--------|
| CI nightly | live | `.github/workflows/ci-nightly.yml` — `-m integration` |
| CI opt-in cluster e2e | live | `.github/workflows/ci-e2e.yml` — label `e2e` / `[e2e]` |
| K8s pod e2e (in-cluster Job) | live | `tests/e2e/k8s/`, `infra/k3s/overlays/e2e/` |
| Live API e2e | live | `tests/e2e/live/` |
| Release gate checklist automation | live (subset) | `tools/release_gate_check.py` in ci-api |
| Widget E2E | hole (subset) | `employee_widgets_test.dart` — banner + chip; no full shell navigation |
| Idle pause policy | live (subset) | policy + company/platform admin sweep + opt-in worker; ops curl in `ops-cron-hooks.md` |
| Rematerialize after MCP deploy | live | deploy/disable returns `rematerialized`; Flutter settings button |

## Quality | **7** | doing — vertical E2E + Flutter viewer widgets; full shell Widget E2E remains hole |
