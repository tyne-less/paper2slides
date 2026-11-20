-- Summary of one load-test run on a fresh database (all jobs belong to the run).
WITH first_attempt AS (
    SELECT job_id, started_at FROM job_attempts WHERE attempt_no = 1
),
succeeded_attempts AS (
    SELECT extract(epoch FROM finished_at - started_at) * 1000 AS ms
    FROM job_attempts WHERE outcome = 'succeeded'
)
SELECT
    count(*) AS jobs,
    count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
    count(*) FILTER (WHERE j.status = 'dead') AS dead,
    count(*) FILTER (WHERE j.status IN ('queued', 'running')) AS unfinished,
    sum(j.attempts) - count(*) AS extra_attempts,
    round(extract(epoch FROM max(j.created_at) - min(j.created_at))::numeric, 1) AS submit_window_s,
    round((count(*) FILTER (WHERE j.status = 'succeeded')
        / extract(epoch FROM max(j.finished_at) - min(f.started_at)))::numeric, 2) AS processed_per_s,
    round(extract(epoch FROM max(j.finished_at) - max(j.created_at))::numeric, 1)
        AS drain_after_last_submit_s,
    round(percentile_cont(0.50) WITHIN GROUP (
        ORDER BY extract(epoch FROM f.started_at - j.created_at))::numeric, 2) AS queue_wait_p50_s,
    round(percentile_cont(0.95) WITHIN GROUP (
        ORDER BY extract(epoch FROM f.started_at - j.created_at))::numeric, 2) AS queue_wait_p95_s,
    round(percentile_cont(0.50) WITHIN GROUP (
        ORDER BY extract(epoch FROM j.finished_at - j.created_at))::numeric, 2) AS e2e_p50_s,
    round(percentile_cont(0.95) WITHIN GROUP (
        ORDER BY extract(epoch FROM j.finished_at - j.created_at))::numeric, 2) AS e2e_p95_s,
    (SELECT round(percentile_cont(0.50) WITHIN GROUP (ORDER BY ms)::numeric) FROM succeeded_attempts)
        AS attempt_p50_ms,
    (SELECT round(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)::numeric) FROM succeeded_attempts)
        AS attempt_p95_ms
FROM jobs AS j
JOIN first_attempt AS f ON f.job_id = j.id;
