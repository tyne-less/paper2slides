locals {
  services = [
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ]

  # Settings shared by the API and the worker on the free-tier deployment.
  common_env = {
    P2S_STORAGE_BACKEND  = "postgres"
    P2S_MAX_UPLOAD_BYTES = "10485760" # 10 MB: PDFs and results are stored in Neon (1 GB free)
    P2S_LOG_LEVEL        = "INFO"
  }
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# --- Images -----------------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = "p2s"
  format        = "DOCKER"
  location      = var.region

  # Artifact Registry includes 0.5 GB of free storage; keep only the latest images.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-latest-2"
    action = "KEEP"
    most_recent_versions {
      keep_count = 2
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- Secret: Neon connection string ------------------------------------------------------
# Terraform creates the secret but no version. Add the value by hand (README_DEPLOY.md), so
# it never appears in Terraform state or in this repository.

resource "google_secret_manager_secret" "database_url" {
  secret_id = "p2s-database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

# --- Service accounts --------------------------------------------------------------------

resource "google_service_account" "api" {
  account_id   = "p2s-api"
  display_name = "paper2slides API (Cloud Run service)"
}

resource "google_service_account" "worker" {
  account_id   = "p2s-worker"
  display_name = "paper2slides worker and migrations (Cloud Run jobs)"
}

resource "google_service_account" "scheduler" {
  account_id   = "p2s-scheduler"
  display_name = "Cloud Scheduler: starts worker executions"
}

resource "google_secret_manager_secret_iam_member" "database_url_readers" {
  for_each  = { api = google_service_account.api.email, worker = google_service_account.worker.email }
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

# --- API: Cloud Run service --------------------------------------------------------------

resource "google_cloud_run_v2_service" "api" {
  name                = "p2s-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        # Request-based billing: CPU is only allocated (and billed) while handling requests.
        cpu_idle = true
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "P2S_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      # The default TCP startup probe is kept on purpose: an HTTP probe on /health would
      # query Postgres and wake the Neon compute on every instance start.
    }
  }

  lifecycle {
    # deploy.sh and the deploy workflow roll out new images.
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_secret_manager_secret_iam_member.database_url_readers]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.api_public ? 1 : 0
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Worker and migrations: Cloud Run jobs ------------------------------------------------

resource "google_cloud_run_v2_job" "worker" {
  name                = "p2s-worker"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      # Shorter than the 15-minute schedule; on timeout Cloud Run sends SIGTERM and the worker
      # releases an unfinished job back to the queue (shutdown_grace_seconds).
      timeout = "540s"

      containers {
        image   = var.image
        command = ["python", "-m", "p2s_service", "worker", "--drain"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        dynamic "env" {
          for_each = merge(local.common_env, {
            P2S_WORKER_METRICS_PORT = "0" # nothing scrapes a short-lived job
          })
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "P2S_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_secret_manager_secret_iam_member.database_url_readers]
}

resource "google_cloud_run_v2_job" "migrate" {
  name                = "p2s-migrate"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      timeout         = "300s"

      containers {
        image   = var.image
        command = ["alembic", "upgrade", "head"]

        env {
          name = "P2S_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_secret_manager_secret_iam_member.database_url_readers]
}

# --- Schedule: one worker execution per interval -----------------------------------------

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_worker" {
  name     = google_cloud_run_v2_job.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "worker" {
  name             = "p2s-worker"
  region           = var.region
  schedule         = var.worker_schedule
  time_zone        = "Etc/UTC"
  attempt_deadline = "60s"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_runs_worker]
}
