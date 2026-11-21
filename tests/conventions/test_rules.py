"""Checks on the rule registries: static scan, unit-test coverage, and parse-time failures.

The compile-time Jinja check in the quarantine framework covers one registry at a time. The
checks here cover what Jinja cannot see: codes unique across every registry file, every flag
exercised by a unit test as both 0 and 1, and the framework failing loudly when a registry is
wrong.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

REGISTRY_GLOB = "macros/rules/*/*.sql"
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
CODE_LITERAL = re.compile(r"'code'\s*:\s*'([^']*)'")
CAST_CHECK_LITERAL = re.compile(r"cast_check_rule\(\s*'([^']*)'")
DICT_LITERAL = re.compile(r"\{[^{}]*\}")


def registry_files(root: Path = REPO_ROOT) -> dict[str, str]:
    """Return every registry file under `root`, keyed by its path relative to `root`."""
    return {
        str(path.relative_to(root)): path.read_text() for path in sorted(root.glob(REGISTRY_GLOB))
    }


def registry_codes(files: dict[str, str]) -> list[str]:
    """Return every rule code literal found in the registry files, in file order."""
    codes = []
    for text in files.values():
        codes += CODE_LITERAL.findall(text)
        codes += CAST_CHECK_LITERAL.findall(text)
    return codes


def scan_registries(files: dict[str, str]) -> list[str]:
    """Return the problems found in the registry files, sorted.

    Rules: every code is UPPER_SNAKE with an entity prefix and at least one more part, every
    rule dict spells out `on_null` and `severity`, and no code repeats across files, with
    codes compared ignoring case.
    """
    problems: list[str] = []
    seen: dict[str, tuple[str, str]] = {}

    for path, text in files.items():
        for literal in DICT_LITERAL.findall(text):
            match = CODE_LITERAL.search(literal)
            if not match:
                continue
            code = match.group(1)
            for key in ("on_null", "severity"):
                if not re.search(rf"'{key}'\s*:", literal):
                    problems.append(f"{path}: rule {code} has no '{key}' key")

        for code in CODE_LITERAL.findall(text) + CAST_CHECK_LITERAL.findall(text):
            if not CODE_PATTERN.match(code):
                problems.append(
                    f"{path}: rule code {code!r} must be UPPER_SNAKE with an entity prefix"
                )
            folded = code.upper()
            if folded in seen:
                first_code, first_path = seen[folded]
                problems.append(
                    f"duplicate rule code {code!r} in {path}: already used as "
                    f"{first_code!r} in {first_path}"
                )
            else:
                seen[folded] = (code, path)
    return sorted(problems)


def _flag_values(manifest: dict, flag: str) -> set[int]:
    values: set[int] = set()
    for unit_test in (manifest.get("unit_tests") or {}).values():
        for row in ((unit_test.get("expect") or {}).get("rows")) or []:
            if flag in row and str(row[flag]).strip() in ("0", "1"):
                values.add(int(str(row[flag]).strip()))
    return values


def check_unit_test_coverage(manifest: dict, codes: list[str]) -> list[str]:
    """Return one problem per rule flag that no unit test shows as both 0 and 1."""
    problems = []
    for code in codes:
        flag = f"dq_fail_{code.lower()}"
        seen = _flag_values(manifest, flag)
        for wanted in (0, 1):
            if wanted not in seen:
                problems.append(f"{code}: no unit-test expect row has {flag} = {wanted}")
    return sorted(problems)


def check_uniform_expect_keys(manifest: dict) -> list[str]:
    """Return one problem per unit test whose expect rows do not all carry the same keys."""
    problems = []
    for uid, unit_test in (manifest.get("unit_tests") or {}).items():
        rows = ((unit_test.get("expect") or {}).get("rows")) or []
        key_sets = {tuple(sorted(row)) for row in rows if isinstance(row, dict)}
        if len(key_sets) > 1:
            problems.append(f"{uid}: expect rows carry {len(key_sets)} different key sets")
    return sorted(problems)


# --- synthetic registries ----------------------------------------------------------------

GOOD_REGISTRY = """{% macro rules__widgets__parts() %}
  {{- return([
    {
      'code': 'WIDGET_PART_MISSING',
      'description': 'part is missing',
      'severity': 'error',
      'on_null': 'fail',
      'fail_when': 'part is null'
    },
    starter.cast_check_rule('WIDGET_PART_UNPARSEABLE', 'part__raw', 'part', 'part is not a number')
  ]) -}}
{% endmacro %}
"""


def one_rule(code: str, *, on_null: bool = True, severity: bool = True) -> str:
    keys = [f"'code': '{code}'", "'description': 'd'", "'fail_when': 'x is null'"]
    if severity:
        keys.append("'severity': 'error'")
    if on_null:
        keys.append("'on_null': 'pass'")
    return (
        "{% macro rules__widgets__other() %}{{- return([{"
        + ", ".join(keys)
        + "}]) -}}{% endmacro %}"
    )


def test_a_good_registry_passes_the_scan() -> None:
    assert scan_registries({"macros/rules/widgets/parts.sql": GOOD_REGISTRY}) == []


def test_the_real_registries_pass_the_scan() -> None:
    files = registry_files()
    assert files, f"no registry files matched {REGISTRY_GLOB}"
    assert registry_codes(files), "the registries hold no rule codes, so the scan checked nothing"
    assert scan_registries(files) == []


def test_a_code_repeated_across_two_files_names_both_paths() -> None:
    files = {
        "macros/rules/widgets/a.sql": one_rule("WIDGET_SAME"),
        "macros/rules/widgets/b.sql": one_rule("WIDGET_SAME"),
    }
    problems = scan_registries(files)
    assert len(problems) == 1
    assert "duplicate" in problems[0]
    assert "a.sql" in problems[0] and "b.sql" in problems[0]


def test_codes_equal_ignoring_case_are_duplicates() -> None:
    files = {
        "macros/rules/widgets/a.sql": one_rule("WIDGET_SAME"),
        "macros/rules/widgets/b.sql": one_rule("widget_same"),
    }
    assert any("duplicate" in problem for problem in scan_registries(files))


def test_a_lowercase_code_fails() -> None:
    problems = scan_registries({"macros/rules/widgets/a.sql": one_rule("widget_low")})
    assert any("UPPER_SNAKE" in problem for problem in problems)


def test_a_code_without_an_entity_prefix_fails() -> None:
    problems = scan_registries({"macros/rules/widgets/a.sql": one_rule("WIDGET")})
    assert any("UPPER_SNAKE" in problem and "WIDGET" in problem for problem in problems)


def test_a_dict_without_on_null_fails() -> None:
    problems = scan_registries({"macros/rules/widgets/a.sql": one_rule("WIDGET_A", on_null=False)})
    assert problems == ["macros/rules/widgets/a.sql: rule WIDGET_A has no 'on_null' key"]


def test_a_dict_without_severity_fails() -> None:
    problems = scan_registries({"macros/rules/widgets/a.sql": one_rule("WIDGET_A", severity=False)})
    assert problems == ["macros/rules/widgets/a.sql: rule WIDGET_A has no 'severity' key"]


def test_scan_problems_are_sorted() -> None:
    files = {
        "macros/rules/widgets/b.sql": one_rule("bad_b"),
        "macros/rules/widgets/a.sql": one_rule("bad_a"),
    }
    problems = scan_registries(files)
    assert len(problems) == 2
    assert problems == sorted(problems)


# --- unit-test coverage ------------------------------------------------------------------


def unit_tests(*expect_row_groups: list[dict]) -> dict:
    return {
        "unit_tests": {
            f"unit_test.proj.m.t{index}": {"expect": {"rows": rows}}
            for index, rows in enumerate(expect_row_groups)
        }
    }


def test_coverage_passes_when_a_flag_is_seen_as_0_and_as_1() -> None:
    manifest = unit_tests([{"dq_fail_widget_a": 0}, {"dq_fail_widget_a": 1}])
    assert check_unit_test_coverage(manifest, ["WIDGET_A"]) == []


def test_coverage_counts_values_across_different_unit_tests() -> None:
    manifest = unit_tests([{"dq_fail_widget_a": 0}], [{"dq_fail_widget_a": 1}])
    assert check_unit_test_coverage(manifest, ["WIDGET_A"]) == []


def test_coverage_accepts_numbers_written_as_strings() -> None:
    manifest = unit_tests([{"dq_fail_widget_a": "0"}, {"dq_fail_widget_a": "1"}])
    assert check_unit_test_coverage(manifest, ["WIDGET_A"]) == []


def test_a_flag_seen_only_as_1_is_reported() -> None:
    manifest = unit_tests([{"dq_fail_widget_a": 1}])
    problems = check_unit_test_coverage(manifest, ["WIDGET_A"])
    assert problems == ["WIDGET_A: no unit-test expect row has dq_fail_widget_a = 0"]


def test_a_flag_seen_only_as_0_is_reported() -> None:
    manifest = unit_tests([{"dq_fail_widget_a": 0}])
    problems = check_unit_test_coverage(manifest, ["WIDGET_A"])
    assert problems == ["WIDGET_A: no unit-test expect row has dq_fail_widget_a = 1"]


def test_a_flag_never_seen_is_reported_for_both_values() -> None:
    manifest = unit_tests([{"dq_fail_widget_b": 0}, {"dq_fail_widget_b": 1}])
    problems = check_unit_test_coverage(manifest, ["WIDGET_A", "WIDGET_B"])
    assert len(problems) == 2
    assert all("WIDGET_A" in problem for problem in problems)


def test_coverage_with_no_unit_tests_reports_every_rule() -> None:
    assert len(check_unit_test_coverage({"unit_tests": {}}, ["WIDGET_A"])) == 2


def test_every_real_rule_has_a_passing_and_a_failing_unit_test_row(duckdb_manifest: dict) -> None:
    codes = registry_codes(registry_files())
    assert codes, "the registries hold no rule codes"
    assert duckdb_manifest["unit_tests"], "the manifest holds no unit tests"
    assert check_unit_test_coverage(duckdb_manifest, codes) == []


def test_uniform_keys_pass_when_every_expect_row_has_the_same_keys() -> None:
    manifest = unit_tests([{"a": 1, "b": 2}, {"b": 3, "a": 4}])
    assert check_uniform_expect_keys(manifest) == []


def test_expect_rows_with_different_keys_are_reported() -> None:
    manifest = unit_tests([{"a": 1, "b": 2}, {"a": 3}])
    problems = check_uniform_expect_keys(manifest)
    assert len(problems) == 1
    assert "unit_test.proj.m.t0" in problems[0]


def test_the_real_unit_tests_use_uniform_expect_keys(duckdb_manifest: dict) -> None:
    assert check_uniform_expect_keys(duckdb_manifest) == []


# --- parse-time failures -----------------------------------------------------------------

COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".claude",
    ".planning",
    "target",
    "data",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)


def defective_rule(code: str, **overrides: str | None) -> str:
    """Return a rule dict literal with every key present, then apply the overrides.

    An override of None removes the key.
    """
    rule = {
        "code": code,
        "description": "a deliberately defective rule",
        "severity": "error",
        "on_null": "pass",
        "fail_when": "1 = 1",
    }
    rule.update(overrides)
    body = ", ".join(f"'{key}': '{value}'" for key, value in rule.items() if value is not None)
    return "{" + body + "},"


@pytest.fixture(scope="module")
def project_copy(dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """A copy of the project plus its first registry file and that file's first rule code."""
    destination = tmp_path_factory.mktemp("proj")
    shutil.copytree(REPO_ROOT, destination, symlinks=True, ignore=COPY_IGNORE, dirs_exist_ok=True)
    registry = min(destination.glob(REGISTRY_GLOB))
    text = registry.read_text()
    first = re.search(r"'code'\s*:\s*'([^']*)'|cast_check_rule\(\s*'([^']*)'", text)
    assert first, f"{registry} holds no rule code"
    return {
        "root": destination,
        "registry": registry,
        "original": text,
        "code": first.group(1) or first.group(2),
    }


def parse_copy(project: dict, name: str) -> tuple[int, str]:
    result = run_dbt(
        ["parse", "--no-partial-parse", "--target-path", str(project["root"] / f"t_{name}")],
        project_dir=project["root"],
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_the_untouched_copy_parses(project_copy: dict) -> None:
    code, output = parse_copy(project_copy, "control")
    assert code == 0, output[-2000:]


def inject(project: dict, code: str, **overrides: str | None) -> str:
    return project["original"].replace(
        "return([", "return([" + defective_rule(code, **overrides), 1
    )


# case -> (code of the injected rule from the first real code C, overrides, message fragment)
PARSE_CASES = {
    "missing_on_null": (lambda c: c + "_NEG", {"on_null": None}, "on_null"),
    "on_null_maybe": (lambda c: c + "_NEG", {"on_null": "maybe"}, "on_null"),
    "severity_warn": (lambda c: c + "_NEG", {"severity": "warn"}, "Phase 2"),
    "severity_fatal": (lambda c: c + "_NEG", {"severity": "fatal"}, "severity"),
    "code_not_upper_snake": (lambda c: "bad_code", {}, "UPPER_SNAKE"),
    "wrong_prefix": (lambda c: "OTHER_PREFIX_RULE", {}, "prefix"),
    "duplicate_code": (lambda c: c, {}, "duplicate"),
}


@pytest.mark.parametrize("case", sorted(PARSE_CASES))
def test_a_defective_registry_fails_dbt_parse_with_its_message(
    project_copy: dict, case: str
) -> None:
    make_code, overrides, fragment = PARSE_CASES[case]
    text = inject(project_copy, make_code(project_copy["code"]), **overrides)
    assert text != project_copy["original"]
    registry = project_copy["registry"]
    registry.write_text(text)
    try:
        code, output = parse_copy(project_copy, case)
    finally:
        registry.write_text(project_copy["original"])
    assert code != 0, f"{case}: dbt parse succeeded on a defective registry"
    assert fragment in output, f"{case}: {fragment!r} not in output\n{output[-2000:]}"


def test_an_empty_registry_fails_dbt_parse_saying_it_has_no_rules(project_copy: dict) -> None:
    text = re.sub(r"return\(\[.*\]\)", "return([])", project_copy["original"], flags=re.DOTALL)
    assert text != project_copy["original"]
    registry = project_copy["registry"]
    registry.write_text(text)
    try:
        code, output = parse_copy(project_copy, "empty")
    finally:
        registry.write_text(project_copy["original"])
    assert code != 0, "dbt parse succeeded on an empty registry"
    assert "no rules" in output, output[-2000:]


# --- NULL predicate handling -------------------------------------------------------------

NULL_PREDICATE_SQL = """
{% set rules = [
    {'code': 'WIDGET_A', 'description': 'a', 'severity': 'error', 'on_null': 'fail',
     'fail_when': 'cast(null as int) > 1'},
    {'code': 'WIDGET_B', 'description': 'b', 'severity': 'error', 'on_null': 'pass',
     'fail_when': 'cast(null as int) > 1'}
] %}
select {{ starter.flag_columns(rules) }}
"""


def test_a_null_predicate_follows_on_null_for_each_rule(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    result = run_dbt(
        [
            "show",
            "--inline",
            NULL_PREDICATE_SQL,
            "--output",
            "json",
            "--target-path",
            str(tmp_path),
        ],
        env={"DBT_DUCKDB_PATH": str(tmp_path / "show.duckdb")},
    )
    start = result.stdout.find('{\n  "show"')
    assert start != -1, result.stdout[-2000:]
    shown, _end = json.JSONDecoder().raw_decode(result.stdout[start:])
    flat = shown["show"][0]
    assert flat["dq_fail_widget_a"] == 1
    assert flat["dq_fail_widget_b"] == 0
