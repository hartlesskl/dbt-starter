-- Read straight from the seed: gold may read a seed, and a thin silver model between them
-- would add a model with no behaviour. The widths equal the seed's column_types and are sized
-- to the longest published name, so neither engine can cut a name short.
select
    cast(payment_type as int) as payment_type,
    cast(payment_type_name as varchar(60)) as payment_type_name
from {{ ref('nyc_taxi__payment_types') }}
