# PROD cluster: Terraform → k3s + Argo на выделенном сервере

Выделенный prod-хост (без dev/e2e контуров и CI-раннеров). Один `terraform apply`
поднимает k3s + Traefik (80/443) + Argo CD + root-app (prod-only) → Argo синкает
prod-оверлей из ветки `prod`.

| Параметр | Значение |
|----------|----------|
| Хост | `vzlet-ai@185.103.100.124`, ssh-порт **44345**, ключ `~/.ssh/myserver_key` |
| UI (HTTPS) | **https://185.103.100.124/** — Traefik websecure :443, локальный CA (SAN: IP сервера) |
| UI (HTTP) | http://185.103.100.124/ и http://185.103.100.124:30090/ (NodePort) |
| Keycloak | http://185.103.100.124:8099/ (hostPort; issuer в токенах — 127.0.0.1:8099, менять не нужно) |
| k3s API | https://185.103.100.124:6443 (TLS SAN: 127.0.0.1 + IP сервера) |
| kubeconfig | `/home/vzlet-ai/.kube/prodavan-prod.yaml` |

## Bootstrap (однократно)

Host prep на сервере (единственные шаги вне Terraform):

```bash
# 1. Terraform НЕ нужен на сервере — apply идёт с Windows-хоста по SSH.
# 2. Репозиторий + sudoers для пользователя vzlet-ai:
git clone https://github.com/ne-tort/agentscale.git ~/git/agentscale
sudo install -m 440 ~/git/agentscale/infra/.ssh/prodavan-terraform.sudoers /etc/sudoers.d/prodavan-terraform
sudo sed -i 's/^Defaults:www /Defaults:vzlet-ai /; s/^www ALL/vzlet-ai ALL/' /etc/sudoers.d/prodavan-terraform
```

Дальше — один Terraform с Windows-хоста (PowerShell):

```powershell
$env:TF_VAR_ghcr_token = (gh auth token)   # read:packages; для ghcr-pull секрета
terraform -chdir=infra/terraform/environments/prod init
terraform -chdir=infra/terraform/environments/prod apply -auto-approve
```

Если ключ лежит не в `~/.ssh/myserver_key` — переопределить:
`$env:TF_VAR_ssh_private_key_path = "C:/Users/<user>/.ssh/myserver_key"`.

Итог: k3s (systemd) + Traefik 80/443 + boot-heal + Argo CD + root-app-prod
(`infra/argocd/root-app-prod.yaml`, directory.include только prod-приложения) →
prod-контур синкается из ветки `prod` (образы запинены в overlays/prod).

## Отличия от environments/vm

- `gitops_apps = "prod"`: root-app синкает ТОЛЬКО projects + agent-sandbox +
  agentscale-prod (dev/e2e приложения на prod-хост не ставятся).
- Нет runner-кubeconfig (CI-раннеров на prod-сервере нет).
- Порты 80/443 вместо 8088/443; SAN сертификата — публичный IP сервера.
- state-файл свой (`terraform.tfstate` рядом) — dev VM этим окружением не
  затрагивается.

## Day-2

- Поставка кода: PR → main → CI Images → промоция PR main→prod (пин newTag) →
  Argo sync. Не через terraform.
- `terraform destroy` снимает k3s с сервера через тот же SSH.
