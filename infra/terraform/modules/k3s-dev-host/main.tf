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
  tls_san_flags   = join(" ", [for s in var.k3s_tls_sans : "--tls-san=${s}"])
  # /mnt/c/Users/<user>/git/.../prodavan → /mnt/c/Users/<user>/.kube/prodavan-dev.yaml
  _repo_parts = split("/", var.remote_repo_path)
  # ["", "mnt", "c", "Users", "<user>", ...]
  windows_kubeconfig_path = (
    !var.export_docker_kubeconfig ? "" : (
      var.windows_kubeconfig_path != "" ? var.windows_kubeconfig_path : (
        length(local._repo_parts) >= 5 && local._repo_parts[1] == "mnt" && local._repo_parts[3] == "Users"
        ? "/mnt/c/Users/${local._repo_parts[4]}/.kube/prodavan-dev.yaml"
        : ""
      )
    )
  )
}

# Custom sshd is WSL-only: Kali WSL ships no sshd and Windows portproxy (2222)
# fronts it. A dedicated VM uses the system sshd — nothing to install.
resource "null_resource" "sshd" {
  count = var.host_profile == "wsl" ? 1 : 0

  triggers = {
    rev              = "v7-sshd-no-restart"
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
      # Never restart prodavan-sshd here: terraform is connected through it.
      "if ss -tln | grep -q \":${var.ssh_port} \"; then echo sshd-already-listening; else sudo -n systemctl reset-failed prodavan-sshd.service || true; sudo -n systemctl start prodavan-sshd.service; fi",
      "ss -tln | grep -q \":${var.ssh_port} \"'",
    ]
  }
}

resource "null_resource" "k3s_server" {
  depends_on = [null_resource.sshd]

  triggers = {
    # k3s_version intentionally NOT in triggers: already-running path skips
    # reinstall; changing the var alone must not churn SSH provisioners.
    # v9: host_profile param — vm skips custom sshd and keeps Docker Engine
    # (CI runners on the VM share it), tls-san list, runner kubeconfig.
    rev       = "v9-host-profile-vm"
    http_port = tostring(var.http_port)
    https_port = tostring(var.https_port)
    cluster   = var.cluster_name
    traefik_tpl = filesha256("${path.module}/templates/traefik-port.yaml.tpl")
    boot_heal = filesha256("${path.module}/templates/prodavan-boot-heal.conf.tpl")
    preflight = filesha256("${path.module}/templates/k3s-preflight.sh.tpl")
    post_heal = filesha256("${path.module}/templates/post-k3s-heal.sh.tpl")
    # SSH coords in triggers so provisioners may only use self.*
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/traefik-port.yaml.tpl", {
      http_port = var.http_port
      https_port = var.https_port
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

  provisioner "file" {
    content = templatefile("${path.module}/templates/prodavan-boot-heal.conf.tpl", {})
    destination = "/tmp/prodavan-boot-heal.conf"
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "10m"
    }
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/k3s-preflight.sh.tpl", {
      disable_docker = var.host_profile == "wsl"
    })
    destination = "/tmp/prodavan-k3s-preflight.sh"
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "10m"
    }
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/post-k3s-heal.sh.tpl", {
      http_port = var.http_port
    })
    destination = "/tmp/prodavan-post-k3s-heal.sh"
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
      # Do NOT wrap this block in bash -lc '...': nested tr -d '\r' breaks the outer
      # single quotes and becomes `tr -d r`, which strips every letter r from scripts.
      # NOTE: remote-exec runs this via /bin/sh (dash on Ubuntu) — POSIX only, no pipefail.
      "set -eu",
      "export PATH=\"$HOME/.local/bin:/usr/sbin:/usr/bin:$PATH\"",
      "sudo -n mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s /etc/systemd/system/k3s.service.d /usr/local/lib/prodavan",
      "sudo -n cp /tmp/prodavan-traefik-port.yaml /var/lib/rancher/k3s/server/manifests/prodavan-traefik-port.yaml",
      "sudo -n cp /tmp/prodavan-boot-heal.conf /etc/systemd/system/k3s.service.d/prodavan-boot-heal.conf",
      "python3 -c \"from pathlib import Path; p=Path('/tmp/prodavan-k3s-preflight.sh'); Path('/tmp/prodavan-k3s-preflight.lf').write_bytes(p.read_bytes().replace(b'\\r', b''))\"",
      "python3 -c \"from pathlib import Path; p=Path('/tmp/prodavan-post-k3s-heal.sh'); Path('/tmp/prodavan-post-k3s-heal.lf').write_bytes(p.read_bytes().replace(b'\\r', b''))\"",
      "sudo -n cp /tmp/prodavan-k3s-preflight.lf /usr/local/lib/prodavan/k3s-preflight.sh",
      "sudo -n cp /tmp/prodavan-post-k3s-heal.lf /usr/local/lib/prodavan/post-k3s-heal.sh",
      "sudo -n chmod 755 /usr/local/lib/prodavan/k3s-preflight.sh /usr/local/lib/prodavan/post-k3s-heal.sh",
      "sudo -n rm -f /etc/systemd/system/k3s.service.d/prodavan-wsl-stop.conf",
      # WSL only: Docker Engine inside WSL fights k3s CNI. On a vm-profile host
      # Docker stays — CI runners (same VM) build images with it.
      var.host_profile == "wsl" ? "if systemctl list-unit-files docker.service >/dev/null 2>&1; then sudo -n systemctl stop docker.socket docker 2>/dev/null || true; sudo -n systemctl disable --now docker.socket docker 2>/dev/null || true; sudo -n systemctl mask docker.socket docker 2>/dev/null || true; fi" : "echo vm-profile: docker engine left running for CI runners",
      # Broken/unauthenticated Tailscale netmon flaps routes around CNI veths on WSL.
      "if systemctl is-active --quiet tailscaled 2>/dev/null && ! tailscale status >/dev/null 2>&1; then sudo -n systemctl stop tailscaled 2>/dev/null || true; fi",
      "if ! command -v k3s >/dev/null 2>&1; then",
      "  curl -sfL https://get.k3s.io -o /tmp/prodavan-k3s-install.sh",
      "  INSTALL_K3S_VERSION=\"${var.k3s_version}\" sh /tmp/prodavan-k3s-install.sh server --write-kubeconfig-mode 644 ${local.tls_san_flags}",
      "elif ! sudo -n systemctl is-active --quiet k3s; then",
      "  sudo -n systemctl start k3s",
      "else",
      "  echo k3s-already-running-skip-restart",
      "fi",
      "sudo -n systemctl daemon-reload",
      "sudo -n systemctl enable k3s || true",
      "for i in $(seq 1 60); do sudo -n k3s kubectl get --raw=/readyz >/dev/null 2>&1 && break; sleep 2; done",
      "sudo -n k3s kubectl wait --for=condition=Ready node --all --timeout=180s",
      "mkdir -p /home/${var.ssh_user}/.kube",
      "sudo -n cp /etc/rancher/k3s/k3s.yaml ${local.kubeconfig_path}",
      "sudo -n chown ${var.ssh_user}:${var.ssh_user} ${local.kubeconfig_path}",
      "chmod 600 ${local.kubeconfig_path}",
      # vm profile: kubeconfig for the CI runner user (runners share this host;
      # k3s API is reachable at 127.0.0.1 — no host.docker.internal needed).
      "if [ -n \"${var.runner_kubeconfig_path}\" ] && id runner >/dev/null 2>&1; then sudo -n install -D -o runner -g runner -m 600 /etc/rancher/k3s/k3s.yaml \"${var.runner_kubeconfig_path}\"; else echo runner-kubeconfig-skipped; fi",
    ]
  }
}

# Optional CI helper (export_docker_kubeconfig=true): kubeconfig for Docker Desktop runners.
# Not required for UI — Traefik listens 0.0.0.0:${http_port}, open http://127.0.0.1:${http_port}/.
resource "null_resource" "windows_kubeconfig" {
  count = local.windows_kubeconfig_path != "" ? 1 : 0

  depends_on = [null_resource.k3s_server]

  triggers = {
    rev        = "v1"
    k3s_id     = null_resource.k3s_server.id
    win_path   = local.windows_kubeconfig_path
    script_sha = filesha256("${path.module}/templates/export-windows-kubeconfig.sh.tpl")
    ssh_host   = var.ssh_host
    ssh_port   = tostring(var.ssh_port)
    ssh_user   = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  connection {
    type        = "ssh"
    host        = self.triggers.ssh_host
    port        = tonumber(self.triggers.ssh_port)
    user        = self.triggers.ssh_user
    private_key = file(self.triggers.ssh_key_path)
    timeout     = "5m"
  }

  provisioner "file" {
    content     = file("${path.module}/templates/export-windows-kubeconfig.sh.tpl")
    destination = "/tmp/prodavan-export-win-kube.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "tr -d '\\r' < /tmp/prodavan-export-win-kube.sh > /tmp/prodavan-export-win-kube.lf && mv /tmp/prodavan-export-win-kube.lf /tmp/prodavan-export-win-kube.sh",
      "chmod 700 /tmp/prodavan-export-win-kube.sh",
      "KUBECONFIG_SRC=${local.kubeconfig_path} WINDOWS_KUBECONFIG=${local.windows_kubeconfig_path} API_PORT=${var.api_port} bash /tmp/prodavan-export-win-kube.sh",
    ]
  }
}

# Uninstall only on full terraform destroy / cluster rename — NOT when key path,
# traefik hash, or other mutable SSH coords change (that previously wiped k3s).
resource "null_resource" "k3s_uninstall" {
  depends_on = [null_resource.k3s_server]

  triggers = {
    cluster = var.cluster_name
    # SSH coords in triggers (frozen at create) for the destroy-time connection.
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
      port        = tonumber(try(self.triggers.ssh_port, "22"))
      user        = try(self.triggers.ssh_user, "www")
      private_key = file(try(self.triggers.ssh_key_path, "/home/www/.ssh/prodavan_tf"))
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
    gitops_rev = "v12-templatefile"
    token_fp   = var.ghcr_token != "" ? substr(sha256(var.ghcr_token), 0, 12) : "none"
    script_sha = filesha256("${path.module}/templates/gitops-bootstrap.sh.tpl")
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
    content     = var.ghcr_token != "" ? var.ghcr_token : ""
    destination = "/tmp/prodavan-ghcr.token"
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/gitops-bootstrap.sh.tpl", {
      kubeconfig_path  = local.kubeconfig_path
      remote_repo_path = var.remote_repo_path
      ghcr_username    = var.ghcr_username
    })
    destination = "/tmp/prodavan-gitops.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "tr -d '\\r' < /tmp/prodavan-gitops.sh > /tmp/prodavan-gitops.lf && mv /tmp/prodavan-gitops.lf /tmp/prodavan-gitops.sh",
      "chmod 700 /tmp/prodavan-gitops.sh",
      "bash /tmp/prodavan-gitops.sh",
    ]
  }
}

output "kubeconfig_path" {
  value = local.kubeconfig_path
}

output "api_endpoint" {
  value = "https://127.0.0.1:${var.api_port}"
}

output "http_url" {
  value = "http://127.0.0.1:${var.http_port}/"
}

output "https_url" {
  value = var.https_port != 0 ? "https://127.0.0.1:${var.https_port}/" : ""
}

output "cluster_name" {
  value = var.cluster_name
}
