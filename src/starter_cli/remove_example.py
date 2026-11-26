"""`uv run remove-example`: take the example out of a copy of the template (ADPT-03, D-08, D-10).

With no flag the command is a dry run. It prints every file it would delete, every marker block
with its file and line range, every overlay, the README Status rewrite and advice about untracked
data, and changes nothing. `--apply` then:

1. strips every example marker block (`starter_cli.markers`) from the tracked text files;
2. deletes every tracked file under a directory named exactly the example handle, and everything
   tracked under `docs/verification/`, the dated records of the example's live runs;
3. copies the tracked `skeleton/` payload over the repository and deletes `skeleton/`;
4. rewrites the README Status section to say this copy is not verified on Fabric yet;
5. deletes any file that stripping left empty;
6. stages exactly those paths with `git add -A --`.

It never commits, so an interrupted run leaves only uncommitted changes that git restores, and a
rerun on that dirty tree refuses. It touches only tracked files. Untracked data such as the DuckDB
file and the download cache is never read, changed or deleted; the command only names it in its
advice. It refuses (exit 2, nothing changed) outside a git work tree and when `git status
--porcelain` prints anything, so the removal never mixes with the user's own edits. Two runs at
once in one checkout are not supported; the second refuses as soon as the first has changed a file.

The command is generic: it never spells the example's name, it reads the handle from
`starter_cli.markers`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from starter_cli.markers import HANDLE_DIR, MarkerError, example_blocks, strip_example_blocks
from starter_cli.project import repo_root

# Tracked paths under these prefixes go too, although the handle never reaches them.
EXTRA_TARGETS: tuple[str, ...] = ("docs/verification/",)
# Files that only the template needs: the workflow that proves this removal on every pull request,
# and its structure test. A copy has nothing to prove it against, so the removal deletes both.
TEMPLATE_ONLY_PATHS: tuple[str, ...] = (
    ".github/workflows/skeleton.yml",
    "tests/conventions/test_skeleton_workflow.py",
)
SKELETON_DIR = "skeleton"
STATUS_HEADING = "## Status"

# The Status paragraph a copy gets. It names no example and no month, because a copy's README
# describes only the copy's own build.
UNVERIFIED_STATUS = (
    "Fabric: not verified live yet in this copy. Run your own verification before you claim a "
    "Fabric run (docs/fabric-setup.md, Re-verify). Until then the offline compile and the Spark "
    "scan are the only Fabric checks."
)

# Untracked local paths worth deleting after the removal, named in the advice and never touched.
ADVICE_PATHS: tuple[str, ...] = (
    "data/dbt_starter.duckdb",
    f"data/raw/{HANDLE_DIR}/",
    "target/",
    "site/",
)


class RemoveError(Exception):
    """The tree cannot be changed safely; the message says why and nothing has been changed."""


@dataclass
class Removal:
    """What a removal would do, computed without changing anything."""

    deletes: list[str] = field(default_factory=list)
    blocks: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    overlays: list[tuple[str, str]] = field(default_factory=list)
    readme_status: bool = False
    advice: str = ""

    @property
    def empty(self) -> bool:
        return not (self.deletes or self.blocks or self.overlays or self.readme_status)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False, encoding="utf-8"
    )


def _check_tree(root: Path) -> None:
    """Raise RemoveError unless `root` is the top of a clean git work tree."""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RemoveError("not a git work tree, so nothing was changed")
    top = _git(root, "rev-parse", "--show-toplevel").stdout.strip()
    if not top or Path(top).resolve() != root.resolve():
        raise RemoveError(f"{root} is not the top of its git work tree, so nothing was changed")
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise RemoveError(f"git status failed: {status.stderr.strip()}")
    entries = [line for line in status.stdout.splitlines() if line.strip()]
    if entries:
        shown = "; ".join(entries[:3]) + (" ..." if len(entries) > 3 else "")
        raise RemoveError(
            f"uncommitted changes ({len(entries)} in `git status --porcelain`: {shown}). "
            "Commit or stash them first, so the removal does not mix with your edits"
        )


def _tracked(root: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False
    ).stdout
    return [raw.decode("utf-8") for raw in listing.split(b"\0") if raw]


def _is_handle_path(path: str) -> bool:
    return (
        HANDLE_DIR in path.split("/")
        or path.startswith(EXTRA_TARGETS)
        or path in TEMPLATE_ONLY_PATHS
    )


def _in_skeleton(path: str) -> bool:
    return path.startswith(f"{SKELETON_DIR}/")


def _read_text(path: Path) -> str | None:
    """The UTF-8 text of a file, or None when it is not a readable text file."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def rewrite_status(text: str) -> str | None:
    """Return `text` with its `## Status` body replaced, or None when there is no such section."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == STATUS_HEADING)
    except StopIteration:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out = [*lines[: start + 1], "", UNVERIFIED_STATUS]
    if end < len(lines):
        out += ["", *lines[end:]]
    return "\n".join(out) + "\n"


def _status_differs(text: str) -> bool:
    new = rewrite_status(text)
    return new is not None and new.strip() != text.strip()


def _guard(root: Path, path: str) -> None:
    """Refuse a target that is a symlink or that resolves outside `root`."""
    full = root / path
    if full.is_symlink():
        raise RemoveError(f"{path} is a symlink, so nothing was changed")
    if not full.resolve().is_relative_to(root.resolve()):
        raise RemoveError(f"{path} resolves outside the checkout, so nothing was changed")


def plan_removal(root: Path) -> Removal:
    """Work out the removal for the clean git work tree at `root`, changing nothing.

    Raises RemoveError outside a git work tree, on a dirty tree, on a broken marker block and
    when a target is a symlink or resolves outside `root`.
    """
    _check_tree(root)
    removal = Removal()
    tracked = _tracked(root)
    for path in tracked:
        full = root / path
        if _in_skeleton(path):
            if full.is_file() or full.is_symlink():
                dest = path[len(SKELETON_DIR) + 1 :]
                _guard(root, path)
                _guard(root, dest)
                removal.overlays.append((path, dest))
        elif _is_handle_path(path):
            if full.is_file() or full.is_symlink():
                _guard(root, path)
                removal.deletes.append(path)
        elif full.is_file() and not full.is_symlink():
            # A tracked symlink outside the handle is never read, so it is never stripped.
            _guard(root, path)
            text = _read_text(full)
            if text is None:
                continue
            try:
                found = example_blocks(text)
            except MarkerError as error:
                raise RemoveError(f"{path}: {error}") from error
            if found:
                removal.blocks[path] = found

    readme = root / "README.md"
    if (
        "README.md" in tracked
        and readme.is_file()
        and not readme.is_symlink()
        and "README.md" not in removal.deletes
    ):
        text = _read_text(readme)
        if text is not None:
            try:
                stripped = strip_example_blocks(text)
            except MarkerError as error:
                raise RemoveError(f"README.md: {error}") from error
            removal.readme_status = _status_differs(stripped)

    present = [name for name in ADVICE_PATHS if (root / name.rstrip("/")).exists()]
    if present:
        removal.advice = (
            "Untracked files and folders are never touched. To reclaim space you may delete "
            + ", ".join(f"`{name}`" for name in present)
            + " yourself."
        )
    return removal


def describe(removal: Removal) -> list[str]:
    """The plan as printable lines."""
    lines: list[str] = []
    if removal.deletes:
        lines.append(f"Delete {len(removal.deletes)} tracked files:")
        lines += [f"  {path}" for path in removal.deletes]
    if removal.blocks:
        count = sum(len(found) for found in removal.blocks.values())
        lines.append(f"Strip {count} example blocks:")
        for path, found in removal.blocks.items():
            lines += [f"  {path}:{first}-{last}" for first, last in found]
    if removal.overlays:
        lines.append(f"Overlay {len(removal.overlays)} files from {SKELETON_DIR}/ onto the repo:")
        lines += [f"  {source} -> {dest}" for source, dest in removal.overlays]
        lines.append(f"  then delete {SKELETON_DIR}/")
    if removal.readme_status:
        lines.append("Rewrite the README Status section to the unverified wording:")
        lines.append(f"  {UNVERIFIED_STATUS}")
    return lines


def _remove_empty_parents(root: Path, path: Path) -> None:
    """Remove the folders above `path` that are now empty, never `root` itself."""
    for parent in path.parents:
        if parent == root or root not in parent.parents:
            break
        try:
            parent.rmdir()
        except OSError:
            break


def apply_removal(root: Path, removal: Removal) -> list[str]:
    """Carry out `removal` in `root`, stage the touched paths and return them. Never commits."""
    touched: list[str] = []

    for path in removal.blocks:
        full = root / path
        text = _read_text(full)
        if text is None:
            continue
        full.write_bytes(strip_example_blocks(text).encode("utf-8"))
        touched.append(path)

    for path in removal.deletes:
        full = root / path
        full.unlink()
        _remove_empty_parents(root, full)
        touched.append(path)

    for source, dest in removal.overlays:
        source_path, dest_path = root / source, root / dest
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(source_path.read_bytes())
        dest_path.chmod(source_path.stat().st_mode & 0o777)
        source_path.unlink()
        _remove_empty_parents(root, source_path)
        touched += [source, dest]

    if removal.readme_status:
        readme = root / "README.md"
        text = _read_text(readme)
        new = rewrite_status(text) if text is not None else None
        if new is not None:
            readme.write_bytes(new.encode("utf-8"))
            if "README.md" not in touched:
                touched.append("README.md")

    unique = sorted(dict.fromkeys(touched))
    for path in unique:
        full = root / path
        text = _read_text(full) if full.is_file() else None
        if text is not None and not text.strip():
            full.unlink()
            _remove_empty_parents(root, full)

    staged = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "add",
            "-A",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
        ],
        cwd=root,
        input=b"\0".join(path.encode("utf-8") for path in unique),
        capture_output=True,
        check=False,
    )
    if staged.returncode != 0:
        raise RemoveError(f"git add failed: {staged.stderr.decode('utf-8', 'replace').strip()}")
    return unique


def run(root: Path, *, apply: bool) -> int:
    """Plan, print and (with `apply`) carry out the removal in `root`. Returns the exit code."""
    try:
        removal = plan_removal(root)
        if removal.empty:
            print("remove-example: nothing to remove")
            return 0
        print("remove-example: " + ("applying" if apply else "dry run, nothing is changed"))
        for line in describe(removal):
            print(line)
        if not apply:
            print("Run `uv run remove-example --apply` to do this. It stages the changes only.")
            if removal.advice:
                print(removal.advice)
            return 0
        touched = apply_removal(root, removal)
    except RemoveError as error:
        print(f"remove-example: {error}", file=sys.stderr)
        return 2
    print(f"remove-example: changed and staged {len(touched)} paths. Nothing is committed.")
    print(
        "Next: review `git diff --cached`, run `uv run --locked --extra fabric gate`, then commit."
    )
    if removal.advice:
        print(removal.advice)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="remove-example",
        description=(
            "Remove the example from this copy of the template. With no flag it only prints what "
            "it would do. It refuses outside a git work tree and when `git status --porcelain` "
            "prints anything. It deletes tracked files only, stages the result and never commits."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="carry out the removal and stage it (the default is a dry run)",
    )
    args = parser.parse_args(argv)
    return run(repo_root(), apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
