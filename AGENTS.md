# Prodavan Agent

Работаешь в репозитории **`prodavan/`** (подмодуль Commerce) — облачная платформа автоматизации задач (SaaS).  
**Не** Telegram Commerce-бот, **не** закупочный пайплайн из корня Commerce.

Код `apps/*` сейчас stub: [`STUB.md`](STUB.md). Legacy (`docs/` вне target) — только справка: [`docs/LEGACY.md`](docs/LEGACY.md); не копировать домен из git history.

## Документация (актуальная)

Всё продуктовое и реализационное — **`docs/target/`**. Индекс: [`docs/target/README.md`](docs/target/README.md).

| Что | Где | Когда читать |
|-----|-----|----------------|
| Суть / принципы продукта | [`00-principles.md`](docs/target/00-principles.md), [`00-glossary.md`](docs/target/00-glossary.md) | Старт любой задачи |
| Канон BC | [`01`](docs/target/01-platform-admin/)…[`10`](docs/target/10-identity-keycloak/) | Модуль по теме |
| Platform infra (P0) | [`13-platform-infra/`](docs/target/13-platform-infra/) | До крупных backend-задач |
| Gap / запреты | [`09-gap-map.md`](docs/target/09-gap-map.md) | Перед крупными решениями |
| План слоёв | [`11-implementation-plan/`](docs/target/11-implementation-plan/) | Перед реализацией |
| As-built | [`12-layer-docs/`](docs/target/12-layer-docs/) | Сначала при работе со слоем |

## Git / CI / кластер (GitOps)

**Ранбук:** [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md).

- Поставка: PR → **CI Gate** → Auto-merge → **CI Images** → Argo CD sync → **Verify Dev**.
- Bootstrap кластера: **SSH + Terraform** (`infra/terraform/environments/local`) → UI **http://127.0.0.1:8088/**.
- Императив только **`infra/ops`**: `validate` / `wait` / `rollout` / `smoke`.
- **Запрещены** `.sh` под `infra/`, docker-compose как кластер, k3d в git, recover/deploy shell.
- Кластер: **k3s** + Argo (`infra/argocd` → `infra/k3s/overlays/dev`).

## Суть продукта

Admin → Company → Employee → **динамический Cabinet** → **Project**.  
Не static `profile_id` code-packs.

## Субагенты

Всегда **Auto**: `model: "inherit"`.

## Язык и границы

- Ответы — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/target/`.
- Infra не ломать без явной задачи; не добавлять bash.
