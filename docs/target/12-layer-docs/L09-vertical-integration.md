# L09 — Vertical integration

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 5 |
| Quality note | API E2E smoke + chat/transcript + disabled employee/key; Flutter reload; CI/release — gap |
| Plan | [L09](../11-implementation-plan/L09-vertical-integration.md) |
| Last updated | 2026-08-23 — bundle export/import in smoke |
| Owners | — |

---

## Семантика

Сшивка слоёв: E2E smoke, metrics end-to-end. Не замена DoD L01…L08.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `test_e2e_smoke.py` — Admin→Company→Key→Cabinet→Project→Agent→metrics | Flutter widget/integration test |
| `POST /projects/{id}/chat` + `GET .../chat/transcript` | storage_bytes, last_activity |
| Disabled employee 403 on chat | |
| Disabled AI key → `NO_AI_KEY` 404 | expired-by-date key path — done |
| AGENT_BUDGET 429 on chat follow-up | done | test_e2e_agent_budget_blocks_followup |
| Admin metrics list in smoke | done | GET /admin/metrics/companies |
| SSE chat stream in smoke | done | POST /chat/stream + meta/tabs + bundle import |
| `GET meta/tables/{slug}` in smoke | done | columns on table detail |
| CI nightly workflow | done | `.github/workflows/ci-nightly.yml` |
| `employees_active` in metrics | done | excludes disabled |
| Admin metrics: agent_tokens_used, agent_messages, projects_total | |
| Peer cabinet 403 in smoke | |
| Flutter ProjectWorkspacePage → SSE chat + transcript reload | SSE cancel mid-stream |

## Карта кода

```text
apps/api/tests/integration/test_e2e_smoke.py
apps/api/src/prodavan/api/v1/agent.py (chat + transcript)
apps/flutter/lib/features/employee/project_workspace_page.dart
apps/api/src/prodavan/application/admin/company_service.py (metrics)
apps/api/src/prodavan/api/v1/admin_metrics.py
```

## Gaps

| Требование | Статус |
|------------|--------|
| CI nightly | live (subset) | integration pytest on schedule |
| Widget E2E | hole |
| Release gate checklist automation | hole |

## Quality | **5** | doing — API vertical + Flutter chat subset |
