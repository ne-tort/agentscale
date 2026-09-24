# Agent-runtime image: сборка → GHCR → Pod (as-built)

**Обновлено:** 2026-09-24 · **Статус:** as-built + gap map  
Поставка образа `prodavan-agent-runtime` (ядро агента, репо-подмодуль [`prodavan-claw`](https://github.com/ne-tort/prodavan-claw)) — от CI-сборки до Pod'а проекта. Как **должно** быть: (1) кэшированная сборка через CI-раннер, (2) образ с бинарником → GHCR, (3) pod_service тянет образ из GHCR в Pod проекта. Ниже — как устроено сейчас и где дыры.

## 0. Поток (TL;DR)

```text
prodavan-claw PR → openclaw-ci → auto-merge → openclaw-images ─┐
   (клон в SF + пуш :latest без кэша и smoke)                  │
                                                                ├→ GHCR ghcr.io/ne-tort/prodavan-agent-runtime:{sha,latest}
prodavan main push (paths: prodavan-claw/**, …)                │
   → ci-images.yml::build-agent-runtime ────────────────────────┘
      (клон submodule pointer, buildx local-cache, smoke, :latest)
   → trigger-verify → Verify Dev (ne-tort/prodavan)
      → prodavan-ops rollout → restart prodavan-probe-pod (re-pull :latest)

Pod проекта: pod_service создаёт Pod c POD_AGENT_RUNTIME_IMAGE (:latest)
  + imagePullPolicy: Always + secret ghcr-pull → новый Pod = свежий digest;
  запущенный Pod обновляется только recreate (generation bump).
```

**Два независимых пайплайна собирают и пушат один и тот же образ `:latest`** — см. дыру №1.

## 1. Сборка: два пайплайна

| | `prodavan-claw` `openclaw-images.yml` | `prodavan` `ci-images.yml::build-agent-runtime` |
|---|---|---|
| Триггер | push в claw main (после `openclaw-ci` + auto-merge из PR) | push в prodavan main, paths `prodavan-claw/**`, `.gitmodules`, … |
| Контекст / Dockerfile | claw repo root / `platform-openclaw/Dockerfile` | клон claw (PAT) / тот же Dockerfile |
| Ревизия | HEAD claw main | **указатель сабмодуля**, при недоступности — молчаливый `git checkout main` |
| Кэш Docker | **нет вообще** | `type=local` `/tmp/.buildx-cache-agent` + ручная ротация (rmtree+rename) |
| Smoke | нет | артефакты (`dist/server.js`, `node_modules`) + `/health` 200 |
| Auth пуша | `GITHUB_TOKEN` claw | PAT `PRODAVAN_CLAW_TOKEN` (package принадлежит claw-репо) |
| Теги | `openclaw-bridge:{sha,latest}` **и** `prodavan-agent-runtime:{sha,latest}` | `prodavan-agent-runtime:{sha12,latest}` |
| После пуша | `trigger-verify` → dispatch `verify-dev.yml` в prodavan | `trigger-verify` → Verify Dev |

`pull_request`-ветка в meta-step `build-agent-runtime` (`tag=pr-local`, `push=false`) — мёртвый код: ни один workflow не вызывает `ci-images.yml` как reusable.

## 2. GHCR и секреты

- Образ: `ghcr.io/ne-tort/prodavan-agent-runtime` (`{sha12,latest}`). GHCR-**package принадлежит репо `prodavan-claw`** → пуш из workflows prodavan возможен только через PAT `PRODAVAN_CLAW_TOKEN` (`write:packages`); `GITHUB_TOKEN` prodavan прав не имеет. Тот же PAT используется для клона приватного claw (GITHUB_TOKEN не умеет cross-repo).
- In-cluster pull: secret **`ghcr-pull`** (`kubernetes.io/dockerconfigjson`) в ns `prodavan` и `prodavan-sandboxes`. Источники: SealedSecrets (`overlays/dev/SECRETS.md`) и Terraform bootstrap (`gitops-bootstrap.sh.tpl`, `TF_VAR_ghcr_token`). Ссылается из platform Deployment'ов и из Pod-спеки песочниц (`POD_SANDBOX_IMAGE_PULL_SECRET`).

## 3. Слой образа (Dockerfile as-built)

Стейджи: `sdk-build` (npm ci + tsc для `openclaw-sdk/`) → `build` (npm ci + tsc для `platform-openclaw/packages`, плюс `npm install --no-save` vendor-SDK) → final (`node:22-bookworm-slim` + toolchain).

Замеры `docker history` (образ `:3efaa81d405d`, 2026-09-24, полный размер **5,49–6,27 ГБ**; commerce-форк — отдельный проект — 7,58 ГБ):

| Слой | Размер | Содержимое |
|------|--------|-----------|
| `RUN apt-get … nvm … rustup` (один RUN) | **3,73 ГБ** | build-essential, clang, cmake, golang, JDK17, ffmpeg, gh, yq, второй Node через nvm, pnpm/yarn, Rust 1.83 (~1 ГБ) — тулчейн для работы агента |
| `RUN chown -R node:node /app /openclaw-sdk` | **778 МБ** | анти-паттерн: chown отдельным слоем дублирует всё дерево |
| `COPY /app/node_modules` | 350 МБ | целиком из build-стейджа, **включая devDeps**: typescript 23 МБ, tsx, `@cursor/sdk` 32 МБ, `@anthropic-ai/claude-agent-sdk` 17 МБ |
| `COPY /app/packages` | 351 МБ | dist + вложенные node_modules адаптеров |
| `COPY /openclaw-sdk` | 55 МБ | дерево SDK (с node_modules от `npm ci`) |
| base `node:22-bookworm-slim` | ~370 МБ | |

~85% образа — тулчейн агента (осознанное решение: агент — coding-agent); ~1,2 ГБ — потери (chown-дубль + shipping devDeps); продукт — ~100–200 МБ. tsc по всем 9 пакетам (23 тыс. LOC TS) — секунды: **время сборки и вес образа к TypeScript отношения не имеют.**

## 4. Кэширование сборки: реальность

- **Local-cache в эфемерном месте.** `cache-from/to: type=local, /tmp/.buildx-cache-{api,web,agent}` лежит в ФС **контейнера раннера**. Раннеры — контейнеры (`infra/github-runner/`, `runner-1..4` + `claw-runner-1/2`), их `/tmp` — container-ephemeral: кэш теряется при пересоздании раннера (compose down -v, Docker Desktop/WSL restart) и **не разделяется между runner-1..4** (джоба на другом раннере = холодная сборка). Персистентный volume `prodavan-ci-cache` → `/cache` заведён именно для этого, но CI его **не использует**.
- **Registry/GHA cache backend не используется.** Ротация local-cache (rmtree + rename) может гонять конкурирующие сборки одного образа (workflow_dispatch поверх main push).
- **Dockerfile кэш-дружелюбности не имеет:** ни одного `--mount=type=cache` (единственный в организации — pip-маунт в `apps/api/Dockerfile:19`); `COPY packages` стоит **до** `npm ci` в обоих стейджах → любое изменение исходников перезапускает полный `npm ci`; монолитный apt+nvm+rustup RUN пересобирается целиком при любой правке Dockerfile.
- **`.dockerignore` не работает:** `platform-openclaw/.dockerignore` игнорируется Docker'ом (контекст = корень claw-репо, где dockerignore нет) → весь claw-репо уходит в контекст каждой сборки.

## 5. Потребление в кластере (Pod)

- Источник образа: `POD_AGENT_RUNTIME_IMAGE` (`apps/api/src/prodavan/config/settings.py`, default `…:local`; legacy-алиас `POD_AGENT_BRIDGE_IMAGE`). Configmap base + dev overlay: `ghcr.io/ne-tort/prodavan-agent-runtime:latest`, `POD_AGENT_RUNTIME_ENABLED: "true"`.
- Спека Pod'а: `infrastructure/k8s/sandbox/pod_spec.py` — контейнер `agent-runtime`, `imagePullPolicy: Always` (захардкожено), `imagePullSecrets: [ghcr-pull]`; init-контейнер `hydrate` (образ prodavan-api); при выключенном runtime — stub-holder (`sleep infinity`).
- Обновление до нового образа: **новый** Pod тянет свежий digest (Always + :latest); **запущенный** — только recreate (bump `hydrate_generation`/`bridge_generation` при materialize/reload/sync). Probe-pod перезапускается `prodavan-ops rollout` (в `DEPLOYMENT_TARGETS`). Argo образами динамических подов не управляет; SHA/дайджест **нигде не пинится** (заметка I18 в dev-оверлее — «Do not SHA-override here»).

## 6. Карта дыр

| # | Дыра | Где | Влияние | Направление фикса |
|---|------|-----|---------|-------------------|
| 1 | **Двойной пайплайн** пушит один `:latest` | `openclaw-images.yml` + `ci-images.yml::build-agent-runtime` | гонки тегов, недетерминированный digest, разный набор проверок, два кэш-состояния | один владелец сборки; второй пайплайн — только указатель/smoke |
| 2 | Сборка claw **без кэша и без smoke** | `openclaw-images.yml` (build job) | каждая сборка claw — с нуля, ~ГБ скачиваний; битый образ ловится только в кластере | тот же cache backend + smoke из пайплайна prodavan |
| 3 | Local-cache в эфемерном `/tmp` контейнера раннера; volume `prodavan-ci-cache:/cache` не используется; нет shared между runner-1..4; race при ротации | `ci-images.yml` cache-from/to; `infra/github-runner/README.md` | холодная сборка 10–15 мин после любого пересоздания раннера / попадания на другой раннер | `type=registry` (кэш-манифест в GHCR) или `type=local` на `/cache` |
| 4 | Молчаливый fallback `git checkout main` при клоне сабмодуля | `ci-images.yml` (Clone prodavan-claw submodule) | образ может не соответствовать записанному указателю сабмодуля | fail fast вместо fallback |
| 5 | `COPY packages` до `npm ci` в обоих стейджах | `platform-openclaw/Dockerfile` (sdk-build, build) | любая правка исходников = полный `npm ci` (~2–3 мин ×2) | COPY манифестов → `npm ci` → COPY исходников |
| 6 | Ни одного `--mount=type=cache` (npm/apt/rustup) | `platform-openclaw/Dockerfile` | слои нельзя ускорить кэшем пакетных менеджеров | cache-mounts в RUN-слоях |
| 7 | `npm install --no-save @cursor/sdk … \|\| true` | `platform-openclaw/Dockerfile` (build stage) | незафиксированные версии vendor SDK, ошибки глотаются | pinned версии в lockfile; vendor SDK — опционально |
| 8 | `chown -R` отдельным слоем | `platform-openclaw/Dockerfile` (final) | +778 МБ мёртвого веса в образе | `COPY --chown=node:node` |
| 9 | devDeps летят в runtime (typescript, tsx, vendor SDK) | `COPY /app/node_modules` из build-стейджа | +~100 МБ и поверхность атаки; vendor SDK в проде не нужны (дефолт — свой tool-loop) | `npm ci --omit=dev` для final; vendor SDK лениво/отдельным тегом |
| 10 | `.dockerignore` не в корне build-контекста | claw repo root (нет) / `platform-openclaw/.dockerignore` (игнорируется) | весь claw-репо в контексте каждой сборки | `.dockerignore` в корне claw-репо |
| 11 | `:latest` + Always, дайджест нигде не пинится; откат = re-push | configmap, `pod_spec.py`, I18-заметка | невозможен точечный rollback; «какой digest в кластере» — только гадание | pod_service резолвит digest при создании Pod'а (P3) |

## 7. Бэклог (приоритеты)

- **P1 — кэш и достоверность:** единый cache backend для обеих сборок (`type=registry` в GHCR или `/cache` volume), smoke в claw-пайплайне, fail-fast на SHA сабмодуля. Эффект: тёплая сборка ~1–2 мин вместо 10–15 холодной.
- **P2 — Dockerfile:** re-порядок слоёв (манифесты → npm ci → исходники), `--mount=type=cache`, `COPY --chown=node:node`, `--omit=dev` для final, `.dockerignore` в корень claw, pinned vendor SDK. Эффект: образ ~1,2–1,8 ГБ (с сохранением тулчейна), slim-вариант ~600–800 МБ.
- **P3 — политика поставки:** один пайплайн-владелец сборки; digest-пиннинг в pod_service; ревизия тулчейна в образе (нужны ли Rust + Go + JDK + clang одновременно).

## 8. Связанные документы

- [runbook.md §1.1](runbook.md) — поток поставки (probe pod)
- [github-runner-local.md](github-runner-local.md) — раннеры и buildx cache
- [06-agent-runtime/platform-openclaw-runtime.md](../06-agent-runtime/platform-openclaw-runtime.md) — рантайм и контракт бриджа
- prodavan-claw: [`docs/15-image-delivery.md`](../../prodavan-claw/docs/15-image-delivery.md) — Dockerfile/openclaw-images as-built со стороны claw
- `infra/github-runner/README.md` — контейнеры раннеров, volume `prodavan-ci-cache`
