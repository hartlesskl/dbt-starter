{% set rules = rules__nyc_taxi__taxi_zones() %}
{% do starter.validate_rules(rules, 'ZONE_') %}

with src as (

    select
        locationid,
        borough,
        zone,
        service_zone,
        _source_file,
        _source_row_number,
        _batch_id,
        _loaded_at
    from {{ source('nyc_taxi', 'taxi_zones') }}

),

typed as (

    select
        {{ starter.hash_key(['s._source_file', 's._source_row_number']) }} as zone_key,
        s.locationid as location_id__raw,
        {{ starter.parse_int('s.locationid') }} as location_id,
        s.borough,
        s.zone,
        s.service_zone,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at
    from src s

),

flagged as (

    select
        t.*,
        {{ starter.flag_columns(rules) }}
    from typed t

),

final as (

    select
        f.*,
        {{ starter.valid_expr(rules) }} as dq_is_valid,
        {{ starter.failed_count_expr(rules) }} as dq_failed_rule_count,
        {{ starter.reason_codes_expr(rules) }} as dq_reason_codes
    from flagged f

)

select * from final
