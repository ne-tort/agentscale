# 05 — Project settings

`CabinetProjectSettingsPage` — после create или из списка.

## Поля

| Поле | API | UI |
|------|-----|-----|
| Name | `PATCH /projects/{id}` | `AppValuePreference` |
| About | `PATCH` `about` | multiline preference |
| Modules | `GET/PATCH .../modules` | table: name, profile, checkbox; tap → module properties + profile pick (`project_ids`) |
| Creator | `created_by_login` | read-only |
| Провайдер AI | `PATCH` `resolved_ai_key_id` | табличный picker scoped AI-ключей; backend выставляет `agent_provider` из ключа |
| Metrics | `GET /projects/{id}/metrics` | `ProjectMetricsWrap` вверху страницы |

## Провайдер AI

- Один nav-tile «Провайдер AI» → `ProjectAiKeySelectPage` (таблица: название, провайдер, подписка, radio).
- Без «Авто» и без отдельного dropdown codex/cursor/claude.
- 1 доступный ключ → auto-select при открытии settings.
- 2+ ключей → не выбран до явного выбора; label tile warning до выбора.

## Lifecycle кнопки

| Состояние | Кнопки |
|-----------|--------|
| `draft`, нет Pod, ключ задан | **Запустить проект** → `POST /launch` |
| `draft`, не настроен | подсказка «Укажите провайдер AI» |
| Pod есть, `active` | **Приостановить проект** → `POST /pause` |
| Pod есть, `paused` | **Возобновить проект** → `POST /resume` |
| Pod есть | **Обновить проект** → `POST /sync` (materialize + hydrate) |
| Pod есть | **Сбросить агента** → `POST /agent/reset` (agent BC, не pod_service) |

Изменения модулей (`PATCH .../modules`) и метаданных **не** попадают в Pod до **Обновить проект**.

Agent chat workspace — **не** на этой странице (legacy удалён).
