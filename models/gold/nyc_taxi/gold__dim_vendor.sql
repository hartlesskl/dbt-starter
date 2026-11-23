-- Read straight from the seed: gold may read a seed, and a thin silver model between them
-- would add a model with no behaviour. The widths equal the seed's column_types and are sized
-- to the longest published name, so neither engine can cut a name short.
select
    cast(vendor_id as int) as vendor_id,
    cast(vendor_name as varchar(60)) as vendor_name
from {{ ref('nyc_taxi__vendors') }}
