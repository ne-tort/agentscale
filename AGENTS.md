# Prodavan Agent

Работаешь в репозитории **`prodavan/`** (подмодуль Commerce) — SaaS **управления Pod'ами через UI**; внутри Pod — **AI-агенты с файлами и инструментами**, не чат-обёртка.  
**Не** Telegram Commerce-бот, **не** закупочный пайплайн из корня Commerce.

Legacy AI-канон: [`docs/target/`](docs/target/) (кроме as-built) — см. [`docs/LEGACY.md`](docs/LEGACY.md). Ориентир: [`docs/PRODUCT.md`](docs/PRODUCT.md) + код + тесты.

## Документация (актуальная)

| Что | Где | Когда читать |
|-----|-----|--------------|
| **Продукт** | [`docs/PRODUCT.md`](docs/PRODUCT.md) | Старт любой задачи |
| As-built | [`docs/target/12-layer-docs/`](docs/target/12-layer-docs/) | Что уже в коде |
| Infra / e2e | [`docs/07-infrastructure/`](docs/07-infrastructure/) | GitOps, k3s, CI |
| Pod runtime | [`pod_service/`](apps/api/src/prodavan/application/pod_service/), [`tests/e2e/k8s/`](apps/api/tests/e2e/k8s/) | Real k8s lifecycle |
| Agent + files | [`application/agent/`](apps/api/src/prodavan/application/agent/) | Агент в Pod |
| ~~Канон BC~~ legacy | [`docs/target/01…15`](docs/target/) | Справка, не блокер |

**Правило:** PRODUCT.md + код > gap map. E2E — backend API, не Flutter.
## Git / CI / кластер (GitOps)

**Ранбук:** [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md).

### Поставка через PR (обязательно)

**Задача не считается выполненной, пока нет PR.** Локальный diff, «готово в ветке» или summary без push — **не финал**.

**После любых изменений в коде, тестах или продуктовых docs** — **сразу** в том же заходе: **commit → push → PR** (новый или обновление существующего на той же ветке). **Не** останавливаться на локальном коммите. **Не** спрашивать «коммитить / делать PR?» — если diff готов, PR обязателен.

```text
изменения → commit → push → PR → CI Gate → auto-merge → CI Images → Argo sync → Verify Dev
```

| Правило | Смысл |
|---------|--------|
| **Сразу PR** | Готовый diff → в том же ответе/сессии: commit + push + PR URL пользователю |
| **Всегда PR** | Любой готовый diff — в PR, даже «мелкий» фикс или доработка по ревью |
| **Не спрашивать «коммитить?»** | Если задача выполнена — сразу commit + push + PR |
| **Не GitOps в обход** | Без `kubectl apply`, ручных migrate на shared env, local-only «готово» |
| **CI красный — чинить в той же ветке** | Push в PR, дождаться green **Verify Dev** |
| **Не в PR** | `tools/_*.sh`, секреты, `.env`, артефакты сборки |

- Поставка: PR → **CI Gate** → Auto-merge → **CI Images** → Argo CD sync → **Verify Dev**.
- Bootstrap кластера: **SSH + Terraform** (`infra/terraform/environments/local`) → UI **http://127.0.0.1:8088/**.
- Императив только **`infra/ops`**: `validate` / `wait` / `rollout` / `smoke`.
- **Запрещены** `.sh` под `infra/`, docker-compose как кластер, k3d в git, recover/deploy shell.
- Кластер: **k3s** + Argo (`infra/argocd` → `infra/k3s/overlays/dev`).

### Dev-кластер в WSL (kubectl)

Локальный k3s живёт в дистрибутиве **`kali-linux`**, не в Ubuntu / docker-desktop.

```bash
wsl -d kali-linux
export KUBECONFIG=~/.kube/prodavan-dev.yaml
kubectl get pods -A
```

Альтернатива: `/etc/rancher/k3s/k3s.yaml` (после `sudo`). UI с Windows: **http://127.0.0.1:8088/** · Keycloak hostPort **:8089**. Подробности: [`docs/07-infrastructure/wsl-dev.md`](docs/07-infrastructure/wsl-dev.md), [`runbook.md`](docs/07-infrastructure/runbook.md).

## База данных и миграции (Alembic)

Канон: [`docs/07-infrastructure/alembic.md`](docs/07-infrastructure/alembic.md).

**Источник истины схемы** — SQLAlchemy-модели (`apps/api/src/.../persistence/models/`).  
Ревизии Alembic генерируются из моделей (`alembic revision --autogenerate`) и коммитятся в PR.  
Ручное редактирование migration-файлов — только для переноса/бэкапа данных.

### Жёсткие запреты (кластер и БД)

- **Не** обходить деплой: `kubectl apply`, port-forward как «фикс», ручной rollout, правки в running pod.
- **Не** выполнять миграции на shared env вручную (`kubectl exec … alembic`, `psql ALTER`, `alembic stamp`).
- **Не** запускать `alembic revision --autogenerate` при деплое — только `upgrade head` по закоммиченным файлам.
- **Не** править уже применённые ревизии — только новые файлы в `alembic/versions/`.

Если схема на dev отстаёт от кода — **чинить механизм поставки** (Dockerfile, `migrate.sh`, CI, merge → Images → Argo) и **передеплоить**. Не «лечить» базу императивом.

### Поток

```text
ORM-модель → autogenerate в PR → CI (upgrade + alembic check) → merge
  → CI Images → Argo → initContainer ./scripts/migrate.sh → API
```

## Суть продукта

**UI → API → k8s Pod → agent (файлы, tools, SDK).** Подробно: [`docs/PRODUCT.md`](docs/PRODUCT.md).
## Субагенты

Всегда **Auto**: `model: "inherit"`.

## Язык и границы

- Ответы — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/PRODUCT.md` (as-built — `12-layer-docs`).
- Infra не ломать без явной задачи; не добавлять bash.
