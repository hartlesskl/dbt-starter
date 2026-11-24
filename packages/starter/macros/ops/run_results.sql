{#-
  The run-results hook (D-10): after every dbt invocation, `store_run_results` records each
  executed node and the invocation itself in the `verification` schema, so a build that runs
  where no `run_results.json` can be read (Fabric's native dbt job) still leaves its results
  in the Lakehouse for the verification command to query.

  The hook is opt-in by table existence. It writes only when `verification.run_results`
  exists, and only `create_verification_tables` creates it (the verification command's
  `init` calls it). An adopter who never creates the table pays one metadata lookup per run,
  gets no schema and no table, and sees no extra output. The hook renders no SQL of its own,
  so dbt skips the empty hook statement. `dbt compile` records nothing: it executes no node, and
  the gate's offline Fabric compile has no connection to look the table up on.

  Both macros are dispatched with an explicit `duckdb__` and `fabricspark__` body. Spark text
  columns are `string` and timestamps are the plain `timestamp` type. Every text value goes
  through `starter.string_literal`, which escapes quotes and backslashes per engine.
-#}

{#- Run it once with `dbt run-operation starter.create_verification_tables`. -#}
{% macro create_verification_tables() %}
  {{- return(adapter.dispatch('create_verification_tables', 'starter')()) -}}
{% endmacro %}

{% macro default__create_verification_tables() %}
  {{- exceptions.raise_compiler_error('starter.create_verification_tables is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{% macro duckdb__create_verification_tables() %}
  {%- if execute -%}
    {%- do run_query('create schema if not exists verification') -%}
    {%- do run_query('create table if not exists verification.run_results (invocation_id varchar, unique_id varchar, resource_type varchar, status varchar, failures bigint, execution_time double, message varchar, recorded_at timestamp)') -%}
    {%- do run_query('create table if not exists verification.invocations (invocation_id varchar, dbt_version varchar, target_type varchar, command varchar, full_refresh boolean, engine_version varchar, session_time_zone varchar, recorded_at timestamp)') -%}
  {%- endif -%}
{% endmacro %}

{% macro fabricspark__create_verification_tables() %}
  {%- if execute -%}
    {%- do run_query('create schema if not exists verification') -%}
    {%- do run_query('create table if not exists verification.run_results (invocation_id string, unique_id string, resource_type string, status string, failures bigint, execution_time double, message string, recorded_at timestamp)') -%}
    {%- do run_query('create table if not exists verification.invocations (invocation_id string, dbt_version string, target_type string, command string, full_refresh boolean, engine_version string, session_time_zone string, recorded_at timestamp)') -%}
  {%- endif -%}
{% endmacro %}


{#- Wired as `on-run-end: ["{{ starter.store_run_results(results) }}"]` in dbt_project.yml. -#}
{% macro store_run_results(results) %}
  {{- return(adapter.dispatch('store_run_results', 'starter')(results)) -}}
{% endmacro %}

{% macro default__store_run_results(results) %}
  {{- exceptions.raise_compiler_error('starter.store_run_results is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- One VALUES tuple per executed node. `failures` is a test's failing-row count and is
    NULL for every other node; the message is cut to its first 500 characters. -#}
{% macro _run_result_rows(results, recorded_at) %}
  {%- set rows = [] -%}
  {%- for result in results -%}
    {%- set message = none if result.message is none else (result.message | string)[:500] -%}
    {%- do rows.append(
        '(' ~ starter.string_literal(invocation_id)
        ~ ', ' ~ starter.string_literal(result.node.unique_id)
        ~ ', ' ~ starter.string_literal(result.node.resource_type | string)
        ~ ', ' ~ starter.string_literal(result.status | string)
        ~ ', ' ~ (result.failures if result.failures is not none else 'null')
        ~ ', ' ~ ((result.execution_time or 0) | round(3))
        ~ ', ' ~ starter.string_literal(message)
        ~ ', ' ~ recorded_at ~ ')'
    ) -%}
  {%- endfor -%}
  {{- return(rows) -}}
{% endmacro %}

{% macro duckdb__store_run_results(results) %}
  {%- if execute and flags.WHICH != 'compile' -%}
    {%- set marker = adapter.get_relation(database=target.database, schema='verification', identifier='run_results') -%}
    {%- if marker is not none -%}
      {%- set now = "cast(current_timestamp at time zone 'UTC' as timestamp)" -%}
      {%- set rows = starter._run_result_rows(results, now) -%}
      {%- if rows | length > 0 -%}
        {%- do run_query('insert into verification.run_results values ' ~ (rows | join(', '))) -%}
      {%- endif -%}
      {%- set engine = run_query("select cast(version() as varchar), cast(current_setting('TimeZone') as varchar)") -%}
      {%- do run_query(
          'insert into verification.invocations values ('
          ~ starter.string_literal(invocation_id)
          ~ ', ' ~ starter.string_literal(dbt_version)
          ~ ', ' ~ starter.string_literal(target.type)
          ~ ', ' ~ starter.string_literal(flags.WHICH)
          ~ ', ' ~ ('true' if flags.FULL_REFRESH else 'false')
          ~ ', ' ~ starter.string_literal(engine.rows[0][0])
          ~ ', ' ~ starter.string_literal(engine.rows[0][1])
          ~ ', ' ~ now ~ ')'
      ) -%}
      {#- The hook's own statements run in no dbt-managed commit, so commit them here. -#}
      {%- do adapter.commit() -%}
    {%- endif -%}
  {%- endif -%}
{% endmacro %}

{#- The Fabric target dict has no `database` key; the adapter derives its database from the
    Lakehouse name, so the lookup passes `target.lakehouse`. -#}
{% macro fabricspark__store_run_results(results) %}
  {%- if execute and flags.WHICH != 'compile' -%}
    {%- set marker = adapter.get_relation(database=target.lakehouse, schema='verification', identifier='run_results') -%}
    {%- if marker is not none -%}
      {%- set rows = starter._run_result_rows(results, 'current_timestamp()') -%}
      {%- if rows | length > 0 -%}
        {%- do run_query('insert into verification.run_results values ' ~ (rows | join(', '))) -%}
      {%- endif -%}
      {%- set engine = run_query('select cast(version() as string), cast(current_timezone() as string)') -%}
      {%- do run_query(
          'insert into verification.invocations values ('
          ~ starter.string_literal(invocation_id)
          ~ ', ' ~ starter.string_literal(dbt_version)
          ~ ', ' ~ starter.string_literal(target.type)
          ~ ', ' ~ starter.string_literal(flags.WHICH)
          ~ ', ' ~ ('true' if flags.FULL_REFRESH else 'false')
          ~ ', ' ~ starter.string_literal(engine.rows[0][0])
          ~ ', ' ~ starter.string_literal(engine.rows[0][1])
          ~ ', current_timestamp())'
      ) -%}
      {%- do adapter.commit() -%}
    {%- endif -%}
  {%- endif -%}
{% endmacro %}
