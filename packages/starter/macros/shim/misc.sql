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
