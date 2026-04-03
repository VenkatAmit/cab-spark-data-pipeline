# cab-spark-data-pipeline

Production-grade batch pipeline processing NYC yellow taxi trip data across a
Bronze → Silver → Gold medallion architecture.

## Architecture

```
NYC Taxi Parquet (TLC public data)
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  Bronze  — psycopg3 COPY into Postgres raw_trips        │
│            BronzeIngestor + GXValidator                 │
│            Airflow DAG: bronze_dag (daily, 06:00 UTC)   │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  Silver  — dbt incremental models on Postgres           │
│            stg_trips → silver_trips, silver_zones       │
│            Airflow: BashOperator runs dbt after bronze  │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  Gold    — PySpark JDBC read → Delta Lake MERGE         │
│            GoldLoader: fact_trips, dim_zones            │
│            Airflow DAG: gold_dag (daily, 08:00 UTC)     │
└─────────────────────────────────────────────────────────┘
```

**Stack:** Python 3.11 · psycopg3 · PySpark 3.4 · Delta Lake 2.4 · dbt-postgres · Great Expectations · Apache Airflow 2.9 · PostgreSQL 15 · Docker

## Stack Versions

| Component | Version |
|---|---|
| Python (workers) | 3.11 |
| Python (tooling) | 3.14 |
| Apache Airflow | 2.9.x |
| PySpark | 3.4.x |
| delta-spark | 2.4.x |
| dbt-postgres | 1.7.x |
| Great Expectations | 0.18.x |
| PostgreSQL | 15 |

## Project Structure

```
cab-spark-data-pipeline/
├── pipeline/
│   ├── bronze/
│   │   ├── ingestor.py      # BronzeIngestor — psycopg3 COPY into Postgres
│   │   └── validator.py     # GXValidator — Great Expectations SQL datasource
│   ├── gold/
│   │   ├── loader.py        # GoldLoader — PySpark JDBC → Delta Lake MERGE
│   │   └── run_logger.py    # RunLogger — observability to pipeline_runs table
│   ├── exceptions.py        # Typed exception hierarchy
│   ├── settings.py          # pydantic-settings config (env vars)
│   └── spark_session.py     # Singleton SparkSession factory
├── cli/
│   ├── airflow_client.py    # httpx wrapper for Airflow REST API
│   ├── commands/            # run, status, backfill, logs
│   ├── config.py            # AirflowSettings re-export
│   ├── exceptions.py        # OrchestratorError re-export
│   └── main.py              # Typer app entry point (cab pipeline ...)
├── dags/
│   ├── bronze_dag.py        # Airflow DAG: ingest + validate
│   └── gold_dag.py          # Airflow DAG: PySpark gold load
├── dbt/
│   ├── models/
│   │   ├── staging/         # stg_trips, stg_zones
│   │   ├── silver/          # silver_trips (incremental), silver_zones
│   │   └── gold/            # fact_trips, dim_zone, dim_date, dim_vendor,
│   │                        # agg_hourly_metrics, agg_zone_summary
│   ├── seeds/               # taxi_zones.csv (265 NYC zones)
│   └── tests/               # Custom data tests
├── docker/
│   ├── Dockerfile.bronze    # Slim image — psycopg3 + GX, no JVM
│   └── Dockerfile.gold      # PySpark + Delta Lake + Postgres JDBC jar
├── tests/                   # 191 tests — unit + integration
├── .github/workflows/ci.yml # 7-job CI pipeline
├── docker-compose.yml
└── pyproject.toml
```

## Quick Start

```bash
# Copy env template and configure
cp .env.example .env

# Start the full stack
docker compose up -d

# Airflow UI: http://localhost:8080  (admin / admin)
# Trigger bronze ingestion for today
cab pipeline run bronze

# Trigger gold load after bronze succeeds
cab pipeline run gold

# Or run both in sequence
cab pipeline run all --date 2024-01-01
```

## CLI

```bash
# Install CLI dependencies
uv sync --extra cli

# Configure credentials
export AIRFLOW_API_URL=http://localhost:8080
export AIRFLOW_USERNAME=admin
export AIRFLOW_PASSWORD=admin

# Commands
cab health                                         # Check Airflow connectivity
cab pipeline run bronze --date 2024-01-01 --wait  # Trigger + wait
cab pipeline run gold   --date 2024-01-01 --wait
cab pipeline run all    --date 2024-01-01          # Bronze then gold
cab pipeline status                                # Latest runs for all DAGs
cab pipeline status --dag bronze_dag --limit 5
cab pipeline backfill --start 2024-01-01 --end 2024-03-31
cab pipeline backfill --start 2024-01-01 --end 2024-03-31 --dry-run
cab pipeline logs --dag bronze_dag --run-id <run_id>
cab pipeline logs --dag bronze_dag --run-id <run_id> --task ingest_trips --stream
```

## CI

7 jobs run on every push and pull request:

| Job | What it checks |
|---|---|
| Lint | ruff, black, mypy --strict (Python 3.14) |
| SQL lint | sqlfluff on dbt/models/ |
| Test | 191 pytest tests (Python 3.11) |
| DAG integrity | AST syntax + banned import checks |
| Secret scan | TruffleHog on PR diffs |
| Docker build (bronze) | Dockerfile.bronze builds cleanly |
| Docker build (gold) | Dockerfile.gold builds cleanly |

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `POSTGRES_HOST` | Postgres host | Yes |
| `POSTGRES_PORT` | Postgres port | Yes |
| `POSTGRES_DB` | Database name | Yes |
| `POSTGRES_USER` | Database user | Yes |
| `POSTGRES_PASSWORD` | Database password | Yes |
| `DELTA_GOLD_BASE_PATH` | Delta Lake base path | Yes |
| `DELTA_CHECKPOINT_PATH` | Delta checkpoint path | Yes |
| `AIRFLOW_API_URL` | Airflow webserver URL | Yes |
| `AIRFLOW_USERNAME` | Airflow basic auth user | Yes |
| `AIRFLOW_PASSWORD` | Airflow basic auth password | Yes |
| `SPARK_APP_NAME` | Spark application name | No |
| `SPARK_MASTER` | Spark master URL | No |
| `SPARK_EXECUTOR_MEMORY` | Executor memory | No |
| `SPARK_DRIVER_MEMORY` | Driver memory | No |
| `SPARK_JDBC_NUM_PARTITIONS` | JDBC read partitions | No |
| `SPARK_JDBC_FETCH_SIZE` | JDBC fetch size | No |
