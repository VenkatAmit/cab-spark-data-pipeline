# ADR 001 — Medallion Architecture (Bronze → Silver → Gold)

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** VenkatAmit

---

## Context

The pipeline processes NYC TLC yellow taxi trip data — a public dataset published monthly as Parquet files. The data needs to be ingested, cleaned, enriched, and made available for analytical queries. Several architectural patterns were considered for organising the data transformation stages.

---

## Decision

Adopt the Bronze → Silver → Gold medallion architecture with strict layer boundaries:

- **Bronze** stores raw source data exactly as published, with no transformation beyond what is required to write to Postgres.
- **Silver** applies cleaning, deduplication, type enforcement, and enrichment to produce a trusted, conformed dataset.
- **Gold** produces a business-ready star schema optimised for analytical queries.

Each layer has a single input (the layer above it) and a single output (its own storage). No layer reads from a layer above it. No business logic lives in orchestration (DAG) files.

---

## Rationale

**Auditability.** Because Bronze is an unmodified copy of the source, any question about what the TLC published for a given month can be answered by querying `raw_trips` directly. If a cleaning rule in Silver is wrong, the raw data is still intact and Silver can be rebuilt from it.

**Independent reprocessing.** Each layer can be reprocessed independently. If a Silver enrichment rule changes, Silver can be rebuilt from Bronze without re-downloading the source files. If a Gold model changes, Gold can be rebuilt from Silver without touching Bronze or re-running GX validation.

**Clear quality boundary.** The Silver layer is the point at which data quality is guaranteed. Everything that enters Gold has passed through Silver's dbt schema tests. Consumers of Gold data can trust that `trip_id` is unique, foreign keys resolve, and amounts are within expected ranges.

**Debugging.** When a Gold metric looks wrong, the medallion structure gives a clear debugging path: is the issue in Bronze (source data), Silver (cleaning logic), or Gold (aggregation logic)? Without layer separation, all three concerns are mixed together and much harder to isolate.

---

## Alternatives considered

**Single-layer pipeline (raw → analytics)** — Rejected. Mixing ingestion, cleaning, and aggregation in one step makes it impossible to reprocess one stage without re-running all stages. It also makes debugging failures much harder.

**Two-layer pipeline (raw → analytics)** — Rejected. Without a stable Silver layer, Gold models would need to embed cleaning logic (filtering bad rows, handling nulls, computing derived columns). This duplicates logic across models and creates inconsistency when the cleaning rules change.

**Data lake only (no Postgres for Bronze/Silver)** — Rejected. The pipeline volume (~3M rows/month) is well within Postgres's capabilities. Postgres provides immediate SQL access for debugging and GX validation without a Spark session. A pure Delta Lake approach would add Spark startup overhead to every debugging query.

---

## Consequences

- Three distinct storage targets must be maintained: Postgres (Bronze + Silver) and Delta Lake (Gold).
- Each layer requires its own technology choice: psycopg3 for Bronze, dbt for Silver, PySpark for Gold.
- CI must enforce that DAG files do not contain business logic or direct storage imports.
- The layer boundary between Silver and Gold (JDBC read from Postgres into Spark) adds latency and complexity compared to a single-storage pipeline.
