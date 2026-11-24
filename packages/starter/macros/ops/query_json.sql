{#-
  Run operation: runs each named SQL statement and prints one line per query, so a script can
  read the rows back from any target. Call it as
  `dbt run-operation starter.query_json --args '{queries: {name: "select 1"}}'`.

  Each line is `@@STARTER_JSON@@ ` followed by a JSON object with the keys name, columns and
  rows. Every value is its string form and a NULL is JSON null, so a timestamp or a count reads
  the same on DuckDB and on Spark. Queries run in sorted name order, so the output is stable.

  The macro is not dispatched: it uses only `run_query`, `log` and `tojson`, which both
  adapters provide. The caller passes code-authored SQL only (the Python bridge checks the
  names), and no `%` sign, because the Spark adapter formats `%` only when parameters are passed.
-#}
{% macro query_json(queries) %}
  {%- if execute -%}
    {%- for name in queries.keys() | sort -%}
      {%- set table = run_query(queries[name]) -%}
      {%- set rows = [] -%}
      {%- for row in table.rows -%}
        {%- set cells = [] -%}
        {%- for value in row.values() -%}
          {%- do cells.append(none if value is none else value | string) -%}
        {%- endfor -%}
        {%- do rows.append(cells) -%}
      {%- endfor -%}
      {%- do log('@@STARTER_JSON@@ ' ~ tojson({'name': name, 'columns': table.column_names | list, 'rows': rows}), info=True) -%}
    {%- endfor -%}
  {%- endif -%}
{% endmacro %}
