{#-
  Generic test that two relations agree, key by key, on a list of totals. It returns one row per
  key whose totals differ, so a failure count is the number of keys that are wrong, and the
  failing rows show both totals side by side.

  totals_reconcile(model, compare_model, key_column, measures,
                   compare_key_column, compare_join_model, compare_join_on)
      `measures` is a list of dicts, each with three entries: `name` (the output name of the
      total), `model_expr` (an aggregate expression over `model`, for example `sum(trip_count)`)
      and `compare_expr` (the aggregate over `compare_model` that must equal it, for example
      `count(*)`). Both sides are grouped by the key. A key present on only one side counts
      as a difference, because the missing side is read as 0 for every measure. A NULL total is
      read as 0 as well, so a NULL cannot hide a difference.

      `key_column` is one column name or a list of them (a composite key, for example a day and
      a borough). It names the columns of `model`.

      By default `compare_model` is grouped by the same column names. When the compare side
      does not carry a key column itself, three optional arguments reach it through a lookup:
      `compare_key_column` names the compare-side key, one entry per entry of `key_column` and
      in the same order (`alias.column` is allowed); `compare_join_model` is the lookup relation,
      aliased `j`, and `compare_join_on` is the inner-join condition between `compare_model`,
      aliased `c`, and `j`. Measure expressions on the compare side may use either alias. The
      join is an inner join, so a compare row the lookup cannot match is not counted.

  The comparison is exact: there is no tolerance, so the measures must be integers or exact
  decimals. Every key column and every measure name must be a plain lower case identifier, and
  compilation stops otherwise. The measure and join expressions come from project-owned YAML,
  so they are reviewed through the pull request gate rather than checked here.

  The SQL is sequential CTEs only (no nested CTE) and uses no engine-specific syntax, so the
  same test compiles on DuckDB and on Fabric T-SQL. Two empty relations reconcile.
-#}
{% test totals_reconcile(model, compare_model, key_column, measures, compare_key_column=none, compare_join_model=none, compare_join_on=none) %}
{%- set keys = [key_column] if key_column is string else key_column -%}
{%- if compare_key_column is none -%}
    {%- set compare_keys = keys -%}
{%- else -%}
    {%- set compare_keys = [compare_key_column] if compare_key_column is string else compare_key_column -%}
{%- endif -%}
{%- if keys | length == 0 -%}
    {{- exceptions.raise_compiler_error('totals_reconcile: key_column must name at least one column') -}}
{%- endif -%}
{%- for key in keys -%}
    {%- if not modules.re.match('^[a-z][a-z0-9_]*$', key) -%}
        {{- exceptions.raise_compiler_error('totals_reconcile: key_column must match ^[a-z][a-z0-9_]*$, not ' ~ key) -}}
    {%- endif -%}
{%- endfor -%}
{%- if compare_keys | length != keys | length -%}
    {{- exceptions.raise_compiler_error('totals_reconcile: compare_key_column must have one entry per key_column entry') -}}
{%- endif -%}
{%- for key in compare_keys -%}
    {%- if not modules.re.match('^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)?$', key) -%}
        {{- exceptions.raise_compiler_error('totals_reconcile: compare_key_column must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)?$, not ' ~ key) -}}
    {%- endif -%}
{%- endfor -%}
{%- if (compare_join_model is none) != (compare_join_on is none) -%}
    {{- exceptions.raise_compiler_error('totals_reconcile: compare_join_model and compare_join_on must be given together') -}}
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
        {%- for key in keys %}
        {{ key }} as key_{{ loop.index }},
        {%- endfor %}
        {%- for measure in measures %}
        {{ measure['model_expr'] }} as model_{{ measure['name'] }}
        {%- if not loop.last %},{% endif -%}
        {%- endfor %}
    from {{ model }}
    group by {{ keys | join(', ') }}
),

compare_totals as (
    select
        {%- for key in compare_keys %}
        {{ key }} as key_{{ loop.index }},
        {%- endfor %}
        {%- for measure in measures %}
        {{ measure['compare_expr'] }} as compare_{{ measure['name'] }}
        {%- if not loop.last %},{% endif -%}
        {%- endfor %}
    from {{ compare_model }} as c
    {%- if compare_join_model is not none %}
    inner join {{ compare_join_model }} as j
        on {{ compare_join_on }}
    {%- endif %}
    group by {{ compare_keys | join(', ') }}
),

joined as (
    select
        {%- for key in keys %}
        coalesce(m.key_{{ loop.index }}, c.key_{{ loop.index }}) as key_{{ loop.index }},
        {%- endfor %}
        {%- for measure in measures %}
        coalesce(m.model_{{ measure['name'] }}, 0) as model_{{ measure['name'] }},
        coalesce(c.compare_{{ measure['name'] }}, 0) as compare_{{ measure['name'] }}
        {%- if not loop.last %},{% endif -%}
        {%- endfor %}
    from model_totals as m
    full outer join compare_totals as c
        on
        {% for key in keys -%}
        m.key_{{ loop.index }} = c.key_{{ loop.index }}
        {%- if not loop.last %} and {% endif -%}
        {%- endfor %}
)

select
    {%- for key in keys %}
    key_{{ loop.index }},
    {%- endfor %}
    {%- for measure in measures %}
    model_{{ measure['name'] }},
    compare_{{ measure['name'] }}
    {%- if not loop.last %},{% endif -%}
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
