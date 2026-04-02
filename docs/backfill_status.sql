-- Backfill progress monitor against the new pipeline_runs table
-- Usage: psql -h localhost -U postgres -d cab_pipeline -f docs/backfill_status.sql

SELECT
    dag_id,
    partition_date,
    status,
    rows_written,
    ROUND(duration_seconds / 60.0, 1) AS duration_min,
    TO_CHAR(finished_at, 'YYYY-MM-DD HH24:MI') AS finished_at
FROM pipeline_runs
ORDER BY partition_date;

-- Summary
SELECT
    COUNT(*) AS runs_total,
    COUNT(*) FILTER (WHERE status = 'success') AS succeeded,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed,
    ROUND(SUM(duration_seconds) / 3600.0, 1) AS total_hours,
    ROUND(AVG(duration_seconds) / 60.0, 1) AS avg_min_per_run
FROM pipeline_runs
WHERE dag_id = 'gold_dag';
