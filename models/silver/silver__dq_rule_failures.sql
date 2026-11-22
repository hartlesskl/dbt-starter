{#- One row per failed rule and row key, for warn and error rules alike. Count from this table,
    never from the display strings in dq_reason_codes. -#}
{% set pairs = starter.dq_rule_pairs(dq_entities()) %}
{% if pairs | length == 0 %}
select
    cast(null as {{ starter.type_varchar(64) }}) as entity,
    cast(null as {{ starter.type_varchar(64) }}) as row_key,
    cast(null as {{ starter.type_varchar(255) }}) as _source_file,
    cast(null as {{ starter.type_varchar(64) }}) as _batch_id,
    cast(null as {{ starter.type_varchar(64) }}) as rule_code,
    cast(null as {{ starter.type_varchar(8) }}) as severity
where 1 = 0
{% else %}
{% for p in pairs %}
select
    cast('{{ starter.dq_literal(p.entity) }}' as {{ starter.type_varchar(64) }}) as entity,
    cast({{ p.row_key }} as {{ starter.type_varchar(64) }}) as row_key,
    cast(_source_file as {{ starter.type_varchar(255) }}) as _source_file,
    cast(_batch_id as {{ starter.type_varchar(64) }}) as _batch_id,
    cast('{{ starter.dq_literal(p.rule.code) }}' as {{ starter.type_varchar(64) }}) as rule_code,
    cast('{{ starter.dq_literal(p.rule.severity) }}' as {{ starter.type_varchar(8) }}) as severity
from {{ p.relation }}
where {{ starter.flag_name(p.rule) }} = 1
{% if not loop.last %}
union all
{% endif %}
{% endfor %}
{% endif %}
