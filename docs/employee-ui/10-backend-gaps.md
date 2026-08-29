# 10 — Backend gaps (status)

| Gap | Endpoint / change | Status |
|-----|-------------------|--------|
| Self-service password | `PUT /me/password` | Implemented in employee-ui rebuild |
| Self-service email | `PATCH /me/contact-email` | Implemented |
| Project `about` | column + PATCH | Implemented |
| Project modules toggle | `GET/PATCH /projects/{id}/modules` | Implemented |
| rematerialize path | Flutter `/materialize` | Fixed |
| Employee key binding | `employee_ai_key_bindings` + `company_id` | Implemented |
| Cabinet key binding | `cabinet_ai_key_bindings` | Implemented |
| Project key binding | `project_ai_key_bindings` | Implemented |
| resolve_for_project wired | session + resume | Implemented |
| Scope filter on availability | `project_is_key_allowed` | Implemented |
| Platform key scope UI/API | `require_company_key_visible` | Implemented |
| Cascade on unbind | company/cabinet/project | Implemented |
| Metrics BC in API | Kafka ingest + Redis + REST | Implemented |
| k8s metrics-server | cluster addon (kube-system) | Required infra; see wsl-dev.md |
| Agent chat in Flutter | — | Deferred (PRODUCT.md) |
