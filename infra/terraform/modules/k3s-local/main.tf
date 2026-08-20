# Local single-node k3s via k3d (Docker Desktop / WSL).
# Runtime recover after reboot: infra/scripts/ensure_k3d_cluster.sh
# This module records desired params and invokes the same ensure script.

variable "cluster_name" {
  type    = string
  default = "prodavan-dev"
}

variable "k3s_version" {
  type    = string
  default = "v1.29.5-k3s1"
}

variable "http_port" {
  type        = number
  default     = 8088
  description = "Host port mapped to Traefik/ingress HTTP (avoid compose :8080)."
}

variable "https_port" {
  type    = number
  default = 8443
}

variable "api_port" {
  type        = number
  default     = 6443
  description = "Host port for Kubernetes API."
}

variable "kubeconfig_path" {
  type        = string
  description = "Absolute path where kubeconfig is written for CI/runner."
}

variable "ensure_script" {
  type        = string
  description = "Path to ensure_k3d_cluster.sh"
  default     = ""
}

locals {
  ensure_script = var.ensure_script != "" ? var.ensure_script : "${path.module}/../../../scripts/ensure_k3d_cluster.sh"
}

resource "terraform_data" "k3d_cluster" {
  input = {
    cluster_name = var.cluster_name
    k3s_version  = var.k3s_version
    http_port    = tostring(var.http_port)
    https_port   = tostring(var.https_port)
    api_port     = tostring(var.api_port)
    kubeconfig   = var.kubeconfig_path
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      K3D_CLUSTER     = var.cluster_name
      K3S_VERSION     = var.k3s_version
      HTTP_PORT       = tostring(var.http_port)
      HTTPS_PORT      = tostring(var.https_port)
      API_PORT        = tostring(var.api_port)
      KUBECONFIG_OUT  = var.kubeconfig_path
    }
    command = "bash '${local.ensure_script}'"
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
