# Docker

Local infrastructure for `cab-spark-data-pipeline` is fully containerised. This document covers every image, every Compose service, and the known architecture dependency.

---

## Images

The project uses three custom images and two stock Postgres images.

### Dockerfile.airflow

**Path:** `docker/Dockerfile.airflow`
**Base:** `apache/airflow:2.9.2-python3.11`
**Purpose:** Airflow webserver and scheduler with Java and all pipeline Python dependencies baked in.

```
apache/airflow:2.9.2-python3.11
    │
    ├── apt-get: openjdk-17-jre-headless, libpq-dev
    ├── ENV JAVA_HOME, PYSPARK_PYTHON, SPARK_LOCAL_DIRS
    └── pip: psycopg[binary], pydantic-settings,
             great-expectations==0.18.19,
             dbt-postgres==1.7.18,
             pyspark==3.4.4,
             delta-spark==2.4.0
```

Java is required because PySpark launches a JVM subprocess inside the Airflow scheduler container when the gold DAG runs. Without Java, PySpark raises `JAVA_HOME is not set` and the SparkSession fails to initialise.

**`_PIP_ADDITIONAL_REQUIREMENTS` is not used.** Dependencies are baked into the image at build time. This avoids pip running on every container start (slow, non-reproducible) and keeps the image self-contained.

#### Architecture note

`JAVA_HOME` is hardcoded to the arm64 JVM path:

```dockerfile
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-arm64
```

This works correctly on Apple Silicon (arm64). On Intel/amd64 machines, change line 21 of `docker/Dockerfile.airflow` before building:

```dockerfile
# amd64 (Intel Mac / Linux x86_64)
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
```

Check your architecture with `uname -m`:
- `arm64` → use the default (no change needed)
- `x86_64` → use the amd64 path

See `.env.example` for the full note.

---

### Dockerfile.bronze

**Path:** `docker/Dockerfile.bronze`
**Purpose:** Slim image for bronze ingestion tasks. No JVM, no PySpark.

```
python:3.11-slim
    │
    ├── apt-get: libpq-dev
    └── pip: psycopg[binary], pydantic-settings,
             great-expectations==0.18.19
```

This image is used in CI to verify the bronze layer builds and runs without Spark dependencies.

---

### Dockerfile.gold

**Path:** `docker/Dockerfile.gold`
**Purpose:** PySpark + Delta Lake image for gold layer tasks.

```
python:3.11-slim
    │
    ├── apt-get: openjdk-17-jre-headless, libpq-dev
    ├── ENV JAVA_HOME, PYSPARK_PYTHON
    └── pip: pyspark==3.4.4, delta-spark==2.4.0,
             psycopg[binary], pydantic-settings
```

The PostgreSQL JDBC jar (`org.postgresql:postgresql:42.7.3`) is fetched at SparkSession initialisation via `spark.jars.packages` (Maven coordinate resolution), not bundled in the image. This keeps the image smaller while ensuring the correct jar version is always used.

---

## Compose services

### docker-compose.yml overview

```
airflow_db      ← Postgres 15 (Airflow metadata)
pipeline_db     ← Postgres 15 (bronze raw + silver conformed)
airflow_init    ← One-shot: db migrate + admin user create
airflow_webserver
airflow_scheduler
superset        ← profile: tools (optional)
jupyter         ← profile: tools (optional)
```

All services share the `taxi_net` bridge network.

---

### airflow_db

| Property | Value |
|---|---|
| Image | `postgres:15` |
| Purpose | Airflow metadata database (DAG runs, task state, XCom) |
| Port | Not exposed externally |
| Volume | `airflow_db_data` (named volume) |
| Memory | 512 MB |

---

### pipeline_db

| Property | Value |
|---|---|
| Image | `postgres:15` |
| Purpose | Bronze `raw_trips` + Silver `cleaned_trips` storage |
| Port | `5433:5432` (exposed for local inspection) |
| Volume | `pipeline_db_data` (named volume) |
| Memory | 1500 MB |
| Init | Runs all `.sql` files in `sql/` via `docker-entrypoint-initdb.d` |

Tuning flags applied at startup:

```
shared_buffers=256MB
work_mem=64MB
maintenance_work_mem=128MB
max_connections=20
```

Connect locally:

```bash
psql -h localhost -p 5433 -U $PIPELINE_DB_USER -d $PIPELINE_DB_NAME
```

---

### airflow_init

| Property | Value |
|---|---|
| Base | `*airflow-common` |
| Purpose | One-shot initialisation: `airflow db migrate` + admin user create |
| Restart | `on-failure` |

Runs once on first `docker compose up`. If the container exits with code 0, subsequent `up` calls skip it cleanly.

---

### airflow_webserver

| Property | Value |
|---|---|
| Base | `*airflow-common` (builds `docker/Dockerfile.airflow`) |
| Port | `8081:8080` |
| URL | http://localhost:8081 |
| Credentials | admin / admin |
| Memory | 1 GB |
| Health check | `curl --fail http://localhost:8080/health` every 30s |

---

### airflow_scheduler

| Property | Value |
|---|---|
| Base | `*airflow-common` (builds `docker/Dockerfile.airflow`) |
| Port | None |
| Memory | 4 GB |
| Notes | Requires 4 GB because PySpark driver runs here during gold DAG execution |

The scheduler container is where `GoldLoader` and `BronzeIngestor` actually execute. It needs Java (installed in `Dockerfile.airflow`) and sufficient memory for the Spark driver heap.

---

### superset (optional)

| Property | Value |
|---|---|
| Image | `apache/superset:3.1.0` |
| Profile | `tools` — not started by default |
| Port | `8088:8088` |
| URL | http://localhost:8088 |
| Credentials | admin / admin |

Start with:

```bash
docker compose --profile tools up -d superset
```

Pre-configured to connect to `pipeline_db` for querying gold and silver tables.

---

### jupyter (optional)

| Property | Value |
|---|---|
| Image | `jupyter/pyspark-notebook:latest` |
| Profile | `tools` — not started by default |
| Port | `8888:8888` |
| Volume | `./notebooks:/home/jovyan/work` |

Start with:

```bash
docker compose --profile tools up -d jupyter
```

The Delta Lake path (`./airflow/data/delta`) is mounted at `/home/jovyan/delta` for direct DeltaTable reads in notebooks.

---

## Volume layout

| Volume | Contents |
|---|---|
| `airflow_db_data` | Airflow Postgres data |
| `pipeline_db_data` | Bronze + Silver Postgres data |
| `superset_data` | Superset metadata |
| `./airflow/data` | Delta Lake files (`./airflow/data/delta/`) and raw parquet downloads |
| `./airflow/logs` | Airflow task logs |
| `./dbt` | Mounted into scheduler at `/opt/airflow/dbt` |
| `./dags` | Mounted into scheduler at `/opt/airflow/dags` |
| `./pipeline` | Mounted into scheduler at `/opt/airflow/pipeline` |
| `./data` | Mounted at `/data` for parquet source files |
| `./great_expectations/reports` | GX HTML validation reports |

---

## Common commands

```bash
# Start core stack
docker compose up -d

# Start with optional tools
docker compose --profile tools up -d

# View running containers and health
docker compose ps

# Tail scheduler logs (where pipeline tasks execute)
docker compose logs -f airflow_scheduler

# Tail a specific task log
docker compose exec airflow_scheduler \
  airflow tasks logs bronze_dag ingest_trips <run_id>

# Rebuild the Airflow image after Dockerfile change
docker compose build airflow_webserver airflow_scheduler
docker compose up -d

# Stop everything (preserves volumes)
docker compose down

# Stop and wipe all data (destructive)
docker compose down -v

# Connect to pipeline_db
docker compose exec pipeline_db \
  psql -U $PIPELINE_DB_USER -d $PIPELINE_DB_NAME

# Connect to airflow_db
docker compose exec airflow_db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB
```

---

## Memory allocation guide

The full stack (core only, no tools) requires approximately 7 GB of Docker memory:

| Service | Limit |
|---|---|
| airflow_scheduler | 4 GB (Spark driver) |
| airflow_webserver | 1 GB |
| pipeline_db | 1.5 GB |
| airflow_db | 512 MB |
| **Total** | **~7 GB** |

Set Docker Desktop memory to at least 8 GB. The gold DAG will OOM-kill the scheduler if the Spark driver cannot allocate its heap. Increase `SPARK_DRIVER_MEMORY` and the `mem_limit` on `airflow_scheduler` together if processing larger months.
