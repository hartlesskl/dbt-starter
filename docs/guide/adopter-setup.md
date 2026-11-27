# Adopter setup checklist

"Use this template" copies the files and nothing else. The settings that make the repository safe and useful, such as the required check, the Pages source and the opt-in variables, live in GitHub rather than in the files, so a new copy starts without them. This checklist lists each one, in the order that works. Replace `<owner>/<repo>` with your own repository in every command.

## First steps

- [ ] Create your repository with **Use this template**, then clone it.
- [ ] Run `uv sync --locked`, then `uv run demo`, to see the conventions working before you change anything.
- [ ] Run `uv run remove-example` to see what would go, then `uv run remove-example --apply`, and review the staged result with `git diff --cached`. It deletes the skeleton workflow along with the example, so a copy that has applied it runs the `gate` and `pages` workflows on its next push and no longer runs `skeleton`. On the walkthrough copy the `gate` passed on the removal commit.
- [ ] Commit and push the result. The [swap-out guide](swap-out.md) then adds your own source, and the [rename checklist](rename-checklist.md) renames the project.

## Required check

- [ ] Let the `gate` workflow run once. Creating a copy from the template starts it by itself, on the copy's first commit, within seconds of the creation, and it takes about twelve minutes. GitHub offers a check name for selection in the web form only after the check has run within the last seven days.
- [ ] Add a branch ruleset on the default branch that requires the status check `gate`. Settings, Rules, Rulesets, New branch ruleset, then Require status checks to pass, then add `gate`.
- [ ] Read it back with `gh api repos/<owner>/<repo>/rulesets`. The API accepts the ruleset from the command line too: `gh api -X POST repos/<owner>/<repo>/rulesets --input ruleset.json`, where the file holds one `required_status_checks` rule with the context `gate`.
- [ ] Decide who may bypass it. A ruleset that lists the repository admin role as a bypass actor lets the owner push straight to the default branch; GitHub prints "Bypassed rule violations" and the push goes through. Leave the bypass list empty if the owner should open a pull request like everyone else.

## Pages

- [ ] Declare `DOCS_ATTRIBUTION` in every source module first, as a pair of the source name and an https link. The placeholder source declares none, and the site step refuses a source that declares none, so that private data is never published by accident.
- [ ] In Settings, Pages, set the source to **GitHub Actions**, or run `gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow`.
- [ ] Add the repository variable `ENABLE_PAGES` with the value `true`: `gh variable set ENABLE_PAGES -R <owner>/<repo> --body true`.
- [ ] Push to `main` or run the `pages` workflow by hand: `gh workflow run pages.yml -R <owner>/<repo> --ref main`. It deploys only from `main`. On the walkthrough copy the build and deploy jobs both succeeded, the deploy created the `github-pages` environment, and the site root answered with HTTP 200. The README section "Publish the docs on GitHub Pages" has the detail.

## Opt-in workflows

Each workflow below stays skipped, and green, until its variable is `true`. Set one only when you want that workflow.

- [ ] `ENABLE_NIGHTLY` runs the demo every night and then the checks.
- [ ] `ENABLE_CLEANROOM` proves the demo from a fresh checkout on Linux, macOS and Windows each week.
- [ ] `ENABLE_FABRIC` builds the synthetic fixture on Fabric by hand. Read the next section before you set it.
- [ ] Set a variable with `gh variable set <NAME> -R <owner>/<repo> --body true`. Schedules run only on the default branch, and GitHub pauses the schedule of a public repository after 60 days without activity.

## Secrets and Fabric CI

Only the Fabric workflow needs secrets; the gate, nightly, clean-room and Pages workflows use none.

- [ ] Follow the CI section of [docs/fabric-setup.md](../fabric-setup.md#ci-opt-in) to create the app registration, the federated credential and the workspace role.
- [ ] Set four repository secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `FABRIC_WORKSPACE_ID` and `FABRIC_CI_LAKEHOUSE_ID`. Pipe each value on stdin, as `printf '%s' "<value>" | gh secret set AZURE_CLIENT_ID -R <owner>/<repo>`, so it never shows in a terminal or a history.
- [ ] Set the variable `FABRIC_CI_LAKEHOUSE` to the CI Lakehouse name, then `ENABLE_FABRIC` last.
- [ ] Never commit an id, a tenant or a token to the repository. It is public once you publish it.

## Actions permissions

- [ ] Leave the default workflow token read-only. The gate, nightly, clean-room and skeleton workflows ask only for `contents: read`, the Pages deploy job adds `pages: write` and `id-token: write` for itself, and the Fabric job adds `id-token: write` for its sign-in.
- [ ] Read the setting back with `gh api repos/<owner>/<repo>/actions/permissions/workflow`.

## Description and topics

- [ ] Set your own description and topics. A copy does not carry the template's, and the template's description is not yours: `gh repo edit <owner>/<repo> --description "<your description>" --add-topic dbt`.

## What a template copy inherits

Observed on 2026-11-27, on a public copy made from the template with **Use this template** on a personal account, then read back with the commands in the last column. A public copy on a personal plan stands in for yours; private copies and organization defaults were not walked, so run each read-back command on your own copy and trust the result over the table.

| Setting | Observed in a copy | Read-back command |
|---------|--------------------|-------------------|
| Files | Copied, 196 tracked files in one commit named "Initial commit", with none of the template's history | `git ls-files` after you clone |
| Rulesets | None; add the ruleset requiring `gate` | `gh api repos/<owner>/<repo>/rulesets` |
| Branch protection | None; the default branch answered 404 | `gh api repos/<owner>/<repo>/branches/main/protection` |
| Actions variables | None, so every `ENABLE_*` workflow stays skipped | `gh api repos/<owner>/<repo>/actions/variables` |
| Actions secrets | None | `gh api repos/<owner>/<repo>/actions/secrets` |
| Environments | None; the first Pages deploy created `github-pages` | `gh api repos/<owner>/<repo>/environments` |
| Pages source | Unset; the read-back answered 404 until the source was set | `gh api repos/<owner>/<repo>/pages` |
| Actions permissions | Actions enabled, all actions allowed, and the default workflow token read-only with pull request approval off | `gh api repos/<owner>/<repo>/actions/permissions` |
| Default branch | `main`, the template's name | `gh api repos/<owner>/<repo> --jq .default_branch` |
| Topics | Empty, and so is the description; the template's description and its six topics were not copied | `gh api repos/<owner>/<repo> --jq '{description,topics}'` |
| Template flag | Off; the MIT license file was copied | `gh api repos/<owner>/<repo> --jq .is_template` |
| Workflows on the copy's first commit | Present. The creation itself started `gate` and `skeleton` as push runs on the first commit, and the `pages` run on it was skipped because `ENABLE_PAGES` was unset. `nightly`, `cleanroom` and `fabric` did not run | `gh run list -R <owner>/<repo>` |
