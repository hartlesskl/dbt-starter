"""No generic module holds a month value (D-22, D-07).

A month appears in generic code only inside a format example in prose, a month list or range in
a docstring, an argparse `help=` text or a raised error message, and a lone month there is
reported like a value. Every other string constant that holds a month is a value and belongs in
the source module that owns it.

The scan reads every string constant in every top-level module of `src/starter_cli`, function and
method bodies and f-string parts included. Prose is exactly three places: a docstring (a bare
string statement), every constant inside the value of a `help=` keyword, and every constant inside
the exception of a `raise`. In those places each month list or range (`<month>,<month>` or
`<month>..<month>`, or a mix) is removed from the text before the month search; the `YYYY-MM`
placeholder is not a month, and neither is a full `YYYY-MM-DD` date.

The file names no source and no example, so it stays in a copy and passes there.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from support.dbt_cli import REPO_ROOT

PACKAGE_DIR = REPO_ROOT / "src" / "starter_cli"

_MONTH = r"[0-9]{4}-(?:0[1-9]|1[0-2])"
# A month, and not the start of a full `YYYY-MM-DD` date.
MONTH_RE = re.compile(rf"\b{_MONTH}\b(?!-[0-9])")
# A month list, a range or a mix: a month followed by one or more of `..` or `,` (with an optional
# space) and another month.
FORMAT_EXAMPLE_RE = re.compile(rf"\b{_MONTH}(?:[ ]?(?:\.\.|,)[ ]?{_MONTH})+\b(?!-[0-9])")


def _is_prose_holder(node: ast.Expr | ast.keyword) -> bool:
    """A docstring statement (a bare constant) or a `help=` keyword."""
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant)
    return node.arg == "help"


def prose_constants(tree: ast.AST) -> set[int]:
    """Return the ids of the string constants that are prose.

    Prose is the constant of a bare expression statement (a docstring), every constant inside the
    value of a `help=` keyword, and every constant inside the exception of a `raise`.
    """
    prose: set[int] = set()
    for node in ast.walk(tree):
        roots: list[ast.AST] = []
        if isinstance(node, ast.Expr | ast.keyword) and _is_prose_holder(node):
            roots.append(node.value)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            roots.append(node.exc)
        for root in roots:
            prose.update(id(inner) for inner in ast.walk(root) if isinstance(inner, ast.Constant))
    return prose


def month_value_problems(path: str, text: str) -> list[str]:
    """Return `<path>:<line>` for every string constant that holds a month it must not.

    A non-prose constant holding a month is a value. A prose constant is reported when it still
    holds a month after each month list or range is removed from its text.
    """
    tree = ast.parse(text)
    prose = prose_constants(tree)
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        searched = node.value
        if id(node) in prose:
            searched = FORMAT_EXAMPLE_RE.sub("", searched)
        if MONTH_RE.search(searched):
            lines.append(node.lineno)
    return [f"{path}:{line}" for line in sorted(lines)]


def generic_modules(root: Path = PACKAGE_DIR) -> list[Path]:
    return sorted(root.glob("*.py"))


def test_no_generic_module_holds_a_month_value() -> None:
    modules = generic_modules()
    assert {module.name for module in modules} >= {"landing.py", "load.py", "demo.py"}
    problems: list[str] = []
    for module in modules:
        relative = module.relative_to(REPO_ROOT).as_posix()
        problems.extend(month_value_problems(relative, module.read_text(encoding="utf-8")))
    assert problems == []


# The doctored cases use a neutral month, never the example's.


def test_a_module_level_constant_is_reported_with_its_line() -> None:
    text = 'import os\n\nDEFAULT = "2031-07"\n'
    assert month_value_problems("m.py", text) == ["m.py:3"]


def test_a_constant_inside_a_method_body_is_reported() -> None:
    text = (
        "class Spec:\n"
        "    def check(self):\n"
        "        sample = self.name.replace('{month}', '2031-07')\n"
        "        return sample\n"
    )
    assert month_value_problems("m.py", text) == ["m.py:3"]


def test_a_month_in_an_f_string_part_outside_prose_is_reported() -> None:
    text = 'def path(x):\n    return f"data/2031-07/{x}"\n'
    assert month_value_problems("m.py", text) == ["m.py:2"]


def test_a_lone_month_in_a_docstring_is_reported() -> None:
    text = '"""Lands 2031-07 by default."""\n'
    assert month_value_problems("m.py", text) == ["m.py:1"]


def test_a_lone_month_in_a_function_docstring_is_reported() -> None:
    text = 'def f():\n    """Lands 2031-07 by default."""\n'
    assert month_value_problems("m.py", text) == ["m.py:2"]


def test_a_help_text_with_examples_and_a_lone_default_month_is_reported() -> None:
    text = (
        "parser.add_argument(\n"
        '    "--months",\n'
        '    help="Months such as 2031-07,2031-09 or 2031-07..2031-09; the default is 2031-07.",\n'
        ")\n"
    )
    assert month_value_problems("m.py", text) == ["m.py:3"]


def test_a_lone_month_in_a_raised_message_is_reported() -> None:
    text = 'def f():\n    raise ValueError("the default is 2031-07")\n'
    assert month_value_problems("m.py", text) == ["m.py:2"]


def test_a_month_list_and_a_range_are_not_reported_in_a_docstring() -> None:
    text = (
        '"""Takes 2031-07,2031-09 or 2031-07..2031-09."""\n'
        "\n"
        "class C:\n"
        '    """A mix: 2031-07, 2031-09..2031-11."""\n'
        "\n"
        "    def f(self):\n"
        '        """A range 2031-07..2031-09."""\n'
    )
    assert month_value_problems("m.py", text) == []


def test_a_month_list_and_a_range_are_not_reported_in_a_help_text() -> None:
    text = 'parser.add_argument("--months", help="A list 2031-07,2031-09 or a range 2031-07..2031-09.")\n'
    assert month_value_problems("m.py", text) == []


def test_a_month_list_and_a_range_are_not_reported_in_a_raised_message() -> None:
    text = (
        "def f(part):\n"
        '    raise ValueError(f"Month {part!r} must look like YYYY-MM (a list such as '
        '2031-07,2031-09 or a range such as 2031-07..2031-09).")\n'
    )
    assert month_value_problems("m.py", text) == []


def test_the_placeholder_a_full_date_and_a_regex_are_not_reported() -> None:
    text = (
        '"""Months look like YYYY-MM; the run date 2031-07-04 is not a month."""\n'
        'STAMP = "2031-07-04"\n'
        'PATTERN = r"^\\d{4}-\\d{2}$"\n'
        'PLACEHOLDER = "YYYY-MM"\n'
    )
    assert month_value_problems("m.py", text) == []


def test_a_list_in_prose_does_not_hide_a_second_lone_month_beside_it() -> None:
    text = '"""Takes 2031-07,2031-09 and defaults to 2031-12."""\n'
    assert month_value_problems("m.py", text) == ["m.py:1"]


def test_a_month_outside_prose_is_reported_even_when_it_is_a_list() -> None:
    text = 'MONTHS = "2031-07,2031-09"\n'
    assert month_value_problems("m.py", text) == ["m.py:1"]
