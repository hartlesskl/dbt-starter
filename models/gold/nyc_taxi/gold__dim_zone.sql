select
    location_id,
    borough,
    zone,
    service_zone,
    is_unknown_location,
    is_airport
from {{ ref('silver__taxi_zones') }}
