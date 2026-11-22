{#-
  One row per calendar day of the span set by the vars dim_date_start and dim_date_end.

  Built from the digits seed with sequential CTEs only. There is no recursive CTE, no dbt_date
  macro and no dbt_utils calendar macro, because those either recurse or query the database
  at compile time, and the offline Fabric compile has no connection.
  The weekday comes from starter.iso_weekday (Monday = 1), never an engine function (D-03).
  Month and day names come from fixed case lookups, never locale functions (D-04).
  The Jinja below stops compilation when a var is not a YYYY-MM-DD date, when its year is 9999
  (the month-length check below reads the first day of the next month, which would be year
  10000), when the end is before the start, or when the span is longer than the 10,000 days that
  four digits can number.
-#}
{%- set span_start = var('dim_date_start') | string -%}
{%- set span_end = var('dim_date_end') | string -%}
{%- set date_pattern = '^[1-9]\\d{3}-(0[1-9]|1[0-2])-(0[1-9]|[12]\\d|3[01])$' -%}
{%- for var_name, var_text in [('dim_date_start', span_start), ('dim_date_end', span_end)] -%}
{%- if not modules.re.match(date_pattern, var_text) -%}
{{- exceptions.raise_compiler_error('var ' ~ var_name ~ ' must be a date written YYYY-MM-DD, not ' ~ var_text) -}}
{%- endif -%}
{%- set y = var_text[0:4] | int -%}
{%- if y > 9998 -%}
{{- exceptions.raise_compiler_error('var ' ~ var_name ~ ' must have a year from 1000 to 9998, not ' ~ var_text) -}}
{%- endif -%}
{%- set m = var_text[5:7] | int -%}
{%- set d = var_text[8:10] | int -%}
{%- set month_days = (modules.datetime.date(y + (m // 12), m % 12 + 1, 1) - modules.datetime.date(y, m, 1)).days -%}
{%- if d > month_days -%}
{{- exceptions.raise_compiler_error('var ' ~ var_name ~ ' must be a real calendar date, and ' ~ var_text ~ ' is not') -}}
{%- endif -%}
{%- endfor -%}
{%- set span_days = (modules.datetime.date.fromisoformat(span_end) - modules.datetime.date.fromisoformat(span_start)).days + 1 -%}
{%- if span_days < 1 -%}
{{- exceptions.raise_compiler_error('var dim_date_end (' ~ span_end ~ ') is before dim_date_start (' ~ span_start ~ ')') -}}
{%- endif -%}
{%- if span_days > 10000 -%}
{{- exceptions.raise_compiler_error('The span from ' ~ span_start ~ ' to ' ~ span_end ~ ' is ' ~ span_days ~ ' days. The digits seed gives at most 10,000 days, so shorten the span') -}}
{%- endif -%}
with digits as (
    select digit
    from {{ ref('digits') }}
),

numbers as (
    select d1.digit * 1000 + d2.digit * 100 + d3.digit * 10 + d4.digit as day_offset
    from digits as d1
    cross join digits as d2
    cross join digits as d3
    cross join digits as d4
),

days as (
    select {{ starter.date_add_days(starter.parse_date("'" ~ var('dim_date_start') ~ "'"), 'n.day_offset') }} as date_day
    from numbers as n
),

in_span as (
    select date_day
    from days
    where date_day <= {{ starter.parse_date("'" ~ var('dim_date_end') ~ "'") }}
),

calendar as (
    select
        date_day,
        {{ starter.date_key('date_day') }} as date_key,
        {{ starter.date_part('year', 'date_day') }} as calendar_year,
        {{ starter.date_part('quarter', 'date_day') }} as calendar_quarter,
        {{ starter.date_part('month', 'date_day') }} as calendar_month,
        {{ starter.date_part('day', 'date_day') }} as day_of_month,
        {{ starter.iso_weekday('date_day') }} as day_of_week
    from in_span
),

named as (
    select
        date_day,
        date_key,
        calendar_year,
        calendar_quarter,
        calendar_month,
        day_of_month,
        day_of_week,
        case calendar_month
            when 1 then 'January'
            when 2 then 'February'
            when 3 then 'March'
            when 4 then 'April'
            when 5 then 'May'
            when 6 then 'June'
            when 7 then 'July'
            when 8 then 'August'
            when 9 then 'September'
            when 10 then 'October'
            when 11 then 'November'
            when 12 then 'December'
        end as month_name,
        case day_of_week
            when 1 then 'Monday'
            when 2 then 'Tuesday'
            when 3 then 'Wednesday'
            when 4 then 'Thursday'
            when 5 then 'Friday'
            when 6 then 'Saturday'
            when 7 then 'Sunday'
        end as day_name,
        case when day_of_week >= 6 then 1 else 0 end as is_weekend,
        {{ starter.date_add_days('date_day', '1 - day_of_week') }} as week_start_date
    from calendar
)

select
    cast(date_key as int) as date_key,
    cast(date_day as date) as date_day,
    cast(calendar_year as int) as calendar_year,
    cast(calendar_quarter as int) as calendar_quarter,
    cast(calendar_month as int) as calendar_month,
    cast(month_name as varchar(20)) as month_name,
    cast(day_of_month as int) as day_of_month,
    cast(day_of_week as int) as day_of_week,
    cast(day_name as varchar(20)) as day_name,
    cast(is_weekend as int) as is_weekend,
    cast(week_start_date as date) as week_start_date
from named
