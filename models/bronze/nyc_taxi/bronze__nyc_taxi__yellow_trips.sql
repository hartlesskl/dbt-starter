{% set rules = rules__nyc_taxi__yellow_trips() %}
{% do starter.validate_rules(rules, 'TRIP_') %}

{#- Exact duplicates (TRIP_EXACT_DUPLICATE, D-09 and D-10).
    Two rows are duplicates when every declared landed data column has the same text. The
    fingerprint hashes the landing text, not the typed values: starter.hash_key writes NULL as a
    sentinel, so two NULL cells are equal and a NULL differs from the text of an empty string
    (an empty cell lands as NULL, so it is a NULL here). Lineage columns are not in the
    fingerprint, so a trip published again in another file is a duplicate.
    The joiner of starter.hash_key is "-", which timestamps and negative numbers also contain,
    so two different rows could in theory hash to one text if a boundary shifted between two
    columns. Each position holds a fixed-shape value, so this is a theoretical false duplicate
    and not an observed one (RESEARCH Pitfall 16).
    D-10 as clarified on 2026-11-22: the fingerprint covers every DECLARED landed data column.
    A landing column nobody declared stays out of bronze (D-11), so it stays out of the
    fingerprint: rows that differ only in it count as exact duplicates until it is declared in
    the source YAML and in the src column list below. Declaring it is the one step that adds it.
    This also keeps the model free of any database lookup at compile time, which the offline
    Fabric compile cannot make.
    D-09 as amended on 2026-11-22 (in-month copy first): a copy whose pickup month equals the
    month of its file ranks before a copy that does not, and ties fall back to file name and row
    number. A cross-file copy that is out of month in one file therefore never displaces the
    in-month copy, and the real trip stays in silver exactly once. When no copy is in month, the
    copy in the earliest file and row keeps rank 1 and every copy is quarantined (it fails the
    out-of-month rule). The leading key is the negation of the TRIP_PICKUP_OUT_OF_MONTH
    comparison written as an integer: a NULL pickup or an unparseable file month gives 1, so
    those copies fall back to file and row order. -#}

{#- Lookups (D-02). The code seeds and the valid zones are joined through select distinct
    derived tables, so a duplicated seed row cannot multiply the trips. The zones come from
    bronze zones where dq_is_valid = 1, not from landing and not from silver: silver zones is
    exactly that filter, so a zone that is quarantined (a duplicated id, say) is unknown to the
    trips as well, and a later relationship test from trips to zones cannot meet a trip whose
    zone is missing. Reading silver would invert the layers, and landing would accept zones that
    silver rejects. This bronze-to-bronze read is the one named edge in ALLOWED_EDGES of
    tests/conventions/test_layer_direction.py. -#}

{#- Timestamps are clamped to this window before any duration is computed. A seconds count
    must stay inside a 32-bit int on both engines, and an int overflows past about 68 years
    (2,147,483,647 seconds). The published files hold pickups in 2001 and dropoffs in 2088. A
    50-year window keeps the count there, and saturating (not NULL-ing) keeps a far-future
    dropoff measurably long. -#}
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
        -- the text of the 20 declared landing data columns, lineage left out
        {{ starter.hash_key([
            's.vendorid',
            's.tpep_pickup_datetime',
            's.tpep_dropoff_datetime',
            's.passenger_count',
            's.trip_distance',
            's.ratecodeid',
            's.store_and_fwd_flag',
            's.pulocationid',
            's.dolocationid',
            's.payment_type',
            's.fare_amount',
            's.extra',
            's.mta_tax',
            's.tip_amount',
            's.tolls_amount',
            's.improvement_surcharge',
            's.total_amount',
            's.congestion_surcharge',
            's.airport_fee',
            's.cbd_congestion_fee'
        ]) }} as duplicate_fingerprint,
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
    from src as s

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
    from typed as t

),

looked_up as (

    select
        c.*,
        -- the only place the duration is computed, and only from the clamped pair
        {{ starter.datediff('second', 'c.pickup_clamped', 'c.dropoff_clamped') }} as trip_duration_seconds,
        -- 1 for the copy that is kept: the in-month copy first, then file name, then row number
        row_number() over (
            partition by c.duplicate_fingerprint
            order by
                case when c.pickup_month = c.source_month then 0 else 1 end,
                c._source_file,
                c._source_row_number
        ) as duplicate_rank,
        case when pz.location_id is null then 0 else 1 end as pu_zone_found,
        case when dz.location_id is null then 0 else 1 end as do_zone_found,
        case when v.vendor_id is null then 0 else 1 end as vendor_found,
        case when r.rate_code_id is null then 0 else 1 end as rate_code_found,
        case when p.payment_type is null then 0 else 1 end as payment_type_found
    from clamped as c
    left join (
        select distinct location_id
        from {{ ref('bronze__nyc_taxi__taxi_zones') }}
        where dq_is_valid = 1
    ) as pz
        on c.pu_location_id = pz.location_id
    left join (
        select distinct location_id
        from {{ ref('bronze__nyc_taxi__taxi_zones') }}
        where dq_is_valid = 1
    ) as dz
        on c.do_location_id = dz.location_id
    left join (select distinct vendor_id from {{ ref('nyc_taxi__vendors') }}) as v
        on c.vendor_id = v.vendor_id
    left join (select distinct rate_code_id from {{ ref('nyc_taxi__rate_codes') }}) as r
        on c.rate_code_id = r.rate_code_id
    left join (select distinct payment_type from {{ ref('nyc_taxi__payment_types') }}) as p
        on c.payment_type = p.payment_type

),

flagged as (

    select
        l.*,
        {{ starter.flag_columns(rules) }}
    from looked_up as l

),

final as (

    select
        f.*,
        {{ starter.valid_expr(rules) }} as dq_is_valid,
        {{ starter.failed_count_expr(rules) }} as dq_failed_rule_count,
        {{ starter.reason_codes_expr(rules) }} as dq_reason_codes,
        -- warn rules never change dq_is_valid: the three expressions above read error flags only
        {{ starter.warning_count_expr(rules) }} as dq_warning_count,
        {{ starter.warning_codes_expr(rules) }} as dq_warning_codes
    from flagged as f

)

select * from final
