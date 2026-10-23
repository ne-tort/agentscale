# Ранбук DevOps (Prodavan) — GitOps

Канон поставки: **git → Argo CD → kustomize → k3s**.  
Императив только `poetry run prodavan-ops {validate|wait|rollout|smoke}`.  
**Запрещены:** `.sh` под `infra/`, docker-compose как кластер, k3d в git, recover/deploy shell.

> ## Политика сред: работаем только с dev (2026-10-08)
>
> **Вся текущая работа — ТОЛЬКО dev-контур** (Argo `agentscale-dev` ← ветка `main`, ns `agentscale-dev`).
> **Prod трогаем исключительно по явному требованию** пользователя: никаких промоций, sync/refresh prod-приложения, правок `infra/k3s/overlays/prod/**` или prod-ресурсов (ns `agentscale`) «заодно» с обычной задачей.
>
> | | Dev (по умолчанию) | Prod (только по явному требованию) |
> |---|---|---|
> | Argo app | `agentscale-dev` ← `main` | `agentscale-prod` ← `prod` |
> | Namespace | `agentscale-dev` | `agentscale` |
> | Поставка | PR → `main` → auto-merge → CI Images → Argo sync | PR `main`→`prod` + пин образов |
> | UI | http://172.31.156.203:8088 | http://172.31.156.203:30090 |
>
> Обычный PR завершается на **Verify Dev** (green) — дальше не идём. Промоция в prod — отдельный заход по прямой просьбе.

Репозиторий: [ne-tort/agentscale](https://github.com/ne-tort/agentscale) (подмодуль `prodavan/`).  
**UI:** http://127.0.0.1:8088/ (Traefik слушает `0.0.0.0:8088`, Ingress без фильтра `host`).  
После bootstrap — единая форма логин/пароль (Keycloak ROPC). Platform Admin: `admin` / `admin`. Issuer: `http://127.0.0.1:8089/realms/prodavan` (hostPort).

---

## 0. Топология

| Компонент | Где |
|-----------|-----|
| Кластер | **k3s** на SSH-хосте. Workloads только из git. |
| Argo CD | `infra/argocd/install` → `root-app.yaml` → `apps/` → `infra/k3s/overlays/dev` |
| Sealed Secrets | `infra/argocd/sealed-secrets` + `overlays/dev/SECRETS.md` |
| Ops CLI | [`infra/ops`](../../infra/ops): `validate` / `wait` / `rollout` / `smoke` |

Образы: `ghcr.io/ne-tort/prodavan-{api,web}:latest` (основной репо, CI Images), `ghcr.io/ne-tort/prodavan-agent-runtime:latest` (агент-runtime — собирает **только** openclaw-images в подмодуле prodavan-claw; дубль-job в CI Images удалён 2026-09-24; см. [agent-runtime-delivery.md](agent-runtime-delivery.md)). `imagePullPolicy: Always`, secret `ghcr-pull`. Probe pod в `prodavan-sandboxes` (см. §1.1).

---

## 1. Git: только PR

```text
ветка → PR → CI Gate → Auto-merge squash
  → CI Images (GHCR)
  → Verify Dev (rollout + wait + smoke)
```

Это **dev-поток** (Argo `agentscale-dev` ← `main`). На нём обычная задача и **завершается**. Промоция в prod (merge `main`→`prod`, PR в ветку `prod`, пин `newTag` в `overlays/prod/kustomization.yaml`) — **только по явному требованию** пользователя (см. «Политика сред» вверху).

Не `git push origin main`.

**Миграции Alembic** — только через initContainer при деплое API; см. [alembic.md](alembic.md). Ручной `alembic upgrade` / `kubectl exec` на shared env запрещён.

### 1.1 Agent-runtime (probe pod) — отдельный поток

Образ `prodavan-agent-runtime` собирается **только** в **подмодуле `prodavan-claw`** (workflow `openclaw-images.yml`, после auto-merge PR в claw): buildx кэш `type=registry` (общий для dd-claw-раннеров), smoke (артефакты + `/health`) гейтит trigger-verify. Дубль-job `build-agent-runtime` в CI Images основного репо удалён (2026-09-24, дыра №1 закрыта) — карта дыр и статус фиксов в [agent-runtime-delivery.md](agent-runtime-delivery.md).

```text
prodavan-claw PR → openclaw-ci → auto-merge
  → openclaw-images (build prodavan-agent-runtime:latest → GHCR)
  → trigger-verify (dispatch Verify Dev in ne-tort/prodavan)
    → Verify Dev: prodavan-ops rollout
      → restart prodavan-probe-pod (prodavan-sandboxes, imagePullPolicy: Always)
        → kubelet re-pulls :latest digest
      → wait Argo → smoke
```

**Probe pod** (`prodavan-probe-pod` в namespace `prodavan-sandboxes`) — это long-lived agent-runtime pod для проверки AI-ключей. Включён в `DEPLOYMENT_TARGETS` `prodavan-ops` (вместе с api/web/celery-worker/celery-beat), поэтому `prodavan-ops rollout` перезапускает и его. `imagePullPolicy: Always` → при restart тянется свежий digest.

**Project sandbox pods** (динамические, per-project, создаются `pod_service` через `POD_AGENT_RUNTIME_IMAGE` env) — при создании нового sandbox pod подхватит свежий digest (Always + новый pod = новый pull). Уже запущенные sandbox pods обновляются при reload/sync проекта (recreate).

**Для cross-repo trigger** нужен секрет `PRODAVAN_REPO_TOKEN` в `prodavan-claw` репо (PAT с `actions:write` на `ne-tort/prodavan`). Без него trigger-verify выводит warning (образ запушен, но кластер подхватит при следующем Verify Dev из основного репо — теперь probe-pod в `DEPLOYMENT_TARGETS`, так что он тоже перезапустится).

---

## 2. Bootstrap кластера (канон)

**Только два шага: SSH-хост + Terraform.**

### 2.1 Dev-кластер (общий хост, dev+prod контуры)

```bash
cd infra/terraform/environments/vm
export TF_VAR_ghcr_token="$(gh auth token)"
terraform init
terraform apply -auto-approve
# UI:
#   https://172.31.156.203/  (или http://172.31.156.203:8088/)
```

Подробности хоста/ключа: [`infra/terraform/environments/vm/README.md`](../../infra/terraform/environments/vm/README.md).

### 2.2 Prod-кластер (выделенный сервер, только prod)

```bash
cd infra/terraform/environments/prod
export TF_VAR_ghcr_token="$(gh auth token)"
terraform init
terraform apply -auto-approve
# UI: https://185.103.100.124/  (или http://185.103.100.124:30090/)
```

Отличия: ssh `vzlet-ai@185.103.100.124:44345` (ключ `~/.ssh/myserver_key`),
`gitops_apps = "prod"` → root-app-prod синкает **только** projects + agent-sandbox +
agentscale-prod (dev/e2e на этот хост не ставятся), Traefik на 80/443, CI-раннеров нет.
Подробности: [`infra/terraform/environments/prod/README.md`](../../infra/terraform/environments/prod/README.md).

Day-2: merge в `main` + Argo. Не `kubectl apply -k infra/k3s/...` руками.

### 2.3 Устойчивость старта кластера

После ребута ноды сервисы поднимаются неравномерно (postgres/opensearch/keycloak —
минуты). Чтобы медленный старт не превращался в CrashLoop:

- **api initContainer `migrate.sh`** ждёт БД до 5 мин (alembic current в цикле) —
  под висит в `Init:0/N` вместо падения по коннекту.
- **`startupProbe`** на keycloak/postgres/minio/mongodb/redis/searchnet/gotenberg:
  liveness/readiness не срабатывают, пока старт не завершился (бюджет 5–10 мин).
- **boot-heal** (`/usr/local/lib/prodavan/post-k3s-heal.sh`, ExecStartPost k3s):
  чистит Terminating/Unknown/Failed поды, перезапускает Traefik, ждёт smoke.

---

## 3. Reboot / хост

1. k3s → systemd.  
2. Argo selfHeal.  
3. PVC local-path на диске узла.  
4. ImagePullBackOff → `ghcr-pull`, не image import.

Не ставить **Docker Engine** в тот же OS, где k3s (маскировать docker).  
Не оставлять незалогиненный Tailscale рядом с CNI.

### 3.1 DNS кластера после ребута (инцидент 2026-10-22)

**Симптом.** Агент из пода не может достучаться до провайдера ИИ. При этом
всё остальное зелёное: поды `Running`, HTTP-пробы OK, TCP-egress работает
(по IP провайдер отвечает). Выглядит как «сломался агент», а ломается DNS.

**Что именно происходит.** CoreDNS форвардит на upstream из своего
`/etc/resolv.conf`, который kubelet копирует с **хоста** (`dnsPolicy: Default`).
Если после ребута хост поднялся с нерабочим nameserver (например, шлюз, на
котором DNS-сервис не запустился), CoreDNS шлёт запросы в мёртвый адрес, и
**ни одно внешнее имя из подов не резолвится**. Диагностика усложняется тем,
что:

- хост при этом резолвит нормально — у него свой резолвер;
- `dig @10.43.0.10` **с хоста** даёт ложный таймаут (ClusterIP недоступен
  с хоста по определению) — проверять надо **из пода**;
- в логах CoreDNS виден поток `read udp ...->X:53: i/o timeout`.

**Диагностика.**

```bash
# резолв ИЗ ПОДА (не с хоста!)
kubectl -n agentscale exec <pod> -- python3 -c \
  "import socket;print(socket.gethostbyname('github.com'))"

# поток таймаутов CoreDNS = мёртвый upstream
kubectl -n kube-system logs -l k8s-app=kube-dns --since=120s | grep -c 'i/o timeout'

# жив ли upstream, который видит хост
dig +short @<host-nameserver> github.com
```

Или одной командой (падает с готовым диагнозом и инструкцией):

```bash
prodavan-ops dns-check --namespace agentscale
```

**Фикс.** Не полагаться на единственный upstream из `/etc/resolv.conf` —
задать CoreDNS явный список живых резолверов. Патчим ConfigMap `coredns`
(k3s владеет им, `coredns-custom`/HelmChartConfig для этого не годятся:
`*.override` с `forward` CoreDNS отклоняет как duplicate plugin, а HelmChart
coredns в k3s нет):

```bash
kubectl -n kube-system get cm coredns -o yaml > /tmp/coredns-backup.yaml   # бэкап!
# заменить:  forward . /etc/resolv.conf
# на:        forward . <живой> <резерв> <резерв> { policy sequential  max_fails 1  health_check 5s }
kubectl -n kube-system rollout restart deploy/coredns
```

Три upstream + `max_fails 1` + `health_check 5s` дают устойчивость: падение
одного резолвера больше не выключает DNS всему кластеру. Проверено на prod
2026-10-22: до фикса — непрерывный поток таймаутов и 0 резолвов из подов,
после — 0 таймаутов за минуту и провайдер отвечает `HTTP/1.1 200 OK`.

**Профилактика.** `prodavan-ops heal` (post-reboot recovery) теперь вызывает
`dns-check` и печатает диагноз с инструкцией, а `prodavan-ops dns-check`
можно ставить в CI/дежурный прогон. Проверка намеренно резолвит **из пода**:
если DNS мёртв, новый под не скачает образ, и проба «не стартует» вместо
«DNS мёртв» — диагностика теряется.

### 3.2 Сеть подов наружу: PMTU и «медленный GitHub» (2026-10-23)

**Симптом.** Argo не может получить target state: `git fetch` падает через
~10 минут с `error: RPC failed; curl 92 HTTP/2 stream 5 was not closed
cleanly: CANCEL (err 8)` или `fatal: early EOF`. Ни один PR не доезжает до
кластера. Параллельно: `pip install` в CI ловит `ReadTimeout` от PyPI, а
браузерные бинарники для образа searchnet качаются 40 минут и не докачиваются.

**Замеры (это важно — симптомы выглядят как «нестабильный интернет»):**

| откуда → куда | скорость |
|---|---|
| под → нода (CNI) | **155 МБ/с** — сеть пода здорова |
| под → GitHub | **~20 КБ/с**, обрывы `IncompleteRead` |
| нода → GitHub | **2.2 МБ/с** |

**PMTU до интернета меньше 1500:**

```bash
ping -M do -s 1472 140.82.121.4   # → Frag needed
ping -M do -s 1400 140.82.121.4   # → OK
```

MTU пода — 1450 (VXLAN 1500−50), и пакеты такого размера в этот путь не
проходят: TCP уходит в ретрансмиты и деградирует в 100 раз. Хост этого не
замечает, потому что его собственные сокеты делают PMTU-обнаружение.

**Что сделано.** `argocd-repo-server` переведён на `hostNetwork: true` — он
ходит тем же путём, что нода, и `git fetch` занимает ~1с вместо 10 минут
(манифест: `infra/argocd/install/patch-repo-server-timeout.yaml`). Порт 8081
при этом занимается на ноде — для однонодового кластера приемлемо.

Дополнительно: `http.version=HTTP/1.1` и `http.postBuffer=512MB` через
`GIT_CONFIG_*` (HTTP/2 на этом канале рвётся чаще), `ARGOCD_EXEC_TIMEOUT=900s`.

**Постоянное лечение (не сделано, требует рестарта k3s):** `flannel-mtu: 1400`
в `/etc/rancher/k3s/config.yaml` — тогда MSS пода = 1360 и клампинг не нужен.
Рестарт k3s останавливает весь dev-кластер, поэтому вынесено отдельной задачей.
MSS-клампинг на ноде (`iptables -t mangle FORWARD --set-mss 1360`) применён как
страховка, но сам по себе проблему не решил.

---

### 3.3 Память узла: защита от глобального OOM (инцидент 2026-10-23)

**Что случилось.** Прод-нода ушла в глобальный OOM: свободно 62 МБ,
`active_anon` 7.9 ГиБ. Сразу после этого apiserver деградировал — запросы к
`/apis/agents.x-k8s.io/v1beta1/sandboxes` висели **22 минуты**,
`ListPodSandbox` отдавал `DeadlineExceeded`, client-side throttling доходил до
5м50с. Снаружи это выглядело как «нода зависла по CPU», и настоящая причина
(нехватка памяти) была не видна.

**Замеры до включения защиты.**

| | значение |
|---|---|
| allocatable | 14.88 ГиБ |
| сумма requests | 7.4 ГиБ |
| сумма **limits** | **23.6 ГиБ** (перекоммит 158%) |
| подов Argo CD без requests/limits | **7 из 7** (BestEffort) |

BestEffort ядро убивает первыми — в журнале дважды убит
`argocd-application-controller`.

**Что теперь задано (декларативно).**

`infra/terraform/modules/k3s-dev-host/templates/k3s-config.yaml.tpl` →
`/etc/rancher/k3s/config.yaml`:

```yaml
kubelet-arg:
  - "system-reserved=memory=1024Mi,cpu=250m"
  - "kube-reserved=memory=512Mi,cpu=250m"
  - "eviction-hard=memory.available<1200Mi,nodefs.available<10%,imagefs.available<15%"
  - "eviction-soft=memory.available<1800Mi"
  - "eviction-soft-grace-period=memory.available=90s"
  - "eviction-minimum-reclaim=memory.available=256Mi"
```

Эффект: allocatable **14.9 → 12.2 ГиБ** (резерв системы вычитается из
продаваемого), выселение начинается на 1.2 ГиБ свободных, а не на 62 МБ.

Бюджеты namespace'ов:

| объект | где | что даёт |
|---|---|---|
| `LimitRange` argocd / kube-system / agent-sandbox-system | `infra/k3s/node-manifests/platform-guards.yaml` (k3s применяет сам) | непустые requests и потолок на контейнер вместо BestEffort |
| `ResourceQuota` argocd | там же | 2Gi requests / 8Gi limits / 20 подов |
| `LimitRange` + `ResourceQuota` app-ns | `infra/k3s/base/guards/app-budget.yaml` (через Argo) | max 4Gi на контейнер (столько объявляет searchnet), 12Gi/26Gi на namespace |

`namespace` в app-guard не прописан — подставляет NamespaceTransformer
оверлея, поэтому один файл работает и в dev, и в prod.

**Проверка.**

```bash
sudo k3s kubectl get node pc-for-ai -o jsonpath='{.status.allocatable.memory}'
# 12806844Ki  (было 15608508Ki)

sudo k3s kubectl get --raw /api/v1/nodes/pc-for-ai/proxy/configz \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["kubeletconfig"]; print(d["evictionHard"], d["systemReserved"])'

sudo k3s kubectl get limitrange,resourcequota -n argocd
```

**Важно при рестарте k3s.** `config.yaml` читается только при старте — правка
файла без `systemctl restart k3s` не действует. Поды при рестарте не
перезапускаются (containerd держит контейнеры), но CoreDNS **сбрасывается** —
см. §3.1.

**Что осталось за рамками.** Нода 14.9 ГиБ при 33 подах (OpenSearch 1.5 ГиБ +
Keycloak 1.5 + Kafka 2 + searchnet 4 + MongoDB) — запас есть, но не двойной.
Если появится ещё один тяжёлый сервис, расширять RAM, а не поднимать лимиты.

---

## 4. CI

| Workflow | Роль |
|----------|------|
| CI Gate | `prodavan-ops validate` (+ pytest ops) + api/flutter/schemas |
| CI Images | buildx → GHCR |
| Verify Dev | `rollout` + `wait` + `smoke` |
| Auto-merge | squash после Gate |

Self-hosted runners и kubeconfig для Verify — только [`infra/github-runner/`](../../infra/github-runner/README.md) (не bootstrap UI).

---

## 5. MinIO (object store)

| Компонент | Значение |
|-----------|----------|
| Bucket | `prodavan` (private, versioning off) |
| Init Job | `prodavan-minio-init` — **Sync hook, wave 9** (до `prodavan-api` wave 10); bucket + IAM user `prodavan-api` |
| API creds | `S3_ACCESS_KEY=prodavan-api` в `prodavan-api-secrets` |
| Root creds | `prodavan-minio` Secret — только init / break-glass |
| PVC | `prodavan-minio-data` (10Gi, RWO) — данные переживают pod reschedule |

**Ротация API creds (dev/staging):**

1. Сгенерировать новый пароль; обновить `prodavan-minio` Secret (`api-password`) и `prodavan-api-secrets` (`S3_SECRET_KEY`) в git (SealedSecret на shared).
2. Argo sync → init Job пересоздаёт/обновляет user policy (idempotent).
3. Rollout `prodavan-api`; smoke `/health/ready`.

**Verify PVC retain:** `prodavan-ops validate` проверяет наличие PVC `prodavan-minio-data` в overlay render.

API **не** вызывает `create_bucket` на startup — только `head_bucket` health.

---

## 5b. MongoDB (Document Store)

| Компонент | Значение |
|-----------|----------|
| Service | `prodavan-mongodb:27017` (ns `prodavan`) |
| Init Job | `prodavan-mongodb-init` — Sync hook wave 9; app user `prodavan` + db `prodavan` |
| API creds | `MONGODB_URL` в `prodavan-api-secrets` (app user, `authSource=admin`) |
| Root creds | `prodavan-mongodb` Secret — только init / break-glass |
| PVC | `prodavan-mongodb-data` (5Gi, RWO) |
| Sandbox | **нет** egress на 27017 — только API process |

Document Store BC: in-proc Port + admin HTTP; Kafka bus `document` → `prodavan.document.events`.

---

## 5c. OpenSearch (Search Index)

| Компонент | Значение |
|-----------|----------|
| Service | `prodavan-opensearch:9200` (ns `prodavan`) |
| Init Job | `prodavan-opensearch-init` — Sync hook wave 9; smoke `/_cluster/health` |
| API | `OPENSEARCH_URL`, `OPENSEARCH_ENABLED`, `OPENSEARCH_REQUIRED` in `prodavan-config` |
| Security | MVP: `DISABLE_SECURITY_PLUGIN=true`; ACL in Search Index BC |
| PVC | `prodavan-opensearch-data` (10Gi, RWO) |
| Sandbox | **нет** egress на 9200 — только API process |

Search Index BC: in-proc Port + admin HTTP `/admin/search-index`; Kafka bus `search` → `prodavan.search.events`.

---

## 6. Keycloak (identity)

| Компонент | Значение |
|-----------|----------|
| Public issuer | `http://127.0.0.1:8089/realms/prodavan` (Keycloak hostPort 8089) |
| In-cluster Admin | `http://prodavan-keycloak:8080` |
| Init Job | `prodavan-keycloak-init` — Sync hook wave 8 (до API); SA roles + user `admin`/`admin` |
| Platform Admin | username `admin`, password `admin`, realm role `platform.admin` |
| API | `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`, JWKS via in-cluster URL |

Flutter login: ROPC (`grant_type=password`) на client `prodavan-flutter`. One-click test personas удалены.

