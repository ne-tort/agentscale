# `plugin-platform.yaml` и AppProject `agentscale` — справка для родителя

**Статус:** справочный файл задачи №15 (`docs/migration/plugin-migration-plan.md`), НЕ манифест Kubernetes и НЕ правка `00-project.yaml`.
**Область:** `infra/argocd/apps/plugin-platform.yaml`, `infra/argocd/apps/00-project.yaml` (только читали, **не редактировали**).
**Источники:** `docs/ARCHITECTURE/plugin-architecture-complete.md` v4 (§1 «Что запрещено «просто добавить»», §5.3, §7.1, §7.4, §10, §12 п.7, §14 п.9), `docs/migration/plugin-migration-plan.md` (§4), `infra/k3s/base/guards/app-budget.yaml`.

---

## 1. Почему в `00-project.yaml` НЕ добавлены новые kinds

`AppProject agentscale` содержит жёсткий `namespaceResourceWhitelist` — **27 kinds** (посчитано по файлу: 4 CRD `Sandbox*`/`extensions.*`, ConfigMap, Secret, Service, ServiceAccount, PVC, SealedSecret, Deployment, StatefulSet, Job, CronJob, PodDisruptionBudget, LimitRange, ResourceQuota, Role, RoleBinding, Ingress, NetworkPolicy, IngressRoute). Пустой whitelist значил бы «разрешить все kinds»; здесь он намеренно закреплён под то, что реально поставляют оверлеи.

Бюджет узла (`infra/k3s/base/guards/app-budget.yaml`): **45 подов**, `requests.memory: 12Gi`, `limits.memory: 26Gi`; зафиксированы **3 OOM-инцидента** (2026-10-21/22/23, см. `docs/07-infrastructure/runbook.md` и `infra/ops/tests/test_oom_guards.py`). Single-node k3s; АБСОЛЮТНО любой новый control plane eats из этого же бюджета.

| Группа kinds | Что это | Почему НЕ добавляем в v4 |
|---|---|---|
| `keda.sh/*` (`ScaledObject`, `TriggerAuthentication`), `http.keda.sh/*` (`HTTPScaledObject`) | KEDA + HTTP Add-on (0→1 по HTTP) | §7.4: достойная цель на будущее, но сейчас beta; требует **+4–6 подов** control plane и новых CRD. `00-project.yaml` не содержит `clusterResourceWhitelist` для CRD `apiextensions.k8s.io` (это есть только у проекта `agent-sandbox`), поэтому даже установить CRD через проект `agentscale` нельзя без его правки. Задача будущего (§12 п.7). Сейчас 0→1 делает Plugin Gateway патчем `deployments/scale` + `ignoreDifferences`. |
| `autoscaling/HPA` (`HorizontalPodAutoscaler`) | KEDA создаёт HPA под каждым `ScaledObject` | Добавлять без KEDA бессмысленно, а вместе с KEDA — тот же +4–6 подов и дрейф, который selfHeal будет чинить. HPA вдобавок конфликтует с `replicas: 0` activator'а. |
| `serving.knative.dev/*` (`Service`, `Configuration`, `Revision`, `Route`) | Knative Serving | §13.1: scale-to-zero/cold start 100–500 мс не соответствует этому кластеру (pull образа идёт секунды/десятки секунд), это **+8–12 подов и второй ingress-стек**. В §0 прямо: «Не в v1 реализации — Knative». |
| `cert-manager.io/*` (`Certificate`, `ClusterIssuer`, `Issuer`, `CertificateRequest`), `acme.cert-manager.io/*` (`Challenge`, `Order`) | cert-manager + ACME DNS-01 | §7.1 / `docs/07-infrastructure/k3s-services.md`: в кластере TLS = self-signed **default TLSStore** (`kube-system/agentscale-tls`), его SAN уже покрывает `agentscale.dev`/`localhost`/IP. Path-based `plugins-gateway` НЕ требует wildcard-сертификата. Переход на wildcard subdomain (`{company}--{plugin}.plugins.agentscale.dev`, §5.3) отложен до появления (1) реального публичного домена, (2) DNS-01 солвера — in-tree солвера для reg.ru нет, нужен Cloudflare/acme-dns/проверенный webhook, (3) новых kinds в whitelist. Cert-manager = ещё control plane против 12Gi. |
| `SPIRE`/`spiffe.io/*`, `temporal.io/*` | mTLS/SPIFFE, Temporal | §4.1 и §7.3: mTLS отложен (достаточно short-lived signed token с `kid`/JWKS), Celery beat остаётся для cron, KEDA/Temporal/k8s Job-runner не вводятся. |

`docs/migration/plugin-migration-plan.md` §4 фиксирует: «KEDA / Knative / SPIRE / cert-manager в v1 / Temporal — не вводим (whitelist 27 kinds, 45 подов, 12Gi requests, 3 OOM)».

**ВЫВОД (fail-closed):** манифест `plugin-platform.yaml` не содержит ни одного `kind` из `{ScaledObject, HTTPScaledObject, Service (knative), Certificate, ClusterIssuer, HorizontalPodAutoscaler}`; это закреплено тестом `infra/ops/tests/test_plugin_ingress_priority.py`. `00-project.yaml` в этой задаче **не редактировался** — плагиновый слой укладывается в уже разрешённые kinds: Deployment, Service, ConfigMap, Secret, ServiceAccount, PersistentVolumeClaim, Role, RoleBinding, Ingress, NetworkPolicy.

### 1.1 Что нужно будет добавить ПРИ ПЕРЕХОДЕ НА KEDA / cert-manager (задача будущего)

> Не делать сейчас. Чек-лист для отдельной задачи после решения §12 п.6/п.7 и пересмотра бюджета.

**Вариант A — KEDA HTTP Add-on вместо собственного activator'а:**
1. Установить сам KEDA и HTTP Add-on как отдельный Argo `Application` (новый файл `infra/argocd/apps/plugin-keda.yaml`, проект `agent-sandbox` или новый AppProject — у `agentscale` нет `clusterResourceWhitelist` на `CustomResourceDefinition`).
2. Добавить в `00-project.yaml` → `spec.namespaceResourceWhitelist`: `keda.sh/ScaledObject`, `keda.sh/TriggerAuthentication`, `http.keda.sh/HTTPScaledObject`, `autoscaling/HorizontalPodAutoscaler` (+ при необходимости `keda.sh/ScaledJob`).
3. Добавить CRD `apiextensions.k8s.io/CustomResourceDefinition` в `clusterResourceWhitelist` `00-project.yaml` **или** (предпочтительнее) оставить установку CRD в отдельном проекте с уже существующим разрешением (`00-agent-sandbox-project.yaml`).
4. Пересчитать `infra/k3s/base/guards/app-budget.yaml`: +4–6 подов control plane (KEDA operator, metrics-apiserver, HTTP add-on operator/interceptor/external-scaler) против лимита `pods: "45"` и `requests.memory: 12Gi`; при 3 OOM-инцидентах это отдельное решение, а не «добавить строчку».
5. Убрать/пересмотреть `ignoreDifferences` для `/spec/replicas`: HPA станет владельцем `replicas`, и собственный activator больше не должен патчить scale.
6. Обновить acceptance №13 (e2e activator) и `infra/ops/tests/test_plugin_ingress_priority.py` — проверка «нет KEDA kinds» станет проверкой «KEDA kinds разрешены».

**Вариант B — cert-manager для wildcard subdomain (§5.3):**
1. Реальный публичный домен + валидный TLS вместо self-signed TLSStore.
2. DNS-01: делегировать `_acme-challenge` на Cloudflare/acme-dns или проверенный webhook (in-tree солвера для reg.ru нет).
3. Добавить в `00-project.yaml` → `namespaceResourceWhitelist`: `cert-manager.io/Certificate`, `cert-manager.io/ClusterIssuer` (и `Issuer`, если issuer namespaced); в `clusterResourceWhitelist` — `cert-manager.io/ClusterIssuer` при использовании cluster-scoped issuer; CRD — через отдельный AppProject.
4. Выделить ОДИН wildcard `Certificate`, не N сертификатов на плагин; новый Argo `Application` для cert-manager control plane; снова пересчитать бюджеты подов/памяти.
5. Переделать `plugins-gateway` с path-based на `IngressRoute`/wildcard host; пересмотреть `router.priority` 120 и тест приоритетов.
6. Снять противоречие §5.2 (отдельный origin, без `allow-same-origin`) с path-based routing'ом — subdomain как раз и даёт настоящую изоляцию cookie/CSP/`event.origin`.

Оба варианта требуют ADR и явного решения о верификации; в v4 они **не реализуются**.

---

## 2. Что уже сделано в `plugin-platform.yaml` (кратко)

| Поле | Значение | Причина |
|---|---|---|
| `spec.project` | `agentscale` | тот же AppProject, не `default` |
| `source.path` | `infra/k3s/overlays/dev/plugins` | dev-only; альтернатива `infra/k3s/base/plugins` — в комментарии на выбор родителя |
| `sync-wave` (Application) | `"1"` | после CRD `agent-sandbox` (-5) и `agentscale-dev` (0). НЕ путать с `"25"` на Ingress |
| `automated.selfHeal` | `true` | по задаче |
| `automated.prune` | `false` | fail-closed: не удалять динамически созданные Deployment'ы плагинов |
| `syncOptions` | `CreateNamespace=true`, `RespectIgnoreDifferences=true`, `Wait=false` | `RespectIgnoreDifferences` обязателен для replicas |
| `ignoreDifferences` | `group: apps / kind: Deployment / jsonPointers: [/spec/replicas]` (+ `jqPathExpressions: [.spec.replicas]`) | см. §3 |
| `destination.namespace` | `agentscale-dev` (альтернатива `agentscale-dev-sandboxes`) | оба уже в `00-project.yaml` |

---

## 3. Что родитель ДОЛЖЕН внести в `00-project.yaml` при появлении НОВОГО namespace плагинов

Сейчас `spec.destinations` проекта `agentscale` (строки 18–31): `agentscale`, `agentscale-sandboxes`, `agentscale-dev`, `agentscale-dev-sandboxes`, `agent-sandbox-system` — все с `server: https://kubernetes.default.svc`. Этого достаточно для текущего дизайна (§6.2: NP `plugin-egress` в `agentscale-dev-sandboxes`, Plugin Gateway/Ingress в `agentscale-dev`).

Если родитель решит вынести поды плагинов в отдельный namespace (например `agentscale-dev-plugins` или `plugins`), то **без правки `00-project.yaml` синк упадёт целиком** с `destination namespace ... is not permitted by project agentscale` — Argo откажет в применении ВСЕГО приложения, а не только «лишнего» ресурса (тот же fail-closed механизм, что описан в комментарии про `LimitRange`/`ResourceQuota` в `00-project.yaml`).

Обязательный чек-лист правок `00-project.yaml` (выполняет ТОЛЬКО родитель, этот файл не в зоне задачи №15):

1. `spec.destinations` — добавить запись (fail-closed: синк падает ЦЕЛИКОМ):
   ```yaml
   - namespace: <новый-namespace>
     server: https://kubernetes.default.svc
   ```
   Не использовать `namespace: '*'`: это размоет repo/namespace-scoping пострадавшего проекта.
2. `spec.sourceRepos` — трогать НЕ надо, если манифесты плагинов остаются в `https://github.com/ne-tort/agentscale.git`.
3. `spec.namespaceResourceWhitelist` — проверить, что все kinds нового слоя уже перечислены; при появлении новых control plane (KEDA/cert-manager/HPA) см. §1.1. Не добавлять «на всякий случай».
4. `spec.clusterResourceWhitelist` — только если новый слой приносит cluster-scoped объекты (CRD, ClusterRole); у `agentscale` сейчас разрешён лишь `"" / Namespace`.
5. `spec.orphanedResources.ignore` — при необходимости добавить динамические объекты плагинов, чтобы `warn: true` не засыпал оператора ложными сиротами.
6. Бюджеты: если новый namespace НЕ покрыт `infra/k3s/base/guards/app-budget.yaml` (он через NamespaceTransformer работает на `agentscale*`), добавить `LimitRange`/`ResourceQuota`/`node-manifests/platform-guards.yaml` — иначе поды плагинов уйдут в BestEffort и станут первой жертвой OOM-killer'а (инцидент 2026-10-23).
7. Синхронизировать `root-app.yaml` / `root-app-prod.yaml`: dev-root подхватит `plugin-*.yaml` автоматически, prod-root — нет (политика dev-only, см. комментарий в `plugin-platform.yaml`). Включать плагины на prod-хосте можно только отдельным явным решением.
8. Обновить `infra/argocd/README.md` (список `apps/`) — тоже зона родителя.
9. Проверить `NetworkPolicy` §6.2: `namespaceSelector` по `kubernetes.io/metadata.name` и secret `ghcr-pull` в новом namespace (bootstrap создаёт их только в четырёх известных namespace'ах, см. `infra/argocd/README.md` Day-2). Без секрета образ плагина не скачается.
10. Обновить `destination.namespace` в `plugin-platform.yaml` (наш файл) и/или явно проставить `metadata.namespace` у ресурсов — после согласования с родителем.

---

## 4. Риски, требующие решения родителя

1. **`ingress-plugins.yaml` ещё не существует.** Задача №14 (`infra/k3s/overlays/dev/plugins/**`) не создала каталог на момент написания; тест `test_plugin_ingress_priority.py` читает `infra/k3s/overlays/dev/plugins/ingress-plugins.yaml` и до создания файла будет красным. Тест написан по контракту из §7.1 дизайна v4, а **не запускался** (verification freeze).
2. **Родитель ведёт `kustomization.yaml`.** Дизайн §7.1 требует добавить `ingress-plugins.yaml` в `overlays/dev/kustomization.yaml`; нам редактировать его запрещено. Также нужно решить, живёт ли `plugins-gateway` внутри `overlays/dev` (тогда он синкается приложением `agentscale-dev`, а `plugin-platform.yaml` дублирует Ingress) или только внутри `overlays/dev/plugins` (тогда только `plugin-platform.yaml`). **Двойное владение одним Ingress недопустимо** — два Application на один объект дадут «пилу» selfHeal'а.
3. **`app: plugin` как селектор.** В `ignoreDifferences` запись намеренно НЕ сужена по `name` (имена Deployment'ов динамические); фактически `/spec/replicas` игнорируется у всех Deployment приложения, включа always-running Plugin Gateway. Это безопасно, но если родитель захочет сузить — нужна стабильная метка/имя и `managedFieldsManagers` всё равно не подойдёт (см. комментарий в манифесте).
4. **Новая запись в `root-app.yaml` dev-контура** появится автоматически после коммита. Убедиться, что bootstrap `wait agentscale-dev` не считает `plugin-platform` обязательным до создания каталога `overlays/dev/plugins` (иначе холодный старт встанет на несуществующем source.path).
5. **Prod не задет:** `root-app-prod.yaml` include не содержит `plugin-*.yaml`, `overlays/prod/**` и `agentscale-prod.yaml` не тронуты.
