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
- **Metrics BC** (`application/metrics/`) — внутри `prodavan-api`: Kafka consumer, Redis (presence + pod samples), REST для admin/company UI.

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
