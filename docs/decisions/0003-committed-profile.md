# 0003. Commit `profiles.yml` with a DuckDB default and an `env_var`-only Fabric target

- **Date:** 2026-11-21
- **Status:** accepted
- **Reversibility:** costly. The README, CI and the Phase 5 Fabric guide reference the committed file, so switching to a gitignored example file means a coordinated edit to those docs and a new setup step for every user. There is no data migration.

## Context

The demo must run with no setup (DEMO-05): a user clones the template, runs one command, and
dbt finds a profile. dbt looks for `profiles.yml` in the current directory only when told to
(`--profiles-dir .`), and the commands in this repo are run from the repo root for that reason
(RESEARCH Finding 6). A DuckDB profile needs no secret, so there is nothing to hide in it.

The Fabric target is different. It needs a workspace and a warehouse, and in a real deployment
it may carry a tenant, a client id and a client secret. The file is public and is copied by
everyone who clicks "Use this template", so none of those values can ever be written in it.

## Decision

Commit `profiles.yml` at the repo root with:

- `target: duckdb` as the default, with a `path` that reads `DBT_DUCKDB_PATH` and falls back to
  `data/dbt_starter.duckdb`. The override exists so tests and the conversion fixture can build
  into a temporary database without touching the demo database.
- a `fabric` output whose every field except the non-secret driver settings (`type`, `driver`,
  `threads`, `retries` and the two timeouts) is one `env_var()` call. Fields that identify a tenant or carry a secret (`workspace_name`, `workspace_id`,
  `database`, `host`, `server`, `tenant_id`, `client_id`, `client_secret`, `user`, `password`,
  `access_token`) have no default, so an unset variable fails loudly instead of silently
  falling back to a value somebody wrote in the file.

## Departure from the stack research

The stack research in `.claude/CLAUDE.md` suggested gitignoring `profiles.yml` and shipping
`profiles.yml.example`, the usual pattern for a public dbt repo. That is rejected here because
it adds a copy-and-edit step in front of the first command, which is the step the template most
needs to remove. The phase context listed both options under Claude's discretion, so this is a
choice between two allowed designs, not a break from a locked decision.

## Safeguards

- `tests/conventions/test_profile.py` fails if any Fabric field is a literal instead of an
  `env_var()` call, if a secret-bearing field has a default, or if the file holds a GUID or a
  Fabric host name. It also runs `dbt parse` on DuckDB with every `FABRIC_*` variable removed,
  so the default path cannot come to depend on Fabric settings.
- `.gitignore` keeps `.env`, `.user.yml` and the local databases out of git, and
  `tests/conventions/test_repo_hygiene.py` asserts those lines.
- gitleaks and pre-commit arrive in Phase 2 (CI-06) as a second, independent guard.

## Revisit when

A secret-bearing field must ever be read from the file itself, for example a connection string
that cannot come from the environment. At that point move to a gitignored profile with a
committed example, and update the README, CI and the Fabric guide together.
