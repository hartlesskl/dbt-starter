{#-
  Calendar shims. They exist because the two engines disagree on the basics of dates, and the
  calendar dimension and the fact that joins to it must compute the same key on both.

  - date_part: DuckDB year(), quarter(), month() and day() return BIGINT and Spark's return
    int. Casting both to int keeps declared contract types equal.
  - date_add_days: DuckDB rejects a date plus a BIGINT, and Spark's date_add takes an int, so
    each engine gets its own spelling that returns a date.
  - date_key: the integer YYYYMMDD of a date or timestamp, built by arithmetic from date_part
    on both engines (never from engine formatting), so the dimension and the fact cannot drift.
  - iso_weekday: Monday = 1 to Sunday = 7 by an explicit formula from a known Monday, never an
    engine weekday function or a session setting, which change the answer.

  Each shim has the same three parts as the shims in parse.sql: a public macro that
  dispatches on the adapter, a `default__` that raises, and explicit `duckdb__` and
  `fabricspark__` versions.
-#}

{% macro date_part(part, expr) %}
  {%- if part not in ['year', 'quarter', 'month', 'day'] -%}
    {{- exceptions.raise_compiler_error('starter.date_part accepts year, quarter, month or day, not ' ~ part) -}}
  {%- endif -%}
  {{- return(adapter.dispatch('date_part', 'starter')(part, expr)) -}}
{% endmacro %}

{% macro default__date_part(part, expr) %}
  {{- exceptions.raise_compiler_error('starter.date_part is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__date_part(part, expr) -%}
cast({{ part }}({{ expr }}) as int)
{%- endmacro %}

{% macro fabricspark__date_part(part, expr) -%}
cast({{ part }}({{ expr }}) as int)
{%- endmacro %}


{% macro date_add_days(date_expr, days_expr) %}
  {{- return(adapter.dispatch('date_add_days', 'starter')(date_expr, days_expr)) -}}
{% endmacro %}

{% macro default__date_add_days(date_expr, days_expr) %}
  {{- exceptions.raise_compiler_error('starter.date_add_days is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- The day count may be negative. The result is always a date. -#}
{% macro duckdb__date_add_days(date_expr, days_expr) -%}
cast({{ date_expr }} + cast({{ days_expr }} as int) as date)
{%- endmacro %}

{% macro fabricspark__date_add_days(date_expr, days_expr) -%}
cast(date_add({{ date_expr }}, cast({{ days_expr }} as int)) as date)
{%- endmacro %}


{% macro date_key(expr) %}
  {{- return(adapter.dispatch('date_key', 'starter')(expr)) -}}
{% endmacro %}

{% macro default__date_key(expr) %}
  {{- exceptions.raise_compiler_error('starter.date_key is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- year * 10000 + month * 100 + day, for example 20240115. Both versions render the same
    text on purpose. -#}
{% macro duckdb__date_key(expr) -%}
cast({{ starter.date_part('year', expr) }} * 10000 + {{ starter.date_part('month', expr) }} * 100 + {{ starter.date_part('day', expr) }} as int)
{%- endmacro %}

{% macro fabricspark__date_key(expr) -%}
cast({{ starter.date_part('year', expr) }} * 10000 + {{ starter.date_part('month', expr) }} * 100 + {{ starter.date_part('day', expr) }} as int)
{%- endmacro %}


{% macro iso_weekday(date_expr) %}
  {{- return(adapter.dispatch('iso_weekday', 'starter')(date_expr)) -}}
{% endmacro %}

{% macro default__iso_weekday(date_expr) %}
  {{- exceptions.raise_compiler_error('starter.iso_weekday is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- 1900-01-01 was a Monday. Days since then, modulo 7, plus 1 gives Monday = 1. The second
    modulo keeps dates before 1900 correct, because a negative number has a negative remainder
    on both engines. -#}
{% macro duckdb__iso_weekday(date_expr) -%}
cast(((({{ starter.datediff('day', starter.parse_date("'1900-01-01'"), date_expr) }}) % 7) + 7) % 7 + 1 as int)
{%- endmacro %}

{% macro fabricspark__iso_weekday(date_expr) -%}
cast(((({{ starter.datediff('day', starter.parse_date("'1900-01-01'"), date_expr) }}) % 7) + 7) % 7 + 1 as int)
{%- endmacro %}
