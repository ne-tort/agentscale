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
  # Версия provisioner-контракта k3s/Argo. Меняется, когда меняется смысл
  # provisioner'ов (не косметика) — это заставляет пересоздать k3s_server и
  # gitops_bootstrap. Одна переменная на оба ресурса, чтобы они не разъехались.
  k3s_rev        = "v14-clean-install"
  kctl           = "sudo -n /usr/local/bin/k3s kubectl"
  tls_san_flags  = join(" ", [for s in var.k3s_tls_sans : "--tls-san=${s}"])
  https_tls_sans = join(",", var.https_tls_sans)
  ssh_home       = "/home/${var.ssh_user}"
  # /mnt/c/Users/<user>/git/.../agentscale → /mnt/c/Users/<user>/.kube/prodavan-dev.yaml
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
      "mkdir -p /home/${var.ssh_user}/.ssh/sshd-agentscale",
      "test -f /home/${var.ssh_user}/.ssh/sshd-agentscale/host_ed25519 || ssh-keygen -t ed25519 -N \"\" -f /home/${var.ssh_user}/.ssh/sshd-agentscale/host_ed25519",
      "mv /tmp/prodavan-sshd_config /home/${var.ssh_user}/.ssh/sshd-agentscale/sshd_config",
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
  # host_dns runs first: a cold VM must resolve get.k3s.io, and the host resolver
  # is the thing that broke twice (2026-10-22/29). Destroy order then takes k3s
  # down before the resolver drop-in is removed — the right way round.
  depends_on = [null_resource.sshd, null_resource.host_dns]

  triggers = {
    # k3s_version intentionally NOT in triggers: already-running path skips
    # reinstall; changing the var alone must not churn SSH provisioners.
    # v9: host_profile param — vm skips custom sshd and keeps Docker Engine
    # (CI runners on the VM share it), tls-san list, runner kubeconfig.
    # v10: https_tls_sans — local CA + leaf as Traefik default cert (websecure).
    # v11: host sysctl unprivileged_port_start; v12: two contours (agentscale ns)
    # (hostNetwork + NET_BIND_SERVICE cap alone is not enough for userns reasons).
    # v13: DNS is declarative — CoreDNS explicit upstreams + host resolved
    # drop-in; both are re-applied after every k3s start (post-k3s-heal).
    # v14: clean-install contract — force_reinstall wipes a previous k3s before
    # installing, config drift restarts k3s instead of being silently ignored,
    # and the API-readiness wait covers a cold VM (was 2 min → 9 min).
    rev = local.k3s_rev
    # One-shot "install from scratch" switch: while true, the provisioners wipe
    # /var/lib/rancher/k3s (etcd/sqlite, containerd images, local-path PVC data)
    # before installing. Flip to true, apply, flip back to false — the flip back
    # only re-runs idempotent provisioners (k3s keeps running, no reinstall).
    force_reinstall = tostring(var.force_reinstall)
    http_port       = tostring(var.http_port)
    https_port      = tostring(var.https_port)
    cluster         = var.cluster_name
    traefik_tpl     = filesha256("${path.module}/templates/traefik-port.yaml.tpl")
    boot_heal       = filesha256("${path.module}/templates/prodavan-boot-heal.conf.tpl")
    preflight       = filesha256("${path.module}/templates/k3s-preflight.sh.tpl")
    post_heal       = filesha256("${path.module}/templates/post-k3s-heal.sh.tpl")
    tls_tpl         = filesha256("${path.module}/templates/agentscale-tls.sh.tpl")
    # DNS upstreams are rendered into post-k3s-heal.sh; changing them must
    # recreate the provisioners even though the template hash stays the same.
    # (The host resolver is a separate null_resource.host_dns.)
    dns_upstreams = var.dns_upstreams
    # Изменение этих файлов должно пересоздавать provisioner'ы.
    k3s_cfg = filesha256("${path.module}/templates/k3s-config.yaml.tpl")
    guards  = filesha256("${path.module}/../../../k3s/node-manifests/platform-guards.yaml")
    # SSH coords in triggers so provisioners may only use self.*
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/traefik-port.yaml.tpl", {
      http_port  = var.http_port
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
    content     = templatefile("${path.module}/templates/prodavan-boot-heal.conf.tpl", {})
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
      http_port     = var.http_port
      dns_upstreams = var.dns_upstreams
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

  # kubelet: резервы под систему и пороги выселения — чтобы узел выселял поды
  # ДО того, как ядро уйдёт в глобальный OOM (инцидент 2026-10-23: свободно
  # 62 МБ, apiserver перестал отвечать, нода «зависла»).
  provisioner "file" {
    content     = templatefile("${path.module}/templates/k3s-config.yaml.tpl", {})
    destination = "/tmp/prodavan-k3s-config.yaml"
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "10m"
    }
  }

  # Бюджеты памяти для namespace'ов вне GitOps (argocd, kube-system,
  # agent-sandbox-system): k3s применяет всё из server/manifests сам, поэтому
  # объект переживает рестарт и не зависит от ручного kubectl.
  provisioner "file" {
    content     = file("${path.module}/../../../k3s/node-manifests/platform-guards.yaml")
    destination = "/tmp/prodavan-platform-guards.yaml"
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
    content = templatefile("${path.module}/templates/agentscale-tls.sh.tpl", {
      https_tls_sans = local.https_tls_sans
      ssh_home       = local.ssh_home
    })
    destination = "/tmp/agentscale-tls.sh"
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
      # Deterministic resolv-conf for kubelet/CoreDNS (dnsPolicy: Default):
      # direct upstreams rendered from host_dns_servers, rewritten (tmp + sudo
      # mv) whenever the content differs. Pods and the stock Corefile
      # `forward . /etc/resolv.conf` must not depend on host systemd-resolved
      # being alive — a dead stub upstream is exactly the 2026-10-22/29
      # incident. Written BEFORE k3s starts; post-k3s-heal.sh re-pins CoreDNS
      # after every k3s restart.
      var.k3s_resolv_conf == "" || var.host_dns_servers == "" ? "echo 'k3s-resolv-conf: default (unmanaged)'" : "desired=$(printf '# Managed by terraform (k3s-dev-host): direct upstreams, no dependency on host resolved.\\n${join("\\n", [for s in split(" ", var.host_dns_servers) : "nameserver ${s}"])}'); current=$(cat ${var.k3s_resolv_conf} 2>/dev/null || true); if [ \"$current\" != \"$desired\" ]; then echo k3s-resolv-conf: updating ${var.k3s_resolv_conf}; printf '%s\\n' \"$desired\" | sudo -n tee ${var.k3s_resolv_conf}.tmp >/dev/null && sudo -n mv ${var.k3s_resolv_conf}.tmp ${var.k3s_resolv_conf}; else echo k3s-resolv-conf: up-to-date; fi",
      # Non-root pods with hostNetwork (Traefik websecure <1024) cannot bind low
      # ports even with NET_BIND_SERVICE — relax on this dedicated CI/cluster host.
      "sudo -n sh -c 'printf \"net.ipv4.ip_unprivileged_port_start=79\\n\" > /etc/sysctl.d/99-agentscale-unprivileged-ports.conf && sysctl -w net.ipv4.ip_unprivileged_port_start=79 >/dev/null'",
      "sudo -n mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s /etc/systemd/system/k3s.service.d /usr/local/lib/prodavan",
      "sudo -n cp /tmp/prodavan-traefik-port.yaml /var/lib/rancher/k3s/server/manifests/prodavan-traefik-port.yaml",
      "sudo -n cp /tmp/prodavan-platform-guards.yaml /var/lib/rancher/k3s/server/manifests/prodavan-platform-guards.yaml",
      "sudo -n cp /tmp/prodavan-boot-heal.conf /etc/systemd/system/k3s.service.d/prodavan-boot-heal.conf",
      "python3 -c \"from pathlib import Path; p=Path('/tmp/prodavan-k3s-preflight.sh'); Path('/tmp/prodavan-k3s-preflight.lf').write_bytes(p.read_bytes().replace(b'\\r', b''))\"",
      "python3 -c \"from pathlib import Path; p=Path('/tmp/prodavan-post-k3s-heal.sh'); Path('/tmp/prodavan-post-k3s-heal.lf').write_bytes(p.read_bytes().replace(b'\\r', b''))\"",
      "python3 -c \"from pathlib import Path; p=Path('/tmp/agentscale-tls.sh'); Path('/tmp/agentscale-tls.lf').write_bytes(p.read_bytes().replace(b'\\r', b''))\"",
      "sudo -n cp /tmp/prodavan-k3s-preflight.lf /usr/local/lib/prodavan/k3s-preflight.sh",
      "sudo -n cp /tmp/prodavan-post-k3s-heal.lf /usr/local/lib/prodavan/post-k3s-heal.sh",
      "sudo -n chmod 755 /usr/local/lib/prodavan/k3s-preflight.sh /usr/local/lib/prodavan/post-k3s-heal.sh",
      # Clean install: wipe the previous cluster's data dir (etcd/sqlite, containerd
      # images, local-path PVC contents) BEFORE installing. k3s-uninstall.sh is the
      # supported way out; the manual rm is the fallback when the script is gone
      # (e.g. someone deleted /usr/local/bin) so a "from scratch" apply still works.
      "if [ \"${var.force_reinstall}\" = \"true\" ]; then",
      "  echo FORCE-REINSTALL: wiping previous k3s state",
      "  if [ -x /usr/local/bin/k3s-uninstall.sh ]; then sudo -n /usr/local/bin/k3s-uninstall.sh || true; fi",
      "  sudo -n systemctl stop k3s 2>/dev/null || true",
      "  sudo -n systemctl disable k3s 2>/dev/null || true",
      "  sudo -n rm -rf /var/lib/rancher/k3s /etc/rancher/k3s /var/lib/kubelet /var/lib/cni /run/k3s /run/flannel",
      "  sudo -n rm -f /usr/local/bin/k3s /usr/local/bin/k3s-uninstall.sh /usr/local/bin/k3s-killall.sh",
      "  sudo -n rm -f /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env",
      "  sudo -n systemctl daemon-reload",
      "  sudo -n mkdir -p /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s",
      # Manifests were removed with the dirs above — put them back. config.yaml
      # is (re)written by the block right after this one.
      "  sudo -n cp /tmp/prodavan-traefik-port.yaml /var/lib/rancher/k3s/server/manifests/prodavan-traefik-port.yaml",
      "  sudo -n cp /tmp/prodavan-platform-guards.yaml /var/lib/rancher/k3s/server/manifests/prodavan-platform-guards.yaml",
      "fi",
      # Защита от OOM: пороги kubelet'а + бюджеты памяти кластерных namespace.
      # config.yaml читается k3s ДО старта сервера, поэтому кладём его раньше.
      # AFTER the force_reinstall wipe: that block deletes /etc/rancher/k3s, so
      # writing config.yaml before it would be lost on a clean install.
      # Hash before/after: k3s reads config.yaml only at start, so a changed
      # file is silently ignored by a running server (old kubelet-arg drift would
      # live on until the next reboot). Remember the change and restart below.
      "prev_cfg=$(sudo -n sha256sum /etc/rancher/k3s/config.yaml 2>/dev/null | cut -d' ' -f1 || true)",
      "sudo -n cp /tmp/prodavan-k3s-config.yaml /etc/rancher/k3s/config.yaml",
      # kubelet/CoreDNS upstream override. Appended (not templated into the file)
      # so k3s-config.yaml.tpl stays plain YAML for the ops tests. Rewritten from
      # /tmp on every run, so the append never duplicates.
      var.k3s_resolv_conf == "" ? "echo k3s-resolv-conf: default" : "printf 'resolv-conf: %s\\n' '${var.k3s_resolv_conf}' | sudo -n tee -a /etc/rancher/k3s/config.yaml >/dev/null",
      "cfg_changed=0",
      "if [ -n \"$prev_cfg\" ] && [ \"$prev_cfg\" != \"$(sudo -n sha256sum /etc/rancher/k3s/config.yaml | cut -d' ' -f1)\" ]; then cfg_changed=1; fi",
      "sudo -n rm -f /etc/systemd/system/k3s.service.d/agentscale-wsl-stop.conf",
      # WSL only: Docker Engine inside WSL fights k3s CNI. On a vm-profile host
      # Docker stays — CI runners (same VM) build images with it.
      var.host_profile == "wsl" ? "if systemctl list-unit-files docker.service >/dev/null 2>&1; then sudo -n systemctl stop docker.socket docker 2>/dev/null || true; sudo -n systemctl disable --now docker.socket docker 2>/dev/null || true; sudo -n systemctl mask docker.socket docker 2>/dev/null || true; fi" : "echo vm-profile: docker engine left running for CI runners",
      # Broken/unauthenticated Tailscale netmon flaps routes around CNI veths on WSL.
      "if systemctl is-active --quiet tailscaled 2>/dev/null && ! tailscale status >/dev/null 2>&1; then sudo -n systemctl stop tailscaled 2>/dev/null || true; fi",
      "if ! command -v k3s >/dev/null 2>&1; then",
      # Cold VM / slow link: retry the download instead of failing the whole
      # apply on one flaky TLS handshake.
      "  if ! curl -sSfL --retry 5 --retry-delay 3 --retry-connrefused --max-time 300 https://get.k3s.io -o /tmp/agentscale-k3s-install.sh; then echo 'ERROR: не удалось скачать https://get.k3s.io (нет сети/DNS?)' >&2; getent hosts get.k3s.io || echo 'DNS не резолвит get.k3s.io'; exit 1; fi",
      "  INSTALL_K3S_VERSION=\"${var.k3s_version}\" sh /tmp/agentscale-k3s-install.sh server --write-kubeconfig-mode 644 ${local.tls_san_flags}",
      "elif ! sudo -n systemctl is-active --quiet k3s; then",
      "  sudo -n systemctl start k3s",
      "elif [ \"$cfg_changed\" = 1 ]; then",
      "  echo k3s-config-changed-restarting",
      "  sudo -n systemctl restart k3s",
      "else",
      "  echo k3s-already-running-skip-restart",
      "fi",
      "sudo -n systemctl daemon-reload",
      "sudo -n systemctl enable k3s || true",
      "sudo -n bash /tmp/agentscale-tls.lf",
      # Cold VM: first boot pulls the k3s image + all system pods. 120 s was not
      # enough after a wipe (fresh containerd cache), so allow 9 min, then fail
      # loudly with a diagnostic instead of letting `kubectl wait` fail vaguely.
      "ready=0; for i in $(seq 1 270); do if sudo -n k3s kubectl get --raw=/readyz >/dev/null 2>&1; then ready=1; break; fi; sleep 2; done",
      "if [ \"$ready\" != 1 ]; then echo 'ERROR: k3s API /readyz not ready after 540s'; sudo -n systemctl status k3s --no-pager -n 30 || true; sudo -n journalctl -u k3s -n 60 --no-pager || true; exit 1; fi",
      "sudo -n k3s kubectl wait --for=condition=Ready node --all --timeout=300s",
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
    rev          = "v1"
    k3s_id       = null_resource.k3s_server.id
    win_path     = local.windows_kubeconfig_path
    script_sha   = filesha256("${path.module}/templates/export-windows-kubeconfig.sh.tpl")
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
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
#
# Full wipe contract: after this runs the host must look like a machine that never
# ran k3s, because the next apply is a "from scratch" install. k3s-uninstall.sh
# covers /var/lib/rancher/k3s (etcd/sqlite + containerd images + local-path PVC
# data) and /etc/rancher/k3s, but it skips anything it did not create and bails
# out silently when the script is gone (half-installed host). The explicit rm
# below is the belt to that suspenders — rm -rf is idempotent, so a second
# destroy after a partial failure is safe.
resource "null_resource" "k3s_uninstall" {
  depends_on = [null_resource.k3s_server]

  triggers = {
    cluster = var.cluster_name
    # SSH coords in triggers (frozen at create) for the destroy-time connection.
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
    # DELIBERATELY no other keys: adding one forces Terraform to REPLACE this
    # resource, and a replace runs the destroy-time provisioner — i.e. it would
    # uninstall k3s in the middle of an ordinary apply. Paths the provisioner
    # cleans up are derived from ssh_user inside the script instead.
  }

  provisioner "remote-exec" {
    when = destroy
    # Not `on_failure = continue`: a destroy that leaves k3s running would be
    # reported as success and the next apply would install onto a dirty host.
    connection {
      type        = "ssh"
      host        = try(self.triggers.ssh_host, "127.0.0.1")
      port        = tonumber(try(self.triggers.ssh_port, "22"))
      user        = try(self.triggers.ssh_user, "www")
      private_key = file(try(self.triggers.ssh_key_path, "/home/www/.ssh/prodavan_tf"))
      timeout     = "10m"
    }
    inline = [
      "set -eu",
      # 1. Supported uninstall path: stops k3s, removes the unit, the data dir
      #    (incl. containerd images and local-path PVCs), /etc/rancher/k3s.
      "if [ -x /usr/local/bin/k3s-uninstall.sh ]; then sudo -n /usr/local/bin/k3s-uninstall.sh || echo WARN: k3s-uninstall.sh failed, falling through to manual wipe; else echo WARN: k3s-uninstall.sh missing, manual wipe only; fi",
      # 2. Stop/disable whatever is left, then remove every directory the module
      #    (or k3s itself) populated. Nothing here is needed by the host OS.
      "sudo -n systemctl stop k3s 2>/dev/null || true",
      "sudo -n systemctl disable k3s 2>/dev/null || true",
      "sudo -n rm -rf /var/lib/rancher/k3s /etc/rancher/k3s /var/lib/kubelet /var/lib/cni /run/k3s /run/flannel /var/log/pods /var/log/containers",
      "sudo -n rm -f /usr/local/bin/k3s /usr/local/bin/k3s-uninstall.sh /usr/local/bin/k3s-killall.sh",
      # 3. Module-owned host files: boot-heal drop-in + helper scripts, TLS CA
      #    (a new CA is generated on install; the stale one must not linger),
      #    and kubeconfigs that would otherwise point at a dead cluster.
      "sudo -n rm -rf /etc/systemd/system/k3s.service.d /usr/local/lib/prodavan",
      "sudo -n rm -f /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env",
      "sudo -n systemctl daemon-reload || true",
      "sudo -n systemctl reset-failed k3s 2>/dev/null || true",
      # ssh_user is available via self.triggers; the module's default layout is
      # /home/<ssh_user>/.kube/prodavan-dev.yaml + <home>/agentscale-dev-ca.crt
      # and the CI runner copy at /home/runner/.kube/prodavan-dev.yaml.
      # NB: ${self.triggers.ssh_user} — terraform-интерполяция (одинарный $),
      # а $${hm:-...} — наоборот shell-default (двойной $ экранирует $$→$).
      "hm=\"$(getent passwd ${self.triggers.ssh_user} | cut -d: -f6 || true)\"; hm=$${hm:-/home/${self.triggers.ssh_user}}",
      "rm -f \"$hm/.kube/prodavan-dev.yaml\" \"$hm/agentscale-dev-ca.crt\"",
      "if [ -d /home/runner/.kube ]; then sudo -n rm -f /home/runner/.kube/prodavan-dev.yaml; fi",
      # k3s-killall only drops KUBE-/CNI-/flannel rules; leftover DNAT from a
      # previous cluster is a classic "dirty install" symptom.
      "if [ -x /usr/sbin/iptables-save ]; then sudo -n sh -c 'iptables-save | grep -v KUBE- | grep -v CNI- | grep -iv flannel | iptables-restore' || true; fi",
      # Кавычки обязательны: remote-exec = /bin/sh (dash), голые скобки в
      # аргументах echo — «Syntax error: "(" unexpected» на ПОСЛЕДНЕЙ строке,
      # т.е. весь wipe прошёл, а destroy помечен упавшим (и при повторе — снова).
      "echo 'destroy: k3s state wiped (data dir, TLS CA, kubeconfigs, boot-heal)'",
    ]
  }
}

resource "null_resource" "gitops_bootstrap" {
  count = var.bootstrap_gitops ? 1 : 0

  depends_on = [null_resource.k3s_server, null_resource.k3s_uninstall]

  triggers = {
    repo       = var.remote_repo_path
    gitops_rev = local.k3s_rev
    token_fp   = var.ghcr_token != "" ? substr(sha256(var.ghcr_token), 0, 12) : "none"
    script_sha = filesha256("${path.module}/templates/gitops-bootstrap.sh.tpl")
    # Re-bootstrap whenever k3s itself is (re)created — otherwise a clean
    # reinstall (force_reinstall, or any trigger change on k3s_server) leaves
    # the platform un-bootstrapped while this resource still looks "created".
    # NOT null_resource.k3s_server.id: resource references inside triggers are
    # not allowed (and would flake on a known-after-apply value). This is a cheap
    # stand-in that changes exactly when the k3s provisioners re-run.
    k3s_rev = "${local.k3s_rev}:${var.force_reinstall}"
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
      gitops_apps      = var.gitops_apps
    })
    destination = "/tmp/prodavan-gitops.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "tr -d '\\r' < /tmp/prodavan-gitops.sh > /tmp/agentscale-gitops.lf && mv /tmp/agentscale-gitops.lf /tmp/prodavan-gitops.sh",
      "chmod 700 /tmp/prodavan-gitops.sh",
      "bash /tmp/prodavan-gitops.sh",
    ]
  }
}

# Host resolver is part of the same declarative contract: a dead DoT upstream
# brought down pod DNS twice (2026-10-22/23 and 2026-10-29). When configured,
# write a drop-in that overrides any older DoT file and restart resolved.
# Kept in a separate resource so environments that use tunnel DNS (prod) don't
# touch the host resolver at all.
resource "null_resource" "host_dns" {
  count = var.host_dns_servers != "" ? 1 : 0

  triggers = {
    servers    = var.host_dns_servers
    over_tls   = var.host_dns_over_tls
    stub_extra = var.host_dns_stub_listener_extra
    # Содержимое шаблона — часть контракта: правка tpl обязана пересоздать ресурс
    # (иначе уже применённый drop-in молча останется старым).
    tpl_sha      = filesha256("${path.module}/templates/host-dns.conf.tpl")
    ssh_host     = var.ssh_host
    ssh_port     = tostring(var.ssh_port)
    ssh_user     = var.ssh_user
    ssh_key_path = var.ssh_private_key_path
  }

  provisioner "file" {
    content = templatefile("${path.module}/templates/host-dns.conf.tpl", {
      dns_servers  = var.host_dns_servers
      dns_over_tls = var.host_dns_over_tls
      stub_extra   = var.host_dns_stub_listener_extra
    })
    destination = "/tmp/prodavan-host-dns.conf"
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "5m"
    }
  }

  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = self.triggers.ssh_host
      port        = tonumber(self.triggers.ssh_port)
      user        = self.triggers.ssh_user
      private_key = file(self.triggers.ssh_key_path)
      timeout     = "5m"
    }
    inline = [
      "set -eu",
      "sudo -n install -d /etc/systemd/resolved.conf.d",
      # Legacy 99-dot.conf — ручной артефакт инцидента 2026-10-29 (DNS=1.1.1.1
      # 9.9.9.9). systemd НАКАПЛИВАЕТ list-опции из всех drop-in: его 1.1.1.1
      # (rate-limited с этой сети) встал бы ПЕРЕД управляемыми upstream. Файл
      # заменён управляемым drop-in ниже; удаление идемпотентно.
      "sudo -n rm -f /etc/systemd/resolved.conf.d/99-dot.conf",
      # 99-zz sorts after any other drop-in: our values win. The template also
      # starts with an empty DNS= assignment, which resets any list accumulated
      # from other drop-ins before setting ours.
      "sudo -n cp /tmp/prodavan-host-dns.conf /etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf",
      "sudo -n systemctl restart systemd-resolved",
      # Штатная схема Ubuntu 24.04: host-процессы → 127.0.0.53 (stub) → resolved.
      # Статический /etc/resolv.conf (единственный nameserver=stub IP VM) — точка
      # отказа хостового резолва: при смерти resolved DNS терял весь хост.
      # Нормализуем в симлинк на stub-resolv.conf; уже-симлинк не трогаем.
      "if [ ! -L /etc/resolv.conf ]; then sudo -n ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf; fi",
      "ok=0; for i in $(seq 1 20); do if getent hosts github.com >/dev/null 2>&1; then ok=1; break; fi; sleep 1; done",
      "if [ \"$ok\" != 1 ]; then echo 'ERROR: host DNS drop-in applied, but github.com does not resolve' >&2; resolvectl status 2>/dev/null | head -20 || true; exit 1; fi",
    ]
  }

  provisioner "remote-exec" {
    when = destroy
    # Явно fail (а не дефолт и НЕ `continue`): упавший destroy не должен
    # рапортовать успех. Код внутри идемпотентен и не падает на уже
    # удалённом drop-in, так что повтор destroy безопасен.
    on_failure = fail
    connection {
      type        = "ssh"
      host        = try(self.triggers.ssh_host, "127.0.0.1")
      port        = tonumber(try(self.triggers.ssh_port, "22"))
      user        = try(self.triggers.ssh_user, "www")
      private_key = file(try(self.triggers.ssh_key_path, "/home/www/.ssh/prodavan_tf"))
      timeout     = "5m"
    }
    inline = [
      "set -eu",
      # Managed drop-in only. Legacy 99-dot.conf удалён ещё на create (заменён
      # управляемым drop-in) и здесь НЕ восстанавливается: до следующего apply
      # у хоста нет управляемых upstream — apply должен идти сразу за destroy
      # (см. README «Схема DNS»). Restart is skipped when the file is already
      # gone (repeat destroy).
      "if [ -f /etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf ]; then sudo -n rm -f /etc/systemd/resolved.conf.d/99-zz-agentscale-dns.conf; sudo -n systemctl restart systemd-resolved || true; else echo host-dns: drop-in already absent; fi",
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
