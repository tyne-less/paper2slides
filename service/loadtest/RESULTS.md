# Load test results (2026-11-20)

## Setup

- Everything on one laptop: Apple M3, 16 GB; Docker Desktop with 8 CPUs and 7.7 GB. k6, the
  API (one uvicorn process), the workers, Postgres 16 and the S3-compatible store all share it,
  so the load generator and the workers compete with the API for CPU.
- k6 1.3.0 (`grafana/k6:1.3.0`) on the compose network, `loadtest/submit.js`: constant arrival
  rate for 60 s; each iteration uploads one PDF (`POST /jobs`) and reads its status once
  (`GET /jobs/{id}`). k6 `maxVUs` is 200, and request timeout is the k6 default (60 s).
- Input: Findings ACL 2024 paper 164 (8 pages, 438 KB). A unique PDF comment is appended to
  each upload so every request creates a new job (otherwise deduplication would return the
  first job).
- Each run starts from an empty stack (`loadtest/run.sh LABEL RATE DURATION WORKERS PDF`).
  Job-side figures come from Postgres after the queue drained (`loadtest/run_summary.sql`);
  the backlog is sampled once per second from the API's `/metrics`.

Per-run files: `results/<label>/` (k6 summary JSON and text, backlog samples, DB summary).
Container logs are not committed.

## Results

e2e = submission to job finished. Queue wait = submission to first claim.

| run | workers | target rate | submitted (dropped by k6) | HTTP failures | POST /jobs p50 / p95 ms | GET /jobs/{id} p50 / p95 ms | processed jobs/s | max backlog | e2e p50 / p95 s |
|---|---|---|---|---|---|---|---|---|---|
| w1-r5 | 1 | 5/s | 300 (0) | 0 / 600 | 21.7 / 61.6 | 3.1 / 8.3 | 5.02 | 8 | 0.72 / 1.27 |
| w1-r20 | 1 | 20/s | 1185 (16) | 0 / 2370 | 21.9 / 878.7 | 3.9 / 173.1 | 6.41 | 799 | 64.09 / 118.96 |
| w4-r20 | 4 | 20/s | 1106 (95) | 1 / 2211 | 1009.5 / 3770.2 | 446.7 / 2197.1 | 13.13 | 381 | 17.42 / 24.62 |
| w4-r20-repeat | 4 | 20/s | 1201 (0) | 0 / 2402 | 31.8 / 122.3 | 6.1 / 32.7 | 19.34 | 37 | 0.80 / 1.98 |
| w4-r40 | 4 | 40/s | 1012 (1389) | 0 / 2024 | 5781.0 / 23447.1 | 4020.6 / 7433.4 | 9.72 | 413 | 21.62 / 40.29 |
| w4-r40-repeat | 4 | 40/s | 1639 (762) | 0 / 3278 | 3610.8 / 6164.4 | 2572.2 / 4675.0 | 16.58 | 704 | 26.03 / 35.25 |

Every submitted job succeeded on its first attempt in every run (no dead jobs, no retries).

What the numbers show, and what they do not:

- **At 5/s one worker keeps up.** POST p95 62 ms, e2e p95 1.3 s. Most of the e2e time is queue
  wait (p50 0.61 s): an idle worker polls every `P2S_POLL_INTERVAL_SECONDS` (1 s).
- **At 20/s one worker falls behind** (6.4 jobs/s processed): the backlog peaked at 799 and
  took 126 s to drain after the last submission. Nothing was lost; jobs only waited.
- **At 40/s the API is the limit.** k6 hit its 200-VU cap and dropped 762–1389 iterations;
  POST latency rose to seconds. One uvicorn process does the upload read, PDF open, SHA-256
  and object-store write for every request, on a CPU shared with four workers and k6.
- **Run-to-run variance is large on this machine.** The two w4-r20 runs differ by 30× in POST
  p50 (1009.5 vs 31.8 ms) and 13.1 vs 19.3 processed jobs/s with identical settings, and
  w1-r20 processed 10.2 jobs/s in an earlier run versus 6.41 here. These figures describe this
  laptop under self-generated load, not the capacity of the service on dedicated hardware.
- **Unexplained failure:** the one failure in w4-r20 was a client-side `write: broken pipe`
  while uploading (k6 log); the API log has no matching error. Not investigated further.

## Bugs found by load testing (both fixed before the runs above)

1. **Intermittent 500s from deduplication (`ON CONFLICT`).** Earlier runs failed 1/601 (5/s)
   and 15/2343 (20/s) uploads with `there is no unique or exclusion constraint matching the ON
   CONFLICT specification`. The partial-index predicate was sent as a bound parameter
   (`status != $1`); after five executions on one connection psycopg uses a server-side
   prepared statement, and with a generic plan Postgres can no longer match that predicate to
   the partial unique index. Fix: literal predicate `status <> 'dead'`. Regression test:
   `test_submit_keeps_working_once_psycopg_prepares_the_statement` (failed before the fix).
2. **API connection-pool stall under load.** With 4 workers, runs failed 172/510 (20/s) and
   185/309 (40/s) requests: 57 `QueuePool limit of size 5 overflow 10 reached` errors after
   30 s waits, then k6 timeouts at 60 s, while Postgres and all containers were nearly idle.
   The API used a sync `yield` dependency for its session. Its cleanup, which returns the
   connection, runs on a threadpool thread after the endpoint. The likely mechanism: all 40
   threadpool threads were waiting for a connection while the connections were held by
   sessions waiting for a thread to close them. Fix: each endpoint opens a session only around
   its database calls. After the fix, none of the four runs with 4 workers had a pool timeout.
   The mechanism is inferred from the symptoms and from the fix removing them. It was not
   observed directly (for example from thread dumps).
