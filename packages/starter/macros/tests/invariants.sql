{#-
  Generic tests that prove a split loses nothing. Each one returns the offending rows, so a
  failure count is the number of batches (or keys) that are wrong, and the failing rows say
  which ones.

  A batch is a `(_source_file, _batch_id)` pair, the lineage every landed row carries. Counts are
  grouped by that pair, so the tests do not depend on row order, and one extra row is returned
  with the label `(total)` when the overall counts differ. A NULL lineage value is counted as the
  text `(none)` so the row is still counted rather than dropped by the join.

  The SQL is sequential CTEs only (no nested CTE) and uses no engine-specific syntax, so the
  same tests compile on DuckDB and on Fabric T-SQL. An empty relation has no batch, so every
  test here passes on a table that holds no rows.

  rows_equal_by_batch(model, compare_model)
      Per-batch and total row counts of the two relations are equal.
  rows_split_by_batch(model, kept_model, rejected_model)
      Per-batch and total row counts of `model` equal the counts of `kept_model` plus
      `rejected_model` together.
  no_shared_keys(model, other_model, column_name)
      No value of `column_name` appears in both relations.
-#}
{% test rows_equal_by_batch(model, compare_model) %}
with model_rows as (
    select
        coalesce(_source_file, '(none)') as source_file,
        coalesce(_batch_id, '(none)') as batch_id
    from {{ model }}
),

model_counts as (
    select
        source_file,
        batch_id,
        count(*) as row_count
    from model_rows
    group by source_file, batch_id
),

compare_rows as (
    select
        coalesce(_source_file, '(none)') as source_file,
        coalesce(_batch_id, '(none)') as batch_id
    from {{ compare_model }}
),

compare_counts as (
    select
        source_file,
        batch_id,
        count(*) as row_count
    from compare_rows
    group by source_file, batch_id
),

per_batch as (
    select
        coalesce(m.source_file, c.source_file) as source_file,
        coalesce(m.batch_id, c.batch_id) as batch_id,
        coalesce(m.row_count, 0) as model_rows,
        coalesce(c.row_count, 0) as compare_rows
    from model_counts as m
    full outer join compare_counts as c
        on m.source_file = c.source_file
        and m.batch_id = c.batch_id
),

totals as (
    select
        '(total)' as source_file,
        '(total)' as batch_id,
        coalesce(sum(model_rows), 0) as model_rows,
        coalesce(sum(compare_rows), 0) as compare_rows
    from per_batch
)

select
    source_file,
    batch_id,
    model_rows,
    compare_rows
from per_batch
where model_rows <> compare_rows

union all

select
    source_file,
    batch_id,
    model_rows,
    compare_rows
from totals
where model_rows <> compare_rows
{% endtest %}


{% test rows_split_by_batch(model, kept_model, rejected_model) %}
with model_rows as (
    select
        coalesce(_source_file, '(none)') as source_file,
        coalesce(_batch_id, '(none)') as batch_id
    from {{ model }}
),

model_counts as (
    select
        source_file,
        batch_id,
        count(*) as row_count
    from model_rows
    group by source_file, batch_id
),

split_rows as (
    select
        coalesce(_source_file, '(none)') as source_file,
        coalesce(_batch_id, '(none)') as batch_id
    from {{ kept_model }}

    union all

    select
        coalesce(_source_file, '(none)') as source_file,
        coalesce(_batch_id, '(none)') as batch_id
    from {{ rejected_model }}
),

split_counts as (
    select
        source_file,
        batch_id,
        count(*) as row_count
    from split_rows
    group by source_file, batch_id
),

per_batch as (
    select
        coalesce(m.source_file, s.source_file) as source_file,
        coalesce(m.batch_id, s.batch_id) as batch_id,
        coalesce(m.row_count, 0) as model_rows,
        coalesce(s.row_count, 0) as split_rows
    from model_counts as m
    full outer join split_counts as s
        on m.source_file = s.source_file
        and m.batch_id = s.batch_id
),

totals as (
    select
        '(total)' as source_file,
        '(total)' as batch_id,
        coalesce(sum(model_rows), 0) as model_rows,
        coalesce(sum(split_rows), 0) as split_rows
    from per_batch
)

select
    source_file,
    batch_id,
    model_rows,
    split_rows
from per_batch
where model_rows <> split_rows

union all

select
    source_file,
    batch_id,
    model_rows,
    split_rows
from totals
where model_rows <> split_rows
{% endtest %}


{% test no_shared_keys(model, other_model, column_name) %}
select
    m.{{ column_name }} as shared_key
from {{ model }} as m
inner join {{ other_model }} as o
    on m.{{ column_name }} = o.{{ column_name }}
group by m.{{ column_name }}
{% endtest %}
