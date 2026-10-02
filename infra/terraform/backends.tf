# Remote state — configure before real apply (Yandex Object Storage / S3).
# Uncomment and fill when provisioning cloud state.
#
# terraform {
#   backend "s3" {
#     bucket = "agentscale-terraform-state"
#     key    = "env/placeholder/terraform.tfstate"
#     region = "ru-central1"
#     endpoints = {
#       s3 = "https://storage.yandexcloud.net"
#     }
#     skip_region_validation      = true
#     skip_credentials_validation = true
#     skip_requesting_account_id  = true
#   }
# }

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
