# Agent-runtime image: сборка → GHCR → Pod (as-built)

**Обновлено:** 2026-11-06 · **Статус:** as-built; P1+P2 реализованы (§7), дыра №11 закрыта пиннингом SHA-тега (§5–6)  
Поставка образа `prodavan-agent-runtime` (ядро агента, репо-подмодуль [`prodavan-claw`](https://github.com/ne-tort/prodavan-claw)) — от CI-сборки до Pod'а проекта. Как **должно** быть: (1) кэшированная сборка через CI-раннер, (2) образ с бинарником → GHCR, (3) кластер тянет образ из GHCR в Pod проекта (в sandbox-режиме образ берётся из `SandboxTemplate`, в legacy k8s-режиме — из `POD_AGENT_RUNTIME_IMAGE`). Ниже — как устроено сейчас и где дыры.

## 0. Поток (TL;DR)

```text
prodavan-claw PR → openclaw-ci (live-smokes: continue-on-error) → auto-merge
  → push в claw main → openclaw-images
     (registry-кэш type=registry, Dockerfile: toolchain stage + manifests-first)
     build-bridge → push {sha,latest} → smoke (артефакты + /health, green only)
  → trigger-verify → Verify Dev (ne-tort/prodavan)
      → prodavan-ops rollout → restart agentscale-probe-pod

Pod проекта: в sandbox-режиме (as-built) образ задан `SandboxTemplate agentscale-agent`,
  в legacy k8s-режиме — `POD_AGENT_RUNTIME_IMAGE`; оба пинят один и тот же
  SHA-тег (…:58211a516231) + secret ghcr-pull. imagePullPolicy: Always перечитывает
  манифест тега, а не «подтягивает свежий digest»: новый код приезжает только
  со сменой тега в манифестах и пересозданием Pod'а (reload/sync проекта).
```

Единственный владелец сборки — **prodavan-claw** (дубль-job `build-agent-runtime` в prodavan ci-images удалён — дыра №1 закрыта).

## 1. Сборка: единственный пайплайн — `openclaw-images.yml` в prodavan-claw

| | Как устроено (после 2026-09-24) |
|---|---|
| Триггер | push в claw main (после `openclaw-ci` + auto-merge из PR); path-фильтр `platform-openclaw/**`, `openclaw-sdk/**` |
| Контекст / Dockerfile | claw repo root / `platform-openclaw/Dockerfile` (toolchain-стейдж, manifests-first, `COPY --chown`, `.dockerignore` в корне) |
| Ревизия | HEAD claw main (указатель сабмодуля в prodavan — только метадата-пин и e2e-фикстура, в сборке не участвует) |
| Кэш Docker | buildx `type=registry` (`<image>-buildcache`, mode=max) — общий для vm-claw-раннеров, переживает пересоздание контейнеров; `concurrency`-группа на ref |
| Smoke | job `smoke`: артефакты (`dist/server.js`, `node_modules`) + `/health` 200 (в scratch docker network — host networking давал EADDRINUSE между раннерами) |
| Auth пуша | `GITHUB_TOKEN` claw (владелец GHCR-пакета — claw) |
| Теги | `openclaw-bridge:{sha,latest}` **и** `prodavan-agent-runtime:{sha,latest}` |
| После пуша | `trigger-verify` → dispatch `verify-dev.yml` в prodavan (только при green smoke) |

Историческая схема «два пайплайна на один `:latest`» (job `build-agent-runtime` в prodavan `ci-images.yml` с клоном по указателю сабмодуля, silent fallback `git checkout main`, local-cache в эфемерном `/tmp`) **удалена** — дыры №1–№5 закрыты. Замеры до фикса: `openclaw-images` failure 28м47с (uncached); `ci-images` 34м41с/45м41с, оба failure (codeload flake); `openclaw-ci` красный на docs-only push (дыра №12, исправлено `continue-on-error`).

## 2. GHCR и секреты

- Образ: `ghcr.io/ne-tort/prodavan-agent-runtime` (`{sha12,latest}`). GHCR-**package принадлежит репо `prodavan-claw`** → пуш из workflows prodavan возможен только через PAT `PRODAVAN_CLAW_TOKEN` (`write:packages`); `GITHUB_TOKEN` prodavan прав не имеет. Тот же PAT используется для клона приватного claw (GITHUB_TOKEN не умеет cross-repo).
- In-cluster pull: secret **`ghcr-pull`** (`kubernetes.io/dockerconfigjson`) в ns `agentscale-dev` и `agentscale-dev-sandboxes`, а той же bootstrap-логикой (`GHCR_NS` в `gitops-bootstrap.sh.tpl`) — ещё и в `agentscale`/`agentscale-sandboxes`, чтобы prod-контур тоже стартовал без ручных шагов. Источники: Terraform bootstrap (`TF_VAR_ghcr_token`; пустой токен = немедленное падение bootstrap'а) и SealedSecrets. Ссылается из platform Deployment'ов, Pod-спеки песочниц (`POD_SANDBOX_IMAGE_PULL_SECRET`) и probe-pod.

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

> **Раздел — исторический снимок (до 2026-09-24, эпоха Docker Desktop-раннеров).** Он
> объясняет, *почему* появились P1-фиксы, и не описывает текущее состояние: сейчас
> раннеры — systemd-сервисы на CI-VM (`vm-*`) с общим `/cache` и persistent buildx
> builder'ом, а `openclaw-images` использует `type=registry` (см. §1, §7 и
> [`infra/github-runner-vm/README.md`](../../infra/github-runner-vm/README.md)).

- **Local-cache в эфемерном месте.** `cache-from/to: type=local, /tmp/.buildx-cache-{api,web,agent}` лежит в ФС **контейнера раннера**. Раннеры — контейнеры (`infra/github-runner/`, `runner-1..4` + `claw-runner-1/2`), их `/tmp` — container-ephemeral: кэш теряется при пересоздании раннера (compose down -v, Docker Desktop/WSL restart) и **не разделяется между runner-1..4** (джоба на другом раннере = холодная сборка). Персистентный volume `prodavan-ci-cache` → `/cache` заведён именно для этого, но CI его **не использует**.
- **Масштаб проблемы (замер 2026-09-24):** workflow `CI Images` на main выполнялся **34м41с и 45м41с** (два запуска подряд) и оба завершились failure — на сетевой флаке скачивания action'ов (`codeload.github.com` timeouts, раннер в WSL). Холодная многогигабайтная сборка × сетевая ненадёжность = самый долгий и самый падающий этап поставки.
- **Registry/GHA cache backend не используется.** Ротация local-cache (rmtree + rename) может гонять конкурирующие сборки одного образа (workflow_dispatch поверх main push).
- **Dockerfile кэш-дружелюбности не имеет:** ни одного `--mount=type=cache` (единственный в организации — pip-маунт в `apps/api/Dockerfile:19`); `COPY packages` стоит **до** `npm ci` в обоих стейджах → любое изменение исходников перезапускает полный `npm ci`; монолитный apt+nvm+rustup RUN пересобирается целиком при любой правке Dockerfile.
- **`.dockerignore` не работает:** `platform-openclaw/.dockerignore` игнорируется Docker'ом (контекст = корень claw-репо, где dockerignore нет) → весь claw-репо уходит в контекст каждой сборки.

## 5. Потребление в кластере (Pod)

- Источник образа: `POD_AGENT_RUNTIME_IMAGE` (`apps/api/src/prodavan/config/settings.py`, default `…:local`; legacy-алиас `POD_AGENT_BRIDGE_IMAGE`). **Значение — SHA-тег, не `:latest`:** `base/agentscale-api/configmap.yaml:87` и `overlays/dev/patch-dev-config.yaml:84` пинят `ghcr.io/ne-tort/prodavan-agent-runtime:58211a516231` (коммит `prodavan-claw@58211a5`). Тот же пин стоит в `SandboxTemplate agentscale-agent` (`base/agentscale-sandboxes-ks/sandboxtemplate.yaml`) и в probe-pod. `POD_AGENT_RUNTIME_ENABLED: "true"`.
- Спека Pod'а: `infrastructure/k8s/sandbox/pod_spec.py` — контейнер `agent-runtime`, `imagePullPolicy: Always` (захардкожено, `pod_spec.py:101`), `imagePullSecrets: [ghcr-pull]`; init-контейнер `hydrate` (образ prodavan-api); при выключенном runtime — stub-holder (`sleep infinity`). `Always` здесь означает «сходить в реестр за манифестом тега», а не «взять последнюю сборку»: тег неизменяемый.
- Обновление до нового образа: **смена тега** в configmap/SandboxTemplate (PR в этом репо) → Argo sync → новый Pod поднимается на новой сборке; **запущенный** Pod — только через recreate (bump `hydrate_generation`/`bridge_generation` при materialize/reload/sync). Probe-pod перезапускается `prodavan-ops rollout` (он в `DEPLOYMENT_TARGETS`) и пинится тем же тегом с `imagePullPolicy: IfNotPresent` (`overlays/dev/sandboxes/probe-pod.yaml:46-47`) — restart нужен, чтобы подхватить смену пина, а не «свежий digest». Заметки I18 в dev-оверлее нет; единственное упоминание I18 — комментарий в `infra/argocd/apps/agentscale-dev.yaml` про api/web `:latest` и он к runtime не относится.

## 6. Карта дыр

| # | Дыра | Где | Влияние | Направление фикса |
|---|------|-----|---------|-------------------|
| 1 | ~~Двойной пайплайн~~ **закрыта 2026-09-24**: claw — единственный владелец, `build-agent-runtime` удалён из `ci-images.yml` (PR #445) | — | — | — |
| 2 | ~~Сборка без кэша и smoke~~ **закрыта**: `type=registry` кэш + job `smoke` (артефакты + `/health`) гейтит trigger-verify | — | — | — |
| 3 | ~~Local-cache в эфемерном `/tmp`~~ **закрыта**: buildx `type=registry` (`<image>-buildcache`) — общий для раннеров, переживает их пересоздание; гонки ротации сняты `concurrency`-группой | — | — | — |
| 4 | ~~Молчаливый fallback~~ **закрыта**: клона сабмодуля в image-CI больше нет (job удалён) | — | — | — |
| 5 | ~~COPY-до-npm-ci~~ **закрыта**: manifests-first в обоих стейджах (верифицировано локально) | — | — | — |
| 6 | ~~Нет cache-mounts~~ **закрыта**: npm (`/root/.npm`) + apt (`/var/cache/apt`, `/var/lib/apt`) | — | — | — |
| 7 | ~~--no-save + \|\| true~~ **закрыта**: pinned версии из bridge devDeps, ошибки loud | — | — | — |
| 8 | ~~chown -R~~ **закрыта**: `COPY --chown=node:node` (−778 МБ) | — | — | — |
| 9 | devDeps летят в runtime (typescript, tsx, vendor SDK) | `COPY /app/node_modules` из build-стейджа | +~100 МБ и поверхность атаки; vendor SDK в проде не нужны (дефолт — свой tool-loop) | `npm ci --omit=dev` для final; vendor SDK лениво/отдельным тегом |
| 10 | ~~.dockerignore~~ **закрыта**: `.dockerignore` в корне claw (excludes references/, docs/, .git, node_modules) | — | — | — |
| 11 | ~~`:latest` + Always, дайджест нигде не пинится~~ **закрыта**: `POD_AGENT_RUNTIME_IMAGE` (`base/agentscale-api/configmap.yaml:87`, `overlays/dev/patch-dev-config.yaml:84`), `SandboxTemplate` и probe-pod пинят **один и тот же** SHA-тег `58211a516231`; `imagePullPolicy: Always` остался только у динамических `pod_spec.py`, где он безвреден (тег неизменяемый) | — | — | Остаток: пин обновляется руками (PR в этом репо) — автоматической связи «новая сборка claw → новый тег» нет; точечный rollback = смена тега на предыдущий |
| 12 | ~~Optional live smokes фейлят pipeline~~ **закрыта**: `continue-on-error: true` на всех live-шагах `openclaw-ci.yml` (внешний API-флейк больше не красит push) | — | — | — |
| 13 | Все 6 раннеров (dd-pv-1..4 + dd-claw-1/2) — контейнеры на **одном Docker Desktop хосте** с общим docker.sock | `infra/github-runner/docker-compose.yml` | тяжёлые сборки делят CPU/IO/диск с CI Gate приложения | **частично смягчено**: registry-кэш + manifests-first резко сократили длительность сборок; `concurrency` убрал гонки. Полностью — отдельный docker daemon для image-сборок |

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
| prodavan → claw | PAT-клон в CI (`ci-e2e.yml`, job `bridge`) | **e2e-фикстура и сборка образа под тест**: `tests/e2e/bridge/test_bridge_openclaw_mcp.py` монтирует исходники claw (README + openclaw-sdk echo server) в тестовый контейнер; `bridge` собирает `prodavan-agent-runtime:e2e` локально (`docker build -f platform-openclaw/Dockerfile`). Джобы `build-agent-runtime` в `ci-images.yml` больше нет |
| prodavan → claw | GHCR pull (secret `ghcr-pull`) | pod_service тянет образ в Pod'ы |
| claw → prodavan | `trigger-verify` → dispatch `verify-dev.yml` (секрет `PRODAVAN_REPO_TOKEN`) | перезапуск probe-pod, чтобы он подхватил смену пина (сам тег меняется PR'ом в этом репо) |
| claw → GHCR | `openclaw-images.yml` push | **владелец GHCR-пакета** `prodavan-agent-runtime` |
| runtime | HTTP-контракт бриджа (OpenAPI) + `POD_AGENT_RUNTIME_IMAGE` | единственная рантайм-зависимость; **исходников claw приложение в рантайме не использует** |

### 8.2 Ответы на ключевые вопросы

**Подхватит ли кластер образ, если claw собирается и пушится сам?** Сам по себе — **нет**. Образ запинен SHA-тегом (`58211a516231`) в трёх местах: `POD_AGENT_RUNTIME_IMAGE` (base configmap + dev overlay), `SandboxTemplate` и probe-pod. Новая сборка claw попадает в кластер только после PR в этом репо, который меняет тег; дальше Argo синхронизирует ConfigMap/SandboxTemplate, новые Pod'ы (создание проекта, recreate по generation bump) поднимаются на новой сборке, probe-pod — после `prodavan-ops rollout` (его перезапускает `trigger-verify` → Verify Dev). Уже запущенные Pod'ы проектов обновляются только при recreate (reload/sync проекта). Плюс запина: одинаковый тег у API-конфига и у шаблона песочницы гарантирует, что Pod песочницы и API работают на одной сборке — с `:latest` они расходились после каждого мержа.

**Должен ли claw собираться и пушиться изолированно?** Да — и почти уже так: claw владеет GHCR-пакетом, итерации идут в claw main, delivery — main-push. Вторая сборка (`build-agent-runtime` в ci-images) — чистый дубль: те же исходники, тот же `:latest`, гонка тегов (дыра №1), лишние 30–45 мин CI на каждый push в main. Рекомендация: **claw — единственный владелец build+push**; из `ci-images.yml` job удалить (или свести к smoke уже опубликованного образа).

**Должен ли claw быть сабмодулем вообще?** Рантайм-зависимости от исходников нет — контракт это HTTP API + digest образа. Функциональные роли сабмодуля сегодня: (1) e2e-фикстура prodavan монтирует исходники claw; (2) метадата-пин для трассируемости. Рекомендация: **сабмодуль оставить** (клон нужен e2e, пин почти бесплатен), но убрать из него сборку образа. В перспективе (P3) digest-пиннинг в pod_service сделает сабмодуль чистой метадатой.

### 8.3 Раннеры: топология

- agentscale: `vm-as-1..5` (systemd-сервисы `actions.runner.ne-tort-agentscale.vm-as-*` на CI-VM); claw: `vm-claw-1/2` (`actions.runner.ne-tort-prodavan-claw.vm-claw-*`). Лейблы одинаковые (`self-hosted,linux,docker,ci-vm`) — джоба уходит в пул своего репо (repo scope). Бутстрап и эксплуатация — [`infra/github-runner-vm/`](../../infra/github-runner-vm/README.md).
- Все семь — сервисы **одной VM** (`www@172.31.156.203`, 8 vCPU / 15 GiB), делят один docker daemon и один persistent buildx builder: сборка на `vm-claw-*` конкурирует за CPU/IO с CI Gate — и с dev-кластером, который живёт на той же машине (~12 ГиБ RAM). Отсюда же последовательная цепочка job'ов в `ci-api.yml` (см. [github-actions.md](github-actions.md)).
- Общий кэш — `/cache` (pip/Poetry/pnpm/pub/Flutter SDK/kubectl/kustomize/toolcache) + buildx builder `agentscale-ci` (`docker-container`, том `buildx_buildkit_agentscale-ci0_state`, GC 20 GiB / 7 дней).

### 8.4 Целевая схема (рекомендация)

```text
claw (владелец образа):
  PR/main: openclaw-ci — unit + doctor; live-smokes → continue-on-error / отдельный scheduled job
  main push: openclaw-images (на vm-claw-1/2)
    кэш: type=registry (GHCR cache-manifest); --mount=type=cache npm/apt
    Dockerfile: манифесты → npm ci → исходники; npm ci --omit=dev; COPY --chown; .dockerignore; pinned vendor SDK
    → smoke (артефакты + /health) → push {sha, latest} в GHCR
  → trigger-verify: rollout probe-pod (единственный кластерный шаг)
      + PR в agentscale, который меняет SHA-тег в configmap/SandboxTemplate/probe-pod

agentscale (потребитель):
  POD_AGENT_RUNTIME_IMAGE + SandboxTemplate: SHA-тег (обновляется PR'ом) + ghcr-pull
  ci-e2e: клон сабмодуля для фикстуры (как сейчас)
  ci-images::build-agent-runtime — удалён
```

## 9. Связанные документы

- [runbook.md §1.1](runbook.md) — поток поставки (probe pod)
- [github-runner-local.md](github-runner-local.md) — раннеры и buildx cache
- [`infra/github-runner-vm/README.md`](../../infra/github-runner-vm/README.md) — VM-раннеры, `/cache`, диск-гигиена (живая схема; `infra/github-runner/` — историческая)
- [k3s-services.md](k3s-services.md) — SandboxTemplate/проба и пины в кластере
- [06-agent-runtime/platform-openclaw-runtime.md](../06-agent-runtime/platform-openclaw-runtime.md) — рантайм и контракт бриджа
- prodavan-claw: [`docs/15-image-delivery.md`](../../prodavan-claw/docs/15-image-delivery.md) — Dockerfile/openclaw-images as-built со стороны claw
