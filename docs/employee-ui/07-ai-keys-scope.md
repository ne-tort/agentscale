# 07 — AI keys scope

## Правило доступности ключа в проекте

Ключ доступен для выбора, если:

```
company-visible(key, project.company_id)
AND (
  bound_to(project.owner_employee_id)
  OR bound_to(project.cabinet_id)
  OR company-wide binding (legacy)
)
```

## Tables

- `employee_ai_key_bindings(company_id, employee_id, key_id)`
- `cabinet_ai_key_bindings(cabinet_id, key_id)`

## Resolve at runtime

`AiKeysService.resolve_credentials_for_project(project)` — union A ∪ B ∪ C, filter provider, pick first or project.ai_key_id.

## Company UI

- `CompanyAiKeyDetailPage`: multi-select employees + cabinets (как companies picker у admin)
