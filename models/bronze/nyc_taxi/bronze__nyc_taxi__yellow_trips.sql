{% set rules = rules__nyc_taxi__yellow_trips() %}
{% do starter.validate_rules(rules, 'TRIP_') %}

{#- Timestamps are clamped to this window before any duration is computed. A T-SQL seconds
    datediff returns an int and overflows past about 68 years (2,147,483,647 seconds), and the
    published files hold pickups in 2001 and dropoffs in 2088. A 50-year window is safe, and
    saturating (not NULL-ing) keeps a far-future dropoff measurably long. -#}
{% set clamp_floor = starter.parse_timestamp("'2000-01-01 00:00:00'") %}
{% set clamp_ceiling = starter.parse_timestamp("'2050-01-01 00:00:00'") %}

with src as (

    select
        vendorid,
        tpep_pickup_datetime,
        tpep_dropoff_datetime,
        passenger_count,
        trip_distance,
        ratecodeid,
        store_and_fwd_flag,
        pulocationid,
        dolocationid,
        payment_type,
        fare_amount,
        extra,
        mta_tax,
        tip_amount,
        tolls_amount,
        improvement_surcharge,
        total_amount,
        congestion_surcharge,
        airport_fee,
        cbd_congestion_fee,
        _source_file,
        _source_row_number,
        _batch_id,
        _loaded_at
    from {{ source('nyc_taxi', 'yellow_trips') }}

),

typed as (

    select
        {{ starter.hash_key(['s._source_file', 's._source_row_number']) }} as trip_key,
        s.vendorid as vendor_id__raw,
        {{ starter.parse_int('s.vendorid') }} as vendor_id,
        s.tpep_pickup_datetime as pickup_datetime__raw,
        {{ starter.parse_timestamp('s.tpep_pickup_datetime') }} as pickup_datetime,
        s.tpep_dropoff_datetime as dropoff_datetime__raw,
        {{ starter.parse_timestamp('s.tpep_dropoff_datetime') }} as dropoff_datetime,
        s.passenger_count as passenger_count__raw,
        {{ starter.parse_int('s.passenger_count') }} as passenger_count,
        s.trip_distance as trip_distance__raw,
        {{ starter.parse_decimal('s.trip_distance') }} as trip_distance,
        s.ratecodeid as rate_code_id__raw,
        {{ starter.parse_int('s.ratecodeid') }} as rate_code_id,
        s.store_and_fwd_flag,
        s.pulocationid as pu_location_id__raw,
        {{ starter.parse_int('s.pulocationid') }} as pu_location_id,
        s.dolocationid as do_location_id__raw,
        {{ starter.parse_int('s.dolocationid') }} as do_location_id,
        s.payment_type as payment_type__raw,
        {{ starter.parse_int('s.payment_type') }} as payment_type,
        s.fare_amount as fare_amount__raw,
        {{ starter.parse_decimal('s.fare_amount') }} as fare_amount,
        s.extra as extra__raw,
        {{ starter.parse_decimal('s.extra') }} as extra,
        s.mta_tax as mta_tax__raw,
        {{ starter.parse_decimal('s.mta_tax') }} as mta_tax,
        s.tip_amount as tip_amount__raw,
        {{ starter.parse_decimal('s.tip_amount') }} as tip_amount,
        s.tolls_amount as tolls_amount__raw,
        {{ starter.parse_decimal('s.tolls_amount') }} as tolls_amount,
        s.improvement_surcharge as improvement_surcharge__raw,
        {{ starter.parse_decimal('s.improvement_surcharge') }} as improvement_surcharge,
        s.total_amount as total_amount__raw,
        {{ starter.parse_decimal('s.total_amount') }} as total_amount,
        s.congestion_surcharge as congestion_surcharge__raw,
        {{ starter.parse_decimal('s.congestion_surcharge') }} as congestion_surcharge,
        s.airport_fee as airport_fee__raw,
        {{ starter.parse_decimal('s.airport_fee') }} as airport_fee,
        s.cbd_congestion_fee as cbd_congestion_fee__raw,
        {{ starter.parse_decimal('s.cbd_congestion_fee') }} as cbd_congestion_fee,
        -- the file name carries the month: yellow_tripdata_YYYY-MM.parquet, month from character 17
        {{ starter.parse_date(starter.concat(["substring(s._source_file, 17, 7)", "'-01'"])) }} as source_month,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at
    from src s

),

clamped as (

    select
        t.*,
        {{ starter.date_trunc('month', 't.pickup_datetime') }} as pickup_month,
        case
            when t.pickup_datetime < {{ clamp_floor }} then {{ clamp_floor }}
            when t.pickup_datetime > {{ clamp_ceiling }} then {{ clamp_ceiling }}
            else t.pickup_datetime
        end as pickup_clamped,
        case
            when t.dropoff_datetime < {{ clamp_floor }} then {{ clamp_floor }}
            when t.dropoff_datetime > {{ clamp_ceiling }} then {{ clamp_ceiling }}
            else t.dropoff_datetime
        end as dropoff_clamped
    from typed t

),

looked_up as (

    select
        c.*,
        -- the only place the duration is computed, and only from the clamped pair
        {{ starter.datediff('second', 'c.pickup_clamped', 'c.dropoff_clamped') }} as trip_duration_seconds
    from clamped c

),

flagged as (

    select
        l.*,
        {{ starter.flag_columns(rules) }}
    from looked_up l

),

final as (

    select
        f.*,
        {{ starter.valid_expr(rules) }} as dq_is_valid,
        {{ starter.failed_count_expr(rules) }} as dq_failed_rule_count,
        {{ starter.reason_codes_expr(rules) }} as dq_reason_codes,
        -- no warn rule is registered yet; these are the values an empty warn list gives, which
        -- plan 02-03 renders through starter.warning_count_expr(rules) and
        -- starter.warning_codes_expr(rules)
        0 as dq_warning_count,
        cast(null as {{ starter.type_varchar(1) }}) as dq_warning_codes
    from flagged f

)

select * from final
