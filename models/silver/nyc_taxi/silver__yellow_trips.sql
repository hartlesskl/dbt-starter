-- trip_duration_seconds is selected from bronze, never recomputed: bronze computes it from
-- clamped timestamps, and a second computation here could overflow on a T-SQL target.
-- The two unknown-zone marks use the same rule as is_unknown_location in silver.taxi_zones
-- (ids 264 and 265). They come last because the linter wants calculated columns after plain ones.
select
    trip_key,
    _source_file,
    _source_row_number,
    _batch_id,
    _loaded_at,
    duplicate_fingerprint,
    vendor_id,
    pickup_datetime,
    dropoff_datetime,
    trip_duration_seconds,
    passenger_count,
    trip_distance,
    rate_code_id,
    store_and_fwd_flag,
    pu_location_id,
    do_location_id,
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
    dq_warning_count,
    dq_warning_codes,
    case when pu_location_id in (264, 265) then 1 else 0 end as pu_is_unknown_zone,
    case when do_location_id in (264, 265) then 1 else 0 end as do_is_unknown_zone
from {{ ref('bronze__nyc_taxi__yellow_trips') }}
where dq_is_valid = 1
