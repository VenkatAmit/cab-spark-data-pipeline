# Architecture

## Overview

`cab-spark-data-pipeline` is a production-grade batch pipeline that processes NYC TLC yellow taxi trip data through a three-layer medallion architecture. Each layer has a single responsibility, a clearly defined input contract, and a clearly defined output contract. No layer reads from the layer above it. No business logic lives in DAG files.

```
TLC Public Data (Parquet, ~50 MB/month)
           │
           ▼
     ┌───────────┐
     │  Bronze   │  Raw ingestion — data arrives exactly as published
     └───────────┘
           │
           ▼
     ┌───────────┐
     │  Silver   │  Conformed & enriched — clean, typed, deduplicated
     └───────────┘
           │
           ▼
     ┌───────────┐
     │   Gold    │  Business-ready — star schema in Delta Lake
     └───────────┘
```

---

## Layer 1 — Bronze

### Responsibility

Ingest one month of raw TLC parquet data into Postgres with zero transformation. The bronze layer is a faithful copy of the source. No filtering, no casting beyond what is required to write to Postgres, no business logic.

### Storage

PostgreSQL 15 — `public` schema, table `raw_trips`. A `partition_date` column records the first day of the ingested month (e.g. `2024-01-01`).

### Key components

| Component | File | Role |
|---|---|---|
| `BronzeIngestor` | `pipeline/bronze/ingestor.py` | Downloads TLC parquet, streams rows into Postgres via psycopg3 `COPY` |
| `GXValidator` | `pipeline/bronze/validator.py` | Runs Great Expectations suite against the ingested partition |
| `bronze_dag` | `dags/bronze_dag.py` | Airflow DAG — orchestrates ingest → validate → dbt silver |

### Idempotency

Before each ingest, the ingestor deletes all rows for the target `partition_date`. A re-run for the same month is safe.

### Data quality

Great Expectations 0.18.19 runs four expectation classes after each ingest:

- `TripsRowCountExpectation` — row count within expected range for the month
- `TripsNullExpectation` — null rate on key columns below threshold
- `TripsPositiveAmountExpectation` — fare amounts between –$500 and $100,000 (TLC includes refunds), `mostly=0.99`
- `TripsDateRangeExpectation` — pickup timestamps fall within the partition month

Validation uses the GX `success` flag, which respects the `mostly` threshold. Raw failure counts are recorded in the `ExpectationResult` for observability but do not gate the pipeline on their own.

---

## Layer 2 — Silver

### Responsibility

Produce a clean, typed, deduplicated, enriched dataset in Postgres that downstream consumers (gold models, ad-hoc queries) can trust without further cleaning.

### Storage

PostgreSQL 15 — `public_silver` schema, table `silver_trips`. A separate `silver_zones` table holds the 265 TLC taxi zone records.

### Key components

| Component | File | Role |
|---|---|---|
| `stg_trips` | `dbt/models/staging/stg_trips.sql` | View — casts types, applies date/distance/fare filters |
| `silver_trips` | `dbt/models/silver/silver_trips.sql` | Incremental table — enriched columns, surrogate key |
| `silver_zones` | `dbt/models/silver/silver_zones.sql` | Full-refresh dimension — 265 NYC zones |
| `bronze_dag` | `dags/bronze_dag.py` | BashOperator runs `dbt run --select staging.* silver.*` after ingest |

### Incremental strategy

`silver_trips` uses `unique_key = trip_id` with `incremental_strategy = delete+insert`. On each run, dbt deletes existing rows that match the incoming `partition_date` and inserts the new batch. This makes re-runs safe.

### Surrogate key design

`trip_id` is an MD5 hash of six fields plus a `ROW_NUMBER()` tie-breaker:

```sql
MD5(
    vendor_id || '|' || pickup_datetime || '|' || dropoff_datetime
    || '|' || pu_location_id || '|' || do_location_id
    || '|' || fare_amount || '|'
    || ROW_NUMBER() OVER (
        PARTITION BY vendor_id, pickup_datetime, pu_location_id
        ORDER BY dropoff_datetime, fare_amount
    )
)
```

The original three-field MD5 produced 41,000 collisions across 23 months of real TLC data. The six-field hash plus `ROW_NUMBER()` eliminates all duplicates while remaining deterministic for idempotent re-runs.

### Enriched columns

Silver adds columns that do not exist in the source:

| Column | Derivation |
|---|---|
| `trip_duration_minutes` | `dropoff_datetime - pickup_datetime` in minutes |
| `fare_per_mile` | `fare_amount / trip_distance` (null when distance is 0) |
| `tip_pct` | `tip_amount / total_amount * 100` |
| `has_tip` | `tip_amount > 0` |
| `is_airport_trip` | pickup or dropoff zone in known airport zone list |
| `transformed_at` | `NOW()` at dbt run time |

---

## Layer 3 — Gold

### Responsibility

Produce a business-ready star schema in Delta Lake. Gold is the layer that analytics tools, dashboards, and data scientists query. It is optimised for read performance, not write throughput.

### Storage

Delta Lake on local disk (configurable via `DELTA_GOLD_BASE_PATH`). Each model is a separate Delta table partitioned by `trip_month`.

### Key components

| Component | File | Role |
|---|---|---|
| `GoldLoader` | `pipeline/gold/loader.py` | Reads silver via JDBC, merges into Delta tables |
| `spark_session.py` | `pipeline/spark_session.py` | Singleton SparkSession with Delta + JDBC config |
| `gold_dag` | `dags/gold_dag.py` | Airflow DAG — ExternalTaskSensor then PySpark load |
| dbt gold models | `dbt/models/gold/` | Six models built on top of `silver_trips` via `ref()` |

### Star schema

```
dim_date ──────┐
dim_zone ──────┤
dim_vendor ────┼──── fact_trips
               │
agg_hourly_metrics   (derived from fact_trips)
agg_zone_summary     (derived from fact_trips)
```

All gold dbt models reference silver via `{{ ref('silver_trips') }}`. No gold model uses `source()` — that would bypass dbt lineage and incremental logic.

### JDBC partitioning

Spark reads from Postgres silver using JDBC with configurable partition count (`SPARK_JDBC_NUM_PARTITIONS`). Each partition reads a distinct range of `partition_date` values to avoid a single serial scan of the full table.

### Delta MERGE

Each gold table uses a `MERGE` operation keyed on the table's primary key (e.g. `trip_id` for `fact_trips`, `date_key` for `dim_date`). This makes gold loads idempotent — re-running the same month updates existing rows and inserts new ones without producing duplicates.

---

## Orchestration

### DAGs

| DAG | Schedule | Catchup | Purpose |
|---|---|---|---|
| `bronze_dag` | `0 6 1 * *` | False | Ingest, validate, run dbt silver |
| `gold_dag` | `0 8 1 * *` | False | Load gold from silver via PySpark |

Both DAGs run monthly on the first of the month. `gold_dag` uses an `ExternalTaskSensor` with `execution_delta=timedelta(hours=2)` to wait for `bronze_dag` to complete before starting.

### Task dependency within bronze_dag

```
ingest_trips → validate_bronze → run_silver_dbt → test_silver_dbt
```

Silver dbt runs inside `bronze_dag` rather than a third DAG because silver has no independent trigger — it always runs immediately after bronze ingestion.

### Backfill

Backfills are performed manually via the Airflow CLI or the `cab pipeline backfill` command. The DAGs have `catchup=False` in code. The 23-month backfill (Jan 2024 – Nov 2025) was executed as a manual sequential run using `max_active_runs=1` to avoid Postgres and Spark resource contention.

---

## Layer boundaries

These rules are enforced by the `dag-check` CI job (AST import scan):

| Rule | Enforcement |
|---|---|
| DAG files must not import `psycopg`, `pyspark`, or `delta` directly | CI — banned import check |
| Gold models must use `ref()` not `source()` to read silver | Code review + dbt lineage |
| No business logic in DAG files — DAGs call pipeline functions only | Code review |
| CLI must not import pipeline service classes directly | CI — import boundary check |

---

## Dependency injection

All configuration is injected via pydantic-settings classes that read from environment variables:

| Settings class | Used by | Variables |
|---|---|---|
| `PostgresSettings` | `BronzeIngestor`, `GXValidator` | `POSTGRES_*` |
| `SparkSettings` | `GoldLoader`, `spark_session.py` | `SPARK_*`, `DELTA_*` |
| `AirflowSettings` | CLI commands | `AIRFLOW_*` |

`SparkSettings` is intentionally unhashable (it is a mutable Pydantic model). `get_spark()` is called with no arguments and reads `SparkSettings` internally, avoiding the `lru_cache` TypeError that occurs when an unhashable argument is passed to a cached function.

---

## Exception hierarchy

```
PipelineError (base)
├── BronzeError
│   ├── IngestError
│   └── ValidationError
├── GoldError
│   └── SparkError
└── OrchestratorError  (CLI layer)
```

All exceptions are typed and carry a `cause` attribute for exception chaining. DAG tasks catch `PipelineError` and re-raise as Airflow `AirflowException` so task state reflects the failure correctly.

---

## Infrastructure

See [docker.md](docker.md) for full details. Summary:

| Container | Image | Role |
|---|---|---|
| `airflow_webserver` | `docker/Dockerfile.airflow` | Airflow UI |
| `airflow_scheduler` | `docker/Dockerfile.airflow` | DAG scheduling |
| `airflow_db` | `postgres:15` | Airflow metadata database |
| `pipeline_db` | `postgres:15` | Bronze raw + Silver conformed data |

`Dockerfile.airflow` installs Java (`openjdk-17-jre-headless`) so PySpark can launch a JVM inside the scheduler container. `JAVA_HOME` is set to the arm64 JVM path — see [docker.md](docker.md) for the amd64 note.
