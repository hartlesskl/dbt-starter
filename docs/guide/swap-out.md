# Swap out the example: add your own source

This guide adds one new source to the template, end to end, so you can see every convention at work
on data you chose. The worked example is the earthquake catalog of the U.S. Geological Survey
(USGS). It is public domain, it needs no account, and its `time` column ends in `Z`, which teaches
one real lesson about the parse rules. Follow the steps with the USGS data first. Then repeat them
with your own file, changing the names.

Every file the guide asks you to create is a fenced block whose first line says `file=<path>`. A
test writes exactly those blocks into a copy of this template, lands a small committed sample of the
data, and builds it on DuckDB, then compiles it for Fabric and scans the compiled SQL. So the
steps below build; they are not a sketch. The test is
[tests/guide/test_swap_out.py](../../tests/guide/test_swap_out.py), and the sample is a few dozen
rows kept as Python literals in [tests/guide/usgs_sample.py](../../tests/guide/usgs_sample.py).

## Before you start

Run `uv run remove-example` to see what the removal would do, then `uv run remove-example --apply`
to do it, and commit the result. That deletes the example and leaves a small placeholder source,
`example_source`, so the project still builds. Keep the placeholder until your own source builds,
and delete it last. The conventions you are about to follow are in [Conventions](conventions.md).

## 1. Describe the source

A source is a Python package under `src/starter_cli/`. It names the file to download, lists its
columns, and says whose data it is. Columns are lower case. The service publishes `magType`, and
the loader matches headers without regard to case, so you write `magtype`.

```python file=src/starter_cli/usgs/__init__.py
"""The USGS earthquake catalog: one entity, `quakes`, from the FDSN event service."""

from __future__ import annotations

import csv
from pathlib import Path

from starter_cli.landing import EntitySpec

SOURCE = "usgs"

# The name and link the published docs credit: the publisher and the page that states its terms.
# `uv run site` refuses a source that declares none.
DOCS_ATTRIBUTION = (
    "U.S. Geological Survey (USGS) Earthquake Catalog",
    "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
)

# The credit `load --help` echoes. The access date is the day you read the publisher's terms, not
# a download date. The disclaimer is the publisher's own words, copied exactly.
_NAME, _LINK = DOCS_ATTRIBUTION
_DISCLAIMER = (
    "USGS-authored or produced data and information are considered to be in the U.S. Public Domain."
)
_ACCESS_DATE = "2026-11-27"
DATA_TERMS = {
    "name": _NAME,
    "link": _LINK,
    "access_date": _ACCESS_DATE,
    "disclaimer": _DISCLAIMER,
    "credit_line": f'{_NAME}, {_LINK} (accessed {_ACCESS_DATE}). The USGS says: "{_DISCLAIMER}"',
}

ENTITIES: dict[str, EntitySpec] = {
    # A fixed two-hour window, so the download is the same file every time you run it.
    "quakes": EntitySpec(
        source=SOURCE,
        entity="quakes",
        url=(
            "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
            "&starttime=2024-01-01T04:00:00&endtime=2024-01-01T06:00:00&orderby=time-asc"
        ),
        file_name="quakes.csv",
        columns=(
            "time", "latitude", "longitude", "depth", "mag", "magtype", "nst", "gap", "dmin",
            "rms", "net", "id", "updated", "place", "type", "horizontalerror", "deptherror",
            "magerror", "magnst", "status", "locationsource", "magsource",
        ),
        header_prefix=b"time,latitude,longitude,depth,mag",
    ),
}

# Ordered (label, relation) pairs the demo prints, the silver relations whose warnings it counts,
# and the bronze = silver + quarantine check.
REPORT_RELATIONS: list[tuple[str, str]] = [
    ("landing", "landing.usgs__quakes"),
    ("bronze", "bronze.usgs__quakes"),
    ("silver", "silver.quakes"),
    ("quarantine", "silver.quakes_quarantine"),
]
REPORT_WARNINGS: list[str] = ["silver.quakes"]
REPORT_CONSERVATION: dict[str, tuple[str, str, str]] = {
    "quakes": ("bronze.usgs__quakes", "silver.quakes", "silver.quakes_quarantine"),
}


def _event(event_id: str, **changes: str) -> dict[str, str]:
    """One invented event. Every value is made up; `changes` overrides the columns a row plants."""
    row = {
        "time": "2024-02-01T00:00:00.000Z",
        "latitude": "10.5",
        "longitude": "20.5",
        "depth": "5.0",
        "mag": "2.5",
        "magtype": "ml",
        "id": event_id,
        "place": "an invented place",
        "type": "earthquake",
        "status": "reviewed",
    }
    return {**row, **changes}


# The gate builds from this fixture and downloads nothing: two rows that pass (one above the datum,
# which only warns) and one row for each error rule, so every rule fails at least one row.
FIXTURE_ROWS: list[dict[str, str]] = [
    _event("fx-1"),
    _event("fx-2", depth="-1.5"),
    _event(""),
    _event("fx-4", time="2024-02-01T00:00:00.000+05:00"),
    _event("fx-5", mag=""),
    _event("fx-6", latitude="91.5"),
]


def write_fixture(folder: Path) -> dict[str, tuple[str, ...]]:
    """Write the gate's fixture file into `folder`; return the months written per entity (none)."""
    folder.mkdir(parents=True, exist_ok=True)
    columns = ENTITIES["quakes"].columns
    with (folder / "quakes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, restval="", lineterminator="\n")
        writer.writeheader()
        writer.writerows(FIXTURE_ROWS)
    return {"quakes": ()}
```

The file is named `quakes.csv`, and the URL asks for the window `2024-01-01T04:00:00` to
`2024-01-01T06:00:00` with no magnitude floor, so small events and blank magnitudes come through as
they would in real data. A source that declares `DATA_TERMS` must also be credited in three places,
and a convention test checks that they agree with the module: a section in
[Data terms](../data-terms.md) that holds the link, the access date and the disclaimer word for word,
the credit line in your README, and the same three in your reading of the terms in
[ADR 0008](../decisions/0008-data-terms-and-license.md). For this source the section reads:

```markdown
## U.S. Geological Survey earthquake catalog

- Source: U.S. Geological Survey (USGS) Earthquake Catalog, https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits
- Accessed: 2026-11-27
- The USGS says: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain."
```

Read your own source's terms and write what you read, not what this guide says about the USGS. The
USGS asks for the credit "U.S. Geological Survey".

The Fabric tooling reads three more optional names from the same module. `FIXTURE_UNDECLARED_COLUMNS`
is how many columns your fixture file plants that the landing YAML does not declare, and it
defaults to 0. `ADDED_COLUMN_ENTITY` names the entity whose file the verification re-lands with one
extra column, to prove a new column is kept and reported. For a monthly source,
`FABRIC_DEFAULT_MONTHS` is the tuple of months a Fabric run lands by default. None of them is needed
for the DuckDB build below. They matter when you verify on Fabric, which
[Setting up Microsoft Fabric](../fabric-setup.md) explains.

## 2. Declare the landing table

The loader writes the landing table, not dbt, so this folder holds only a declaration. Landing keeps
every value as text. The row-key test says a row is identified by its file and its position in that
file. The `undeclared_columns` test warns when the file gains a column nobody described, so list all
22 columns here.

```yaml file=models/landing/usgs/_sources.yml
version: 2

sources:
  - name: usgs
    description: >
      The USGS earthquake catalog, landed by the loader exactly as published: every data column is
      text, an empty cell is NULL, and four lineage columns say which file and run each row came from.
    schema: landing
    config:
      # Generous on purpose: this guide downloads a fixed window once. A real feed should use
      # thresholds that match its cadence.
      freshness:
        warn_after: {count: 400, period: day}
        error_after: {count: 800, period: day}
      loaded_at_field: _loaded_at
    tables:
      - name: quakes
        identifier: usgs__quakes
        description: >
          One row per event of quakes.csv, as published, including events with blank
          magnitudes and events above sea level (a depth below zero).
        data_tests:
          - dbt_utils.unique_combination_of_columns:
              arguments:
                combination_of_columns: [_source_file, _source_row_number]
          - starter.undeclared_columns:
              name: quakes_undeclared_landing_columns
              config:
                severity: warn
        columns:
          - name: time
            description: Origin time of the event as text, such as 2024-01-01T04:05:02.992Z. The final Z means UTC.
          - name: latitude
            description: Latitude in decimal degrees, as text.
          - name: longitude
            description: Longitude in decimal degrees, as text.
          - name: depth
            description: Depth of the event in kilometres, as text. It is negative for an event above the datum.
          - name: mag
            description: Magnitude as text. It can be blank, and small events have negative magnitudes.
          - name: magtype
            description: The method used to calculate the magnitude, such as ml or mb.
          - name: nst
            description: Number of seismic stations used to find the location. Often blank.
          - name: gap
            description: Largest azimuthal gap between stations, in degrees. Often blank.
          - name: dmin
            description: Horizontal distance from the epicentre to the nearest station, in degrees. Often blank.
          - name: rms
            description: Root-mean-square travel time residual, in seconds.
          - name: net
            description: Identifier of the network that contributed the event.
          - name: id
            description: Unique event identifier.
          - name: updated
            description: Time the event was last updated, as text. The service revises past events now and then.
          - name: place
            description: Text description of the location, such as 3 km N of Reno, Nevada.
          - name: type
            description: Type of event, such as earthquake or quarry blast.
          - name: horizontalerror
            description: Uncertainty of the location, in kilometres. Often blank.
          - name: deptherror
            description: Uncertainty of the depth, in kilometres.
          - name: magerror
            description: Uncertainty of the magnitude. Often blank.
          - name: magnst
            description: Number of stations used to calculate the magnitude. Often blank.
          - name: status
            description: Review state of the event, automatic or reviewed.
          - name: locationsource
            description: Network that provided the location.
          - name: magsource
            description: Network that provided the magnitude.
          - name: _source_file
            description: Name of the file this row was loaded from. Together with _source_row_number it identifies the row.
          - name: _source_row_number
            description: Position of the row in its source file, counting from 1 in file order.
          - name: _batch_id
            description: Fingerprint of the file content (first 16 hex characters of its SHA-256). Identical files give the same value.
          - name: _loaded_at
            description: UTC time the loader wrote the row, without a time zone. Drives source freshness.
```

## 3. Type it in bronze

Bronze turns text into typed columns, keeps the original text next to anything a rule needs, gives
each row a stable key, and adds one 0/1 flag per rule. It removes no row.

The teaching step is the `time` column. The USGS writes `2024-01-01T04:05:02.992Z`, and the final `Z`
means UTC. The portable timestamp parse (`starter.parse_timestamp`) accepts a date, a `T` or a space,
and a time, and it returns NULL for any zone designator: `Z`, `+05:00` and `UTC` all fail. That is on
purpose. The project fixes the Spark session zone at UTC, so a time without a zone is read the same
on DuckDB and on Fabric, while a time with a zone would mean something else on each engine unless
every engine converted it. So this model removes the `Z` itself, with `translate(time, 'Z', '')`.
`translate` with an empty replacement deletes the character, it behaves the same on both engines,
and it is on the Spark scan's allowed list. The scan does not allow `replace`, so use `translate`.

Only a `Z` is removed. A time with an offset such as `+05:00` keeps its offset, fails the parse, and
the `QUAKE_TIME_UNPARSEABLE` rule sends the row to quarantine. That is better than reading the offset
as UTC and shifting the event by five hours without a word.

```sql file=models/bronze/usgs/bronze__usgs__quakes.sql
{% set rules = rules__usgs__quakes() %}
{% do starter.validate_rules(rules, 'QUAKE_') %}

with src as (

    select
        s.time,
        s.latitude,
        s.longitude,
        s.depth,
        s.mag,
        s.magtype,
        s.id,
        s.place,
        s.type,
        s.status,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at
    from {{ source('usgs', 'quakes') }} as s

),

typed as (

    select
        {{ starter.hash_key(['s._source_file', 's._source_row_number']) }} as quake_key,
        s.id as quake_id,
        s.time as time__raw,
        {{ starter.parse_timestamp("translate(s.time, 'Z', '')") }} as event_time,
        s.latitude as latitude__raw,
        {{ starter.parse_decimal('s.latitude') }} as latitude,
        {{ starter.parse_decimal('s.longitude') }} as longitude,
        {{ starter.parse_decimal('s.depth') }} as depth_km,
        s.mag as mag__raw,
        {{ starter.parse_decimal('s.mag') }} as magnitude,
        s.magtype as mag_type,
        s.place,
        s.type as event_type,
        s.status,
        s._source_file,
        s._source_row_number,
        s._batch_id,
        s._loaded_at
    from src as s

),

flagged as (

    select
        t.*,
        {{ starter.flag_columns(rules) }}
    from typed as t

),

final as (

    select
        f.*,
        {{ starter.valid_expr(rules) }} as dq_is_valid,
        {{ starter.failed_count_expr(rules) }} as dq_failed_rule_count,
        {{ starter.reason_codes_expr(rules) }} as dq_reason_codes,
        {{ starter.warning_count_expr(rules) }} as dq_warning_count,
        {{ starter.warning_codes_expr(rules) }} as dq_warning_codes
    from flagged as f

)

select * from final
```

## 4. Write the rules

A rule is a code, a one-line description, a severity, what to do with an unknown result, the
condition that fails it, and a rationale. An `error` rule sends the row to quarantine. A `warn` rule
only counts: the row is real but looks odd, so it stays in silver with a warning. Codes start with
the entity prefix, here `QUAKE_`, and never change once released. The rules below are the ones the
USGS data calls for:

- a missing id cannot key an event;
- a time that does not parse cannot be placed or ordered;
- a missing magnitude cannot be ranked (negative magnitudes are real, so there is no range rule);
- a latitude outside -90 to 90 is not on the Earth, and a blank or unreadable latitude fails too, which
  is what `on_null: fail` says;
- a negative depth is a warning, because some events really are above the datum.

```sql file=macros/rules/usgs/quakes.sql
{#- Quarantine rules for the USGS quakes. Codes start with QUAKE_ and never change once released.
    Error means the row is unusable and moves to quarantine. Warn means a real value that looks
    suspicious: the row stays in silver and is counted in dq_warning_count. The order below is the
    order of the codes in dq_reason_codes. -#}
{% macro rules__usgs__quakes() %}
  {{- return([
    {
      'code': 'QUAKE_ID_MISSING',
      'description': 'event id is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'An event with no id cannot be keyed or matched to a later revision of the same event, so it would sit in the table and join to nothing.',
      'fail_when': 'quake_id is null'
    },
    starter.cast_check_rule(
      'QUAKE_TIME_UNPARSEABLE',
      'time__raw',
      'event_time',
      'event time text is present but is not a timestamp',
      'A time that does not parse cannot be placed or ordered, usually because it carries an offset instead of Z or a format the parse does not accept.'
    ),
    {
      'code': 'QUAKE_MAGNITUDE_MISSING',
      'description': 'magnitude is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'An event with no magnitude, or one that is not a number, cannot be ranked or filtered by size. Some events are published before a magnitude is known.',
      'fail_when': 'magnitude is null'
    },
    {
      'code': 'QUAKE_LATITUDE_INVALID',
      'description': 'latitude is missing or outside -90 to 90',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'A latitude outside -90 to 90 is not a place on the Earth, usually a mistyped digit. A blank or unreadable latitude fails too, because the event cannot be placed.',
      'fail_when': 'latitude < -90 or latitude > 90'
    },
    {
      'code': 'QUAKE_DEPTH_NEGATIVE',
      'description': 'depth is below zero',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A negative depth means the event is above the datum, which happens for shallow events at altitude, so the row is real but unusual and stays in silver with a warning.',
      'fail_when': 'depth_km < 0'
    }
  ]) -}}
{% endmacro %}
```

Register the entity in the one list the cross-entity data quality models read. Keep the placeholder
entry while it exists, and add yours beside it.

```sql file=macros/dq/dq_entities.sql
{#-
  The one list of registered entities. The three cross-entity data quality models
  (dq_rule_failures, dq_quarantine_summary and dq_rule_catalog) read every entity from here, so
  adding an entity to the list is the only edit they need.

  Each entry is {entity, relation, row_key, rules}: a short name, the bronze model that carries
  one flag column per rule, the column that keys a row, and the entity's rule registry.
-#}
{% macro dq_entities() %}
  {%- set entities = [] -%}
  {%- do entities.append({'entity': 'items', 'relation': ref('bronze__example_source__items'), 'row_key': 'item_key', 'rules': rules__example_source__items()}) -%}
  {%- do entities.append({'entity': 'quakes', 'relation': ref('bronze__usgs__quakes'), 'row_key': 'quake_key', 'rules': rules__usgs__quakes()}) -%}
  {{- return(entities) -}}
{% endmacro %}
```

## 5. Split silver and quarantine

Silver keeps the rows that passed every error rule. The quarantine table keeps the rest, whole, with
the original text and the reason codes. Every bronze row lands in exactly one of the two, so
nothing is dropped.

```sql file=models/silver/usgs/silver__quakes.sql
select
    quake_key,
    _source_file,
    _source_row_number,
    _batch_id,
    quake_id,
    event_time,
    latitude,
    longitude,
    depth_km,
    magnitude,
    mag_type,
    place,
    event_type,
    status,
    dq_warning_count,
    dq_warning_codes
from {{ ref('bronze__usgs__quakes') }}
where dq_is_valid = 1
```

```sql file=models/silver/usgs/silver__quakes_quarantine.sql
{% set rules = rules__usgs__quakes() %}

select
    quake_key,
    _source_file,
    _source_row_number,
    _batch_id,
    _loaded_at,
    quake_id,
    time__raw,
    latitude__raw,
    mag__raw,
    {{ starter.flag_names(rules, 'error') }},
    dq_failed_rule_count,
    dq_reason_codes,
    dq_warning_count,
    dq_warning_codes
from {{ ref('bronze__usgs__quakes') }}
where dq_is_valid = 0
```

## 6. Test it

Four kinds of test cover the source, each in the layer where its failure is fixed.

- Counts: landing equals bronze, and bronze equals silver plus quarantine, file by file. Nothing is
  lost between layers.
- Keys: the row key is unique and never NULL in every table that carries it, and silver and
  quarantine share no key.
- Levels: `starter.quarantine_rate_levels` warns and then fails the build when too many rows
  are quarantined. Levels are fractions of rows, and you choose them from measured rates: run the
  source on a month or more of real data, read the rate in the demo's quarantine summary, and set the
  warn level a little above what normal data shows and the error level well above that. The values
  here (2% and 10%) fit a window where none of 34 real rows is quarantined, and a few dozen rows is
  too few to measure a real rate, so treat them as a start. [ADR 0005](../decisions/0005-quarantine-thresholds.md)
  explains the levels.
- Rules: one unit test per rule with a row that passes and a row that fails. A convention test
  checks that every flag is shown both at 0 and at 1.

```yaml file=models/bronze/usgs/_models.yml
version: 2

models:
  - name: bronze__usgs__quakes
    description: >
      The landed USGS events with every row kept. Text is converted to typed columns, each row gets a
      stable key, and one 0/1 flag per rule says whether the row broke that rule. Bad rows are not
      removed here: silver keeps the valid ones and the quarantine table keeps the rest.
    data_tests:
      - dbt_utils.equal_rowcount:
          arguments:
            compare_model: "source('usgs', 'quakes')"
      - starter.rows_equal_by_batch:
          name: quakes_landing_equals_bronze
          arguments:
            compare_model: "source('usgs', 'quakes')"
          config:
            severity: error
      - starter.rows_split_by_batch:
          name: quakes_bronze_equals_silver_plus_quarantine
          arguments:
            kept_model: "ref('silver__quakes')"
            rejected_model: "ref('silver__quakes_quarantine')"
          config:
            severity: error
    columns:
      - name: quake_key
        description: Row key, a hash of the source file name and the row number. It does not depend on the data values.
        data_tests:
          - unique
          - not_null
      - name: quake_id
        description: Event identifier as landed, or NULL when the cell was empty.
      - name: time__raw
        description: Event time exactly as landed, still text with its final Z.
      - name: event_time
        description: Event time in UTC as a timestamp without a zone, or NULL when the landed text is empty or does not parse.
      - name: latitude__raw
        description: Latitude exactly as landed, still text.
      - name: latitude
        description: Latitude in decimal degrees, or NULL when the landed text is empty or is not a number.
      - name: longitude
        description: Longitude in decimal degrees, or NULL when the landed text is empty or is not a number.
      - name: depth_km
        description: Depth in kilometres, negative above the datum, or NULL when the landed text is empty or is not a number.
      - name: mag__raw
        description: Magnitude exactly as landed, still text.
      - name: magnitude
        description: Magnitude as an exact decimal, or NULL when the landed text is empty or is not a number.
      - name: mag_type
        description: The method used to calculate the magnitude, as landed.
      - name: place
        description: Text description of the location, as landed.
      - name: event_type
        description: Type of event, such as earthquake or quarry blast, as landed.
      - name: status
        description: Review state of the event, as landed.
      - name: _source_file
        description: Name of the file this row was loaded from.
      - name: _source_row_number
        description: Position of the row in its source file, counting from 1 in file order.
      - name: _batch_id
        description: Fingerprint of the file content the row came from. Identical files give the same value.
      - name: _loaded_at
        description: UTC time the loader wrote the row, without a time zone.
      - name: dq_fail_quake_id_missing
        description: 1 when the event id is empty, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_time_unparseable
        description: 1 when the landed time text is present but is not a timestamp, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_magnitude_missing
        description: 1 when the magnitude is empty or is not a number, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_latitude_invalid
        description: 1 when the latitude is empty, is not a number or is outside -90 to 90, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_depth_negative
        description: "1 when the depth is below zero, otherwise 0. Never NULL. Warn rule QUAKE_DEPTH_NEGATIVE: the row stays in silver and the warning is counted in dq_warning_count."
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_is_valid
        description: 1 when no error rule flagged the row, otherwise 0. Silver keeps the rows with 1 and the quarantine table keeps the rows with 0.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_failed_rule_count
        description: How many error rules flagged the row. It is 0 for a valid row.
        data_tests:
          - not_null
      - name: dq_reason_codes
        description: The codes of the failed error rules, in rule order, joined with a vertical bar. NULL for a valid row. It is a display string, not a list to filter on.
      - name: dq_warning_count
        description: How many warn rules the row failed. It is 0 when no warn rule flagged the row. Warn rules never move a row to quarantine.
        data_tests:
          - not_null
      - name: dq_warning_codes
        description: The codes of the warn rules the row failed, joined with a vertical bar. NULL when there are none. It is a display string, not a list to filter on.
```

```yaml file=models/bronze/usgs/_unit_tests.yml
version: 2

# One unit test per rule. Each mocks the landing source with one row that passes the rule and one
# that fails it. Columns a row leaves out are NULL, so each row names only what the rule reads.
unit_tests:
  - name: quake_id_missing_rule
    description: >
      The missing id rule. Row 1 has an id and passes; row 2 has no id and fails with one reason.
    model: bronze__usgs__quakes
    given:
      - input: source('usgs', 'quakes')
        rows:
          - {time: '2024-01-01T04:05:02.992Z', latitude: '39.5624', mag: '1.1', id: 'nn1', _source_file: 'f.csv', _source_row_number: 1, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
          - {time: '2024-01-01T04:06:02.992Z', latitude: '39.5624', mag: '1.1', id: null, _source_file: 'f.csv', _source_row_number: 2, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
    expect:
      rows:
        - {_source_row_number: 1, dq_fail_quake_id_missing: 0, dq_is_valid: 1, dq_reason_codes: null}
        - {_source_row_number: 2, dq_fail_quake_id_missing: 1, dq_is_valid: 0, dq_reason_codes: 'QUAKE_ID_MISSING'}

  - name: quake_time_unparseable_rule
    description: >
      The unparseable time rule. Row 1 ends in Z, which bronze removes, so it parses and passes. Row 2
      carries an offset instead, which is kept, so the parse refuses it and the row fails.
    model: bronze__usgs__quakes
    given:
      - input: source('usgs', 'quakes')
        rows:
          - {time: '2024-01-01T04:05:02.992Z', latitude: '39.5624', mag: '1.1', id: 'nn1', _source_file: 'f.csv', _source_row_number: 1, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
          - {time: '2024-01-01T04:05:02.992+05:00', latitude: '39.5624', mag: '1.1', id: 'nn2', _source_file: 'f.csv', _source_row_number: 2, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
    expect:
      rows:
        - {_source_row_number: 1, dq_fail_quake_time_unparseable: 0, dq_is_valid: 1, dq_reason_codes: null}
        - {_source_row_number: 2, dq_fail_quake_time_unparseable: 1, dq_is_valid: 0, dq_reason_codes: 'QUAKE_TIME_UNPARSEABLE'}

  - name: quake_magnitude_missing_rule
    description: >
      The missing magnitude rule. Row 1 has a negative magnitude, which is real, and passes; row 2 has
      a blank magnitude and fails.
    model: bronze__usgs__quakes
    given:
      - input: source('usgs', 'quakes')
        rows:
          - {time: '2024-01-01T04:05:02.992Z', latitude: '39.5624', mag: '-0.1', id: 'nn1', _source_file: 'f.csv', _source_row_number: 1, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
          - {time: '2024-01-01T04:06:02.992Z', latitude: '39.5624', mag: null, id: 'nn2', _source_file: 'f.csv', _source_row_number: 2, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
    expect:
      rows:
        - {_source_row_number: 1, dq_fail_quake_magnitude_missing: 0, dq_is_valid: 1, dq_reason_codes: null}
        - {_source_row_number: 2, dq_fail_quake_magnitude_missing: 1, dq_is_valid: 0, dq_reason_codes: 'QUAKE_MAGNITUDE_MISSING'}

  - name: quake_latitude_invalid_rule
    description: >
      The latitude rule. Row 1 is in range and passes; row 2 is 139.5624, a mistyped digit, and fails.
    model: bronze__usgs__quakes
    given:
      - input: source('usgs', 'quakes')
        rows:
          - {time: '2024-01-01T04:05:02.992Z', latitude: '39.5624', mag: '1.1', id: 'nn1', _source_file: 'f.csv', _source_row_number: 1, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
          - {time: '2024-01-01T04:06:02.992Z', latitude: '139.5624', mag: '1.1', id: 'nn2', _source_file: 'f.csv', _source_row_number: 2, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
    expect:
      rows:
        - {_source_row_number: 1, dq_fail_quake_latitude_invalid: 0, dq_is_valid: 1, dq_reason_codes: null}
        - {_source_row_number: 2, dq_fail_quake_latitude_invalid: 1, dq_is_valid: 0, dq_reason_codes: 'QUAKE_LATITUDE_INVALID'}

  - name: quake_depth_negative_rule
    description: >
      The warn rule. Row 1 has a positive depth and passes; row 2 is above the datum and fails the
      rule, but the row stays valid and only the warning is counted.
    model: bronze__usgs__quakes
    given:
      - input: source('usgs', 'quakes')
        rows:
          - {time: '2024-01-01T04:05:02.992Z', latitude: '39.5624', depth: '5.4', mag: '1.1', id: 'nn1', _source_file: 'f.csv', _source_row_number: 1, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
          - {time: '2024-01-01T04:06:02.992Z', latitude: '39.5624', depth: '-0.15', mag: '1.1', id: 'nn2', _source_file: 'f.csv', _source_row_number: 2, _batch_id: 'b1', _loaded_at: '2024-01-02 00:00:00'}
    expect:
      rows:
        - {_source_row_number: 1, dq_fail_quake_depth_negative: 0, dq_is_valid: 1, dq_warning_codes: null}
        - {_source_row_number: 2, dq_fail_quake_depth_negative: 1, dq_is_valid: 1, dq_warning_codes: 'QUAKE_DEPTH_NEGATIVE'}
```

```yaml file=models/silver/usgs/_models.yml
version: 2

models:
  - name: silver__quakes
    description: >
      The USGS events that passed every error rule, one row per event. An event with a negative depth
      is here with a warning, because that rule only warns.
    data_tests:
      - starter.no_shared_keys:
          name: quakes_silver_and_quarantine_do_not_overlap
          arguments:
            other_model: "ref('silver__quakes_quarantine')"
            column_name: quake_key
          config:
            severity: error
      # Levels are fractions of rows. Choose yours from measured rates (ADR 0005). The second
      # parent must stay the bronze model, so an error breach skips this entity's gold models.
      - starter.quarantine_rate_levels:
          name: quakes_quarantine_rate_within_levels
          arguments:
            all_rows_model: "ref('bronze__usgs__quakes')"
            warn_above: 0.02  # 2% of rows
            error_above: 0.1  # 10% of rows
    columns:
      - name: quake_key
        description: Row key carried from bronze, a hash of the source file name and the row number.
        data_tests:
          - unique
          - not_null
      - name: _source_file
        description: Name of the file this row was loaded from.
      - name: _source_row_number
        description: Position of the row in its source file, counting from 1 in file order.
      - name: _batch_id
        description: Fingerprint of the file content the row came from. It lets a count check compare silver with bronze one file at a time.
      - name: quake_id
        description: Event identifier. Valid rows always have one.
        data_tests:
          - not_null
      - name: event_time
        description: Event time in UTC, without a time zone. Valid rows always have one.
        data_tests:
          - not_null
      - name: latitude
        description: Latitude in decimal degrees, between -90 and 90.
        data_tests:
          - not_null
      - name: longitude
        description: Longitude in decimal degrees. It can be NULL, because no rule requires it.
      - name: depth_km
        description: Depth in kilometres. Negative above the datum, and NULL when the file had none.
      - name: magnitude
        description: Magnitude as an exact decimal. Valid rows always have one, and it can be negative.
        data_tests:
          - not_null
      - name: mag_type
        description: The method used to calculate the magnitude, as landed.
      - name: place
        description: Text description of the location, as landed.
      - name: event_type
        description: Type of event, such as earthquake or quarry blast, as landed.
      - name: status
        description: Review state of the event, as landed.
      - name: dq_warning_count
        description: How many warn rules the row failed. It is 0 when no warn rule flagged the row.
        data_tests:
          - not_null
      - name: dq_warning_codes
        description: The codes of the warn rules the row failed, joined with a vertical bar. NULL when there are none.

  - name: silver__quakes_quarantine
    description: >
      The USGS events that broke at least one error rule, kept whole: the landed text, the load
      lineage, one 0/1 flag per error rule and the reason codes. Every bronze row is in silver or in
      this table, never both. The table exists even when no event failed, and is then empty.
    columns:
      - name: quake_key
        description: Row key carried from bronze, a hash of the source file name and the row number.
        data_tests:
          - unique
          - not_null
      - name: _source_file
        description: Name of the file this row was loaded from.
      - name: _source_row_number
        description: Position of the row in its source file, counting from 1 in file order.
      - name: _batch_id
        description: Fingerprint of the file content the row came from. It ties a quarantined row to the load that produced it.
      - name: _loaded_at
        description: UTC time the loader wrote the row, without a time zone.
      - name: quake_id
        description: Event identifier as landed. It is NULL when the id was missing.
      - name: time__raw
        description: Event time exactly as landed, still text.
      - name: latitude__raw
        description: Latitude exactly as landed, still text.
      - name: mag__raw
        description: Magnitude exactly as landed, still text.
      - name: dq_fail_quake_id_missing
        description: 1 when the event id is empty, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_time_unparseable
        description: 1 when the landed time text is present but is not a timestamp, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_magnitude_missing
        description: 1 when the magnitude is empty or is not a number, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_fail_quake_latitude_invalid
        description: 1 when the latitude is empty, is not a number or is outside -90 to 90, otherwise 0. Never NULL.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 1]
                quote: false
      - name: dq_failed_rule_count
        description: How many error rules flagged the row. Always at least 1 in this table.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
      - name: dq_reason_codes
        description: The codes of the failed error rules, in rule order, joined with a vertical bar. It is for display, so filter on the flag columns instead of splitting it.
        data_tests:
          - not_null
      - name: dq_warning_count
        description: How many warn rules the row failed. It is 0 when no warn rule flagged the row.
      - name: dq_warning_codes
        description: The codes of the warn rules the row failed, joined with a vertical bar. NULL when there are none.
```

## 7. Document it

Every model, column and source table needs a description, and the gate fails a blank one. You wrote
them as you went: they are the `description` lines above. The docs site is built from the same
text, so the descriptions are what a reader of the published docs sees, and the lineage graph shows
the path from `landing.usgs__quakes` through bronze to silver and quarantine. A description says what
the value means and how it can be empty, in plain words.

## 8. Build and check

Land the file, build every layer, and read the summary, then run the whole pull request check:

```text
uv run load usgs quakes
uv run demo
make gate
```

`uv run load usgs quakes` downloads the fixed window once into `data/raw/usgs/` and writes it to the
landing table. `uv run demo` lands every source, builds bronze, silver and gold with their tests,
and prints a row count per layer, so you should see `usgs` in the landing, bronze, silver and
quarantine lines. The quarantine summary shows each rule and how many rows it failed. Real rows
from the window fail no error rule, so the quarantine stays empty, and the two events above the
datum show as warnings.

`make gate` builds from a fixture, not a download. That is why the source module above has a
`write_fixture` function: two invented rows that pass and one planted row for each error rule, the
way the placeholder does. For exact counts that the build must reproduce, copy the pattern of the
placeholder's `tests/example_source/test_fixture_counts.py`: tag each fixture row with the rules it
plants, derive the counts in Python, and compare them with what the build holds. The gate checks the
new source in every way the placeholder is checked: descriptions, the unit test of each rule, test
placement, layer direction, the offline Fabric compile and the Spark scan.

While the placeholder is in the tree, the gate reports two failures, both from the credit rule: the
placeholder declares no `DOCS_ATTRIBUTION`, so the root page of the docs site cannot be built for
both sources. Both pass once the placeholder is gone. Replacing it is the last step and this guide
does not cover it, because other files name it: `tests/conventions/test_selectors.py` tags its
model, the Fabric verification expects one source to declare `ADDED_COLUMN_ENTITY`, and the contract
test expects two gold models, so first give your entity a gold model like the placeholder's.

## Download the real data

The real query is the one in the source module. Run `uv run load usgs quakes` for the fixed
window, or change `starttime` and `endtime` for another window. Keep a window small enough for
your laptop: a day of the whole catalog is about 500 rows, and a year runs to hundreds of thousands.
Credit the data as the USGS asks: "U.S. Geological Survey". The USGS data is in the public domain, and
the decision record [ADR 0008](../decisions/0008-data-terms-and-license.md) holds the reading of its terms.

CI never downloads. The guide's test lands a committed sample of the same window: 34 real rows
and 3 planted rows that fail a rule on purpose, kept as Python literals in
[tests/guide/usgs_sample.py](../../tests/guide/usgs_sample.py). The USGS revises past events now and
then, so a file you download later can differ from the sample.
