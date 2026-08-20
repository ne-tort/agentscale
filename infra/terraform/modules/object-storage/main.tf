variable "bucket_name" {
  type = string
}

resource "null_resource" "object_storage_placeholder" {
  triggers = {
    bucket_name = var.bucket_name
  }
}

output "bucket_name" {
  value = var.bucket_name
}

output "endpoint" {
  value = ""
}
