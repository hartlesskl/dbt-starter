{#-
  One row per pickup day and pickup borough, built from the star (the fact and the zone
  dimension), not from silver. The decisions:

  - The join to gold.dim_zone is an inner join. Silver guarantees every trip a valid pickup
    zone, so the join cannot drop a trip. If it ever did, the reconciliation test would fail
    the build; a left join would hide the loss behind a NULL borough. Zones 264 and 265 are in
    the dimension under the boroughs Unknown and N/A, so their trips roll up there (D-08).
  - Sums are wrapped in coalesce(..., 0) so a day whose amounts are all NULL still has a total.
  - Counts are cast to bigint before and after summing. DuckDB sums an int into HUGEINT and
    T-SQL keeps int, which can overflow on a large month.
  - An average divides by the number of non-NULL values of its own column, through
    the safe_divide shim, never a bare division (D-09). A day with no fare has a NULL average.
  The two helper counts (fare_count and distance_count) are not selected.
-#}
with trips as (
    select
        f.pickup_date_key,
        z.borough as pickup_borough,
        f.dq_warning_count,
        f.passenger_count,
        f.trip_distance,
        f.fare_amount,
        f.tip_amount,
        f.total_amount
    from {{ ref('gold__fct_trips') }} as f
    inner join {{ ref('gold__dim_zone') }} as z
        on f.pu_location_id = z.location_id
),

rolled as (
    select
        pickup_date_key,
        pickup_borough,
        cast(count(*) as bigint) as trip_count,
        cast(sum(case when dq_warning_count > 0 then 1 else 0 end) as bigint) as warned_trip_count,
        cast(coalesce(sum(cast(passenger_count as bigint)), 0) as bigint) as total_passengers,
        cast(coalesce(sum(trip_distance), 0) as decimal(38, 6)) as total_distance,
        cast(coalesce(sum(fare_amount), 0) as decimal(38, 6)) as fare_amount_sum,
        cast(coalesce(sum(tip_amount), 0) as decimal(38, 6)) as tip_amount_sum,
        cast(coalesce(sum(total_amount), 0) as decimal(38, 6)) as total_amount_sum,
        cast(count(fare_amount) as bigint) as fare_count,
        cast(count(trip_distance) as bigint) as distance_count
    from trips
    group by pickup_date_key, pickup_borough
)

select
    cast(pickup_date_key as int) as pickup_date_key,
    cast(pickup_borough as varchar(100)) as pickup_borough,
    cast(trip_count as bigint) as trip_count,
    cast(warned_trip_count as bigint) as warned_trip_count,
    cast(total_passengers as bigint) as total_passengers,
    cast(total_distance as decimal(38, 6)) as total_distance,
    cast(fare_amount_sum as decimal(38, 6)) as fare_amount_sum,
    cast(tip_amount_sum as decimal(38, 6)) as tip_amount_sum,
    cast(total_amount_sum as decimal(38, 6)) as total_amount_sum,
    {{ starter.safe_divide('fare_amount_sum', 'fare_count') }} as avg_fare,
    {{ starter.safe_divide('total_distance', 'distance_count') }} as avg_distance
from rolled
