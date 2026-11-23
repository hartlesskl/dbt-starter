-- Every column is cast to its contract type. The varchar widths are sized to the longest
-- published name, so neither engine can cut a name short. The noqa on zone is for
-- the linter's keyword rule: zone is the published column name and the contract needs it.
select
    cast(location_id as int) as location_id,
    cast(borough as varchar(100)) as borough,
    cast(zone as varchar(100)) as zone, -- noqa: RF04
    cast(service_zone as varchar(100)) as service_zone,
    cast(is_unknown_location as int) as is_unknown_location,
    cast(is_airport as int) as is_airport
from {{ ref('silver__taxi_zones') }}
