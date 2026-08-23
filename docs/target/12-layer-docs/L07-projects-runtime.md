# L07 — Projects & runtime (container)

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Project CRUD+lifecycle+materialize+local MCP spawn+trigger drain/worker; k8s isolator — gap |
| Plan | [L07](../11-implementation-plan/L07-projects-runtime.md) |
| Canon | [06-projects-runtime](../06-projects-runtime/), [workspace-context](../08-agent-providers/workspace-context.md) |
| Last updated | 2026-08-24 — rematerialize while paused + k8s CronJob manifests |
| Owners | — |

---

## Семантика

Project = workspace + `local-ws:{key}` container ref внутри CabinetInstance. Materialize из cabinet meta + enabled MCP packages. Triggers — project-scoped queue; attachments → inbox.

## Что сделано

| Сделано | Gaps |
|---------|------|
| ORM projects / project_triggers / project_attachments + migration | k8s pod scheduler |
| CRUD: create/list/get/PATCH (name, agent_provider); pause/resume/delete | |
| Materialize: AGENTS from cabinet workspace-docs + packages/sandbox | bubblewrap/k8s isolator |
| `container_ref=local-ws:{workspace_key}` | |
| Triggers: enqueue + list + dispatch + signed webhook/telegram ingress + admin drain + worker | External broker (Kafka/SQS) |
| Outbox-lite: `attempts` / `lease_until` / `available_at` / `last_error` + SKIP LOCKED claim | |
| Platform events bus + cabinet SPI deliver (audit) | MCP stdio handler protocol; bubblewrap |
| Attachments: upload/list/delete/download + ref validation; size/type/magic policy + MIME sniff (+`.json`) | Full AV; real PDF renderer |
| Idle pause: `idle_pause_after_hours` (default off) + admin company/platform sweep + opt-in worker + k8s CronJob examples | External broker |
| MCP package deploy/disable → rematerialize cabinet projects | |
| Integration tests lifecycle + FS layout + provider patch + admin drain | E2E with agent ping |

## Как сделано

1. `ProjectService` — cabinet ACL via L06; create → materialize → `project.prepare` trigger; PATCH name/agent_provider.
2. `ProjectMaterializeService` — idempotent FS under `storage/projects/{workspace_key}/workspace/`.
3. `WorkspaceLayoutWriter` — container.md layout; extracts enabled package zips.
4. HTTP: `/cabinets/{id}/projects`, `/projects/{id}/*` per project-contract; `POST /admin/triggers/drain`.
5. L06 `materialize-stub` → real FS (status `materialized`).
6. Opt-in trigger worker (`TRIGGER_WORKER_ENABLED`) — in-process asyncio + `pg_try_advisory_lock` + row lease/SKIP LOCKED; hole: no external broker.
7. `PlatformEventService` — subscription transitions + SPI fan-out. Package `platform_events` + opt-in `MCP_PLATFORM_EVENT_INVOKE` runs `src/on_platform_event.py` (stdin JSON event, optional stdout JSON `result`); full MCP stdio — hole.
8. `ProjectTriggerService.enqueue` + drain — `COMPANY_SUSPENDED` for runtime kinds; `project.prepare` exempt; queued triggers → `failed` on drain; dispatch errors → retry with backoff until max attempts.
9. Project GET/list includes `company_subscription` read model (L04 → L05).
10. Signed webhook ingress `POST .../webhooks/http` with company `webhook_hmac_secret` (not returned in GET; `webhook_hmac_configured` flag).
11. Signed telegram ingress `POST .../webhooks/telegram` with company `telegram_hmac_secret` (`telegram_hmac_configured` flag).
12. Trigger outbox lease columns (migration `2026082315`) — claim increments `attempts`, sets `lease_until`; crash → lease expiry → re-claim.
13. Lazy `company.suspended` emit commits inside `CompanySubscriptionGate` (no session auto-commit — SSE keeps the session open).
14. Idle pause — `idle_pause_after_hours` (0/None=off); admin company + platform sweep; opt-in worker; CronJob examples in `deploy/k8s/cron/`.
15. Attachment upload sniffs PNG/JPEG/GIF/PDF/ZIP magic for `content_type`; allowlist includes `.json`.
16. MCP package deploy/disable rematerializes all non-deleted projects in the cabinet (L09 DoD).
17. `GET .../attachments/{id}/content` — inline bytes for Flutter image/text preview (ACL read; works while paused).
18. Manual rematerialize allowed while project is paused (maintenance; chat/upload still blocked).

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-PROJECT | entity + lifecycle API | **live** (subset) |
| C-MATERIALIZE | FS layout + paths | **live** (local-ws; no pod) |
| C-TRIGGERS | enqueue + list + dispatch/drain + admin drain + opt-in worker + outbox lease | **live** (subset; outbox-lite) |
| C-ATTACH | upload + list + download + storage_ref validation on chat | **live** (subset) |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-INSTANCE / C-MCP-PKG | L06 | live |
| C-HEADERS | L01 | live |

## Связи

cwd/mcp.json → L08 AgentPort. Chat UI → triggers (L05/L09).

## Инварианты

- Project только в accessible cabinet (peer isolation).
- Materialize идемпотентен (re-materialize overwrites layout).
- Delete purges workspace tree; pause keeps volume.
- Project triggers scoped to `project_id`.

## Карта кода

```text
apps/api/src/prodavan/
  domain/projects/types.py
  application/projects/{project_service,materialize,trigger_service,attachment_service,access}.py
  infrastructure/projects/workspace.py
  infrastructure/persistence/models/projects.py
  api/v1/projects.py
apps/api/alembic/versions/2026082306_projects.py
apps/api/alembic/versions/2026082312_platform_events.py
apps/api/alembic/versions/2026082315_trigger_outbox_lease.py
apps/api/tests/integration/test_projects.py
apps/api/tests/unit/test_projects_domain.py
apps/api/tests/unit/test_trigger_outbox.py
docs/target/12-layer-docs/ops-cron-hooks.md
deploy/k8s/cron/ops-hooks.yaml
apps/api/.env.example
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Project CRUD in cabinet | done | |
| Materialize layout | done | AGENTS from `meta_workspace_docs` (slug=agents); empty → default |
| Pause/resume/delete | done | local-ws only |
| Trigger dispatch to agent | done | chat.message + chat.regenerate; schedule/webhook ack or run-if-text; advisory lock + row lease |
| MCP package sandbox run | live (subset) | prepare + opt-in local spawn (`MCP_SANDBOX_SPAWN`); k8s/bubblewrap — hole |
| Platform vs project event bus split | live (subset) | fan-out; zip handler stdin JSON + optional stdout JSON result; full MCP stdio — hole |
| Attachment refs scoped to project | done | normalize id/storage_ref before agent send |
| Attachment virus/size policy | live (subset) | max_attachment_mb + extension + magic sniff; AV — hole |
| Project preferred_provider | done | `agent_provider` create/PATCH; resolve uses project override |
| Attachment preview in chat UI | live (subset) | image + text/JSON selectable preview + PDF stub; real PDF renderer — hole |
| telegram.message trigger | done | dispatch like chat.message; HMAC ingress like webhook |
| Webhook HMAC ingress | done | company policy secret + X-Prodavan-Signature |
| Attachment DELETE | done | DB + inbox file; Flutter pending remove calls DELETE |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_projects_domain.py tests/integration/test_projects.py -q
```

## Оценка качества

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 1 | core API+FS+worker opt-in; pod gap |
| B. Контракты | 2 | C-PROJECT/MATERIALIZE/TRIGGERS live subset |
| C. Инварианты и проверки | 1 | ACL + lifecycle + patch/drain tests |
| D. As-built ясность | 2 | эта карточка |
| **Quality (итог)** | **7** | doing; k8s isolator + external broker gaps |
