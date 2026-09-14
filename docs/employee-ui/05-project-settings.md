# 05 — Project settings

`CabinetProjectSettingsPage` — после create или из списка.

## Поля / подстраницы

| Поле | API | UI |
|------|-----|-----|
| Name | `PATCH /projects/{id}` | `AppValuePreference` на корне |
| **О проекте** | nav → `ProjectAboutPage` | Описание (`about`), Бюджет, Создатель (RO) |
| **Диалоги** | nav → `ProjectDialogsPage` (когда проект chat-ready) | таблица: title, Tokens, Requests; selection pin+delete; tap → chat settings |
| Modules | nav tile → `ProjectModulesListPage` | table on subpage; row → `ProjectModuleEditPage` |
| Провайдер AI | `PATCH` `resolved_ai_key_id` | табличный picker scoped AI-ключей |
| Metrics | `GET /projects/{id}/metrics` | `ProjectMetricsWrap` (usage: tokens, storage) |

## Диалоги

- `GET /projects/{id}/agent/sessions` — список с `agent_tokens_used` / `agent_requests`; create с optional `title`; `GET …/sessions/{sid}` — одна сессия + метрики.
- Pin — иконка в selection (не колонка); switch в chat settings.
- Tap row → `ProjectChatSettingsPage`: `SessionMetricsWrap` (tokens/requests) + title/pin/delete.
- Workspace AppBar title — бесшовный rename (`AppBarTitleEditor`, save on blur).

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

Подстраница **Контейнер** — `observed_state` (источник истины), live k8s phase, orchestrator status. CPU/RAM/Storage — StatTiles; при недоступных метриках warning-баннер, тайлы с warning-акцентом (контейнер не в error).

`observed_state=running` при k8s Ready. Метрики CPU/RAM — опционально (`metrics_available`); отсутствие metrics-server **не** переводит проект в error. Промежуточные: `preparing`, `provisioning`, `hydrating`, `starting`.

`project.error` — pod sync failed; agent triggers blocked (как paused). Recovery только через **Перезагрузить**.

Изменения модулей (`PATCH .../modules`) и метаданных **не** попадают в Pod до **Обновить проект**.

При **создании проекта** для модулей с профилями backend выставляет `project_ids` на профиль **Default** (по имени), иначе `is_default`, иначе первый по алфавиту — пока пользователь не переопределит.
