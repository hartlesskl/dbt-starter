{#-
  Flattening helper for the cross-entity data quality models.

  An entity is a dict {entity, relation, row_key, rules}: its name, the bronze relation that
  carries one flag column per rule, the column that keys a row, and its rule registry.
  Jinja has no way to read the outer loop variable from an inner loop (no `loop.parent`), so the
  models read one flat list instead: one dict {entity, relation, row_key, rule} per rule, in
  entity order and then registry order.
-#}
{% macro dq_rule_pairs(entities) -%}
  {%- set pairs = [] -%}
  {%- for e in entities -%}
    {%- for r in e.rules -%}
      {%- do pairs.append({'entity': e.entity, 'relation': e.relation, 'row_key': e.row_key, 'rule': r}) -%}
    {%- endfor -%}
  {%- endfor -%}
  {{- return(pairs) -}}
{%- endmacro %}

{#- A string rendered as a SQL literal body: every single quote is doubled. -#}
{% macro dq_literal(text) -%}
{{ text | string | replace("'", "''") }}
{%- endmacro %}
