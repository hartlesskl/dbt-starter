{#-
  Text-to-number shims. Landed values are all text, so every typed column starts here.

  Each shim has the same three parts: a public macro that dispatches on the adapter, a
  `default__` that raises (so an unsupported target fails at compile time, not at run time),
  and an explicit `duckdb__` and `fabric__` version. Models call `starter.<name>` and never
  dbt's own cast macros, which compile to different behaviour per target.
-#}

{% macro parse_decimal(expr, precision=18, scale=6) %}
  {{- return(adapter.dispatch('parse_decimal', 'starter')(expr, precision, scale)) -}}
{% endmacro %}

{% macro default__parse_decimal(expr, precision, scale) %}
  {{- exceptions.raise_compiler_error('starter.parse_decimal is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Trim first so ' 7 ' never depends on an engine's own whitespace handling; an empty
    string is NULL; text that is not a number is NULL, never an error. -#}
{% macro duckdb__parse_decimal(expr, precision, scale) -%}
try_cast(nullif(ltrim(rtrim({{ expr }})), '') as decimal({{ precision }}, {{ scale }}))
{%- endmacro %}

{% macro fabric__parse_decimal(expr, precision, scale) -%}
try_cast(nullif(ltrim(rtrim({{ expr }})), '') as decimal({{ precision }}, {{ scale }}))
{%- endmacro %}


{% macro parse_int(expr) %}
  {{- return(adapter.dispatch('parse_int', 'starter')(expr)) -}}
{% endmacro %}

{% macro default__parse_int(expr) %}
  {{- exceptions.raise_compiler_error('starter.parse_int is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Whole numbers only. DuckDB rounds a decimal to int and T-SQL truncates it, so '1.5'
    would be 2 on one engine and 1 on the other. Going through decimal and casting only when
    floor(D) = D makes both engines return NULL for '1.5'. -#}
{% macro duckdb__parse_int(expr) -%}
case when floor({{ starter.parse_decimal(expr) }}) = {{ starter.parse_decimal(expr) }} then try_cast({{ starter.parse_decimal(expr) }} as int) end
{%- endmacro %}

{% macro fabric__parse_int(expr) -%}
case when floor({{ starter.parse_decimal(expr) }}) = {{ starter.parse_decimal(expr) }} then try_cast({{ starter.parse_decimal(expr) }} as int) end
{%- endmacro %}


{% macro parse_date(expr) %}
  {{- return(adapter.dispatch('parse_date', 'starter')(expr)) -}}
{% endmacro %}

{% macro default__parse_date(expr) %}
  {{- exceptions.raise_compiler_error('starter.parse_date is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Trim, then an empty string is NULL, and text that is not a real calendar date
    ('2024-02-30', 'garbage') is NULL, never an error. -#}
{% macro duckdb__parse_date(expr) -%}
try_cast(nullif(ltrim(rtrim({{ expr }})), '') as date)
{%- endmacro %}

{% macro fabric__parse_date(expr) -%}
try_cast(nullif(ltrim(rtrim({{ expr }})), '') as date)
{%- endmacro %}
