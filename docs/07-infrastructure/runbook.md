# Ранбук DevOps (Prodavan) — GitOps

Канон поставки: **git → Argo CD → kustomize → k3s**.  
Императив только `poetry run prodavan-ops {validate|wait|rollout|smoke|dns-check}`.  
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
**Кластер и Terraform живут на VM `www@172.31.156.203`**; Windows-хост — только клиент
(SSH/scp/браузер), WSL/portproxy/keepalive не нужны.  
**UI:** http://172.31.156.203:8088/ (Traefik `web`, dev — wildcard Ingress) и
https://172.31.156.203/ (`websecure`, сертификат локального CA — импорт
`agentscale-dev-ca.crt` в браузер, см. [VM README](../../infra/terraform/environments/vm/README.md)).  
После bootstrap — единая форма логин/пароль (Keycloak ROPC). Platform Admin: `admin` / `admin`.
Issuer (dev overlay): `http://127.0.0.1:8089/realms/agentscale` — закреплён за 127.0.0.1
специально; Keycloak hostPort 8089, с Windows доступен как `http://172.31.156.203:8089/`.

---

## 0. Топология

| Компонент | Где |
|-----------|-----|
| Кластер | **k3s** на VM `www@172.31.156.203` (Terraform выполняется на VM, SSH на localhost). Workloads только из git. |
| Argo CD | `infra/argocd/install` → `root-app.yaml` → `apps/` → `infra/k3s/overlays/dev` |
| Sealed Secrets | `infra/argocd/sealed-secrets` + `overlays/dev/SECRETS.md` |
| Ops CLI | [`infra/ops`](../../infra/ops): `validate` / `wait` / `rollout` / `smoke` / `dns-check` |

Образы: `ghcr.io/ne-tort/agentscale-{api,web}:latest` (основной репо, CI Images), `ghcr.io/ne-tort/prodavan-agent-runtime` (агент-runtime — собирает **только** openclaw-images в подмодуле prodavan-claw, dev-оверлей пинит SHA; дубль-job в CI Images удалён 2026-09-24; см. [agent-runtime-delivery.md](agent-runtime-delivery.md)). `imagePullPolicy: Always`, secret `ghcr-pull`. Probe pod `agentscale-probe-pod` в `agentscale-dev-sandboxes` (см. §1.1).

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
      → restart agentscale-probe-pod (agentscale-dev-sandboxes, imagePullPolicy: Always)
        → kubelet re-pulls свежий digest
      → wait Argo → smoke
```

**Probe pod** (`agentscale-probe-pod` в namespace `agentscale-dev-sandboxes`) — это long-lived agent-runtime pod для проверки AI-ключей. Включён в `DEPLOYMENT_TARGETS` `prodavan-ops` (вместе с `agentscale-api`/`agentscale-web`/`agentscale-celery-worker`/`agentscale-celery-beat`/`agentscale-celery-indexer`/`searchnet` в `agentscale-dev`), поэтому `prodavan-ops rollout` перезапускает и его. `imagePullPolicy: Always` → при restart тянется свежий digest.

**Project sandbox pods** (динамические, per-project, создаются `pod_service` через `POD_AGENT_RUNTIME_IMAGE` env) — при создании нового sandbox pod подхватит свежий digest (Always + новый pod = новый pull). Уже запущенные sandbox pods обновляются при reload/sync проекта (recreate).

**Для cross-repo trigger** нужен секрет `PRODAVAN_REPO_TOKEN` в `prodavan-claw` репо (PAT с `actions:write` на `ne-tort/prodavan`). Без него trigger-verify выводит warning (образ запушен, но кластер подхватит при следующем Verify Dev из основного репо — теперь probe-pod в `DEPLOYMENT_TARGETS`, так что он тоже перезапустится).

---

## 2. Bootstrap кластера (канон)

**Только два шага: SSH-хост + Terraform.** Dev: Terraform выполняется **на VM**
(SSH на localhost, state на VM). Prod: apply идёт **с Windows-хоста** по SSH на сервер.

### 2.1 Dev-кластер (VM `www@172.31.156.203`, dev+prod контуры на общем хосте)

Terraform, checkout `~/git/agentscale` и `terraform.tfstate` живут на VM; с Windows — только SSH:

```bash
# Токен берётся на Windows (gh залогинен там; на VM gh НЕ залогинен):
#   pwsh> gh auth token                            # read:packages
ssh www@172.31.156.203
# НЕ «git pull --ff-only» вслепую: checkout на VM обычно висит на уже
# удалённой рабочей ветке, и pull падает «no such remote ref». Сначала main.
cd ~/git/agentscale && git fetch origin && git checkout main && git pull --ff-only
export TF_VAR_ghcr_token='<токен с Windows>'       # ОБЯЗАТЕЛЕН; не коммитить, на диск не писать
terraform -chdir=infra/terraform/environments/vm init
terraform -chdir=infra/terraform/environments/vm apply -auto-approve
# UI:
#   https://172.31.156.203/  (или http://172.31.156.203:8088/)
```

Пустой `TF_VAR_ghcr_token` теперь падает **сразу** (в `gitops-bootstrap.sh`, с точной
командой для исправления): без токена не создаются секреты `ghcr-pull` / `repo-agentscale`,
Argo не может читать приватный репозиторий, а kubelet — тянуть образы. `gh` на VM
**не залогинен** — значение токена генерируется на аутентифицированной машине
(Windows: `gh auth token`) и передаётся в SSH-сессию.

Первый apply «с нуля» идёт **~25–40 мин** (тайминги — §2.2). Повторный apply идемпотентен;
работающий k3s не переустанавливается (day-2), но при изменении `/etc/rancher/k3s/config.yaml`
сервис перезапускается (kubelet-arg/resolv-conf читаются только на старте).

Два разных SSH-ключа, не перепутать: ключ на Windows — для входа **с Windows на VM**;
Terraform на VM ходит на localhost и использует `~/.ssh/prodavan_tf` (`ssh_private_key_path`).

Подробности хоста/ключа/CA: [`infra/terraform/environments/vm/README.md`](../../infra/terraform/environments/vm/README.md).

### 2.2 Полный цикл: снести → поставить с нуля на dev-VM

Контракт provisioner'ов — **`v14-clean-install`**. Все команды **на VM**
(`ssh www@172.31.156.203`), из `~/git/agentscale/infra/terraform/environments/vm`.

**Шаг 1. Снос.**

```bash
terraform state list          # ожидаем 4 ресурса: k3s_server, k3s_uninstall,
                              #   gitops_bootstrap, host_dns (модуль k3s-dev-host)
terraform destroy -auto-approve
```

`destroy` прогоняет destroy-provisioner'ы в обратном порядке
(`gitops_bootstrap → k3s_uninstall → host_dns → k3s_server`). После `k3s_uninstall`
на хосте **не остаётся ничего кластерного** — следующий apply ставит с нуля:

- `/var/lib/rancher/k3s` — etcd/sqlite, containerd-образы, **данные local-path PVC**
  и TLS CA (`agentscale-tls/` уходит вместе с data-dir; новый CA генерируется при установке);
- `/etc/rancher/k3s`, `/var/lib/kubelet`, `/var/lib/cni`, `/run/k3s`, `/run/flannel`,
  `/var/log/pods`, `/var/log/containers`;
- бинарники `/usr/local/bin/k3s*`, unit `k3s.service`(+env), boot-heal drop-in
  (`/etc/systemd/system/k3s.service.d`) и скрипты `/usr/local/lib/prodavan`;
- оба kubeconfig (`/home/www/.kube/prodavan-dev.yaml` и CI-раннерский
  `/home/runner/.kube/prodavan-dev.yaml`) + экспорт CA `~/agentscale-dev-ca.crt`;
- iptables-чистка: из сохранённых правил вырезаются `KUBE-`/`CNI-`/flannel-цепочки
  (k3s-killall снимает только свои; чужой DNAT от прошлого кластера — классический
  симптом «грязной» установки);
- managed DNS drop-in `/etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf`
  (destroy `host_dns`; legacy-файл `99-dot.conf` удаляется provisioner'ом при
  следующей установке — DNS-контракт v14, §3.1).

Destroy-provisioner `k3s_uninstall` **не** `on_failure = continue`: если снос не удался,
`destroy` падает с ошибкой, а не «успешно» оставляет живой k3s на хосте.

**Шаг 1b. Если state потерян, а кластер жив** — destroy нечего удалять; чистая установка
поверх живого кластера делается одним флагом:

```bash
terraform apply -auto-approve -var force_reinstall=true
terraform apply -auto-approve -var force_reinstall=false   # снять флаг
```

`force_reinstall` стирает предыдущее состояние **до** установки (k3s-uninstall + rm
data-dir) и входит в triggers `k3s_server`, поэтому пересоздаёт provisioner'ы. Обратное
переключение в `false` только перезапускает идемпотентные provisioner'ы — без переустановки.

**Шаг 2. Установка.**

```bash
cd ~/git/agentscale && git fetch origin && git checkout main && git pull --ff-only
cd infra/terraform/environments/vm
export TF_VAR_ghcr_token='<токен>'                 # ОБЯЗАТЕЛЕН: пустой — падение сразу.
                                                   # Токен — с аутентифицированной машины
                                                   # (Windows: gh auth token; на VM gh не залогинен)
terraform init
terraform apply -auto-approve
```

**Тайминги первого apply после сноса (суммарно ~25–40 мин):**

После установки на хосте действует DNS-контракт v14 (drop-in `systemd-resolved`,
`/etc/resolv.conf` → штатный stub-симлинк, `/etc/k3s-resolv.conf` с `9.9.9.9`/`8.8.8.8`
напрямую) — детали и проверка в §3.1.

| Этап | Бюджет ожидания |
|------|-----------------|
| Скачивание k3s (curl с ретраями) + установка | — |
| k3s API `/readyz` (холодная VM: свежие containerd-кэш и образы) | до **540 с (9 мин)**; на таймауте в вывод печатаются `systemctl status k3s` и `journalctl` |
| Node Ready (`kubectl wait`) | 300 с |
| Argo CD: `argocd-server`, `argocd-repo-server` (по очереди) | по **600 с** |
| `sealed-secrets-controller` | 300 с |
| Application `agent-sandbox` (CRD должны встать до платформы) | 8 мин (48×10 с; если не успела — WARN с диагностикой, не fail) |
| Application `agentscale-dev` Synced+Healthy (keycloak/postgres/opensearch/redpanda/mongo + pull всех образов) | **18 мин** (108×10 с); на провале — дамп Application/подов/событий и список частых причин |

**Шаг 3. Если apply упал посередине** — просто повторить `terraform apply -auto-approve`.
Все provisioner'ы идемпотентны, ни один шаг не требует «удалить state, чтобы повторить»;
если платформа уже поднялась, `gitops-bootstrap.sh` коротко выходит на
`gitops-already-healthy`. Частые причины провала: пустой/протухший `TF_VAR_ghcr_token`
(ImagePullBackOff), память (CrashLoop keycloak/postgres), недоступный репозиторий
(Argo OutOfSync).

**Шаг 4. Верификация (на VM):**

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd ~/git/agentscale/infra/ops && poetry install    # или python3 -m venv + pip install -e .
poetry run prodavan-ops validate    # контракт репо + kustomize render + пины образов (кластер не нужен)
poetry run prodavan-ops wait        # Argo agentscale-dev Synced+Healthy
poetry run prodavan-ops smoke       # /health/live, /health/ready, /api/v1/auth/config, /
poetry run prodavan-ops dns-check   # резолв ИЗ пода; дефолт ищет под сначала в dev-контуре
resolvectl status                   # Global показывает ровно 9.9.9.9 8.8.8.8 (DNS-контракт v14, §3.1)
```

`wait`/`rollout`/`smoke`/`dns-check` требуют живого кластера; `validate` — только
`kubectl`/`kustomize` на PATH. Готовность k3s API ops-CLI проверяет через `/readyz`
(а не `get ns agentscale-dev` — на свежем кластере namespace появляется только после
sync платформы, и старая проба выглядела как «API не готов» на здоровом кластере).

**Шаг 5. UI и доступы:**

- UI: **http://172.31.156.203:8088/** или **https://172.31.156.203/** (для HTTPS —
  импорт `agentscale-dev-ca.crt` в доверенные корневые браузера, см. VM README);
- Keycloak: **http://172.31.156.203:8089/** (realm `agentscale`, логин `admin` / `admin`);
- kubectl с Windows: `scp www@172.31.156.203:.kube/prodavan-dev.yaml $env:USERPROFILE\.kube\prodavan-dev.yaml`
  и заменить в файле `server: https://127.0.0.1:6443` → `https://172.31.156.203:6443`.

### 2.3 Prod-кластер (выделенный сервер, только prod)

Terraform для prod выполняется **с Windows-хоста** по SSH (на сервере terraform не нужен):

```powershell
$env:TF_VAR_ghcr_token = (gh auth token)
terraform -chdir=infra/terraform/environments/prod init
terraform -chdir=infra/terraform/environments/prod apply -auto-approve
# UI: https://185.103.100.124/  (или http://185.103.100.124:30090/)
```

Отличия: ssh `vzlet-ai@185.103.100.124:44345` (ключ `~/.ssh/myserver_key`),
`gitops_apps = "prod"` → root-app-prod синкает **только** projects + agent-sandbox +
agentscale-prod (dev/e2e на этот хост не ставятся), Traefik на 80/443, CI-раннеров нет.
Подробности: [`infra/terraform/environments/prod/README.md`](../../infra/terraform/environments/prod/README.md).

Day-2: merge в `main` + Argo. Не `kubectl apply -k infra/k3s/...` руками.

### 2.4 Устойчивость старта кластера

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

На VM (`host_profile = "vm"`) Docker Engine **сосуществует** с k3s — он нужен
CI-раннерам на том же хосте, k3s его не трогает (не маскировать). Правило WSL-эры
«маскировать docker» относится только к `host_profile = "wsl"` (исторический контур).  
Не оставлять незалогиненный Tailscale рядом с CNI.

### 3.1 DNS кластера после ребута (инциденты 2026-10-22, 2026-10-23, 2026-10-29)

**Симптом.** Агент из пода не может достучаться до провайдера ИИ. При этом
всё остальное зелёное: поды `Running`, HTTP-пробы OK, TCP-egress работает
(по IP провайдер отвечает). Выглядит как «сломался агент», а ломается DNS.
2026-10-29 на dev: `auth.x.ai` перестал резолвиться, Grok OAuth (device-flow и
refresh) отдавал 502 «Сервер временно недоступен».

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
kubectl -n agentscale-dev exec <pod> -- python3 -c \
  "import socket;print(socket.gethostbyname('github.com'))"

# поток таймаутов CoreDNS = мёртвый upstream
kubectl -n kube-system logs -l k8s-app=kube-dns --since=120s | grep -c 'i/o timeout'

# жив ли upstream, который видит хост
dig +short @<host-nameserver> github.com
```

Или одной командой (падает с готовым диагнозом и инструкцией):

```bash
prodavan-ops dns-check
# без --namespace под ищет в dev-контуре первым:
#   agentscale-dev → agentscale-dev-sandboxes → agentscale → agentscale-sandboxes
# (старый дефолт `agentscale` на чистом dev-кластере падал как «нет Running-пода»,
# хотя DNS был жив — на dev-контуре подов в prod-ns просто нет)
```

**Фикс (декларативный).** Не полагаться на единственный upstream из
`/etc/resolv.conf` и не полагаться на DoT, который может быть закрыт сетью
(инцидент 2026-10-29: `DNSOverTLS=yes` на `1.1.1.1:853`, порт заблокирован →
pod'ы перестали резолвить `auth.x.ai`, Grok OAuth отдавал 502).

**DNS-контракт v14 (dev-VM после чистой установки).** Всё задаётся Terraform-модулем
`infra/terraform/modules/k3s-dev-host`:

1. **Хост**: `systemd-resolved`; управляемый drop-in
   `/etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf` — сброс `DNS=` +
   `DNS=9.9.9.9 8.8.8.8`, `DNSOverTLS=no`, пустой `FallbackDNS=`,
   `DNSStubListenerExtra=172.31.156.203` (IP ноды, а не loopback — SSH-хост
   `127.0.0.1` для stub-слушателя непригоден). Legacy-файл `99-dot.conf` (ручной
   артефакт инцидента 2026-10-29, тащил `1.1.1.1` первым) **удаляется**
   provisioner'ом.
2. **`/etc/resolv.conf`**: штатный симлинк → `/run/systemd/resolve/stub-resolv.conf`
   (`127.0.0.53`). Прежний ручной статический файл (`nameserver 172.31.156.203`)
   нормализуется provisioner'ом.
3. **`/etc/k3s-resolv.conf`**: terraform-управляемый **по содержимому**
   (идемпотентно): `nameserver 9.9.9.9` + `nameserver 8.8.8.8` **напрямую**, без
   stub — kubelet/CoreDNS (`dnsPolicy: Default`) не зависят от `systemd-resolved`.
4. **k3s `config.yaml`**: `resolv-conf: /etc/k3s-resolv.conf` (дописывается
   provisioner'ом; файл читается только на старте k3s — при дрейфе config.yaml
   сервис перезапускается).
5. **`post-k3s-heal.sh`** (systemd `ExecStartPost` k3s) патчит Corefile CoreDNS на
   `forward . 9.9.9.9 8.8.8.8 { policy sequential; max_fails 1; health_check 5s }`
   после каждого старта k3s (k3s перезаписывает addon ConfigMap) — остаётся
   страховкой; стартовое окно больше не деградирует. Значения upstream'ов входят в
   triggers `null_resource`, так что `terraform apply` переустанавливает скрипт при
   их изменении.
6. **Проверка**: `prodavan-ops dns-check` (ищет под в dev-ns первым; хосты
   `github.com`, `auth.x.ai`) + после apply `resolvectl status` должен показывать
   Global ровно `9.9.9.9 8.8.8.8`.

Переменные модуля (значения для dev): `dns_upstreams = "9.9.9.9 8.8.8.8"`
(1.1.1.1:53 из CoreDNS-пода ловит таймауты под нагрузкой searchnet — проверено
2026-10-29), `host_dns_servers = "9.9.9.9 8.8.8.8"`, `host_dns_over_tls = "no"`,
`host_dns_stub_listener_extra = 172.31.156.203`, `k3s_resolv_conf = "/etc/k3s-resolv.conf"`.

`Verify Dev` прогоняет `prodavan-ops dns-check --namespace agentscale-dev`;
`REQUIRED_HOSTS` включает `auth.x.ai`, поэтому битый DNS валит деплой, а не
всплывает позже как «Сервер временно недоступен».

**Break-glass (только если Terraform/boot-heal ещё не применён).** Патч
ConfigMap руками; после этого всё равно привести состояние к Terraform:

```bash
kubectl -n kube-system get cm coredns -o yaml > /tmp/coredns-backup.yaml
# заменить:  forward . /etc/resolv.conf
# на:        forward . 9.9.9.9 8.8.8.8 { policy sequential  max_fails 1  health_check 5s }
kubectl -n kube-system rollout restart deploy/coredns
```

Проверка: `dns-check` резолвит **из пода** (с хоста ClusterIP недоступен);
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
NODE=$(sudo k3s kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl get node "$NODE" -o jsonpath='{.status.allocatable.memory}'
# на момент инцидента 2026-10-23: 12806844Ki  (было 15608508Ki)

sudo k3s kubectl get --raw "/api/v1/nodes/$NODE/proxy/configz" \
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
| Verify Dev | `rollout` + `wait` + `dns-check` (github.com, auth.x.ai) + `smoke` |
| Auto-merge | squash после Gate |

Self-hosted runners — на той же VM (systemd-сервисы `actions.runner.*`): [`infra/github-runner-vm/`](../../infra/github-runner-vm/README.md). Kubeconfig для Verify `/home/runner/.kube/prodavan-dev.yaml` кладёт тот же terraform apply (`runner_kubeconfig_path`, server `127.0.0.1:6443`); с Windows ничего синхронизировать не нужно. Исторический Docker Desktop-вариант — [`infra/github-runner/`](../../infra/github-runner/README.md) (не bootstrap UI).

---

## 5. MinIO (object store)

| Компонент | Значение |
|-----------|----------|
| Service | `agentscale-minio:9000` (консоль :9001), ns `agentscale-dev` |
| Bucket | `agentscale` (private, versioning off) |
| Init Job | `agentscale-minio-init` — **Sync hook, wave 9** (до `agentscale-api` wave 10); bucket + IAM user `agentscale-api` |
| API creds | `S3_ACCESS_KEY=agentscale-api` в `agentscale-api-secrets` |
| Root creds | `agentscale-minio` Secret — только init / break-glass |
| PVC | `agentscale-minio-data` (10Gi, RWO) — данные переживают pod reschedule |

**Ротация API creds (dev/staging):**

1. Сгенерировать новый пароль; обновить `agentscale-minio` Secret (`api-password`) и `agentscale-api-secrets` (`S3_SECRET_KEY`) в git (SealedSecret на shared).
2. Argo sync → init Job пересоздаёт/обновляет user policy (idempotent).
3. Rollout `agentscale-api`; smoke `/health/ready`.

**Verify PVC retain:** `prodavan-ops validate` проверяет наличие PVC `agentscale-minio-data` (и Job `agentscale-minio-init` c hook Sync wave 9) в overlay render.

API **не** вызывает `create_bucket` на startup — только `head_bucket` health.

---

## 5b. MongoDB (Document Store)

| Компонент | Значение |
|-----------|----------|
| Service | `agentscale-mongodb:27017` (ns `agentscale-dev`) |
| Init Job | `agentscale-mongodb-init` — Sync hook wave 9; app user `agentscale` + db `agentscale` |
| API creds | `MONGODB_URL` в `agentscale-api-secrets` (app user, `authSource=admin`) |
| Root creds | `agentscale-mongodb` Secret — только init / break-glass |
| PVC | `agentscale-mongodb-data` (5Gi, RWO) |
| Sandbox | **нет** egress на 27017 — только API process |

Document Store BC: in-proc Port + admin HTTP; Kafka bus `document` → `agentscale.document.events`.

---

## 5c. OpenSearch (Search Index)

| Компонент | Значение |
|-----------|----------|
| Service | `agentscale-opensearch:9200` (ns `agentscale-dev`) |
| Init Job | `agentscale-opensearch-init` — Sync hook wave 9; smoke `/_cluster/health` |
| API | `OPENSEARCH_URL`, `OPENSEARCH_ENABLED`, `OPENSEARCH_REQUIRED` in `agentscale-config` |
| Security | MVP: `DISABLE_SECURITY_PLUGIN=true`; ACL in Search Index BC |
| PVC | `agentscale-opensearch-data` (10Gi, RWO) |
| Sandbox | **нет** egress на 9200 — только API process |

Search Index BC: in-proc Port + admin HTTP `/admin/search-index`; Kafka bus `search` → `agentscale.search.events`.

---

## 6. Keycloak (identity)

| Компонент | Значение |
|-----------|----------|
| Public issuer | `http://127.0.0.1:8089/realms/agentscale` (Keycloak hostPort 8089; с Windows — `http://172.31.156.203:8089/`) |
| In-cluster Admin | `http://agentscale-keycloak:8080` |
| Init Job | `agentscale-keycloak-init` — Sync hook wave 8 (до API); realm `agentscale` + clients + SA roles + user `admin`/`admin` |
| Platform Admin | username `admin`, password `admin`, realm role `platform.admin` |
| API | `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`, JWKS via in-cluster URL |

Flutter login: ROPC (`grant_type=password`) на client `agentscale-flutter`. One-click test personas удалены.

