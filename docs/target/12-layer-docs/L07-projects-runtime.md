# L07 — Projects & runtime (container)

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 6 |
| Quality note | Project CRUD+lifecycle+materialize FS+triggers+attachments; k8s pod sandbox L08; AGENTS from UI — gap |
| Plan | [L07](../11-implementation-plan/L07-projects-runtime.md) |
| Canon | [06-projects-runtime](../06-projects-runtime/), [workspace-context](../08-agent-providers/workspace-context.md) |
| Last updated | 2026-08-23 — L07 API + local-ws materialize |
| Owners | — |

---

## Семантика

Project = workspace + `local-ws:{key}` container ref внутри CabinetInstance. Materialize из cabinet meta + enabled MCP packages. Triggers — project-scoped queue; attachments → inbox.

## Что сделано

| Сделано | Gaps |
|---------|------|
| ORM projects / project_triggers / project_attachments + migration | k8s pod scheduler |
| CRUD: create/list/get; pause/resume/delete | AgentProviderPort wiring (L08) |
| Materialize: AGENTS.md + CLAUDE.md dual-write, mcp.json, packages/, inbox/out | AGENTS text from cabinet UI meta |
| `container_ref=local-ws:{workspace_key}` | Sandbox MCP process start |
| Triggers: enqueue + list (`chat.message`, `project.prepare`, …) | Background worker loop |
| Attachments: base64 upload → inbox + DB ref | Virus scan; company policy limits |
| Integration tests lifecycle + FS layout | E2E with agent ping |

## Как сделано

1. `ProjectService` — cabinet ACL via L06; create → materialize → `project.prepare` trigger.
2. `ProjectMaterializeService` — idempotent FS under `storage/projects/{workspace_key}/workspace/`.
3. `WorkspaceLayoutWriter` — container.md layout; extracts enabled package zips.
4. HTTP: `/cabinets/{id}/projects`, `/projects/{id}/*` per project-contract.
5. L06 `materialize-stub` → real FS (status `materialized`).

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-PROJECT | entity + lifecycle API | **live** (subset) |
| C-MATERIALIZE | FS layout + paths | **live** (local-ws; no pod) |
| C-TRIGGERS | enqueue + list | **live** (subset; no worker) |
| C-ATTACH | upload + storage_ref | **live** (subset) |

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
apps/api/tests/integration/test_projects.py
apps/api/tests/unit/test_projects_domain.py
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Project CRUD in cabinet | done | |
| Materialize layout | done | default AGENTS; UI meta later |
| Pause/resume/delete | done | local-ws only |
| Trigger dispatch to agent | done | POST triggers/dispatch → AgentTriggerDispatcher |
| MCP package sandbox run | hole | extract only |
| Platform vs project event bus split | partial | project_triggers table only |
| Attachment virus/size policy | stub | ATTACHMENT_MAX_BYTES constant |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_projects_domain.py tests/integration/test_projects.py -q
```

## Оценка качества

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 1 | core API+FS; worker/pod gaps |
| B. Контракты | 1 | C-PROJECT/MATERIALIZE live subset |
| C. Инварианты и проверки | 1 | ACL + lifecycle tests |
| D. As-built ясность | 2 | эта карточка |
| **Quality (итог)** | **6** | doing; sandbox MCP run gap |
