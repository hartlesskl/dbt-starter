# Conventions

This template follows eight conventions. The gate enforces each one, so a change that breaks a
rule fails before it merges. Read this page before you keep or change a rule. Each topic below
gives the rule, a short reason, and links to the decision record that holds the depth and to the
test, macro or module that enforces it.

## Naming

A model file starts with its layer: `bronze__<source>__<entity>`, `silver__<entity>`,
`silver__<entity>_quarantine`, and in gold `gold__fct_<name>`, `gold__dim_<name>` or
`gold__agg_<name>`. The table name is the file name without the layer prefix, so
`bronze__<source>__<entity>` builds `bronze.<source>__<entity>` and `gold__dim_zone` builds
`gold.dim_zone`. Seeds keep their own name. A rule code is UPPER_SNAKE and starts with its entity's
prefix.

Why: the layer in the file name tells a reader where a model sits before they open it, and the
schema holds the table name without repeating the layer. A code that starts with its entity cannot
collide with a code from another entity, and a failure row names its rule on sight.

Enforced by: [the layer naming test](../../tests/conventions/test_layers.py), the
[alias macro](../../macros/config/generate_alias_name.sql) and
[the rule validator](../../packages/starter/macros/dq/rules.sql). The `fct_`, `dim_` and `agg_`
words are a convention that review keeps, not a test. Depth: [ADR 0006](../decisions/0006-fabric-lakehouse-retarget.md).

<!-- example:begin -->
In the example, `bronze__nyc_taxi__yellow_trips` builds `bronze.nyc_taxi__yellow_trips`, and its
rule codes start with `TRIP_`, as in [the rules file](../../macros/rules/nyc_taxi/yellow_trips.sql).
<!-- example:end -->

## Layers

Data moves through four layers: landing, bronze, silver and gold. Landing holds the loaded text,
bronze types it and flags each row, silver splits good rows from quarantined rows, and gold shapes
the good rows for analysis. A model reads only the layer before it, plus seeds. Skipping a layer
or reading the same layer is an error, unless one named pair is listed as an exception.

Why: each layer has one job, so a problem has one place to be fixed. A gold model that reads
bronze would bypass the quarantine, and a number in gold could then include rows that silver
rejected.

Enforced by: [the layer direction test](../../tests/conventions/test_layer_direction.py), which
holds the rule in one table, and [the layer test](../../tests/conventions/test_layers.py). Depth:
[ADR 0005](../decisions/0005-quarantine-thresholds.md) shows how a quarantine breach stops gold,
and [ADR 0006](../decisions/0006-fabric-lakehouse-retarget.md) holds the layout.

## Schemas

There are exactly four schemas, `landing`, `bronze`, `silver` and `gold`, on both targets. A
layer's folder, its file prefix and its schema are the same word. DuckDB builds them in one
database file. On Fabric they are the four schemas of one schema-enabled Lakehouse, with three-part
names.

Why: one word for the folder, the prefix and the schema means a reader never translates between
them. Four schemas in one Lakehouse is the layout the Fabric adapter supports for writing across
layers.

Enforced by: [the schema macro](../../macros/config/generate_schema_name.sql), which uses the
layer name as written, and [the layer test](../../tests/conventions/test_layers.py), which checks
the built database holds exactly these four schemas. Depth:
[ADR 0006](../decisions/0006-fabric-lakehouse-retarget.md).

## Typing

Landing holds every column as text. Bronze turns text into types through the `starter` parse
shims and keeps every row, even one that does not parse, so a bad value becomes a flag and not a
lost row. Money and measures are exact decimals, never floating point. Gold models carry an
enforced contract with a short list of portable types.

Why: a loader that guesses types fails on the first odd value and hides it. Keeping text first and
typing in bronze lets the quarantine explain every row it rejects. A float turns a sum into an
estimate, and a total that does not reconcile is a bug that is hard to see.

Enforced by: [the contracts test](../../tests/conventions/test_contracts.py), which also refuses a
float in silver and gold, and [the parse shims](../../packages/starter/macros/shim/parse.sql).
Depth: [ADR 0002](../decisions/0002-starter-shims.md) and
[ADR 0004](../decisions/0004-gold-contracts.md).

## Landing metadata

The loader adds four columns to every landed row: `_source_file`, `_source_row_number`,
`_batch_id` and `_loaded_at`. The batch id is a fingerprint of the file's content. Loading a file
replaces that file's rows and nothing else. A column the source gains is kept as text and reported
by a warning, never dropped and never a failure.

Why: the file name and row number identify a row for as long as the data lives, so every failure
points back to a source line. Replacing by file makes a load safe to repeat. Keeping a new column
tells the team it exists without stopping the run.

Enforced by: [the landing module](../../src/starter_cli/landing.py),
[the source contract test](../../tests/conventions/test_source.py) and the
[undeclared columns test](../../packages/starter/macros/tests/undeclared_columns.sql). Depth:
[ADR 0007](../decisions/0007-lakehouse-landing-sink.md) holds the Lakehouse sink, and
[ADR 0008](../decisions/0008-data-terms-and-license.md) holds what a source must say about its terms.

## The quarantine design

Every entity has one rule registry. Each rule has a code, a severity, a predicate and a
rationale. Bronze computes one flag per rule, an integer that is never NULL, and one overall
validity flag. An `error` rule sends the row to the entity's quarantine model and keeps it out of
silver. A `warn` rule only flags the row, which stays in silver. Three shared models report on
the result: the long-form list of failures, a summary per rule and a catalog of the rules. Each
entity also has warn and error levels for its share of quarantined rows, and an error-level breach
stops that entity's gold models.

Why: a rejected row that leaves no trace is lost data. Never-NULL flags keep the split exact, with
no row that is neither good nor bad. Levels turn a bad month of data into a stopped build instead
of a quietly shrinking fact table.

Enforced by: [the rule registry checks](../../tests/conventions/test_rules.py),
[the levels test](../../tests/conventions/test_quarantine_levels.py), the
[rule framework](../../packages/starter/macros/dq/rules.sql) and the
[rate test](../../packages/starter/macros/tests/quarantine_rate.sql). Depth:
[ADR 0005](../decisions/0005-quarantine-thresholds.md).

<!-- example:begin -->
The example's rules live in [one file per entity](../../macros/rules/nyc_taxi/yellow_trips.sql).
<!-- example:end -->

## Test placement

Each key, relationship and accepted-values test sits in the layer where its failure can be fixed.
Row keys are tested in landing and bronze, accepted values in silver, and relationships and
reconciliation in gold. Bronze checks only the 0 or 1 range of the flags it computes. A failure
then points at the place to repair, and not at a layer that only shows the symptom.

Why: a relationships test in bronze fails on a code that only a rule or a seed in silver can
fix. Putting the test where the fix is saves the search.

Enforced by: [the placement test](../../tests/conventions/test_test_placement.py), which holds the
policy in one place. The table of what is tested where is in
[the project overview](../../models/overview.md). Depth:
[ADR 0005](../decisions/0005-quarantine-thresholds.md) places the rate test in silver, and
[ADR 0004](../decisions/0004-gold-contracts.md) explains why data tests stay the real enforcement
for gold keys.

## Portable SQL

Models use the SQL that both DuckDB and Fabric's Spark SQL accept. Every difference between the
engines goes through a `starter` shim, which has an explicit `duckdb__` body and an explicit
`fabricspark__` body. Models never call dbt's own cross-database macros, never branch on the
target, and never use a DuckDB-only construct such as `QUALIFY` or the `::` cast. Fabric's
adapter looks up only its own prefix, so a missing body would fail only on that engine.

Why: one codebase for both targets is the template's promise. The shims make the engines accept
and reject the same text, so a model that runs on a laptop also runs in Fabric.

Enforced by: [the portability test](../../tests/conventions/test_portability.py), which bans the
cross-database macros and checks every shim body, [the SQL scan test](../../tests/conventions/test_sql_scan.py)
and [its scanner](../../tests/support/sql_scan.py), which read the offline Fabric compile and reject
what Spark rejects, and [the compile test](../../tests/conventions/test_fabric_compile.py). Depth:
[ADR 0002](../decisions/0002-starter-shims.md) and
[ADR 0006](../decisions/0006-fabric-lakehouse-retarget.md).
