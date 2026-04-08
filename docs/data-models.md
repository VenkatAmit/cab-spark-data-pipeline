# Data Models

Description of every table in the pipeline across all three layers, their relationships, and the design rationale behind each one.

---

## Layer map

```
┌─────────────────────────────────────────────────────┐
│  Bronze (Postgres — public schema)                  │
│  raw_trips                                          │
└─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  Silver (Postgres — public_silver schema)           │
│  silver_trips          silver_zones                 │
└─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  Gold (Delta Lake — $DELTA_GOLD_BASE_PATH)          │
│  fact_trips            dim_date                     │
│  dim_zone              dim_vendor                   │
│  agg_hourly_metrics    agg_zone_summary             │
└─────────────────────────────────────────────────────┘
```

---

## Bronze

### raw_trips

Faithful copy of the TLC parquet source. No transformation beyond type casting required to write to Postgres. One row per trip as published by the TLC.

**Primary key:** none (append-only per partition, deduplication happens in silver)
**Partition key:** `partition_date` (first day of the ingested month)
**Row count:** ~2.5M – 3.5M per month, ~57M total across 23 months

---

## Silver

### silver_trips

Conformed, enriched, deduplicated trip records. The authoritative dataset for all downstream consumption.

**Primary key:** `trip_id`
**Incremental key:** `partition_date`
**Strategy:** `delete+insert` on `partition_date` — idempotent re-runs

### silver_zones

TLC Taxi Zone dimension. 265 zones covering all NYC boroughs plus EWR.

**Primary key:** `location_id`
**Refresh:** full refresh on every dbt run

### silver_trips → silver_zones relationship

```
silver_trips.pu_location_id ──FK──► silver_zones.location_id
silver_trips.do_location_id ──FK──► silver_zones.location_id
```

Both foreign keys are tested via dbt `relationships` tests. Referential integrity is enforced at the dbt test layer, not at the Postgres constraint layer (to allow fast bulk inserts).

---

## Gold

The gold layer is a star schema optimised for analytical queries. All models are built by dbt from `{{ ref('silver_trips') }}` — no gold model reads directly from bronze or from a `source()`.

### Star schema

```
                    ┌─────────────┐
                    │  dim_date   │
                    │  date_key   │
                    └──────┬──────┘
                           │
┌─────────────┐    ┌───────▼──────────┐    ┌─────────────┐
│  dim_vendor │    │   fact_trips     │    │  dim_zone   │
│  vendor_id  ├────┤  vendor_id  (FK) ├────┤  zone_id    │
└─────────────┘    │  pickup_date_key │    └─────────────┘
                   │  pu_zone_id (FK) │
                   │  do_zone_id (FK) │
                   └──────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
   ┌──────────────────┐    ┌──────────────────────┐
   │ agg_hourly_      │    │  agg_zone_summary    │
   │ metrics          │    │                      │
   └──────────────────┘    └──────────────────────┘
```

### dim_date

One row per hour across the full date range of the dataset. Provides calendar attributes for time-based analysis.

**Primary key:** `date_key` (integer — `YYYYMMDD` + zero-padded hour, e.g. `2024010106`)
**Grain:** one row per hour
**Refresh:** full rebuild on each gold run

Key columns: `date_key`, `trip_month`, `trip_date`, `hour_of_day`, `day_of_week`, `day_name`, `is_weekend`, `month_name`, `quarter`, `year`

### dim_zone

One row per TLC Taxi Zone. Built from `silver_zones`.

**Primary key:** `zone_id` (= `location_id` from silver_zones)
**Grain:** one row per zone (265 rows)
**Refresh:** full rebuild on each gold run

Key columns: `zone_id`, `zone_name`, `borough`, `service_zone`, `is_manhattan`, `is_airport`

### dim_vendor

One row per taxi vendor as reported in TLC data.

**Primary key:** `vendor_id`
**Grain:** one row per vendor (2 active vendors in TLC data: Creative Mobile Technologies, VeriFone)
**Refresh:** full rebuild on each gold run

Key columns: `vendor_id`, `vendor_name`

### fact_trips

One row per trip. The central fact table of the star schema.

**Primary key:** `trip_id` (inherited from `silver_trips.trip_id`)
**Partition:** `trip_month`
**Foreign keys:** `pickup_date_key → dim_date.date_key`, `pu_zone_id → dim_zone.zone_id`, `do_zone_id → dim_zone.zone_id`, `vendor_id → dim_vendor.vendor_id`
**Merge key:** `trip_id`

Key measure columns: `trip_distance`, `trip_duration_minutes`, `fare_amount`, `tip_amount`, `total_amount`, `fare_per_mile`, `tip_pct`, `has_tip`, `is_airport_trip`, `passenger_count`, `payment_type`

### agg_hourly_metrics

Pre-aggregated metrics at hour × pickup zone grain. Designed for dashboard queries that would otherwise require a full `fact_trips` scan.

**Primary key:** `(trip_hour, pu_zone_id)`
**Grain:** one row per hour per pickup zone
**Partition:** `trip_month`

Key columns: `trip_hour`, `pu_zone_id`, `total_trips`, `avg_fare`, `avg_distance`, `avg_duration_minutes`, `total_revenue`, `avg_tip_pct`

### agg_zone_summary

Pre-aggregated metrics at zone × month grain. Designed for zone-level performance analysis and borough comparison.

**Primary key:** `(zone_id, trip_month)`
**Grain:** one row per zone per month

Key columns: `zone_id`, `trip_month`, `total_pickups`, `total_dropoffs`, `avg_fare`, `avg_distance`, `pct_airport_trips`, `pct_tipped`

---

## Observability

### pipeline_run_log

Written by `RunLogger` after each successful gold load. One row per pipeline run.

**Storage:** Postgres `pipeline_db`, `public` schema
**Purpose:** Operational observability — timing, row counts, quality flags per run

Key columns: `run_id`, `dag_id`, `partition_date`, `started_at`, `completed_at`, `duration_seconds`, `bronze_rows`, `silver_rows`, `gold_rows`, `validation_passed`, `error_message`

---

## Design decisions

**Why no Postgres foreign key constraints in silver?**
Bulk insert via psycopg3 `COPY` and dbt incremental loads are significantly faster without constraint checking on every row. Referential integrity is enforced by dbt `relationships` tests at the end of each run instead.

**Why Delta Lake for gold and not Postgres?**
Delta Lake provides ACID MERGE semantics, time travel, and efficient columnar storage for analytical workloads. Postgres would require manual upsert logic and lacks native columnar optimisation. See [ADR 002](adr/002-delta-lake-for-gold.md).

**Why a star schema in gold instead of a wide flat table?**
Dimension tables (`dim_date`, `dim_zone`, `dim_vendor`) are reused across multiple fact and aggregate queries. Storing them separately avoids repeating zone and date attributes on every `fact_trips` row and keeps the fact table narrow for fast columnar scans.

**Why pre-aggregate in gold?**
`agg_hourly_metrics` and `agg_zone_summary` exist because `fact_trips` at 57M+ rows is expensive to scan for dashboard queries. Pre-aggregation moves that cost to pipeline time (once per month) rather than query time (every dashboard refresh).
