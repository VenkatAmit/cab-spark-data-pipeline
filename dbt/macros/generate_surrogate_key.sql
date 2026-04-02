{% macro generate_surrogate_key(field_list) %}
    /*
    Deterministic surrogate key using MD5.

    This macro is a lightweight fallback used when dbt_utils is not
    available. If dbt_utils is installed (recommended), its version
    of generate_surrogate_key takes precedence and handles nulls more
    robustly.

    Usage:
        {{ generate_surrogate_key(['vendor_id', 'pickup_datetime', 'pu_location_id']) }}
    */

    md5(
        cast(
            {% for field in field_list %}
                coalesce(cast({{ field }} as varchar), '_null_')
                {% if not loop.last %} || '|' || {% endif %}
            {% endfor %}
        as varchar)
    )

{% endmacro %}
