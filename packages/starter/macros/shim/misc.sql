{#-
  Concatenation and hashing shims, same three-part shape as parse.sql.

  T-SQL CONCAT_WS needs a separator and at least two value arguments, and DuckDB accepts
  one. When a list has fewer than two entries the shims append a typed NULL, which both
  engines skip, so the result is unchanged and T-SQL stays valid.
-#}

{% macro concat_ws(separator, values) %}
  {{- return(adapter.dispatch('concat_ws', 'starter')(separator, values)) -}}
{% endmacro %}

{% macro default__concat_ws(separator, values) %}
  {{- exceptions.raise_compiler_error('starter.concat_ws is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__concat_ws(separator, values) -%}
{%- set padded = values + [starter._null_pad()] if values | length < 2 else values -%}
concat_ws('{{ separator | replace("'", "''") }}', {{ padded | join(', ') }})
{%- endmacro %}

{% macro fabric__concat_ws(separator, values) -%}
{%- set padded = values + [starter._null_pad()] if values | length < 2 else values -%}
concat_ws('{{ separator | replace("'", "''") }}', {{ padded | join(', ') }})
{%- endmacro %}

{#- A typed NULL used to pad short argument lists. Same text on both targets. -#}
{% macro _null_pad() -%}
cast(null as varchar(1))
{%- endmacro %}


{% macro concat(values) %}
  {{- return(adapter.dispatch('concat', 'starter')(values)) -}}
{% endmacro %}

{% macro default__concat(values) %}
  {{- exceptions.raise_compiler_error('starter.concat is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- NULL parts are skipped on both engines, unlike the `||` operator, which turns the whole
    result NULL on DuckDB. A one-element list is padded to two values for T-SQL. -#}
{% macro duckdb__concat(values) -%}
{%- set padded = values + [starter._null_pad()] if values | length < 2 else values -%}
concat({{ padded | join(', ') }})
{%- endmacro %}

{% macro fabric__concat(values) -%}
{%- set padded = values + [starter._null_pad()] if values | length < 2 else values -%}
concat({{ padded | join(', ') }})
{%- endmacro %}


{% macro safe_divide(numerator, denominator) %}
  {{- return(adapter.dispatch('safe_divide', 'starter')(numerator, denominator)) -}}
{% endmacro %}

{% macro default__safe_divide(numerator, denominator) %}
  {{- exceptions.raise_compiler_error('starter.safe_divide is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- The numerator is cast to decimal first, because T-SQL divides two integers as integers
    (3 / 2 = 1) and DuckDB does not. A zero denominator gives NULL, not an error. -#}
{% macro duckdb__safe_divide(numerator, denominator) -%}
cast(cast({{ numerator }} as decimal(18, 6)) / nullif({{ denominator }}, 0) as decimal(18, 6))
{%- endmacro %}

{% macro fabric__safe_divide(numerator, denominator) -%}
cast(cast({{ numerator }} as decimal(18, 6)) / nullif({{ denominator }}, 0) as decimal(18, 6))
{%- endmacro %}


{% macro date_trunc(part, expr) %}
  {%- if part not in ['year', 'month', 'day'] -%}
    {{- exceptions.raise_compiler_error('starter.date_trunc accepts year, month or day, not ' ~ part) -}}
  {%- endif -%}
  {{- return(adapter.dispatch('date_trunc', 'starter')(part, expr)) -}}
{% endmacro %}

{% macro default__date_trunc(part, expr) %}
  {{- exceptions.raise_compiler_error('starter.date_trunc is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Always returns a date. dbt's own date_trunc returns a timestamp on DuckDB and a date on
    T-SQL, so models would otherwise see two different types. -#}
{% macro duckdb__date_trunc(part, expr) -%}
cast(date_trunc('{{ part }}', {{ expr }}) as date)
{%- endmacro %}

{% macro fabric__date_trunc(part, expr) -%}
cast(dateadd({{ part }}, datediff({{ part }}, 0, {{ expr }}), 0) as date)
{%- endmacro %}


{% macro type_varchar(length=none) %}
  {{- return(adapter.dispatch('type_varchar', 'starter')(length)) -}}
{% endmacro %}

{% macro default__type_varchar(length) %}
  {{- exceptions.raise_compiler_error('starter.type_varchar is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- dbt's own string type is TEXT on DuckDB and varchar(max) on T-SQL, and TEXT is on the
    portable-subset ban list. DuckDB has no length limit on varchar; T-SQL needs one. -#}
{% macro duckdb__type_varchar(length) -%}
varchar
{%- endmacro %}

{% macro fabric__type_varchar(length) -%}
varchar({{ length if length is not none else 8000 }})
{%- endmacro %}


{% macro hash_key(columns) %}
  {{- return(adapter.dispatch('hash_key', 'starter')(columns)) -}}
{% endmacro %}

{% macro default__hash_key(columns) %}
  {{- exceptions.raise_compiler_error('starter.hash_key is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- md5 of the columns joined with '-'. A NULL column becomes the marker '_null_' so that
    NULL and the empty string hash differently on both engines. -#}
{% macro duckdb__hash_key(columns) -%}
{%- set parts = [] -%}
{%- for c in columns -%}
  {%- do parts.append("coalesce(cast(" ~ c ~ " as varchar), '_null_')") -%}
{%- endfor -%}
{%- if parts | length < 2 -%}{%- do parts.append(starter._null_pad()) -%}{%- endif -%}
md5(concat_ws('-', {{ parts | join(', ') }}))
{%- endmacro %}

{% macro fabric__hash_key(columns) -%}
{%- set parts = [] -%}
{%- for c in columns -%}
  {%- do parts.append("coalesce(cast(" ~ c ~ " as varchar(8000)), '_null_')") -%}
{%- endfor -%}
{%- if parts | length < 2 -%}{%- do parts.append(starter._null_pad()) -%}{%- endif -%}
lower(convert(varchar(32), hashbytes('md5', concat_ws('-', {{ parts | join(', ') }})), 2))
{%- endmacro %}
