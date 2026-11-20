-- Failed attempts over the last 7 days by error type.
--   pct_of_attempts: share of all finished attempts (released-on-shutdown attempts excluded).
--   jobs_affected:   distinct jobs that had at least one such attempt.
--   jobs_dead:       of those, jobs that ended dead with this as their last error.
-- The failed attempts are selected straight from job_attempts (not from a shared CTE), so the
-- planner can use column statistics, see that few rows match, and join them to jobs by key.
SELECT
    a.error_type,
    a.outcome,
    count(*) AS attempts,
    round(100.0 * count(*) / (
        SELECT count(*)
        FROM job_attempts
        WHERE finished_at >= now() - interval '7 days'
          AND outcome <> 'released'
    ), 2) AS pct_of_attempts,
    count(DISTINCT a.job_id) AS jobs_affected,
    count(DISTINCT a.job_id) FILTER (
        WHERE j.status = 'dead' AND j.last_error_type = a.error_type
    ) AS jobs_dead
FROM job_attempts AS a
JOIN jobs AS j ON j.id = a.job_id
WHERE a.finished_at >= now() - interval '7 days'
  AND a.outcome NOT IN ('succeeded', 'released')
GROUP BY a.error_type, a.outcome
ORDER BY attempts DESC;
