{#-
  Quarantine framework. Domain-free: it knows nothing about any dataset.

  A rule is a dict with the six keys code, description, severity, on_null, fail_when and
  rationale. `fail_when` is a SQL predicate that is true when the row breaks the rule. `on_null`
  says what the flag is when the predicate is unknown (NULL): 'pass' gives 0, 'fail' gives 1,
  so a flag is never NULL by construction. `rationale` says what data problem the rule catches,
  in plain ASCII words, and it is required: the rule catalog publishes it.

  `severity` is 'error' or 'warn'. Rule of thumb: error means the row is unusable, so the row
  moves to quarantine; warn means a real value that looks suspicious, so the row stays valid
  and the warning is counted beside it. Validity, the failed count and the reason codes read
  error flags only; the warning count and the warning codes read warn flags only.
-#}

{#- validate_rules: raises a compile error naming the rule code. Renders nothing. -#}
{% macro validate_rules(rules, prefix) %}
  {%- if rules | length == 0 -%}
    {{ exceptions.raise_compiler_error('rule registry is empty: the registry has no rules, and every entity needs at least one') }}
  {%- endif -%}
  {%- set seen = [] -%}
  {%- for r in rules -%}
    {%- set label = r.get('code') or ('#' ~ loop.index) -%}
    {%- for key in ['code', 'description', 'severity', 'on_null', 'fail_when', 'rationale'] -%}
      {%- if not r.get(key) -%}
        {{ exceptions.raise_compiler_error('rule ' ~ label ~ ': missing or empty key ' ~ key) }}
      {%- endif -%}
    {%- endfor -%}
    {%- if not modules.re.match('^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$', r.code) -%}
      {{ exceptions.raise_compiler_error('rule code must be UPPER_SNAKE: ' ~ r.code) }}
    {%- endif -%}
    {%- if not r.code.startswith(prefix) -%}
      {{ exceptions.raise_compiler_error('rule code ' ~ r.code ~ ' must start with the entity prefix ' ~ prefix) }}
    {%- endif -%}
    {%- if r.code in seen -%}
      {{ exceptions.raise_compiler_error('duplicate rule code ' ~ r.code ~ ': it appears more than once in this registry') }}
    {%- endif -%}
    {%- do seen.append(r.code) -%}
    {%- if r.on_null not in ['pass', 'fail'] -%}
      {{ exceptions.raise_compiler_error('rule ' ~ r.code ~ ': on_null must be pass or fail, got ' ~ r.on_null) }}
    {%- endif -%}
    {%- if r.severity not in ['error', 'warn'] -%}
      {{ exceptions.raise_compiler_error('rule ' ~ r.code ~ ': severity must be error or warn, got ' ~ r.severity) }}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}


{#- A rule for "text is present but does not convert to the typed column". The code is an
    explicit literal argument so a static scan can find every rule code. -#}
{% macro cast_check_rule(code, raw, typed, description, rationale) %}
  {{- return({
    'code': code,
    'description': description,
    'severity': 'error',
    'on_null': 'pass',
    'rationale': rationale,
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

{#- The flag column names, one per line and comma separated, in registry order. With a
    severity ('error' or 'warn') only the rules of that severity are listed. -#}
{% macro flag_names(rules, severity=none) -%}
{%- set picked = rules if severity is none else starter._of_severity(rules, severity) -%}
{%- for r in picked %}
{{ starter.flag_name(r) }}
{%- if not loop.last %},{% endif -%}
{%- endfor %}
{%- endmacro %}

{#- The rules of one severity, as a list. -#}
{% macro _of_severity(rules, severity) -%}
{%- set picked = [] -%}
{%- for r in rules -%}
  {%- if r.severity == severity -%}{%- do picked.append(r) -%}{%- endif -%}
{%- endfor -%}
{{- return(picked) -}}
{%- endmacro %}

{#- The flags added up. An empty list renders 0, so an entity with no warn rule still compiles. -#}
{% macro _count_expr(rules) -%}
{%- if rules | length == 0 -%}
0
{%- else -%}
(
{%- for r in rules -%}
{{ starter.flag_name(r) }}{% if not loop.last %} + {% endif %}
{%- endfor -%}
)
{%- endif -%}
{%- endmacro %}

{#- Pipe-delimited codes of the flagged rules. No flagged rule gives NULL, not an empty string.
    An empty list renders a typed NULL, so an entity with no warn rule still compiles. -#}
{% macro _codes_expr(rules) -%}
{%- if rules | length == 0 -%}
cast(null as {{ starter.type_varchar(1) }})
{%- else -%}
{%- set parts = [] -%}
{%- for r in rules -%}
  {%- do parts.append("case when " ~ starter.flag_name(r) ~ " = 1 then '" ~ r.code ~ "' end") -%}
{%- endfor -%}
nullif({{ starter.concat_ws('|', parts) }}, '')
{%- endif -%}
{%- endmacro %}

{#- Error rules only: a row that fails a warn rule is still valid. -#}
{% macro failed_count_expr(rules) -%}
{{ starter._count_expr(starter._of_severity(rules, 'error')) }}
{%- endmacro %}

{% macro valid_expr(rules) -%}
case when {{ starter.failed_count_expr(rules) }} = 0 then 1 else 0 end
{%- endmacro %}

{#- Pipe-delimited codes of the failed error rules. Valid rows get NULL, not an empty string. -#}
{% macro reason_codes_expr(rules) -%}
{{ starter._codes_expr(starter._of_severity(rules, 'error')) }}
{%- endmacro %}

{#- Warn rules only. These never change dq_is_valid. -#}
{% macro warning_count_expr(rules) -%}
{{ starter._count_expr(starter._of_severity(rules, 'warn')) }}
{%- endmacro %}

{% macro warning_codes_expr(rules) -%}
{{ starter._codes_expr(starter._of_severity(rules, 'warn')) }}
{%- endmacro %}
