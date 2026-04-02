{{
    config(
        materialized='table',
        on_schema_change='fail'
    )
}}

SELECT
    location_id,
    borough,
    zone,
    service_zone,
    ingested_at,
    borough = 'Manhattan' AS is_manhattan,
    COALESCE(
        NULLIF(
            CASE service_zone
                WHEN 'Yellow Zone' THEN 'yellow'
                WHEN 'Boro Zone' THEN 'boro'
                WHEN 'EWR' THEN 'ewr'
            END,
            NULL
        ),
        'unknown'
    ) AS service_zone_code,
    CURRENT_TIMESTAMP AS transformed_at

FROM {{ ref('stg_zones') }}
