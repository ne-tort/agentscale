# Prodavan Agent

Работаешь в репозитории **`prodavan/`** (подмодуль Commerce) — облачная платформа автоматизации задач (SaaS).  
**Не** Telegram Commerce-бот, **не** закупочный пайплайн из корня Commerce.

Код `apps/*` сейчас stub: [`STUB.md`](STUB.md). Legacy (`docs/` вне target) — только справка: [`docs/LEGACY.md`](docs/LEGACY.md); не копировать домен из git history.

## Документация (актуальная)

Всё продуктовое и реализационное — **`docs/target/`**. Индекс: [`docs/target/README.md`](docs/target/README.md).

| Что | Где | Когда читать |
|-----|-----|----------------|
| **Сущности / иерархия** | [`00-entities.md`](docs/target/00-entities.md) | Старт любой задачи |
| Принципы / глоссарий | [`00-principles.md`](docs/target/00-principles.md), [`00-glossary.md`](docs/target/00-glossary.md) | Рядом с entities |
| Канон BC | [`01`](docs/target/01-platform-admin/)…[`14`](docs/target/14-project-containers/) | Модуль по теме |
| Containers = **Pod** | [`14-project-containers/`](docs/target/14-project-containers/) | Runtime изоляция Project |
| Cabinets = оболочка+meta | [`05-cabinets/entity.md`](docs/target/05-cabinets/entity.md) | Кабинеты |
| Platform infra | [`13-platform-infra/`](docs/target/13-platform-infra/) | До крупных backend-задач |
| Gap (код ≠ канон) | [`09-gap-map.md`](docs/target/09-gap-map.md) | Перед крупными решениями |
| As-built | [`12-layer-docs/`](docs/target/12-layer-docs/) | Что уже в коде |

**Правило:** `docs/target/` = как **должно**; `12-layer-docs` / код = что есть. Не возводить object-ws без Pod в «канон контейнера».

## Git / CI / кластер (GitOps)

**Ранбук:** [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md).

### Поставка через PR (обязательно)

**После любых изменений в коде, тестах или продуктовых docs** — не останавливаться на локальном коммите.  
Финальный шаг задачи: **commit → push → PR** (новый или обновление существующего на той же ветке).

```text
изменения → commit → push → PR → CI Gate → auto-merge → CI Images → Argo sync → Verify Dev
```

| Правило | Смысл |
|---------|--------|
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

Admin (KC) → Company (KC) как **локальный Admin** (сотрудники, контейнеры, свои AI keys) → Employee → Project → Pod.  
Канон: [`docs/target/00-entities.md`](docs/target/00-entities.md) · Company: [`03-companies/`](docs/target/03-companies/) · Gaps: [`09-gap-map.md`](docs/target/09-gap-map.md).

## Субагенты

Всегда **Auto**: `model: "inherit"`.

## Язык и границы

- Ответы — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/target/`.
- Infra не ломать без явной задачи; не добавлять bash.
