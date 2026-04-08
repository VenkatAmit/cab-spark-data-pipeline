# ADR 005 — trip_id Surrogate Key and Collision Resolution

**Status:** Accepted
**Date:** 2024-04-06
**Deciders:** VenkatAmit

---

## Context

The Silver layer uses a surrogate key (`trip_id`) to deduplicate trip records and support idempotent incremental loads. The key is computed as an MD5 hash of source fields. During the 23-month backfill (Jan 2024 – Nov 2025), the dbt `unique` test on `trip_id` failed with 41,000 duplicate values across the dataset.

---

## Problem

The original `trip_id` was computed from three fields:

```sql
MD5(
    COALESCE(CAST(vendor_id AS varchar), '_null_')
    || '|' || COALESCE(CAST(pickup_datetime AS varchar), '_null_')
    || '|' || COALESCE(CAST(pu_location_id AS varchar), '_null_')
) AS trip_id
```

In real TLC data, multiple trips genuinely share the same `vendor_id`, `pickup_datetime`, and `pu_location_id`. These are not duplicate records — they are distinct trips (e.g. multiple passengers picked up at the same zone at the same second by the same vendor). The three-field hash cannot distinguish them.

This caused the dbt `unique` test to fail and produced incorrect `delete+insert` behaviour: when Silver re-loaded a partition, one trip's row would overwrite another's because they shared the same `trip_id`.

---

## Decision

Extend `trip_id` to six source fields plus a `ROW_NUMBER()` tie-breaker:

```sql
MD5(
    COALESCE(CAST(vendor_id AS varchar), '_null_')
    || '|' || COALESCE(CAST(pickup_datetime AS varchar), '_null_')
    || '|' || COALESCE(CAST(dropoff_datetime AS varchar), '_null_')
    || '|' || COALESCE(CAST(pu_location_id AS varchar), '_null_')
    || '|' || COALESCE(CAST(do_location_id AS varchar), '_null_')
    || '|' || COALESCE(CAST(fare_amount AS varchar), '_null_')
    || '|' || CAST(ROW_NUMBER() OVER (
        PARTITION BY vendor_id, pickup_datetime, pu_location_id
        ORDER BY dropoff_datetime, fare_amount
    ) AS varchar)
) AS trip_id
```

---

## Rationale

**Six fields eliminate natural collisions.** Two trips with the same vendor, pickup time, and pickup zone almost never also share the same dropoff time, dropoff zone, and fare amount. Adding these three fields reduces natural collisions to effectively zero across real TLC data.

**ROW_NUMBER() eliminates residual collisions.** For any trips that still share all six natural fields, `ROW_NUMBER()` assigns a deterministic sequence number based on the same PARTITION and ORDER clause. This guarantees uniqueness within any input batch.

**Deterministic across re-runs.** `ROW_NUMBER() OVER (PARTITION BY vendor_id, pickup_datetime, pu_location_id ORDER BY dropoff_datetime, fare_amount)` produces the same sequence for the same input rows regardless of when the query runs. This means re-running Silver for a given `partition_date` produces identical `trip_id` values — idempotency is preserved.

**MD5 is acceptable for a surrogate key.** MD5 is not cryptographically secure, but it is not used for security here. It is used to produce a short, fixed-length string from a variable-length concatenation. The collision probability of MD5 across ~3M rows per month is negligible. The `ROW_NUMBER()` tie-breaker removes all practical collision risk.

---

## Alternatives considered

**Serial surrogate key (SERIAL / SEQUENCE)** — Rejected. A database sequence is not deterministic across re-runs. If Silver is re-loaded for a given month, the same trip would receive a different `trip_id` on each run. This breaks the `delete+insert` idempotency guarantee — Gold records keyed on the old `trip_id` would become orphaned.

**UUID v4** — Rejected for the same reason. Random UUIDs are not reproducible.

**UUID v5 (name-based SHA1)** — Viable alternative. UUID v5 is deterministic given the same namespace and name string. It would work as well as MD5 for this purpose. MD5 was retained because the existing code already used MD5 and the hash width difference (128 bits vs 128 bits) is identical. No practical difference.

**Natural composite key (no hash)** — Rejected. A composite primary key of seven columns would make the `delete+insert` merge condition verbose, make join conditions in Gold models complex, and significantly increase index size on `silver_trips`.

---

## Consequences

- The `trip_id` column description in `dbt/models/silver/schema.yml` must be updated to reflect the six-field + ROW_NUMBER() definition.
- Any external system or query that stored the old three-field `trip_id` values will find those values no longer present after the Silver full rebuild. A full rebuild of Silver was performed as part of the fix.
- The dbt `unique` test on `trip_id` now passes across all 23 months of TLC data.
- Gold models keyed on `trip_id` (e.g. `fact_trips`) are automatically correct because they are built from Silver after the fix.
