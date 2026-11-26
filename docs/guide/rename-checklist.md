# Rename checklist

After you remove the example, a copy of this template holds the files below, and each one is either kept as it is, kept and edited, or replaced with your own. A test reads these two lists on every gate run and checks that every file of a copy sits under exactly one item, so a list cannot drift from the files.

An item that ends in `/` is a folder. When two items match a file, the longer one wins, so a folder item can be Keep while a folder inside it is Replace.

## Rename

Apply the rename by hand, in one checkout and on a branch. A half-done rename shows in `git status`, and `dbt parse` fails while the project's `profile` and the key in `profiles.yml` disagree, so the gate stops it.

1. In `dbt_project.yml`, change `name` from `dbt_starter` to your project name and `profile` to your profile name. Change the two keys that repeat the project name, one under `models:` and one under `seeds:`.
2. In `profiles.yml`, change the top key `dbt_starter` to the same profile name; it must equal `profile` exactly. The `profile` line in `.sqlfluff` changes with it.
3. In `pyproject.toml`, change `name` under `[project]` from `dbt-starter` to your Python project name, then run `uv lock` so the lock records the new name.
4. Change the titles: the heading of `README.md`, the heading of `models/overview.md`, and the page title and heading in `src/starter_cli/site.py`, which `tests/loader/test_site.py` also asserts.
5. Find every other place the project is named with `git grep -n dbt_starter`. It lists the unique ids of dbt nodes in the tests (`model.dbt_starter.`), the default DuckDB file name and the docs. Then run `git grep -n dbt-starter` for the hyphenated name, which also appears in the loader's user agent. The DuckDB file name is only a file name: change it everywhere it appears or keep it everywhere.
6. Keep the name of the `starter_cli` package unless you have a reason. Renaming it means editing `[project.scripts]` and `packages = ["src/starter_cli"]` in `pyproject.toml`, renaming the folder, and following `git grep -n starter_cli` through the imports.
7. Run `make gate`. It rebuilds the project from a fixture and runs every convention test, so a name you missed fails there.

## Keep

These files are the framework and the tooling. Keep them, with the edits noted.

- `.github/workflows/`: the gate, the nightly run, the Pages site and the Fabric workflows. Keep them; they read your sources, not the example's.
- `.gitignore`: keep.
- `.gitleaksignore`: keep, and add only what your own secret scan needs.
- `.pre-commit-config.yaml`: keep.
- `.python-version`: keep.
- `.sqlfluff`: keep. Its `profile` line changes with a rename.
- `.sqlfluffignore`: keep.
- `LICENSE`: keep, with your own copyright line if you prefer.
- `Makefile`: keep.
- `data/`: keep; the loader writes the raw files and the DuckDB file here, and git ignores them.
- `dbt_project.yml`: keep; rename the project and its profile.
- `docs/decisions/`: keep as history. The decision records explain why the conventions are what they are.
- `docs/fabric-native-job.md`: keep; it describes how the template runs in Fabric's dbt job.
- `docs/fabric-setup.md`: keep; it describes the Lakehouse target.
- `docs/guide/`: keep; these guides explain the conventions you adopted.
- `macros/config/`: keep; the schema and alias macros set the layer schemas.
- `macros/dq/dq_entities.sql`: keep and edit the entity list so it names your own entities.
- `models/gold/`: keep the date dimension and its description. Your own gold models go in a folder beside it.
- `models/silver/`: keep the data-quality models. Your own silver models go in a folder beside them.
- `package-lock.yml`: keep.
- `packages.yml`: keep.
- `packages/starter/`: keep; this is the local dbt package of shims, rules and test macros that every model uses.
- `profiles.yml`: keep; rename its top key to match the dbt profile.
- `pyproject.toml`: keep; rename the project, and edit the scripts only if you rename the `starter_cli` package.
- `seeds/`: keep the digits seed, and put your own seeds in a folder beside it.
- `selectors.yml`: keep.
- `src/starter_cli/`: keep the loader, the demo, the gate and the Fabric tooling. The Fabric record tooling reads your sources: a source declares `FIXTURE_UNDECLARED_COLUMNS` and, if it lands by month, `FABRIC_DEFAULT_MONTHS`.
- `tests/conftest.py`: keep.
- `tests/conventions/`: keep; these tests hold the conventions on your models and files.
- `tests/gold/`: keep; it tests the date dimension.
- `tests/guide/`: keep; it builds the swap-out guide's files and checks that they pass the gate.
- `tests/loader/`: keep; it tests the loader, the site and the commands.
- `tests/support/`: keep; the shared test helpers.
- `uv.lock`: keep, and regenerate it with `uv lock` only when you change a dependency.

## Replace

These files describe or hold the placeholder source, or the template's own text. Replace them with yours.

- `README.md`: rewrite the sections that describe the placeholder source, its data and its verification.
- `docs/data-terms.md`: replace it with the terms and credit of your own sources.
- `macros/rules/example_source/`: your sources' quarantine rules, one file per entity. Delete the placeholder's.
- `models/bronze/example_source/`: your bronze models, their description and their unit tests.
- `models/gold/example_source/`: your gold models and their contracts.
- `models/landing/example_source/`: your source declaration for the landing tables.
- `models/overview.md`: the docs overview page; describe your own project.
- `models/silver/example_source/`: your silver models and their quarantine models.
- `src/starter_cli/example_source/`: your source module, with its entities, loader and fixture.
- `tests/example_source/`: the exact-count test of your source's fixture.

<!-- example:begin -->
In the template itself, `skeleton/` holds the placeholder source that replaces the example, the example's own folders are every folder named for it, and `docs/verification/` holds the dated records of its live runs. `uv run remove-example --apply` deletes or replaces them, so a copy lists none of them.
<!-- example:end -->

## What depends on the placeholder source

Add your own source first, with the [swap-out guide](swap-out.md), and delete the placeholder last. Four things name it or need something it declares, and each needs a decision from you:

- `tests/conventions/test_selectors.py` tags one bronze model as a known limit. Its `TAGGED_MODEL` and `ENTITY_FOLDER` constants name the placeholder's bronze model and folder, so point them at one of your bronze models.
- `tests/conventions/test_verify_fabric.py` takes its bronze model from `bronze_trips_model()`, which reads `ADDED_COLUMN_ENTITY` from a source module. Keep exactly one source that declares it, with the name of one of that source's entities, and that also offers `write_added_column_variant(dest)`, which writes one file of that entity with one undeclared column added.
- `tests/conventions/test_contracts.py` expects at least `MIN_GOLD_MODELS` gold models, which is two: the date dimension and one of yours. Give your entity a gold model before you delete the placeholder's.
- `uv run site` refuses a source that declares no `DOCS_ATTRIBUTION = (name, https link)`. The placeholder declares none by design, so the site is refused until the placeholder is gone and every source of yours declares its credit.

A source module may also set two optional attributes. `FABRIC_DEFAULT_MONTHS` is a non-empty tuple of `YYYY-MM` months that a Fabric run lands by default, for a source that lands by month. `FIXTURE_UNDECLARED_COLUMNS` is the number of undeclared columns that your fixture plants, and it defaults to 0. Leave both out and the tooling falls back to the entity's own defaults and to zero.
