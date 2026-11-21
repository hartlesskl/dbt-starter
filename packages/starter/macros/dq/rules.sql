{#-
  Quarantine framework. Domain-free: it knows nothing about any dataset.

  A rule is a dict with the keys code, description, severity, on_null and fail_when.
  `fail_when` is a SQL predicate that is true when the row breaks the rule. `on_null` says
  what the flag is when the predicate is unknown (NULL): 'pass' gives 0, 'fail' gives 1, so a
  flag is never NULL by construction.
-#}

{#- validate_rules: raises a compile error naming the rule code. Renders nothing. -#}
{% macro validate_rules(rules, prefix) %}
  {%- if rules | length == 0 -%}
    {{ exceptions.raise_compiler_error('rule registry is empty; every entity needs at least one rule') }}
  {%- endif -%}
  {%- set seen = [] -%}
  {%- for r in rules -%}
    {%- set label = r.get('code') or ('#' ~ loop.index) -%}
    {%- for key in ['code', 'description', 'severity', 'on_null', 'fail_when'] -%}
      {%- if not r.get(key) -%}
        {{ exceptions.raise_compiler_error('rule ' ~ label ~ ': missing or empty key ' ~ key) }}
      {%- endif -%}
    {%- endfor -%}
    {%- if not modules.re.match('^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$', r.code) -%}
      {{ exceptions.raise_compiler_error('rule code must be UPPER_SNAKE: ' ~ r.code) }}
    {%- endif -%}
    {%- if not r.code.startswith(prefix) -%}
      {{ exceptions.raise_compiler_error('rule code ' ~ r.code ~ ' must start with ' ~ prefix) }}
    {%- endif -%}
    {%- if r.code in seen -%}
      {{ exceptions.raise_compiler_error('rule code ' ~ r.code ~ ' appears more than once') }}
    {%- endif -%}
    {%- do seen.append(r.code) -%}
    {%- if r.on_null not in ['pass', 'fail'] -%}
      {{ exceptions.raise_compiler_error('rule ' ~ r.code ~ ': on_null must be pass or fail, got ' ~ r.on_null) }}
    {%- endif -%}
    {%- if r.severity == 'warn' -%}
      {{ exceptions.raise_compiler_error('rule ' ~ r.code ~ ': only severity error is accepted now; warn-severity rules arrive with Phase 2') }}
    {%- endif -%}
    {%- if r.severity != 'error' -%}
      {{ exceptions.raise_compiler_error('rule ' ~ r.code ~ ': severity must be error, got ' ~ r.severity) }}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}


{#- A rule for "text is present but does not convert to the typed column". The code is an
    explicit literal argument so a static scan can find every rule code. -#}
{% macro cast_check_rule(code, raw, typed, description) %}
  {{- return({
    'code': code,
    'description': description,
    'severity': 'error',
    'on_null': 'pass',
    'fail_when': raw ~ ' is not null and ' ~ typed ~ ' is null'
  }) -}}
{% endmacro %}

{% macro flag_name(rule) -%}
dq_fail_{{ rule.code | lower }}
{%- endmacro %}

{#- One 0/1 integer column per rule, in registry order. Never boolean, never NULL. -#}
{% macro flag_columns(rules) -%}
{%- for r in rules %}
case when ({{ r.fail_when }}) then 1 when not ({{ r.fail_when }}) then 0 else {{ 1 if r.on_null == 'fail' else 0 }} end as {{ starter.flag_name(r) }}
{%- if not loop.last %},{% endif -%}
{%- endfor %}
{%- endmacro %}

{% macro failed_count_expr(rules) -%}
(
{%- for r in rules -%}
{{ starter.flag_name(r) }}{% if not loop.last %} + {% endif %}
{%- endfor -%}
)
{%- endmacro %}

{% macro valid_expr(rules) -%}
case when {{ starter.failed_count_expr(rules) }} = 0 then 1 else 0 end
{%- endmacro %}

{#- Pipe-delimited codes of the failed rules. Valid rows get NULL, not an empty string. -#}
{% macro reason_codes_expr(rules) -%}
{%- set parts = [] -%}
{%- for r in rules -%}
  {%- do parts.append("case when " ~ starter.flag_name(r) ~ " = 1 then '" ~ r.code ~ "' end") -%}
{%- endfor -%}
nullif({{ starter.concat_ws('|', parts) }}, '')
{%- endmacro %}
