{{ config(tags=['conversion_fixture']) }}

{#-
  Shared conversion fixture for the starter shims (PORT-02).

  A singular test: it returns one row for every case whose actual value differs from the
  expected value, and a passing run returns zero rows. Each mismatch row names the shim, the
  input, the expected value and the actual value.

  It runs in every `dbt build` on DuckDB and compiles unchanged for Fabric. Phase 5 reruns the
  same file on Fabric with `dbt test --select tag:conversion_fixture --target fabric`.

  Inputs are limited to text the loader can produce. Exponent text ('1e3'), hex text ('0x10')
  and infinity text ('inf') are left out on purpose: DuckDB accepts them as numbers and T-SQL
  decimal casts do not, so an expected-equal row for them would encode a divergence between
  the two engines instead of testing a shim. The same holds for the timestamp texts 'infinity'
  and 'epoch': DuckDB converts them and T-SQL returns NULL, so they stay out of the
  parse_timestamp cases.

  A timestamp is compared as a count of seconds from 2024-01-01 00:00:00, not as printed text,
  because the two engines print a timestamp differently. The datediff cases stay inside the
  2000 to 2050 window the trip model clamps to, because a T-SQL seconds count is an int.

  Everything is compared as text (varchar(40)), so a number that prints differently on the
  two engines shows up as a mismatch.
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

    union all select 'parse_decimal', '12.345678', null, '12.345678'
    union all select 'parse_decimal', '', null, null
    union all select 'parse_decimal', 'abc', null, null
    union all select 'parse_decimal', null, null, null

    union all select 'parse_date', '2024-03-15', null, '2024-03-15'
    union all select 'parse_date', '2024-02-30', null, null
    union all select 'parse_date', 'garbage', null, null
    union all select 'parse_date', '', null, null
    union all select 'parse_date', null, null, null

    union all select 'parse_timestamp', '2024-01-01 00:57:55', null, '3475'
    union all select 'parse_timestamp', '2024-01-01 00:57:55.123456', null, '3475'
    union all select 'parse_timestamp', '2024-01-01', null, '0'
    union all select 'parse_timestamp', ' 2024-01-01 00:00:00 ', null, '0'
    union all select 'parse_timestamp', 'garbage', null, null
    union all select 'parse_timestamp', '', null, null
    union all select 'parse_timestamp', '2024-13-45 99:99:99', null, null
    union all select 'parse_timestamp', '2024-02-30 00:00:00', null, null
    union all select 'parse_timestamp', null, null, null

    union all select 'datediff_day', '2024-01-01', '2024-03-01', '60'
    union all select 'datediff_minute', '2024-01-01 00:00:00', '2024-01-01 01:30:00', '90'
    union all select 'datediff_second', '2024-01-01 00:00:00.9', '2024-01-01 00:00:01.1', '1'
    union all select 'datediff_second', '2000-01-01', '2050-01-01', '1577923200'

    union all select 'safe_divide', '3', '2', '1.500000'
    union all select 'safe_divide', '3', '0', null

    union all select 'concat', 'a', 'b', 'ab'
    union all select 'concat_one', 'a', null, 'a'

    union all select 'concat_ws', 'a', null, 'a|c'
    union all select 'concat_ws_one', 'a', null, 'a'

    union all select 'hash_key_one', 'abc', null, '900150983cd24fb0d6963f7d28e17f72'
    union all select 'hash_key', 'a', 'b', '8ca2ed590cf2ea2404f2e67641bcdf50'
    union all select 'hash_key', 'b', 'a', '31a6518d1b04a090784938645b4a6633'
    union all select 'hash_key', 'x', null, '5be06c497e6e6750a492923d5fd70a79'

    union all select 'date_trunc_month', '2024-03-15', null, '2024-03-01'
    union all select 'date_trunc_year', '2024-03-15', null, '2024-01-01'
    union all select 'date_trunc_day', '2024-03-15', null, '2024-03-15'

    union all select 'type_varchar', 'abc', null, 'abc'

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
            when shim = 'concat'
                then cast({{ starter.concat(['input', 'arg2', 'cast(null as varchar(5))']) }} as varchar(40))
            when shim = 'concat_one'
                then cast({{ starter.concat(['input']) }} as varchar(40))
            when shim = 'concat_ws'
                then cast({{ starter.concat_ws('|', ['input', 'arg2', "'c'"]) }} as varchar(40))
            when shim = 'concat_ws_one'
                then cast({{ starter.concat_ws('|', ['input']) }} as varchar(40))
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
