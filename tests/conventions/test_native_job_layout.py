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
It also holds the Fabric setup guide, `docs/fabric-setup.md`, to the topics, the Phase 5 check ids
and the commands a team needs to set Fabric up (PORT-06), and the pointer page that keeps the old
address of the native-job notes alive.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
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

DOC = "docs/fabric-setup.md"
POINTER_DOC = "docs/fabric-native-job.md"
DOC_ADR_PATH = "decisions/0006-fabric-lakehouse-retarget.md"
DOC_SINK_ADR_PATH = "decisions/0007-lakehouse-landing-sink.md"
# One entry per `## ` heading of the guide, in order. Every walkthrough record maps these names.
GUIDE_SECTIONS = (
    "Before you start",
    "Sign in",
    "Create the Lakehouse",
    "Environment variables",
    "Land data",
    "Self-hosted builds",
    "Known limits",
    "The native dbt job",
    "CI (opt-in)",
    "Re-verify",
    "Teardown",
)
# What the guide has to say, as phrases it contains. Grouped by the section that holds them.
GUIDE_TOPICS = (
    # Before you start: the own-tenant trial and its fallback.
    "work or school",
    "60 days",
    "deleted when",
    "F2",
    "Runtime 2.0",
    # Sign in.
    "az login --allow-no-subscriptions",
    # Create the Lakehouse.
    "setup-fabric",
    "enableSchemas",
    ".env.fabric",
    # Environment variables.
    "FABRIC_WORKSPACE_ID",
    "FABRIC_LAKEHOUSE_ID",
    "FABRIC_LAKEHOUSE",
    "FABRIC_SCHEMA",
    "FABRIC_AUTHENTICATION",
    "FABRIC_HIGH_CONCURRENCY",
    "FABRIC_DBT_JOB_ID",
    "UTC",
    # Land data.
    "extra fabric load <source> <entity> --target fabric",
    "--target fabric",
    "FABRIC_DEFAULT_MONTHS",
    "One load at a time",
    # Self-hosted builds.
    "make build-fabric",
    "--selector fabric_fixture",
    "make demo-fabric",
    "CLEAN=1",
    "livySessions",
    "TooManyRequestsForCapacity",
    # Known limits.
    "fabric_known_limit",
    "tag:fabric_known_limit+",
    # The native dbt job.
    "repository root",
    "classic",
    "public_repo",
    "5,000",
    "dbt deps",
    "Seed data",
    "threads",
    "Full refresh",
    "InvalidJobType",
    "verify-fabric init",
    # CI (opt-in).
    "ENABLE_FABRIC",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "FABRIC_CI_LAKEHOUSE_ID",
    "FABRIC_CI_LAKEHOUSE",
    "oidc/customization/sub",
    "sub_claim_prefix",
    ":ref:refs/heads/main",
    "Contributor",
    "Service principals can use Fabric APIs",
    "Developer settings",
    "not run live",
    # Re-verify and Teardown.
    "make verify-fabric",
    "verify-fabric record",
    "az ad app delete",
    "gh secret delete",
)
# The Phase 5 live checks the doc relies on, spelled as ADR 0006's PHASE5_CHECKS spells them.
DOC_PHASE5_CHECKS = (
    "native-job-deps",
    "schema-layout",
    "session-time-zone",
    "native-job-profile-schema",
)


GUIDE_PLACEHOLDERS = (
    "<workspace-id>",
    "<lakehouse-id>",
    "<tenant>",
    "<owner>/<repo>",
    "<client-id>",
)
UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Words that open a command in the guide's code: `make <target>` and `uv run ... <script>`.
UV_VALUE_FLAGS = ("--extra", "--with", "--python", "--group")
SHELL_OPERATORS = (">>", ">", "|", ";", "&&", "||")


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


# ---------------------------------------------------------------------------------------------
# The setup guide (PORT-06)


def _guide_text(relative: str = DOC) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


def guide_headings(text: str) -> list[str]:
    """Return the `## ` headings of a Markdown page, outside fenced code."""
    headings, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def guide_spans(text: str) -> list[str]:
    """Return every line of a fenced block and every inline code span, stripped."""
    spans, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif fenced:
            spans.append(line.strip())
        else:
            spans.extend(span.strip() for span in re.findall(r"`([^`]+)`", line))
    return spans


# Built from parts so test_handle's scan, which reads this file for the marker words, does not
# take the constants for a marker of its own.
_MARK = "<!-- example:"
EXAMPLE_BEGIN = f"{_MARK}begin -->"
EXAMPLE_END = f"{_MARK}end -->"
GUIDE_MONTH_RE = re.compile(r"\b20[0-9]{2}-(0[1-9]|1[0-2])\b")


def months_outside_markers(text: str) -> list[int]:
    """Return the 1-based lines outside example marker blocks that name a `20YY-MM` month.

    A line whose stripped text is exactly an example begin or end marker opens or closes a block
    (the forms `tests/conventions/test_handle.py` names); the `YYYY-MM` placeholder is not a month.
    """
    lines, inside = [], False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == EXAMPLE_BEGIN:
            inside = True
        elif stripped == EXAMPLE_END:
            inside = False
        elif not inside and GUIDE_MONTH_RE.search(line):
            lines.append(number)
    return lines


def guide_commands(text: str) -> list[dict[str, object]]:
    """Return the `make <target>` and `uv run ... <script>` commands the guide shows.

    Each is `{"kind": "make" | "script", "name", "args", "variables"}`. `args` holds what follows
    the target or script (flags, then positional words) up to a shell operator; `variables` holds
    the `NAME=value` words of a make command. Only spans that start with `make ` or `uv run ` are
    commands, so prose such as "make sure" is never read as one.
    """
    commands: list[dict[str, object]] = []
    for span in guide_spans(text):
        if not span.startswith(("make ", "uv run ")):
            continue
        words = shlex.split(span, posix=True)
        for index, word in enumerate(words):
            if word in SHELL_OPERATORS:
                words = words[:index]
                break
        if words[0] == "make":
            rest = [word for word in words[1:] if not word.startswith("-")]
            if not rest:
                continue
            commands.append(
                {
                    "kind": "make",
                    "name": rest[0],
                    "args": [],
                    "variables": [word.split("=", 1)[0] for word in rest[1:] if "=" in word],
                }
            )
            continue
        rest, skip = words[2:], False
        script_at = None
        for index, word in enumerate(rest):
            if skip:
                skip = False
            elif word in UV_VALUE_FLAGS:
                skip = True
            elif not word.startswith("-"):
                script_at = index
                break
        if script_at is None:
            continue
        commands.append(
            {
                "kind": "script",
                "name": rest[script_at],
                "args": rest[script_at + 1 :],
                "variables": [],
            }
        )
    return commands


def _makefile_names() -> tuple[set[str], set[str]]:
    """Return the Makefile's targets and its `NAME ?=` variables."""
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", text, flags=re.MULTILINE))
    variables = set(re.findall(r"^([A-Z][A-Z_]*)\s*\?=", text, flags=re.MULTILINE))
    return targets, variables


def _console_script_sources() -> dict[str, str]:
    """Map each console script of pyproject.toml to the text of the module that implements it."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sources = {}
    for name, target in pyproject["project"]["scripts"].items():
        module = target.split(":", 1)[0].replace(".", "/")
        sources[name] = (REPO_ROOT / "src" / f"{module}.py").read_text(encoding="utf-8")
    return sources


def command_problems(
    commands: list[dict[str, object]],
    targets: set[str],
    variables: set[str],
    scripts: Mapping[str, str],
) -> list[str]:
    """Return every command that names a target, variable, script, flag or subcommand that is not real."""
    problems: list[str] = []
    for command in commands:
        name = str(command["name"])
        if command["kind"] == "make":
            if name not in targets:
                problems.append(f"make {name}: not a Makefile target")
            problems.extend(
                f"make {name} {variable}=: not a Makefile variable"
                for variable in command["variables"]  # type: ignore[attr-defined]
                if variable not in variables
            )
            continue
        if name not in scripts:
            problems.append(f"uv run {name}: not a console script")
            continue
        source = scripts[name]
        args: list[str] = command["args"]  # type: ignore[assignment]
        problems.extend(
            f"{name} {arg}: the script does not define that flag"
            for arg in args
            if arg.startswith("--") and f'"{arg}"' not in source
        )
        positional = [arg for arg in args if not arg.startswith("-")]
        if name == "verify-fabric" and positional and f'"{positional[0]}"' not in source:
            problems.append(f"verify-fabric {positional[0]}: not a subcommand")
    return problems


def test_the_guide_has_every_section_in_order() -> None:
    assert guide_headings(_guide_text()) == list(GUIDE_SECTIONS)


def test_the_setup_guide_covers_the_topics() -> None:
    text = _guide_text()
    missing = [phrase for phrase in GUIDE_TOPICS if phrase not in text]
    assert missing == [], f"{DOC} does not mention: {missing}"
    assert DOC_ADR_PATH in text, f"{DOC} does not point at {DOC_ADR_PATH} for the versions"
    assert DOC_SINK_ADR_PATH in text, f"{DOC} does not point at {DOC_SINK_ADR_PATH}"
    absent = [check for check in DOC_PHASE5_CHECKS if check not in text]
    assert absent == [], f"{DOC} does not name the Phase 5 checks: {absent}"


def test_the_fabric_guide_names_no_month_outside_example_markers() -> None:
    assert months_outside_markers(_guide_text()) == []


def test_a_month_outside_the_example_markers_is_reported_with_its_line() -> None:
    block = f"{EXAMPLE_BEGIN}\nIn the block.\n{EXAMPLE_END}\n"
    before = f"The default is 2031-07.\n{block}"
    assert months_outside_markers(before) == [1]
    after = f"{block}Then 2031-07 again.\n"
    assert months_outside_markers(after) == [4]
    option = "Pass `--months 2031-07..2031-09` to choose.\n"
    assert months_outside_markers(option) == [1]


def test_a_month_inside_the_example_markers_and_a_placeholder_are_not_reported() -> None:
    text = (
        "Pass `--months YYYY-MM..YYYY-MM` to choose.\n"
        f"{EXAMPLE_BEGIN}\n"
        "The example lands 2031-07 and 2031-08.\n"
        f"{EXAMPLE_END}\n"
        "Lands YYYY-MM.\n"
    )
    assert months_outside_markers(text) == []


def test_the_guide_names_only_real_commands() -> None:
    commands = guide_commands(_guide_text())
    targets, variables = _makefile_names()
    names = {(command["kind"], command["name"]) for command in commands}
    # Not vacuous: the commands a reader runs are all found.
    for expected in (
        ("make", "setup-fabric"),
        ("make", "build-fabric"),
        ("make", "demo-fabric"),
        ("make", "verify-fabric"),
        ("script", "setup-fabric"),
        ("script", "load"),
        ("script", "build-fabric"),
        ("script", "verify-fabric"),
    ):
        assert expected in names, f"{DOC} shows no `{expected[0]}` command for {expected[1]}"
    assert command_problems(commands, targets, variables, _console_script_sources()) == []


def test_a_command_the_repository_does_not_have_is_reported() -> None:
    text = (
        "```bash\nmake -s setup-fabric >> .env.fabric\n```\n"
        "Run `make demo-fabric CLEAN=1`, `make deploy-fabric`, `make demo-fabric SPEED=1`,\n"
        "`uv run --locked --extra fabric load some_source some_entity --target fabric --bogus`,\n"
        "`uv run --extra fabric nope-fabric`, `uv run verify-fabric rehearse` and make sure of it.\n"
    )
    targets, variables = _makefile_names()
    commands = guide_commands(text)
    assert [(c["kind"], c["name"]) for c in commands] == [
        ("make", "setup-fabric"),
        ("make", "demo-fabric"),
        ("make", "deploy-fabric"),
        ("make", "demo-fabric"),
        ("script", "load"),
        ("script", "nope-fabric"),
        ("script", "verify-fabric"),
    ]
    assert command_problems(commands, targets, variables, _console_script_sources()) == [
        "make deploy-fabric: not a Makefile target",
        "make demo-fabric SPEED=: not a Makefile variable",
        "load --bogus: the script does not define that flag",
        "uv run nope-fabric: not a console script",
        "verify-fabric rehearse: not a subcommand",
    ]


def test_the_guide_holds_placeholders_not_ids() -> None:
    text = _guide_text()
    assert UUID_PATTERN.findall(text) == [], f"{DOC} holds UUID-shaped text"
    missing = [placeholder for placeholder in GUIDE_PLACEHOLDERS if placeholder not in text]
    assert missing == [], f"{DOC} does not use the placeholders: {missing}"


def test_a_uuid_in_the_guide_would_be_caught() -> None:
    assert UUID_PATTERN.findall("workspace 0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d here")
    assert not UUID_PATTERN.findall("<workspace-id> and <lakehouse-id>")


def test_the_pointer_page_links_the_guide() -> None:
    text = _guide_text(POINTER_DOC)
    assert "fabric-setup.md" in text
    assert len(text.splitlines()) <= 12, f"{POINTER_DOC} is a pointer, not a second guide"


def readme_run_modes(text: str) -> str:
    """Return the README's "Run modes" section, up to the next `## ` heading."""
    start = text.index("## Run modes")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_the_readme_links_the_guide_for_each_run_mode() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme_run_modes(readme)
    missing = [
        command
        for command in ("make demo", "make demo-fabric", "make build-fabric", "make setup-fabric")
        if f"`{command}`" not in section
    ]
    assert missing == [], f"README's Run modes section does not name: {missing}"
    assert "docs/fabric-setup.md" in section
    assert "docs/fabric-setup.md#the-native-dbt-job" in section, "the native job needs its step"
    assert "docs/fabric-setup.md#environment-variables" in section
    assert POINTER_DOC not in readme, "the README should link the guide, not the pointer page"


def test_the_readme_anchors_exist_in_the_guide() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    anchors = set(re.findall(r"docs/fabric-setup\.md#([a-z0-9-]+)", readme))
    assert anchors, "the README links no guide section"
    known = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in GUIDE_SECTIONS}
    assert anchors <= known, f"README links guide sections that do not exist: {anchors - known}"


def test_a_readme_without_the_run_mode_commands_is_caught() -> None:
    section = readme_run_modes("# x\n\n## Run modes\n\nOnly `make demo` here.\n\n## Next\n")
    assert "make demo-fabric" not in section
    assert "## Next" not in section
