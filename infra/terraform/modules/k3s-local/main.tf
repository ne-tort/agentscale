# Local single-node k3s via k3d.
# Provisioning modes:
#   local — local-exec on the machine running Terraform
#   ssh   — remote-exec over SSH (typical: Terraform on Windows → WSL sshd)

variable "cluster_name" {
  type    = string
  default = "prodavan-dev"
}

variable "k3s_version" {
  type    = string
  default = "v1.29.5-k3s1"
}

variable "http_port" {
  type    = number
  default = 8088
}

variable "https_port" {
  type    = number
  default = 8443
}

variable "api_port" {
  type    = number
  default = 6443
}

variable "kubeconfig_path" {
  type        = string
  description = "Path where kubeconfig is written on the target host."
}

variable "connection_type" {
  type        = string
  description = "local | ssh"
  default     = "local"
  validation {
    condition     = contains(["local", "ssh"], var.connection_type)
    error_message = "connection_type must be local or ssh."
  }
}

variable "ssh_host" {
  type    = string
  default = "127.0.0.1"
}

variable "ssh_port" {
  type    = number
  default = 2222
}

variable "ssh_user" {
  type    = string
  default = "www"
}

variable "ssh_private_key_path" {
  type        = string
  description = "Private key for Terraform → target host (ed25519)."
  default     = ""
}

variable "remote_repo_path" {
  type        = string
  description = "Absolute path to prodavan checkout on the SSH target."
  default     = ""
}

variable "ensure_script" {
  type    = string
  default = ""
}

locals {
  ensure_script = var.ensure_script != "" ? var.ensure_script : "${path.module}/../../../scripts/ensure_k3d_cluster.sh"
  use_ssh       = var.connection_type == "ssh"
  use_local     = var.connection_type == "local"
  trigger = {
    cluster_name = var.cluster_name
    k3s_version  = var.k3s_version
    http_port    = tostring(var.http_port)
    https_port   = tostring(var.https_port)
    api_port     = tostring(var.api_port)
    kubeconfig   = var.kubeconfig_path
    mode         = var.connection_type
  }
}

resource "terraform_data" "k3d_cluster_local" {
  count = local.use_local ? 1 : 0

  input = local.trigger

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      K3D_CLUSTER    = var.cluster_name
      K3S_VERSION    = var.k3s_version
      HTTP_PORT      = tostring(var.http_port)
      HTTPS_PORT     = tostring(var.https_port)
      API_PORT       = tostring(var.api_port)
      KUBECONFIG_OUT = var.kubeconfig_path
    }
    command = "bash '${local.ensure_script}'"
  }
}

resource "terraform_data" "k3d_cluster_ssh" {
  count = local.use_ssh ? 1 : 0

  input = local.trigger

  connection {
    type        = "ssh"
    host        = var.ssh_host
    port        = var.ssh_port
    user        = var.ssh_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "5m"
  }

  provisioner "remote-exec" {
    inline = [
      "set -euo pipefail",
      "export PATH=\"$HOME/.local/bin:/usr/sbin:/usr/bin:$PATH\"",
      "export K3D_CLUSTER='${var.cluster_name}'",
      "export K3S_VERSION='${var.k3s_version}'",
      "export HTTP_PORT='${var.http_port}'",
      "export HTTPS_PORT='${var.https_port}'",
      "export API_PORT='${var.api_port}'",
      "export KUBECONFIG_OUT='${var.kubeconfig_path}'",
      "test -d '${var.remote_repo_path}'",
      "bash '${var.remote_repo_path}/infra/scripts/ensure_k3d_cluster.sh'",
    ]
  }
}

output "cluster_name" {
  value = var.cluster_name
}

output "kubeconfig_path" {
  value = var.kubeconfig_path
}

output "http_port" {
  value = var.http_port
}

output "api_endpoint" {
  value = "https://127.0.0.1:${var.api_port}"
}

output "connection_type" {
  value = var.connection_type
}
