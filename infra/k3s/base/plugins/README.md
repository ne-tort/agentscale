# k3s-манифесты plugin-платформы (задача 14, дизайн v4)

Короткая справка по каталогам `infra/k3s/base/plugins/**` и
`infra/k3s/overlays/dev/plugins/**`. Источник истины —
`docs/ARCHITECTURE/plugin-architecture-complete.md` (v4) и
`docs/migration/plugin-migration-plan.md` (§2, задача 14).

## Что подключает родитель в kustomization

Я (`kustomization.yaml`) **не редактирую ни в base, ни в overlays/dev** — это зона
родителя. Фактическая схема поставки (решение родителя от задачи 15):

| Файл | Владелец | Роль |
|---|---|---|
| `infra/argocd/apps/plugin-platform.yaml` | задача 15 / родитель | отдельное Argo Application, `spec.source.path: infra/k3s/overlays/dev/plugins`, `destination.namespace: agentscale-dev`, `sync-wave: "1"` в app-of-apps |
| `infra/k3s/base/plugins/kustomization.yaml` | задача 14 (этот каталог) | **новый** файл для подключения каталога как kustomize-ресурса. Включает 5 боевых манифестов. НЕ включает `networkpolicy-agent.yaml` (справочный) и `README.md` |
| `infra/k3s/overlays/dev/plugins/kustomization.yaml` | **родитель** | включает `- ../../../base/plugins` + `ingress-plugins.yaml`, задаёт `namespace: agentscale-dev`. Шаблон `plugin-deployment-template.yaml` НЕ подключается (у него `replicas: 0` и плейсхолдеры) |
| `infra/k3s/overlays/dev/kustomization.yaml` | **родитель** | плагиновые файлы НЕ подключает: двойного владения нет, `agentscale-dev` их не видит |
| `infra/k3s/base/kustomization.yaml` | **родитель** | не изменяется |

Base-манифесты намеренно **не задают `metadata.namespace`** (паттерн
`infra/k3s/base/guards/app-budget.yaml`): namespace подставляет директива
`namespace: agentscale-dev` из `overlays/dev/plugins/kustomization.yaml`,
поэтому один файл работает в dev и prod (при переносе меняется только оверлей).
Ingress и шаблон Deployment в `overlays/dev/plugins/` задают
`namespace: agentscale-dev` явно (dev-специфичный слой, политика сред).

`Argo Application plugin-platform` НЕ добавляет плагиновые пути в
`agentscale-dev`; `plugins-gateway` — отдельный Ingress в отдельном приложении.

## Файлы

### `infra/k3s/base/plugins/`

| Файл | Kind'ы | Что даёт |
|---|---|---|
| `tenant-postgres.yaml` | Secret, PVC, ConfigMap, Deployment, Service, Job | Отдельный PostgreSQL 16.15 (`replicas: 1`) для `tenant_<company_id>`, **НЕ `prodavan`**. `pgaudit` в `shared_preload_libraries`, `max_connections=100`, `scram-sha-256`, явный `pg_hba.conf`, явные resources, startup/liveness/readiness probes, preStop, отдельный Service `tenant-postgres`. Bootstrap-Job (Sync-хук, wave 1) делает `REVOKE CONNECT, TEMPORARY ... FROM PUBLIC` (включая `template1`) на tenant- и платформенном инстансах, ПЕРЕД этим выдавая CONNECT админской роли `plugin_platform_admin` |
| `pgbouncer.yaml` | Secret, ConfigMap, Deployment, Service | **ОДИН** пул на tenant-кластер (`pool_mode = transaction`, `max_client_conn`, `default_pool_size`), `auth_file = userlist.txt` из Secret, `auth_type = scram-sha-256`. НЕ N пулов |
| `plugin-gateway.yaml` | ServiceAccount, Role, RoleBinding, ConfigMap, Deployment, Service | Отдельный trust-контур: всегда running (2 реплики), `:8080`, `/capabilities/v1/*`. RBAC — **ТОЛЬКО** `deployments/scale` (get/patch/update) + read-only `deployments` для hold-until-Ready; без `pods`/`secrets`/`deployments` на запись |
| `networkpolicy-plugin.yaml` | NetworkPolicy | `plugin-egress`: `podSelector {app: plugin}`, ingress от `app: plugin-gateway` :8080; egress — gateway :8080, egress-proxy :8080, kube-dns UDP/TCP 53, tenant-postgres :5432, pgbouncer :6432, MinIO :9000, Kafka :9092. **`app: agentscale-postgres` (prodavan) НЕ включён** |
| `networkpolicy-agent.yaml` | NetworkPolicy | **СПРАВОЧНЫЙ** эталон для генератора задачи 08, НЕ применяется к плагинам: DNS + 80/443 + :8001, **без** 5432/6432/9000/9092/8080. Боевая агентская NP — `base/agentscale-sandboxes-ks/networkpolicy.yaml`, не меняется |
| `egress-proxy.yaml` | ConfigMap, Deployment, Service | Envoy. Стартовый конфиг **fail-closed**: `clusters: []`, единственный маршрут → `direct_response: 403`. **Без** статического `webhooks.example.com`; кластеры генерирует задача 08 из `CapabilityGrant` в БД |

### `infra/k3s/overlays/dev/plugins/`

| Файл | Kind'ы | Что даёт |
|---|---|---|
| `ingress-plugins.yaml` | Ingress | **ОТДЕЛЬНЫЙ** `plugins-gateway`: `ingressClassName: traefik`, `router.entrypoints: websecure`, `router.tls: "true"`, `router.priority: "120"`, `tls.hosts: [agentscale.dev, localhost]`, `/webhook` и `/pluginpage` → `plugin-gateway:8080`, `sync-wave: "25"`. Приоритет — **аннотация на Ingress, не на path** |
| `plugin-deployment-template.yaml` | ServiceAccount, Deployment, Service | Шаблон: `replicas: 0`, `image` **по digest** (НЕ `:latest`), `imagePullPolicy: IfNotPresent`, readinessProbe `/health`, явные resources, env `GATEWAY_URL`/`PLUGIN_ID`/`TENANT_DB_DSN` из Secret, `automountServiceAccountToken: false`, лейблы `app: plugin`, `plugin_id`, `plugin_instance_id`. Реальные манифесты генерируются контроллером при grant |

## Разрешённые kinds

Все манифесты используют **только** kinds из жёсткого whitelist
`infra/argocd/apps/00-project.yaml:37-88`:

`ConfigMap`, `Secret`, `Service`, `ServiceAccount`, `PersistentVolumeClaim`,
`Deployment`, `Job`, `Ingress`, `NetworkPolicy`, `Role`, `RoleBinding`
(и `IngressRoute` из `traefik.io/v1alpha1`, если бы понадобился).

**НИКАКИХ CRD**: KEDA (`keda.sh/*`, `http.keda.sh/*`), Knative
(`serving.knative.dev/*`), cert-manager (`cert-manager.io/*`),
SPIRE, Temporal, `autoscaling/HPA` — ни в этих файлах, ни в
`plugin-platform.yaml`.

## Почему нет KEDA / Knative / cert-manager

| | Причина |
|---|---|
| Whitelist | `00-project.yaml` разрешает 27 kinds; ни одного из перечисленных control plane там нет. Добавление потребовало бы правки AppProject и отдельного Argo App |
| Бюджет | `infra/k3s/base/guards/app-budget.yaml`: **45 подов / 12Gi requests / 26Gi limits, 3 OOM-инцидента**. KEDA HTTP Add-on — +4–6 подов control plane, Knative — +8–12 подов и второй ingress-стек. На single-node k3s это несоразмерно |
| TLS | В кластере self-signed default TLSStore Traefik; cert-manager отсутствует. `tls.hosts: [agentscale.dev, localhost]` использует дефолтный сертификат. Переход на wildcard-subdomain + cert-manager DNS-01 — отдельная цель §5.3 при появлении публичного домена |
| Lifecycle | Вместо `SandboxClaim`/warm pool (агенты) — обычный Deployment `replicas: 0` + Plugin Gateway activator: патч `deployments/scale`, удержание до Ready, idle TTL. **Никаких новых CRD** (§7.4, ADR-008) |

## Бюджет подов (важно!)

Always-running плагиновая платформа добавляет **5 подов** к квоте 45:

| Компонент | Реплики | requests |
|---|---|---|
| `plugin-gateway` | 2 (решение родителя) | 200m / 512Mi |
| `tenant-postgres` | 1 | 100m / 256Mi |
| `pgbouncer` | 1 | 50m / 64Mi |
| `egress-proxy` | 1 | 50m / 64Mi |
| **Итого** | **5** | **400m / ~896Mi** |

Плюс кратковременный bootstrap-Job и проснувшиеся поды плагинов (лимит
одновременных wake-up = 2). Если квота 45 подов / 12Gi requests упрётся —
`plugin-gateway.replicas` можно опустить до **1** (Gateway останется always
running, но без запаса на rolling update). Уменьшать `tenant-postgres`/`pgbouncer`
нельзя: по одному инстансу по дизайну (§2.1).

## Почему `ignoreDifferences` обязателен

`plugin-platform.yaml` включает `automated.selfHeal: true`. Deployment плагина
декларируется в Git с `replicas: 0`, а activator патчит live `deployments/scale`
на 1 и обратно. Без `ignoreDifferences` Argo видит дрейф и **немедленно**
возвращает `replicas: 0` — 0→1 никогда не успевает отработать, плагин
«умирает» посреди запроса (acceptance №13, риск в §10).

```yaml
ignoreDifferences:
  - group: apps
    kind: Deployment
    jsonPointers: [/spec/replicas]
    jqPathExpressions: [.spec.replicas]
syncOptions:
  - RespectIgnoreDifferences=true
```

Запись **не сужена по `name`**: имена Deployment'ов плагинов создаются
динамически при grant. Побочный эффект — `/spec/replicas` игнорируется и у
самого Gateway; это безопасно (его реплики меняет только Git), а сужение по
имени сломало бы wake-up у новых плагинов.

Кроме `/spec/replicas`, родитель добавляет `ignoreDifferences` на **`/data`**
трёх объектов, которые контроллеры правят вне Git (иначе selfHeal сотрёт
выданные grant'ы):

| Объект | Кто правит | Зачем |
|---|---|---|
| ConfigMap `tenant-postgres-config` | задача 05 | ключ `tenant_hba.conf` — пары `(role, tenant_<company>)` + `pg_reload_conf()` |
| Secret `pgbouncer-credentials` | задача 05 | ключ `userlist.txt` — SCRAM-секреты ролей + `RELOAD;` на консоли PgBouncer |
| ConfigMap `egress-proxy-config` | задача 08 | Envoy-кластеры/маршруты из `CapabilityGrant` |

## Известные риски / зависимости

1. **Образы ещё не собраны** (отдельная задача родителя):
   `ghcr.io/ne-tort/postgres:16.15` **не содержит pgaudit**, а
   `shared_preload_libraries=pgaudit` задан явно → без достроенного образа
   tenant-postgres упадёт в FATAL на старте. Это **намеренный fail-closed**
   (никакого «тихого» отключения аудита DDL). Также нужны
   `ghcr.io/ne-tort/pgbouncer:1.24.1` (с SCRAM) и
   `ghcr.io/ne-tort/envoy:v1.31.2`.
2. **`plugin-gateway-secrets` и `plugin-instance-secrets` не описаны в Git**:
   первый — обязательный Secret (без него pod не стартует, это fail-closed),
   второй создаётся платформой при grant и ротируется при revoke (§5.5).
3. **pg_hba `map=` несовместим с `scram-sha-256`**: пары `(role, tenant_<co>)`
   задаются полями `DATABASE`/`USER` в include-файле `tenant_hba.conf`;
   `pg_ident.conf` — пустая заглушка. Граница = `REVOKE ... FROM PUBLIC`
   (включая `template1`) + pg_hba + `NOLOGIN` при revoke, а **не** только
   `GRANT CONNECT` (acldefault у PUBLIC).
4. **Лейблы NP берутся из as-built**: MinIO
   `app.kubernetes.io/name: agentscale-minio`
   (`base/platform/minio.yaml`), Kafka
   `app.kubernetes.io/name: agentscale-kafka` (`base/platform/kafka.yaml`),
   а не `app: minio` / `app: kafka` из §6.2 дизайна. Задача 08 должна
   генерировать NP по реальным лейблам, иначе правила не совпадут с подами.
5. `networkpolicy-agent.yaml` — **справочный**, не подключать в боевой рендер
   как третью политику (`agentscale.io/manifest-role: reference-only`).
6. Dev-контур: `namespaceSelector` везде `kubernetes.io/metadata.name:
   agentscale-dev`. При переносе в prod заменить на `agentscale` /
   `agentscale-sandboxes` (оба есть в destinations `00-project.yaml`).

---

Проверки (`kubectl`, `kustomize`, тесты, линт, `git`) **намеренно не
запускались** — verification freeze по `docs/migration/plugin-migration-plan.md`
§0.2 и `AGENTS.md`. Манифесты только записаны. `kustomization.yaml` не
редактировался.
