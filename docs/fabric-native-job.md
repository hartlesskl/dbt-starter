# Pointing Fabric's native dbt job at this repository

Fabric Lakehouse is a second target beside DuckDB. This page says how to run the repository in
Fabric's native dbt job, what the repository already provides for it, and what stays unverified
until Phase 5. DuckDB stays the default: `make demo` is unchanged. Fabric is compile-checked only
until the Phase 5 verification record exists, and this page is folded into the Phase 5 setup guide.

## What the native job runs

The job's runtime (V1.0) ships dbt Core 1.11, dbt-fabricspark 1.12.2 for a Fabric Lakehouse and
Python 3.12. The lock follows it. [ADR 0006](decisions/0006-fabric-lakehouse-retarget.md) holds the
pairing and the steps to take when the job upgrades, so this page does not repeat the versions.

## What the repository already provides

- `dbt_project.yml` sits in the repository root, which is where the job requires it. A project in
  a subdirectory is not supported, and none is tracked. The one other `dbt_project.yml`, in
  `packages/starter`, belongs to the local package and is not a project the job runs.
- The repository holds far fewer than the 5,000 files the job can import, and a GitHub connection
  avoids that limit anyway.
- `env_var()` appears only in `profiles.yml`, never in a file dbt parses, so the job cannot meet a
  variable without a default there.

`tests/conventions/test_native_job_layout.py` holds all of this on every gate run.

## Pointing the job at GitHub

Connect the repository with a classic personal access token. Give it the narrowest scope that can
read the repository. A project connected this way cannot be edited in the Fabric UI, so changes go
through the repository.

## The job's profile

The job's profile is set in the UI under "dbt configurations": the adapter (Fabric Lakehouse), the
connection and the schema, `dbo`. The committed `profiles.yml` serves the self-hosted mode,
`make build-fabric`, and is assumed to be unused by the job. Phase 5 check
`native-job-profile-schema` settles how the job's profile maps the schema on a schema-enabled
Lakehouse.

## The Lakehouse

Use one schema-enabled Lakehouse with the schemas `landing`, `bronze`, `silver` and `gold`.
`FABRIC_SCHEMA`, which defaults to `dbo`, is the adapter's default schema and must differ from the
Lakehouse name. The Phase 5 setup command creates the Lakehouse with `enableSchemas: true`,
idempotently, and never deletes anything. Phase 5 check `schema-layout` proves that dbt writes and
reads across the four schemas.

## Session time zone

A timestamp without a zone is parsed in the Spark session's zone, so the template assumes UTC
sessions. The self-hosted profile requests UTC. The job's own setting is Phase 5 check
`session-time-zone`.

## Packages

Whether `dbt deps` runs in the job is undocumented. Packages are not vendored: `dbt_packages/` is
ignored and no copy is tracked. The first Phase 5 live check, `native-job-deps`, runs the job and
records whether it installs `packages/starter` and `dbt_utils`. ADR 0006 holds the vendoring recipe
for the case where it does not.

## Status

Fabric is compile-checked only. Nothing on this page has run against a live workspace. Use
placeholders such as `<workspace-id>` and `<lakehouse-id>` in any note you write about your own
setup, and never commit a real id, tenant or workspace name.
