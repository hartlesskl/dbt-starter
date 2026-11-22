{#-
  Generic warn test: lists the columns a landing table holds that its source YAML does not
  declare. The landing contract keeps an undeclared column as text instead of failing the load,
  so this test is how a team learns that a new column needs a decision (map it or ignore it).
  Attach it to a source table with `config: {severity: warn}`. One row is returned per
  undeclared column, so the failure count is the number of such columns.

  The declared names come from the graph, which exists only at execution time, so the lookup is
  guarded by `execute`: a clean parse has no graph to read (and the test then reads nothing).
-#}
{% test undeclared_columns(model) %}
  {%- set declared = [] -%}
  {%- if execute -%}
    {%- for node in graph.get('sources', {}).values() -%}
      {%- if (node.schema | lower) == (model.schema | lower) and (node.identifier | lower) == (model.identifier | lower) -%}
        {%- for column_name in node.columns.keys() -%}
          {%- do declared.append(column_name | lower) -%}
        {%- endfor -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endif -%}

select column_name
from information_schema.columns
where lower(table_schema) = '{{ model.schema | lower | replace("'", "''") }}'
    and lower(table_name) = '{{ model.identifier | lower | replace("'", "''") }}'
    {%- if declared | length > 0 %}
    and lower(column_name) not in (
        {%- for name in declared -%}
        '{{ name | replace("'", "''") }}'{% if not loop.last %}, {% endif %}
        {%- endfor -%}
    )
    {%- endif %}
{% endtest %}
