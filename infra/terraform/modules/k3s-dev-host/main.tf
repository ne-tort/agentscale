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
  kctl            = "sudo -n k3s kubectl"
}

resource "null_resource" "sshd" {
  triggers = {
    rev              = "v3"
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
      "if ss -tln | grep -q \":${var.ssh_port} \"; then sudo -n systemctl reload prodavan-sshd.service || true; else sudo -n systemctl start prodavan-sshd.service; fi",
      "ss -tln | grep -q \":${var.ssh_port} \"'",
    ]
  }
}

resource "null_resource" "k3s_server" {
  depends_on = [null_resource.sshd]

  triggers = {
    k3s_version    = var.k3s_version
    http_port      = tostring(var.http_port)
    cluster        = var.cluster_name
    traefik_tpl    = filesha256("${path.module}/templates/traefik-port.yaml.tpl")
  }

  connection {
    type        = "ssh"
    host        = var.ssh_host
    port        = var.ssh_port
    user        = var.ssh_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "10m"
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/traefik-port.yaml.tpl", {
      http_port = var.http_port
    })
    destination = "/tmp/prodavan-traefik-port.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      "bash -lc 'set -euo pipefail",
      "export PATH=\"$HOME/.local/bin:/usr/sbin:/usr/bin:$PATH\"",
      "sudo -n mkdir -p /var/lib/rancher/k3s/server/manifests",
      "sudo -n cp /tmp/prodavan-traefik-port.yaml /var/lib/rancher/k3s/server/manifests/prodavan-traefik-port.yaml",
      "if ! command -v k3s >/dev/null 2>&1; then",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=\"${var.k3s_version}\" sh -s - server --write-kubeconfig-mode 644 --tls-san=127.0.0.1 --tls-san=prodavan.local",
      "else",
      "  sudo -n systemctl restart k3s",
      "fi",
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

resource "null_resource" "gitops_bootstrap" {
  count = var.bootstrap_gitops ? 1 : 0

  depends_on = [null_resource.k3s_server]

  triggers = {
    repo       = var.remote_repo_path
    gitops_rev = "v4"
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
    inline = concat(
      [
        "bash -lc 'set -euo pipefail",
        "export KUBECONFIG=${local.kubeconfig_path}",
        "K=${local.kctl}",
        "test -d ${var.remote_repo_path}/infra/argocd/install",
        "$K apply -k ${var.remote_repo_path}/infra/argocd/install",
        "$K -n argocd wait --for=condition=Available deployment/argocd-server --timeout=300s",
        "$K -n argocd wait --for=condition=Available deployment/argocd-repo-server --timeout=300s",
      ],
      var.ghcr_token != "" ? [
        "$K -n argocd delete secret repo-prodavan --ignore-not-found",
        "$K -n argocd create secret generic repo-prodavan --from-literal=type=git --from-literal=url=https://github.com/ne-tort/prodavan.git --from-literal=username=git --from-literal=password=${var.ghcr_token}",
        "$K -n argocd label secret repo-prodavan argocd.argoproj.io/secret-type=repository --overwrite",
      ] : [
        "echo WARN: no TF_VAR_ghcr_token — Argo cannot sync private repo",
      ],
      [
        "$K apply -k ${var.remote_repo_path}/infra/argocd/sealed-secrets",
        "$K -n kube-system wait --for=condition=Available deployment/sealed-secrets-controller --timeout=180s",
      ],
      var.ghcr_token != "" ? [
        "$K create namespace prodavan --dry-run=client -o yaml | $K apply -f -",
        "$K -n prodavan delete secret ghcr-pull --ignore-not-found",
        "$K -n prodavan create secret docker-registry ghcr-pull --docker-server=ghcr.io --docker-username=${var.ghcr_username} --docker-password=${var.ghcr_token}",
      ] : [],
      [
        "$K apply -f ${var.remote_repo_path}/infra/argocd/root-app.yaml",
        "for i in $(seq 1 36); do",
        "  sync=$($K -n argocd get application prodavan-dev -o jsonpath={.status.sync.status} 2>/dev/null || echo Pending)",
        "  health=$($K -n argocd get application prodavan-dev -o jsonpath={.status.health.status} 2>/dev/null || echo Unknown)",
        "  echo prodavan-dev sync=$sync health=$health",
        "  test \"$sync\" = Synced -a \"$health\" = Healthy && exit 0",
        "  sleep 5",
        "done",
        "$K -n argocd get applications",
        "$K -n prodavan get pods",
        "exit 1'",
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
