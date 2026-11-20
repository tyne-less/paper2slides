# paper2slides service

An asynchronous job service around the existing `pdf2slides` pipeline (`../src/pdf2slides`).
A client uploads a PDF, the API queues a job in Postgres, a worker runs the pipeline stage by
stage, and the slide deck JSON and Beamer file are written to object storage.

The research code is used as a library and is not modified. The service lives entirely in
`service/`.

Status: stages 1–5 done (skeleton, reliable job processing, analytics SQL, observability and load tests, cloud deployment). Deployed to Cloud Run + Neon on 2026-11-20 and checked end to end once (`deploy/README_DEPLOY.md`). Stage 6 (LLM step) not started. Sections below say what exists now and what
does not yet.

## Run locally

Requires Docker.

```bash
docker compose -f service/docker-compose.yml up --build
```

This starts Postgres, an S3-compatible object store, a one-shot `alembic upgrade head`, the API
on `http://localhost:8080`, and one worker. Swagger UI is at `http://localhost:8080/docs`.

End-to-end check against the running stack:

```bash
service/scripts/smoke_test.sh path/to/paper.pdf
```

Any text-layer PDF works; `python service/scripts/make_sample_pdf.py sample.pdf` (needs
`pymupdf`) writes a small two-page one, which is what CI uses.

It uploads the PDF, waits for the job to succeed, checks that a repeat upload returns the same
job, and downloads the Beamer file.

Local object store: MinIO was the plan, but on 2026-11-20 neither `docker.io/minio/minio` nor
`quay.io/minio/minio` allowed anonymous pulls, so compose uses SeaweedFS
(`chrislusf/seaweedfs:3.97`) through the same S3 API. The storage code only depends on the S3
API (boto3), so this choice is confined to `docker-compose.yml`. The local store runs without
an S3 identity config and accepts any credentials; it is for local use only.

## API

| Method | Path | Behaviour |
|---|---|---|
| POST | `/jobs` | multipart `file` (PDF) and optional `pdftotext_mode` (`default`, `layout`, `columns`, `hybrid`). Returns 201 with a new job, or 200 with the existing job for the same file and options. 413 too large, 415 not a PDF, 422 unreadable PDF, too many pages, or unknown mode. |
| GET | `/jobs/{id}` | Job status, attempts, last error. |
| GET | `/jobs/{id}/events` | Audit trail: every status change with actor and reason. |
| GET | `/jobs/{id}/result` | 200 with the slide deck JSON and links to all output files, or 409 with the current status. |
| GET | `/jobs/{id}/result/files/{name}` | One output file (`01_raw_text.txt` … `06_slides.tex`), streamed by the API. |
| GET | `/health` | 200 if the database answers, else 503. (Not `/healthz`: Cloud Run reserves paths ending in `z`.) |
| GET | `/metrics` | Prometheus text format. |

Every response carries `x-request-id` (taken from the request header or generated). The id is
stored on the job and appears in the worker's logs for that job.

## How a job is processed

1. The API validates the upload, computes its SHA-256, stores it at
   `documents/{sha256}.pdf`, and inserts `documents` and `jobs` rows in one transaction with
   `INSERT … ON CONFLICT DO NOTHING`.
2. A worker claims the oldest ready job with
   `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1`, sets it to `running`, increments `attempts` and
   `fence_token`, and records a `job_attempts` row.
3. The worker (`python -m p2s_service worker`) starts a child process (`pipeline_child.py`)
   from a multiprocessing `forkserver` that has the pipeline modules preloaded. The child runs
   `pipeline_adapter.run_stages`. That function calls the seven `pdf2slides.stages` functions in
   the same order as `pdf2slides.pipeline.run_pipeline` and times each one; each stage timing is
   sent to the parent as soon as the stage ends. While waiting, the parent renews the lease and
   enforces the timeout and the shutdown grace period.
4. Outputs go to `results/{dedup_key}/`. The job is marked `succeeded` only by an
   `UPDATE … WHERE fence_token = <claimed token>`, so an attempt that no longer owns the job
   cannot overwrite its state.

### Reliability

Delivery semantics: **at-least-once execution with idempotent outputs**. A job can run more than
once (for example after its worker is killed), but result keys are deterministic and only the
attempt holding the current fence token can mark the job `succeeded`. Exactly-once execution is
not claimed.

| Mechanism | Behaviour | Covered by |
|---|---|---|
| Deduplication | `dedup_key = sha256(file sha256, pipeline version, options)`, unique among jobs that are not `dead`. A repeat upload returns the existing job; a `dead` job does not block resubmission, and the old job keeps its failure record. | `test_api.py` |
| Claiming | `FOR UPDATE SKIP LOCKED`; 6 threads draining 40 jobs never claim one twice. | `test_concurrent_workers_never_claim_the_same_job` |
| Fenced completion | A stale attempt cannot complete, fail, or release a job; its attempt is recorded as `lost`. | `test_stale_fence_token_cannot_complete` |
| Retry and backoff | Retried: timeouts, crashes of the pipeline process, and errors outside the pipeline (storage, network). Not retried: an exception raised by a pipeline stage, since the same input and code give the same result. Delay before retry n is uniform in [d/2, d], d = min(`retry_max_seconds`, `retry_base_seconds` · 2^(n-1)). After `max_attempts` counted failures the job is `dead`. | `test_transient_error_is_retried_after_backoff`, `test_retries_stop_at_max_attempts`, `test_stage_error_is_permanent_and_marks_job_dead` |
| Timeout | The pipeline process is killed after `job_timeout_seconds`; the stage that was running is recorded with `error_type = Timeout`. | `test_timeout_kills_a_hung_stage_and_schedules_a_retry` |
| Pipeline process crash | Recorded as `ChildCrashed` with the exit code, then retried. | `test_child_process_crash_is_recorded_and_retried` |
| Lease and heartbeat | The worker renews the lease every `lease_seconds / 3`. If renewal finds the job is no longer owned, the pipeline process is killed and nothing is uploaded. | `test_lease_lost_mid_run_abandons_the_attempt` |
| Recovery after a killed worker | Every worker runs the reaper every `reap_interval_seconds`: running jobs with an expired lease get their attempt marked `lost` (`LeaseExpired`) and are requeued with backoff, or marked `dead` when attempts are used up. | `test_reaper_requeues_job_of_a_worker_that_stopped_heartbeating`, `test_sigkill_mid_run_is_recovered_by_another_worker` (a real worker process and its pipeline process are killed with SIGKILL mid-job; a second worker completes the job on attempt 2) |
| Graceful shutdown | On SIGTERM/SIGINT the worker stops claiming. A job in hand may run for `shutdown_grace_seconds` (default 8 s, below Cloud Run's 10 s); otherwise it is released back to the queue as `released`, which does not count towards `max_attempts`. | `test_sigterm_lets_a_short_job_finish`, `test_sigterm_releases_a_job_that_cannot_finish_within_grace` |
| Worker loop | An exception in one iteration (for example the database briefly unreachable) is logged and the loop continues; a job held at that moment is recovered by the reaper. | — |
| Audit trail | Every status change writes a `job_events` row in the same transaction, with actor (`api`, `worker:<id>`, `reaper:<id>`) and reason. | all of the above |

Reliability tests were checked against deliberately broken code: disabling the heartbeat, the
reaper, retries, or the shutdown release, or counting `released` as a failure, each makes the
corresponding tests above fail.

Checked in the compose stack (2026-11-20): with the object store stopped, the worker's
download failed with `EndpointConnectionError`, the job was requeued after 13.4 s and then
30.1 s (`P2S_RETRY_BASE_SECONDS=20`), and it succeeded on attempt 3 after the store came back.
This exposed a startup bug, now fixed: the worker created the bucket at startup and exited when
the store was down. Only the API creates the bucket now, and compose restarts `api` and `worker`
with `restart: unless-stopped`.

Worker settings (environment variables with the `P2S_` prefix): `LEASE_SECONDS` (60),
`REAP_INTERVAL_SECONDS` (10), `JOB_TIMEOUT_SECONDS` (300), `SHUTDOWN_GRACE_SECONDS` (8),
`RETRY_BASE_SECONDS` (2), `RETRY_MAX_SECONDS` (300), `MAX_ATTEMPTS` (3, stored per job at
submission).

## Schema

Defined in `src/p2s_service/db/models.py`, migrated by `alembic/versions/` (0001 initial schema, 0002 analytics indexes)
(a test checks that the two agree).

| Table | Purpose |
|---|---|
| `documents` | One row per distinct file (unique `sha256`). |
| `jobs` | Current state of each job, lease, fence token, last error. |
| `job_attempts` | One row per claim, with outcome and error details. |
| `stage_runs` | Per-stage duration and status for each attempt. |
| `job_events` | Append-only status history. |

Indexes: partial index on `jobs(run_after) WHERE status = 'queued'` for claiming, partial index on
`jobs(lease_expires_at) WHERE status = 'running'` for the reaper, partial unique index on
`jobs(dedup_key) WHERE status <> 'dead'`, `job_events(job_id, id)`, `stage_runs(attempt_id)`,
and, from migration 0002, `stage_runs(started_at)`, `job_attempts(finished_at)` and
`jobs(finished_at)` for the analytics queries.

## Analytics queries

`sql/analytics/` holds three queries: per-stage latency percentiles (`stage_latency.sql`),
failed attempts by error type (`failures_by_error_type.sql`), and daily throughput with
end-to-end latency (`daily_throughput.sql`). Run them with `SET jit = off`; with the default JIT
setting, `daily_throughput` spent most of its time compiling.

`sql/analytics/RESULTS.md` records their plans before and after migration 0002 on 200,000
synthetic jobs (the data is synthetic and only used to check plans), the index decisions that
came out of that, and the query output for 30 real jobs run through the compose stack.

## Observability

- JSON logs on stdout with `request_id` and `job_id` from context variables. The worker's
  `job succeeded` record carries `phases_ms` (download, process_start, pipeline, process_exit,
  upload, complete) and `stages_ms`; `scripts/phase_breakdown.py` turns a log stream into
  per-phase percentiles.
- API `/metrics` (port 8080): `p2s_http_requests_total` and
  `p2s_http_request_duration_seconds` by method, route template, and status; `p2s_jobs{status}`,
  `p2s_queue_backlog`, `p2s_queue_oldest_ready_age_seconds`, and `p2s_metrics_db_up`. The job
  gauges are read from Postgres at scrape time, so they cover every worker process.
- Worker `/metrics` (port 9100 per worker, `P2S_WORKER_METRICS_PORT`, 0 disables):
  `p2s_worker_attempts_total{outcome}`, `p2s_worker_phase_seconds{phase}` and
  `p2s_worker_stage_seconds{stage}` histograms.

### Per-attempt overhead: measured and reduced

The phase timings showed that most of each attempt was spent starting the pipeline process,
not running the pipeline. With multiprocessing `spawn`, each attempt started a new interpreter
and re-imported the worker's own dependencies (SQLAlchemy, prometheus_client, …): importing
`p2s_service.worker` in the container takes about 550 ms, a bare interpreter about 8 ms.
Two changes fixed it: a `forkserver` with the pipeline modules preloaded, and starting the
worker from the package `__main__` (`python -m p2s_service worker`), which multiprocessing
does not re-run in child processes.

Same 30 real jobs (15 distinct PDFs × 2 modes, 1 worker, compose stack on an Apple M3), same
script (`scripts/run_pdf_batch.sh` then `scripts/phase_breakdown.py`):

| phase | spawn p50 / p95 ms | forkserver p50 / p95 ms |
|---|---|---|
| process_start | 505.8 / 808.9 | 3.0 / 8.4 |
| stages (pipeline work) | 71.5 / 186.6 | 77.0 / 275.1 |
| process_exit | 71.0 / 143.1 | 0.5 / 1.0 |
| upload | 20.9 / 41.4 | 20.1 / 34.2 |
| attempt total | 686.6 / 1013.1 | 122.2 / 321.2 |
| wall time for all 30 jobs | 22.9 s | 5.0 s |

The forkserver max for process_start (197 ms) is the one-time start of the server process.

## Load tests

`loadtest/run.sh` runs one k6 scenario (`loadtest/submit.js`) against a fresh compose stack and
summarises it from k6 and from Postgres. Full results, setup and caveats:
`loadtest/RESULTS.md`. In short, on one laptop that also runs k6 and all containers:

| workers | target rate | HTTP failures | POST /jobs p50 / p95 ms | processed jobs/s | e2e p95 s |
|---|---|---|---|---|---|
| 1 | 5/s | 0 / 600 | 21.7 / 61.6 | 5.02 | 1.27 |
| 1 | 20/s | 0 / 2370 | 21.9 / 878.7 | 6.41 | 118.96 |
| 4 | 20/s (two runs) | 1 / 2211, 0 / 2402 | 1009.5 / 3770.2, 31.8 / 122.3 | 13.13, 19.34 | 24.62, 1.98 |
| 4 | 40/s (two runs) | 0 / 2024, 0 / 3278 | 5781.0 / 23447.1, 3610.8 / 6164.4 | 9.72, 16.58 | 40.29, 35.25 |

All submitted jobs succeeded on the first attempt. One worker sustains about 5 jobs/s with
this paper; at 40/s the single API process is the bottleneck. Identical runs varied a lot on
this machine, so the figures are indicative only. Load testing found two bugs, both fixed and
described in `loadtest/RESULTS.md`: intermittent 500s from the deduplicating `ON CONFLICT`
once psycopg prepared the statement, and an API connection-pool stall caused by a sync
`yield` session dependency.

## Tests and CI

```bash
docker run -d --name p2s-test-db -e POSTGRES_USER=p2s -e POSTGRES_PASSWORD=p2s \
  -e POSTGRES_DB=p2s_test -p 55432:5432 postgres:16-alpine
pip install -c service/requirements.lock -e . -e "./service[dev]"
cd service
P2S_TEST_DATABASE_URL=postgresql+psycopg://p2s:p2s@localhost:55432/p2s_test pytest
```

Tests that need Postgres are skipped when `P2S_TEST_DATABASE_URL` is unset. The pipeline parity
test (`tests/test_pipeline_adapter.py`) checks that the adapter's outputs match `run_pipeline`
for the `default` and `hybrid` modes.

`.github/workflows/ci.yml` runs the existing research tests, the service tests against a
Postgres service container, ruff, and a Docker build followed by the smoke test. Nothing is
pushed or deployed.

`requirements.lock` pins the runtime dependency versions used by the image and CI.

Research tests in CI: all 29 existing tests under `tests/` run, none deselected.

## Deployment

`deploy/` holds Terraform for Cloud Run (API service, worker job started by Cloud Scheduler,
migration job), Secret Manager, Artifact Registry and optional Workload Identity Federation,
plus `deploy/deploy.sh` and a manual-only `.github/workflows/deploy.yml`. The database is a
Neon free Postgres, which also stores the PDFs and results (`P2S_STORAGE_BACKEND=postgres`,
uploads limited to 10 MB). `deploy/README_DEPLOY.md` has the steps, the AWS equivalents, and a
free-tier check. A 10-minute worker schedule would exceed both the Cloud Run jobs CPU
allowance (each execution is billed for at least one minute) and Neon's 100 CU-hours, so the
schedule is every 15 minutes, which fits both. Deployed once on 2026-11-20; what was checked
is listed in `deploy/README_DEPLOY.md`.

Storage backends: `local` (tests), `s3` (compose, any S3-compatible store), `postgres` (cloud).
Worker modes: `python -m p2s_service worker` polls until SIGTERM (compose);
`python -m p2s_service worker --drain` processes every ready job and exits (one Cloud Run job
execution). An error in drain mode ends the execution with a non-zero exit code.
