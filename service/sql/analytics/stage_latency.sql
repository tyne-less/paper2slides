-- Latency per pipeline stage over the last 7 days (successful stage runs only),
-- slowest p95 first.
SELECT
    stage,
    count(*) AS runs,
    round(percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1) AS p50_ms,
    round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1) AS p95_ms,
    round(percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1) AS p99_ms,
    max(duration_ms) AS max_ms
FROM stage_runs
WHERE status = 'succeeded'
  AND started_at >= now() - interval '7 days'
GROUP BY stage
ORDER BY p95_ms DESC;
