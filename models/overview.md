{% docs __overview__ %}

# dbt-starter

A medallion-layer dbt project: raw files land as text (landing), are typed and checked by rule flags (bronze), are split into good rows and quarantined rows (silver), and are shaped for analysis as a star schema (gold). These docs are generated from a real build on DuckDB, and the same models compile for Microsoft Fabric Warehouse.

## What we test where

Every test sits in the layer where the problem it catches can be fixed. A failure then points at the place to repair, not at a layer that only reports the symptom.

| Layer | Tests | Why here |
| --- | --- | --- |
| landing | `unique_combination_of_columns`, `undeclared_columns` | A duplicated or missing source row, or a column the source gained, is a loading fault. The fix is in the loader, so the source table is checked as it lands. |
| bronze | `unique`, `not_null`, `accepted_values`, `equal_rowcount`, `rows_equal_by_batch`, `rows_split_by_batch` | Bronze types the text and computes the rule flags. A repeated row key, a flag outside 0 and 1, or a row lost between landing and bronze is fixed in the loader or in the bronze rule SQL. |
| silver | `unique`, `not_null`, `accepted_values`, `relationships`, `unique_combination_of_columns`, `accepted_range`, `no_shared_keys`, `quarantine_rate_levels` | Silver splits good rows from quarantined rows. An unknown code is fixed by a rule or a seed, a duplicate by deduplication and an out-of-range value by a rule, so those checks live here, with the link from each failure to the rule catalog. The share of rows sent to quarantine is checked here against each entity's warn and error levels, so an error-level breach stops that entity's gold models before they are built. |
| gold | `unique`, `not_null`, `relationships`, `unique_combination_of_columns`, `totals_reconcile`, `date_span_complete` | Gold is the star schema an analyst queries. A fact key with no dimension member, a row lost from an aggregate or a gap in the calendar is fixed in the gold model, so grain, relationships and reconciliation are checked here. |
| seeds | `unique`, `not_null` | A seed is a code list built from a CSV. A duplicate or empty code is fixed in the CSV, so only its key is checked. |

Relationships tests belong in gold, with one named exception: every failure row in silver must name a registered rule, and the fix for an unknown rule is in the rule catalog that silver reads.

Accepted-values tests belong in silver, where an unknown code is fixed. Bronze keeps only the 0 or 1 check of a rule flag it computes itself, because a flag outside 0 and 1 is fixed in that bronze rule. A list of codes is always checked in silver.

<!-- example:begin -->
## Example test inventory

The key, relationships and accepted-values tests of the example, by relation. A code span with a star covers every column that matches it.

| Relation | Key tests | Relationships | Accepted values |
| --- | --- | --- | --- |
| `nyc_taxi.taxi_zones` | `_source_file + _source_row_number` | - | - |
| `nyc_taxi.yellow_trips` | `_source_file + _source_row_number` | - | - |
| `bronze__nyc_taxi__taxi_zones` | `zone_key` | - | `dq_fail_*`, `dq_is_valid` (0 or 1) |
| `bronze__nyc_taxi__yellow_trips` | `trip_key` | - | `dq_fail_*`, `*_found`, `dq_is_valid` (0 or 1) |
| `silver__dq_quarantine_summary` | `entity + rule_code` | - | - |
| `silver__dq_rule_catalog` | `code` | - | `severity`, `on_null` |
| `silver__dq_rule_failures` | `entity + row_key + rule_code` | `silver__dq_rule_failures.rule_code -> silver__dq_rule_catalog` | - |
| `silver__taxi_zones` | `zone_key`, `location_id` | - | `is_unknown_location`, `is_airport` |
| `silver__taxi_zones_quarantine` | `zone_key` | - | `dq_fail_*` (0 or 1) |
| `silver__yellow_trips` | `trip_key`, `duplicate_fingerprint` | - | `pu_is_unknown_zone`, `do_is_unknown_zone` |
| `silver__yellow_trips_quarantine` | `trip_key` | - | `dq_fail_*` (0 or 1) |
| `gold__dim_date` | `date_key`, `date_day` | - | - |
| `gold__dim_zone` | `location_id` | - | - |
| `gold__dim_vendor` | `vendor_id` | - | - |
| `gold__dim_rate_code` | `rate_code_id` | - | - |
| `gold__dim_payment_type` | `payment_type` | - | - |
| `gold__fct_trips` | `trip_key` | `gold__fct_trips.pickup_date_key -> gold__dim_date`, `gold__fct_trips.pu_location_id -> gold__dim_zone`, `gold__fct_trips.do_location_id -> gold__dim_zone`, `gold__fct_trips.vendor_id -> gold__dim_vendor`, `gold__fct_trips.rate_code_id -> gold__dim_rate_code`, `gold__fct_trips.payment_type -> gold__dim_payment_type` | - |
| `gold__agg_trips_daily` | `pickup_date_key + pickup_borough` | `gold__agg_trips_daily.pickup_date_key -> gold__dim_date` | - |
| `digits` | `digit` | - | - |
| `nyc_taxi__known_boroughs` | `borough` | - | - |
| `nyc_taxi__known_service_zones` | `service_zone` | - | - |
| `nyc_taxi__payment_types` | `payment_type` | - | - |
| `nyc_taxi__rate_codes` | `rate_code_id` | - | - |
| `nyc_taxi__vendors` | `vendor_id` | - | - |
<!-- example:end -->

{% enddocs %}
