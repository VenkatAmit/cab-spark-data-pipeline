{{
    config(
        materialized='view'
    )
}}

SELECT
    vendor_id,
    pickup_datetime,
    dropoff_datetime,
    passenger_count,
    pu_location_id,
    do_location_id,
    partition_date,
    ingested_at,
    CAST(trip_distance AS numeric(8, 2)) AS trip_distance,
    CAST(fare_amount AS numeric(10, 2)) AS fare_amount,
    CAST(tip_amount AS numeric(10, 2)) AS tip_amount,
    CAST(total_amount AS numeric(10, 2)) AS total_amount,
    EXTRACT(
        EPOCH FROM (dropoff_datetime - pickup_datetime)
    ) / 60.0 AS trip_duration_minutes,
    DATE_TRUNC('hour', pickup_datetime) AS pickup_hour,
    CAST(EXTRACT(DOW FROM pickup_datetime) AS smallint) AS pickup_day_of_week,
    CAST(EXTRACT(HOUR FROM pickup_datetime) AS smallint) AS pickup_hour_of_day

FROM {{ source('bronze', 'raw_trips') }}

WHERE
    trip_distance > 0
    AND pu_location_id IS NOT NULL
    AND do_location_id IS NOT NULL
    AND fare_amount >= 0
    AND total_amount >= 0
    AND dropoff_datetime > pickup_datetime
