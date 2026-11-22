{#-
  One row per silver trip. The lineage columns (_source_file, _source_row_number, _batch_id,
  _loaded_at and duplicate_fingerprint) stay in silver, and trip_key links back to them (D-07).
  There is one date role, the pickup (D-05). pickup_datetime and dropoff_datetime are selected
  without a cast because starter.parse_timestamp already gives each engine's timestamp type,
  which is what the contract declares (D-21). Every other column is cast to its contract type,
  because dbt compares the declared type with the real one and a mismatch fails the build.
  The two unknown-zone marks of silver are not carried: gold.dim_zone holds that fact.
-#}
select
    pickup_datetime,
    dropoff_datetime,
    cast(trip_key as varchar(32)) as trip_key,
    cast({{ starter.date_key('pickup_datetime') }} as int) as pickup_date_key,
    cast(pu_location_id as int) as pu_location_id,
    cast(do_location_id as int) as do_location_id,
    cast(vendor_id as int) as vendor_id,
    cast(rate_code_id as int) as rate_code_id,
    cast(payment_type as int) as payment_type,
    cast(trip_duration_seconds as int) as trip_duration_seconds,
    cast(passenger_count as int) as passenger_count,
    cast(trip_distance as decimal(18, 6)) as trip_distance,
    cast(store_and_fwd_flag as varchar(20)) as store_and_fwd_flag,
    cast(fare_amount as decimal(18, 6)) as fare_amount,
    cast(extra as decimal(18, 6)) as extra,
    cast(mta_tax as decimal(18, 6)) as mta_tax,
    cast(tip_amount as decimal(18, 6)) as tip_amount,
    cast(tolls_amount as decimal(18, 6)) as tolls_amount,
    cast(improvement_surcharge as decimal(18, 6)) as improvement_surcharge,
    cast(total_amount as decimal(18, 6)) as total_amount,
    cast(congestion_surcharge as decimal(18, 6)) as congestion_surcharge,
    cast(airport_fee as decimal(18, 6)) as airport_fee,
    cast(cbd_congestion_fee as decimal(18, 6)) as cbd_congestion_fee,
    cast(dq_warning_count as int) as dq_warning_count,
    cast(dq_warning_codes as varchar(400)) as dq_warning_codes
from {{ ref('silver__yellow_trips') }}
