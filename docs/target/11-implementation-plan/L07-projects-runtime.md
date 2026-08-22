# L07 — Projects & runtime (container)

## Цель

Project unit внутри кабинета: CRUD, materialize workspace из cabinet, container lifecycle (create/pause/resume/delete), triggers bus foundation, chat attachments pipeline. Агент вызывается через порт L08 (здесь — wiring).

## Канон

- [06-projects-runtime/](../06-projects-runtime/) — [project-contract](../06-projects-runtime/project-contract.md), [container](../06-projects-runtime/container.md), [triggers](../06-projects-runtime/triggers.md), [chat-attachments](../06-projects-runtime/chat-attachments.md)
- [08 workspace-context](../08-agent-providers/workspace-context.md)
- Materialize source: L06 cabinet settings/packages

## Зависимости

| Нужно | Даёт |
|-------|------|
| L06 materialize hook + ACL; L01 headers `X-Project-Id` | Project runtime; `container_ref`; workspace FS layout |

L08 — consumer; L07 публикует cwd + mcp.json + lifecycle.

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `Project` entity | `(company_id, cabinet_id, owner_employee_id, …)` |
| Materialize result | `/workspace` layout: AGENTS.md, prompts, rules, skills, mcp.json, packages/, inbox, out |
| `container_ref` | pod id или `local-ws:{key}` |
| Lifecycle API | create/pause/resume/delete (+ idle policy hook) |
| Trigger ingress | chat message / webhook / cron → queue contract ([triggers](../06-projects-runtime/triggers.md)) |
| Attachments | upload → `attachment_refs` для ChatMessage |
| Event | `project.created` / `project.deleted` → cabinet |

## Изоляция

До k8s: `local-ws` полный layout + lifecycle state machine. Не ждать production scheduler, но **state machine и materialize** — полные.

## DoD

- [ ] Project CRUD только внутри accessible cabinet.
- [ ] Materialize идемпотентен; re-materialize после meta/MCP change.
- [ ] Layout соответствует container.md + workspace-context (provider dual-write где нужно).
- [ ] Pause/resume/delete не оставляют orphan secrets в env dumps.
- [ ] Triggers: минимум chat trigger → enqueue job (обработчик может звать L08).
- [ ] Attachments: store + ref; virus/size policy stub documented.
- [ ] Две шины: platform vs project triggers не смешаны (см. gap-map).

## Не считать готовым, если…

- Project = папка без DB entity/ACL.
- Materialize копирует git pack вместо cabinet DB/packages.
- «Контейнер потом», а agent пишет в общий диск компании.
- Нет delete/pause — только create.

## Exit gate

Fixture: create project → files on disk match contract → pause/delete clean. L08 получает cwd.
