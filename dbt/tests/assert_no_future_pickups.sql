/*
Custom singular test: pickup_datetime must not be in the future.

Catches clock-skew or bad source data before it reaches gold.

Returns rows that violate the assertion.
*/

select
    trip_id,
    pickup_datetime,
    partition_date

from {{ ref('silver_trips') }}

where pickup_datetime > current_timestamp
