-- trip_duration_seconds is selected from bronze, never recomputed: bronze computes it from
-- clamped timestamps, and a second computation here could overflow on a T-SQL target.
select
    trip_key,
    _source_file,
    _source_row_number,
    _batch_id,
    _loaded_at,
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
    dq_warning_codes
from {{ ref('bronze__nyc_taxi__yellow_trips') }}
where dq_is_valid = 1
