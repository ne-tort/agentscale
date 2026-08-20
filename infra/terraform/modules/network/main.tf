# VPC / subnets / security groups (cloud primitives for k3s VMs).
# Provider-specific resources added when cloud apply is requested.

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  name = lookup(var.tags, "Name", "prodavan-network")
}

resource "null_resource" "network_placeholder" {
  triggers = {
    vpc_cidr             = var.vpc_cidr
    public_subnet_cidrs  = join(",", var.public_subnet_cidrs)
    private_subnet_cidrs = join(",", var.private_subnet_cidrs)
  }
}

output "vpc_id" {
  value       = "pending-cloud-provider"
  description = "Filled when Yandex/AWS network resources are implemented."
}

output "public_subnet_ids" {
  value = []
}

output "private_subnet_ids" {
  value = []
}

output "default_security_group_id" {
  value = null
}
