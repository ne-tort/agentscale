# VM cluster: Terraform → k3s + Argo на выделенной Linux VM

Канонический dev-кластер: та же схема «SSH + Terraform», что раньше жила в Kali WSL
(`../local/` — исторический), но на выделенной VM. Один `terraform apply` поднимает
k3s + Traefik hostPort + Argo CD + root-app → Argo синкает платформу из main.

| Параметр | Значение |
|----------|----------|
| VM | `www@172.31.156.203` (Ubuntu 24.04; CI-раннеры и Docker Engine на том же хосте — k3s их не трогает, profile `vm`) |
| UI (HTTPS) | **https://172.31.156.203/** — Traefik websecure на hostPort 443, сертификат локального CA (SAN: IP VM, 127.0.0.1, localhost, agentscale.dev) |
| UI (HTTP) | http://172.31.156.203:8088/ (smoke/CI) |
| Keycloak | http://172.31.156.203:8089/ |
| k3s API | https://172.31.156.203:6443 (TLS SAN: 127.0.0.1 + IP VM) |
| kubeconfig | `/home/www/.kube/prodavan-dev.yaml` (operator), `/home/runner/.kube/prodavan-dev.yaml` (CI-раннеры; сервер 127.0.0.1) |

## HTTPS в браузере (доверие к сертификату)

Terraform генерирует на VM локальный CA (`/var/lib/rancher/k3s/agentscale-tls/`) и leaf-сертификат,
кладёт его в Traefik как default TLSStore (`kube-system/agentscale-tls`), а CA экспортирует в
`/home/www/agentscale-dev-ca.crt`. Чтобы браузер доверял:

```powershell
# Windows (admin): импорт CA в доверенные корневые
scp www@172.31.156.203:agentscale-dev-ca.crt $env:TEMP\agentscale-dev-ca.crt
certutil -addstore -f ROOT $env:TEMP\agentscale-dev-ca.crt
# опционально: hosts-запись + portproxy для красивого имени
Add-Content C:\Windows\System32\drivers\etc\hosts "172.31.156.203 agentscale.dev"
netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=443 connectaddress=172.31.156.203 connectport=443
```

После этого работают без предупреждений: `https://172.31.156.203/`, `https://localhost/`,
`https://agentscale.dev/`. SANs задаёт переменная `https_tls_sans` (смена SAN = `terraform apply`,
leaf пересоздаётся, CA остаётся — переимпорт в браузер не нужен).

## Bootstrap (однократно)

Host prep (единственные шаги вне Terraform — типовые):

```bash
# 1. Terraform (официальный apt-репозиторий HashiCorp)
sudo apt-get install -y gnupg software-properties-common
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt-get update && sudo apt-get install -y terraform

# 2. SSH-ключ для localhost-провижининга + sudoers (файлы из репо)
ssh-keygen -t ed25519 -N '' -f ~/.ssh/prodavan_tf
cat ~/.ssh/prodavan_tf.pub >> ~/.ssh/authorized_keys
git clone https://github.com/ne-tort/agentscale.git ~/git/agentscale
sudo install -m 440 ~/git/agentscale/infra/.ssh/prodavan-terraform.sudoers /etc/sudoers.d/prodavan-terraform
```

Дальше — один Terraform:

> **Провайдеры: реестр HashiCorp гео-блокирован с этой VM.** `terraform init`
> безусловно обращается к `registry.terraform.io`, и с адресов, где CloudFront
> отдаёт «Content not available in your region», он падает с
> `Invalid provider registry host ... does not offer a Terraform provider
> registry` — даже когда нужная версия уже лежит в `.terraform/providers`.
> Обход (один раз на хост, переживает переустановки):
>
> ```bash
> # провайдер уже скачан прошлым apply — раскладываем его в файловую миррорку
> mkdir -p ~/.terraform.d/plugins/registry.terraform.io/hashicorp/null/3.3.2/linux_amd64
> cp -a ~/git/agentscale/infra/terraform/environments/vm/.terraform/providers/registry.terraform.io/hashicorp/null/3.3.2/linux_amd64/* \
>       ~/.terraform.d/plugins/registry.terraform.io/hashicorp/null/3.3.2/linux_amd64/
> cat > ~/.terraformrc <<'EOF'
> provider_installation {
>   filesystem_mirror {
>     path    = "/home/www/.terraform.d/plugins"
>     include = ["registry.terraform.io/hashicorp/*"]
>   }
>   direct {
>     exclude = ["registry.terraform.io/hashicorp/*"]
>   }
> }
> EOF
> ```
>
> Если кэша `.terraform/providers` на хосте ещё нет, `.terraformrc` не поможет —
> провайдер придётся принести с машины, где реестр доступен, и положить в ту же
> директорию миррорки вручную.

```bash
cd ~/git/agentscale
# gh на VM НЕ залогинен: токен (read:packages) получают на аутентифицированной
# машине и кладут на VM файлом вне git. С Windows (PowerShell):
#   gh auth token | ssh www@172.31.156.203 "umask 077; mkdir -p ~/.config; cat > ~/.config/ghcr-token"
# затем на VM:
export TF_VAR_ghcr_token="$(cat ~/.config/ghcr-token)"   # только для ghcr-pull секрета
terraform -chdir=infra/terraform/environments/vm init
terraform -chdir=infra/terraform/environments/vm apply -auto-approve
```

Итог: k3s (systemd, `k3s.service`) + Traefik на hostPort 8088/443 + boot-heal
(ExecStartPost смоук-хелс) + Argo CD + sealed-secrets + root-app → платформа
синкается из `main` (ghcr-pull секрет создаётся из `TF_VAR_ghcr_token`).

Раннеры CI на этой же VM (`infra/github-runner-vm/`) получают kubeconfig
`/home/runner/.kube/prodavan-dev.yaml` из того же apply.

Два разных ключа, не перепутать: `ci_vm_key` на Windows — это SSH **с Windows на
VM**; Terraform же ходит по localhost и использует `~/.ssh/prodavan_tf`
(`ssh_private_key_path`), который создаётся на VM шагом 2.

### Проверка после установки

```bash
ssh www@172.31.156.203
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd ~/git/agentscale/infra/ops && poetry install   # или python3 -m venv + pip install -e .
poetry run prodavan-ops validate    # контракт репо + kustomize render + пины образов
poetry run prodavan-ops wait        # Argo agentscale-dev Synced+Healthy
poetry run prodavan-ops smoke       # /health/live, /health/ready, /api/v1/auth/config, /
poetry run prodavan-ops dns-check   # резолв ИЗ пода (dev ns первым)
```

`validate` не требует кластера (нужны `kubectl`/`kustomize` на PATH); остальные
команды — после того как `wait` зелёный.

kubectl с Windows-хоста:

```powershell
scp www@172.31.156.203:.kube/prodavan-dev.yaml $env:USERPROFILE\.kube\prodavan-dev.yaml
# заменить в файле server: https://127.0.0.1:6443 → https://172.31.156.203:6443
```

## Полный цикл «снести → поставить с нуля»

Terraform запускается **на VM** (там же `/usr/bin/terraform`, checkout `~/git/agentscale`
и `terraform.tfstate`). С Windows — только SSH.

```bash
ssh www@172.31.156.203
# нужен актуальный модуль. Не «git pull --ff-only» вслепую: checkout на VM
# исторически остаётся на уже удалённой рабочей ветке, и pull падает с
# «no such remote ref». Сначала встаём на main:
cd ~/git/agentscale && git status --short --branch
git fetch origin && git checkout main && git pull --ff-only

# --- 1. снос ---------------------------------------------------------------
cd ~/git/agentscale/infra/terraform/environments/vm
terraform state list          # ожидаем 4 ресурса: k3s_server, k3s_uninstall, gitops_bootstrap[0], host_dns[0].
                                # В state ДО v13 нет host_dns: create-провижинера там не было,
                                # поэтому destroy его НЕ снимет (см. «Снос старым state» ниже).
                                # Если state потерян — шаг 1b
terraform destroy -auto-approve
# destroy прогоняет destroy-provisioner'ы в порядке, обратном зависимостям:
#   gitops_bootstrap → k3s_uninstall → k3s_server → host_dns
#   (sshd на профиле vm — count=0, windows_kubeconfig также)
# k3s_server объявлен с depends_on = [sshd, host_dns], поэтому host_dns
# снимается ПОСЛЕДНИМ: k3s ложится раньше, чем убирается хостовый DNS-конфиг.
# k3s_uninstall: k3s-uninstall.sh + явный rm. После него на хосте НЕ остаётся:
#   /var/lib/rancher/k3s (etcd/sqlite, containerd-образы, local-path PVC-данные),
#   /etc/rancher/k3s (включая config.yaml с resolv-conf), /var/lib/kubelet,
#   /var/lib/cni, /run/k3s, /run/flannel, /etc/systemd/system/k3s.service.d
#   (boot-heal), /usr/local/lib/prodavan, /usr/local/bin/k3s*,
#   TLS CA (/var/lib/rancher/k3s/agentscale-tls — уходит вместе с data-dir)
#   и оба kubeconfig (www + runner).
# Ни один destroy-провижинер не объявлен с `on_failure = continue`: если снос
# не удался, destroy падает с ошибкой, а не «успешно» оставляет живой k3s.

# --- 1b. если state потерян, а кластер жив --------------------------------
# destroy нечего удалять. Ставим заново поверх живого кластера одной командой:
terraform apply -auto-approve -var force_reinstall=true
terraform apply -auto-approve -var force_reinstall=false   # снять флаг
# force_reinstall стирает предыдущее состояние перед установкой (k3s-uninstall
# + rm data-dir) и входит в triggers, поэтому пересоздаёт provisioner'ы.

# --- 2. установка ----------------------------------------------------------
# gh на VM НЕ залогинен — токен заранее лежит в ~/.config/ghcr-token (см. «Bootstrap»)
export TF_VAR_ghcr_token="$(cat ~/.config/ghcr-token)"   # read:packages — ОБЯЗАТЕЛЕН
terraform init
terraform apply -auto-approve
```

Пустой `TF_VAR_ghcr_token` теперь падает **сразу** в `gitops-bootstrap.sh` с
объяснением и точной командой (раньше печатался один WARN, и через 12 минут
apply падал по таймауту — выглядело как сломанный кластер).

### Снос старым state новым модулем

Destroy-time provisioner видит только `self.triggers`, записанные в state на
момент `apply`. Поэтому «новый код против старого state» — это ограниченный
сценарий, и вот его границы:

| Что в state | Что реально произойдёт |
|-------------|-------------------------|
| Есть `host_dns[0]` с `ssh_*` (state v13+) | drop-in удаляется, resolved перезапускается |
| **Нет** `host_dns` (state до v13) | delete этого ресурса не будет вообще → `99-zz-agentscale-dns.conf` остаётся на хосте |
| Нет ключей `ssh_host`/`ssh_port`/`ssh_user`/`ssh_key_path` (state совсем ранний) | connection берёт fallback через `try()`: `127.0.0.1:22`, user `www`, ключ `/home/www/.ssh/prodavan_tf`. Если пользователь/порт/ключ другие — destroy падает на SSH (и это правильный сигнал, а не тихий пропуск) |
| `k3s_uninstall[0]` | всё равно сработает: его скрипт сам находит `k3s-uninstall.sh`, data-dir и kubeconfig по `ssh_user` |

Если state старый/потерян, а хост живой, безопаснее не полагаться на destroy
(см. шаг 1b — `force_reinstall`), либо один раз снять хвосты руками:

```bash
ssh www@172.31.156.203 'sudo -n /usr/local/bin/k3s-uninstall.sh || true; \
  sudo -n rm -f /etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf; \
  sudo -n systemctl restart systemd-resolved'
```

### Схема DNS (детерминированная, управляется terraform)

- `host_dns` (create): drop-in `/etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf`
  — пустой `DNS=` (сброс накопленного systemd списка) + `DNS=9.9.9.9 8.8.8.8`,
  `FallbackDNS=` (пусто, осознанно), `DNSOverTLS=no`, `DNSStubListenerExtra=172.31.156.203`;
  удаляет ручной legacy-артефакт `99-dot.conf` (инцидент 2026-10-29: его rate-limited
  1.1.1.1 накапливался ПЕРЕД нашими upstream) и нормализует `/etc/resolv.conf`
  в штатный симлинк на `/run/systemd/resolve/stub-resolv.conf` (статический файл
  с единственным nameserver=stub-IP был точкой отказа хостового резолва).
  Проверка готовности: `getent hosts github.com` (20×1с), иначе apply падает с диагностикой.
- `/etc/k3s-resolv.conf` (k3s_server): управляемый файл — ПРЯМЫЕ upstream'ы
  (`nameserver 9.9.9.9`, `nameserver 8.8.8.8`), переписывается при отличии содержимого
  (tmp + mv). kubelet/CoreDNS (dnsPolicy: Default) не зависят от живости resolved.
- `post-k3s-heal.sh`: страховка — явный `forward . 9.9.9.9 8.8.8.8` в Corefile
  CoreDNS после каждого старта k3s. Якорь — регулярка на строку `forward … /etc/resolv.conf`
  (работает при любом отступе в блоке server), а не литерал с фиксированными
  пробелами: иначе правка молча уходила в «не распознан — не трогаю».
- `host_dns` (destroy): удаляет только управляемый drop-in и рестартует resolved
  (идемпотентно: повторный destroy на уже отсутствующем файле не падает).
  До следующего apply у хоста нет управляемых upstream — apply нужно запускать сразу
  за destroy (штатный сценарий «снести → поставить»).

### Если apply упал посередине

Просто повторите `terraform apply -auto-approve` — все provisioner'ы
идемпотентны:

| Что упало | Что будет при повторе |
|-----------|------------------------|
| k3s install / curl | ставит заново; уже установленный k3s не переустанавливается |
| k3s не поднялся за 540 с | падает с `systemctl status`/`journalctl` в выводе |
| Argo install (образы) | `apply -k` идемпотентен, wait 600 с на каждый deployment |
| платформа не Healthy за 18 мин | падает с дампом Application/подов/событий; повторный apply идёт с начала скрипта и выходит на `gitops-already-healthy`, если платформа успела встать |

Ни один шаг не требует «удалить state, чтобы повторить».

**Замечание про повторный apply при живом k3s:** `k3s_server` не переустанавливает
работающий k3s (это day-2 поведение), но теперь **перезапускает** k3s, если
изменился `/etc/rancher/k3s/config.yaml` (kubelet-arg/resolv-conf читаются только
на старте — раньше правка молча не применялась до ребута). Для установки именно
с нуля нужен `destroy` (шаг 1) или `force_reinstall` (шаг 1b).

## Day-2

- Поставка кода: PR → CI Gate → auto-merge → CI Images (ghcr `:latest`) → Argo sync →
  Verify Dev — **не** через terraform.
- `terraform destroy` — k3s-uninstall через тот же SSH (state хранит координаты).
- Зависимости apt на VM ставить руками нельзя «рядом» с кластером — кластерные
  компоненты только через Argo/kustomize.

## Отличия от environments/local (WSL)

- `host_profile = "vm"`: **без** кастомного `prodavan-sshd` (системный sshd:22) и
  **без** отключения Docker Engine (на VM он нужен CI-раннерам; в WSL он ломал CNI).
- TLS SAN: `127.0.0.1` + IP VM (вместо `host.docker.internal`).
- Kubeconfig дублируется юзеру `runner` (CI).
- Traefik/API доступны с Windows-хоста напрямую по IP VM — portproxy 2222/6443/8088/8089
  и keepalive WSL больше не нужны.
