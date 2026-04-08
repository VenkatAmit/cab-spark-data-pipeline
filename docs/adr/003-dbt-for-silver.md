# ADR 003 — dbt for Silver Transforms

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** VenkatAmit

---

## Context

The Silver layer needs to clean, type-enforce, deduplicate, and enrich raw Bronze data. The transform logic includes column derivations, staging filters, a surrogate key, and referential integrity checks. A technology was needed to express these transforms declaratively, run them incrementally, and test the output with schema contracts.

---

## Decision

Use dbt-postgres 1.7.18 for all Silver transforms. The Silver layer consists of:

- `stg_trips` — a view that applies staging filters and type casts
- `silver_trips` — an incremental table with enriched columns and a surrogate key
- `silver_zones` — a full-refresh dimension seeded from `taxi_zones.csv`

dbt schema tests in `dbt/models/silver/schema.yml` enforce 21 data quality contracts after each run.

---

## Rationale

**SQL is the right language for tabular transforms.** Cleaning and enriching relational data (filtering rows, casting types, computing derived columns, joining dimensions) is most naturally expressed in SQL. dbt keeps this logic in `.sql` files that are readable, reviewable, and diffable.

**Incremental materialisation handles monthly batches cleanly.** dbt's `incremental` materialisation with `unique_key = trip_id` and `delete+insert` strategy makes Silver loads idempotent: the same month can be re-run any number of times without producing duplicate rows. Implementing this in pure Python would require manual upsert logic.

**Schema tests are first-class.** dbt's built-in tests (`not_null`, `unique`, `accepted_values`, `relationships`) provide a declarative contract on the Silver output. These 21 tests run after every Silver build and block Gold from running if any test fails. This catches data quality regressions before they propagate to analytics.

**Lineage and documentation.** dbt tracks the dependency graph between models (`stg_trips` → `silver_trips`). This makes it clear which upstream change affects which downstream model. `dbt docs generate` produces a browsable data catalogue if needed.

**dbt runs inside the existing Airflow container.** No additional infrastructure is needed. The `BashOperator` in `bronze_dag` calls `dbt run` and `dbt test` as subprocesses. dbt is installed in `Dockerfile.airflow` alongside all other pipeline dependencies.

---

## Alternatives considered

**PySpark for Silver** — Rejected. PySpark adds JVM startup overhead (~30–60 seconds) to a stage that processes ~3M rows in Postgres. SQL on Postgres is faster for this volume. PySpark is reserved for Gold where the columnar/Delta capabilities justify the overhead.

**Pure Python (psycopg3 + pandas)** — Rejected. Expressing incremental upsert logic, derived column calculations, and schema tests in Python produces more code that is harder to read and maintain than equivalent dbt SQL. Column derivations like `fare_per_mile` and `trip_duration_minutes` are one-line SQL expressions but multi-step pandas operations.

**SQLAlchemy ORM** — Rejected. An ORM introduces an abstraction layer that hides the SQL, making it harder to review transforms and harder to debug query performance. The Silver transforms are pure SQL — no ORM benefits apply.

---

## Consequences

- `dbt-postgres==1.7.18` must be installed in `Dockerfile.airflow`. Upgrading to 1.8.x changes incremental strategy defaults and requires re-testing.
- The dbt project directory must be mounted into the Airflow container at `/opt/airflow/dbt`. This is done via the `docker-compose.yml` volume mount.
- The dbt `BashOperator` command must include both `staging.*` and `silver.*` selectors — omitting `staging.*` causes silver models to fail to resolve their staging view sources.
- All Gold models must reference Silver via `{{ ref('silver_trips') }}` not `{{ source('silver', 'cleaned_trips') }}`. Using `source()` bypasses dbt lineage tracking and incremental logic.
