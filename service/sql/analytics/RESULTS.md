# Analytics queries: plans and results (2026-11-20)

Queries: `stage_latency.sql` (per-stage p50/p95/p99), `failures_by_error_type.sql` (failed
attempts by error type), `daily_throughput.sql` (per-day submitted/succeeded/dead and end-to-end
latency).

Environment: Apple M3, 16 GB; Docker Desktop with 8 CPUs and 7.7 GB; `postgres:16-alpine`
(PostgreSQL 16.16), default configuration. Timings on this setup vary by roughly ±30% between
runs, so only differences well beyond that are treated as real.

## 1. Query plans at realistic table size (synthetic data)

`service/sql/seed_synthetic.sql` with `n_jobs=200000` fills a throwaway database with 200,000
jobs over 90 days, 218,254 attempts and 1,381,606 stage rows. **The data is synthetic**: the
durations and error mix are made up, so the query *results* on it mean nothing; it is used only
to see which plans Postgres chooses and how long they take at this size.

Reproduce:

```bash
psql "$URL" -c "CREATE DATABASE p2s_explain"
P2S_DATABASE_URL=postgresql+psycopg://.../p2s_explain alembic upgrade 0001
psql ".../p2s_explain" -v n_jobs=200000 -f service/sql/seed_synthetic.sql
python service/scripts/explain_analytics.py postgresql://.../p2s_explain out-0001 --runs 15 --jit off
P2S_DATABASE_URL=postgresql+psycopg://.../p2s_explain alembic upgrade head
python service/scripts/explain_analytics.py postgresql://.../p2s_explain out-0002 --runs 15 --jit off
```

Median execution time of 15 `EXPLAIN (ANALYZE, BUFFERS)` runs, after `VACUUM ANALYZE`. Full plans
are in `plans/`.

| Query | Schema 0001, JIT off | Schema 0002, JIT off | Schema 0002, JIT on (default) |
|---|---|---|---|
| stage_latency | 100.6 ms, seq scan of `stage_runs` | 46.4 ms, bitmap scan on `ix_stage_runs_started_at` | 48.9 ms |
| failures_by_error_type | 25.6 ms, seq scan of `job_attempts` | 10.6 ms, bitmap scan on `ix_job_attempts_finished_at`, `jobs` by primary key | 17.0 ms |
| daily_throughput | 48.5 ms, seq scan of `jobs` for `finished_at` | 29.5 ms, bitmap scan on `ix_jobs_finished_at` | 172.2 ms, of which 143.6 ms JIT |

Findings:

- **Missing time-window indexes.** All three queries filter on a recent time window (7 or 14
  days, about 8–16% of rows here), and 0001 had no index on `stage_runs.started_at`,
  `job_attempts.finished_at` or `jobs.finished_at`. Migration `0002_analytics_indexes` adds
  them. Index sizes at this volume: 30 MB, 4.8 MB and 4.4 MB.
- **An unused index.** 0001's `stage_runs(stage, started_at)` (72 MB here) was not used by any
  query, because none filters on a single stage. 0002 drops it. `stage_runs` gets 7 inserts per
  successful attempt, so each index on it costs writes.
- **A covering index was not worth it.** `stage_runs(started_at) INCLUDE (stage, status,
  duration_ms)` allowed an index-only scan but was 77 MB, against 30 MB for the plain index,
  and the measured gain (about 47 → 42 ms) was within run-to-run noise.
- **CTE statistics.** The first version of `failures_by_error_type` read failed attempts from a
  CTE used twice. Postgres materialises such a CTE and has no statistics for it, so it estimated
  16,808 failed rows against an actual 1,863, and chose a hash join over a full seq scan of
  `jobs` (200,000 rows, 63.5 ms). Selecting failed attempts directly from `job_attempts` gives a
  correct estimate and a nested loop over `jobs_pkey` (10.6 ms with 0002).
- **JIT.** With the default `jit = on`, `daily_throughput` spends about 144 ms of 172 ms
  compiling, more than the query itself. Run these analytics queries with `SET jit = off` (or
  raise `jit_above_cost` for the role that runs them).
- **Migration on a populated table.** 0002 builds its indexes with `CREATE INDEX CONCURRENTLY`
  (writes continue during the build). On the seeded database the upgrade took 1.4 s.

## 2. Results on real jobs

All 17 PDFs available locally (9 from the private research repository and 8 Findings ACL 2024
papers; none of them are committed here) were submitted to the compose stack in both `default` and `hybrid` modes, with one
worker. Three of the files are byte-identical copies of one report; the uploads of the two
repeat copies returned the existing jobs (HTTP 200, 4 responses). That left 15 distinct files ×
2 modes = 30 jobs. All 30 succeeded on the first attempt. Queries were
run with `SET jit = off`.

`stage_latency` (n = 30 per stage):

| stage | runs | p50_ms | p95_ms | p99_ms | max_ms |
|---|---|---|---|---|---|
| extract_text | 30 | 39.5 | 141.0 | 165.1 | 175 |
| detect_layout | 30 | 9.5 | 23.0 | 25.1 | 26 |
| parse_structure | 30 | 7.0 | 15.1 | 21.0 | 23 |
| transcribe | 30 | 3.0 | 5.0 | 8.6 | 10 |
| generate_slides | 30 | 1.0 | 1.0 | 1.0 | 1 |
| split_pages | 30 | 0.0 | 1.0 | 1.0 | 1 |
| render_beamer | 30 | 0.0 | 0.0 | 0.0 | 0 |

`failures_by_error_type`: no rows (no failed attempts in this run).

`daily_throughput` (2026-11-20 row; the other 13 days are empty): submitted 30, succeeded 30,
dead 0, success 100.00%, e2e p50 6.18 s, e2e p95 12.78 s, average attempts 1.00.

The end-to-end figures mostly measure queueing, not processing: all 30 jobs were submitted
within a few seconds to a single worker, and the median wait between submission and claim was
5.7 s. Per attempt, the median wall time was 510 ms while the stages summed to 62 ms; the
remaining ~434 ms (p95 525 ms) is per-attempt overhead outside the stages: starting the
pipeline process (`spawn`), object-store download and uploads, and database writes. It has not
been broken down further yet. With 30 jobs this is a small sample, not a benchmark.
