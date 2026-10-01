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

variable "https_port" {
  type        = number
  default     = 8443
  description = "Traefik websecure host port (HTTPS). 0 disables hostPort."
}

variable "ssh_host" {
  type    = string
  default = "127.0.0.1"
}

variable "ssh_port" {
  type    = number
  default = 22
}

variable "ssh_user" {
  type    = string
  default = "www"
}

variable "ssh_private_key_path" {
  type    = string
  default = "/home/www/.ssh/prodavan_tf"
}

variable "remote_repo_path" {
  type        = string
  description = "Prodavan checkout on the VM (this repo, cloned where terraform runs)."
  default     = "/home/www/git/agentscale"
}

variable "api_tls_sans" {
  type        = list(string)
  default     = ["127.0.0.1", "172.31.156.203"]
  description = "k3s API cert SANs: localhost for runners/on-host kubectl, VM IP for kubectl from the Windows host."
}

variable "runner_kubeconfig_path" {
  type    = string
  default = "/home/runner/.kube/prodavan-dev.yaml"
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

module "k3s_dev" {
  source = "../../modules/k3s-dev-host"

  host_profile           = "vm"
  cluster_name           = var.cluster_name
  http_port              = var.http_port
  https_port             = var.https_port
  k3s_tls_sans           = var.api_tls_sans
  runner_kubeconfig_path = var.runner_kubeconfig_path
  ssh_host               = var.ssh_host
  ssh_port               = var.ssh_port
  ssh_user               = var.ssh_user
  ssh_private_key_path   = var.ssh_private_key_path
  remote_repo_path       = var.remote_repo_path
  bootstrap_gitops       = var.bootstrap_gitops
  ghcr_token             = var.ghcr_token
  ghcr_username          = var.ghcr_username
}

output "kubeconfig_path" {
  value = module.k3s_dev.kubeconfig_path
}

output "http_url" {
  value = "http://${var.api_tls_sans[length(var.api_tls_sans) - 1}:${var.http_port}/"
}

output "https_url" {
  value = var.https_port != 0 ? "https://${var.api_tls_sans[length(var.api_tls_sans) - 1]}:${var.https_port}/" : ""
}

output "api_endpoint" {
  value = "https://${var.api_tls_sans[length(var.api_tls_sans) - 1]}:6443"
}

output "runner_kubeconfig_path" {
  value = var.runner_kubeconfig_path
}

output "bootstrap_hint" {
  value = "export KUBECONFIG=${module.k3s_dev.kubeconfig_path} && cd ${var.remote_repo_path}/infra/ops && poetry run prodavan-ops smoke --addr 127.0.0.1 --port ${var.http_port}"
}
