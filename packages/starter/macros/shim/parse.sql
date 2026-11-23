{#-
  Text-to-number shims. Landed values are all text, so every typed column starts here.

  Each shim has the same three parts: a public macro that dispatches on the adapter, a
  `default__` that raises (so an unsupported target fails at compile time, not at run time),
  and an explicit `duckdb__` and `fabricspark__` version. dbt-fabricspark searches only its own
  prefix, so a missing body fails when the project compiles for that target. Models call
  `starter.<name>` and never dbt's own cast macros, which compile to different behaviour per
  target.

  The decimal, date and timestamp bodies share one grammar on both engines: a whitelist of
  characters, then (for date and timestamp) an anchored structure check, then the native
  `try_cast`. A bare `try_cast` is not enough, because the engines disagree on what text counts
  as a number or a date: `1_000` and `epoch` parse on DuckDB and not on Spark, `2024` and
  `2024-03` parse on Spark and not on DuckDB, and a zone offset is dropped by one and applied
  by the other. The whitelist has no newline, which matters because Java's `$` matches before a
  trailing newline and RE2's does not. The patterns hold no backslash, so they do not depend on
  Spark's `escapedStringLiterals` setting. The regex function is spelt `regexp_matches` on
  DuckDB and `regexp_like` on Spark and neither exists on the other engine, which is one more
  reason each engine has its own body.
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

{#- Spark: the same guard as DuckDB's. Plain digits, a sign, a point and spaces only, so
    `1e3`, `1_000`, `inf`, `nan`, `0x10`, Arabic-Indic digits, tabs and NBSP are NULL on both
    engines. Exponent text is rejected on purpose; the loader writes doubles without exponents. -#}
{% macro fabricspark__parse_decimal(expr, precision, scale) -%}
case when translate({{ expr }}, ' 0123456789+-.', '') = '' then try_cast(nullif(trim({{ expr }}), '') as decimal({{ precision }}, {{ scale }})) end
{%- endmacro %}


{% macro parse_int(expr) %}
  {{- return(adapter.dispatch('parse_int', 'starter')(expr)) -}}
{% endmacro %}

{% macro default__parse_int(expr) %}
  {{- exceptions.raise_compiler_error('starter.parse_int is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Whole numbers only. A decimal cast to int rounds on one engine and truncates on the other,
    so '1.5' could be 2 or 1. Going through decimal and casting only when floor(D) = D makes
    both engines return NULL for '1.5'. -#}
{% macro duckdb__parse_int(expr) -%}
case when floor({{ starter.parse_decimal(expr) }}) = {{ starter.parse_decimal(expr) }} then try_cast({{ starter.parse_decimal(expr) }} as int) end
{%- endmacro %}

{% macro fabricspark__parse_int(expr) -%}
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

{#- Spark: exactly YYYY-MM-DD (years 1000 to 9999) with optional surrounding spaces. The
    whitelist keeps newlines out before the regex runs. -#}
{% macro fabricspark__parse_date(expr) -%}
case when translate({{ expr }}, ' 0123456789-', '') = '' and regexp_like(trim({{ expr }}), '^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}$') then try_cast(trim({{ expr }}) as date) end
{%- endmacro %}


{% macro parse_timestamp(expr) %}
  {{- return(adapter.dispatch('parse_timestamp', 'starter')(expr)) -}}
{% endmacro %}

{% macro default__parse_timestamp(expr) %}
  {{- exceptions.raise_compiler_error('starter.parse_timestamp is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Trim, then an empty string is NULL, and text that is not a real timestamp
    ('garbage', '2024-13-45 99:99:99', '2024-02-30 00:00:00') is NULL, never an error. -#}
{% macro duckdb__parse_timestamp(expr) -%}
try_cast(nullif(ltrim(rtrim({{ expr }})), '') as timestamp)
{%- endmacro %}

{#- Spark: date, or date plus time, with a space or T between them and no zone designator
    (`Z`, `+05:00` and `UTC` are NULL). A Spark timestamp is parsed in the session time zone,
    which the profile sets to UTC so that it matches DuckDB's zone-less timestamp. -#}
{% macro fabricspark__parse_timestamp(expr) -%}
case when translate({{ expr }}, ' 0123456789-:.T', '') = '' and regexp_like(trim({{ expr }}), '^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}([ T]([01][0-9]|2[0-3]):[0-9]{2}(:[0-9]{2}([.][0-9]{1,9})?)?)?$') then try_cast(trim({{ expr }}) as timestamp) end
{%- endmacro %}


{% macro datediff(part, start_expr, end_expr) %}
  {%- if part not in ['day', 'minute', 'second'] -%}
    {{- exceptions.raise_compiler_error('starter.datediff accepts day, minute or second, not ' ~ part) -}}
  {%- endif -%}
  {{- return(adapter.dispatch('datediff', 'starter')(part, start_expr, end_expr)) -}}
{% endmacro %}

{% macro default__datediff(part, start_expr, end_expr) %}
  {{- exceptions.raise_compiler_error('starter.datediff is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- Counts part boundaries crossed between the two values: 00:00:00.9 to 00:00:01.1 is one
    second. The result is an int on both engines, so a seconds count above 2,147,483,647
    (about 68 years) overflows: callers must pass values that are already clamped to a safe
    window. -#}
{% macro duckdb__datediff(part, start_expr, end_expr) -%}
datediff('{{ part }}', {{ start_expr }}, {{ end_expr }})
{%- endmacro %}

{#- Spark's three-argument timestampdiff counts whole units, not boundaries, so both sides are
    truncated to the part first. Its two-argument datediff takes (end, start), the reverse of
    DuckDB's order. -#}
{% macro fabricspark__datediff(part, start_expr, end_expr) -%}
{%- if part == 'day' -%}
datediff(cast({{ end_expr }} as date), cast({{ start_expr }} as date))
{%- else -%}
timestampdiff({{ part }}, date_trunc('{{ part }}', {{ start_expr }}), date_trunc('{{ part }}', {{ end_expr }}))
{%- endif -%}
{%- endmacro %}
