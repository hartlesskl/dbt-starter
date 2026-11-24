{#-
  Concatenation, arithmetic and hashing shims, same three-part shape as parse.sql: a public
  dispatcher, a raising `default__`, and an explicit `duckdb__` and `fabricspark__` body.

  `concat_ws` skips NULL values and accepts a single value on both engines, so the lists
  need no padding. Spark's `concat` is the exception: it returns NULL when any argument is
  NULL, so the Spark body of `concat` is `concat_ws('', ...)`.
-#}

{% macro concat_ws(separator, values) %}
  {{- return(adapter.dispatch('concat_ws', 'starter')(separator, values)) -}}
{% endmacro %}

{% macro default__concat_ws(separator, values) %}
  {{- exceptions.raise_compiler_error('starter.concat_ws is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__concat_ws(separator, values) -%}
concat_ws('{{ separator | replace("'", "''") }}', {{ values | join(', ') }})
{%- endmacro %}

{% macro fabricspark__concat_ws(separator, values) -%}
concat_ws('{{ separator | replace("'", "''") }}', {{ values | join(', ') }})
{%- endmacro %}


{% macro concat(values) %}
  {{- return(adapter.dispatch('concat', 'starter')(values)) -}}
{% endmacro %}

{% macro default__concat(values) %}
  {{- exceptions.raise_compiler_error('starter.concat is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- NULL parts are skipped on both engines, unlike the `||` operator, which turns the whole
    result NULL on DuckDB. DuckDB's `concat` skips NULLs; Spark's returns NULL if any argument
    is NULL, so Spark uses `concat_ws` with an empty separator. -#}
{% macro duckdb__concat(values) -%}
concat({{ values | join(', ') }})
{%- endmacro %}

{% macro fabricspark__concat(values) -%}
concat_ws('', {{ values | join(', ') }})
{%- endmacro %}


{% macro safe_divide(numerator, denominator) %}
  {{- return(adapter.dispatch('safe_divide', 'starter')(numerator, denominator)) -}}
{% endmacro %}

{% macro default__safe_divide(numerator, denominator) %}
  {{- exceptions.raise_compiler_error('starter.safe_divide is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- The numerator is cast to decimal first, so two integers never divide as integers
    (3 / 2 is 1.5, not 1) whatever the engine's own rule is. A zero denominator gives NULL,
    not an error. Both engines use the same text. -#}
{% macro duckdb__safe_divide(numerator, denominator) -%}
cast(cast({{ numerator }} as decimal(18, 6)) / nullif({{ denominator }}, 0) as decimal(18, 6))
{%- endmacro %}

{% macro fabricspark__safe_divide(numerator, denominator) -%}
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

{#- Always returns a date. dbt's own date_trunc returns a timestamp on both engines, so the
    cast gives models one type. Both engines use the same text. -#}
{% macro duckdb__date_trunc(part, expr) -%}
cast(date_trunc('{{ part }}', {{ expr }}) as date)
{%- endmacro %}

{% macro fabricspark__date_trunc(part, expr) -%}
cast(date_trunc('{{ part }}', {{ expr }}) as date)
{%- endmacro %}


{% macro type_varchar(length=none) %}
  {{- return(adapter.dispatch('type_varchar', 'starter')(length)) -}}
{% endmacro %}

{% macro default__type_varchar(length) %}
  {{- exceptions.raise_compiler_error('starter.type_varchar is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- The length is ignored on both engines: DuckDB's varchar has no limit, and Spark's `string`
    is the portable text type (a varchar(n) cast is accepted but reports `string`). -#}
{% macro duckdb__type_varchar(length) -%}
varchar
{%- endmacro %}

{% macro fabricspark__type_varchar(length) -%}
string
{%- endmacro %}


{% macro hash_key(columns) %}
  {{- return(adapter.dispatch('hash_key', 'starter')(columns)) -}}
{% endmacro %}

{% macro default__hash_key(columns) %}
  {{- exceptions.raise_compiler_error('starter.hash_key is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- md5 of the columns joined with '-'. A NULL column becomes the marker '_null_' so that
    NULL and the empty string hash differently on both engines; the digest is the same hex
    text on both. -#}
{% macro duckdb__hash_key(columns) -%}
{%- set parts = [] -%}
{%- for c in columns -%}
  {%- do parts.append("coalesce(cast(" ~ c ~ " as varchar), '_null_')") -%}
{%- endfor -%}
md5(concat_ws('-', {{ parts | join(', ') }}))
{%- endmacro %}

{% macro fabricspark__hash_key(columns) -%}
{%- set parts = [] -%}
{%- for c in columns -%}
  {%- do parts.append("coalesce(cast(" ~ c ~ " as string), '_null_')") -%}
{%- endfor -%}
md5(concat_ws('-', {{ parts | join(', ') }}))
{%- endmacro %}


{% macro string_literal(value) %}
  {{- return(adapter.dispatch('string_literal', 'starter')(value)) -}}
{% endmacro %}

{% macro default__string_literal(value) %}
  {{- exceptions.raise_compiler_error('starter.string_literal is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- A quoted SQL string literal for any text, or `null` for none. The two engines escape a
    quote differently, which is why this shim exists: DuckDB doubles it ('it''s'), while
    Spark reads `''` as two adjacent literals and drops the quote, so Spark escapes a quote
    with a backslash ('it\'s') and must first double each backslash, because Spark reads a
    backslash in a literal as an escape and DuckDB does not. -#}
{% macro duckdb__string_literal(value) -%}
{%- if value is none -%}null{%- else -%}'{{ value | string | replace("'", "''") }}'{%- endif -%}
{%- endmacro %}

{% macro fabricspark__string_literal(value) -%}
{%- if value is none -%}null{%- else -%}'{{ value | string | replace("\\", "\\\\") | replace("'", "\\'") }}'{%- endif -%}
{%- endmacro %}
