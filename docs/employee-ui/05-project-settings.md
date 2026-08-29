# 05 — Project settings

`CabinetProjectSettingsPage` — после create или из списка.

## Поля

| Поле | API | UI |
|------|-----|-----|
| Name | `PATCH /projects/{id}` | `AppValuePreference` |
| About | `PATCH` `about` | multiline preference |
| Modules | `GET/PATCH .../modules` | multi-select cabinet modules |
| Creator | `owner_employee_id` / display | read-only |
| Runtime metrics | `runtime` in GET project | container runtime presenter |
| Agent provider | `agent_provider` | dropdown |
| AI key | `ai_key_id` (optional) | picker from available keys |
| Pause/Resume | `POST pause/resume` | toggle button «Запустить» / «Приостановить» |

## Pause semantics

- **Active + running** → «Приостановить проект»
- **Paused** → «Запустить проект» (resume + pod sync)

Agent chat workspace — **не** на этой странице (legacy удалён).
