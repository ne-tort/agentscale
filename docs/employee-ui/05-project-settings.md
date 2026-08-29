# 05 — Project settings

`CabinetProjectSettingsPage` — после create или из списка.

## Поля

| Поле | API | UI |
|------|-----|-----|
| Name | `PATCH /projects/{id}` | `AppValuePreference` |
| About | `PATCH` `about` | multiline preference |
| Modules | nav tile → `ProjectModulesListPage` | table on subpage; row → `ProjectModuleEditPage` |
| Creator | `created_by_login` | read-only |
| Провайдер AI | `PATCH` `resolved_ai_key_id` | табличный picker scoped AI-ключей; backend выставляет `agent_provider` из ключа |
| Metrics | `GET /projects/{id}/metrics` | `ProjectMetricsWrap` (usage: tokens, storage) |

## Провайдер AI

- Один nav-tile «Провайдер AI» → `ProjectAiKeySelectPage` (таблица: название, провайдер, подписка, radio).
- Без «Авто» и без отдельного dropdown codex/cursor/claude.
- 1 доступный ключ → auto-select при открытии settings.
- 2+ ключей → не выбран до явного выбора; label tile warning до выбора.

## Lifecycle кнопки

| Состояние | Кнопки |
|-----------|--------|
| всегда | **Модули** → подстраница со списком |
| `draft`, ключ задан | **Запустить проект** → `POST /launch` |
| запущен (`active`/`paused`/`error`) | **Контейнер** → `ProjectContainerPage` (`GET /container`, pod CPU/RAM) |
| pod unhealthy / `error` | tile «Контейнер» — danger color |
| `error` | **Перезагрузить** → `POST /reload` (rate limit Redis: 1/min, 3/30min) |
| Pod live, `active` | **Управление проектом** → `ProjectManagementPage`: **Приостановить проект** (warning), **Обновить**, **Сбросить агента** |
| Pod live, `paused` | **Возобновить проект** (warning) на settings; update/reset скрыты |

Подстраница **Контейнер** — `observed_state` (источник истины), live k8s phase, orchestrator status, CPU/RAM только при `running` (verified metrics). Без info-баннеров.

`observed_state=running` только при свежем sample CPU/RAM из metrics-server. Grace-период `starting` после k8s Ready. Промежуточные: `preparing` (materialize), `provisioning`, `hydrating` (initContainer).

`project.error` — pod sync failed; agent triggers blocked (как paused). Recovery только через **Перезагрузить**.

Изменения модулей (`PATCH .../modules`) и метаданных **не** попадают в Pod до **Обновить проект**.

При **создании проекта** для модулей с профилями backend выставляет `project_ids` на профиль **Default** (по имени), иначе `is_default`, иначе первый по алфавиту — пока пользователь не переопределит.

Agent chat workspace — **не** на этой странице (legacy удалён).
