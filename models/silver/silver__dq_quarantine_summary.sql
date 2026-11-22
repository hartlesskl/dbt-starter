{#- Counts and rates by entity and rule. Each entity's bronze table is scanned once, in its own
    CTE named from the entity's position, and every rule then reads its counts from that CTE.
    `failed_rows` counts a row once per rule it fails; `entity_quarantined_rows` counts a row
    once, however many error rules it fails. -#}
{% set entities = dq_entities() %}
{% set pairs = starter.dq_rule_pairs(entities) %}
{% if pairs | length == 0 %}
select
    cast(null as {{ starter.type_varchar(64) }}) as entity,
    cast(null as {{ starter.type_varchar(64) }}) as rule_code,
    cast(null as {{ starter.type_varchar(8) }}) as severity,
    cast(null as bigint) as failed_rows,
    cast(null as decimal(18, 6)) as failed_rate,
    cast(null as bigint) as entity_rows,
    cast(null as bigint) as entity_quarantined_rows,
    cast(null as decimal(18, 6)) as entity_quarantine_rate,
    cast(null as bigint) as entity_warned_rows,
    cast(null as decimal(18, 6)) as entity_warned_rate
where 1 = 0
{% else %}
{% set cte_of = {} %}
with
{% for e in entities %}
{% do cte_of.update({e.entity: 'entity_' ~ loop.index}) %}
{{ cte_of[e.entity] }} as (
    select
        count(*) as entity_rows,
        coalesce(sum(case when dq_is_valid = 0 then 1 else 0 end), 0) as entity_quarantined_rows,
        coalesce(sum(case when {{ starter.warning_count_expr(e.rules) }} > 0 then 1 else 0 end), 0) as entity_warned_rows,
        {% for r in e.rules %}
        coalesce(sum(cast({{ starter.flag_name(r) }} as bigint)), 0) as {{ starter.flag_name(r) }}{% if not loop.last %},{% endif %}
        {% endfor %}
    from {{ e.relation }}
){% if not loop.last %},{% endif %}
{% endfor %}
{% for p in pairs %}
{% set counts = cte_of[p.entity] %}
select
    cast('{{ starter.dq_literal(p.entity) }}' as {{ starter.type_varchar(64) }}) as entity,
    cast('{{ starter.dq_literal(p.rule.code) }}' as {{ starter.type_varchar(64) }}) as rule_code,
    cast('{{ starter.dq_literal(p.rule.severity) }}' as {{ starter.type_varchar(8) }}) as severity,
    cast({{ counts }}.{{ starter.flag_name(p.rule) }} as bigint) as failed_rows,
    {{ starter.safe_divide(counts ~ '.' ~ starter.flag_name(p.rule), counts ~ '.entity_rows') }} as failed_rate,
    cast({{ counts }}.entity_rows as bigint) as entity_rows,
    cast({{ counts }}.entity_quarantined_rows as bigint) as entity_quarantined_rows,
    {{ starter.safe_divide(counts ~ '.entity_quarantined_rows', counts ~ '.entity_rows') }} as entity_quarantine_rate,
    cast({{ counts }}.entity_warned_rows as bigint) as entity_warned_rows,
    {{ starter.safe_divide(counts ~ '.entity_warned_rows', counts ~ '.entity_rows') }} as entity_warned_rate
from {{ counts }}
{% if not loop.last %}
union all
{% endif %}
{% endfor %}
{% endif %}
