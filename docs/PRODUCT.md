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
| Document Store (Mongo) | `application/document_store/`, admin `/admin/document-store`, [ADR](02-architecture/ADR-document-store-mongo.md) |
| Tenant Infra Gateway | `application/tenant_infra/` — Cache/Docs/UserDB/Events/Objects via `:8001` + Bridge scopes + company quotas; [as-built](target/12-layer-docs/tenant-infra-gateway.md) |
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
  → bind to owner (local | global)
       local  → Module Instance fork (meta + data JSONB)
       global → no new instance; SoT = resolve up to nearest local/platform instance
  → Materialize into a project workspace only if an explicit module↔project bind exists
```

| Layer | What |
|-------|------|
| **Template** | Catalog module meta (`modules` + `module_meta_documents`) — source for first platform instance |
| **Instance** | Independent copy when bind is **local**: `module_instances` + meta/data JSONB. Owner: `platform` / `company` / `cabinet` / `project` |
| **Bind** | Edge on `platform→company` (grant), `company/cabinet` (MC), `cabinet→project` (MP). Fields: `bind_kind` (`local`\|`global`), `child_may_edit` (bool). **Local** forks a child instance. **Global** does not fork; child depends on parent SoT; writes allowed only if `child_may_edit` |
| **SoT resolve** | `resolve_sot_instance(module, owner)`: local instance for owner if present, else follow global bind to parent. Replaces former tab `instance_owner` hack |
| **Project hubs** | Employee UI edits the **SoT owner** returned by resolve (cabinet for global project binds on prompts/MCP/files; project leaf for local equipment binds) |
| **Materialize** | Module runs for a project **only** with an explicit MP row. Row `project_ids` further filters entities (empty = all **bound** projects for that module). Bind alone does not dump every row into the workspace |
| **Materialize rules** | From template meta slug `materialize` (MVP) |
| **Seed upsert (Alembic)** | Meta refresh for all instances; data rows insert-only (`ON CONFLICT DO NOTHING`) |
| **Storage** | Postgres JSONB for module instances. App Document Store on Mongo — [ADR](02-architecture/ADR-document-store-mongo.md). Module-instance→Mongo cutover still deferred — [backlog ADR](02-architecture/ADR-backlog-module-instance-mongo.md) |

Product module seed changes ship only via Alembic calling `upsert_product_modules`.

### Bind kinds (UI: «Локальная» / «Глобальная»)

| `bind_kind` | Effect | Child edit |
|-------------|--------|------------|
| **local** | Child gets own instance (fork) | Child owns and edits its copy |
| **global** | Child has no instance; uses parent SoT | Locked unless `child_may_edit=true` (lock icon in bind UI) |

Product defaults (`default_project_bind` on module meta / tabs):

| Module | Typical cabinet→project bind |
|--------|------------------------------|
| `mod_prompts`, `mod_mcp`, `mod_files` | **global** |
| `mod_equipment` | **local** |

### Row `project_ids`

- UI `project_multiselect` lists **only projects bound to the module**. No binds → control hidden.
- Empty/`null` `project_ids` = all projects **bound to the module** (not every cabinet project).
- Fan-out (e.g. equipment catalogs after merge): empty `project_ids` + local MP → materialize into each bound project workspace.

**Prompts hub:** `prompt_paths` (name, path, `files_json`); empty `files_json` skips folder creation; workspace writes `AGENTS.md` only. One active profile per project among matching `project_ids`.

**File & env pipeline:** see [12-content-file-pipeline](target/06-modules/meta-syntax/12-content-file-pipeline.md) · [13-container-env-secrets](target/06-modules/meta-syntax/13-container-env-secrets.md).

### Equipment matching module (`mod_equipment`)

Product module for computer-equipment matching (hub on **Данные**). Project binds stay **local** (per-project instance).

| Layer | SoT | Notes |
|-------|-----|--------|
| Catalog cards, request lines, found offers, selection | Project leaf instance rows | Editable UI + agent via rows API |
| Local parsed price tables (csv/xlsx → index) | MinIO content blob (**raw SQLite artifact**) | Kept for rematerialize; not copied 1:1 into Pod |
| Remote PostgreSQL catalogs | External DB (live) | DSN in Vault `secret_ref`; probe columns + `COUNT(*)` only — **no** download into SQLite |
| Normalized merged catalog (local only) | Materialize `merge_mapped_sqlite` | Single RO `/workspace/catalogs/catalog.sqlite` |
| Remote DSN in Pod | `container_env_secrets` foreach | `EQUIPMENT_CATALOG_DSN_<ROW>` + `EQUIPMENT_REMOTE_CATALOGS` JSON for future MCP |

**Hybrid (fixed):** heavy **local** catalogs are not JSONB rows. Ingest is `content.index_tabular`. **Remote** catalogs stay live — platform action `content.probe_remote_sql` only introspects schema/`COUNT(*)`; data is never snapshotted. Per-catalog `column_map` maps source headers → canonical columns (autosave in UI). Materialize merges ready non-paused **local** catalogs (`source_kind!=remote`) that apply (`project_ids` empty = all **module-bound** projects).

| Table | Scope intent | Role |
|-------|--------------|------|
| `catalogs` | project leaf (+ row `project_ids`) | name, `source_kind` local\|remote, file or remote DSN/table, artifact (local), status, paused, column_map, project_ids |
| `request_lines` | project leaf | customer line: title, P/N, qty, found_count, selected_offer_id |
| `found_offers` | project leaf | candidates linked to a line; exactly one `is_selected` primary |
| `s4b_settings` | project leaf (+ row `project_ids`) | S4B URL/login/password(secret)/MCP zip; empty `project_ids` = all bound projects; injects `S4B_*` env + materialize `mcp_package` |

Agent fills `found_offers` / `found_count` through rows APIs (or declarative `mcp_tools`); MCP RO tools query the merged SQLite for **local** catalogs. Live remote SQL via MCP is a follow-up (DSN already in Pod env).

**S4B:** hub tile → settings form; password via cabinet secrets → Pod `S4B_PASSWORD`; zip materialize reuses `mcp_package` path. Redis/Kafka/Mongo/MinIO from Pod — **not** direct. Pod reaches platform only via **Pod API `:8001`** + Bridge JWT (scopes): `/infra`, `/modules`, `/agent`, `/internal/pods` (incl. workspace-archive hydrate). See [tenant-infra-gateway](target/12-layer-docs/tenant-infra-gateway.md). App Document Store (Mongo) is in-proc for platform BCs only — [ADR](02-architecture/ADR-document-store-mongo.md).

Meta primitives: hub + collections, `file_ref`, `secret_ref`, `column_map`, master–detail, `data.select_row`, `content.index_tabular`, `content.probe_remote_sql`, `merge_mapped_sqlite`, `foreach_rows` env. See [meta-syntax](target/06-modules/meta-syntax/).

## Project lifecycle (employee UI)

- **Create** (`draft`) — DB record + project settings only; no Pod, no workspace files.
- **Launch** — first materialize + Pod provision (`POST /projects/{id}/launch`); requires agent provider + AI key. On pod failure → `error` (not silent rollback).
- **Reload** — restart pod workload (`POST /projects/{id}/reload`); Redis rate limit 1/min, 3/30min; available anytime (UI shows button on settings only when `error`).
- **Container UI** — employee subpage with live `observed_state` (`GET /projects/{id}/container`, `/container/metrics`). **`running`** = k8s Ready (phase Running + readiness); CPU/RAM metrics are display-only (`metrics_available` flag + warning banner when absent).
- **Workspace files** — when Pod is `running`, UI **Files** opens live `/workspace` tree (list, text preview, download) via k8s exec; no MinIO fallback when paused.
- **Sync** — apply module binding changes to existing Pod workspace (`POST /projects/{id}/sync`); module folder prune + hydrate.
- **Agent reset** — cancel sessions + purge chat history (`POST /projects/{id}/agent/reset`); lives in agent BC, not pod_service.
