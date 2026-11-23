{#-
  Generic test that an entity's quarantine rate stays within its levels. It returns one row per
  scope whose rate is above the warn level, so a failure row says which source file (or all
  files together) is over which level.

  quarantine_rate_levels(model, all_rows_model, warn_above, error_above)
      `model` is an entity's silver model (the rows that were kept) and `all_rows_model` is its
      bronze model (every landed row). The levels are fractions of rows from 0 to 1, for example
      0.01 for 1% of rows. Both levels are written in the entity's own YAML, beside its silver
      model.

  The second parent must be the bronze model. dbt makes a downstream model wait for a test only
  when the model descends from every parent of the test. A gold model descends from the silver
  model and from the bronze model, and never from the quarantine model, so a test whose second
  parent was the quarantine model would run beside gold and would not stop it.

  The rate is the rows that are in bronze but not in silver, divided by the rows in bronze. Only
  rows that failed an error rule leave silver, so a warn rule never counts. It is worked out per
  `_source_file` (a NULL file is read as `(none)`) and once for `(all files)`, and the same two
  levels apply at both grains. A scope fires a level only when its rate is strictly above it, so
  a rate equal to a level does not fire. Rates are `decimal(18, 6)`, so the comparison is made at
  6 decimal places and the levels are rendered as `'%.6f'` literals, never as `1e-05`.

  A rate above the warn level is a dbt warning. A rate above the error level fails the test, and
  dbt then skips the models downstream of the silver model, while the silver model, its
  quarantine model and the data quality models still build. The failure count is the highest
  level reached among the scopes (0, 1 or 2), so it does not depend on row or file order. An
  entity with no rows has no scope, so the test passes.

  The body is one SELECT over derived tables, never a CTE. dbt-fabric wraps every test body in a
  CTE (`with test_main_sql as (...)`), so a CTE inside the body would become a nested WITH, which
  the portable subset bans. Every derived table has an alias and every column a name, as T-SQL
  needs.

  The var `quarantine_rate_override`, a mapping with `warn_above` and `error_above`, replaces both
  levels of every levels test. It exists only for builds of synthetic data, whose rates are far
  above any real level, and must never be set for a real build.

  A level that is not a number, is a boolean, lies outside 0 to 1, or a warn level above the
  error level, stops compilation with a message that starts `quarantine_rate_levels:`. An override
  that is not a mapping holding both levels stops it with `quarantine_rate_override:`.
-#}
{% test quarantine_rate_levels(model, all_rows_model, warn_above, error_above) %}
{{ config(severity='error', fail_calc='coalesce(max(level_rank), 0)', warn_if='>= 1', error_if='>= 2') }}
{%- set override = var('quarantine_rate_override', none) -%}
{%- if override is not none -%}
    {%- if override is not mapping or 'warn_above' not in override or 'error_above' not in override -%}
        {{- exceptions.raise_compiler_error('quarantine_rate_override: the var must be a mapping holding both warn_above and error_above, got ' ~ override) -}}
    {%- endif -%}
    {%- set warn_above = override['warn_above'] -%}
    {%- set error_above = override['error_above'] -%}
{%- endif -%}
{%- for level in [warn_above, error_above] -%}
    {%- if level is not number or level is boolean or level < 0 or level > 1 -%}
        {{- exceptions.raise_compiler_error('quarantine_rate_levels: warn_above and error_above must be numbers from 0 to 1, got ' ~ level) -}}
    {%- endif -%}
{%- endfor -%}
{%- if warn_above > error_above -%}
    {{- exceptions.raise_compiler_error('quarantine_rate_levels: warn_above must not exceed error_above, got ' ~ warn_above ~ ' and ' ~ error_above) -}}
{%- endif -%}
{%- set per_file_sql -%}
select
    all_rows.source_file as source_file,
    all_rows.total_rows as total_rows,
    all_rows.total_rows - coalesce(kept.kept_rows, 0) as rejected_rows
from (
    select
        coalesce(_source_file, '(none)') as source_file,
        count(*) as total_rows
    from {{ all_rows_model }}
    group by coalesce(_source_file, '(none)')
) as all_rows
left join (
    select
        coalesce(_source_file, '(none)') as source_file,
        count(*) as kept_rows
    from {{ model }}
    group by coalesce(_source_file, '(none)')
) as kept
    on all_rows.source_file = kept.source_file
{%- endset -%}
select
    scope,
    rejected_rows,
    total_rows,
    quarantine_rate,
    case when quarantine_rate > {{ '%.6f' | format(error_above) }} then 2 else 1 end as level_rank
from (
    select
        scope,
        rejected_rows,
        total_rows,
        {{ starter.safe_divide('rejected_rows', 'total_rows') }} as quarantine_rate
    from (
        select
            source_file as scope,
            rejected_rows,
            total_rows
        from ({{ per_file_sql }}) as per_file

        union all

        select
            '(all files)' as scope,
            coalesce(sum(rejected_rows), 0) as rejected_rows,
            coalesce(sum(total_rows), 0) as total_rows
        from ({{ per_file_sql }}) as all_files
    ) as scopes
) as rated
where quarantine_rate > {{ '%.6f' | format(warn_above) }}
{% endtest %}
