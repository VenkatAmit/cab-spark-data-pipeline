# Data Dictionary

Column-level reference for every table in the pipeline. Organised by layer.

---

## Bronze

### public.raw_trips

Raw TLC yellow taxi trip records. One row per trip as published by the TLC. No transformations applied beyond type casting.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `vendor_id` | integer | yes | Taxi vendor: 1 = Creative Mobile Technologies, 2 = VeriFone |
| `pickup_datetime` | timestamptz | yes | Meter engaged timestamp |
| `dropoff_datetime` | timestamptz | yes | Meter disengaged timestamp |
| `passenger_count` | integer | yes | Number of passengers (driver-entered) |
| `trip_distance` | numeric | yes | Trip distance in miles as reported by taximeter |
| `rate_code_id` | integer | yes | Rate code: 1=Standard, 2=JFK, 3=Newark, 4=Nassau/Westchester, 5=Negotiated, 6=Group ride |
| `store_and_fwd_flag` | text | yes | `Y` = trip record held in vehicle memory before send; `N` = not a store-and-forward trip |
| `pu_location_id` | integer | yes | TLC Taxi Zone ID for pickup location |
| `do_location_id` | integer | yes | TLC Taxi Zone ID for dropoff location |
| `payment_type` | integer | yes | 1=Credit card, 2=Cash, 3=No charge, 4=Dispute, 5=Unknown, 6=Voided |
| `fare_amount` | numeric | yes | Metered fare in USD. May be negative for refunds (floor: –$500) |
| `extra` | numeric | yes | Miscellaneous extras: rush hour ($0.50) and overnight ($1) charges |
| `mta_tax` | numeric | yes | $0.50 MTA tax |
| `tip_amount` | numeric | yes | Credit card tips only. Cash tips not included |
| `tolls_amount` | numeric | yes | Total tolls paid |
| `improvement_surcharge` | numeric | yes | $0.30 improvement surcharge |
| `total_amount` | numeric | yes | Total charged to passengers. Does not include cash tips |
| `congestion_surcharge` | numeric | yes | NYC congestion surcharge (post-2019) |
| `airport_fee` | numeric | yes | $1.25 for pickups at LaGuardia and JFK (post-2022) |
| `partition_date` | date | no | First day of the ingested month, e.g. `2024-01-01` |

---

## Silver

### public_silver.silver_trips

Conformed, enriched, deduplicated trip records. Source of truth for all downstream gold models.

| Column | Type | Nullable | Tests | Description |
|---|---|---|---|---|
| `trip_id` | text | no | not_null, unique | MD5 surrogate key — 6 source fields + ROW_NUMBER() tie-breaker |
| `vendor_id` | integer | yes | — | Taxi vendor identifier |
| `pickup_datetime` | timestamptz | no | not_null | Meter engaged timestamp |
| `dropoff_datetime` | timestamptz | no | not_null | Meter disengaged timestamp |
| `passenger_count` | integer | yes | — | Number of passengers |
| `trip_distance` | numeric | no | not_null | Distance in miles (> 0 guaranteed by staging filter) |
| `rate_code_id` | integer | yes | — | Rate code |
| `pu_location_id` | integer | no | not_null, FK→silver_zones | Pickup TLC zone ID |
| `do_location_id` | integer | no | not_null, FK→silver_zones | Dropoff TLC zone ID |
| `payment_type` | integer | yes | — | Payment method code |
| `fare_amount` | numeric | no | not_null | Metered fare in USD |
| `extra` | numeric | yes | — | Extras and surcharges |
| `mta_tax` | numeric | yes | — | MTA tax |
| `tip_amount` | numeric | yes | — | Credit card tip amount |
| `tolls_amount` | numeric | yes | — | Tolls paid |
| `improvement_surcharge` | numeric | yes | — | Improvement surcharge |
| `total_amount` | numeric | no | not_null | Total charged |
| `congestion_surcharge` | numeric | yes | — | NYC congestion surcharge |
| `airport_fee` | numeric | yes | — | Airport pickup fee |
| `trip_duration_minutes` | numeric | yes | — | `(dropoff - pickup)` in minutes |
| `fare_per_mile` | numeric | yes | — | `fare_amount / trip_distance`. Null when distance = 0 |
| `tip_pct` | numeric | yes | — | `tip_amount / total_amount * 100` |
| `has_tip` | boolean | yes | — | True when `tip_amount > 0` |
| `is_airport_trip` | boolean | yes | — | True when pickup or dropoff zone is JFK, LGA, or EWR |
| `partition_date` | date | no | not_null | First day of the ingested month |
| `transformed_at` | timestamptz | no | not_null | Timestamp when dbt wrote this row |

### public_silver.silver_zones

TLC Taxi Zone reference data. 265 zones covering all NYC boroughs and Newark Airport.

| Column | Type | Nullable | Tests | Description |
|---|---|---|---|---|
| `location_id` | integer | no | not_null, unique | TLC Taxi Zone ID (primary key) |
| `zone_name` | text | yes | — | Zone name as published by TLC |
| `borough` | text | no | not_null, accepted_values | NYC borough. Values: Manhattan, Brooklyn, Queens, Bronx, Staten Island, EWR, Unknown, N/A |
| `service_zone` | text | yes | — | Original TLC service zone label |
| `service_zone_code` | text | yes | accepted_values | Normalised code. Values: yellow, boro, ewr, unknown |
| `is_manhattan` | boolean | no | not_null | True when borough = Manhattan |

---

## Gold

### fact_trips

One row per trip. Central fact table of the star schema.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `trip_id` | text | no | Surrogate key (inherited from silver_trips) |
| `pickup_date_key` | bigint | yes | FK → dim_date.date_key. Format: `YYYYMMDD` + zero-padded hour |
| `vendor_id` | integer | yes | FK → dim_vendor.vendor_id |
| `pu_zone_id` | integer | yes | FK → dim_zone.zone_id |
| `do_zone_id` | integer | yes | FK → dim_zone.zone_id |
| `passenger_count` | integer | yes | Number of passengers |
| `trip_distance` | numeric | yes | Distance in miles |
| `trip_duration_minutes` | numeric | yes | Duration in minutes |
| `fare_amount` | numeric | yes | Metered fare in USD |
| `tip_amount` | numeric | yes | Tip amount in USD |
| `total_amount` | numeric | yes | Total charge in USD |
| `fare_per_mile` | numeric | yes | Fare per mile |
| `tip_pct` | numeric | yes | Tip as % of total |
| `has_tip` | boolean | yes | True when tip > 0 |
| `is_airport_trip` | boolean | yes | True for JFK/LGA/EWR trips |
| `payment_type` | integer | yes | Payment method code |
| `trip_month` | date | yes | Partition column — first day of trip month |

### dim_date

One row per hour across the dataset date range.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `date_key` | bigint | no | Primary key. Format: `YYYYMMDD` + zero-padded hour |
| `trip_month` | date | yes | First day of the month |
| `trip_date` | date | yes | Calendar date |
| `hour_of_day` | integer | yes | 0–23 |
| `day_of_week` | integer | yes | 0=Sunday … 6=Saturday |
| `day_name` | text | yes | Monday, Tuesday … |
| `is_weekend` | boolean | yes | True for Saturday and Sunday |
| `month_name` | text | yes | January, February … |
| `quarter` | integer | yes | 1–4 |
| `year` | integer | yes | Calendar year |

### dim_zone

One row per TLC Taxi Zone.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `zone_id` | integer | no | Primary key (= TLC location_id) |
| `zone_name` | text | yes | Zone name |
| `borough` | text | yes | NYC borough |
| `service_zone` | text | yes | TLC service zone |
| `is_manhattan` | boolean | yes | True when borough = Manhattan |
| `is_airport` | boolean | yes | True for JFK, LGA, EWR zones |

### dim_vendor

One row per taxi vendor.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `vendor_id` | integer | no | Primary key |
| `vendor_name` | text | yes | Creative Mobile Technologies or VeriFone Inc |

### agg_hourly_metrics

Pre-aggregated trip metrics at hour × pickup zone grain.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `trip_hour` | timestamptz | no | Truncated to hour — part of composite PK |
| `pu_zone_id` | integer | no | Pickup zone ID — part of composite PK |
| `total_trips` | bigint | yes | Number of trips in this hour and zone |
| `avg_fare` | numeric | yes | Average fare amount |
| `avg_distance` | numeric | yes | Average trip distance in miles |
| `avg_duration_minutes` | numeric | yes | Average trip duration |
| `total_revenue` | numeric | yes | Sum of total_amount |
| `avg_tip_pct` | numeric | yes | Average tip percentage |
| `trip_month` | date | yes | Partition column |

### agg_zone_summary

Pre-aggregated trip metrics at zone × month grain.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `zone_id` | integer | no | Zone ID — part of composite PK |
| `trip_month` | date | no | Month — part of composite PK |
| `total_pickups` | bigint | yes | Trips originating in this zone |
| `total_dropoffs` | bigint | yes | Trips ending in this zone |
| `avg_fare` | numeric | yes | Average fare for pickups in zone |
| `avg_distance` | numeric | yes | Average distance for pickups |
| `pct_airport_trips` | numeric | yes | % of trips that are airport trips |
| `pct_tipped` | numeric | yes | % of trips with a tip |

---

## Observability

### public.pipeline_run_log

One row written by `RunLogger` after each successful gold load.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `run_id` | text | no | Unique run identifier (Airflow run_id) |
| `dag_id` | text | no | Airflow DAG ID |
| `partition_date` | date | no | Month processed in this run |
| `started_at` | timestamptz | no | Pipeline start time |
| `completed_at` | timestamptz | yes | Pipeline completion time |
| `duration_seconds` | integer | yes | Wall-clock duration |
| `bronze_rows` | bigint | yes | Rows ingested into raw_trips |
| `silver_rows` | bigint | yes | Rows written to silver_trips |
| `gold_rows` | bigint | yes | Rows written to fact_trips |
| `validation_passed` | boolean | yes | True if all GX expectations passed |
| `error_message` | text | yes | Error detail if pipeline failed |
