# Аудит infra/k8s и CI/CD для долгой миграции

Дата аудита: 2026-11-01  
Контекст: рабочая ветка/worktree `wt-migration`.  
Область: `infra/k3s`, `infra/argocd`, `.github/workflows`, образы, namespaces и текущая web-поставка. Код приложения и существующие инфраструктурные манифесты не изменялись.

## 1. Краткий вывод

1. Текущий frontend не React: это Flutter Web (`apps/flutter`), упакованный в nginx-образ `ghcr.io/ne-tort/agentscale-web`.
2. Backend — FastAPI/Python 3.12 в `ghcr.io/ne-tort/agentscale-api`; Alembic запускается initContainer через `./scripts/migrate.sh`, а не entrypoint приложения.
3. Основной dev-контур: Argo Application `agentscale-dev` отслеживает `main`, overlay `infra/k3s/overlays/dev`, namespace `agentscale-dev`; песочницы живут в `agentscale-dev-sandboxes`.
4. `agentscale-dev` автоматически sync/self-heal/prune. `infra/argocd/root-app.yaml` также отслеживает `main` и создаёт дочерние Applications из `infra/argocd/apps`.
5. Dev намеренно использует mutable `:latest` и `imagePullPolicy: Always` для first-party API/web/worker/beat. Это удобно для dev-поставки, но непригодно как основа воспроизводимого долгого migration preview.
6. Prod отделён: Argo `agentscale-prod` отслеживает ветку `prod`, namespace `agentscale`, образы API/web pinned SHA. Prod-ресурсы и overlay в этой работе не трогать.
7. Отдельного staging/preview контура в репозитории сейчас нет. `docs/07-infrastructure/env-matrix.md` упоминает staging, но фактических staging Argo Application/kustomize overlay/namespace не обнаружено.
8. Безопасный текущий подход: проверять migration-ветку локально (Flutter analyze/build, API unit/integration и Docker smoke с отдельным PostgreSQL), затем рендерить/валидировать kustomize без apply. Shared dev подключать только обычным PR-потоком; preview добавлять позже отдельным GitOps-контуром, не меняя `main`, `dev` или `prod` напрямую.

## 2. Фактическая схема поставки

### CI Gate и merge

`ci-gate.yml` запускается на PR в `main` и собирает четыре обязательных результата:

- `infra`: установка/кэширование kustomize, тесты `infra/ops`, `prodavan-ops validate`;
- `api`: reusable workflow `ci-api.yml`;
- `flutter`: reusable workflow `ci-flutter.yml`;
- `schemas`: reusable workflow `ci-schemas.yml`.

Итоговый job `gate` падает, если любой из четырёх результатов не `success`. `auto-merge.yml` после успешного Gate делает squash merge. Следовательно, долгую миграцию нельзя считать проверенной по одному локальному diff: минимум должен быть пройден тот же локальный контракт Gate, а для shared dev — Verify Dev.

### Сборка образов

`ci-images.yml` срабатывает после изменений в `apps/api`, `apps/flutter`, packages и workflow либо вручную:

- API собирается из корня репозитория по `apps/api/Dockerfile`;
- web собирается из корня по `apps/flutter/Dockerfile`, target `runtime`;
- при обычном push в ветку образ получает короткий SHA и одновременно `latest`;
- для PR предусмотрен `pr-local`, но workflow сейчас ориентирован на `main` push, поэтому этот PR-режим не является готовым preview deployment механизмом;
- API image smoke поднимает отдельный PostgreSQL container и запускает `./scripts/migrate.sh`;
- image jobs используют общий self-hosted Linux/Docker runner и persistent buildx builder `agentscale-ci`; сборки конкурируют за диск и кэш;
- searchnet намеренно не пересобирается обычным запуском, кроме явного `workflow_dispatch build_searchnet=true`.

После успешной сборки `trigger-verify` dispatch-ит `verify-dev.yml` для `main`.

### Verify Dev

`verify-dev.yml` работает только с dev-контуром:

1. получает kubeconfig runner-а;
2. проверяет namespace `agentscale-dev`;
3. `prodavan-ops rollout` перезапускает first-party workload;
4. ждёт Argo Application `agentscale-dev`;
5. выполняет DNS check из namespace;
6. выполняет HTTP smoke через Traefik на `127.0.0.1:8088`.

Это shared-environment проверка. Для migration preview её нельзя перенаправлять на dev: workflow жёстко зашит на `agentscale-dev`, а concurrency group `verify-dev` глобальная.

## 3. Kustomize и Argo

### Dev

`infra/argocd/apps/agentscale-dev.yaml`:

- repo: `https://github.com/ne-tort/agentscale.git`;
- `targetRevision: main`;
- path: `infra/k3s/overlays/dev`;
- destination namespace: `agentscale-dev`;
- automated sync, prune, self-heal, `CreateNamespace=true`, `Wait=false`.

`infra/k3s/overlays/dev/kustomization.yaml` подключает base, PostgreSQL, userdb init, sandbox layer и dev patch. Namespace transformer переводит namespaced resources в `agentscale-dev`; sandbox overlay отдельно переводит sandbox resources в `agentscale-dev-sandboxes` и сохраняет cross-namespace RBAC/NetworkPolicy связи.

Dev overlay подставляет:

- `ghcr.io/ne-tort/agentscale-api:latest`;
- `ghcr.io/ne-tort/agentscale-web:latest`.

Для first-party workload задан `imagePullPolicy: Always`, чтобы rollout фактически забирал новый digest `latest`. Это означает, что tag не является audit/rollback идентификатором.

Особенность overlay: установлен общий `argocd.argoproj.io/skip-health-check: "true"`. Argo не блокирует применение следующей ревизии из-за health gate, поэтому после sync нужно отдельно смотреть реальные readiness/startup probes и `prodavan-ops wait`.

### E2E

`agentscale-e2e` — отдельное manual-sync Argo Application на тот же `main`, path `infra/k3s/overlays/e2e`, destination `agentscale-dev`. Оно не является отдельным окружением: job и RBAC используют `agentscale-dev`, а sandbox resources — `agentscale-dev-sandboxes`. Это ephemeral test workload поверх dev-платформы, а не изолированный migration preview.

### Prod

`agentscale-prod` отслеживает `prod`, path `infra/k3s/overlays/prod`, destination `agentscale`. Sync automated/self-heal/prune, образы API/web pinned SHA в `overlays/prod/kustomization.yaml`. Sandbox image references в prod заменяются через kustomize replacement на тот же pinned API image.

Для текущей задачи prod-контур является границей безопасности: не выполнять sync/refresh, не менять `overlays/prod/**`, не применять ресурсы в namespace `agentscale`.

### Root Application

`infra/argocd/root-app.yaml` отслеживает `main` и path `infra/argocd/apps`. Поэтому добавление нового Application-файла в этот каталог и merge в `main` автоматически делает его видимым root-app. Это нельзя использовать для временного локального preview без заранее предусмотренной фильтрации/ручного источника: иначе preview станет частью общего GitOps state.

## 4. Namespaces и границы изоляции

| Контур | Argo source | Основной namespace | Sandbox namespace | Режим |
|---|---|---|---|---|
| Dev | `agentscale-dev` → `main` + `overlays/dev` | `agentscale-dev` | `agentscale-dev-sandboxes` | auto-sync/self-heal |
| E2E | `agentscale-e2e` → `main` + `overlays/e2e` | `agentscale-dev` | `agentscale-dev-sandboxes` | manual/CI orchestration |
| Prod | `agentscale-prod` → `prod` + `overlays/prod` | `agentscale` | `agentscale-sandboxes` | auto-sync/self-heal |
| Migration preview (сейчас) | отсутствует | отсутствует | отсутствует | не создавать в main/dev/prod этой задачей |

Новые preview resources нельзя направлять в `agentscale-dev`, `agentscale-dev-sandboxes`, `agentscale`, `agentscale-sandboxes`: это нарушит наблюдаемую изоляцию и может смешать DB/PVC/Keycloak/Redis/Kafka topics, service DNS, RBAC и dynamic sandbox claims.

Минимум для будущего preview: отдельные `agentscale-migration-<id>` и `agentscale-migration-<id>-sandboxes`, отдельные namespaced secrets/config, отдельные PVC и отдельные logical DB/tenant data. Cluster-scoped CRD/controller устанавливать повторно не нужно; нужно заранее проверить, что sandbox controller и его RBAC позволяют безопасный отдельный namespace.

## 5. Images и миграционные риски

### API

`apps/api/Dockerfile` копирует ORM, Alembic revisions и scripts в один image layer. Это снижает риск рассинхронизации модели и миграций. `migrate.sh` запускается initContainer; приложение стартует только после успешного upgrade/check.

Риск для preview: если preview использует shared PostgreSQL или shared persistent storage, `upgrade head` может изменить схему общего контура. Preview должен иметь отдельную DB/schema, а не только другой Deployment.

### Web

`apps/flutter/Dockerfile` имеет:

- `from-host` target для nginx + заранее собранного `apps/flutter/build/web`;
- `runtime` target с Flutter SDK build внутри image.

Web pod слушает port 80, probes идут на `/health`. Ingress маршрутизирует `/api` и `/health` в API, `/` в `agentscale-web`; Flutter использует same-origin API base, поэтому отдельный публичный API host для preview не обязателен.

Для локальной быстрой проверки можно использовать `from-host`, но только после `flutter build web`; отсутствие `apps/flutter/build/web` делает такой target непригодным. Полная image-сборка `runtime` тяжёлая из-за Flutter SDK и зависит от внешней загрузки SDK/pub cache.

### Mutable tags

В dev `latest` + `Always` означает:

- rollout может подобрать digest, который не виден из git diff;
- два параллельных preview/проверки на одном namespace неразличимы;
- rollback по tag невозможен;
- migration frontend/backend может быть проверена на разных image digest, если rollout/registry timing расходится.

Для долгой миграции preview использовать только immutable tags: `<git-sha>` или отдельный CI-generated image digest; в manifests фиксировать tag/digest API и web совместно. `latest` оставить только для существующего dev-контракта.

## 6. Безопасная локальная проверка без shared cluster

Ниже порядок, который не изменяет `main`, `dev`, `prod`, Argo или shared DB.

### 6.1 Backend unit и migration smoke

Из корня worktree:

```bash
cd apps/api
python -m venv .venv
# активировать venv способом текущей Windows shell
python -m pip install -e ".[dev]"
pytest -q
```

Для проверки Alembic поднимать отдельный локальный PostgreSQL container с уникальным именем/volume и отдельными dev-only credentials. Затем:

```bash
set DATABASE_URL=postgresql+asyncpg://agentscale:agentscale@127.0.0.1:<port>/agentscale_migration_local
alembic upgrade head
alembic check
```

Не использовать shared dev kubeconfig для этой проверки и не выполнять `kubectl exec ... alembic`, `alembic stamp` или ручной SQL DDL в shared environments. Для максимального совпадения с CI собрать API image из корня и запустить его `./scripts/migrate.sh` against that disposable PostgreSQL container; CI делает именно такой image-level smoke.

### 6.2 Flutter/Web

Это Flutter, не React. Локальный minimum:

```bash
cd apps/flutter
flutter pub get
flutter analyze
flutter test
flutter build web --release --build-name=1.0.0 --build-number=0 \
  --dart-define=BUILD_ID=migration-local \
  --dart-define=BUILD_NAME=1.0.0 \
  --dart-define=BUILD_NUMBER=0
```

Для browser smoke использовать `flutter run -d chrome` или локальный static server для `build/web`. API base должен указывать на локальный backend или same-origin reverse proxy; не направлять migration build в prod и не переопределять OIDC issuer на prod.

### 6.3 Local integration

Рекомендуемая безопасная связка:

- отдельный PostgreSQL container;
- при необходимости отдельные Redis/MinIO/Mongo/Redpanda/OpenSearch containers только для тестов, с уникальными именами и volumes;
- API на `127.0.0.1:<free-port>`;
- Flutter dev server на другом локальном порту;
- browser smoke по `/health/live`, `/health/ready`, frontend root и auth/API contract.

Если тесту нужен Kubernetes API/sandbox lifecycle, использовать disposable k3d/kind/minikube только вне git и только при наличии локальных CRD/controller prerequisites. Не считать такой кластер заменой shared dev: network policy, Traefik, storage class, GHCR pulls и agent-sandbox versions могут отличаться.

### 6.4 Render-only infra validation

До любого PR локально проверить синтаксис и итоговый render:

```bash
kustomize build infra/k3s/overlays/dev
kustomize build infra/k3s/overlays/e2e
kubectl apply --dry-run=client -k infra/k3s/overlays/dev
```

`apply --dry-run=client` не подключает полноценную серверную валидацию и не заменяет `prodavan-ops validate`; не запускать `kubectl apply` без dry-run в shared cluster. Для миграции preview сначала добавить render/validation в отдельную экспериментальную ветку, а не в текущий dev overlay.

## 7. Как позже завести migration preview environment

Цель: проверять длинную миграцию из отдельной ветки/коммита, не меняя `main`, текущий dev и prod.

### Предлагаемая модель

1. Создать отдельную ветку, например `migration-preview/<id>`; не коммитить preview wiring в `main`, `dev` или `prod`.
2. Добавить отдельный overlay в этой ветке, например `infra/k3s/overlays/migration-preview`, с namespace `agentscale-migration-<id>` и sandbox namespace `agentscale-migration-<id>-sandboxes`.
3. Создать отдельные API/web immutable image tags для одного commit SHA. Не использовать `latest`.
4. Добавить отдельные PostgreSQL database/PVC, object-storage bucket/prefix, Redis DB/key prefix, Kafka topics/consumer groups, Mongo/OpenSearch index prefix и Keycloak realm/client либо полностью отдельный Keycloak instance. Нельзя подключить preview API к dev/prod data только сменой namespace.
5. Ограничить Ingress отдельным host, например `migration-<id>.agentscale.dev`, или локальным hosts-file/port mapping. Не использовать wildcard host, который может выиграть у prod/dev router.
6. Добавить NetworkPolicy/RBAC allowlist только между preview app namespace и preview sandbox namespace. Не давать preview ServiceAccount права на dev/prod namespaces.
7. Создать отдельный Argo Application только после проверки манифестов. Предпочтительно источник — конкретная ветка/commit и path preview overlay, `automated.prune` включать только если lifecycle владельца и TTL определены.
8. В течение миграции держать `auto-sync` выключенным или sync window/manual approval, если preview содержит destructive schema/data operations. Preview CI должен явно запускать sync только в его Application.
9. Добавить TTL/cleanup policy: удаление Application, namespaces, PVC и registry tags после завершения ветки. Проверить orphaned resources и finalizers.
10. Проверять preview тем же набором: API migration smoke, frontend build/browser smoke, `kustomize build`, server-side dry-run против preview cluster, rollout/wait/smoke и sandbox e2e.

### Где хранить Argo wiring

Текущий root-app автоматически подхватывает все YAML из `infra/argocd/apps` с `main`. Поэтому будущий preview Application нельзя просто добавить в этот каталог и ожидать, что он останется веточным/локальным.

Безопасные варианты:

- отдельный Argo root/Application bootstrap, применяемый оператором в preview-кластер/namespace из конкретной ветки;
- отдельный repo/path, не отслеживаемый production/dev root-app;
- ApplicationSet с явным allowlist preview branches и namespace, если lifecycle/permissions уже проверены.

Для первого запуска предпочтителен отдельный ручной Application bootstrap с source `targetRevision: migration-preview/<id>`, destination только preview namespace/cluster, `CreateNamespace=true`, без доступа к dev/prod. Это временный операторский шаг; после стабилизации можно автоматизировать ApplicationSet.

### Preview CI

Нужен отдельный workflow, не расширение `verify-dev.yml`, со следующими свойствами:

- trigger: PR/dispatch для migration branch, не `push main`;
- concurrency key включает preview id;
- image jobs push immutable tags в GHCR и публикуют tag/digest как artifact/output;
- kustomize overlay получает API/web tags через `images` или generated values;
- kubeconfig и GitHub secret scoped на preview cluster/namespace;
- rollout/wait/smoke используют preview Application и preview host/port;
- никаких `prodavan-ops rollout` с hardcoded `agentscale-dev`;
- cleanup выполняется по закрытию PR/TTL, но после подтверждения отсутствия PVC/data, которые нужны для миграционного сравнения.

## 8. Ограничения и открытые решения

- Название “React app” не соответствует текущему репозиторию; перед миграцией нужно подтвердить, планируется ли замена Flutter или только миграция backend/runtime.
- Реальный staging не реализован, несмотря на таблицы env-matrix. Нельзя считать staging-значения готовым deployment contract.
- Текущий dev Argo `skip-health-check` снижает блокировки sync, но переносить это бездумно в migration preview нельзя: preview должен явно ждать миграционные initContainers и readiness.
- Shared k3s host содержит dev/prod контуры; namespace separation не гарантирует изоляцию node resources, local-path storage и cluster-scoped CRD/controller.
- Agent runtime image pin (`ghcr.io/ne-tort/prodavan-agent-runtime:58211a516231` в dev sandbox config/base) поставляется отдельным потоком из `prodavan-claw`; миграция API/frontend не должна неявно менять его.
- Secrets/SealedSecrets не должны копироваться в ветку/документы. Для preview нужны новые secret names и, по возможности, отдельные credentials с минимальными правами.

## 9. Checklist перед будущим preview

- [ ] Подтверждён frontend scope: Flutter migration или отдельный React target.
- [ ] Есть отдельная ветка и immutable commit/image tags.
- [ ] `kustomize build` и API image migration smoke зелёные.
- [ ] Preview namespaces и sandbox namespaces уникальны.
- [ ] DB/schema/PVC/object storage/queues/search индексы не shared с dev/prod.
- [ ] RBAC/NetworkPolicy проверены на отсутствие cross-namespace доступа к dev/prod.
- [ ] Ingress host не конфликтует с dev/prod catch-all rules.
- [ ] Argo Application не подхватывается текущим `agentscale-root` случайно.
- [ ] Preview workflow не вызывает hardcoded `verify-dev`/`agentscale-dev`.
- [ ] Есть ручной rollback на предыдущие immutable image tags и cleanup/TTL план.
- [ ] Prod overlay, branch и namespace не изменялись без отдельного явного запроса.

## 10. Источники в репозитории

- `infra/argocd/root-app.yaml`, `infra/argocd/apps/agentscale-dev.yaml`, `agentscale-e2e.yaml`, `agentscale-prod.yaml`.
- `infra/k3s/base/kustomization.yaml`, `base/ingress.yaml`, `base/agentscale-api/*`, `base/agentscale-web/*`.
- `infra/k3s/overlays/dev/*`, `overlays/e2e/*`, `overlays/prod/kustomization.yaml`.
- `.github/workflows/ci-gate.yml`, `ci-images.yml`, `verify-dev.yml`, `ci-api.yml`, `ci-e2e.yml`.
- `apps/api/Dockerfile`, `apps/api/README.md`, `apps/flutter/Dockerfile`, `apps/flutter/README.md`.
- `docs/07-infrastructure/runbook.md`, `github-actions.md`, `env-matrix.md`, `alembic.md`.
- `docs/08-migration/agent-sandbox-adr.md`.
