-- Read straight from the seed: gold may read a seed, and a thin silver model between them
-- would add a model with no behaviour. The widths equal the seed's column_types, so a
-- T-SQL cast cannot truncate a published name.
select
    cast(rate_code_id as int) as rate_code_id,
    cast(rate_code_name as varchar(60)) as rate_code_name
from {{ ref('nyc_taxi__rate_codes') }}
