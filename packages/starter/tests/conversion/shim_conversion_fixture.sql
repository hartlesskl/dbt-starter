{{ config(tags=['conversion_fixture']) }}

{#-
  Shared conversion fixture for the starter shims (PORT-02).

  A singular test: it returns one row for every case whose actual value differs from the
  expected value, and a passing run returns zero rows. Each mismatch row names the shim, the
  input, the expected value and the actual value.

  It runs in every `dbt build` on DuckDB and compiles unchanged for the Fabric Lakehouse
  (Spark SQL). Phase 5 reruns the same file there with
  `dbt test --select tag:conversion_fixture --target fabric`.

  The parse shims implement one grammar on both engines: a whitelist of characters, an anchored
  structure check for dates and timestamps, then the native `try_cast`. The fixture therefore
  includes, on purpose, exponent text ('1e3'), digit separators ('1_000'), special values
  ('inf', 'nan', 'epoch'), hex text ('0x10'), partial dates ('2024', '2024-03'), slashed
  dates, out-of-range years, zone designators ('Z', '+05:00') and control characters. Each of
  these parses on one engine's bare `try_cast` and not on the other's, and the shared grammar
  makes every one of them NULL on both. An expected-equal row for any of them would pin a
  divergence between the engines, so they are all expected to be NULL.

  A timestamp is compared as a count of seconds from 2024-01-01 00:00:00, not as printed text,
  because the engines print a timestamp differently. The datediff cases stay inside the
  2000 to 2050 window the trip model clamps to, because the seconds count is an int.

  Control characters are written with `chr(9)`, `chr(10)` and `chr(160)`: below 256 the two
  engines return the same character, and a backslash escape would not, because Spark
  unescapes string literals and DuckDB does not. The Arabic-Indic digits are written as a
  UTF-8 literal, because Spark's `chr` wraps above 255.

  Everything is compared as text (varchar(40)), so a number that prints differently on the
  two engines shows up as a mismatch.

  Text with a quote or a backslash is pinned too (WR-05): the `concat_ws` separator and the
  `dq_literal` text each hold both, and each engine escapes them by its own rule (DuckDB doubles
  a quote; Spark escapes a backslash and then a quote with a backslash). The expected values
  are built from `chr(39)` and `chr(92)`, which both engines agree on.

  `safe_divide` is pinned at exact rounding ties (WR-01): 2.01 / 32 is 0.062813 and -2.01 / 32
  is -0.062813, because both engines round half away from zero at the sixth decimal. A binary
  double on DuckDB used to give 0.062812.

  The date shims (date_part, date_add_days, date_key, iso_weekday) are pinned here too, at
  month ends, a leap day and dates on both sides of 1900-01-01. Phase 5 reruns these cases on
  Fabric.
-#}

with cases as (

    select
        cast('parse_int' as varchar(30)) as shim,
        cast('1' as varchar(40)) as input,
        cast(null as varchar(40)) as arg2,
        cast('1' as varchar(40)) as expected
    union all select 'parse_int', '1.0', null, '1'
    union all select 'parse_int', '1.5', null, null
    union all select 'parse_int', '', null, null
    union all select 'parse_int', ' 7 ', null, '7'
    union all select 'parse_int', 'abc', null, null
    union all select 'parse_int', null, null, null
    union all select 'parse_int', '-3', null, '-3'
    union all select 'parse_int', '+5', null, '5'
    union all select 'parse_int', '1_000', null, null
    union all select 'parse_int', '١٢', null, null

    union all select 'parse_decimal', '12.345678', null, '12.345678'
    union all select 'parse_decimal', '', null, null
    union all select 'parse_decimal', 'abc', null, null
    union all select 'parse_decimal', null, null, null
    union all select 'parse_decimal', '+5', null, '5.000000'
    union all select 'parse_decimal', '.5', null, '0.500000'
    union all select 'parse_decimal', '1.2345678', null, '1.234568'
    union all select 'parse_decimal', '1e3', null, null
    union all select 'parse_decimal', '1E3', null, null
    union all select 'parse_decimal', '1_000', null, null
    union all select 'parse_decimal', 'inf', null, null
    union all select 'parse_decimal', 'infinity', null, null
    union all select 'parse_decimal', 'nan', null, null
    union all select 'parse_decimal', '0x10', null, null
    union all select 'parse_decimal', '١٢', null, null
    union all select 'parse_decimal', chr(9) || '7' || chr(9), null, null
    union all select 'parse_decimal', '7' || chr(10), null, null
    union all select 'parse_decimal', chr(160) || '7', null, null
    union all select 'parse_decimal', '1234567890123', null, null

    union all select 'parse_date', '2024-03-15', null, '2024-03-15'
    union all select 'parse_date', '2024-02-30', null, null
    union all select 'parse_date', 'garbage', null, null
    union all select 'parse_date', '', null, null
    union all select 'parse_date', null, null, null
    union all select 'parse_date', ' 2024-03-15 ', null, '2024-03-15'
    union all select 'parse_date', '1000-01-01', null, '1000-01-01'
    union all select 'parse_date', '9999-12-31', null, '9999-12-31'
    union all select 'parse_date', '0000-01-01', null, null
    union all select 'parse_date', '10000-01-01', null, null
    union all select 'parse_date', '-2024-03-15', null, null
    union all select 'parse_date', '+2024-03-15', null, null
    union all select 'parse_date', '2024', null, null
    union all select 'parse_date', '2024-03', null, null
    union all select 'parse_date', '2024/03/15', null, null
    union all select 'parse_date', '2024-1-1', null, null
    union all select 'parse_date', '24-03-15', null, null
    union all select 'parse_date', 'epoch', null, null
    union all select 'parse_date', 'infinity', null, null
    union all select 'parse_date', '-infinity', null, null
    union all select 'parse_date', '2024-03-15 10:00:00', null, null

    union all select 'parse_timestamp', '2024-01-01 00:57:55', null, '3475'
    union all select 'parse_timestamp', '2024-01-01 00:57:55.123456', null, '3475'
    union all select 'parse_timestamp', '2024-01-01', null, '0'
    union all select 'parse_timestamp', ' 2024-01-01 00:00:00 ', null, '0'
    union all select 'parse_timestamp', 'garbage', null, null
    union all select 'parse_timestamp', '', null, null
    union all select 'parse_timestamp', '2024-13-45 99:99:99', null, null
    union all select 'parse_timestamp', '2024-02-30 00:00:00', null, null
    union all select 'parse_timestamp', null, null, null
    union all select 'parse_timestamp', '2024-01-01T00:57:55', null, '3475'
    union all select 'parse_timestamp', 'epoch', null, null
    union all select 'parse_timestamp', '2024-01-01 00:57:55Z', null, null
    union all select 'parse_timestamp', '2024-01-01 00:57:55+05:00', null, null
    union all select 'parse_timestamp', '2024-01-01 24:00:00', null, null
    union all select 'parse_timestamp', '2024', null, null
    union all select 'parse_timestamp', '2024-03', null, null
    union all select 'parse_timestamp', '2024-01-01 00', null, null
    union all select 'parse_timestamp', '2024-01-01  00:57:55', null, null

    union all select 'datediff_day', '2024-01-01', '2024-03-01', '60'
    union all select 'datediff_minute', '2024-01-01 00:00:00', '2024-01-01 01:30:00', '90'
    union all select 'datediff_second', '2024-01-01 00:00:00.9', '2024-01-01 00:00:01.1', '1'
    union all select 'datediff_second', '2000-01-01', '2050-01-01', '1577923200'
    union all select 'datediff_minute', '2024-01-01 00:00:59', '2024-01-01 00:01:00', '1'
    union all select 'datediff_day', '2024-01-01 23:59:59', '2024-01-02 00:00:00', '1'

    union all select 'safe_divide', '3', '2', '1.500000'
    union all select 'safe_divide', '3', '0', null
    union all select 'safe_divide_decimal', '2.01', '32', '0.062813'
    union all select 'safe_divide_decimal', '-2.01', '32', '-0.062813'
    union all select 'safe_divide_decimal', '2.01', '-32', '-0.062813'
    union all select 'safe_divide_decimal', '1', '3', '0.333333'
    union all select 'safe_divide_decimal', '2', '3', '0.666667'
    union all select 'safe_divide_decimal', '3', '2', '1.500000'
    union all select 'safe_divide_decimal', '3', '0', null

    union all select 'concat', 'a', 'b', 'ab'
    union all select 'concat_one', 'a', null, 'a'
    union all select 'concat', 'a', null, 'a'

    union all select 'concat_ws', 'a', null, 'a|c'
    union all select 'concat_ws_one', 'a', null, 'a'
    union all select 'concat_ws_nulls', 'n', null, ''
    union all select 'concat_ws_quote_backslash', 'a', 'b', 'a' || chr(39) || chr(92) || 'b'
    union all select 'dq_literal_quote_backslash', 'x', null, 'it' || chr(39) || 's a ' || chr(92) || ' b'

    union all select 'hash_key_one', 'abc', null, '900150983cd24fb0d6963f7d28e17f72'
    union all select 'hash_key', 'a', 'b', '8ca2ed590cf2ea2404f2e67641bcdf50'
    union all select 'hash_key', 'b', 'a', '31a6518d1b04a090784938645b4a6633'
    union all select 'hash_key', 'x', null, '5be06c497e6e6750a492923d5fd70a79'
    union all select 'hash_key', 'x', '', '8d4865ef3b48d4a022df6bd0023535f6'

    union all select 'date_trunc_month', '2024-03-15', null, '2024-03-01'
    union all select 'date_trunc_year', '2024-03-15', null, '2024-01-01'
    union all select 'date_trunc_day', '2024-03-15', null, '2024-03-15'

    union all select 'type_varchar', 'abc', null, 'abc'

    union all select 'date_part_year', '2024-03-15', null, '2024'
    union all select 'date_part_quarter', '2024-03-15', null, '1'
    union all select 'date_part_month', '2024-03-15', null, '3'
    union all select 'date_part_day', '2024-03-15', null, '15'

    union all select 'date_add_days', '2024-01-31', '1', '2024-02-01'
    union all select 'date_add_days', '2024-03-01', '-1', '2024-02-29'

    union all select 'date_key', '2024-01-05 10:00:00', null, '20240105'

    union all select 'iso_weekday', '2024-01-01', null, '1'
    union all select 'iso_weekday', '2024-01-07', null, '7'
    union all select 'iso_weekday', '1900-01-01', null, '1'
    union all select 'iso_weekday', '1899-12-31', null, '7'

),

actual as (

    select
        shim,
        input,
        expected,
        case
            when shim = 'parse_int'
                then cast({{ starter.parse_int('input') }} as varchar(40))
            when shim = 'parse_decimal'
                then cast({{ starter.parse_decimal('input') }} as varchar(40))
            when shim = 'parse_date'
                then cast({{ starter.parse_date('input') }} as varchar(40))
            when shim = 'parse_timestamp'
                then cast(
                    {{ starter.datediff('second', starter.parse_timestamp("'2024-01-01 00:00:00'"), starter.parse_timestamp('input')) }}
                    as varchar(40)
                )
            when shim = 'datediff_day'
                then cast({{ starter.datediff('day', starter.parse_timestamp('input'), starter.parse_timestamp('arg2')) }} as varchar(40))
            when shim = 'datediff_minute'
                then cast({{ starter.datediff('minute', starter.parse_timestamp('input'), starter.parse_timestamp('arg2')) }} as varchar(40))
            when shim = 'datediff_second'
                then cast({{ starter.datediff('second', starter.parse_timestamp('input'), starter.parse_timestamp('arg2')) }} as varchar(40))
            when shim = 'safe_divide'
                then cast(
                    {{ starter.safe_divide(starter.parse_int('input'), starter.parse_int('arg2')) }}
                    as varchar(40)
                )
            when shim = 'safe_divide_decimal'
                then cast(
                    {{ starter.safe_divide(starter.parse_decimal('input'), starter.parse_int('arg2')) }}
                    as varchar(40)
                )
            when shim = 'concat'
                then cast({{ starter.concat(['input', 'arg2', 'cast(null as varchar(5))']) }} as varchar(40))
            when shim = 'concat_one'
                then cast({{ starter.concat(['input']) }} as varchar(40))
            when shim = 'concat_ws'
                then cast({{ starter.concat_ws('|', ['input', 'arg2', "'c'"]) }} as varchar(40))
            when shim = 'concat_ws_one'
                then cast({{ starter.concat_ws('|', ['input']) }} as varchar(40))
            when shim = 'concat_ws_nulls'
                then cast({{ starter.concat_ws('|', ['cast(null as varchar(5))', 'cast(null as varchar(5))']) }} as varchar(40))
            when shim = 'concat_ws_quote_backslash'
                then cast({{ starter.concat_ws("'\\", ['input', 'arg2']) }} as varchar(40))
            when shim = 'dq_literal_quote_backslash'
                then cast('{{ starter.dq_literal("it's a \\ b") }}' as varchar(40))
            when shim = 'hash_key_one'
                then cast({{ starter.hash_key(['input']) }} as varchar(40))
            when shim = 'hash_key'
                then cast({{ starter.hash_key(['input', 'arg2']) }} as varchar(40))
            when shim = 'date_trunc_month'
                then cast({{ starter.date_trunc('month', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_trunc_year'
                then cast({{ starter.date_trunc('year', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_trunc_day'
                then cast({{ starter.date_trunc('day', starter.parse_date('input')) }} as varchar(40))
            when shim = 'type_varchar'
                then cast(cast(input as {{ starter.type_varchar(10) }}) as varchar(40))
            when shim = 'date_part_year'
                then cast({{ starter.date_part('year', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_part_quarter'
                then cast({{ starter.date_part('quarter', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_part_month'
                then cast({{ starter.date_part('month', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_part_day'
                then cast({{ starter.date_part('day', starter.parse_date('input')) }} as varchar(40))
            when shim = 'date_add_days'
                then cast(
                    {{ starter.date_add_days(starter.parse_date('input'), starter.parse_int('arg2')) }}
                    as varchar(40)
                )
            when shim = 'date_key'
                then cast({{ starter.date_key(starter.parse_timestamp('input')) }} as varchar(40))
            when shim = 'iso_weekday'
                then cast({{ starter.iso_weekday(starter.parse_date('input')) }} as varchar(40))
            else 'unhandled shim'
        end as actual
    from cases

)

select
    shim,
    input,
    expected,
    actual
from actual
where
    (actual is null and expected is not null)
    or (actual is not null and expected is null)
    or (actual <> expected)
