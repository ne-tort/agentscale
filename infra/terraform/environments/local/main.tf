terraform {
  required_version = ">= 1.5.0"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

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
  description = "ssh — Terraform on Windows/WSL connects to Kali WSL via 127.0.0.1:2222"
  default     = "ssh"

  validation {
    condition     = var.connection_type == "ssh"
    error_message = "Only connection_type=ssh is supported (native k3s on WSL host)."
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
  type    = string
  default = ""
}

variable "remote_repo_path" {
  type        = string
  description = "Prodavan checkout on WSL, e.g. /mnt/c/Users/<you>/git/Commerce/prodavan"
  default     = ""
}

variable "bootstrap_gitops" {
  type    = bool
  default = true
}

variable "ghcr_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "ghcr_username" {
  type    = string
  default = "ne-tort"
}

locals {
  remote_repo_path = var.remote_repo_path != "" ? var.remote_repo_path : "/mnt/c/Users/qwerty/git/Commerce/prodavan"
  ssh_key          = var.ssh_private_key_path != "" ? var.ssh_private_key_path : abspath("${path.module}/../../../.ssh/prodavan_tf")
}

module "k3s_dev" {
  source = "../../modules/k3s-dev-host"

  cluster_name           = var.cluster_name
  http_port              = var.http_port
  kubeconfig_path        = var.kubeconfig_path
  ssh_host               = var.ssh_host
  ssh_port               = var.ssh_port
  ssh_user               = var.ssh_user
  ssh_private_key_path   = local.ssh_key
  remote_repo_path       = local.remote_repo_path
  bootstrap_gitops       = var.bootstrap_gitops
  ghcr_token             = var.ghcr_token
  ghcr_username          = var.ghcr_username
}

output "kubeconfig_path" {
  value = module.k3s_dev.kubeconfig_path
}

output "http_url" {
  value = module.k3s_dev.http_url
}

output "api_endpoint" {
  value = module.k3s_dev.api_endpoint
}

output "bootstrap_hint" {
  value = "export KUBECONFIG=${module.k3s_dev.kubeconfig_path} && cd ${local.remote_repo_path}/infra/ops && poetry run prodavan-ops smoke --addr 127.0.0.1 --port ${var.http_port}"
}
