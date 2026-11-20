# Keyless deploys from GitHub Actions (Workload Identity Federation). Created only when
# var.github_repository is set; the deploy workflow then needs no service-account key.

locals {
  github = var.github_repository != ""
}

resource "google_service_account" "deployer" {
  count        = local.github ? 1 : 0
  account_id   = "p2s-deployer"
  display_name = "GitHub Actions deployer"
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = local.github ? 1 : 0
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = local.github ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  count              = local.github ? 1 : 0
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = local.github ? toset(["roles/run.developer", "roles/artifactregistry.writer"]) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer[0].email}"
}

# Deploying a revision that runs as these service accounts requires actAs on them.
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each = local.github ? {
    api    = google_service_account.api.name
    worker = google_service_account.worker.name
  } : {}
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer[0].email}"
}
