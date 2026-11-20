output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "deployer_service_account" {
  value = local.github ? google_service_account.deployer[0].email : null
}

output "workload_identity_provider" {
  value = local.github ? google_iam_workload_identity_pool_provider.github[0].name : null
}
