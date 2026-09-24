# Agent-runtime image: сборка → GHCR → Pod (as-built)

**Обновлено:** 2026-09-24 · **Статус:** as-built; P1+P2 реализованы (см. §7)  
Поставка образа `prodavan-agent-runtime` (ядро агента, репо-подмодуль [`prodavan-claw`](https://github.com/ne-tort/prodavan-claw)) — от CI-сборки до Pod'а проекта. Как **должно** быть: (1) кэшированная сборка через CI-раннер, (2) образ с бинарником → GHCR, (3) pod_service тянет образ из GHCR в Pod проекта. Ниже — как устроено сейчас и где дыры.

## 0. Поток (TL;DR)

```text
prodavan-claw PR → openclaw-ci (live-smokes: continue-on-error) → auto-merge
  → push в claw main → openclaw-images
     (registry-кэш type=registry, Dockerfile: toolchain stage + manifests-first)
     build-bridge → push {sha,latest} → smoke (артефакты + /health, green only)
  → trigger-verify → Verify Dev (ne-tort/prodavan)
      → prodavan-ops rollout → restart prodavan-probe-pod (re-pull :latest)

Pod проекта: pod_service создаёт Pod c POD_AGENT_RUNTIME_IMAGE (:latest)
  + imagePullPolicy: Always + secret ghcr-pull → новый Pod = свежий digest;
  запущенный Pod обновляется только recreate (generation bump).
```

Единственный владелец сборки — **prodavan-claw** (дубль-job `build-agent-runtime` в prodavan ci-images удалён — дыра №1 закрыта).

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

**Замеры 2026-09-24:** `openclaw-images` — failure после **28м47с** (uncached, без smoke), следующий запуск — in_progress >27 мин; `ci-images` на main — **34м41с** и **45м41с**, оба failure (сетевой флак скачивания action'ов, `codeload.github.com`). Плюс `openclaw-ci` в claw **красный на docs-only push**: optional live smoke (`smoke-live-cursor`, `api_error` от реального Cursor API) валит job — см. дыру №12.

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
- **Масштаб проблемы (замер 2026-09-24):** workflow `CI Images` на main выполнялся **34м41с и 45м41с** (два запуска подряд) и оба завершились failure — на сетевой флаке скачивания action'ов (`codeload.github.com` timeouts, раннер в WSL). Холодная многогигабайтная сборка × сетевая ненадёжность = самый долгий и самый падающий этап поставки.
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
| 12 | Optional live smokes фейлят pipeline: `smoke-live-cursor` (реальный Cursor API, `api_error`) валит `openclaw-ci` **на docs-only push** | `prodavan-claw/.github/workflows/openclaw-ci.yml` | main claw регулярно красный без реальной причины; красный CI приучает игнорировать | `continue-on-error` для live-смоуков или вынести в отдельный scheduled job |
| 13 | Все 6 раннеров (dd-pv-1..4 + dd-claw-1/2) — контейнеры на **одном Docker Desktop хосте** с общим docker.sock | `infra/github-runner/docker-compose.yml` | 28-минутная uncached сборка образа делит CPU/IO/диск с CI Gate приложения — тяжёлые сборки душат остальные джобы | тот же P1-кэш (сократить длительность сборки); `concurrency:` group в image-workflow; в перспективе — отдельный демон для image-сборок |

## 7. Бэклог: статус (P1+P2 реализованы 2026-09-24)

**Сделано** (claw `46090c7` + PR #445 в prodavan):

- ✅ **P1 кэш:** `openclaw-images.yml` — buildx кэш `type=registry` (`<image>-buildcache`, mode=max): общий для dd-claw-раннеров, переживает пересоздание контейнеров; `concurrency`-группа на ref. ✅ **P1 smoke:** новый smoke-job (артефакты + `/health`) гейтит trigger-verify. ✅ **P1 fail-fast**: отпало вместе с удалением дубль-джобы (нет клона сабмодуля в image-CI). ✅ **P1 один пайплайн:** `build-agent-runtime` удалён из `ci-images.yml`; claw — единственный владелец.
- ✅ **P2 Dockerfile:** стейдж `toolchain` (apt/nvm/rustup в 3 RUN с cache-mounts; пересборка только при правке списка пакетов), manifests-first COPY (правка `.ts` не перезапускает `npm ci` — верифицировано), pinned vendor SDK (без `|| true`), `COPY --chown` вместо `chown -R` (−778 МБ), `.dockerignore` в корне claw. Образ **6,27 ГБ → 4,37 ГБ**, health OK. ✅ **P2 live-smokes:** `continue-on-error` в `openclaw-ci.yml`.
- ⚠️ **P2 `--omit=dev`:** отложено (~50 МБ, devDeps остаются в runtime node_modules) — мик-оптимизация, отдельным PR при желании.

**Осталось (P3):**

- **P3 — политика поставки:** digest-пиннинг в pod_service (точечный rollback); ревизия тулчейна: нужны ли Rust + Go + JDK + clang одновременно (~3,7 ГБ; slim-вариант ~600–800 МБ); devDeps `--omit=dev`.

## 8. Кто владеет сборкой: prodavan ↔ claw

### 8.1 Инвентарь связей

| Связь | Механизм | Назначение |
|-------|----------|-----------|
| prodavan → claw | submodule pointer в git tree | пин «какой claw known-good на момент релиза приложения» |
| prodavan → claw | PAT-клон в CI (`ci-images.yml` build-agent-runtime, `ci-e2e.yml`) | сборка образа из исходников (дубль!) и **e2e-фикстура**: `tests/e2e/bridge/test_bridge_openclaw_mcp.py:135–143` монтирует исходники claw (README + openclaw-sdk echo server) в тестовый контейнер |
| prodavan → claw | GHCR pull (secret `ghcr-pull`) | pod_service тянет образ в Pod'ы |
| claw → prodavan | `trigger-verify` → dispatch `verify-dev.yml` (секрет `PRODAVAN_REPO_TOKEN`) | перезапуск probe-pod после нового `:latest` |
| claw → GHCR | `openclaw-images.yml` push | **владелец GHCR-пакета** `prodavan-agent-runtime` |
| runtime | HTTP-контракт бриджа (OpenAPI) + `POD_AGENT_RUNTIME_IMAGE` | единственная рантайм-зависимость; **исходников claw приложение в рантайме не использует** |

### 8.2 Ответы на ключевые вопросы

**Подхватит ли кластер образ, если claw собирается и пушится сам?** Да. `pod_service` создаёт Pod проекта с `:latest` + `imagePullPolicy: Always` — любой **новый** Pod (создание проекта, recreate по generation bump) тянет свежий digest c GHCR без всякого участия CI приложения. Два исключения: уже запущенные Pod'ы обновляются только при recreate (reload/sync проекта), а статический probe-pod — только через `kubectl rollout restart`, что и делает `trigger-verify` → Verify Dev. То есть prodavan-CI **не нужен для доставки** образа; без trigger-verify обновятся только новые/пересозданные поды.

**Должен ли claw собираться и пушиться изолированно?** Да — и почти уже так: claw владеет GHCR-пакетом, итерации идут в claw main, delivery — main-push. Вторая сборка (`build-agent-runtime` в ci-images) — чистый дубль: те же исходники, тот же `:latest`, гонка тегов (дыра №1), лишние 30–45 мин CI на каждый push в main. Рекомендация: **claw — единственный владелец build+push**; из `ci-images.yml` job удалить (или свести к smoke уже опубликованного образа).

**Должен ли claw быть сабмодулем вообще?** Рантайм-зависимости от исходников нет — контракт это HTTP API + digest образа. Функциональные роли сабмодуля сегодня: (1) e2e-фикстура prodavan монтирует исходники claw; (2) метадата-пин для трассируемости. Рекомендация: **сабмодуль оставить** (клон нужен e2e, пин почти бесплатен), но убрать из него сборку образа. В перспективе (P3) digest-пиннинг в pod_service сделает сабмодуль чистой метадатой.

### 8.3 Раннеры: топология

- prodavan: `dd-pv-1..4` (repo-scoped `ne-tort/prodavan`); claw: `dd-claw-1/2` (repo-scoped `ne-tort/prodavan-claw`, compose-профиль `claw`; в compose: «private repo cannot use ubuntu-latest»). Labels идентичны (`self-hosted,linux,docker,docker-desktop`) — джоба уходит в пул своего репо (repo scope).
- Но все шесть — контейнеры **одного Docker Desktop хоста** с общим `docker.sock`: 28-минутная uncached сборка на `dd-claw-2` делит CPU/IO/диск с CI Gate приложения → наблюдение «заняты раннеры основного проекта» на уровне хоста справедливо (дыра №13).
- `prodavan-ci-cache:/cache` смонтирован во **все** шесть раннеров — общий кэш-том уже есть, не используется ни одним пайплайном.

### 8.4 Целевая схема (рекомендация)

```text
claw (владелец образа):
  PR/main: openclaw-ci — unit + doctor; live-smokes → continue-on-error / отдельный scheduled job
  main push: openclaw-images (на dd-claw-1/2)
    кэш: type=registry (GHCR cache-manifest) или type=local на /cache; --mount=type=cache npm/apt
    Dockerfile: манифесты → npm ci → исходники; npm ci --omit=dev; COPY --chown; .dockerignore; pinned vendor SDK
    → smoke (артефакты + /health) → push {sha, latest} в GHCR
  → trigger-verify: rollout probe-pod (единственный кластерный шаг; project pods подхватят сами)

prodavan (потребитель):
  pod_service: POD_AGENT_RUNTIME_IMAGE (:latest → в перспективе digest) + ghcr-pull
  ci-e2e: клон сабмодуля для фикстуры (как сейчас)
  ci-images::build-agent-runtime — удалить
```

## 9. Связанные документы

- [runbook.md §1.1](runbook.md) — поток поставки (probe pod)
- [github-runner-local.md](github-runner-local.md) — раннеры и buildx cache
- [06-agent-runtime/platform-openclaw-runtime.md](../06-agent-runtime/platform-openclaw-runtime.md) — рантайм и контракт бриджа
- prodavan-claw: [`docs/15-image-delivery.md`](../../prodavan-claw/docs/15-image-delivery.md) — Dockerfile/openclaw-images as-built со стороны claw
- `infra/github-runner/README.md` — контейнеры раннеров, volume `prodavan-ci-cache`
