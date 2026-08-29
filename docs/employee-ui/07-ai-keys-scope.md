# 07 — AI keys scope

## Предусловие: company-visible

Ключ виден компании, если:

- `owner_scope=company` и `owner_company_id = company_id`, **или**
- Admin-bound через `company_ai_key_bindings`.

Scope UI и `PUT scope-bindings` доступны для **любого** company-visible ключа (включая platform-bound, `writable=false`).

## Scope (сужение, опционально)

Если для пары `(key_id, company_id)` есть **хотя бы одна** scope-привязка (employee / cabinet / project), проект может использовать ключ только если:

```
bound_to(project.cabinet_id)
OR bound_to(project.owner_employee_id)
OR bound_to(project.id)
```

Если scope-привязок **нет** → ключ **company-wide** (legacy fallback для всех проектов компании).

## Tables

- `employee_ai_key_bindings(company_id, employee_id, key_id)` — unique `(company_id, employee_id, key_id)`
- `cabinet_ai_key_bindings(cabinet_id, key_id)` — фильтр по `cabinet.company_id` в API
- `project_ai_key_bindings(company_id, project_id, key_id)` — unique `(project_id, key_id)`

## Resolve at runtime

`AiKeysService.resolve_credentials_for_project(project)`:

1. Список company-visible ключей, прошедших `project_is_key_allowed`
2. Фильтр runtime-capable + provider
3. `resolved_ai_key_id` если задан и доступен в scoped set
4. Иначе первый доступный runtime-ключ из scoped set
5. Иначе `NO_AI_KEY`

Вызывается из `AgentSessionService.create_session` и `ProjectCommand.resume`.

Pod наследует ключ проекта (через `project_ai_key_bindings` + `projects.resolved_ai_key_id`).

## Cascade matrix

| Событие | Действия |
|---------|----------|
| Admin unbind company (`set_companies` −company) | Очистить scope bindings компании; `resolved_ai_key_id = NULL`; cancel sessions; **pause** active-проекты без runtime-ключа |
| Company −cabinet scope | **pause** active-проекты кабинета, потерявшие ключ |
| Company −employee scope | Только удалить binding; **не** pause, **не** трогать `resolved_ai_key_id` |
| Company −project scope | **pause** проект если `resolved_ai_key_id == key` и нет альтернативы; иначе clear override |
| Admin delete/disable key | `cascade_key_runtime_stop` + очистка всех scope tables |

## Company UI

- `CompanyAiKeyDetailPage`: multi-select employees + cabinets + **projects** для любого company-visible ключа
- `_writable` — CRUD полей ключа (company-owned only)
- `_scopeEditable` — scope bindings (company-visible, включая platform-bound)
