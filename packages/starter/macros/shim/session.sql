{#-
  The session-zone guard (WR-04). `fabricspark__parse_timestamp` reads zone-less text in the
  Spark session's time zone, and it matches DuckDB only when that zone is UTC (source files that
  hold local times without a zone are shifted by a non-UTC session when a time falls in a
  spring-forward gap, with no error). `profiles.yml` asks for UTC, but Fabric's native dbt job ignores
  profiles.yml, so a job run takes whatever zone the workspace sets.

  So the project asks again, in every run mode: `dbt_project.yml` calls `ensure_utc_session()`
  on run start and before each bronze model. On Spark it sets the zone, reads it back and fails
  with a message that names the zone when it is not UTC. On DuckDB it does nothing.

  Same three-part shape as the other shims: a public dispatcher, a raising `default__`, and an
  explicit `duckdb__` and `fabricspark__` body. The macro renders no SQL of its own, so dbt skips
  the empty hook statement. The live confirmation is the Phase 5 check `session-time-zone`.
-#}

{% macro ensure_utc_session() %}
  {{- return(adapter.dispatch('ensure_utc_session', 'starter')()) -}}
{% endmacro %}

{% macro default__ensure_utc_session() %}
  {{- exceptions.raise_compiler_error('starter.ensure_utc_session is not implemented for target type ' ~ target.type) -}}
{% endmacro %}

{#- DuckDB has no session zone to guard: its zone-less timestamps never shift. -#}
{% macro duckdb__ensure_utc_session() -%}
{%- endmacro %}

{#- `dbt compile` is skipped: it reads no data and parses no timestamp, and the offline compile of
    the gate (dummy values, no connection) would otherwise try to open a Livy session. -#}
{% macro fabricspark__ensure_utc_session() %}
  {%- if execute and flags.WHICH != 'compile' -%}
    {%- do run_query('set spark.sql.session.timeZone = UTC') -%}
    {%- set found = run_query('select cast(current_timezone() as string) as session_time_zone') -%}
    {%- set zone = found.rows[0][0] -%}
    {%- if zone != 'UTC' -%}
      {{- exceptions.raise_compiler_error('ensure_utc_session: the Spark session zone is ' ~ zone ~ ', not UTC; timestamps without a zone would parse differently from DuckDB') -}}
    {%- endif -%}
  {%- endif -%}
{% endmacro %}
