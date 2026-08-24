terraform {
  required_version = ">= 1.5.0"

  backend "local" {
    path = "terraform.tfstate"
  }
}

variable "cluster_name" {
  type    = string
  default = "prodavan-dev"
}

variable "http_port" {
  type    = number
  default = 8088
}

variable "kubeconfig_path" {
  type    = string
  default = ""
}

variable "connection_type" {
  type        = string
  description = "local (Terraform runs ensure on same host) | ssh (Windows Terraform → WSL sshd)"
  default     = "ssh"
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
  type    = string
  default = ""
}

variable "remote_repo_path" {
  type        = string
  description = "Prodavan checkout on WSL (e.g. /mnt/c/Users/<you>/git/Commerce/prodavan). Empty → derive from module path when connection_type=local only."
  default     = ""
}

variable "bootstrap_gitops" {
  type        = bool
  description = "After k3d is up, run bootstrap_gitops.sh (images + Argo + smoke). Needs GHCR_TOKEN in env for private images."
  default     = true
}

locals {
  # Prefer TF_VAR_remote_repo_path. local-exec can derive checkout; SSH needs a WSL path.
  remote_repo_path = var.remote_repo_path != "" ? var.remote_repo_path : (
    var.connection_type == "local"
    ? abspath("${path.module}/../../..")
    : "/mnt/c/Users/qwerty/git/Commerce/prodavan"
  )
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : (
    var.connection_type == "ssh"
    ? "${local.remote_repo_path}/infra/.kube/prodavan-k3d.yaml"
    : abspath("${path.module}/../../../.kube/prodavan-k3d.yaml")
  )
  ssh_private_key_path = var.ssh_private_key_path != "" ? var.ssh_private_key_path : abspath("${path.module}/../../../.ssh/prodavan_tf")
}

module "k3s_local" {
  source = "../../modules/k3s-local"

  cluster_name         = var.cluster_name
  http_port            = var.http_port
  kubeconfig_path      = local.kubeconfig_path
  connection_type      = var.connection_type
  ssh_host             = var.ssh_host
  ssh_port             = var.ssh_port
  ssh_user             = var.ssh_user
  ssh_private_key_path = local.ssh_private_key_path
  remote_repo_path     = local.remote_repo_path
}

# GitOps layer: images → Argo → wait → smoke (idempotent scripts).
resource "terraform_data" "gitops_local" {
  count = var.bootstrap_gitops && var.connection_type == "local" ? 1 : 0

  input = {
    cluster = var.cluster_name
    # Bump to re-run: terraform apply -replace=terraform_data.gitops_local[0]
    rev     = "gitops-v5-graceful-brokers"
  }

  depends_on = [module.k3s_local]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      KUBECONFIG           = local.kubeconfig_path
      K3D_CLUSTER          = var.cluster_name
      FROM_TERRAFORM       = "1"
      HTTP_PORT            = tostring(var.http_port)
      SEED_UI              = "1"
      BUILD_LOCAL_IMAGES   = "1"
    }
    command = "bash '${local.remote_repo_path}/infra/scripts/from_scratch_local.sh'"
  }
}

resource "terraform_data" "gitops_ssh" {
  count = var.bootstrap_gitops && var.connection_type == "ssh" ? 1 : 0

  input = {
    cluster = var.cluster_name
    rev     = "gitops-v5-graceful-brokers"
  }

  depends_on = [module.k3s_local]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    port        = var.ssh_port
    user        = var.ssh_user
    private_key = file(local.ssh_private_key_path)
    timeout     = "15m"
  }

  provisioner "remote-exec" {
    inline = [
      "set -euo pipefail",
      "export PATH=\"$HOME/.local/bin:/usr/sbin:/usr/bin:$PATH\"",
      "export KUBECONFIG='${local.kubeconfig_path}'",
      "export K3D_CLUSTER='${var.cluster_name}'",
      "export HTTP_PORT='${var.http_port}'",
      "export FROM_TERRAFORM=1",
      "export SEED_UI=1",
      "export BUILD_LOCAL_IMAGES=1",
      "bash '${local.remote_repo_path}/infra/scripts/from_scratch_local.sh'",
    ]
  }
}

output "kubeconfig_path" {
  value = module.k3s_local.kubeconfig_path
}

output "api_endpoint" {
  value = module.k3s_local.api_endpoint
}

output "http_port" {
  value = module.k3s_local.http_port
}

output "cluster_name" {
  value = module.k3s_local.cluster_name
}

output "connection_type" {
  value = module.k3s_local.connection_type
}

output "smoke_url" {
  value = "http://127.0.0.1:${module.k3s_local.http_port}"
}

output "ssh_target" {
  value = var.connection_type == "ssh" ? "${var.ssh_user}@${var.ssh_host}:${var.ssh_port}" : "(local-exec)"
}

output "bootstrap_gitops" {
  value = var.bootstrap_gitops
}

output "next_ui_seed" {
  value = "bash infra/scripts/seed_dev_identity.sh"
}
