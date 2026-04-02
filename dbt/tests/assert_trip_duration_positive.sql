/*
Custom singular test: trip_duration_minutes must always be positive.

The staging filter (dropoff_datetime > pickup_datetime) should prevent
negative durations from reaching silver — this test verifies that guard
is working correctly.

Returns rows that violate the assertion. dbt fails the test if any
rows are returned.
*/

select
    trip_id,
    pickup_datetime,
    dropoff_datetime,
    trip_duration_minutes

from {{ ref('silver_trips') }}

where trip_duration_minutes <= 0
