# Agentscale Agent

Работаешь в репозитории **ne-tort/agentscale** (локальная директория подмодуля — `prodavan/`) — SaaS **управления Pod'ами через UI**; внутри Pod — **AI-агенты с файлами и инструментами**, не чат-обёртка.  
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
- Bootstrap кластера: **SSH + Terraform** (`infra/terraform/environments/vm`) → UI **http://172.31.156.203:8088/**.
- Императив только **`infra/ops`**: `validate` / `wait` / `rollout` / `smoke`.
- **Запрещены** `.sh` под `infra/`, docker-compose как кластер, k3d в git, recover/deploy shell.
- Кластер: **k3s** + Argo (`infra/argocd` → `infra/k3s/overlays/dev`).

### Принципы инфраструктуры (канон)

1. **Декларативно везде, где возможно.** GitOps (Argo CD) + Terraform — источник истины. Никаких императивных `.sh`/`.ps1` скриптов для инфраструктуры: состояние описывается манифестами/HelmChartConfig/Terraform, применяется контроллерами.
2. **Где GitOps/Terraform не могут декларативно** — **init containers (Python)** внутри Pod/job, которые при старте приводят состояние к нужному. Не shell-скрипты на хосте, а контейнер с Python-логикой в k8s.
3. **Windows-хост — только как клиент.** Раньше требовался WSL→Windows port forwarding и keepalive (исключение из декларативности). Теперь кластер на выделенной VM, доступной с Windows по IP напрямую: portproxy/keepalive не нужны.
4. **Dev overlay: wildcard access.** Dev-доступ идёт через произвольные reverse-proxy/VPN (punnel) с непредсказуемым Host/SNI/IP — dev overlays **не пиняют** конкретные SNI/IP, а разрешают любой origin (wildcard). TLS — self-signed default cert Traefik (без cert-manager в dev).
5. **Prod overlay: cert-manager + Let's Encrypt** на домене **`ai-qwerty.ru`** (ClusterIssuer, автоматический выпуск). Prod overlay пиняет host + TLS cert.
6. **Не ломать punnel/demux.** Punnel — L4 plaintext reverse relay по дизайну (FEATURE 029: no TLS terminate). HTTPS обеспечивается на ingress-уровне (Traefik websecure), не punnel'ом.

### Dev-кластер на VM (kubectl)

Локальный k3s живёт на выделенной VM **`www@172.31.156.203`** (там же CI-раннеры,
см. `infra/github-runner-vm/`). Раньше — Kali WSL (удалён).

```bash
ssh www@172.31.156.203
export KUBECONFIG=~/.kube/prodavan-dev.yaml   # или sudo k3s kubectl
kubectl get pods -A
```

UI с Windows: **http://172.31.156.203:8088/** · Keycloak hostPort **:8089** ·
kubectl с Windows: `scp` kubeconfig и заменить server на `https://172.31.156.203:6443`.
Подробности: [`infra/terraform/environments/vm/README.md`](infra/terraform/environments/vm/README.md), [`runbook.md`](docs/07-infrastructure/runbook.md).

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

## Окружение: Windows

**ОС: Windows.** Shell для системных команд — Git Bash: `git`, `npm`/`pnpm`, тесты, сборки, `gh`, `wsl`, скрипты.

### Файловые операции — через native-инструменты, НЕ через bash

Не читать/писать/искать файлы через `cat`, `head`, `tail`, `sed`, `echo >`, heredoc (`cat <<EOF`): на Windows это ломает кодировку (кириллица → mojibake) и нестабильно. Используй native-инструменты агента (названия отличаются между harness'ами, смысл один — файловый I/O вне shell):

| Задача | Инструмент |
|--------|------------|
| Прочитать файл | Read |
| Создать файл | Write |
| Править файл | Edit |
| Найти файл | Glob |
| Поиск по содержимому | Grep |

Подводные камни:

- bash-вывод кириллицы → mojibake; native Read → корректный UTF-8.
- Glob по умолчанию не заглядывает в dot-папки (`.github`, `.git`) — передавай `path` к такой папке явно.
- Read возвращает строки с префиксом `N: ` (номер строки); в Edit `old_string` копируй без этого префикса.
- Перед Write в существующий файл — сначала Read.

## Язык и границы

- Ответы — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/PRODUCT.md` (as-built — `12-layer-docs`).
- Infra не ломать без явной задачи; не добавлять bash.
