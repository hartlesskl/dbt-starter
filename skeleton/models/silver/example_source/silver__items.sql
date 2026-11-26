select
    item_key,
    _source_file,
    _source_row_number,
    _batch_id,
    item_id,
    item_name,
    amount,
    updated_at
from {{ ref('bronze__example_source__items') }}
where dq_is_valid = 1
