-- Daily throughput for the last 14 days (UTC days).
--   submitted:   jobs created that day.
--   succeeded / dead: jobs that reached a final status that day.
--   e2e_p50_s / e2e_p95_s: submit-to-finish time of jobs that succeeded that day.
WITH days AS (
    SELECT generate_series(
        date_trunc('day', now() AT TIME ZONE 'UTC') - interval '13 days',
        date_trunc('day', now() AT TIME ZONE 'UTC'),
        interval '1 day'
    ) AS day
),
submitted AS (
    SELECT date_trunc('day', created_at AT TIME ZONE 'UTC') AS day, count(*) AS submitted
    FROM jobs
    WHERE created_at >= (SELECT min(day) FROM days) AT TIME ZONE 'UTC'
    GROUP BY 1
),
finished AS (
    SELECT
        date_trunc('day', finished_at AT TIME ZONE 'UTC') AS day,
        count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
        count(*) FILTER (WHERE status = 'dead') AS dead,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at))
            FILTER (WHERE status = 'succeeded') AS e2e_p50_s,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at))
            FILTER (WHERE status = 'succeeded') AS e2e_p95_s,
        avg(attempts) AS avg_attempts
    FROM jobs
    WHERE finished_at >= (SELECT min(day) FROM days) AT TIME ZONE 'UTC'
    GROUP BY 1
)
SELECT
    d.day::date AS day,
    coalesce(s.submitted, 0) AS submitted,
    coalesce(f.succeeded, 0) AS succeeded,
    coalesce(f.dead, 0) AS dead,
    round(100.0 * f.succeeded / nullif(f.succeeded + f.dead, 0), 2) AS success_pct,
    round(f.e2e_p50_s::numeric, 2) AS e2e_p50_s,
    round(f.e2e_p95_s::numeric, 2) AS e2e_p95_s,
    round(f.avg_attempts, 2) AS avg_attempts
FROM days AS d
LEFT JOIN submitted AS s ON s.day = d.day
LEFT JOIN finished AS f ON f.day = d.day
ORDER BY d.day;
