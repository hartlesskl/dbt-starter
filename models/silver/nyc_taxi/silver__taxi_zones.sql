select
    zone_key,
    _source_file,
    _source_row_number,
    _batch_id,
    location_id,
    borough,
    zone,
    service_zone,
    case when location_id in (264, 265) then 1 else 0 end as is_unknown_location,
    case when service_zone in ('Airports', 'EWR') then 1 else 0 end as is_airport
from {{ ref('bronze__nyc_taxi__taxi_zones') }}
where dq_is_valid = 1
