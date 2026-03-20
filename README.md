# NYC Taxi Pipeline

Production-grade batch data pipeline processing 100M+ NYC taxi trips.

**Stack:** Apache Airflow · PySpark · dbt · Great Expectations · Delta Lake · PostgreSQL · Docker

## Architecture
```
NYC Taxi Parquet (TLC public data)
        |
        v
Airflow DAG (@daily, one month per run)
  ingest        — download parquet files to local storage
  spark_transform — PySpark: clean, partition, enrich (100M+ rows)
  dbt run       — SQL models: silver cleaning, gold aggregations
  dbt test      — schema tests, not_null, accepted_values, range checks
  ge_validate   — Great Expectations HTML validation report
  load          — pipeline_run_log, metrics, Delta Lake commit
        |
        v
PostgreSQL + Delta Lake (medallion schema)
  raw_trips        bronze
  cleaned_trips    silver  (dbt)
  trip_metrics     gold    (dbt)
  zone_summary     gold    (dbt)
  pipeline_run_log observability
```

## Setup

Coming soon — see branch `feature/core-pipeline` for progress.

## Branches

| Branch | Status | What it adds |
|--------|--------|-------------|
| feature/core-pipeline | in progress | Airflow + Postgres foundation |
| feature/spark-transform | planned | PySpark transform layer |
| feature/dbt-models | planned | SQL models + data tests |
| feature/great-expectations | planned | HTML data quality reports |
| feature/delta-lake | planned | ACID storage + time travel |
