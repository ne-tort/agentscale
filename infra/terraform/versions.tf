# Terraform root versions (shared constraints).
# Environments: environments/dev, environments/staging
# Cloud apply is opt-in — validate skeleton only in CI/local.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
