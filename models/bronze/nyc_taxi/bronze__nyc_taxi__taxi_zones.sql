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
        count(*) over (partition by {{ starter.parse_int('s.locationid') }}) as location_id_count,
        s.borough,
        s.zone,
        s.service_zone,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at,
        case when b.borough is null then 0 else 1 end as borough_found,
        case when sz.service_zone is null then 0 else 1 end as service_zone_found
    from src s
    -- select distinct keeps a duplicated seed row from multiplying the landed rows
    left join (select distinct borough from {{ ref('nyc_taxi__known_boroughs') }}) b
        on s.borough = b.borough
    left join (select distinct service_zone from {{ ref('nyc_taxi__known_service_zones') }}) sz
        on s.service_zone = sz.service_zone

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
