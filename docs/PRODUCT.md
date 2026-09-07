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
- **Cabinet hubs** — «Управление» (`nav.placement: management`) и «Данные» (`data`) — страницы-хабы как peer к Projects; **скрыты**, если ни один модуль не подключил вкладку. Промпты/MCP/Файлы — preference tiles в «Управление»; **Подбор техники** (`mod_equipment`) — hub в «Данные».
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
- **Metrics BC** (`application/metrics/`) — Kafka-first read-model внутри `prodavan-api`: facts (`metrics.usage.turn`, `metrics.counter.delta`, `metrics.storage.snapshot`, presence heartbeat) + `relation.*` links → Redis counters/cascade; overview REST читает store (не live SQL/FS scan для requests/tokens/storage). Presence: login/refresh/logout + cabinet heartbeat. Backfill: `POST /admin/metrics/rebuild`. Series: `GET /admin/metrics/series` / `GET /cabinets/{id}/metrics/series`.
- **Project lifecycle** — статусы `draft` | `active` | `paused` | `error` | `completed`. `error` = fatal pod failure after grace; **node/WSL flap** (`NotFound` / transient Unknown при `desired=RUNNING`) не залипает в `error` сразу — grace + reconcile **auto-reprovision**; recovery также через `POST /projects/{id}/reload`. Chat readable для `active|paused|error`; send только при `active` + `observed_state=running`; иначе composer wake (tap → resume/reload).
- **Agent sessions** — pause **suspend** (recoverable); resume/reload reactivate ту же PG-сессию и re-register bridge. История чата в Postgres сохраняется.
- **Chat durability** — SoT UI-транскрипта = **Postgres** (`agent_sessions` / `agent_events`), не MinIO. `user_message` коммитится сразу; stream events flush mid-turn; финальный commit на DONE. Crash mid-turn не должен съедать уже записанный user turn.
- **Workspace durability** — MinIO `projects/{workspace_key}/workspace/` = last-good tree; live Pod `/workspace` = emptyDir. После успешного agent turn и **перед pause** API делает **dehydrate** (pod→MinIO overwrite). Resume/reload hydrate MinIO→emptyDir. Без dehydrate правки агента на диске Pod терялись при pause/reload/flap.
- **Workspace sync (modules)** — rematerialize модулей **только** явным `POST /projects/{id}/sync` («Обновить проект») или при launch. Изменения cabinet/module data помечают `workspace_outdated_at` до sync.

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
| **Project hubs** | Employee Management/Data UI. Tab `instance_owner` selects data API: `cabinet` = cabinet instance (no project required); `project` = selected project leaf (reload on project change) |
| **Materialize data** | Default: project leaf. Modules with `instance_owner: cabinet` (prompts / MCP / files) read **cabinet** instance rows and filter by row `project_ids` |
| **Materialize rules** | Rule definitions still from **template** meta slug `materialize` (MVP); instance-level rules later |
| **Admin/company edit** | Template catalog PUT mirrors into platform/company **instance** meta; seed upsert refreshes platform instance meta only (children untouched) |

Product module seed changes (`PRODUCT_MODULES`) ship only via Alembic calling `upsert_product_modules` — not silent bootstrap overwrite.

**Legacy note:** older `cab_inst_*.module_data_rows` remain as migration/read fallback. Management modules (`mod_prompts`, `mod_mcp`, `mod_files`) keep **cabinet instance** as SoT with row `project_ids` for materialize targeting — orthogonal to project-leaf equipment data.

### `instance_owner` (tab / module)

Independent of `nav.placement` (rail / management / data):

| Value | UI | Materialize SoT |
|-------|----|-----------------|
| `cabinet` | Cabinet instance API; Management/Data без обязательного выбранного проекта | Cabinet rows + `project_ids` filter |
| `project` | Project leaf API; только при выбранном проекте | Project leaf rows |

Seeds: `mod_prompts` / `mod_mcp` / `mod_files` → `cabinet`; `mod_equipment` → `project`.

**Prompts hub:** path-cards (`prompt_paths`: name, path, `files_json`); empty `files_json` skips folder creation; workspace writes only `AGENTS.md` (no `CLAUDE.md` alias).

Module-level **MP binding** (`module_project_bindings`): if bindings exist, module materializes only for bound projects.

Future base modules follow the same cascade: edit in the owner’s instance, fork on bind down the chain, materialize from the project leaf.

**File & env pipeline (meta-syntax spec):** upload via Content Service → FileRef in row → materialize (`copy_blob` / `raw`) → Pod `/workspace`; container env and Vault secrets — declarative slugs, implementation backlog. See [12-content-file-pipeline](target/06-modules/meta-syntax/12-content-file-pipeline.md) · [13-container-env-secrets](target/06-modules/meta-syntax/13-container-env-secrets.md) · [gap map P-META-*](target/09-gap-map.md).

### Equipment matching module (`mod_equipment`)

Product module for computer-equipment matching (meta-tables, hub on **Данные**).

| Layer | SoT | Notes |
|-------|-----|--------|
| Catalog cards, request lines, found offers, selection | Postgres module instance rows | Editable UI + agent via rows API |
| Parsed price tables (csv/xlsx → index) | MinIO content blob (**raw SQLite artifact**) | Kept for rematerialize; not copied 1:1 into Pod |
| Normalized merged catalog | Materialize `merge_mapped_sqlite` | Single RO `/workspace/catalogs/catalog.sqlite` |

**Hybrid (fixed):** heavy catalogs are not JSONB rows and not live platform Postgres DSN in the Pod. Ingest is platform action `content.index_tabular` (reusable). Per-catalog `column_map` maps source headers → canonical columns (`title`, `price`, `part_number`, `supplier`, `lead_time` + auto `source_catalog`). Materialize merges all `status=ready` and `paused=false` catalogs that apply to the project (`project_ids` empty = all).

| Table | Scope intent | Role |
|-------|--------------|------|
| `catalogs` | shared (cabinet instance) | name, source file, artifact, status, paused, column_map, project_ids |
| `request_lines` | project leaf | customer line: title, P/N, qty, found_count, selected_offer_id |
| `found_offers` | project leaf | candidates linked to a line; exactly one `is_selected` primary |

Agent fills `found_offers` / `found_count` through cabinet/project rows APIs (or declarative `mcp_tools`); MCP RO tools query the merged SQLite only.

Meta primitives used/extended: hub + collections, `file_ref`, `column_map`, master–detail (`open_view` + `context_bind`), `data.select_row`, `content.index_tabular`, `merge_mapped_sqlite`. See [meta-syntax](target/06-modules/meta-syntax/).

## Project lifecycle (employee UI)

- **Create** (`draft`) — DB record + project settings only; no Pod, no workspace files.
- **Launch** — first materialize + Pod provision (`POST /projects/{id}/launch`); requires agent provider + AI key. On pod failure → `error` (not silent rollback).
- **Reload** — restart pod workload (`POST /projects/{id}/reload`); Redis rate limit 1/min, 3/30min; available anytime (UI shows button on settings only when `error`).
- **Container UI** — employee subpage with live `observed_state` (`GET /projects/{id}/container`, `/container/metrics`). **`running`** = k8s Ready (phase Running + readiness); CPU/RAM metrics are display-only (`metrics_available` flag + warning banner when absent).
- **Workspace files** — when Pod is `running`, UI **Files** opens live `/workspace` tree (list, text preview, download) via k8s exec; no MinIO fallback when paused.
- **Sync** — apply module binding changes to existing Pod workspace (`POST /projects/{id}/sync`); module folder prune + hydrate.
- **Agent reset** — cancel sessions + purge chat history (`POST /projects/{id}/agent/reset`); lives in agent BC, not pod_service.
