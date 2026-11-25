# Setting up Microsoft Fabric

This guide takes you from nothing to this project building on a Fabric Lakehouse, three ways: from
your machine (self-hosted), by Fabric's native dbt job, and from an opt-in GitHub workflow. DuckDB
stays the default, and `make demo` needs none of this. The README's Status line says which versions
were verified on Fabric and when; this page does not repeat it. The reasons behind the design are in
[ADR 0006](decisions/0006-fabric-lakehouse-retarget.md) (the Lakehouse, the adapter and the
versions) and [ADR 0007](decisions/0007-lakehouse-landing-sink.md) (how files reach the Lakehouse).

Every command is POSIX shell (macOS and Linux). Windows PowerShell equivalents are not covered.
Every value that belongs to your tenant is written as a placeholder: `<workspace-id>`,
`<lakehouse-id>`, `<tenant>`, `<owner>/<repo>`. Keep your real values in gitignored files such as
`.env.fabric` and never commit one.

## Before you start

- **An own-tenant trial.** Start a Microsoft Fabric trial in a tenant you control, with a
  work or school account. The trial lasts 60 days, and its items (Lakehouses, the dbt job) are
  deleted when it expires, so export anything you want to keep before then.
- **The fallback.** If the trial lapses or becomes unusable before you have what you need, a paid
  F2 capacity assigned to the workspace works the same way. Pause it when it is idle, because it
  bills while it runs.
- **The runtime.** Use Fabric Runtime 2.0 (Spark 4.1). It is the default for new workspaces.
- **The trial capacity runs one Spark (Livy) session at a time.** Read "Check the capacity" under
  "Self-hosted builds" before your first build.
- **Tools.** [uv](https://docs.astral.sh/uv/), the [Azure CLI](https://learn.microsoft.com/cli/azure/)
  (`az`), and for the CI section the [GitHub CLI](https://cli.github.com/) (`gh`).

Install the Fabric extra once. It brings dbt-fabricspark, the Azure sign-in libraries and the Delta
writer that landing uses:

```bash
uv sync --locked --extra fabric
```

## Sign in

```bash
az login --allow-no-subscriptions --tenant <tenant>
az account show
```

`--allow-no-subscriptions` is needed when the tenant has no Azure subscription, which is the case
for a Microsoft 365 developer tenant. The signed-in identity needs the Contributor, Member or Admin
role on the workspace. A `404 EntityNotFound` from the next step means the identity you signed in
with cannot see the workspace (it sees only its personal one); sign in again with the right
account.

## Create the Lakehouse

Find the workspace id in the portal: open the workspace and read the address, which looks like
`https://app.fabric.microsoft.com/groups/<workspace-id>/...`. Then create a gitignored
`.env.fabric` with the workspace id and a name for the Lakehouse:

```bash
cat > .env.fabric <<'EOF'
FABRIC_WORKSPACE_ID=<workspace-id>
FABRIC_LAKEHOUSE=<lakehouse-name>
EOF
```

Load the file and run the setup command. It prints one line, `FABRIC_LAKEHOUSE_ID=<lakehouse-id>`,
and the block appends that line to the file:

```bash
set -a; . ./.env.fabric; set +a
make -s setup-fabric >> .env.fabric
```

Without make: `uv run --locked --extra fabric setup-fabric >> .env.fabric`.

`setup-fabric` looks the Lakehouse up by name in the workspace and creates it with schemas enabled
(`enableSchemas: true`) only when there is none. It is idempotent: a second run finds the Lakehouse
and prints the same id, so replace the `FABRIC_LAKEHOUSE_ID` line instead of appending a second one.
It only lists, reads and creates. It never deletes or renames anything, and it refuses (exit 2) a
Lakehouse that exists without schemas, a name that differs from an existing one only in case, and a
`FABRIC_SCHEMA` equal to the Lakehouse name.

The Lakehouse holds the schemas `landing`, `bronze`, `silver` and `gold`. Nobody creates `landing`:
it appears when the first table is written ([ADR 0007](decisions/0007-lakehouse-landing-sink.md)).

## Environment variables

Every run mode reads its connection from these variables, through `profiles.yml`. Load the file
before each command (every new shell needs it):

```bash
set -a; . ./.env.fabric; set +a
```

| Variable | Needed | Meaning |
| --- | --- | --- |
| `FABRIC_WORKSPACE_ID` | always | The workspace id, a UUID. |
| `FABRIC_LAKEHOUSE_ID` | always | The Lakehouse id, a UUID. `setup-fabric` prints it. |
| `FABRIC_LAKEHOUSE` | always | The Lakehouse name. |
| `FABRIC_SCHEMA` | optional, default `dbo` | The adapter's default schema. It must differ from the Lakehouse name. The project's own relations go to `bronze`, `silver` and `gold`, so `dbo` stays empty. |
| `FABRIC_AUTHENTICATION` | optional, default `CLI` | `CLI` is the only value the committed profile supports: sign in with `az login`, or with `azure/login` in CI. |
| `FABRIC_HIGH_CONCURRENCY` | optional, default `true` | The adapter's session mode. `true` uses high-concurrency Livy sessions, `false` uses standard sessions. High-concurrency sessions refuse a service principal's token ("The user access token was not found"), so the CI workflow sets `false`. Local runs and the native job keep `true`. |
| `FABRIC_DBT_JOB_ID` | only for `verify-fabric start`, `wait` and `sequence` | The id of the native dbt job. |

`build-fabric`, `demo --target fabric` and `load --target fabric` name any missing or malformed
variable (the name, never the value) and run nothing. The session time zone is not a variable: the
profile asks for UTC (`spark.sql.session.timeZone`), and the project sets and checks it at the
start of every run, in every run mode (`ensure_utc_session`), because a timestamp without a zone is
parsed in the session zone. A session that is not UTC stops the run with a message.

## Land data

The command is `uv run --locked --extra fabric load <source> <entity> --target fabric`. For the
example data:

<!-- example:begin -->
```bash
uv run --locked --extra fabric load nyc_taxi taxi_zones --target fabric
uv run --locked --extra fabric load nyc_taxi yellow_trips --target fabric
```
<!-- example:end -->

`load --target fabric` lands the same text rows as the DuckDB loader, through the Lakehouse sink: it
writes Delta tables to `landing` straight to OneLake with delta-rs, using your `az` sign-in, so it
needs write access to the Lakehouse (the Contributor role) and opens no Spark session. Each source
file is one replace, so loading a file again replaces its own rows and nothing else, and a file with
a new column adds the column. Both choices are in
[ADR 0007](decisions/0007-lakehouse-landing-sink.md).

- **The default month is `2024-01`**, the one real month verified on Fabric, for both
  `load --target fabric` and the Fabric demo. Pass `--months` to choose others, for example
  `--months 2024-01..2024-03`; the same size rules as on DuckDB apply (`--allow-large`).
- **One load at a time per Lakehouse.** Two writers race on one table's transaction log.
- **Keep the synthetic fixture and real months in different Lakehouses.** The fixture's file names
  can collide with real months. Verification (`verify-fabric`) and the CI workflow land the fixture;
  use a Lakehouse of your own for real data.

## Self-hosted builds

Self-hosted means dbt runs on your machine and Fabric runs the SQL. `build-fabric` checks the
variables, runs `dbt deps`, then `dbt build --target fabric`, and stops at the first failure:

```bash
make build-fabric
```

Without make: `uv run --locked --extra fabric build-fabric`. For the synthetic fixture (which
breaches the real quarantine levels by design, so its levels tests are left out), build with the
`fabric_fixture` selector: `uv run --locked --extra fabric build-fabric --selector fabric_fixture`.
Add `--summary-file FILE` to append a Markdown summary of the counts and any failing node.

`make demo-fabric` does the whole example in one command: it lands `2024-01` through the sink, runs
`dbt deps`, builds, and prints the same closing summary as `make demo`, read back from the Lakehouse.
It reads the summary in one Spark session, after one wait for the capacity. `MONTHS` and
`ALLOW_LARGE=1` work as they do for `make demo`. `make demo-fabric CLEAN=1` first drops `bronze`,
`silver` and `gold` (after `dbt deps` has succeeded) and rebuilds them from scratch; it never drops
`landing`.

### Check the capacity

The trial capacity fits one Livy session at a time, and a session stays active for a few minutes
after the command that opened it has returned. A second session started meanwhile is refused with
HTTP 430 `TooManyRequestsForCapacity`. So before a self-hosted build, the demo or a CI dispatch,
and never while a native job run is in progress, count the workspace's sessions that have not
ended. The count must be 0:

```bash
TOKEN=$(az account get-access-token --scope https://api.fabric.microsoft.com/.default --query accessToken -o tsv)
curl -s -H "Authorization: Bearer $TOKEN" "https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/spark/livySessions" | python3 -c 'import json, sys; ended = ("Succeeded", "Failed", "Cancelled", "Dead", "Killed"); print(sum(1 for s in json.load(sys.stdin).get("value", []) if s.get("state") not in ended))'
```

This is the Fabric REST call `GET /v1/workspaces/<workspace-id>/spark/livySessions`. Once it prints
0, wait another minute or so before you start. `make demo-fabric` and `verify-fabric` wait for the
capacity themselves; `make build-fabric` does not. Loading needs no wait, because it opens no
session. A larger capacity does not have this limit.

## Known limits

A node that cannot run on Fabric because of an adapter, runtime or upstream bug with no workaround
is declared a known limit instead of being hidden. The node carries the `fabric_known_limit` tag
and a `meta.fabric_known_limit` entry with the upstream issue link and a one-line reason, set by
its path in `dbt_project.yml`:

```yaml
models:
  dbt_starter:
    <layer>:
      <folder>:
        <model-name>:
          +tags: [fabric_known_limit]
          +meta:
            fabric_known_limit:
              issue: https://github.com/<owner>/<repo>/issues/<number>
              reason: <one line>
```

The `fabric_fixture` selector, `build-fabric` and the Fabric demo leave the node out of every
Fabric run together with every node downstream of it (`tag:fabric_known_limit+`), because a
descendant cannot build without it. The verification record lists the node and its descendants with
the issue link and the reason. A tagged node whose meta lacks an https GitHub issue link or a
reason is an error. The limits a project declares, if any, are listed in its verification record.

## The native dbt job

Fabric's native dbt job pulls this repository from GitHub and runs dbt inside the workspace. Its
runtime pins dbt Core and dbt-fabricspark; the lock follows it ([ADR 0006](decisions/0006-fabric-lakehouse-retarget.md)
has the pairing and the steps to take when it upgrades). The repository is laid out for it, and
`tests/conventions/test_native_job_layout.py` holds the layout on every gate run: `dbt_project.yml`
sits at the repository root (a project in a subdirectory is not supported), the repository holds
far fewer than the job's 5,000-file import limit, and `env_var()` appears only in `profiles.yml`.

1. **Create the job.** Create a dbt job item in the workspace.
2. **Connect GitHub.** Use a classic personal access token (fine-grained tokens are not accepted).
   For a public repository give it the `public_repo` scope: a token with no scopes failed with
   "Failed to download dbt project from GitHub repository ... NotFound" even though the repository
   is public. `public_repo` can write to every public repository the account owns, so set the
   shortest expiry that covers your work and revoke the token at teardown. Choose the repository
   and the branch; Fabric pulls from that branch on every run, and a connected project cannot be
   edited in the Fabric UI, so changes go through the repository. A private repository was not
   tried.
3. **Set the profile in the UI.** The job ignores `profiles.yml`. Choose the adapter (Fabric
   Lakehouse), the connection (your Lakehouse) and the schema `dbo`, and tick **Seed data**,
   because gold and bronze read the six seeds. Every relation still lands in `bronze`, `silver` or
   `gold` and `dbo` stays empty, because the project names its own schemas
   (Phase 5 check `native-job-profile-schema`; Phase 5 check `schema-layout` for the one
   schema-enabled Lakehouse).
4. **Set the command.** Operation `build`. Set the selector to `fabric_fixture` only when the
   Lakehouse holds the synthetic fixture; with the field blank the job builds everything and the
   two quarantine-level tests fail by design on fixture data. For real data leave the selector
   blank and, if the project declares a known limit, enter `tag:fabric_known_limit+` in the
   Exclude field (this repository declares none, so the field was not exercised).
5. **Set the threads.** Use `threads` 1 on the trial capacity. With the default, the job opened a
   second Livy session and a seed failed with HTTP 430. Also leave no other session running while
   a native run is in progress (see "Check the capacity").
6. **Full refresh.** Leave it off, except for a run that is meant to be a full refresh. Switch it on
   before that run and keep it on until the run has finished, then switch it off. (Tables are
   rebuilt in place either way; the flag changes only how seeds reload.)
7. **Start the run from the portal.** The Fabric REST API refuses to start this job type (the Run
   On Demand Item Job call answers 400 `InvalidJobType` for the type the job reports), so every
   run starts in the portal or from a schedule. `verify-fabric start` prints the settings to use
   and `verify-fabric wait` reads the run back from the job's instance list.

What the job does on its own, as observed: it runs `dbt deps` itself, installing the local
`packages/starter` and the hub package, so nothing is vendored and `dbt_packages/` stays ignored
(Phase 5 check `native-job-deps`). Its Spark session reports the time zone UTC, and the project
asserts it anyway (Phase 5 check `session-time-zone`).

**Recording the results.** The job's results are read from two tables in a `verification` schema,
`verification.invocations` and `verification.run_results`, which the project's `on-run-end` hook
fills. The hook writes nothing unless the tables exist, so create them once on the Lakehouse the
job builds into:

```bash
uv run --locked --extra fabric verify-fabric init
```

Without this step the hook does nothing and the job still runs. `verify-fabric` needs
`FABRIC_DBT_JOB_ID` for the steps that talk to the job. List the workspace's dbt jobs with the
REST call `GET /v1/workspaces/<workspace-id>/items?type=DataBuildToolJob`; the job's `id` in the
reply is the value.

## CI (opt-in)

`.github/workflows/fabric.yml` builds the synthetic fixture on Fabric with your own credentials,
by hand (`workflow_dispatch`), never on pull requests, pushes or a schedule, and it is never a
required check. In a copy of the template it does nothing until you opt in: the job is skipped
until the repository variable `ENABLE_FABRIC` is `true`, and it only writes a one-line note if a
setting is missing. It never opens issues.

**A separate CI Lakehouse.** CI builds into its own Lakehouse in the same workspace, so a dispatch
can never rewrite the Lakehouse your verification record is built from. Create it with its own env
file, choosing a name that identifies nothing, because it appears in run logs:

```bash
cat > .env.fabric-ci <<'EOF'
FABRIC_WORKSPACE_ID=<workspace-id>
FABRIC_LAKEHOUSE=<ci-lakehouse-name>
EOF
set -a; . ./.env.fabric-ci; set +a
make -s setup-fabric >> .env.fabric-ci
```

**Sign-in with OIDC (the setup that was run live).** The workflow signs in with GitHub OIDC through
`azure/login`, so no client secret is stored. You need an Entra app registration with one
federated credential, a service principal and a workspace role:

1. Create the app registration and its service principal (keep the client id it prints):

   ```bash
   az ad app create --display-name <app-name> --sign-in-audience AzureADMyOrg
   az ad sp create --id <client-id>
   ```

2. Find your repository's OIDC subject prefix. Repositories created or renamed after July 15, 2026
   use GitHub's immutable subject form, which carries ids, so read the prefix instead of writing
   it by hand:

   ```bash
   gh api repos/<owner>/<repo>/actions/oidc/customization/sub
   ```

   The reply's `sub_claim_prefix` is the start of the subject. Build the subject as that prefix
   plus `:ref:refs/heads/main`, so only runs on `main` can sign in, and never use a wildcard.
   A mismatch fails with `AADSTS700213` or `AADSTS7002138`.

3. Add the federated credential from a JSON file that holds the name, the issuer
   `https://token.actions.githubusercontent.com`, that subject, and the audience
   `api://AzureADTokenExchange`:

   ```bash
   az ad app federated-credential create --id <client-id> --parameters federated-credential.json
   ```

   `az ad app credential list --id <client-id>` should print an empty list: no client secret.

4. Give the service principal the **Contributor** role on the one workspace: in the portal, open
   the workspace, Manage access, add the app by name. The same call over REST is
   `POST /v1/workspaces/<workspace-id>/roleAssignments` with the principal's object id (from
   `az ad sp show --id <client-id> --query id -o tsv`), type `ServicePrincipal`, and role
   `Contributor`.

5. Turn on the tenant setting. In the Fabric Admin portal, under Tenant settings, Developer
   settings, enable **Service principals can use Fabric APIs** (the same setting appears as
   "Service principals can call Fabric public APIs"). Only a Fabric administrator sees Developer
   settings: someone who administers only the trial capacity sees just Capacity settings, Refresh
   summary and Help + support. Microsoft's documentation says the setting is on by default for new
   customers, so check it, and ask a tenant admin if you cannot see it. A 401 or 403 in the "Land
   the fixture" step points at this setting, or at a missing workspace role.

6. Set the repository settings. Secrets (pipe each value on stdin so it is never echoed):
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `FABRIC_WORKSPACE_ID` and `FABRIC_CI_LAKEHOUSE_ID`; then the
   variable `FABRIC_CI_LAKEHOUSE` (the CI Lakehouse name), and last the variable `ENABLE_FABRIC`:

   ```bash
   printf '%s' "<value>" | gh secret set AZURE_CLIENT_ID -R <owner>/<repo>
   gh variable set FABRIC_CI_LAKEHOUSE -R <owner>/<repo> --body "<ci-lakehouse-name>"
   gh variable set ENABLE_FABRIC -R <owner>/<repo> --body true
   ```

7. Check the capacity (no session active, and no native run in progress), then dispatch:

   ```bash
   gh workflow run fabric.yml -R <owner>/<repo> --ref main
   ```

The workflow signs in, lands the fixture with `verify-fabric land-fixture --label ci`, and runs
`build-fabric --selector fabric_fixture`, writing the counts and any failing node to the job
summary. It sets `FABRIC_HIGH_CONCURRENCY=false` because high-concurrency sessions refuse a service
principal's token.

**The client-secret alternative (documented, not run live).** Instead of a federated credential you
can give the app a client secret, store it as a repository secret and pass it to `azure/login`
through its `creds` input. This repository's workflow does not support it, and its structure test
rejects a `creds` input, so using it means editing both. It was not run live. Prefer OIDC: a stored
secret is a credential that can leak and expire.

## Re-verify

To re-run the whole sequence on a Fabric Lakehouse that holds only the synthetic fixture (the
native job's builds, a self-hosted build and DuckDB parity), with the capacity free:

```bash
make verify-fabric
```

It needs `FABRIC_DBT_JOB_ID`. It runs `verify-fabric init`, lands the fixture, then asks you to
start four native builds in the portal (the third is a full refresh, with the variant file landed
before the fourth), waits for each, then runs the self-hosted build and the parity check, and stops
at the first failure. The evidence goes to `target/verify-fabric/<label>/`. When the sequence is
green, write the dated record:

```bash
uv run --locked --extra fabric verify-fabric record --date <YYYY-MM-DD>
```

It writes `docs/verification/fabric-<date>.md` and refuses a run with any failing node. A record is
created exclusively and never edited: a later verification adds a new dated file.

## Teardown

Everything this guide creates in your tenant, and how to remove it. Deleting the Lakehouses or the
trial itself removes the Lakehouse data; nothing here deletes your GitHub repository.

- **Lakehouses** (the build one and the CI one): delete each in the portal, or
  `az rest --method delete --resource https://api.fabric.microsoft.com --url https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/lakehouses/<lakehouse-id>`.
- **The dbt job:** delete it in the portal, or the same call on
  `.../v1/workspaces/<workspace-id>/items/<job-id>`.
- **The app registration and its federated credential:** `az ad app delete --id <client-id>`
  removes the registration, its credential and its service principal.
- **The workspace role assignment:** remove it under Manage access in the portal, or with
  `DELETE /v1/workspaces/<workspace-id>/roleAssignments/<principal-id>`.
- **The tenant setting:** switch **Service principals can use Fabric APIs** off again only if you
  were the one who turned it on and nothing else depends on it.
- **The personal access token:** revoke it in GitHub, under Settings, Developer settings,
  Personal access tokens (classic).
- **The repository secrets and variables:** `gh secret delete <name> -R <owner>/<repo>` for each of
  `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `FABRIC_WORKSPACE_ID` and `FABRIC_CI_LAKEHOUSE_ID`, and
  `gh variable delete <name> -R <owner>/<repo>` for `FABRIC_CI_LAKEHOUSE` and `ENABLE_FABRIC`.
- **The F2 capacity, if you used one:** pause or delete it in the Azure portal.
- **Local files:** delete `.env.fabric*` and `target/`.
