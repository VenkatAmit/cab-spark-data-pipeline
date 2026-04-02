{{
    config(
        materialized='view'
    )
}}

SELECT
    location_id,
    borough,
    zone,
    service_zone,
    ingested_at

FROM {{ source('bronze', 'raw_zones') }}

WHERE location_id IS NOT NULL
