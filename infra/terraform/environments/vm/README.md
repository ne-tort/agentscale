# VM cluster: Terraform → k3s + Argo на выделенной Linux VM

Канонический dev-кластер: та же схема «SSH + Terraform», что раньше жила в Kali WSL
(`../local/` — исторический), но на выделенной VM. Один `terraform apply` поднимает
k3s + Traefik hostPort + Argo CD + root-app → Argo синкает платформу из main.

| Параметр | Значение |
|----------|----------|
| VM | `www@172.31.156.203` (Ubuntu 24.04; CI-раннеры и Docker Engine на том же хосте — k3s их не трогает, profile `vm`) |
| UI (HTTPS) | **https://172.31.156.203/** — Traefik websecure на hostPort 443, сертификат локального CA (SAN: IP VM, 127.0.0.1, localhost, prodavan.dev) |
| UI (HTTP) | http://172.31.156.203:8088/ (smoke/CI) |
| Keycloak | http://172.31.156.203:8089/ |
| k3s API | https://172.31.156.203:6443 (TLS SAN: 127.0.0.1 + IP VM) |
| kubeconfig | `/home/www/.kube/prodavan-dev.yaml` (operator), `/home/runner/.kube/prodavan-dev.yaml` (CI-раннеры; сервер 127.0.0.1) |

## HTTPS в браузере (доверие к сертификату)

Terraform генерирует на VM локальный CA (`/var/lib/rancher/k3s/prodavan-tls/`) и leaf-сертификат,
кладёт его в Traefik как default TLSStore (`kube-system/prodavan-tls`), а CA экспортирует в
`/home/www/prodavan-dev-ca.crt`. Чтобы браузер доверял:

```powershell
# Windows (admin): импорт CA в доверенные корневые
scp www@172.31.156.203:prodavan-dev-ca.crt $env:TEMP\prodavan-dev-ca.crt
certutil -addstore -f ROOT $env:TEMP\prodavan-dev-ca.crt
# опционально: hosts-запись + portproxy для красивого имени
Add-Content C:\Windows\System32\drivers\etc\hosts "172.31.156.203 prodavan.dev"
netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=443 connectaddress=172.31.156.203 connectport=443
```

После этого работают без предупреждений: `https://172.31.156.203/`, `https://localhost/`,
`https://prodavan.dev/`. SANs задаёт переменная `https_tls_sans` (смена SAN = `terraform apply`,
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

```bash
cd ~/git/agentscale
export TF_VAR_ghcr_token="$(gh auth token)"   # read:packages; только для ghcr-pull секрета
terraform -chdir=infra/terraform/environments/vm init
terraform -chdir=infra/terraform/environments/vm apply -auto-approve
```

Итог: k3s (systemd, `k3s.service`) + Traefik на hostPort 8088/8443 + boot-heal
(ExecStartPost смоук-хелс) + Argo CD + sealed-secrets + root-app → платформа
синкается из `main` (ghcr-pull секрет создаётся из `TF_VAR_ghcr_token`).

Раннеры CI на этой же VM (`infra/github-runner-vm/`) получают kubeconfig
`/home/runner/.kube/prodavan-dev.yaml` из того же apply.

kubectl с Windows-хоста:

```powershell
scp www@172.31.156.203:.kube/prodavan-dev.yaml $env:USERPROFILE\.kube\prodavan-dev.yaml
# заменить в файле server: https://127.0.0.1:6443 → https://172.31.156.203:6443
```

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
