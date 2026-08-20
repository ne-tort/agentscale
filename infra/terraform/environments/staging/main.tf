# Copy of dev wiring for staging sizing defaults.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

variable "env" {
  type    = string
  default = "staging"
}

variable "worker_node_count" {
  type    = number
  default = 1
}

variable "k3s_instance_type" {
  type    = string
  default = "standard-v3"
}

variable "ssh_key_name" {
  type    = string
  default = ""
}

locals {
  common_tags = {
    Project = "prodavan"
    Env     = var.env
  }
}

module "network" {
  source               = "../../modules/network"
  vpc_cidr             = "10.1.0.0/16"
  public_subnet_cidrs  = ["10.1.1.0/24", "10.1.2.0/24"]
  private_subnet_cidrs = ["10.1.10.0/24", "10.1.11.0/24"]
  tags                 = local.common_tags
}

module "k3s" {
  source        = "../../modules/k3s-cluster"
  cluster_name  = "prodavan-${var.env}"
  server_count  = 1
  agent_count   = var.worker_node_count
  instance_type = var.k3s_instance_type
  subnet_ids    = module.network.private_subnet_ids
  ssh_key_name  = var.ssh_key_name
}

module "postgres" {
  source        = "../../modules/postgres"
  instance_name = "prodavan-${var.env}-pg"
  disk_gb       = 50
}

module "object_storage" {
  source      = "../../modules/object-storage"
  bucket_name = "prodavan-${var.env}-artifacts"
}
