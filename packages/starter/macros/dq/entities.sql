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

{#- A string rendered as the body of a SQL literal (the caller supplies the surrounding quotes).
    The engines escape differently. DuckDB doubles each single quote. Spark reads `''` as two
    adjacent literals, which drops the quote, and reads a backslash as an escape, so Spark
    doubles each backslash and then escapes each quote with a backslash. Both rules are the
    ones `starter.string_literal` applies. -#}
{% macro dq_literal(text) %}
  {{- return(adapter.dispatch('dq_literal', 'starter')(text)) -}}
{% endmacro %}

{% macro default__dq_literal(text) %}
  {{- exceptions.raise_compiler_error('starter.dq_literal is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__dq_literal(text) -%}
{{ text | string | replace("'", "''") }}
{%- endmacro %}

{% macro fabricspark__dq_literal(text) -%}
{{ text | string | replace("\\", "\\\\") | replace("'", "\\'") }}
{%- endmacro %}
