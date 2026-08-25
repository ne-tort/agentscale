terraform {
  required_version = ">= 1.5.0"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

locals {
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : "/home/${var.ssh_user}/.kube/prodavan-dev.yaml"
  kctl            = "sudo -n /usr/local/bin/k3s kubectl"
}

resource "null_resource" "sshd" {
  triggers = {
    rev              = "v5-listen-all"
    ssh_port         = tostring(var.ssh_port)
    ssh_user         = var.ssh_user
    sshd_config_hash = filesha256("${path.module}/../../../.ssh/sshd_config.tpl")
  }

  connection {
    type        = "ssh"
    host        = var.ssh_host
    port        = var.ssh_port
    user        = var.ssh_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "5m"
  }

  provisioner "file" {
    content     = file("${path.module}/../../../.ssh/sshd_config.tpl")
    destination = "/tmp/prodavan-sshd_config"
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/prodavan-sshd.service.tpl", {
      ssh_port = var.ssh_port
      ssh_user = var.ssh_user
    })
    destination = "/tmp/prodavan-sshd.service"
  }

  provisioner "remote-exec" {
    inline = [
      "bash -lc 'set -euo pipefail",
      "mkdir -p /home/${var.ssh_user}/.ssh/sshd-prodavan",
      "test -f /home/${var.ssh_user}/.ssh/sshd-prodavan/host_ed25519 || ssh-keygen -t ed25519 -N \"\" -f /home/${var.ssh_user}/.ssh/sshd-prodavan/host_ed25519",
      "mv /tmp/prodavan-sshd_config /home/${var.ssh_user}/.ssh/sshd-prodavan/sshd_config",
      "sudo -n mv /tmp/prodavan-sshd.service /etc/systemd/system/prodavan-sshd.service",
      "sudo -n systemctl daemon-reload",
      "sudo -n systemctl enable prodavan-sshd.service",
      "if systemctl is-active --quiet prodavan-sshd.service; then sudo -n systemctl restart prodavan-sshd.service || true; elif ! ss -tln | grep -q \":${var.ssh_port} \"; then sudo -n systemctl reset-failed prodavan-sshd.service || true; sudo -n systemctl start prodavan-sshd.service; fi",
      "ss -tln | grep -q \":${var.ssh_port} \"'",
    ]
  }
}

resource "null_resource" "k3s_server" {
  depends_on = [null_resource.sshd]

  triggers = {
    rev         = "v3-wsl-timeout-stop"
    k3s_version = var.k3s_version
    http_port   = tostring(var.http_port)
    cluster     = var.cluster_name
    traefik_tpl = filesha256("${path.module}/templates/traefik-port.yaml.tpl")
    # SSH coords in triggers so provisioners may only use self.*
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/traefik-port.yaml.tpl", {
      http_port = var.http_port
    })
    destination = "/tmp/prodavan-traefik-port.yaml"
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "10m"
    }
  }

  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "10m"
    }
    inline = [
      "bash -lc 'set -euo pipefail",
      "export PATH=\"$HOME/.local/bin:/usr/sbin:/usr/bin:$PATH\"",
      "sudo -n mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s",
      "sudo -n cp /tmp/prodavan-traefik-port.yaml /var/lib/rancher/k3s/server/manifests/prodavan-traefik-port.yaml",
      # Docker Engine inside WSL fights k3s CNI; runners use Docker Desktop on Windows.
      "if systemctl list-unit-files docker.service >/dev/null 2>&1; then sudo -n systemctl stop docker.socket docker 2>/dev/null || true; sudo -n systemctl disable --now docker.socket docker 2>/dev/null || true; sudo -n systemctl mask docker.socket docker 2>/dev/null || true; fi",
      # Broken/unauthenticated Tailscale netmon flaps routes around CNI veths on WSL.
      "if systemctl is-active --quiet tailscaled 2>/dev/null && ! tailscale status >/dev/null 2>&1; then sudo -n systemctl stop tailscaled 2>/dev/null || true; fi",
      "if ! command -v k3s >/dev/null 2>&1; then",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=\"${var.k3s_version}\" sh -s - server --write-kubeconfig-mode 644 --tls-san=127.0.0.1 --tls-san=prodavan.local",
      "elif ! sudo -n systemctl is-active --quiet k3s; then",
      "  sudo -n systemctl start k3s",
      "else",
      "  echo k3s-already-running-skip-restart",
      "fi",
      # WSL terminates distros with systemctl poweroff and only waits ~10s; slow k3s
      # stop → InitTerminateInstanceInternal force reboot → eth0/Sandbox churn.
      "sudo -n mkdir -p /etc/systemd/system/k3s.service.d",
      "printf '%s\\n' '[Service]' 'TimeoutStopSec=8' 'TimeoutSec=8' | sudo -n tee /etc/systemd/system/k3s.service.d/prodavan-wsl-stop.conf >/dev/null",
      "sudo -n systemctl daemon-reload",
      "sudo -n systemctl enable k3s",
      "for i in $(seq 1 60); do sudo -n k3s kubectl get --raw=/readyz >/dev/null 2>&1 && break; sleep 2; done",
      "sudo -n k3s kubectl wait --for=condition=Ready node --all --timeout=180s",
      "mkdir -p /home/${var.ssh_user}/.kube",
      "sudo -n cp /etc/rancher/k3s/k3s.yaml ${local.kubeconfig_path}",
      "sudo -n chown ${var.ssh_user}:${var.ssh_user} ${local.kubeconfig_path}",
      "chmod 600 ${local.kubeconfig_path}'",
    ]
  }
}

# Uninstall only on full terraform destroy / cluster rename — NOT when k3s_server
# triggers (rev/traefik hash) change, otherwise every script tweak wipes the cluster.
resource "null_resource" "k3s_uninstall" {
  depends_on = [null_resource.k3s_server]

  triggers = {
    cluster      = var.cluster_name
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  provisioner "remote-exec" {
    when       = destroy
    on_failure = continue
    connection {
      type        = "ssh"
      host        = try(self.triggers.ssh_host, "127.0.0.1")
      port        = tonumber(try(self.triggers.ssh_port, "2222"))
      user        = try(self.triggers.ssh_user, "www")
      private_key = file(try(self.triggers.ssh_key_path, "${path.module}/../../../.ssh/prodavan_tf"))
      timeout     = "10m"
    }
    inline = [
      "bash -lc 'set -euo pipefail; if command -v k3s-uninstall.sh >/dev/null 2>&1; then sudo -n k3s-uninstall.sh; elif [ -x /usr/local/bin/k3s-uninstall.sh ]; then sudo -n /usr/local/bin/k3s-uninstall.sh; else echo WARN: k3s-uninstall.sh missing; fi'",
    ]
  }
}

resource "null_resource" "gitops_bootstrap" {
  count = var.bootstrap_gitops ? 1 : 0

  depends_on = [null_resource.k3s_server, null_resource.k3s_uninstall]

  triggers = {
    repo       = var.remote_repo_path
    gitops_rev = "v6-plain-sh"
    has_token  = var.ghcr_token != "" ? "yes" : "no"
  }

  connection {
    type        = "ssh"
    host        = var.ssh_host
    port        = var.ssh_port
    user        = var.ssh_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "10m"
  }

  provisioner "remote-exec" {
    # Plain sh lines (remote-exec wraps in a script). Absolute k3s path.
    # Cold Argo sync can take several minutes.
    inline = concat(
      [
        "set -eu",
        "export KUBECONFIG=${local.kubeconfig_path}",
        "export PATH=/usr/local/bin:/usr/bin:/bin",
        "test -d ${var.remote_repo_path}/infra/argocd/install",
        "sudo -n /usr/local/bin/k3s kubectl apply -k ${var.remote_repo_path}/infra/argocd/install",
        "sudo -n /usr/local/bin/k3s kubectl -n argocd wait --for=condition=Available deployment/argocd-server --timeout=300s",
        "sudo -n /usr/local/bin/k3s kubectl -n argocd wait --for=condition=Available deployment/argocd-repo-server --timeout=300s",
      ],
      var.ghcr_token != "" ? [
        "sudo -n /usr/local/bin/k3s kubectl -n argocd delete secret repo-prodavan --ignore-not-found",
        "sudo -n /usr/local/bin/k3s kubectl -n argocd create secret generic repo-prodavan --from-literal=type=git --from-literal=url=https://github.com/ne-tort/prodavan.git --from-literal=username=git --from-literal=password=${var.ghcr_token}",
        "sudo -n /usr/local/bin/k3s kubectl -n argocd label secret repo-prodavan argocd.argoproj.io/secret-type=repository --overwrite",
      ] : [
        "echo WARN: no TF_VAR_ghcr_token — Argo cannot sync private repo",
      ],
      [
        "sudo -n /usr/local/bin/k3s kubectl apply -k ${var.remote_repo_path}/infra/argocd/sealed-secrets",
        "sudo -n /usr/local/bin/k3s kubectl -n kube-system wait --for=condition=Available deployment/sealed-secrets-controller --timeout=180s",
      ],
      var.ghcr_token != "" ? [
        "sudo -n /usr/local/bin/k3s kubectl create namespace prodavan --dry-run=client -o yaml | sudo -n /usr/local/bin/k3s kubectl apply -f -",
        "sudo -n /usr/local/bin/k3s kubectl -n prodavan delete secret ghcr-pull --ignore-not-found",
        "sudo -n /usr/local/bin/k3s kubectl -n prodavan create secret docker-registry ghcr-pull --docker-server=ghcr.io --docker-username=${var.ghcr_username} --docker-password=${var.ghcr_token}",
      ] : [],
      [
        "sudo -n /usr/local/bin/k3s kubectl apply -f ${var.remote_repo_path}/infra/argocd/root-app.yaml",
        "i=0; while [ $$i -lt 72 ]; do i=$$((i+1)); sync=$$(sudo -n /usr/local/bin/k3s kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.sync.status}' 2>/dev/null || echo Pending); health=$$(sudo -n /usr/local/bin/k3s kubectl -n argocd get application prodavan-dev -o jsonpath='{.status.health.status}' 2>/dev/null || echo Unknown); echo prodavan-dev sync=$$sync health=$$health; if [ \"$$sync\" = Synced ] && [ \"$$health\" = Healthy ]; then exit 0; fi; sleep 10; done",
        "sudo -n /usr/local/bin/k3s kubectl -n argocd get applications",
        "sudo -n /usr/local/bin/k3s kubectl -n prodavan get pods",
        "exit 1",
      ]
    )
  }
}

output "kubeconfig_path" {
  value = local.kubeconfig_path
}

output "api_endpoint" {
  value = "https://127.0.0.1:${var.api_port}"
}

output "http_url" {
  value = "http://prodavan.local:${var.http_port}/"
}

output "cluster_name" {
  value = var.cluster_name
}
