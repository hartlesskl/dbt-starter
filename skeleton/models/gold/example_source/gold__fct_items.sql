-- One row per item in silver. Every column is cast to its type, so a change upstream cannot
-- change the gold type unnoticed.
select
    cast(item_key as varchar(32)) as item_key,
    cast(item_id as int) as item_id,
    cast(item_name as varchar(200)) as item_name,
    cast(amount as decimal(18, 6)) as amount,
    cast(updated_at as timestamp) as updated_at
from {{ ref('silver__items') }}
