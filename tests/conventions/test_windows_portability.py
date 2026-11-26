"""The command-line code holds no construct that breaks on Windows (D-17, DEMO-08, T-06-49).

Windows is supported through `uv run demo` in native PowerShell, with no make or bash (D-17). A
static scan reads every tracked module of the command-line package, and of the package the
skeleton payload puts in its place, and reports each construct that fails on Windows although it
works on macOS and Linux:

- `os.rename` and `Path.rename`: Windows refuses to rename over an existing file. `os.replace`
  and `Path.replace` replace it on every system.
- Modules and calls that exist only on POSIX (`fcntl`, `pwd`, `os.getuid`, `os.fork`, and so on).
- `shell=True`, `os.system`, and a command that starts with `make`, `bash` or `sh`: PowerShell has
  none of them on its path.
- A path that starts with `/tmp/` or `/dev/`.
- Text I/O with no explicit encoding: Windows opens text with the locale code page, not UTF-8.
- A DuckDB connection that is not closed in a `finally` block or a `with` block: on Windows an
  open connection keeps the database file locked, so the next delete, replace or reopen fails.

It reads the source as data and cannot prove a run on Windows. The clean-room workflow's Windows
job does (plan 06-19); this scan removes the known causes first and holds every later change to
them. `portability_problems` returns every rule a module breaks, so each rule has a fail-first
doctored module and each allowed form has one that must report nothing.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

SCANNED_ROOTS = ("src/starter_cli", "skeleton/src/starter_cli")
POSIX_MODULES = frozenset(
    {"fcntl", "pwd", "grp", "resource", "termios", "tty", "pty", "syslog", "crypt"}
)
POSIX_OS_CALLS = frozenset(
    {
        "getuid",
        "geteuid",
        "getgid",
        "getegid",
        "fork",
        "forkpty",
        "killpg",
        "getpgid",
        "setsid",
        "symlink",
        "chown",
        "lchown",
    }
)
SHELL_COMMANDS = frozenset({"make", "bash", "sh", "zsh"})
SUBPROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
POSIX_PATH_PREFIXES = ("/tmp/", "/dev/")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _line(node: ast.AST) -> int:
    return getattr(node, "lineno", 0)


def _is_attribute_call(node: ast.AST, owner: str | None, names: frozenset[str] | set[str]) -> bool:
    """True for `owner.name(...)`, or `<anything>.name(...)` when `owner` is None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in names:
        return False
    if owner is None:
        return True
    return isinstance(node.func.value, ast.Name) and node.func.value.id == owner


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node of a scope, without descending into a function nested in it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*FUNCTIONS, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _import_problems(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        for name in names:
            if name in POSIX_MODULES:
                found.append((_line(node), f"imports {name}, a POSIX-only module"))
    return found


def _call_problems(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if _is_attribute_call(node, None, {"rename"}):
            found.append(
                (
                    _line(node),
                    "calls rename, which fails on Windows over an existing file; use replace",
                )
            )
        if _is_attribute_call(node, "os", POSIX_OS_CALLS) and isinstance(func, ast.Attribute):
            found.append((_line(node), f"calls os.{func.attr}, which is POSIX-only"))
        if _is_attribute_call(node, "os", {"system", "popen"}) and isinstance(func, ast.Attribute):
            found.append((_line(node), f"calls os.{func.attr}, which runs a shell"))
        shell = _keywords(node).get("shell")
        if isinstance(shell, ast.Constant) and shell.value is True:
            found.append((_line(node), "passes shell=True, which PowerShell does not share"))
        if _is_attribute_call(node, "subprocess", SUBPROCESS_CALLS):
            command = node.args[0] if node.args else _keywords(node).get("args")
            first = _first_word(command)
            if first in SHELL_COMMANDS:
                found.append((_line(node), f"runs {first}, which Windows does not have"))
    return found


def _first_word(command: ast.AST | None) -> str | None:
    """The program a subprocess command names, when the command is a literal."""
    if isinstance(command, (ast.List, ast.Tuple)) and command.elts:
        text = _const_str(command.elts[0])
    else:
        text = _const_str(command)
    if text is None:
        return None
    words = text.split()
    return Path(words[0]).name if words else None


def _literal_problems(tree: ast.AST) -> list[tuple[int, str]]:
    return [
        (_line(node), f"holds the POSIX path {node.value!r}")
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(POSIX_PATH_PREFIXES)
    ]


def _mode_of(call: ast.Call, position: int) -> tuple[bool, str | None]:
    """(a mode argument is present, its literal value or None when it is not a literal)."""
    node = call.args[position] if len(call.args) > position else _keywords(call).get("mode")
    if node is None:
        return False, None
    return True, _const_str(node)


def _text_io_problems(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = _keywords(node)
        has_encoding = "encoding" in keywords
        func = node.func
        is_builtin_open = isinstance(func, ast.Name) and func.id == "open"
        is_io_open = _is_attribute_call(node, "io", {"open"})
        is_path_open = (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and not (
                isinstance(func.value, ast.Name) and func.value.id in {"io", "os", "webbrowser"}
            )
        )
        if is_builtin_open or is_io_open or is_path_open:
            # open(file, mode) takes the mode second; Path.open(mode) takes it first.
            present, mode = _mode_of(node, 0 if is_path_open else 1)
            text_mode = (not present) or (mode is not None and "b" not in mode)
            # The encoding is the fourth positional of open() and the third of Path.open().
            positional_encoding = len(node.args) >= (3 if is_path_open else 4)
            if text_mode and not has_encoding and not positional_encoding:
                found.append((_line(node), "opens a file in text mode without encoding="))
        elif _is_attribute_call(node, None, {"read_text"}) and not (has_encoding or node.args):
            found.append((_line(node), "calls read_text without encoding="))
        elif _is_attribute_call(node, None, {"write_text"}) and not (
            has_encoding or len(node.args) > 1
        ):
            found.append((_line(node), "calls write_text without encoding="))
    return found


def _is_connect(node: ast.AST | None) -> bool:
    return _is_attribute_call(node, "duckdb", {"connect"}) if node is not None else False


def _closed_in_finally(scope: ast.AST, name: str) -> bool:
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Try):
            continue
        for statement in node.finalbody:
            for inner in ast.walk(statement):
                if (
                    _is_attribute_call(inner, name, {"close"})
                    and isinstance(inner, ast.Call)
                    and not inner.args
                ):
                    return True
    return False


def _connection_problems(tree: ast.AST) -> list[tuple[int, str]]:
    """A `name = duckdb.connect(...)` needs `name.close()` in a `finally` of the same scope."""
    found = []
    scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, FUNCTIONS))]
    for scope in scopes:
        for node in _scope_nodes(scope):
            if isinstance(node, ast.Assign) and _is_connect(node.value):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and _is_connect(node.value):
                targets = [node.target.id] if isinstance(node.target, ast.Name) else []
            else:
                continue
            for name in targets:
                if not _closed_in_finally(scope, name):
                    found.append(
                        (
                            _line(node),
                            f"opens a DuckDB connection {name} that no finally block closes",
                        )
                    )
    return found


def portability_problems(path: str, text: str) -> list[str]:
    """Return every Windows hazard in one module as `path:line: what`; an empty list is correct."""
    tree = ast.parse(text, filename=path)
    found = (
        _import_problems(tree)
        + _call_problems(tree)
        + _literal_problems(tree)
        + _text_io_problems(tree)
        + _connection_problems(tree)
    )
    return [f"{path}:{line}: {what}" for line, what in sorted(set(found))]


def _tracked_modules() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCANNED_ROOTS],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    return sorted(path for path in listing.split("\0") if path.endswith(".py"))


def test_the_scan_reaches_every_tracked_module_of_both_packages() -> None:
    modules = _tracked_modules()
    assert "src/starter_cli/demo.py" in modules
    assert "src/starter_cli/landing.py" in modules
    assert "skeleton/src/starter_cli/example_source/__init__.py" in modules
    assert len(modules) >= 15


@pytest.mark.parametrize("module", _tracked_modules())
def test_a_tracked_module_holds_no_windows_hazard(module: str) -> None:
    text = (REPO_ROOT / module).read_text(encoding="utf-8")
    assert portability_problems(module, text) == []


FLAGGED = {
    "os.rename": ("import os\nos.rename(a, b)\n", "calls rename"),
    "Path.rename": ("from pathlib import Path\nPath(a).rename(b)\n", "calls rename"),
    "import fcntl": ("import fcntl\n", "imports fcntl"),
    "from pwd import": ("from pwd import getpwnam\n", "imports pwd"),
    "import grp": ("import grp\n", "imports grp"),
    "import resource": ("import resource\n", "imports resource"),
    "import termios": ("import termios\n", "imports termios"),
    "os.getuid": ("import os\nx = os.getuid()\n", "os.getuid"),
    "os.geteuid": ("import os\nx = os.geteuid()\n", "os.geteuid"),
    "os.fork": ("import os\npid = os.fork()\n", "os.fork"),
    "os.killpg": ("import os\nos.killpg(1, 9)\n", "os.killpg"),
    "os.symlink": ("import os\nos.symlink(a, b)\n", "os.symlink"),
    "os.system": ("import os\nos.system('dir')\n", "os.system"),
    "shell=True": ("import subprocess\nsubprocess.run('dir', shell=True)\n", "shell=True"),
    "make": ("import subprocess\nsubprocess.run(['make', 'demo'])\n", "runs make"),
    "bash": ("import subprocess\nsubprocess.run(['bash', '-c', 'x'])\n", "runs bash"),
    "sh": ("import subprocess\nsubprocess.check_call(('sh', 'x.sh'))\n", "runs sh"),
    "a make command string": ("import subprocess\nsubprocess.run('make demo')\n", "runs make"),
    "/tmp/ path": ("p = '/tmp/work'\n", "POSIX path '/tmp/work'"),
    "/dev/ path": ("p = '/dev/null'\n", "POSIX path '/dev/null'"),
    "open for reading": ("with open(p) as h:\n    h.read()\n", "text mode without encoding"),
    "open for writing": ("with open(p, 'w') as h:\n    h.write(s)\n", "text mode without encoding"),
    "open with a mode keyword": (
        "with open(p, mode='a') as h:\n    pass\n",
        "text mode without encoding",
    ),
    "Path.open in text mode": ("with p.open('w') as h:\n    pass\n", "text mode without encoding"),
    "read_text": ("t = p.read_text()\n", "read_text without encoding"),
    "write_text": ("p.write_text(s)\n", "write_text without encoding"),
    "a connection never closed": (
        "import duckdb\ndef f():\n    con = duckdb.connect(p)\n    con.execute('x')\n",
        "no finally block closes",
    ),
    "a connection closed outside finally": (
        (
            "import duckdb\ndef f():\n    con = duckdb.connect(p)\n    con.execute('x')\n"
            "    con.close()\n"
        ),
        "no finally block closes",
    ),
    "a connection closed in another function": (
        (
            "import duckdb\ndef f():\n    con = duckdb.connect(p)\ndef g(con):\n"
            "    try:\n        pass\n    finally:\n        con.close()\n"
        ),
        "no finally block closes",
    ),
    "a module-level connection": (
        "import duckdb\ncon = duckdb.connect(p)\n",
        "no finally block closes",
    ),
}

ALLOWED = {
    "binary read": "with open(p, 'rb') as h:\n    h.read()\n",
    "binary write": "with open(p, 'wb') as h:\n    h.write(b)\n",
    "binary Path.open": "with p.open('rb') as h:\n    pass\n",
    "os.replace": "import os\nos.replace(a, b)\n",
    "Path.replace": "from pathlib import Path\nPath(a).replace(b)\n",
    "str.replace": "t = s.replace('a', 'b')\n",
    "open with encoding": "with open(p, 'w', encoding='utf-8') as h:\n    pass\n",
    "open with positional encoding": "with open(p, 'w', -1, 'utf-8') as h:\n    pass\n",
    "Path.open with encoding": "with p.open('a', encoding='utf-8') as h:\n    pass\n",
    "read_text with encoding": "t = p.read_text(encoding='utf-8')\n",
    "write_text with encoding": "p.write_text(s, encoding='utf-8')\n",
    "a connection closed in finally": (
        "import duckdb\ndef f():\n    con = duckdb.connect(p)\n    try:\n        con.execute('x')\n"
        "    finally:\n        con.close()\n"
    ),
    "a connection in a with block": (
        "import duckdb\ndef f():\n    with duckdb.connect(p) as con:\n        con.execute('x')\n"
    ),
    "a command list that starts with python": (
        "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'dbt'])\n"
    ),
    "git as a command": "import subprocess\nsubprocess.run(['git', 'status'])\n",
    "shell=False": "import subprocess\nsubprocess.run(['git'], shell=False)\n",
    "a path that only contains /tmp/": "p = 'a/tmp/b'\n",
    "a portable module": "import os, sys, pathlib\nx = os.getcwd()\n",
}


@pytest.mark.parametrize("name", list(FLAGGED))
def test_a_doctored_module_is_reported_with_its_file_line_and_rule(name: str) -> None:
    source, expected = FLAGGED[name]
    problems = portability_problems("doctored.py", source)
    assert problems, name
    assert any(expected in problem for problem in problems), (name, problems)
    assert all(problem.startswith("doctored.py:") for problem in problems)
    line = int(problems[0].split(":")[1])
    assert 1 <= line <= source.count("\n") + 1


@pytest.mark.parametrize("name", list(ALLOWED))
def test_an_allowed_form_reports_nothing(name: str) -> None:
    assert portability_problems("allowed.py", ALLOWED[name]) == []


def test_the_report_names_the_line_of_the_hazard() -> None:
    source = "import os\n\n\nx = 1\nos.rename(a, b)\n"
    assert portability_problems("m.py", source) == [
        "m.py:5: calls rename, which fails on Windows over an existing file; use replace"
    ]
