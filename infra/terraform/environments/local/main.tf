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
  description = "Prodavan checkout on WSL (Windows path mirrored as /mnt/c/...)."
  default     = "/mnt/c/Users/qwerty/git/Commerce/prodavan"
}

locals {
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : (
    var.connection_type == "ssh"
    ? "${var.remote_repo_path}/infra/.kube/prodavan-k3d.yaml"
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
  remote_repo_path     = var.remote_repo_path
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
