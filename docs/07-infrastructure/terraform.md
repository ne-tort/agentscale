# Terraform (as-built)

Живой Terraform в этом репозитории — **bootstrap/destroy кластеров** через один модуль
[`modules/k3s-dev-host`](../../infra/terraform/modules/k3s-dev-host) (null_resource +
SSH-provisioner'ы; контракт provisioner'ов — `v14-clean-install`, ревизия в
`locals.k3s_rev`). Day-2 поставка — **не** через Terraform, а через GitOps
(git → Argo CD → kustomize → k3s): [`runbook.md`](runbook.md).

Облачные модули (`network`, `k3s-cluster`, `postgres`, `object-storage`) и окружения
`environments/dev`, `environments/staging` — **неприменённые cloud skeletons**
(заглушки дизайна, провайдеры не подключены). Не путать их с живыми окружениями ниже;
`backends.tf` содержит реальный backend `local`, S3-backend закомментирован.

---

## Окружения (живые)

| Окружение | Хост | Где выполняется terraform | State | Что ставит |
|-----------|------|---------------------------|-------|------------|
| [`environments/vm`](../../infra/terraform/environments/vm/README.md) | dev-VM `www@172.31.156.203` (Ubuntu 24.04, там же CI-раннеры) | **на VM** (SSH на localhost, ключ `~/.ssh/prodavan_tf`, checkout `~/git/agentscale`) | `backend "local"` → `terraform.tfstate` в каталоге env **на VM** | k3s + Traefik hostPort 8088/443 (локальный CA) + Argo CD + sealed-secrets + root-app (`gitops_apps = "all"`: dev+prod+e2e контуры) + kubeconfig'и (www + runner) |
| [`environments/prod`](../../infra/terraform/environments/prod/README.md) | выделенный сервер `vzlet-ai@185.103.100.124:44345` | **с Windows-хоста** по SSH (ключ `~/.ssh/myserver_key`) | `backend "local"` → `terraform.tfstate` рядом с env (на Windows) | k3s + Traefik 80/443 + Argo + root-app-prod (`gitops_apps = "prod"`: только projects + agent-sandbox + agentscale-prod, из ветки `prod`) |
| `environments/local` | исторический (Kali WSL — удалён) | — | — | не используется; сохранён как справка по `host_profile = "wsl"` |

Команды полного цикла (destroy → apply с нуля, force_reinstall при потерянном state,
тайминги, верификация) — [`runbook.md` §2](runbook.md) и README соответствующего окружения.

---

## State

```hcl
# backends.tf (реальный backend; S3 закомментирован до появления облачного state)
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
```

State живёт там, где выполняется terraform: dev — на VM (`~/git/agentscale/infra/terraform/environments/vm/terraform.tfstate`),
prod — на Windows. Workspaces не используются; изоляция сред — каталогами окружений.

**Потеря state (dev) не блокирует снос/установку:** чистая установка поверх живого
кластера делается флагом `force_reinstall` (см. runbook §2.2, шаг 1b).

---

## Модуль k3s-dev-host — что делают provisioner'ы

Порядок create: `host_dns` → `k3s_server` → (`k3s_uninstall` — только destroy-time) →
`gitops_bootstrap`. Порядок destroy — обратный.

| Ресурс | Назначение |
|--------|------------|
| `null_resource.host_dns` | DNS-контракт v14 на хосте: managed drop-in `systemd-resolved` (сброс `DNS=` + `DNS=9.9.9.9 8.8.8.8`, `DNSOverTLS=no`, пустой `FallbackDNS=`, `DNSStubListenerExtra=<IP ноды>`), удаление legacy `99-dot.conf`, нормализация `/etc/resolv.conf` в штатный симлинк stub-resolv.conf + снятие drop-in при destroy |
| `null_resource.k3s_server` | `/etc/k3s-resolv.conf` (terraform-управляемый по содержимому: `nameserver 9.9.9.9` + `nameserver 8.8.8.8` напрямую, без stub), sysctl unprivileged ports, config.yaml (kubelet-резервы/eviction-пороги + `resolv-conf:`), manifests (traefik-port, platform-guards), boot-heal drop-in (ExecStartPost), preflight/post-k3s-heal/TLS-скрипты, опциональный wipe (`force_reinstall`), установка k3s (get.k3s.io, retry) или рестарт при дрейфе config.yaml, wait `/readyz` **540 с** + node Ready 300 с, kubeconfig'и (оператор + CI runner) |
| `null_resource.k3s_uninstall` | destroy-time полный wipe: k3s-uninstall.sh + явный rm (data-dir с etcd/sqlite, containerd-образами, local-path PVC и TLS CA; `/etc/rancher/k3s`, kubelet/cni/run-каталоги, бинарники, unit, boot-heal, `/usr/local/lib/prodavan`, kubeconfig'и, экспорт CA) + iptables-чистка `KUBE-`/`CNI-`/flannel. **Без** `on_failure = continue`: снос либо удался, либо destroy падает |
| `null_resource.gitops_bootstrap` | Argo CD install (wait `argocd-server`/`argocd-repo-server` по **600 с**), секреты `repo-agentscale` + `ghcr-pull` (из `TF_VAR_ghcr_token`; **пустой токен — немедленное падение** с подсказкой), sealed-secrets (300 с), root-app, wait `agent-sandbox` 8 мин (WARN) и платформы (`agentscale-dev`/`agentscale-prod`) Synced+Healthy **18 мин** с диагностикой на провале. Идемпотентен: повторный apply при живой платформе выходит на `gitops-already-healthy` |
| `null_resource.sshd` | только `host_profile = "wsl"` (кастомный sshd:2222); на VM не создаётся |
| `null_resource.windows_kubeconfig` | только CI-легаси (`export_docker_kubeconfig = true`); в живых окружениях выключен |

Ключевые переменные модуля: `host_profile` (vm|wsl), `force_reinstall`, `ghcr_token`
(`TF_VAR_ghcr_token`, read:packages), `k3s_resolv_conf` (dev: `/etc/k3s-resolv.conf`),
`dns_upstreams` / `host_dns_servers` / `host_dns_over_tls` / `host_dns_stub_listener_extra`
(declarative DNS, см. runbook §3.1), `https_tls_sans` (локальный CA + leaf как default
TLSStore Traefik), `k3s_tls_sans`, `runner_kubeconfig_path`, `gitops_apps` (all|prod),
`bootstrap_gitops`, `k3s_version` (намеренно **не** в triggers — уже работающий k3s не
переустанавливается).

Смена смысла provisioner'ов повышает `locals.k3s_rev` (сейчас `v14-clean-install`) —
это пересоздаёт `k3s_server` и `gitops_bootstrap` при следующем apply.

---

## Apply workflow

CI plan/apply **нет** — Terraform запускает оператор вручную:

```bash
# dev (на VM; токен — с Windows, gh на VM НЕ залогинен: pwsh> gh auth token):
ssh www@172.31.156.203
cd ~/git/agentscale && git pull --ff-only
export TF_VAR_ghcr_token='<токен с Windows>'      # ОБЯЗАТЕЛЕН
terraform -chdir=infra/terraform/environments/vm init
terraform -chdir=infra/terraform/environments/vm apply -auto-approve
```

```powershell
# prod (с Windows):
$env:TF_VAR_ghcr_token = (gh auth token)
terraform -chdir=infra/terraform/environments/prod init
terraform -chdir=infra/terraform/environments/prod apply -auto-approve
```

Повторный apply идемпотентен; первый apply после сноса на dev занимает ~25–40 мин
(тайминги по этапам — runbook §2.2).

---

## Что Terraform НЕ управляет

| Ресурс | Инструмент |
|--------|------------|
| K8s Deployments/сервисы платформы | Argo CD + `infra/k3s/` (PR → main → CI Images → sync) |
| Секреты приложений | SealedSecrets (`infra/argocd/sealed-secrets`) / `ghcr-pull` из bootstrap |
| CI-раннеры | [`infra/github-runner-vm/`](../../infra/github-runner-vm/README.md) (отдельный бутстрап, не terraform) |
| Argo CD apps | `infra/argocd/` (root-app syncs `apps/`) |

Кластер **нельзя** ставить docker-compose/k3d — это запрещено контрактом репо
(`prodavan-ops validate`).

---

## Связанные документы

- [runbook.md](runbook.md) — GitOps-канон, полный цикл «снести → поставить», DNS/OOM-инциденты
- [topology.md](topology.md)
- [k3s-services.md](k3s-services.md)
- [argocd.md](argocd.md)
- [env-matrix.md](env-matrix.md)
