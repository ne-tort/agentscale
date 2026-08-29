# 05 — Project settings

`CabinetProjectSettingsPage` — после create или из списка.

## Поля

| Поле | API | UI |
|------|-----|-----|
| Name | `PATCH /projects/{id}` | `AppValuePreference` |
| About | `PATCH` `about` | multiline preference |
| Modules | `GET/PATCH .../modules` | multi-select cabinet modules |
| Creator | `created_by_login` | read-only |
| Agent provider | `agent_provider` | dropdown |
| AI key | `resolved_ai_key_id` | picker from available keys |

## Lifecycle кнопки

| Состояние | Кнопки |
|-----------|--------|
| `draft`, нет Pod, провайдер+ключ заданы | **Запустить проект** → `POST /launch` |
| `draft`, не настроен | подсказка «Укажите провайдер и ключ» |
| Pod есть, `active` | **Приостановить проект** → `POST /pause` |
| Pod есть, `paused` | **Возобновить проект** → `POST /resume` |
| Pod есть | **Обновить проект** → `POST /sync` (materialize + hydrate) |
| Pod есть | **Сбросить агента** → `POST /agent/reset` (agent BC, не pod_service) |

Изменения модулей (`PATCH .../modules`) и метаданных **не** попадают в Pod до **Обновить проект**.

Agent chat workspace — **не** на этой странице (legacy удалён).
