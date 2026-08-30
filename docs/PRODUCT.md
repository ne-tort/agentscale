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
    → API (pod-service, projects, auth)
    → Kubernetes Pod (prodavan-sandboxes)
        → agent runtime: SDK (Cursor/Codex/…), workspace FS, MCP/tools, файлы проекта
```

1. **Pod** — единица изоляции на Project (1:1). Реальный k8s Pod с volume, hydrate, initContainer.
2. **UI** — список контейнеров/проектов, статус, pause/resume, вход в workspace агента (не модалка-чат).
3. **Agent inside Pod** — провайдер через `AgentProviderPort`, работа с **файлами** (upload, read, edit, bundles), tool calls, HITL; не thin wrapper над completions API.

## Observability

- **k8s metrics-server** — cluster addon для CPU/RAM sandbox pod'ов; Prodavan не деплоит отдельный metrics microservice.
- **Metrics BC** (`application/metrics/`) — внутри `prodavan-api`: Kafka consumer, Redis (presence + pod samples), REST для admin/company/employee container UI.
- **Project lifecycle** — статусы `draft` | `active` | `paused` | `error` | `completed`. `error` = pod не поднялся; recovery через `POST /projects/{id}/reload` (Redis rate limit, fail-closed).

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
Platform meta (template)  →  Cabinet module_data_rows (storage)  →  Project workspace (materialize)
```

| Layer | What |
|-------|------|
| **Template** | Shared module meta: tables, views, materialize rules |
| **Cabinet** | Per-cabinet rows in `module_data_rows` (JSONB body) |
| **Project** | Materialize filters rows by `project_ids` in body; profile pick by `prompt_profiles.project_ids`; optional MP binding skips whole module |

Row-level **`project_ids`** (JSON array in row body): empty or absent → row applies to **all** projects in the cabinet; otherwise only listed projects.

Module-level **MP binding** (`module_project_bindings`): if bindings exist, module materializes only for bound projects.

Future base modules (MCP, Files, Prompts, …) follow the same pattern: edit in cabinet UI, scope rows to projects, materialize into Pod workspace on **launch** or **sync** (not on create).

**File & env pipeline (meta-syntax spec):** upload via Content Service → FileRef in row → materialize (`copy_blob` / `raw`) → Pod `/workspace`; container env and Vault secrets — declarative slugs, implementation backlog. See [12-content-file-pipeline](target/06-modules/meta-syntax/12-content-file-pipeline.md) · [13-container-env-secrets](target/06-modules/meta-syntax/13-container-env-secrets.md) · [gap map P-META-*](target/09-gap-map.md).

## Project lifecycle (employee UI)

- **Create** (`draft`) — DB record + project settings only; no Pod, no workspace files.
- **Launch** — first materialize + Pod provision (`POST /projects/{id}/launch`); requires agent provider + AI key. On pod failure → `error` (not silent rollback).
- **Reload** — restart pod workload (`POST /projects/{id}/reload`); Redis rate limit 1/min, 3/30min; available anytime (UI shows button on settings only when `error`).
- **Container UI** — employee subpage with live `observed_state` (`GET /projects/{id}/container`, `/container/metrics`). **`running`** = k8s Ready (phase Running + readiness); CPU/RAM metrics are display-only (`metrics_available` flag + warning banner when absent).
- **Sync** — apply module binding changes to existing Pod workspace (`POST /projects/{id}/sync`); module folder prune + hydrate.
- **Agent reset** — cancel sessions + purge chat history (`POST /projects/{id}/agent/reset`); lives in agent BC, not pod_service.
