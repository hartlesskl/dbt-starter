{#- One row per registered rule, written as literals from the registries, so each data problem
    behind a rule can be looked up. Every registry string has its single quotes doubled. -#}
{% set pairs = starter.dq_rule_pairs(dq_entities()) %}
{% if pairs | length == 0 %}
select
    cast(null as {{ starter.type_varchar(64) }}) as code,
    cast(null as {{ starter.type_varchar(64) }}) as entity,
    cast(null as {{ starter.type_varchar(8) }}) as severity,
    cast(null as {{ starter.type_varchar(8) }}) as on_null,
    cast(null as {{ starter.type_varchar(1000) }}) as description,
    cast(null as {{ starter.type_varchar(4000) }}) as rationale
where 1 = 0
{% else %}
{% for p in pairs %}
select
    cast('{{ starter.dq_literal(p.rule.code) }}' as {{ starter.type_varchar(64) }}) as code,
    cast('{{ starter.dq_literal(p.entity) }}' as {{ starter.type_varchar(64) }}) as entity,
    cast('{{ starter.dq_literal(p.rule.severity) }}' as {{ starter.type_varchar(8) }}) as severity,
    cast('{{ starter.dq_literal(p.rule.on_null) }}' as {{ starter.type_varchar(8) }}) as on_null,
    cast('{{ starter.dq_literal(p.rule.description) }}' as {{ starter.type_varchar(1000) }}) as description,
    cast('{{ starter.dq_literal(p.rule.rationale) }}' as {{ starter.type_varchar(4000) }}) as rationale
{% if not loop.last %}
union all
{% endif %}
{% endfor %}
{% endif %}
