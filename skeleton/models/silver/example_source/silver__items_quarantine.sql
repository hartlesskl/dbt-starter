select
    item_key,
    _source_file,
    _source_row_number,
    _batch_id,
    _loaded_at,
    id__raw,
    item_name,
    amount__raw,
    updated_at__raw,
    dq_fail_item_id_missing,
    dq_fail_item_id_unparseable,
    dq_fail_item_amount_unparseable,
    dq_fail_item_updated_at_unparseable,
    dq_failed_rule_count,
    dq_reason_codes
from {{ ref('bronze__example_source__items') }}
where dq_is_valid = 0
