# Deploying to GCP (Cloud Run + Neon), intended to stay within free tiers

**Status: deployed once on 2026-11-20** (us-central1, Neon `aws-us-east-2`, Postgres 16, compute
fixed at 0.25 CU), with `deploy.sh` and image `p2s-service:3142d22a95db`. `terraform validate`
also runs in CI.

What was checked after that deploy:

- The API answered only with an identity token (`gcloud auth print-identity-token`); without
  one, `GET /docs` and `POST /jobs` returned 403. `/metrics` reported `p2s_metrics_db_up 1`, so the Secret Manager
  connection string and the migrations worked.
- One paper (Findings ACL 2024 #164) uploaded → `gcloud run jobs execute p2s-worker --wait` →
  job `succeeded` on attempt 1, 9 slides, Beamer file downloaded through the API. The
  `x-request-id` sent with the upload appeared in the worker's log entry in Cloud Logging.
- That worker execution took 1 min 36 s wall time as reported by `gcloud`, most of it before the
  container started. Inside the attempt: process_start 655 ms (the forkserver starts once per
  execution), pipeline 885 ms, object download 216 ms and upload 634 ms (Postgres storage over
  the network to Neon), job completion 181 ms.
- The pushed image is 128.2 MB compressed (linux/amd64), so the two kept versions fit in the
  0.5 GB Artifact Registry free storage.
- Cloud Scheduler job is `ENABLED` with `*/15 * * * *`.

Found in the cloud and fixed in a second deploy the same day (image `41befd5d3323`):
`/healthz` never reached the app because Cloud Run reserves paths ending in `z`; the endpoint
is now `/health` (200 after the redeploy). Worker IDs in the audit trail were `localhost-1` for
every execution; they now use the Cloud Run execution name.

Scheduled run, after the second deploy: a second paper (Findings ACL 2024 #244) was submitted
at 12:40:05 UTC and nothing was started by hand. The 12:45 Cloud Scheduler execution claimed it
at 12:45:34 as `worker:p2s-worker-w5n88-1` and finished it at 12:45:36 on attempt 1 (13 slides).
An earlier scheduled execution at 12:30, with an empty queue, ran 50 s and exited successfully.

Not yet observed: a full month of billing and Neon CU-hour use under the 15-minute schedule.

## Architecture

| Piece | Where | Notes |
|---|---|---|
| API | Cloud Run service `p2s-api` | min 0 / max 1 instance, request-based billing, 1 vCPU / 512 MiB. Not public by default (`api_public = false`). |
| Worker | Cloud Run job `p2s-worker` | `python -m p2s_service worker --drain`: reap expired leases, process every ready job, exit. Task timeout 540 s. |
| Trigger | Cloud Scheduler job `p2s-worker` | Starts one worker execution per `worker_schedule` (default every 15 minutes; see the cost section). |
| Migrations | Cloud Run job `p2s-migrate` | `alembic upgrade head`, run by `deploy.sh` before each rollout. |
| Database and object storage | Neon free Postgres | Jobs and results, plus PDFs and result files in the `stored_objects` table (`P2S_STORAGE_BACKEND=postgres`). Uploads limited to 10 MB. |
| Secret | Secret Manager `p2s-database-url` | Terraform creates an empty secret; you add the connection string by hand. |
| Images | Artifact Registry `p2s` | Cleanup policy keeps the two most recent images. |
| Deploy identity | Workload Identity Federation (optional) | Only if `github_repository` is set; lets `.github/workflows/deploy.yml` deploy without a key. |

Locally nothing changes: `docker compose` runs the API, a polling worker, Postgres and the
S3-compatible store. Storage is chosen by `P2S_STORAGE_BACKEND` (`s3` locally, `postgres` in
the cloud), and the worker mode by the `--drain` flag.

The pipeline is rule-based and calls no language model, so there is no paid API call to switch off.

## Free-tier check (figures read on 2026-11-20)

Sources: [Cloud Run pricing](https://cloud.google.com/run/pricing),
[Cloud Scheduler pricing](https://cloud.google.com/scheduler/pricing),
[Secret Manager pricing](https://cloud.google.com/secret-manager/pricing),
[Neon plans](https://neon.com/docs/introduction/plans). Free tiers are per billing account
and change over time, so re-check them, and set a budget alert before deploying.

| Free allowance | Limit | This deployment |
|---|---|---|
| Cloud Run jobs CPU | 240,000 vCPU-s / month; **each job instance is billed for at least 1 minute** | 1 vCPU × 60 s × executions per month |
| Cloud Run jobs memory | 450,000 GiB-s / month | 0.5 GiB × 60 s × executions |
| Cloud Run services (request-based) | 180,000 vCPU-s, 360,000 GiB-s, 2 M requests | API only while handling requests |
| Cloud Scheduler | 3 jobs per billing account | 1 job |
| Secret Manager | 6 active versions, 10,000 accesses / month | 1 version; one access per job execution and per API instance start |
| Neon compute | 100 CU-hours per project / month; compute scales to zero after 5 min idle (cannot be disabled on Free) | see below |
| Neon storage | 1 GB per project | about 825 KB per job for the 8-page test paper (PDF + 6 result files), i.e. roughly 1,200 jobs |
| Internet egress from Cloud Run | 1 GiB / month within North America | Cloud Run → Neon traffic; stays free only if both are in North America (hence `us-central1` and a US Neon region) |

**The default is every 15 minutes because every 10 minutes does not fit the free tiers**
(the interval first planned), with these assumptions: 30-day month,
Neon compute fixed at 0.25 CU, each execution under 1 minute, and nothing else on the billing
account using the Cloud Run jobs allowance.

| schedule | executions / month | job vCPU-s (≥ 60 s each) | Neon active hours (≈ 6 min per wake) | Neon CU-hours at 0.25 CU |
|---|---|---|---|---|
| every 10 min | 4,320 | 259,200, **over** 240,000 (excess ≈ 19,200 vCPU-s ≈ $0.35 at the listed $0.000018) | ≈ 432 h | ≈ 108, **over** 100 |
| every 15 min | 2,880 | 172,800, within | ≈ 288 h | ≈ 72, within (28 left for API use) |
| every 20 min | 2,160 | 129,600, within | ≈ 216 h | ≈ 54, within |

Each execution queries Postgres, so it wakes the Neon compute, which then stays up for at least
5 minutes. Neon does not bill overages on the Free plan; running out of CU-hours means the
database becomes unavailable until the next month rather than a charge. Changing the interval
is one variable, `worker_schedule`. A job submitted right after an execution waits up to
15 minutes before processing starts.

Other cost notes:

- Image size: the image is 549 MB uncompressed (arm64 build). The compressed size in Artifact
  Registry (0.5 GB free) has not been measured; two kept versions must fit.
- `api_public = true` lets anyone upload to the API, which could fill the 1 GB of Neon storage.
  With the default (`false`), call it through `gcloud run services proxy p2s-api --region REGION`,
  which serves it on `http://localhost:8080` with your own credentials.
- There is no retention job yet: old PDFs and results stay in `stored_objects` until deleted.

## Steps

1. **GCP project and budget** (you): create or choose a project with billing enabled, and set a
   budget alert in the Billing console.
2. **Neon** (you): create a free project in a US AWS region, set the compute size to a fixed
   0.25 CU, and copy the **direct** (not pooled) connection string. The pooled endpoint goes
   through PgBouncer in transaction mode, which does not suit psycopg's server-side prepared
   statements or `CREATE INDEX CONCURRENTLY` in the migrations. Change the scheme for SQLAlchemy:
   `postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require`.
3. **Deploy**: from the repository root, with `gcloud`, `docker` (with buildx) and `terraform`
   installed and `gcloud auth login` and `gcloud auth application-default login` done:

   ```bash
   PROJECT_ID=your-project REGION=us-central1 service/deploy/deploy.sh
   ```

   The first run creates the secret and stops. Add the connection string without putting it in
   shell history or files:

   ```bash
   gcloud secrets versions add p2s-database-url --data-file=- --project your-project
   ```

   (paste the URL, then Ctrl-D), and run `deploy.sh` again. It builds the image for
   `linux/amd64`, applies Terraform, runs migrations, and rolls out the API and the worker job.
4. **Later image rollouts**: re-run `deploy.sh`, or set `github_repository` in Terraform, put
   the `workload_identity_provider` and `deployer_service_account` outputs plus project and
   region into the GitHub repository variables, and start the `deploy` workflow by hand.

Undo everything: `terraform -chdir=service/deploy/terraform destroy -var project_id=... -var image=...`
(the Neon project is deleted in the Neon console).

## AWS equivalents

| GCP | AWS |
|---|---|
| Cloud Run service | App Runner, or ECS on Fargate behind an ALB |
| Cloud Run job + Cloud Scheduler | ECS scheduled task (EventBridge Scheduler → Fargate task) |
| Neon (serverless Postgres) | RDS for PostgreSQL or Aurora Serverless v2 (Neon itself also runs on AWS) |
| Secret Manager | Secrets Manager or SSM Parameter Store |
| Artifact Registry | ECR |
| Workload Identity Federation for GitHub | GitHub OIDC provider + IAM role |
| Cloud Logging / Monitoring | CloudWatch Logs / Metrics |

The AWS free tiers differ from these and were not evaluated.
