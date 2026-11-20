terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }

  # State is local (terraform.tfstate in this directory, git-ignored). It contains no secret
  # values: the database URL is added to Secret Manager by hand, not through Terraform.
}

provider "google" {
  project = var.project_id
  region  = var.region
}
