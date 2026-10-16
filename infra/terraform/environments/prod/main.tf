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

# ---------------------------------------------------------------------------
# PROD server: выделенный хост, только prod-контур (namespace agentscale*).
# Отличия от environments/vm (dev):
#  - только prod Argo-приложения (gitops_apps = "prod": projects + agent-sandbox
#    + agentscale-prod; dev/e2e на этот хост не попадают);
#  - чистые порты: Traefik web=80, websecure=443 (https://185.103.100.124/);
#  - без CI-раннеров (runner_kubeconfig_path пуст);
#  - ssh на нестандартном порту 44345.
# ---------------------------------------------------------------------------

variable "cluster_name" {
  type    = string
  default = "agentscale-prod"
}

variable "http_port" {
  type        = number
  default     = 80
  description = "Traefik web host port (80 = чистый http://185.103.100.124/)."
}

variable "https_port" {
  type    = number
  default = 443
}

variable "https_tls_sans" {
  type        = list(string)
  default     = ["IP:185.103.100.124", "IP:127.0.0.1", "DNS:localhost"]
  description = "SANs листового сертификата websecure (локальный CA; CA экспортируется для доверия в браузере)."
}

variable "ssh_host" {
  type    = string
  default = "185.103.100.124"
}

variable "ssh_port" {
  type    = number
  default = 44345
}

variable "ssh_user" {
  type    = string
  default = "vzlet-ai"
}

variable "ssh_private_key_path" {
  type        = string
  description = "Приватный ключ SSH (на машине, где запускается terraform)."
  default     = "~/.ssh/myserver_key"
}

variable "remote_repo_path" {
  type        = string
  description = "Клон agentscale на сервере."
  default     = "/home/vzlet-ai/git/agentscale"
}

variable "api_tls_sans" {
  type        = list(string)
  default     = ["127.0.0.1", "185.103.100.124"]
  description = "k3s API cert SANs: localhost для kubectl на хосте + публичный IP."
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

module "k3s_prod" {
  source = "../../modules/k3s-dev-host"

  host_profile           = "vm"
  cluster_name           = var.cluster_name
  http_port              = var.http_port
  https_port             = var.https_port
  https_tls_sans         = var.https_tls_sans
  k3s_tls_sans           = var.api_tls_sans
  kubeconfig_path        = "/home/vzlet-ai/.kube/prodavan-prod.yaml"
  runner_kubeconfig_path = ""
  ssh_host               = var.ssh_host
  ssh_port               = var.ssh_port
  ssh_user               = var.ssh_user
  ssh_private_key_path   = pathexpand(var.ssh_private_key_path)
  remote_repo_path       = var.remote_repo_path
  bootstrap_gitops       = var.bootstrap_gitops
  gitops_apps            = "prod"
  ghcr_token             = var.ghcr_token
  ghcr_username          = var.ghcr_username
}

locals {
  api_host = element(var.api_tls_sans, length(var.api_tls_sans) - 1)
}

output "kubeconfig_path" {
  value = module.k3s_prod.kubeconfig_path
}

output "http_url" {
  value = "http://${local.api_host}/"
}

output "https_url" {
  value = "https://${local.api_host}/"
}

output "nodeport_url" {
  value = "http://${local.api_host}:30090/"
}

output "api_endpoint" {
  value = "https://${local.api_host}:6443"
}

output "bootstrap_hint" {
  value = "export KUBECONFIG=${module.k3s_prod.kubeconfig_path} && cd ${var.remote_repo_path}/infra/ops && poetry run prodavan-ops smoke --addr 127.0.0.1 --port ${var.http_port}"
}
