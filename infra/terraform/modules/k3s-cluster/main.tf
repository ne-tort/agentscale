# VM pool for k3s (server + agents). Not managed Kubernetes.
# cloud-init install of k3s is added at apply time.

variable "cluster_name" {
  type = string
}

variable "server_count" {
  type    = number
  default = 1
}

variable "agent_count" {
  type    = number
  default = 0
}

variable "instance_type" {
  type    = string
  default = "standard-v3"
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "ssh_key_name" {
  type    = string
  default = ""
}

variable "k3s_version" {
  type    = string
  default = "v1.29.5+k3s1"
}

resource "null_resource" "k3s_cluster_placeholder" {
  triggers = {
    cluster_name = var.cluster_name
    server_count = tostring(var.server_count)
    agent_count  = tostring(var.agent_count)
    k3s_version  = var.k3s_version
  }
}

output "kubeconfig" {
  value       = ""
  sensitive   = true
  description = "Populated after VM bootstrap."
}

output "server_ips" {
  value = []
}

output "api_endpoint" {
  value = ""
}
