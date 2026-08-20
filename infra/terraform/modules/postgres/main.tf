variable "instance_name" {
  type    = string
  default = "prodavan-pg"
}

variable "disk_gb" {
  type    = number
  default = 20
}

resource "null_resource" "postgres_placeholder" {
  triggers = {
    instance_name = var.instance_name
    disk_gb       = tostring(var.disk_gb)
  }
}

output "connection_host" {
  value = ""
}

output "connection_port" {
  value = 5432
}
