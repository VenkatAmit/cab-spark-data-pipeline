# Runbook

Operational playbook for `cab-spark-data-pipeline`. Covers routine operations, backfill procedures, monitoring, and recovery from common failures.

---

## Routine operations

### Start the stack

```bash
docker compose up -d
```

Wait ~60 seconds for `airflow_webserver` to pass its health check before triggering any DAGs.

```bash
# Confirm all services are healthy
docker compose ps
```

All services should show `healthy` or `running`. If `airflow_init` shows `exited (0)` that is correct — it is a one-shot container.

---

### Check pipeline health

```bash
# CLI health check (requires uv sync --extra cli)
cab health

# Or check via Airflow UI
open http://localhost:8081
```

A healthy pipeline shows:
- Both DAGs (`bronze_dag`, `gold_dag`) enabled
- Most recent run for each DAG in `success` state
- No tasks in `failed` or `up_for_retry` state

---

### Trigger a run manually

```bash
# Via CLI
cab pipeline run bronze --date 2024-06-01 --wait
cab pipeline run gold   --date 2024-06-01 --wait

# Via Airflow UI
# DAGs → bronze_dag → Trigger DAG w/ config → {"partition_date": "2024-06-01"}

# Via Airflow CLI (inside container)
docker compose exec airflow_scheduler \
  airflow dags trigger bronze_dag --conf '{"partition_date": "2024-06-01"}'
```

---

### Check run status

```bash
# All recent runs
cab pipeline status

# Specific DAG, last 5 runs
cab pipeline status --dag bronze_dag --limit 5

# Check pipeline_run_log (gold runs only)
docker compose exec pipeline_db psql \
  -U $PIPELINE_DB_USER -d $PIPELINE_DB_NAME \
  -c "SELECT partition_date, duration_seconds, bronze_rows, silver_rows, gold_rows, validation_passed FROM pipeline_run_log ORDER BY started_at DESC LIMIT 10;"
```

---

### Stream task logs

```bash
# Via CLI
cab pipeline logs --dag bronze_dag --run-id <run_id> --task ingest_trips --stream

# Via Airflow UI
# DAGs → bronze_dag → [run] → [task] → Logs

# Via container
docker compose logs -f airflow_scheduler
```

---

## Backfill

### What backfill means

Both DAGs have `catchup=False`. This means Airflow does not automatically schedule missed historical runs. To process historical months, trigger runs explicitly.

### Sequential backfill via CLI (recommended)

```bash
# Dry run first — see what would be triggered
cab pipeline backfill \
  --start 2024-01-01 \
  --end   2024-12-31 \
  --dry-run

# Execute
cab pipeline backfill \
  --start 2024-01-01 \
  --end   2024-12-31
```

The CLI triggers one month at a time and waits for each to complete before starting the next. This respects `max_active_runs=1` and prevents resource contention.

### Manual backfill via Airflow CLI

```bash
# Inside the scheduler container
docker compose exec airflow_scheduler bash

# Backfill bronze for a date range
airflow dags backfill bronze_dag \
  --start-date 2024-01-01 \
  --end-date   2024-12-31 \
  --reset-dagruns

# Backfill gold for the same range (after bronze completes)
airflow dags backfill gold_dag \
  --start-date 2024-01-01 \
  --end-date   2024-12-31 \
  --reset-dagruns
```

### Re-running a single month

```bash
# Re-run bronze for January 2024 (safe — ingest is idempotent)
cab pipeline run bronze --date 2024-01-01 --wait

# Re-run gold after bronze succeeds
cab pipeline run gold --date 2024-01-01 --wait
```

Re-running is always safe:
- Bronze deletes and re-inserts the `partition_date` slice before ingesting
- Silver uses `delete+insert` on `partition_date`
- Gold uses Delta `MERGE` on primary key

---

## Monitoring

### Key metrics to check after each run

| Metric | Where | What to look for |
|---|---|---|
| Row count per month | `pipeline_run_log.bronze_rows` | Should be 2.5M–3.5M. Drop > 20% warrants investigation |
| Validation passed | `pipeline_run_log.validation_passed` | Should always be `true` |
| Run duration | `pipeline_run_log.duration_seconds` | Gold runs > 20 min may indicate memory pressure |
| GX HTML report | `great_expectations/reports/` | Open in browser — red bars indicate failed expectations |
| Silver row count | `pipeline_run_log.silver_rows` | Should be close to bronze_rows (staging filters typically drop < 2%) |
| Delta table size | `$DELTA_GOLD_BASE_PATH/fact_trips/_delta_log/` | Check log file count — run VACUUM if > 100 entries |

### Check row counts directly

```bash
# Bronze rows for a partition
docker compose exec pipeline_db psql \
  -U $PIPELINE_DB_USER -d $PIPELINE_DB_NAME \
  -c "SELECT COUNT(*) FROM raw_trips WHERE partition_date = '2024-01-01';"

# Silver rows for a partition
docker compose exec pipeline_db psql \
  -U $PIPELINE_DB_USER -d $PIPELINE_DB_NAME \
  -c "SELECT COUNT(*) FROM public_silver.silver_trips WHERE partition_date = '2024-01-01';"
```

### Check Delta table row count (from Jupyter or PySpark shell)

```python
from delta import DeltaTable
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
dt = DeltaTable.forPath(spark, "/path/to/gold/fact_trips")
dt.toDF().filter("trip_month = '2024-01-01'").count()
```

---

## Recovery procedures

### bronze_dag failed on ingest_trips

**Symptom:** Task shows `failed`, error contains HTTP error or connection refused.

**Steps:**
1. Check TLC URL is reachable: `curl -I https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet`
2. If the URL is down, wait and retry — TLC occasionally has outages.
3. Clear and re-run the task from the Airflow UI: Task → Clear → Confirm.
4. The ingest is idempotent — re-running deletes the partial partition before re-ingesting.

---

### bronze_dag failed on validate_bronze

**Symptom:** Task shows `failed`, error contains `ValidationError`.

**Steps:**
1. Open the GX HTML report: `great_expectations/reports/<run_id>.html`
2. Identify which expectation failed and by how much.
3. Common causes:
   - `TripsRowCountExpectation` — TLC published a revised file with fewer rows. Check TLC data release notes.
   - `TripsDateRangeExpectation` — Source file contains records outside the expected month window (known to happen in TLC data). Adjust the expectation range if justified.
   - `TripsPositiveAmountExpectation` — Large refund batch exceeds the –$500 floor. Adjust `min_value` if the TLC refund policy has changed.
4. If the data is genuinely bad, fail the run and do not proceed to silver.
5. If the expectation threshold needs adjusting, update `pipeline/bronze/validator.py`, rebuild the image, and re-run.

---

### bronze_dag failed on run_silver_dbt

**Symptom:** Task shows `failed`, bash exit code non-zero.

**Steps:**
1. Check task logs for the dbt error message.
2. Common causes:
   - Model compilation error — check `dbt/models/silver/*.sql` syntax.
   - Relation not found — verify `stg_trips` view exists in Postgres (`SELECT * FROM stg_trips LIMIT 1`).
   - Unique key violation on `trip_id` — this should not happen after the MD5 fix, but if it does, check for source data anomalies.
3. Fix the model, commit, rebuild the image if needed, and re-run from `run_silver_dbt`:
   ```bash
   # Clear just the failed task in Airflow UI
   # DAGs → bronze_dag → [run] → run_silver_dbt → Clear
   ```

---

### gold_dag failed on load_gold (Spark OOM)

**Symptom:** Task shows `failed`, logs contain `java.lang.OutOfMemoryError` or `ExecutorLostFailure`.

**Steps:**
1. Increase Spark memory in `.env`:
   ```bash
   SPARK_DRIVER_MEMORY=4g
   SPARK_EXECUTOR_MEMORY=4g
   ```
2. Increase the scheduler container memory limit in `docker-compose.yml`:
   ```yaml
   airflow_scheduler:
     mem_limit: 6g
   ```
3. Rebuild and restart:
   ```bash
   docker compose up -d --force-recreate airflow_scheduler
   ```
4. Re-trigger the gold run.

---

### gold_dag failed — JDBC connection refused

**Symptom:** Task shows `failed`, logs contain `Connection refused` or `JDBC URL`.

**Steps:**
1. Verify `pipeline_db` is running and healthy:
   ```bash
   docker compose ps pipeline_db
   docker compose exec pipeline_db pg_isready -U $PIPELINE_DB_USER
   ```
2. If the container crashed, restart it:
   ```bash
   docker compose up -d pipeline_db
   ```
3. Verify `SILVER_SCHEMA` is set to `public_silver` in `pipeline/gold/loader.py`.
4. Re-trigger the gold run.

---

### ExternalTaskSensor times out in gold_dag

**Symptom:** `wait_for_bronze` task times out after 3600 seconds.

**Steps:**
1. Check `bronze_dag` run state for the same `execution_date`:
   ```bash
   cab pipeline status --dag bronze_dag --limit 5
   ```
2. If `bronze_dag` is still running, wait — the sensor will poke again on next interval (60s).
3. If `bronze_dag` failed, fix and re-run bronze first. The sensor will not unblock until `bronze_dag` reaches `success`.
4. If `bronze_dag` succeeded but the sensor still times out, check `execution_delta` — the sensor uses `timedelta(hours=2)`. If gold was triggered more than 2 hours after bronze, the sensor looks for the wrong `execution_date`. Re-trigger `gold_dag` manually with the correct date.

---

### Disk full — Delta Lake log files

**Symptom:** Delta MERGE fails with disk space error, or `_delta_log/` directory has hundreds of JSON files.

**Steps:**
1. Run VACUUM on the affected table (from PySpark or Jupyter):
   ```python
   from delta import DeltaTable
   dt = DeltaTable.forPath(spark, "/path/to/gold/fact_trips")
   dt.vacuum(retentionHours=168)  # 7 days
   ```
2. Run OPTIMIZE to bin-pack small files:
   ```python
   dt.optimize().executeCompaction()
   ```
3. Check available disk space:
   ```bash
   df -h
   du -sh $DELTA_GOLD_BASE_PATH/*
   ```

---

## Upgrading dependencies

### Great Expectations

GX is pinned to `0.18.19`. Version 1.x has breaking API changes (`context.sources` was removed). Do **not** upgrade without reading [ADR 004](adr/004-great-expectations-0.18.md) and fully re-testing the validator.

```bash
# Safe: patch version upgrade within 0.18.x
# Unsafe without testing: any upgrade to 1.x
```

### dbt-postgres

`dbt-postgres==1.7.18`. Upgrading to 1.8.x changes the incremental strategy defaults. Test with `dbt run --full-refresh` after any version change.

### PySpark / delta-spark

`pyspark==3.4.4` and `delta-spark==2.4.0` must be upgraded together — Delta Lake versions are tightly coupled to Spark versions. See the [Delta Lake compatibility matrix](https://docs.delta.io/latest/releases.html).

---

## Useful SQL snippets

```sql
-- Row counts per month across all layers
SELECT
    partition_date,
    bronze_rows,
    silver_rows,
    gold_rows,
    ROUND(silver_rows::numeric / NULLIF(bronze_rows, 0) * 100, 2) AS silver_retention_pct
FROM pipeline_run_log
ORDER BY partition_date;

-- Months with validation failures
SELECT partition_date, error_message
FROM pipeline_run_log
WHERE validation_passed = false
ORDER BY partition_date;

-- Silver trips for a specific month
SELECT COUNT(*), MIN(pickup_datetime), MAX(pickup_datetime)
FROM public_silver.silver_trips
WHERE partition_date = '2024-01-01';

-- Top 10 pickup zones by trip count (silver)
SELECT
    z.zone_name,
    z.borough,
    COUNT(*) AS trips
FROM public_silver.silver_trips t
JOIN public_silver.silver_zones z ON t.pu_location_id = z.location_id
GROUP BY z.zone_name, z.borough
ORDER BY trips DESC
LIMIT 10;
```
