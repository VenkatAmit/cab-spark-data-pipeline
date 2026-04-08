# ADR 002 — Delta Lake for the Gold Layer

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** VenkatAmit

---

## Context

The Gold layer needs to store a star schema (fact_trips + dimensions + aggregates) that can be queried by analytics tools, updated monthly with new data, and re-processed idempotently when upstream Silver changes. A storage technology was needed that supports ACID upserts, handles growing data volumes efficiently, and integrates with the PySpark transform layer already chosen for Gold.

---

## Decision

Use Delta Lake 2.4.x (via `delta-spark`) for all Gold layer tables, stored on local disk under `$DELTA_GOLD_BASE_PATH`.

---

## Rationale

**ACID MERGE semantics.** Delta Lake's `MERGE` operation allows Gold loads to be idempotent — re-running the same month updates existing rows and inserts new ones without producing duplicates. Implementing equivalent upsert logic in Postgres would require manual `INSERT ... ON CONFLICT DO UPDATE` and careful transaction management.

**Columnar storage.** Delta tables use Parquet as the underlying file format. Analytical queries (aggregations, range scans, column projections) are significantly faster on columnar storage than on Postgres row storage. This matters for `fact_trips` at 57M+ rows.

**Time travel.** Delta Lake maintains a transaction log that enables point-in-time reads. If a Gold model produces incorrect data, the previous version of the table can be queried or restored without re-running the full pipeline. This is not possible with a plain Parquet or Postgres approach.

**PySpark integration.** Gold transforms already run in PySpark. Delta Lake is the native ACID table format for PySpark. Using Delta avoids introducing a second storage technology (e.g., writing Parquet directly, then managing overwrites manually).

**OPTIMIZE and VACUUM.** The pipeline runs `OPTIMIZE` (bin-pack small files) and `VACUUM` (7-day retention) after each MERGE. This prevents the Delta log from growing unboundedly and keeps read performance high as the dataset grows.

---

## Alternatives considered

**Postgres for Gold** — Rejected. Postgres row storage is not optimised for the analytical access patterns (column scans, aggregations) that Gold tables serve. UPSERT logic for 57M rows would require careful index management. Postgres also lacks time travel and native Parquet support.

**Plain Parquet (no Delta)** — Rejected. Raw Parquet has no ACID guarantees. Monthly updates would require either full rewrites of each partition (slow, no partial failure recovery) or manual management of partition directories. There is no MERGE operation.

**Apache Iceberg** — Considered but not chosen. Iceberg provides similar ACID guarantees and time travel. However, `delta-spark` is more mature in the PySpark 3.4 ecosystem and has better documentation for the specific version combination used (`delta-spark==2.4.0` + `pyspark==3.4.4`). Iceberg would be a reasonable alternative for a future migration.

---

## Consequences

- Java must be installed in the Airflow scheduler container (PySpark requirement). This is done in `docker/Dockerfile.airflow`.
- `spark.jars.packages` must include both the Delta Maven coordinate and the PostgreSQL JDBC jar.
- The SparkSession must configure `spark.sql.extensions` and `spark.sql.catalog.spark_catalog` for Delta to function correctly.
- `JAVA_HOME` must match the host architecture (arm64 vs amd64) — see `docker/Dockerfile.airflow` and `.env.example`.
- Delta Lake files grow on disk over time. `VACUUM` (7-day retention) is run after each monthly load to keep disk usage bounded.
