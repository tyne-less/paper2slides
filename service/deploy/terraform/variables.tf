variable "project_id" {
  description = "GCP project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Cloud Scheduler. us-central1 keeps traffic to a US Neon region inside the free North America egress allowance (README_DEPLOY.md)."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image for the API, worker and migration job, e.g. REGION-docker.pkg.dev/PROJECT/p2s/p2s-service:TAG. After the first apply, image updates are done by deploy.sh or the deploy workflow, not by Terraform."
  type        = string
}

variable "worker_schedule" {
  description = "Cron schedule (UTC) on which Cloud Scheduler starts one worker execution. See README_DEPLOY.md for how the interval affects the free tiers."
  type        = string
  default     = "*/15 * * * *"
}

variable "api_public" {
  description = "Allow unauthenticated calls to the API. Off by default: a public upload endpoint can fill the 1 GB Neon free storage."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository (owner/name) allowed to deploy through Workload Identity Federation. Empty string: no federation is created."
  type        = string
  default     = ""
}
