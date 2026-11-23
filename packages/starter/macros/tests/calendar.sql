{#-
  Generic test for a calendar column: the span has no gap, no repeat and the right ends.

  date_span_complete(model, column_name, start_date, end_date)
      Returns one row when the relation has a different number of rows from the number of days
      from `start_date` to `end_date` inclusive, when a value of the column appears twice, or
      when the smallest or largest value is not the start or the end. An empty relation returns
      a row. A passing test returns no rows.

  `start_date` and `end_date` are YYYY-MM-DD text. The SQL is sequential CTEs only and uses the
  starter shims, so it compiles on DuckDB and on Fabric Spark SQL.
-#}
{% test date_span_complete(model, column_name, start_date, end_date) %}
with span as (
    select
        count(*) as row_count,
        count(distinct {{ column_name }}) as distinct_days,
        min({{ column_name }}) as first_day,
        max({{ column_name }}) as last_day
    from {{ model }}
),

expected as (
    select
        {{ starter.datediff('day', starter.parse_date("'" ~ start_date ~ "'"), starter.parse_date("'" ~ end_date ~ "'")) }} + 1 as expected_rows,
        {{ starter.parse_date("'" ~ start_date ~ "'") }} as expected_first_day,
        {{ starter.parse_date("'" ~ end_date ~ "'") }} as expected_last_day
)

select
    span.row_count,
    span.distinct_days,
    span.first_day,
    span.last_day,
    expected.expected_rows,
    expected.expected_first_day,
    expected.expected_last_day
from span
cross join expected
where
    span.row_count <> expected.expected_rows
    or span.distinct_days <> span.row_count
    or span.first_day <> expected.expected_first_day
    or span.last_day <> expected.expected_last_day
    or span.first_day is null
{% endtest %}
