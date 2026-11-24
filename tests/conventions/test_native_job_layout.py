"""The repository stays runnable by Fabric's native dbt job (D-20, the offline half of PORT-07).

The native job pulls the repository from GitHub and runs dbt on it inside Fabric. Live verification
of that mode is Phase 5; this module holds what can be checked offline. What the job needs, from
the research (Q1):

- `dbt_project.yml` in the repository root, and no project in a subdirectory;
- no `env_var()` in any file dbt parses, because the job reads its profile from its own settings
  and whether it honours an environment variable without a default is not documented;
- a `packages.yml` that `dbt deps` can resolve, with every hub package pinned in
  `package-lock.yml` and the local package holding its own `dbt_project.yml`;
- no vendored `dbt_packages/` folder (the packages are installed, not committed, in 04.1);
- fewer tracked files than the job's import limit.

Per D-11 no check here ties the repository to the dbt Core line or the adapter version the job
runs. Those live only in `pyproject.toml`, `uv.lock` and ADR 0006, so this file writes neither.
It also holds the short doc, `docs/fabric-native-job.md`, to the phrases and Phase 5 check ids
a team needs to point the job at the repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import PurePosixPath

import yaml
from support.dbt_cli import REPO_ROOT

ROOT_PROJECT = "dbt_project.yml"
ALLOWED_NESTED_PROJECTS = ("packages/starter/dbt_project.yml",)
# A tracked file is parsed by dbt when it sits under one of these folders with one of these
# suffixes, or is one of the two root files. `profiles.yml` and Python files are outside the set.
DBT_PARSED_PREFIXES = (
    "models/",
    "macros/",
    "seeds/",
    "snapshots/",
    "analyses/",
    "tests/",
    "packages/starter/",
)
DBT_PARSED_SUFFIXES = (".sql", ".yml", ".yaml", ".md")
DBT_PARSED_ROOT_FILES = (ROOT_PROJECT, "packages.yml", "selectors.yml")
IMPORT_FILE_LIMIT = 5000

DOC = "docs/fabric-native-job.md"
DOC_ADR_PATH = "decisions/0006-fabric-lakehouse-retarget.md"
DOC_PHRASES = (
    "repository root",
    "classic",
    "5,000",
    "dbt deps",
    "enableSchemas",
    "FABRIC_SCHEMA",
    "UTC",
    "compile-checked only",
)
# The Phase 5 live checks the doc relies on, spelled as ADR 0006's PHASE5_CHECKS spells them.
DOC_PHASE5_CHECKS = (
    "native-job-deps",
    "schema-layout",
    "session-time-zone",
    "native-job-profile-schema",
)


def _is_dbt_parsed(path: str) -> bool:
    if path in DBT_PARSED_ROOT_FILES:
        return True
    return path.startswith(DBT_PARSED_PREFIXES) and path.endswith(DBT_PARSED_SUFFIXES)


def _normalised(local: str) -> str:
    return str(PurePosixPath(local.strip()))


def _package_entries(text: str, key: str) -> list[str]:
    loaded = yaml.safe_load(text) or {}
    entries = loaded.get("packages") or []
    return [str(entry[key]) for entry in entries if isinstance(entry, dict) and key in entry]


def check_native_job_layout(paths: list[str], files: Mapping[str, str]) -> list[str]:
    """Return every way the tracked tree would not run in the native dbt job.

    `paths` is the tracked-path list; `files` maps the files the check reads (the dbt project,
    `packages.yml`, `package-lock.yml`, `.gitignore` and every dbt-parsed file) to their text.
    """
    problems: list[str] = []
    tracked = set(paths)

    if ROOT_PROJECT not in tracked:
        problems.append(f"{ROOT_PROJECT} is not tracked in the repository root")
    for path in sorted(tracked):
        if PurePosixPath(path).name == ROOT_PROJECT and path not in (
            ROOT_PROJECT,
            *ALLOWED_NESTED_PROJECTS,
        ):
            problems.append(f"{path}: a dbt project in a subdirectory is not supported by the job")

    for path in sorted(tracked):
        if _is_dbt_parsed(path) and "env_var(" in files.get(path, ""):
            problems.append(f"{path}: env_var( in a file dbt parses; only profiles.yml may read it")

    packages_text = files.get("packages.yml", "")
    for local in _package_entries(packages_text, "local"):
        if f"{_normalised(local)}/{ROOT_PROJECT}" not in tracked:
            problems.append(f"packages.yml: local package {local} holds no tracked {ROOT_PROJECT}")
    locked = set(_package_entries(files.get("package-lock.yml", ""), "package"))
    for package in _package_entries(packages_text, "package"):
        if package not in locked:
            problems.append(f"packages.yml: {package} is not pinned in package-lock.yml")

    ignored = {line.strip() for line in files.get(".gitignore", "").splitlines()}
    if "dbt_packages/" not in ignored:
        problems.append(".gitignore: no dbt_packages/ line, so installed packages could be tracked")
    for path in sorted(tracked):
        if "dbt_packages" in PurePosixPath(path).parts:
            problems.append(f"{path}: dbt_packages/ is not vendored; `dbt deps` installs it")

    if len(paths) >= IMPORT_FILE_LIMIT:
        problems.append(
            f"{len(paths)} tracked files reach the job's import limit of {IMPORT_FILE_LIMIT}"
        )
    return problems


def _tracked_paths() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    return [raw.decode() for raw in listing.split(b"\0") if raw]


def _read_tracked(paths: list[str]) -> dict[str, str]:
    """Read the files the check needs: the four named ones and every dbt-parsed file."""
    wanted = [path for path in paths if _is_dbt_parsed(path)]
    wanted += [name for name in ("package-lock.yml", ".gitignore") if name in paths]
    return {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in wanted}


def _valid_tree() -> tuple[list[str], dict[str, str]]:
    """A small tree that passes every rule, for the doctored cases to break one rule at a time."""
    paths = [
        ROOT_PROJECT,
        "packages.yml",
        "package-lock.yml",
        ".gitignore",
        "profiles.yml",
        "models/a.sql",
        "packages/starter/dbt_project.yml",
        "packages/starter/macros/m.sql",
    ]
    files = {
        "packages.yml": (
            "packages:\n  - local: packages/starter\n  - package: dbt-labs/dbt_utils\n"
            '    version: [">=1.0.0"]\n'
        ),
        "package-lock.yml": (
            "packages:\n  - local: packages/starter\n    name: starter\n"
            "  - name: dbt_utils\n    package: dbt-labs/dbt_utils\n    version: 1.0.0\n"
        ),
        ".gitignore": "target/\ndbt_packages/\n",
        ROOT_PROJECT: "name: example\n",
        "models/a.sql": "select 1\n",
        "packages/starter/macros/m.sql": "{% macro m() %}1{% endmacro %}\n",
    }
    return paths, files


def test_the_real_tracked_tree_runs_in_the_native_job() -> None:
    paths = _tracked_paths()
    assert paths, "git ls-files returned nothing, so the check would pass vacuously"
    files = _read_tracked(paths)
    assert any(_is_dbt_parsed(path) for path in paths), "no dbt-parsed file, so the scan is vacuous"
    assert ROOT_PROJECT in files
    assert check_native_job_layout(paths, files) == []


def test_the_valid_tree_used_by_the_doctored_cases_passes() -> None:
    paths, files = _valid_tree()
    assert check_native_job_layout(paths, files) == []


def test_a_missing_root_project_is_reported() -> None:
    paths, files = _valid_tree()
    paths.remove(ROOT_PROJECT)
    assert any(
        "is not tracked in the repository root" in p for p in check_native_job_layout(paths, files)
    )


def test_a_project_in_another_subdirectory_is_reported_by_name() -> None:
    paths, files = _valid_tree()
    paths.append("analytics/dbt_project.yml")
    problems = check_native_job_layout(paths, files)
    assert [p for p in problems if p.startswith("analytics/dbt_project.yml")]
    assert not [p for p in problems if p.startswith("packages/starter/")]


def test_env_var_in_a_dbt_parsed_file_is_reported_by_name() -> None:
    paths, files = _valid_tree()
    files["models/a.sql"] = "select '{{ env_var(\"X\") }}'\n"
    problems = check_native_job_layout(paths, files)
    assert len(problems) == 1
    assert problems[0].startswith("models/a.sql")


def test_env_var_in_the_package_or_the_project_file_is_reported() -> None:
    paths, files = _valid_tree()
    files[ROOT_PROJECT] = "name: '{{ env_var(\"N\") }}'\n"
    files["packages/starter/macros/m.sql"] = "{{ env_var('Y') }}\n"
    problems = check_native_job_layout(paths, files)
    assert sorted(p.split(":")[0] for p in problems) == [
        ROOT_PROJECT,
        "packages/starter/macros/m.sql",
    ]


def test_env_var_in_profiles_yml_or_python_is_not_reported() -> None:
    paths, files = _valid_tree()
    paths.append("src/loader.py")
    files["profiles.yml"] = "path: \"{{ env_var('DBT_DUCKDB_PATH', 'x') }}\"\n"
    files["src/loader.py"] = "os.environ.get('env_var(')\n"
    assert check_native_job_layout(paths, files) == []


def test_a_local_package_without_a_project_file_is_reported() -> None:
    paths, files = _valid_tree()
    paths.remove("packages/starter/dbt_project.yml")
    problems = check_native_job_layout(paths, files)
    assert any("local package packages/starter holds no tracked" in p for p in problems)


def test_a_hub_package_missing_from_the_lock_is_reported() -> None:
    paths, files = _valid_tree()
    files["package-lock.yml"] = "packages:\n  - local: packages/starter\n    name: starter\n"
    problems = check_native_job_layout(paths, files)
    assert problems == ["packages.yml: dbt-labs/dbt_utils is not pinned in package-lock.yml"]


def test_a_gitignore_without_dbt_packages_is_reported() -> None:
    paths, files = _valid_tree()
    files[".gitignore"] = "target/\n"
    problems = check_native_job_layout(paths, files)
    assert len(problems) == 1
    assert problems[0].startswith(".gitignore")


def test_a_tracked_dbt_packages_path_is_reported() -> None:
    paths, files = _valid_tree()
    paths.append("dbt_packages/dbt_utils/macros/x.sql")
    problems = check_native_job_layout(paths, files)
    assert len(problems) == 1
    assert problems[0].startswith("dbt_packages/dbt_utils/macros/x.sql")


def test_a_tree_at_the_import_limit_is_reported_and_one_below_it_is_not() -> None:
    paths, files = _valid_tree()
    padding = [f"models/pad_{index}.sql" for index in range(IMPORT_FILE_LIMIT - len(paths) - 1)]
    assert len(paths) + len(padding) == IMPORT_FILE_LIMIT - 1
    assert check_native_job_layout([*paths, *padding], files) == []
    problems = check_native_job_layout([*paths, *padding, "models/one_more.sql"], files)
    assert len(problems) == 1
    assert "import limit" in problems[0]


def test_the_native_job_doc_covers_the_layout() -> None:
    doc_path = REPO_ROOT / DOC
    assert doc_path.is_file(), f"{DOC} is missing"
    text = doc_path.read_text(encoding="utf-8")
    missing = [phrase for phrase in DOC_PHRASES if phrase not in text]
    assert missing == [], f"{DOC} does not mention: {missing}"
    assert DOC_ADR_PATH in text, f"{DOC} does not point at {DOC_ADR_PATH} for the versions"
    absent = [check for check in DOC_PHASE5_CHECKS if check not in text]
    assert absent == [], f"{DOC} does not name the Phase 5 checks: {absent}"
