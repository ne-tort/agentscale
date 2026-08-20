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
  type        = string
  description = "Host path for kubeconfig (mounted into github-runner)."
  default     = ""
}

locals {
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : abspath("${path.module}/../../../.kube/prodavan-k3d.yaml")
}

module "k3s_local" {
  source = "../../modules/k3s-local"

  cluster_name    = var.cluster_name
  http_port       = var.http_port
  kubeconfig_path = local.kubeconfig_path
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

output "smoke_url" {
  value = "http://127.0.0.1:${module.k3s_local.http_port}"
}
