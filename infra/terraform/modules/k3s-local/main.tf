# Local k3d cluster metadata only — no local-exec / remote-exec shell.
# Create cluster declaratively once:
#   k3d cluster create --config infra/k3d/prodavan-dev.yaml
# Reboot: k3d server containers use Docker restart unless-stopped.

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
  description = "Expected kubeconfig path (written by k3d, not Terraform)."
}

variable "connection_type" {
  type        = string
  description = "Informational only (local | ssh)."
  default     = "local"
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
  type    = string
  default = ""
}

variable "k3d_config_path" {
  type        = string
  description = "Path to declarative k3d Simple config."
  default     = ""
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

output "k3d_config_path" {
  value = var.k3d_config_path != "" ? var.k3d_config_path : "${path.module}/../../../k3d/prodavan-dev.yaml"
}

output "bootstrap_hint" {
  value = "k3d cluster create --config <repo>/infra/k3d/prodavan-dev.yaml && kubectl apply -k <repo>/infra/argocd/install && kubectl apply -k <repo>/infra/argocd/apps"
}
