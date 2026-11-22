{#-
  Generic test that two relations agree, key by key, on a list of totals. It returns one row per
  key whose totals differ, so a failure count is the number of keys that are wrong, and the
  failing rows show both totals side by side.

  totals_reconcile(model, compare_model, key_column, measures)
      `measures` is a list of dicts, each with three entries: `name` (the output name of the
      total), `model_expr` (an aggregate expression over `model`, for example `sum(trip_count)`)
      and `compare_expr` (the aggregate over `compare_model` that must equal it, for example
      `count(*)`). Both sides are grouped by `key_column`. A key present on only one side counts
      as a difference, because the missing side is read as 0 for every measure. A NULL total is
      read as 0 as well, so a NULL cannot hide a difference.

  The comparison is exact: there is no tolerance, so the measures must be integers or exact
  decimals. `key_column` and every measure name must be a plain lower case identifier, and
  compilation stops otherwise. The expressions come from project-owned YAML, so they are
  reviewed through the pull request gate rather than checked here.

  The SQL is sequential CTEs only (no nested CTE) and uses no engine-specific syntax, so the
  same test compiles on DuckDB and on Fabric T-SQL. Two empty relations reconcile.
-#}
{% test totals_reconcile(model, compare_model, key_column, measures) %}
{%- if not modules.re.match('^[a-z][a-z0-9_]*$', key_column) -%}
    {{- exceptions.raise_compiler_error('totals_reconcile: key_column must match ^[a-z][a-z0-9_]*$, not ' ~ key_column) -}}
{%- endif -%}
{%- if measures | length == 0 -%}
    {{- exceptions.raise_compiler_error('totals_reconcile: measures must name at least one measure') -}}
{%- endif -%}
{%- for measure in measures -%}
    {%- if not modules.re.match('^[a-z][a-z0-9_]*$', measure['name']) -%}
        {{- exceptions.raise_compiler_error('totals_reconcile: a measure name must match ^[a-z][a-z0-9_]*$, not ' ~ measure['name']) -}}
    {%- endif -%}
{%- endfor -%}
with model_totals as (
    select
        {{ key_column }} as key_value
        {%- for measure in measures %},
        {{ measure['model_expr'] }} as model_{{ measure['name'] }}
        {%- endfor %}
    from {{ model }}
    group by {{ key_column }}
),

compare_totals as (
    select
        {{ key_column }} as key_value
        {%- for measure in measures %},
        {{ measure['compare_expr'] }} as compare_{{ measure['name'] }}
        {%- endfor %}
    from {{ compare_model }}
    group by {{ key_column }}
),

joined as (
    select
        coalesce(m.key_value, c.key_value) as key_value
        {%- for measure in measures %},
        coalesce(m.model_{{ measure['name'] }}, 0) as model_{{ measure['name'] }},
        coalesce(c.compare_{{ measure['name'] }}, 0) as compare_{{ measure['name'] }}
        {%- endfor %}
    from model_totals as m
    full outer join compare_totals as c
        on m.key_value = c.key_value
)

select
    key_value
    {%- for measure in measures %},
    model_{{ measure['name'] }},
    compare_{{ measure['name'] }}
    {%- endfor %}
from joined
where
    {% for measure in measures -%}
    model_{{ measure['name'] }} <> compare_{{ measure['name'] }}
    {%- if not loop.last %}
    or
    {% endif -%}
    {%- endfor %}
{% endtest %}
