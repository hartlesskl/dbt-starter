"""`uv run skeleton`: prove that a copy of this template with the example removed still works (CI-05).

The command copies the tracked tree to a new temporary folder outside the checkout, applies
`remove-example --apply` there with the copy's own code, commits the result in the copy, and then
runs the checks an adopter's first hour needs: `dbt deps`, `dbt parse` for DuckDB, the offline
Fabric compile and a build of the invented fixture. It stops at the first failing step, naming it,
and keeps the copy so the failure can be looked at. On success it deletes the copy unless `--keep`
is given. The checkout itself is only read: `git status --porcelain` is the same before and after.

Running the copy's code, not the checkout's, is the point. `repo_root()` finds the checkout from
the location of its own file and the editable install points at the checkout, so every command in
the copy runs with `PYTHONPATH=<copy>/src`, the virtual environment variable removed and the copy
as the working folder. The interpreter is the locked environment's, so no second install is needed.

The command is generic: it never names a data source. The list of steps grows with the later
plans of Phase 6 (the copy's gate), so it is one tuple, `STEPS`. The last step runs the copy's own
demo, which lands the placeholder source with no download and prints the closing summary.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from starter_cli.gate import DUMMY_FABRIC_ENV, fabric_adapter_installed
from starter_cli.markers import HANDLE_DIR
from starter_cli.project import repo_root
from starter_cli.remove_example import EXTRA_TARGETS, SKELETON_DIR

STEPS: tuple[str, ...] = (
    "copy",
    "remove",
    "deps",
    "parse",
    "compile-fabric",
    "fixture-build",
    "demo",
)

COPY_PREFIX = "dbt-starter-skeleton-"
COMMIT_IDENTITY = ("-c", "user.name=skeleton", "-c", "user.email=skeleton@localhost")
FABRIC_HINT = (
    "The Fabric adapter (dbt-fabricspark) is not installed, so the offline Fabric compile cannot "
    "run. Run this command with its extra: `uv run --locked --extra fabric skeleton`."
)

# The fixture build runs inside the copy, with the copy's own gate module.
FIXTURE_CODE = (
    "import sys\n"
    "from pathlib import Path\n"
    "from starter_cli import gate\n"
    "try:\n"
    "    gate.build_fixture(Path.cwd(), Path.cwd() / 'target' / 'skeleton')\n"
    "except (RuntimeError, ValueError, OSError) as error:\n"
    "    print(error, file=sys.stderr)\n"
    "    sys.exit(1)\n"
)


class SkeletonError(Exception):
    """The copy could not be made or run safely; the message says why."""


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *COMMIT_IDENTITY, *args],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def _tracked(root: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False
    ).stdout
    return [raw.decode("utf-8") for raw in listing.split(b"\0") if raw]


def make_copy(root: Path, dest: Path) -> Path:
    """Copy every tracked file of the checkout at `root` into the new folder `dest`.

    Bytes and file mode are copied, so an edit not yet committed in the checkout is in the copy.
    Untracked files (the DuckDB file, downloads, build output) are not. A tracked symlink is
    refused. The copy becomes its own git repository with one commit, because several checks read
    `git ls-files`. Returns `dest`.
    """
    root_resolved, dest_resolved = root.resolve(), dest.resolve()
    if dest_resolved == root_resolved or dest_resolved.is_relative_to(root_resolved):
        raise SkeletonError(f"{dest} is inside the checkout, so the copy would change it")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise SkeletonError(f"{dest} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    for path in _tracked(root):
        source = root / path
        if source.is_symlink():
            raise SkeletonError(f"{path} is a symlink, which the copy does not follow")
        if not source.is_file():
            continue
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        target.chmod(source.stat().st_mode & 0o777)
    for command in (
        ["init", "-q"],
        ["add", "-A", "-f"],
        ["commit", "-q", "--allow-empty", "-m", "the copy before remove-example"],
    ):
        result = _git(dest, *command)
        if result.returncode != 0:
            raise SkeletonError(f"git {command[0]} in the copy failed: {result.stderr.strip()}")
    return dest


def copy_env(copy: Path) -> dict[str, str]:
    """The environment for a command run in `copy`: the copy's code, the copy's database."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(copy / "src")
    env["DBT_DUCKDB_PATH"] = str(copy / "target" / "skeleton" / "demo.duckdb")
    return env


def run_in_copy(copy: Path, args: list[str], extra_env: dict[str, str] | None = None) -> int:
    """Run `python <args>` with the copy as the working folder and return its exit code."""
    env = {**copy_env(copy), **(extra_env or {})}
    return subprocess.run([sys.executable, *args], cwd=copy, env=env, check=False).returncode


def new_copy_folder(root: Path) -> Path:
    """Make a new empty temporary folder that is not inside the checkout at `root`."""
    folder = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
    resolved = folder.resolve()
    if resolved.is_relative_to(root.resolve()):
        folder.rmdir()
        raise SkeletonError(f"the temporary folder {resolved} is inside the checkout")
    return folder


def _dbt(*args: str) -> list[str]:
    return ["-m", "dbt.cli.main", *args, "--profiles-dir", ".", "--project-dir", "."]


def _step_copy(ctx: dict) -> int:
    folder = new_copy_folder(ctx["root"])
    ctx["copy"] = folder
    make_copy(ctx["root"], folder)
    return 0


def _step_remove(ctx: dict) -> int:
    copy: Path = ctx["copy"]
    code = run_in_copy(copy, ["-m", "starter_cli.remove_example", "--apply"])
    if code != 0:
        return code
    committed = _git(copy, "commit", "-q", "--allow-empty", "-m", "remove the example")
    if committed.returncode != 0:
        print(committed.stderr.strip(), file=sys.stderr)
        return committed.returncode
    left = [
        path
        for path in _tracked(copy)
        if HANDLE_DIR in path.split("/") or path.startswith((*EXTRA_TARGETS, f"{SKELETON_DIR}/"))
    ]
    if left:
        print("remove-example left these paths in the copy: " + ", ".join(left), file=sys.stderr)
        return 1
    return 0


def _step_deps(ctx: dict) -> int:
    return run_in_copy(ctx["copy"], _dbt("deps"))


def _step_parse(ctx: dict) -> int:
    args = _dbt("parse", "--target", "duckdb", "--no-partial-parse")
    return run_in_copy(ctx["copy"], [*args, "--target-path", "target/skeleton/parse"])


def _step_compile_fabric(ctx: dict) -> int:
    args = _dbt("compile", "--target", "fabric", "--no-populate-cache")
    return run_in_copy(
        ctx["copy"], [*args, "--target-path", "target/skeleton/fabric"], DUMMY_FABRIC_ENV
    )


def _step_fixture_build(ctx: dict) -> int:
    return run_in_copy(ctx["copy"], ["-c", FIXTURE_CODE])


def _step_demo(ctx: dict) -> int:
    """Run the copy's own `uv run demo` module: land, build every layer, print the summary."""
    return run_in_copy(ctx["copy"], ["-m", "starter_cli.demo"])


RUNNERS: dict[str, Callable[[dict], int]] = {
    "copy": _step_copy,
    "remove": _step_remove,
    "deps": _step_deps,
    "parse": _step_parse,
    "compile-fabric": _step_compile_fabric,
    "fixture-build": _step_fixture_build,
    "demo": _step_demo,
}


def _cleanup(ctx: dict) -> None:
    """Delete the copy after a pass, unless `--keep`. Only a folder this command made."""
    copy: Path | None = ctx.get("copy")
    if copy is None:
        return
    if ctx["keep"]:
        print(f"skeleton: the copy is kept at {copy}", flush=True)
        return
    resolved = copy.resolve()
    safe = (
        copy.name.startswith(COPY_PREFIX)
        and (copy / ".git").is_dir()
        and not resolved.is_relative_to(ctx["root"].resolve())
        and resolved != resolved.parent
    )
    if safe:
        shutil.rmtree(copy)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skeleton",
        description=(
            "Copy the tracked tree to a temporary folder, remove the example there with the "
            "copy's own code and check that the copy works: " + ", ".join(STEPS) + ". Stops at "
            "the first failing step and keeps the copy then."
        ),
    )
    parser.add_argument("--keep", action="store_true", help="keep the copy after a passing run")
    args = parser.parse_args(argv)

    if not fabric_adapter_installed():
        print(FABRIC_HINT, file=sys.stderr, flush=True)
        return 2

    root = repo_root()
    ctx: dict = {"root": root, "keep": args.keep, "copy": None}
    for step in STEPS:
        print(f"skeleton: {step}", flush=True)
        try:
            code = RUNNERS[step](ctx)
        except (SkeletonError, OSError) as error:
            print(f"skeleton: {error}", file=sys.stderr, flush=True)
            code = 1
        if code != 0:
            print(f"skeleton failed at: {step} (exit {code})", flush=True)
            if ctx["copy"] is not None:
                print(f"skeleton: the copy is kept at {ctx['copy']}", flush=True)
            return code
    print("skeleton: all steps passed", flush=True)
    _cleanup(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
