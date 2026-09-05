# Prodavan — продукт

**Единственный источник правды по смыслу продукта.**  
Всё в [`target/`](target/) и старые `docs/01…10` — **legacy**, справочно (см. [LEGACY.md](LEGACY.md)).

## Что это

SaaS: **управление изолированными Pod'ами через UI**. Внутри каждого Pod — **рабочая среда агента**, не «чат с GPT».

| Не продукт | Продукт |
|------------|---------|
| Очередной ChatGPT-обёртка | Агент с **файлами, инструментами, долгим контекстом** в своём Pod |
| Абстрактная «иерархия BC» ради канона | UI → API → **Pod lifecycle** (create / pause / resume / kill) |
| Документ на 200 страниц «как AI видит enterprise» | Код + e2e backend + этот файл |

## Ядро

```text
Пользователь (UI)
    → API (pod-service, projects, auth, agent sessions, triggers)
    → Kubernetes Pod (prodavan-sandboxes)
        → agent-runtime (single container): proprietary SDK adapters + Platform OpenClaw
        → workspace FS, MCP/tools, файлы проекта
```

### Agent runtime (два класса)

| Класс | Смысл |
|-------|--------|
| **Проприетарные SDK** | Cursor, Codex, Claude Agent SDK — vendor coding harness через `AgentProviderPort` |
| **Platform OpenClaw** | **Наш** универсальный runtime: любой LLM из каталога `ai.http_providers`, полный tool loop, команды **только от приложения** |

Upstream [openclaw/openclaw](https://github.com/openclaw/openclaw) **не** dependency (без Telegram/WhatsApp/ bindings). Идеи gateway/session — да; их код — нет.  
Подробно: [06-agent-runtime/platform-openclaw-runtime.md](06-agent-runtime/platform-openclaw-runtime.md).

1. **Pod** — единица изоляции на Project (1:1). Реальный k8s Pod с volume, hydrate, initContainer.
2. **UI** — список контейнеров/проектов, статус, pause/resume, вход в workspace агента (не модалка-чат).
3. **Agent inside Pod** — провайдер через `AgentProviderPort` (SDK **или** Platform OpenClaw), работа с **файлами**, tool calls, HITL; не thin wrapper над completions API.

### Chat UX (project workspace)

- **Multi-chat** — несколько agent sessions на project; selected project и pin — **per employee**. Rail: peer блок чатов («Новый диалог» → pins → чаты проекта). «Новый диалог» **скрыта**, пока selected project не `active` **и** container `observed_state=running` (`new_chat_enabled`); draft/paused/error/запуск — без кнопки. После launch/resume/reload UI обновляет rail. Settings «Чат» — только при sendable (active+running). Таблица проектов: about / chats / budget (stub). Workspace только с явным `sessionId`.
- **Cabinet hubs** — «Управление» (`nav.placement: management`) и «Данные» (`data`) — страницы-хабы как peer к Projects; **скрыты**, если ни один модуль не подключил вкладку. Промпты/MCP/Файлы — preference tiles (уникальные иконки + subtitle).
- **Live streaming** — assistant text из SSE `text_delta` без full reload после turn; ingress нормализует cumulative/overlap SDK deltas в incremental.
- **Block-based transcript** — `GET /chat/transcript?session_id=` → `{ blocks: [...] }` (user, assistant_markdown, tool_*, subagent, plan, thinking, usage).
- **Cursor-style rendering** — assistant inline без bubble; reasoning/tools — muted underlined lines + inset panel; tools paired by id; consecutive thinking → один spoiler; tool expand с path/`+N −M`.
- **Scroll** — plain chronological `ListView` (no reverse, no auto pin/jump on stream); `loadOlder` only when user scrolls near top.
- **Selection** — one `SelectionArea` over transcript; plain `Text` / non-selectable markdown → cross-paragraph copy without markdown junk.
- **Markdown** — GFM after turn done; LLM pipe-tables normalized (`||` rows, missing separators); plain text while streaming.
- **Chat settings** — model picker + title rename + pin (preference pages, no modal dialogs); Enter отправляет, Shift+Enter — новая строка; usage collapsed по умолчанию.
- **Responsive** — mobile full-width; tablet/desktop center column (768–900px); на узком shell чаты — отдельная страница (не bottom sheet).
- **Subagents** — `subagent_*` events + sidechain transcript API.

## Observability

- **k8s metrics-server** — cluster addon для CPU/RAM sandbox pod'ов; Prodavan не деплоит отдельный metrics microservice.
- **Metrics BC** (`application/metrics/`) — внутри `prodavan-api`: Kafka consumer, Redis (presence + pod samples), REST для admin/company/employee container UI.
- **Project lifecycle** — статусы `draft` | `active` | `paused` | `error` | `completed`. `error` = pod не поднялся; recovery через `POST /projects/{id}/reload` (Redis rate limit, fail-closed).
- **Agent sessions** — pause **suspend** (recoverable); resume/reload reactivate ту же PG-сессию и re-register bridge. История чата в Postgres сохраняется.
- **Workspace sync** — rematerialize модулей **только** явным `POST /projects/{id}/sync` («Обновить проект») или при launch. Изменения cabinet/module data помечают `workspace_outdated_at` до sync.

## Что уже в коде (as-built)

Смотреть **код и тесты**, не legacy-канон:

| Область | Где |
|---------|-----|
| Pod lifecycle (stub + k8s) | `apps/api/src/prodavan/application/pod_service/` |
| K8s adapter | `infrastructure/k8s/pod_runtime.py`, overlay `infra/k3s/overlays/e2e/` |
| Backend e2e (API, не UI) | `apps/api/tests/integration/`, `tests/e2e/k8s/`, `tests/e2e/live/` |
| Agent + chat + files | `application/agent/`, `api/v1/agent.py`, content/assets |
| Flutter UI (частично) | `apps/flutter/lib/features/` |
| **Employee UI канон** | [`employee-ui/README.md`](employee-ui/README.md) |
| As-built слои | [`target/12-layer-docs/`](target/12-layer-docs/) — **описание кода**, не закон |

**Dev deployment** (`infra/k3s/overlays/dev/`): `POD_RUNTIME_MODE=k8s`, real Pod'ы в `prodavan-sandboxes`. После merge → CI Images → Argo sync.

## Инфра (актуально)

GitOps, k3s, CI — не legacy: [`07-infrastructure/runbook.md`](07-infrastructure/runbook.md), [`07-infrastructure/e2e.md`](07-infrastructure/e2e.md).

## Правила для агентов и PR

1. Новые **продуктовые** требования — дополнять **этот файл** или as-built в `12-layer-docs`, не раздувать `target/01…15`.
2. **`docs/target/`** (кроме `12-layer-docs`, `07-infrastructure` cross-links) — **не канон**, не блокировать работу «gap map».
3. E2E — **backend/API**; Flutter E2E не обязателен для merge.
4. Приоритет фич: **Pod UI + agent-in-pod с файлами** > admin metrics > meta-syntax > прочий канонный шум.

## Module data model (base modules)

```text
Template (modules + module_meta_documents)
  → fork on bind → Module Instance (meta + data JSONB)
       Admin/platform → Company → Cabinet → Project (leaf)
  → Materialize reads project instance → workspace files
```

| Layer | What |
|-------|------|
| **Template** | Catalog module meta (`modules` + `module_meta_documents`) — source for first platform instance |
| **Instance** | Independent copy: `module_instances` + `module_instance_meta_documents` + `module_instance_data_rows` (Postgres JSONB). Owner: `platform` / `company` / `cabinet` / `project` |
| **Bind** | Creates a **fork** of parent instance (deep copy meta+data); further edits stay in the child |
| **Project hubs** | Employee Management/Data UI = selected project’s leaf instances (`/projects/{id}/runtime-modules`); hubs stay visible with CTA when no project selected |
| **Materialize data** | Row/profile content from **project** instance (leaf) on launch/sync |
| **Materialize rules** | Rule definitions still from **template** meta slug `materialize` (MVP); instance-level rules later |
| **Admin/company edit** | Template catalog PUT mirrors into platform/company **instance** meta; seed upsert refreshes platform instance meta only (children untouched) |

Product module seed changes (`PRODUCT_MODULES`) ship only via Alembic calling `upsert_product_modules` — not silent bootstrap overwrite.

**Legacy note:** older `cab_inst_*.module_data_rows` + row-level `project_ids` filtering remain as migration/read fallback only — no new writes. Isolation is per-project instances, not shared cabinet rows filtered by `project_ids`.

**Mongo backlog:** optional later ADR to move `module_instance_data_rows` into Mongo with `instance_id` pointers in Postgres — not default until copy-on-bind is stable on Postgres.

Module-level **MP binding** (`module_project_bindings`): if bindings exist, module materializes only for bound projects.

Future base modules follow the same cascade: edit in the owner’s instance, fork on bind down the chain, materialize from the project leaf.

**File & env pipeline (meta-syntax spec):** upload via Content Service → FileRef in row → materialize (`copy_blob` / `raw`) → Pod `/workspace`; container env and Vault secrets — declarative slugs, implementation backlog. See [12-content-file-pipeline](target/06-modules/meta-syntax/12-content-file-pipeline.md) · [13-container-env-secrets](target/06-modules/meta-syntax/13-container-env-secrets.md) · [gap map P-META-*](target/09-gap-map.md).

## Project lifecycle (employee UI)

- **Create** (`draft`) — DB record + project settings only; no Pod, no workspace files.
- **Launch** — first materialize + Pod provision (`POST /projects/{id}/launch`); requires agent provider + AI key. On pod failure → `error` (not silent rollback).
- **Reload** — restart pod workload (`POST /projects/{id}/reload`); Redis rate limit 1/min, 3/30min; available anytime (UI shows button on settings only when `error`).
- **Container UI** — employee subpage with live `observed_state` (`GET /projects/{id}/container`, `/container/metrics`). **`running`** = k8s Ready (phase Running + readiness); CPU/RAM metrics are display-only (`metrics_available` flag + warning banner when absent).
- **Workspace files** — when Pod is `running`, UI **Files** opens live `/workspace` tree (list, text preview, download) via k8s exec; no MinIO fallback when paused.
- **Sync** — apply module binding changes to existing Pod workspace (`POST /projects/{id}/sync`); module folder prune + hydrate.
- **Agent reset** — cancel sessions + purge chat history (`POST /projects/{id}/agent/reset`); lives in agent BC, not pod_service.
