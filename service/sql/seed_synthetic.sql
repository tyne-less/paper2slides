-- Synthetic rows for checking query plans at a realistic table size.
-- NOT measurements: durations and error mixes are made up. Run only against a throwaway
-- database that has the schema applied, e.g.
--   psql "$URL" -v n_jobs=200000 -f service/sql/seed_synthetic.sql
\set ON_ERROR_STOP on
BEGIN;
SELECT setseed(0.42);

CREATE TEMP TABLE seed_jobs ON COMMIT DROP AS
SELECT
    gen_random_uuid() AS job_id,
    gen_random_uuid() AS document_id,
    now() - random() * interval '90 days' AS created_at,
    CASE
        WHEN r < 0.97 THEN 'succeeded'
        ELSE 'dead'
    END AS status,
    CASE
        WHEN r < 0.92 THEN 1
        WHEN r < 0.96 THEN 2
        WHEN r < 0.97 THEN 3
        WHEN r < 0.985 THEN 1   -- dead after a stage error (not retried)
        ELSE 3                  -- dead after exhausting retries
    END AS n_attempts
FROM (SELECT random() AS r FROM generate_series(1, :n_jobs)) AS s;

CREATE TEMP TABLE seed_attempts ON COMMIT DROP AS
SELECT
    gen_random_uuid() AS attempt_id,
    j.job_id,
    k AS attempt_no,
    j.created_at + (k - 1) * interval '20 seconds' + interval '1 second' AS started_at,
    CASE
        WHEN k < j.n_attempts OR j.status = 'dead' THEN
            CASE
                WHEN j.status = 'dead' AND j.n_attempts = 1 THEN 'failed'
                WHEN random() < 0.6 THEN 'failed'
                WHEN random() < 0.7 THEN 'timeout'
                ELSE 'lost'
            END
        ELSE 'succeeded'
    END AS outcome
FROM seed_jobs AS j
CROSS JOIN LATERAL generate_series(1, j.n_attempts) AS k;

INSERT INTO documents (id, sha256, size_bytes, page_count, original_filename, storage_key, created_at)
SELECT document_id, md5(document_id::text) || md5(job_id::text), 500000, 12,
       'paper.pdf', 'documents/' || document_id || '.pdf', created_at
FROM seed_jobs;

INSERT INTO jobs (id, document_id, pipeline_version, options, dedup_key, status, attempts,
                  max_attempts, run_after, fence_token, last_error_type, result_prefix,
                  created_at, updated_at, finished_at)
SELECT j.job_id, j.document_id, 'pdf2slides-rules-v1', '{"pdftotext_mode": "default"}',
       md5(j.job_id::text) || md5(j.document_id::text), j.status, j.n_attempts, 3, j.created_at,
       j.n_attempts,
       CASE WHEN j.status = 'dead' THEN CASE WHEN j.n_attempts = 1 THEN 'ValueError' ELSE 'Timeout' END END,
       CASE WHEN j.status = 'succeeded' THEN 'results/' || j.job_id || '/' END,
       j.created_at, last.finished_at, last.finished_at
FROM seed_jobs AS j
JOIN (
    SELECT job_id, max(started_at) + interval '2 seconds' AS finished_at
    FROM seed_attempts
    GROUP BY job_id
) AS last ON last.job_id = j.job_id;

INSERT INTO job_attempts (id, job_id, attempt_no, worker_id, fence_token, started_at,
                          finished_at, outcome, error_type)
SELECT a.attempt_id, a.job_id, a.attempt_no, 'worker-' || (a.attempt_no % 4), a.attempt_no,
       a.started_at, a.started_at + interval '2 seconds', a.outcome,
       CASE a.outcome
           WHEN 'succeeded' THEN NULL
           WHEN 'timeout' THEN 'Timeout'
           WHEN 'lost' THEN 'LeaseExpired'
           ELSE (ARRAY['EndpointConnectionError', 'ChildCrashed', 'ValueError'])[1 + floor(random() * 3)::int]
       END
FROM seed_attempts AS a;

-- Seven stage rows per successful attempt with log-normal durations; one failed row otherwise.
INSERT INTO stage_runs (attempt_id, stage, started_at, duration_ms, status, error_type)
SELECT a.attempt_id, st.stage, a.started_at + st.ord * interval '10 milliseconds',
       greatest(0, round(exp(ln(st.median_ms) + 0.6 * sqrt(-2 * ln(1 - random())) * cos(2 * pi() * random())))),
       'succeeded', NULL
FROM seed_attempts AS a
CROSS JOIN (VALUES
    (0, 'extract_text', 40.0), (1, 'split_pages', 1.0), (2, 'detect_layout', 8.0),
    (3, 'transcribe', 2.0), (4, 'parse_structure', 7.0), (5, 'generate_slides', 1.0),
    (6, 'render_beamer', 1.0)
) AS st(ord, stage, median_ms)
WHERE a.outcome = 'succeeded'
UNION ALL
SELECT a.attempt_id, 'extract_text', a.started_at, 1000, 'failed', 'Timeout'
FROM seed_attempts AS a
WHERE a.outcome <> 'succeeded';

COMMIT;
ANALYZE documents, jobs, job_attempts, stage_runs;
