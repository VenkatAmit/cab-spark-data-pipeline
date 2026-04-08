# cab-spark-data-pipeline

Production-grade batch data pipeline processing **57M+ NYC yellow taxi trips** across 23 months (Jan 2024 – Nov 2025) using a Bronze → Silver → Gold medallion architecture.

[![CI](https://github.com/VenkatAmit/cab-spark-data-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/VenkatAmit/cab-spark-data-pipeline/actions/workflows/ci.yml)

---

## Table of contents

- [Architecture](#architecture)
- [Stack](#stack)
- [Project structure](#project-structure)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Environment variables](#environment-variables)
- [CI pipeline](#ci-pipeline)
- [Documentation](#documentation)

---

## Architecture

```
NYC TLC Parquet (public S3)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Bronze — raw ingestion                                     │
│  BronzeIngestor  psycopg3 COPY → Postgres raw_trips        │
│  GXValidator     Great Expectations 0.18 SQL datasource    │
│  DAG             bronze_dag  @monthly  06:00 UTC           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Silver — conformed & enriched                              │
│  stg_trips       dbt view  (staging filter + cast)        │
│  silver_trips    dbt incremental model on Postgres         │
│  silver_zones    dbt seed-backed dimension (265 zones)     │
│  DAG             bronze_dag  BashOperator runs dbt after   │
│                  ingestion completes                        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Gold — business-ready Delta Lake                           │
│  GoldLoader      PySpark JDBC read → Delta Lake MERGE      │
│  Models          fact_trips  dim_zone  dim_date             │
│                  dim_vendor  agg_hourly_metrics             │
│                  agg_zone_summary                           │
│  DAG             gold_dag  @monthly  08:00 UTC             │
│                  ExternalTaskSensor waits on bronze_dag    │
└─────────────────────────────────────────────────────────────┘
```

Data flows strictly downward. No layer reads from the layer above it, and no business logic lives in DAG files.

---

## Stack

| Component | Version | Role |
|---|---|---|
| Apache Airflow | 2.9.2 | Orchestration — LocalExecutor |
| Python (workers) | 3.11 | Pipeline + DAG runtime |
| Python (tooling) | 3.14 | Linting, formatting, type checking |
| psycopg3 | ≥ 3.2 | Bronze ingestion (COPY protocol) |
| PySpark | 3.4.4 | Gold transform and Delta merge |
| delta-spark | 2.4.0 | Delta Lake ACID writes |
| dbt-postgres | 1.7.18 | Silver incremental models |
| Great Expectations | 0.18.19 | Data quality validation |
| PostgreSQL | 15 | Bronze raw + Silver conformed storage |
| Docker / Compose | — | Local orchestration |
| uv | 0.11 | Python dependency management |

---

## Project structure

```
cab-spark-data-pipeline/
├── pipeline/
│   ├── bronze/
│   │   ├── ingestor.py       # BronzeIngestor — psycopg3 COPY into raw_trips
│   │   └── validator.py      # GXValidator — Great Expectations SQL datasource
│   ├── gold/
│   │   ├── loader.py         # GoldLoader — PySpark JDBC → Delta Lake MERGE
│   │   └── run_logger.py     # RunLogger — observability to pipeline_runs table
│   ├── exceptions.py         # Typed exception hierarchy
│   ├── settings.py           # pydantic-settings config (reads from env)
│   └── spark_session.py      # Singleton SparkSession factory (lru_cache)
│
├── cli/
│   ├── commands/             # run, status, backfill, logs sub-commands
│   ├── airflow_client.py     # httpx wrapper for Airflow REST API
│   ├── config.py             # AirflowSettings
│   └── main.py               # Typer entry point — `cab pipeline ...`
│
├── dags/
│   ├── bronze_dag.py         # Ingest + validate + run dbt silver
│   └── gold_dag.py           # PySpark gold load (waits on bronze_dag)
│
├── dbt/
│   ├── models/
│   │   ├── staging/          # stg_trips, stg_zones (views)
│   │   ├── silver/           # silver_trips (incremental), silver_zones
│   │   └── gold/             # fact_trips, dim_zone, dim_date, dim_vendor,
│   │                         # agg_hourly_metrics, agg_zone_summary
│   ├── seeds/                # taxi_zones.csv — 265 NYC TLC zones
│   └── tests/                # Custom singular data tests
│
├── docker/
│   ├── Dockerfile.airflow    # Airflow + Java + PySpark + all deps (no JVM skip)
│   ├── Dockerfile.bronze     # Slim image — psycopg3 + GX, no JVM
│   └── Dockerfile.gold       # PySpark + Delta Lake + PostgreSQL JDBC jar
│
├── sql/                      # Raw DDL for Postgres schema initialisation
├── tests/                    # Unit + integration tests (pytest)
├── great_expectations/       # GX suite config + HTML reports
├── docs/                     # Architecture, runbook, data dictionary, ADRs
├── .github/workflows/ci.yml  # 7-job CI pipeline
├── docker-compose.yml        # Full local stack
├── pyproject.toml            # uv/hatch project config + ruff/mypy settings
└── .env.example              # Required environment variables (copy to .env)
```

---

## Quick start

### Prerequisites

- Docker Desktop ≥ 4.x
- 8 GB RAM allocated to Docker (Spark + Airflow + two Postgres instances)
- Apple Silicon (arm64) or Intel/amd64 — see [Docker docs](docs/docker.md) for arch note

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — all required variables are documented inside
```

### 2. Start the stack

```bash
docker compose up -d
```

Services started:

| Service | URL | Credentials |
|---|---|---|
| Airflow webserver | http://localhost:8081 | admin / admin |
| Pipeline Postgres | localhost:5433 | see .env |
| Superset (optional) | http://localhost:8088 | admin / admin |
| Jupyter (optional) | http://localhost:8888 | — |

Superset and Jupyter are behind the `tools` profile and do not start by default:

```bash
docker compose --profile tools up -d
```

### 3. Initialise the Postgres schema

The `pipeline_db` container runs `sql/` scripts automatically on first start via the `docker-entrypoint-initdb.d` mount. No manual step needed.

### 4. Trigger the pipeline

```bash
# Via Airflow UI — enable bronze_dag, then gold_dag
# Both will backfill automatically from start_date if catchup=True

# Or via CLI
uv sync --extra cli
cab pipeline run bronze --date 2024-01-01 --wait
cab pipeline run gold   --date 2024-01-01 --wait
```

---

## CLI reference

```bash
# Install
uv sync --extra cli

# Configure
export AIRFLOW_API_URL=http://localhost:8081
export AIRFLOW_USERNAME=admin
export AIRFLOW_PASSWORD=admin

# Health check
cab health

# Trigger runs
cab pipeline run bronze --date 2024-01-01 --wait
cab pipeline run gold   --date 2024-01-01 --wait
cab pipeline run all    --date 2024-01-01

# Inspect status
cab pipeline status
cab pipeline status --dag bronze_dag --limit 5

# Backfill
cab pipeline backfill --start 2024-01-01 --end 2024-12-31 --dry-run
cab pipeline backfill --start 2024-01-01 --end 2024-12-31

# Logs
cab pipeline logs --dag bronze_dag --run-id <run_id>
cab pipeline logs --dag bronze_dag --run-id <run_id> --task ingest_trips --stream
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_HOST` | Yes | Postgres hostname |
| `POSTGRES_PORT` | Yes | Postgres port (default 5432) |
| `POSTGRES_DB` | Yes | Database name |
| `POSTGRES_USER` | Yes | Database user |
| `POSTGRES_PASSWORD` | Yes | Database password |
| `DELTA_GOLD_BASE_PATH` | Yes | Delta Lake base path on disk |
| `DELTA_CHECKPOINT_PATH` | Yes | Delta checkpoint path |
| `AIRFLOW__CORE__FERNET_KEY` | Yes | 32-byte url-safe base64 Fernet key |
| `AIRFLOW__WEBSERVER__SECRET_KEY` | Yes | Airflow webserver secret |
| `AIRFLOW_API_URL` | Yes (CLI) | Airflow REST API base URL |
| `AIRFLOW_USERNAME` | Yes (CLI) | Airflow basic auth user |
| `AIRFLOW_PASSWORD` | Yes (CLI) | Airflow basic auth password |
| `SPARK_APP_NAME` | No | Spark application name |
| `SPARK_MASTER` | No | Spark master URL (default local[*]) |
| `SPARK_EXECUTOR_MEMORY` | No | Executor memory (default 2g) |
| `SPARK_DRIVER_MEMORY` | No | Driver memory (default 2g) |
| `SPARK_JDBC_NUM_PARTITIONS` | No | JDBC read parallelism |
| `SPARK_JDBC_FETCH_SIZE` | No | JDBC fetch size |

All variables are documented with examples in `.env.example`.

---

## CI pipeline

7 jobs run on every push and pull request:

| Job | Python | What it checks |
|---|---|---|
| `lint` | 3.14 | ruff check, ruff format, mypy --strict |
| `lint-sql` | 3.11 | sqlfluff on `dbt/models/` |
| `test` | 3.11 | pytest — unit + integration tests |
| `dag-check` | 3.11 | AST syntax + banned import boundaries in DAGs |
| `secret-scan` | — | TruffleHog on PR diffs |
| `docker-bronze` | — | Dockerfile.bronze builds cleanly |
| `docker-gold` | — | Dockerfile.gold builds cleanly |

`test` and `dag-check` are prerequisites for the Docker build jobs. The secret scan runs only on pull requests.

---

## Documentation

All extended documentation lives in [`docs/`](docs/):

| Document | Description |
|---|---|
| [Architecture](docs/architecture.md) | Layer design, boundaries, key decisions |
| [Pipeline](docs/pipeline.md) | End-to-end data flow, task walkthrough |
| [Data models](docs/data-models.md) | Table relationships, ERD, model descriptions |
| [Data dictionary](docs/data-dictionary.md) | Column-level reference for every table |
| [Docker](docs/docker.md) | Image breakdown, compose services, arch notes |
| [Runbook](docs/runbook.md) | Backfill, recovery, monitoring, common failures |
| [ADR 001](docs/adr/001-medallion-architecture.md) | Why Bronze → Silver → Gold |
| [ADR 002](docs/adr/002-delta-lake-for-gold.md) | Why Delta Lake for the gold layer |
| [ADR 003](docs/adr/003-dbt-for-silver.md) | Why dbt for silver transforms |
| [ADR 004](docs/adr/004-great-expectations-0.18.md) | GX version pin rationale |
| [ADR 005](docs/adr/005-trip-id-collision-resolution.md) | MD5 collision fix and surrogate key design |
