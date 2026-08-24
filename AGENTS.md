# Prodavan Agent

Работаешь в репозитории **`prodavan/`** (подмодуль Commerce) — облачная платформа автоматизации задач (SaaS).  
**Не** Telegram Commerce-бот, **не** закупочный пайплайн из корня Commerce.

Код `apps/*` сейчас stub: [`STUB.md`](STUB.md). Legacy (`docs/` вне target) — только справка: [`docs/LEGACY.md`](docs/LEGACY.md); не копировать домен из git history.

## Документация (актуальная)

Всё продуктовое и реализационное — **`docs/target/`**. Индекс: [`docs/target/README.md`](docs/target/README.md).

| Что | Где | Когда читать |
|-----|-----|----------------|
| Суть / принципы продукта | [`00-principles.md`](docs/target/00-principles.md), [`00-glossary.md`](docs/target/00-glossary.md) | Старт любой задачи |
| Канон BC (что должно быть) | [`01`](docs/target/01-platform-admin/)…[`10`](docs/target/10-identity-keycloak/) | Модуль по теме задачи |
| Platform infra (P0) | [`13-platform-infra/`](docs/target/13-platform-infra/) | **До** крупных backend-задач (storage/workers/lifespan/bus) |
| Gap / запреты | [`09-gap-map.md`](docs/target/09-gap-map.md) | Перед крупными решениями (блок P0 сверху) |
| План слоёв, DoD, порядок | [`11-implementation-plan/`](docs/target/11-implementation-plan/) | Перед и во время реализации; P0: [`P0-platform-infra.md`](docs/target/11-implementation-plan/P0-platform-infra.md) |
| Правила поставки | [`11/00-rules.md`](docs/target/11-implementation-plan/00-rules.md), [`sequence.md`](docs/target/11-implementation-plan/sequence.md) | Старт слоя |
| Чеклист готовности | [`11/checklist-master.md`](docs/target/11-implementation-plan/checklist-master.md) | Закрытие слоя / P0 |
| Контракты C-* | [`11/contracts-index.md`](docs/target/11-implementation-plan/contracts-index.md) | Границы между слоями |
| As-built (что/как есть) | [`12-layer-docs/`](docs/target/12-layer-docs/) | **Сначала** при работе со слоем; обновлять в том же PR |
| Принципы as-built | [`12/00-principles.md`](docs/target/12-layer-docs/00-principles.md) | Как писать семантику / контракты / связи |
| Шкала Quality 0–10 | [`12/quality-score.md`](docs/target/12-layer-docs/quality-score.md) | Оценка законченности (`done` ≥ 8) |
| Карта факта | [`12/map.md`](docs/target/12-layer-docs/map.md) | Связи после поставки |

Кабинеты (dynamic): [`05-cabinets/dynamic-cabinets.md`](docs/target/05-cabinets/dynamic-cabinets.md), MCP packages: [`mcp-packages.md`](docs/target/05-cabinets/mcp-packages.md).

## Порядок работы

1. Понять слой задачи → карточка плана `11/LNN-*.md` (или `P0-platform-infra.md`) + as-built `12/LNN-*.md`.
2. Канон только нужных модулей `01`…`10` + при backend infra — **`13-platform-infra`** (не весь target подряд).
3. Реализовать за контрактами `C-*`; изолируемое — полностью (см. sequence).
4. В том же изменении: as-built (семантика, что/как, контракты, Gaps, **Quality**).
5. Слой `done` только по DoD + veto + Quality ≥ 8 — не «минимальный прототип».

## Git / CI / кластер

**Ранбук (обязательно):** [`docs/07-infrastructure/runbook.md`](docs/07-infrastructure/runbook.md) — PR вместо push в `main`, Auto-merge, GHCR, Deploy, recover после ребута WSL.

Кратко: ветка от `origin/main` → `gh pr create` → **CI Gate** → Auto-merge squash. Не `git push origin main`. После ребута WSL: `bash infra/scripts/recover_local_stack.sh` из Kali (где k3d).

## Суть продукта (якорь)

Admin → Company → Employee → **динамический Cabinet** (meta+UI+MCP packages, schema-per-instance) → **Project** (агент, materialize из кабинета).  
Не static `profile_id` code-packs. Подробности — в `00-principles` и модуле слоя.

## Субагенты

Всегда **Auto**: `model: "inherit"` (или не указывать). Другие slug'и — только по явной просьбе пользователя.

## Язык и границы

- Ответы — на русском, если не сказано иное.
- Новые продуктовые требования — только в `docs/target/`.
- Infra (k3s, Terraform, Argo, CI) не ломать без явной задачи.
- Логику слоя не восстанавливать «с нуля из кода» — сначала `12`, затем точечно код.
