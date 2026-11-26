{% set rules = rules__example_source__items() %}
{% do starter.validate_rules(rules, 'ITEM_') %}

with src as (

    select
        id,
        name,
        amount,
        updated_at,
        _source_file,
        _source_row_number,
        _batch_id,
        _loaded_at
    from {{ source('example_source', 'items') }}

),

typed as (

    select
        {{ starter.hash_key(['s._source_file', 's._source_row_number']) }} as item_key,
        s.id as id__raw,
        {{ starter.parse_int('s.id') }} as item_id,
        s.name as item_name,
        s.amount as amount__raw,
        {{ starter.parse_decimal('s.amount') }} as amount,
        s.updated_at as updated_at__raw,
        {{ starter.parse_timestamp('s.updated_at') }} as updated_at,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at
    from src as s

),

flagged as (

    select
        t.*,
        {{ starter.flag_columns(rules) }}
    from typed as t

),

final as (

    select
        f.*,
        {{ starter.valid_expr(rules) }} as dq_is_valid,
        {{ starter.failed_count_expr(rules) }} as dq_failed_rule_count,
        {{ starter.reason_codes_expr(rules) }} as dq_reason_codes
    from flagged as f

)

select * from final
