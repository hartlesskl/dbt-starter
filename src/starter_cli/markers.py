"""The one definition of the example marker blocks.

Every file that holds example-only text brackets it with a begin line and an end line, in the
comment form of the file: an HTML comment in Markdown, a hash comment in Python, YAML and SQL
comments, and a Jinja comment in a model. Removing the example deletes those blocks, the guide
renderer hides them, and the handle test checks them, so all three must agree on what a marker
is. This module is that single definition.

A marker is a whole line: optional indent, one comment form, nothing after it. Marker text inside
a string literal on a line of code is therefore not a marker, and a line that merely mentions a
marker in prose is not one either.

This file also defines the example handle: the directory name and the word pattern that mark
everything specific to the example. The handle test checks the tree against them, and
remove-example deletes the directories by that name. Because it holds the pattern, this is the one
file under src/ that the handle test exempts.
"""

from __future__ import annotations

import re

BEGIN = "example:begin"
END = "example:end"
HANDLE_DIR = "nyc_taxi"
HANDLE_RE = re.compile(r"taxi|tlc|nyc", re.IGNORECASE)


class MarkerError(ValueError):
    """A marker block is nested, never closed or never opened; the message names the line."""


def _marker_re(word: str) -> re.Pattern[str]:
    """Return the anchored whole-line pattern for `word` in the three comment forms."""
    return re.compile(
        r"^[ \t]*(?:"
        rf"<!--\s*{re.escape(word)}\s*-->"
        rf"|#\s*{re.escape(word)}"
        rf"|\{{#-?\s*{re.escape(word)}\s*-?#\}}"
        r")[ \t]*$"
    )


BEGIN_RE = _marker_re(BEGIN)
END_RE = _marker_re(END)


def example_blocks(text: str) -> list[tuple[int, int]]:
    """Return each block as 1-based inclusive (begin line, end line), in order.

    Raises MarkerError for a block opened inside another, a begin that is never closed and an end
    with no begin before it.
    """
    blocks: list[tuple[int, int]] = []
    begun_at = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if BEGIN_RE.match(line):
            if begun_at:
                raise MarkerError(f"line {number}: {BEGIN} inside the block opened on {begun_at}")
            begun_at = number
        elif END_RE.match(line):
            if not begun_at:
                raise MarkerError(f"line {number}: {END} with no {BEGIN} before it")
            blocks.append((begun_at, number))
            begun_at = 0
    if begun_at:
        raise MarkerError(f"line {begun_at}: {BEGIN} is never closed by {END}")
    return blocks


def strip_example_blocks(text: str) -> str:
    """Return `text` without its marker blocks, the two marker lines included.

    Runs of three or more blank lines left by a removal collapse to two, and a text that had a
    trailing newline keeps exactly one. Text with no block comes back unchanged.
    """
    blocks = example_blocks(text)
    if not blocks:
        return text
    lines = text.splitlines()
    drop = {number for begin, end in blocks for number in range(begin, end + 1)}
    kept = [line for number, line in enumerate(lines, start=1) if number not in drop]
    out: list[str] = []
    blank_run = 0
    for line in kept:
        blank_run = blank_run + 1 if not line.strip() else 0
        if blank_run <= 2:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n" if out else ""
