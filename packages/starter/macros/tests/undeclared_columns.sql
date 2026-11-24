{#-
  Generic warn test: lists the columns a landing table holds that its source YAML does not
  declare. The landing contract keeps an undeclared column as text instead of failing the load,
  so this test is how a team learns that a new column needs a decision (map it or ignore it).
  Attach it to a source table with `config: {severity: warn}`. One row is returned per
  undeclared column, so the failure count is the number of such columns.

  The declared names come from the graph, which exists only at execution time, so the lookup is
  guarded by `execute`: a clean parse has no graph to read (and the test then reads nothing).

  The query is dispatched, and both engines now report a new column. The DuckDB body reads
  information_schema. A Fabric Lakehouse has no such view, so the fabricspark body asks the
  adapter for the relation's columns at execution time (`adapter.get_columns_in_relation`) and
  renders them as a `values` list. In compile mode (the offline Fabric compile has no
  connection) it makes no metadata call and renders a typed result with zero rows. The live
  check is `undeclared-columns-live` in ADR 0006.
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

{{ starter.undeclared_columns_sql(model, declared) }}
{% endtest %}


{% macro undeclared_columns_sql(model, declared) %}
  {{- return(adapter.dispatch('undeclared_columns_sql', 'starter')(model, declared)) -}}
{% endmacro %}

{% macro default__undeclared_columns_sql(model, declared) %}
  {{- exceptions.raise_compiler_error('starter.undeclared_columns_sql is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__undeclared_columns_sql(model, declared) -%}
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
{%- endmacro %}

{#-
  The rows of the result as a list of names: a typed zero-row select for none, otherwise a
  `values` list with each name rendered through `starter.string_literal`, so a quote or a
  backslash in a column name means the same on both engines. Not dispatched: both shapes run on
  DuckDB and on Spark.
-#}
{% macro undeclared_columns_rows(names) -%}
{%- if names | length == 0 -%}
select cast(null as string) as column_name where 1 = 0
{%- else -%}
select cast(c as string) as column_name
from (values
    {%- for name in names %}
    ({{ starter.string_literal(name) }}){% if not loop.last %},{% endif %}
    {%- endfor %}
) as t(c)
{%- endif -%}
{%- endmacro %}

{#-
  Spark has no information_schema, so the columns come from the adapter, only when the test
  runs (`test` or `build`). Every other command (the offline compile) leaves the list empty.
-#}
{% macro fabricspark__undeclared_columns_sql(model, declared) -%}
{%- set undeclared = [] -%}
{%- if execute and flags.WHICH in ['test', 'build'] -%}
  {%- for column in adapter.get_columns_in_relation(model) -%}
    {%- if (column.name | lower) not in declared -%}
      {%- do undeclared.append(column.name) -%}
    {%- endif -%}
  {%- endfor -%}
{%- endif -%}
{{ starter.undeclared_columns_rows(undeclared) }}
{%- endmacro %}
