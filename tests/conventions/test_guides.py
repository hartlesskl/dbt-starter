"""Every guide link resolves, before and after remove-example, and the conventions guide is whole (DOCS-03).

The guides in `docs/guide/` are read on GitHub and on the Pages site, and an adopter runs
remove-example on a copy of the repository. Two things have to hold for them to stay honest:

- every relative link in a guide resolves to a tracked file, a tracked folder or a sibling guide;
- once `strip_example_blocks` has removed the marker blocks, no link points into an example
  directory or into `docs/verification/`, because those files go or stay behind as history. A link
  to an example file is fine inside a marker block and a problem outside one.

The conventions guide has one more contract (D-15): eight topic sections, in order, and in each one
a link to an ADR under `docs/decisions/` that carries the depth and a link to a file that enforces
the rule (a test, a macro, a package file or a source module). Both contracts are checked on the
text as written and on the text with the example blocks removed, so the ADR and the enforcement
link of a topic cannot live only inside an example block.

The swap-out guide has a third contract (D-14, ADPT-04): its `## ` headings equal `GUIDE_HEADINGS` of
`tests/guide/test_swap_out.py` in order, every `file=` path a fence names lies inside the paths one
new source owns, no fence calls `replace(` (the Spark scan refuses it), and the USGS credit is
present. The build of the guide's files is that test's job; this check keeps the guide's shape.

A link to a generic file that an adopter later deletes is not caught here; their own gate catches
it. The checks are pure functions over text and a tracked-path list. Each has a doctored case that
must report its problem, so a checker that finds nothing is not read as a clean guide.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import tomllib
from typing import NamedTuple

import yaml
from guide.test_swap_out import GUIDE_HEADINGS, guide_files
from markdown_it import MarkdownIt
from support.dbt_cli import REPO_ROOT

from starter_cli import markers
from starter_cli.markers import MarkerError, strip_example_blocks
from starter_cli.remove_example import UNVERIFIED_STATUS, removal_paths

GUIDE_DIR = "docs/guide"
CONVENTIONS_GUIDE = f"{GUIDE_DIR}/conventions.md"
# The eight topics of DOCS-03, in the order the guide gives them.
CONVENTION_TOPICS = (
    "Naming",
    "Layers",
    "Schemas",
    "Typing",
    "Landing metadata",
    "The quarantine design",
    "Test placement",
    "Portable SQL",
)
DECISIONS_PREFIX = "docs/decisions/"
ENFORCER_PREFIXES = ("tests/", "macros/", "packages/", "src/")
# A link into one of these goes away or stays behind as history once the example is removed.
EXAMPLE_ONLY_PREFIXES = ("docs/verification/",)


class Link(NamedTuple):
    """A link target as written in a guide and its 1-based source line."""

    target: str
    line: int


def _parse(text: str) -> list:
    return MarkdownIt("commonmark").enable("table").parse(text)


def guide_links(text: str) -> list[Link]:
    """Return every link of a Markdown page, in order, with the line each one is written on.

    Links come from the parser's `link_open` tokens, so a link inside a fenced block or an inline
    code span is not a link, and a reference-style link is read through to its target.
    """
    links: list[Link] = []
    for token in _parse(text):
        if token.type != "inline" or not token.children or not token.map:
            continue
        line = token.map[0] + 1
        for child in token.children:
            if child.type in ("softbreak", "hardbreak"):
                line += 1
            elif child.type == "link_open":
                links.append(Link(str(child.attrGet("href") or ""), line))
    return links


def guide_h2(text: str) -> list[tuple[str, int]]:
    """Return each `## ` heading as (title, 1-based line), outside fenced code."""
    headings: list[tuple[str, int]] = []
    tokens = _parse(text)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2" and token.map:
            headings.append((tokens[index + 1].content.strip(), token.map[0] + 1))
    return headings


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def resolve_target(target: str, tracked: set[str]) -> str | None:
    """Return the repository path a guide link points at, or None when it does not resolve.

    External links and `#anchor` links are not repository paths and also return None, so a caller
    that needs to tell them from a broken link checks for them first.
    """
    path = target.split("#", 1)[0]
    if not path or _is_external(target):
        return None
    joined = posixpath.normpath(posixpath.join(GUIDE_DIR, path))
    if joined == ".." or joined.startswith("../"):
        return None
    if joined in tracked:
        return joined
    if path.endswith("/") and any(item.startswith(joined + "/") for item in tracked):
        return joined + "/"
    return None


def _checkable(target: str) -> bool:
    return bool(target.split("#", 1)[0]) and not _is_external(target)


def _resolution_problems(path: str, text: str, tracked: set[str]) -> list[str]:
    return [
        f"{path}:{link.line}: link {link.target!r} does not resolve to a tracked file or folder"
        for link in guide_links(text)
        if _checkable(link.target) and resolve_target(link.target, tracked) is None
    ]


def _is_example_only(resolved: str) -> bool:
    parts = resolved.split("/")
    return markers.HANDLE_DIR in parts or resolved.startswith(EXAMPLE_ONLY_PREFIXES)


def link_problems(path: str, text: str, tracked: set[str]) -> list[str]:
    """Return every link of `text` that does not resolve, or that would not survive remove-example.

    The links are resolved against `docs/guide/` on the text as written. Then the marker blocks are
    removed and every link that remains must not point into an example directory or into
    `docs/verification/`. A text whose markers are broken is reported, since removal could not run.
    """
    problems = _resolution_problems(path, text, tracked)
    try:
        stripped = strip_example_blocks(text)
    except MarkerError as error:
        return [*problems, f"{path}: the example markers are broken: {error}"]
    for link in guide_links(stripped):
        resolved = resolve_target(link.target, tracked) if _checkable(link.target) else None
        if resolved is not None and _is_example_only(resolved):
            problems.append(
                f"{path}: link {link.target!r} points at example-only {resolved} outside the "
                "example markers, so it breaks after remove-example"
            )
    return problems


def _topic_links(text: str, tracked: set[str]) -> dict[str, list[str]]:
    """Map each `## ` heading to the repository paths its section links, in order."""
    headings = guide_h2(text)
    by_topic: dict[str, list[str]] = {title: [] for title, _ in headings}
    for link in guide_links(text):
        owners = [title for title, line in headings if line <= link.line]
        resolved = resolve_target(link.target, tracked) if _checkable(link.target) else None
        if owners and resolved is not None:
            by_topic[owners[-1]].append(resolved)
    return by_topic


def _contract_problems(path: str, text: str, tracked: set[str]) -> list[str]:
    problems: list[str] = []
    titles = [title for title, _ in guide_h2(text)]
    for topic in CONVENTION_TOPICS:
        count = titles.count(topic)
        if count == 0:
            problems.append(f"{path}: the topic section '## {topic}' is missing")
        elif count > 1:
            problems.append(f"{path}: the topic section '## {topic}' appears {count} times")
    for title in titles:
        if title not in CONVENTION_TOPICS:
            problems.append(f"{path}: '## {title}' is not one of the eight convention topics")
    present = [title for title in titles if title in CONVENTION_TOPICS]
    if sorted(set(present), key=CONVENTION_TOPICS.index) != list(dict.fromkeys(present)):
        problems.append(f"{path}: the topic sections are out of order: {present}")
    linked = _topic_links(text, tracked)
    for topic in CONVENTION_TOPICS:
        if topic not in linked:
            continue
        targets = linked[topic]
        if not any(item.startswith(DECISIONS_PREFIX) for item in targets):
            problems.append(f"{path}: '## {topic}' links no ADR under {DECISIONS_PREFIX}")
        if not any(item.startswith(ENFORCER_PREFIXES) for item in targets):
            problems.append(
                f"{path}: '## {topic}' links no enforcing file under {', '.join(ENFORCER_PREFIXES)}"
            )
    return problems


def check_conventions(text: str, tracked: set[str], path: str = CONVENTIONS_GUIDE) -> list[str]:
    """Return every way the conventions guide misses its contract (DOCS-03, D-15).

    The eight topic headings appear exactly once, in order, and no other `## ` heading does. Each
    topic section links at least one ADR and at least one enforcing file that resolve. The same
    contract is held again on the text with its example blocks removed.
    """
    problems = _contract_problems(path, text, tracked)
    try:
        stripped = strip_example_blocks(text)
    except MarkerError:
        return problems  # link_problems reports the broken markers
    if stripped != text:
        problems += [
            f"after remove-example: {problem}"
            for problem in _contract_problems(path, stripped, tracked)
            if problem not in problems
        ]
    return problems


# ---------------------------------------------------------------------------------------------
# The real tree


def _tracked() -> set[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    return {raw.decode() for raw in listing.split(b"\0") if raw}


def _tracked_guides(tracked: set[str]) -> list[str]:
    return sorted(
        item
        for item in tracked
        if item.startswith(f"{GUIDE_DIR}/") and item.endswith(".md") and "/" not in item[11:]
    )


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_every_link_in_every_guide_resolves_before_and_after_removal() -> None:
    tracked = _tracked()
    guides = _tracked_guides(tracked)
    assert CONVENTIONS_GUIDE in guides, f"{CONVENTIONS_GUIDE} is not tracked, so it is not checked"
    assert f"{GUIDE_DIR}/index.md" in guides
    problems = [
        problem for guide in guides for problem in link_problems(guide, _read(guide), tracked)
    ]
    assert problems == []


def test_the_guide_links_are_found_so_the_link_check_is_not_vacuous() -> None:
    tracked = _tracked()
    found = [link for guide in _tracked_guides(tracked) for link in guide_links(_read(guide))]
    assert len(found) >= 20, f"only {len(found)} links found across the guides"
    assert any(_checkable(link.target) for link in found)


def test_the_conventions_guide_has_the_eight_topics_each_with_an_adr_and_an_enforcer() -> None:
    tracked = _tracked()
    assert CONVENTIONS_GUIDE in tracked, f"{CONVENTIONS_GUIDE} is not tracked"
    assert check_conventions(_read(CONVENTIONS_GUIDE), tracked) == []


def test_the_conventions_guide_is_linked_from_the_guide_index() -> None:
    tracked = _tracked()
    targets = [link.target for link in guide_links(_read(f"{GUIDE_DIR}/index.md"))]
    assert "conventions.md" in targets
    assert resolve_target("conventions.md", tracked) == CONVENTIONS_GUIDE


def test_the_eight_topics_are_the_ones_the_requirement_names() -> None:
    assert CONVENTION_TOPICS == (
        "Naming",
        "Layers",
        "Schemas",
        "Typing",
        "Landing metadata",
        "The quarantine design",
        "Test placement",
        "Portable SQL",
    )


# ---------------------------------------------------------------------------------------------
# The checker proves itself: a small valid tree, then one broken rule at a time.

_BEGIN = f"<!-- {markers.BEGIN} -->"
_END = f"<!-- {markers.END} -->"
_EXAMPLE_FILE = f"models/gold/{markers.HANDLE_DIR}/gold__fct_x.sql"
_ADR = "docs/decisions/0001-x.md"
_CHECK = "tests/conventions/test_x.py"
_TRACKED = {
    f"{GUIDE_DIR}/index.md",
    f"{GUIDE_DIR}/conventions.md",
    f"{GUIDE_DIR}/other.md",
    "docs/data-terms.md",
    "docs/verification/record.md",
    _ADR,
    _CHECK,
    _EXAMPLE_FILE,
    "macros/config/m.sql",
    "packages/starter/macros/dq/rules.sql",
    "src/starter_cli/landing.py",
}


def _topic(name: str, adr: bool = True, enforcer: bool = True) -> str:
    links = []
    if adr:
        links.append("[ADR](../decisions/0001-x.md)")
    if enforcer:
        links.append("[test](../../tests/conventions/test_x.py)")
    return f"## {name}\n\nThe rule. {' '.join(links)}\n\n"


def _guide(**overrides: str) -> str:
    body = "# Conventions\n\nIntro.\n\n"
    for name in CONVENTION_TOPICS:
        body += overrides.get(name, _topic(name))
    return body


def test_the_valid_guide_used_by_the_doctored_cases_passes() -> None:
    text = _guide()
    assert check_conventions(text, _TRACKED) == []
    assert link_problems(CONVENTIONS_GUIDE, text, _TRACKED) == []


def test_links_are_read_from_the_parser_with_their_lines() -> None:
    text = (
        "# T\n\nSee [one](a.md) and\n[two](b.md#x).\n\n"
        "`[code](c.md)` and\n\n```\n[fenced](d.md)\n```\n\n[ref][r]\n\n[r]: e.md\n"
    )
    assert guide_links(text) == [
        Link("a.md", 3),
        Link("b.md#x", 4),
        Link("e.md", 12),
    ]


def test_a_missing_topic_heading_is_reported_by_name() -> None:
    text = _guide().replace("## Typing", "## Types")
    problems = check_conventions(text, _TRACKED)
    assert any("'## Typing' is missing" in p for p in problems)
    assert any("'## Types' is not one of the eight" in p for p in problems)


def test_a_repeated_topic_heading_is_reported() -> None:
    text = _guide() + _topic("Naming")
    assert any("'## Naming' appears 2 times" in p for p in check_conventions(text, _TRACKED))


def test_topics_out_of_order_are_reported() -> None:
    parts = [_topic(name) for name in CONVENTION_TOPICS]
    parts[0], parts[1] = parts[1], parts[0]
    text = "# Conventions\n\n" + "".join(parts)
    assert any("out of order" in p for p in check_conventions(text, _TRACKED))


def test_a_topic_without_an_adr_link_is_reported_by_name() -> None:
    text = _guide(Schemas=_topic("Schemas", adr=False))
    problems = check_conventions(text, _TRACKED)
    assert problems == [f"{CONVENTIONS_GUIDE}: '## Schemas' links no ADR under {DECISIONS_PREFIX}"]


def test_a_topic_without_an_enforcing_link_is_reported_by_name() -> None:
    text = _guide(Typing=_topic("Typing", enforcer=False))
    problems = check_conventions(text, _TRACKED)
    assert len(problems) == 1
    assert "'## Typing' links no enforcing file" in problems[0]


def test_an_adr_link_to_a_missing_file_does_not_count() -> None:
    topic = "## Layers\n\nRule. [ADR](../decisions/0099-gone.md) [t](../../tests/conventions/test_x.py)\n\n"
    problems = check_conventions(_guide(Layers=topic), _TRACKED)
    assert any("'## Layers' links no ADR" in p for p in problems)


def test_a_topic_whose_only_adr_link_sits_in_an_example_block_is_reported_after_removal() -> None:
    topic = (
        f"## Naming\n\nRule. [t](../../tests/conventions/test_x.py)\n\n{_BEGIN}\n"
        f"[ADR](../decisions/0001-x.md)\n{_END}\n\n"
    )
    problems = check_conventions(_guide(Naming=topic), _TRACKED)
    assert problems == [
        (
            f"after remove-example: {CONVENTIONS_GUIDE}: '## Naming' links no ADR under "
            f"{DECISIONS_PREFIX}"
        )
    ]


def test_a_link_to_a_missing_file_is_reported_with_its_line() -> None:
    text = "# G\n\nSee [gone](../../tests/gone.py) here.\n"
    problems = link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED)
    assert len(problems) == 1
    assert problems[0].startswith(f"{GUIDE_DIR}/other.md:3:")
    assert "../../tests/gone.py" in problems[0]


def test_a_link_that_climbs_out_of_the_repository_is_reported() -> None:
    text = "[up](../../../etc/passwd)\n"
    assert len(link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED)) == 1


def test_an_example_only_link_outside_the_markers_is_reported() -> None:
    text = "See [the model](../../models/gold/" + markers.HANDLE_DIR + "/gold__fct_x.sql).\n"
    problems = link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED)
    assert len(problems) == 1
    assert "example-only" in problems[0]


def test_a_link_into_the_verification_record_outside_the_markers_is_reported() -> None:
    text = "See [the record](../verification/record.md).\n"
    problems = link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED)
    assert len(problems) == 1
    assert "docs/verification/record.md" in problems[0]


def test_an_example_only_link_inside_the_markers_is_not_reported() -> None:
    text = (
        f"Before.\n\n{_BEGIN}\nSee [the model](../../models/gold/{markers.HANDLE_DIR}/gold__fct_x.sql)"
        f" and [record](../verification/record.md).\n{_END}\n\nAfter.\n"
    )
    assert link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED) == []


def test_a_broken_marker_is_reported_instead_of_hiding_a_link() -> None:
    text = f"{_BEGIN}\nSee [x](../verification/record.md).\n"
    problems = link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED)
    assert len(problems) == 1
    assert "markers are broken" in problems[0]


def test_external_and_anchor_links_and_sibling_links_with_anchors_resolve() -> None:
    text = (
        "[web](https://example.org/a) [here](#top) [sib](conventions.md#naming) "
        "[doc](../data-terms.md) [dir](../../packages/starter/) [file](../../macros/config/m.sql)\n"
    )
    assert link_problems(f"{GUIDE_DIR}/other.md", text, _TRACKED) == []


def test_a_folder_link_needs_a_trailing_slash_and_a_tracked_file_inside() -> None:
    assert resolve_target("../../packages/starter/", _TRACKED) == "packages/starter/"
    assert resolve_target("../../packages/starter", _TRACKED) is None
    assert resolve_target("../../packages/none/", _TRACKED) is None


# ---------------------------------------------------------------------------------------------
# The README and the kept workflow files after remove-example (ADPT-03, D-09)
#
# A copy keeps README.md, nightly.yml and pages.yml, so what they say about the build must hold
# for the copy's own sources. Two checks read the README with its example blocks stripped:
# no empty section or dangling quoted reference, and no mention of the example or of one of its
# months. The raw README holds the example inside markers, so only the stripped text is checked.

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{2,3}) +(\S.*?) *$")
_SEE_RE = re.compile(r"\bsee \"([^\"\n]+)\"", re.IGNORECASE)


def _headings(text: str) -> list[tuple[int, int, str]]:
    """Each `##` or `###` heading outside a code fence as (line index, level, title)."""
    found: list[tuple[int, int, str]] = []
    fenced = False
    for index, line in enumerate(text.splitlines()):
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            match = _HEADING_RE.match(line)
            if match:
                found.append((index, len(match.group(1)), match.group(2)))
    return found


def check_readme_after_removal(text: str) -> list[str]:
    """Report a section with an empty body and a quoted `see "X"` whose heading is gone.

    A section's body runs to the next heading of the same or a higher level, so a `##` section
    that holds only `###` subsections is not empty. Run it on the README with the example blocks
    stripped.
    """
    lines = text.splitlines()
    headings = _headings(text)
    problems: list[str] = []
    for position, (index, level, title) in enumerate(headings):
        end = next(
            (later for later, other_level, _ in headings[position + 1 :] if other_level <= level),
            len(lines),
        )
        end = min(end, len(lines))
        if not any(line.strip() for line in lines[index + 1 : end]):
            problems.append(f"line {index + 1}: the section `{title}` is empty")
    titles = {title for _, _, title in headings}
    fenced = False
    for number, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            for match in _SEE_RE.finditer(line):
                if match.group(1) not in titles:
                    problems.append(
                        f'line {number}: refers to the section "{match.group(1)}", which is gone'
                    )
    return problems


# The bare word `example` (not part of remove-example, example_source, an `example:` marker or the
# phrase `for example`), or a month `20YY-MM` that is not the start of a full date.
EXAMPLE_MENTION_RE = re.compile(
    r"(?<![-\w])(?<!for )example(?![-\w:])|\b20[0-9]{2}-(0[1-9]|1[0-2])\b(?!-[0-9])",
    re.IGNORECASE,
)
# The workflow files a copy keeps; both describe the build of every registered source.
KEPT_WORKFLOWS = (".github/workflows/nightly.yml", ".github/workflows/pages.yml")


def example_mentions(text: str) -> list[str]:
    """Each line of `text` that names the example as a word or a `20YY-MM` month, stripped."""
    return [line.strip() for line in text.splitlines() if EXAMPLE_MENTION_RE.search(line)]


def test_the_readme_has_no_empty_section_or_dangling_reference_after_removal() -> None:
    raw = _read("README.md")
    assert check_readme_after_removal(strip_example_blocks(raw)) == []
    assert check_readme_after_removal(raw) == []


def test_the_stripped_readme_names_the_example_only_inside_markers() -> None:
    raw = _read("README.md")
    # example:begin
    assert example_mentions(raw), "the template README names the example, so the check has input"
    # example:end
    assert example_mentions(strip_example_blocks(raw)) == []
    assert example_mentions(UNVERIFIED_STATUS) == []


def test_the_kept_workflow_files_describe_the_build_generically() -> None:
    for path in KEPT_WORKFLOWS:
        assert example_mentions(_read(path)) == [], path


def test_the_readme_has_the_make_it_your_own_and_nightly_sections() -> None:
    headings = [title for _, level, title in _headings(strip_example_blocks(_read("README.md")))]
    assert "Make it your own" in headings
    assert "Run the demo nightly" in headings
    assert "Run the example nightly" not in headings


def test_an_empty_section_is_reported_with_its_heading() -> None:
    text = "# T\n\n## Kept\n\nBody.\n\n## Gone\n\n## Next\n\nBody.\n"
    assert check_readme_after_removal(text) == ["line 7: the section `Gone` is empty"]


def test_a_section_holding_only_subsections_is_not_empty() -> None:
    text = "## Top\n\n### Sub\n\nBody.\n"
    assert check_readme_after_removal(text) == []


def test_an_empty_subsection_at_the_end_of_the_text_is_reported() -> None:
    text = "## Top\n\nBody.\n\n### Sub\n"
    assert check_readme_after_removal(text) == ["line 5: the section `Sub` is empty"]


def test_a_hash_line_inside_a_code_fence_is_not_a_heading() -> None:
    text = "## Top\n\n```bash\n## not a heading\n```\n"
    assert check_readme_after_removal(text) == []


def test_a_quoted_reference_to_a_missing_heading_is_reported_in_any_letter_case() -> None:
    text = '## A\n\nBody, see "Gone" below. See "A" is fine, and SEE "Also gone".\n'
    assert check_readme_after_removal(text) == [
        'line 3: refers to the section "Gone", which is gone',
        'line 3: refers to the section "Also gone", which is gone',
    ]


def test_a_quoted_reference_to_a_remaining_heading_is_not_reported() -> None:
    assert check_readme_after_removal('## A\n\n### B c\n\nBody, see "B c" and see "A".\n') == []


def test_a_reference_to_a_section_that_only_sat_in_a_block_is_reported_after_stripping() -> None:
    text = f'## Run\n\nSee "Details".\n\n{_BEGIN}\n## Details\n\nBody.\n{_END}\n'
    assert check_readme_after_removal(text) == []
    assert check_readme_after_removal(strip_example_blocks(text)) == [
        'line 3: refers to the section "Details", which is gone'
    ]


def test_a_workflow_line_that_builds_the_example_is_reported() -> None:
    text = "# Builds the pinned months of the example data on DuckDB every night.\nname: x\n"
    assert example_mentions(text) == [
        "# Builds the pinned months of the example data on DuckDB every night."
    ]


def test_a_line_naming_the_pinned_months_is_reported() -> None:
    text = "The site is built from the pinned months 2024-01 and 2025-01.\nOther line.\n"
    assert example_mentions(text) == [
        "The site is built from the pinned months 2024-01 and 2025-01."
    ]


def test_a_months_assignment_outside_markers_is_reported() -> None:
    assert example_mentions("make demo MONTHS=2024-01..2024-06\n") == [
        "make demo MONTHS=2024-01..2024-06"
    ]


def test_a_heading_naming_the_example_is_reported() -> None:
    assert example_mentions("## Run the example nightly\n") == ["## Run the example nightly"]


def test_the_same_lines_inside_an_example_block_are_not_reported_once_stripped() -> None:
    body = (
        "## Run the example nightly\n"
        "Builds the pinned months 2024-01 and 2025-01 of the example data.\n"
        "make demo MONTHS=2024-01..2024-06\n"
    )
    text = f"## Kept\n\nGeneric.\n\n{_BEGIN}\n{body}{_END}\n"
    assert example_mentions(text)
    assert example_mentions(strip_example_blocks(text)) == []


def test_the_allowed_forms_are_not_reported() -> None:
    text = (
        "Run `uv run remove-example` to see the plan.\n"
        "Rename example_source to your own source.\n"
        "Wrap it in an example:begin marker.\n"
        "For example, pass a month.\n"
        "Use a list `YYYY-MM,YYYY-MM` or a range `YYYY-MM..YYYY-MM`.\n"
        "Verified on Fabric 2026-11-25, see docs/verification/fabric-2026-11-25.md.\n"
        "Measured on 2026-11-23 (UTC).\n"
    )
    assert example_mentions(text) == []


# ---------------------------------------------------------------------------------------------
# The swap-out guide's shape (ADPT-04, D-14)
#
# `tests/guide/test_swap_out.py` builds the guide's files. This check holds what that test cannot:
# the headings stay the ones the guide is known by, a guide cannot ask the reader to write outside
# the paths one new source owns, and the one function the Spark scan refuses never appears in a
# fence a reader copies.

SWAP_OUT_GUIDE = f"{GUIDE_DIR}/swap-out.md"
SWAP_OUT_CREDIT = "U.S. Geological Survey"
SOURCE_PACKAGE_RE = re.compile(r"^src/starter_cli/([a-z][a-z0-9_]*)/[^/]+$")
LAYERS = ("landing", "bronze", "silver", "gold")
SHARED_PATHS = ("macros/dq/dq_entities.sql",)
REPLACE_CALL_RE = re.compile(r"\breplace\s*\(", re.IGNORECASE)


def _owned(path: str, source: str) -> bool:
    """True when `path` is one a new source named `source` owns, or the shared entity list."""
    if path in SHARED_PATHS:
        return True
    prefixes = (
        f"src/starter_cli/{source}/",
        f"macros/rules/{source}/",
        *(f"models/{layer}/{source}/" for layer in LAYERS),
    )
    return any(path.startswith(prefix) and "/" not in path[len(prefix) :] for prefix in prefixes)


def check_swap_out(text: str, path: str = SWAP_OUT_GUIDE) -> list[str]:
    """Return every way the swap-out guide misses its shape (ADPT-04, D-14).

    - its `## ` headings are exactly `GUIDE_HEADINGS`, in order;
    - every `file=` path is safe (see `guide_files`) and inside the paths one new source owns:
      `src/starter_cli/<source>/`, `models/<layer>/<source>/`, `macros/rules/<source>/` and
      `macros/dq/dq_entities.sql`, where `<source>` is the package the guide's own module creates;
    - no fence, illustrative or not, calls `replace(`;
    - the credit `U.S. Geological Survey` is present.
    """
    problems: list[str] = []
    titles = tuple(title for title, _ in guide_h2(text))
    for title in GUIDE_HEADINGS:
        if title not in titles:
            problems.append(f"{path}: the heading '## {title}' is missing")
    for title in titles:
        if title not in GUIDE_HEADINGS:
            problems.append(f"{path}: '## {title}' is not one of the guide's headings")
    present = [title for title in titles if title in GUIDE_HEADINGS]
    if present != sorted(set(present), key=GUIDE_HEADINGS.index) or len(present) != len(
        set(present)
    ):
        problems.append(f"{path}: the headings are out of order or repeated: {present}")

    try:
        files = guide_files(text)
    except ValueError as error:
        problems.append(f"{path}: {error}")
        files = {}
    sources = {
        match.group(1) for item in files if (match := SOURCE_PACKAGE_RE.match(item)) is not None
    }
    if len(sources) != 1:
        problems.append(
            f"{path}: the guide must create exactly one source package, found {sorted(sources)}"
        )
    else:
        (source,) = sources
        problems += [
            f"{path}: file={item} is outside the paths a new source owns"
            for item in files
            if not _owned(item, source)
        ]

    for token in _parse(text):
        if token.type == "fence" and REPLACE_CALL_RE.search(token.content):
            line = token.map[0] + 1 if token.map else 0
            problems.append(
                f"{path}:{line}: a fence calls replace(, which the Spark scan refuses; use translate"
            )
    if SWAP_OUT_CREDIT not in text:
        problems.append(f"{path}: the credit '{SWAP_OUT_CREDIT}' is missing")
    return problems


def test_the_real_swap_out_guide_passes_check_swap_out() -> None:
    assert SWAP_OUT_GUIDE in _tracked(), f"{SWAP_OUT_GUIDE} is not tracked"
    text = _read(SWAP_OUT_GUIDE)
    assert tuple(title for title, _ in guide_h2(text)) == GUIDE_HEADINGS
    assert guide_files(text), "the guide names no file, so the shape check would pass on nothing"
    assert check_swap_out(text) == []


def test_the_swap_out_guide_is_linked_from_the_guide_index() -> None:
    targets = [link.target for link in guide_links(_read(f"{GUIDE_DIR}/index.md"))]
    assert "swap-out.md" in targets
    assert resolve_target("swap-out.md", _tracked()) == SWAP_OUT_GUIDE


def _swap_out_text() -> str:
    return _read(SWAP_OUT_GUIDE)


def test_check_swap_out_reports_a_missing_heading() -> None:
    text = _swap_out_text().replace("## 4. Write the rules\n", "")
    assert any("'## 4. Write the rules' is missing" in p for p in check_swap_out(text))


def test_check_swap_out_reports_headings_out_of_order() -> None:
    first, second = "## 1. Describe the source\n", "## 2. Declare the landing table\n"
    text = _swap_out_text()
    swapped = text.replace(first, "@@A@@").replace(second, first).replace("@@A@@", second)
    assert swapped != text
    assert any("out of order" in p for p in check_swap_out(swapped))


def test_check_swap_out_reports_a_file_path_outside_the_source() -> None:
    extra = "\n```text file=pyproject.toml\nx\n```\n"
    other = "\n```sql file=models/silver/other/silver__other.sql\nx\n```\n"
    assert any("file=pyproject.toml" in p for p in check_swap_out(_swap_out_text() + extra))
    assert any("silver__other.sql" in p for p in check_swap_out(_swap_out_text() + other))


def test_check_swap_out_reports_an_unsafe_path() -> None:
    for path in ("/etc/passwd", "../outside.sql"):
        problems = check_swap_out(_swap_out_text() + f"\n```text file={path}\nx\n```\n")
        assert any(path in p for p in problems), path


def test_check_swap_out_reports_replace_in_a_fence() -> None:
    sql = "\n```sql\nselect replace(time, 'Z', '') from t\n```\n"
    assert any("replace(" in p for p in check_swap_out(_swap_out_text() + sql))
    bare = "\n```sql\nselect REPLACE (time, 'Z', '') from t\n```\n"
    assert any("replace(" in p for p in check_swap_out(_swap_out_text() + bare))


def test_check_swap_out_does_not_report_the_word_replace_in_prose() -> None:
    assert check_swap_out(_swap_out_text() + "\nThe scan does not allow `replace`.\n") == []


def test_check_swap_out_reports_a_missing_credit() -> None:
    text = _swap_out_text().replace(SWAP_OUT_CREDIT, "USGS")
    assert any("credit" in p for p in check_swap_out(text))


# ---------------------------------------------------------------------------------------------
# The rename checklist's Keep and Replace lists (ADPT-05)
#
# `check_rename_checklist` holds the two lists to the files a copy really holds: every path of the
# copy (the tracked set after remove-example, `removal_paths`) is covered by exactly one list item.
# Paths and items compare as exact, case-sensitive strings with no normalisation, and the problems
# come back sorted, so the output is the same on every run.

RENAME_CHECKLIST = f"{GUIDE_DIR}/rename-checklist.md"
CHECKLIST_LISTS = ("Keep", "Replace")


def checklist_items(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return the backticked path that starts each list item under `## Keep` and `## Replace`.

    The result maps each list name to its items in order, and a second list holds the text of
    every item that does not start with a backticked path. Only the first paragraph of an item is
    read, so a note after the path may hold anything.
    """
    lists: dict[str, list[str]] = {name: [] for name in CHECKLIST_LISTS}
    odd: list[str] = []
    tokens = _parse(text)
    section = ""
    waiting = False
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2":
            section = tokens[index + 1].content.strip()
            waiting = False
        elif token.type == "list_item_open" and section in lists:
            waiting = True
        elif token.type == "inline" and waiting and section in lists:
            waiting = False
            first = token.children[0] if token.children else None
            if first is not None and first.type == "code_inline":
                lists[section].append(first.content)
            else:
                odd.append(f"{section}: {token.content[:40]!r}")
    return lists, odd


def _covers(item: str, path: str) -> bool:
    return item == path or (item.endswith("/") and path.startswith(item))


def check_rename_checklist(
    text: str, paths: set[str] | frozenset[str] | list[str], path: str = RENAME_CHECKLIST
) -> list[str]:
    """Return every way the Keep and Replace lists miss the files of a copy, sorted (ADPT-05).

    Each of `paths` must be covered by exactly one list item, the longest item that equals it or
    is a directory (`.../`) holding it. Reported: a path no item covers, an item listed twice or
    in both lists, an item that is the longest cover of no path, an item that does not start with
    a backticked path, and an empty or missing list.
    """
    lists, odd = checklist_items(text)
    problems = [
        f"{path}: a list item does not start with a backticked path: {item}" for item in odd
    ]
    for name in CHECKLIST_LISTS:
        if not lists[name]:
            problems.append(f"{path}: the '## {name}' list is empty or the heading is missing")
        for item in sorted({i for i in lists[name] if lists[name].count(i) > 1}):
            problems.append(f"{path}: `{item}` is listed twice in '## {name}'")
    owners: dict[str, set[str]] = {}
    for name in CHECKLIST_LISTS:
        for item in lists[name]:
            owners.setdefault(item, set()).add(name)
    for item in sorted(owners):
        if len(owners[item]) > 1:
            problems.append(f"{path}: `{item}` is in both '## Keep' and '## Replace'")
    used: set[str] = set()
    for target in sorted(paths):
        covering = [item for item in owners if _covers(item, target)]
        if not covering:
            problems.append(f"{path}: `{target}` is covered by no Keep or Replace item")
        else:
            used.add(max(covering, key=len))
    for item in sorted(owners):
        if item not in used:
            problems.append(f"{path}: `{item}` covers no file of the copy")
    return sorted(set(problems))


def _the_copy_paths() -> set[str]:
    return removal_paths(_tracked())


def test_the_real_rename_checklist_covers_every_copy_file_once() -> None:
    assert RENAME_CHECKLIST in _tracked(), f"{RENAME_CHECKLIST} is not tracked"
    text = strip_example_blocks(_read(RENAME_CHECKLIST))
    lists, odd = checklist_items(text)
    assert odd == []
    assert all(lists[name] for name in CHECKLIST_LISTS), lists
    paths = _the_copy_paths()
    assert len(paths) > 100, "the copy's file set is implausibly small, so the check is vacuous"
    assert check_rename_checklist(text, paths) == []


_COPY = {"a/one.txt", "a/two.txt", "b.txt", "Docs/Case.md"}


def _lists(keep: str, replace: str) -> str:
    return f"# Rename checklist\n\nIntro.\n\n## Keep\n\n{keep}\n\n## Replace\n\n{replace}\n"


_VALID = _lists("- `a/`: kept\n- `b.txt`", "- `Docs/Case.md`: edit")


def test_check_rename_checklist_passes_a_valid_pair_of_lists() -> None:
    assert check_rename_checklist(_VALID, _COPY) == []


def test_check_rename_checklist_reports_an_uncovered_file() -> None:
    problems = check_rename_checklist(_VALID, _COPY | {"c/three.txt", "A/upper.txt"})
    assert problems == [
        f"{RENAME_CHECKLIST}: `A/upper.txt` is covered by no Keep or Replace item",
        f"{RENAME_CHECKLIST}: `c/three.txt` is covered by no Keep or Replace item",
    ]


def test_check_rename_checklist_compares_paths_as_exact_case_sensitive_strings() -> None:
    lower = _lists("- `a/`\n- `b.txt`", "- `docs/case.md`")
    problems = check_rename_checklist(lower, _COPY)
    assert f"{RENAME_CHECKLIST}: `Docs/Case.md` is covered by no Keep or Replace item" in problems
    assert f"{RENAME_CHECKLIST}: `docs/case.md` covers no file of the copy" in problems
    dotted = _lists("- `./a/`\n- `b.txt`", "- `Docs/Case.md`")
    assert any("`./a/` covers no file" in p for p in check_rename_checklist(dotted, _COPY))


def test_check_rename_checklist_reports_a_file_in_both_lists() -> None:
    both = _lists("- `a/`\n- `b.txt`\n- `Docs/Case.md`", "- `Docs/Case.md`")
    assert check_rename_checklist(both, _COPY) == [
        f"{RENAME_CHECKLIST}: `Docs/Case.md` is in both '## Keep' and '## Replace'"
    ]
    twice = _lists("- `a/`\n- `b.txt`\n- `b.txt`", "- `Docs/Case.md`")
    assert check_rename_checklist(twice, _COPY) == [
        f"{RENAME_CHECKLIST}: `b.txt` is listed twice in '## Keep'"
    ]


def test_check_rename_checklist_reports_an_item_that_covers_nothing() -> None:
    extra = _lists("- `a/`\n- `b.txt`\n- `gone/`\n- `a/missing.txt`", "- `Docs/Case.md`")
    assert check_rename_checklist(extra, _COPY) == [
        f"{RENAME_CHECKLIST}: `a/missing.txt` covers no file of the copy",
        f"{RENAME_CHECKLIST}: `gone/` covers no file of the copy",
    ]


def test_check_rename_checklist_lets_the_longest_item_win() -> None:
    nested = _lists("- `a/`\n- `b.txt`", "- `a/two.txt`\n- `Docs/Case.md`")
    assert check_rename_checklist(nested, _COPY) == []
    shadowed = _lists("- `a/`\n- `a/one.txt`\n- `a/two.txt`\n- `b.txt`", "- `Docs/Case.md`")
    assert check_rename_checklist(shadowed, _COPY) == [
        f"{RENAME_CHECKLIST}: `a/` covers no file of the copy"
    ]


def test_check_rename_checklist_reports_an_empty_list() -> None:
    no_replace = "# R\n\n## Keep\n\n- `a/`\n- `b.txt`\n- `Docs/Case.md`\n\n## Replace\n\nNothing.\n"
    assert f"{RENAME_CHECKLIST}: the '## Replace' list is empty or the heading is missing" in (
        check_rename_checklist(no_replace, _COPY)
    )
    no_keep = "# R\n\n## Replace\n\n- `a/`\n- `b.txt`\n- `Docs/Case.md`\n"
    assert f"{RENAME_CHECKLIST}: the '## Keep' list is empty or the heading is missing" in (
        check_rename_checklist(no_keep, _COPY)
    )
    empty_copy = check_rename_checklist(_VALID, set())
    assert len(empty_copy) == 3, "every item covers nothing when the copy holds no file"


def test_check_rename_checklist_reports_an_item_without_a_backticked_path() -> None:
    plain = _lists("- a/ is kept\n- `a/`\n- `b.txt`", "- `Docs/Case.md`")
    problems = check_rename_checklist(plain, _COPY)
    assert len(problems) == 1
    assert "does not start with a backticked path" in problems[0]


def test_check_rename_checklist_reports_problems_sorted() -> None:
    messy = _lists("- `z/`\n- `a/`\n- `b.txt`\n- `b.txt`", "- `Docs/Case.md`\n- `m/`")
    first = check_rename_checklist(messy, _COPY | {"y.txt", "x.txt"})
    assert first == sorted(first)
    assert len(first) >= 4
    assert check_rename_checklist(messy, list(_COPY | {"x.txt", "y.txt"})) == first


# ---------------------------------------------------------------------------------------------
# The rename section's names (ADPT-05)
#
# `check_rename_section` holds the `## Rename` section to the values it tells the reader to change.
# The real-tree test reads them from the files, so the section cannot drift from them. A rename is
# applied by hand in one checkout, so a half-done one shows in `git status`, and `dbt parse` fails
# while the project's `profile` and the profiles.yml key disagree, which stops the gate.

RENAME_HEADING = "Rename"
RENAME_COMMANDS = ("git grep -n", "make gate")


def rename_section(text: str) -> str | None:
    """The text under `## Rename` up to the next `## ` heading, or None when there is none."""
    lines = text.splitlines()
    for title, line in guide_h2(text):
        if title == RENAME_HEADING:
            later = [other for _, other in guide_h2(text) if other > line]
            return "\n".join(lines[line : (later[0] - 1) if later else len(lines)])
    return None


def _code_spans(text: str) -> set[str]:
    return {
        child.content
        for token in _parse(text)
        if token.type == "inline"
        for child in token.children or []
        if child.type == "code_inline"
    }


def check_rename_section(
    text: str, names: dict[str, str], path: str = RENAME_CHECKLIST
) -> list[str]:
    """Return every current name the `## Rename` section does not hold in backticks, sorted.

    `names` maps what a name is (for example "dbt project name") to its current value. The section
    must also show how to find the places that name the project, `git grep -n`, and end by
    running `make gate`. A missing section is reported once.
    """
    section = rename_section(text)
    if section is None:
        return [f"{path}: the '## {RENAME_HEADING}' section is missing"]
    spans = _code_spans(section)
    problems = [
        f"{path}: the '## {RENAME_HEADING}' section does not name the {label} `{value}` in backticks"
        for label, value in names.items()
        if value not in spans
    ]
    problems += [
        f"{path}: the '## {RENAME_HEADING}' section does not mention `{command}`"
        for command in RENAME_COMMANDS
        if command not in section
    ]
    return sorted(problems)


def real_rename_names() -> dict[str, str]:
    """The current names, read from the files that hold them (no hand-typed value)."""
    project = yaml.safe_load(_read("dbt_project.yml"))
    profiles = yaml.safe_load(_read("profiles.yml"))
    pyproject = tomllib.loads(_read("pyproject.toml"))
    assert list(profiles) == [project["profile"]], "profiles.yml must hold exactly the profile"
    return {
        "dbt project name": project["name"],
        "dbt profile": project["profile"],
        "profiles.yml top key": next(iter(profiles)),
        "Python project name": pyproject["project"]["name"],
    }


def test_the_real_rename_section_names_the_current_values() -> None:
    text = strip_example_blocks(_read(RENAME_CHECKLIST))
    names = real_rename_names()
    assert len(set(names.values())) >= 2, "the names are read from the files, not left empty"
    assert all(names.values())
    assert check_rename_section(text, names) == []


_NAMES = {"dbt project name": "proj_x", "dbt profile": "proj_x", "Python project name": "proj-x"}
_SECTION = (
    "# R\n\n## Rename\n\nSet `name` to `proj_x` and `profile` to `proj_x`, the project to "
    "`proj-x`. Find the rest with `git grep -n proj_x`, then run `make gate`.\n\n## Keep\n\n"
    "- `a/`\n"
)


def test_check_rename_section_passes_a_section_that_holds_every_name() -> None:
    assert check_rename_section(_SECTION, _NAMES) == []


def test_check_rename_section_reports_a_changed_name() -> None:
    text = _SECTION.replace("`proj-x`", "`other-x`")
    assert check_rename_section(text, _NAMES) == [
        (
            f"{RENAME_CHECKLIST}: the '## Rename' section does not name the Python project name "
            "`proj-x` in backticks"
        )
    ]
    plain = _SECTION.replace("`proj_x` and", "proj_x and")
    assert check_rename_section(plain, _NAMES) == []  # the second span still names it
    both = _SECTION.replace("`proj_x`", "proj_x")
    assert len(check_rename_section(both, _NAMES)) == 2


def test_check_rename_section_reports_a_missing_git_grep() -> None:
    text = _SECTION.replace("`git grep -n proj_x`", "a search")
    assert check_rename_section(text, _NAMES) == [
        f"{RENAME_CHECKLIST}: the '## Rename' section does not mention `git grep -n`"
    ]


def test_check_rename_section_reports_a_missing_make_gate() -> None:
    text = _SECTION.replace("`make gate`", "the tests")
    assert check_rename_section(text, _NAMES) == [
        f"{RENAME_CHECKLIST}: the '## Rename' section does not mention `make gate`"
    ]


def test_check_rename_section_reads_only_the_rename_section() -> None:
    elsewhere = "# R\n\n## Rename\n\nNothing here.\n\n## Keep\n\nSee `proj_x` `proj-x`.\n"
    assert len(check_rename_section(elsewhere, _NAMES)) == 5
    assert check_rename_section("# R\n\n## Keep\n", _NAMES) == [
        f"{RENAME_CHECKLIST}: the '## Rename' section is missing"
    ]


def test_check_rename_section_reports_problems_sorted() -> None:
    bare = "# R\n\n## Rename\n\nNothing.\n"
    problems = check_rename_section(bare, _NAMES)
    assert problems == sorted(problems)
    assert len(problems) == 5


def test_the_rename_checklist_is_linked_from_the_guide_index() -> None:
    targets = [link.target for link in guide_links(_read(f"{GUIDE_DIR}/index.md"))]
    assert "rename-checklist.md" in targets
    assert resolve_target("rename-checklist.md", _tracked()) == RENAME_CHECKLIST


# ---------------------------------------------------------------------------
# The adopter setup checklist (ADPT-06)
#
# "Use this template" copies files, not repository settings, so the checklist lists the settings an
# adopter sets by hand. It must name each setting and each variable or secret that a workflow
# reads, hold its eight sections, and hold an inheritance table with a row for each kind of
# setting, headed with the date of the walkthrough on a real copy whose observed values it states.

ADOPTER_SETUP = f"{GUIDE_DIR}/adopter-setup.md"
ADOPTER_SETUP_NAMES = (
    "gate",
    "GitHub Actions",
    "ENABLE_PAGES",
    "ENABLE_NIGHTLY",
    "ENABLE_CLEANROOM",
    "ENABLE_FABRIC",
    "DOCS_ATTRIBUTION",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_CI_LAKEHOUSE_ID",
    "FABRIC_CI_LAKEHOUSE",
)
ADOPTER_SETUP_HEADINGS = (
    "First steps",
    "Required check",
    "Pages",
    "Opt-in workflows",
    "Secrets and Fabric CI",
    "Actions permissions",
    "Description and topics",
    "What a template copy inherits",
)
INHERITANCE_HEADING = "What a template copy inherits"
INHERITANCE_ROWS = (
    "Files",
    "Rulesets",
    "Actions variables",
    "Actions secrets",
    "Environments",
    "Pages source",
    "Actions permissions",
    "Default branch",
    "Topics",
    "Template flag",
    "Workflows on the copy's first commit",
)


def _section_body(text: str, heading: str) -> str | None:
    """Return the text under one `## ` heading up to the next `## ` heading, or None."""
    lines = text.splitlines()
    starts = {title: number for title, number in guide_h2(text)}
    if heading not in starts:
        return None
    later = sorted(number for number in starts.values() if number > starts[heading])
    end = later[0] - 1 if later else len(lines)
    return "\n".join(lines[starts[heading] : end])


def _first_cells(section: str) -> list[str]:
    cells = []
    for line in section.splitlines():
        if line.startswith("|"):
            cells.append(line.strip("|").split("|", 1)[0].strip())
    return cells


def check_adopter_setup(text: str) -> list[str]:
    """Return the problems of an adopter setup checklist: missing names, sections or table rows.

    A name is looked for as written in a code span, except `GitHub Actions`, which is a menu
    choice written in plain words. The inheritance table must carry one row per kind of setting and
    state observed values with a date, as `check_walkthrough_record` checks.
    """
    problems = []
    for name in ADOPTER_SETUP_NAMES:
        needle = name if name == "GitHub Actions" else f"`{name}`"
        if needle not in text:
            problems.append(f"the checklist does not name {needle}")
    titles = [title for title, _ in guide_h2(text)]
    for heading in ADOPTER_SETUP_HEADINGS:
        if heading not in titles:
            problems.append(f"the checklist has no `## {heading}` section")
    section = _section_body(text, INHERITANCE_HEADING)
    if section is not None:
        rows = {cell.lower() for cell in _first_cells(section)}
        for row in INHERITANCE_ROWS:
            if row.lower() not in rows:
                problems.append(f"the inheritance table has no row for {row!r}")
    problems.extend(check_walkthrough_record(text))
    return problems


OBSERVED_DATE = re.compile(r"Observed on \d{4}-\d{2}-\d{2}")
UUID_SHAPE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# The throwaway copy is named dbt-starter-<something>-check; the pattern avoids spelling the name.
THROWAWAY_NAME = re.compile(r"dbt-starter-[a-z-]*check")


def check_walkthrough_record(text: str) -> list[str]:
    """Return the problems of the inheritance section as a record of a walkthrough on a real copy.

    The section must carry `Observed on YYYY-MM-DD`, give every table row an observed value that
    does not still say expected, hold the row for the copy's first commit, and publish no UUID,
    owner name, owner URL or throwaway name.
    """
    section = _section_body(text, INHERITANCE_HEADING)
    if section is None:
        return [f"the checklist has no `## {INHERITANCE_HEADING}` section to hold the record"]
    problems = []
    if not OBSERVED_DATE.search(section):
        problems.append("the inheritance section does not say `Observed on YYYY-MM-DD`")
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and not set(cells[0]) <= set("-: "):
            rows.append(cells)
    # The first table row is the header; every row after it carries a setting and its value.
    for cells in rows[1:]:
        if not cells[1]:
            problems.append(f"the inheritance row {cells[0]!r} has no observed value")
        elif "expected" in cells[1].lower():
            problems.append(f"the inheritance row {cells[0]!r} still says expected")
    if "workflows on the copy's first commit" not in {cells[0].lower() for cells in rows[1:]}:
        problems.append("the inheritance table has no row for the copy's first commit")
    if UUID_SHAPE.search(section):
        problems.append("the inheritance section holds a UUID-shaped identifier")
    if "github.com/hartlesskl/" in section:
        problems.append("the inheritance section holds an owner URL")
    if "hartlesskl" in section.replace("github.com/hartlesskl/", ""):
        problems.append("the inheritance section holds an owner name")
    if THROWAWAY_NAME.search(section):
        problems.append("the inheritance section holds the throwaway's name")
    return problems


def test_the_real_adopter_setup_checklist_passes() -> None:
    assert ADOPTER_SETUP in _tracked(), f"{ADOPTER_SETUP} is not tracked"
    assert check_adopter_setup(_read(ADOPTER_SETUP)) == []


def test_the_adopter_setup_checklist_is_linked_from_the_guide_index() -> None:
    targets = [link.target for link in guide_links(_read(f"{GUIDE_DIR}/index.md"))]
    for guide in ("conventions.md", "swap-out.md", "rename-checklist.md", "adopter-setup.md"):
        assert guide in targets, guide
    assert resolve_target("adopter-setup.md", _tracked()) == ADOPTER_SETUP


def test_the_adopter_setup_checklist_links_the_fabric_ci_section() -> None:
    targets = [link.target for link in guide_links(_read(ADOPTER_SETUP))]
    assert "../fabric-setup.md#ci-opt-in" in targets
    assert "## CI (opt-in)" in _read("docs/fabric-setup.md")


def _setup_text() -> str:
    return _read(ADOPTER_SETUP)


def test_check_adopter_setup_reports_a_missing_name() -> None:
    for name in ADOPTER_SETUP_NAMES:
        needle = name if name == "GitHub Actions" else f"`{name}`"
        # FABRIC_CI_LAKEHOUSE is a prefix of FABRIC_CI_LAKEHOUSE_ID, so the whole token is cut.
        doctored = _setup_text().replace(needle, "removed")
        assert any(needle in problem for problem in check_adopter_setup(doctored)), name


def test_check_adopter_setup_reports_a_missing_section() -> None:
    for heading in ADOPTER_SETUP_HEADINGS:
        doctored = _setup_text().replace(f"## {heading}\n", "")
        problems = check_adopter_setup(doctored)
        assert any(f"`## {heading}`" in problem for problem in problems), heading


def test_check_adopter_setup_reports_a_missing_table_row() -> None:
    for row in INHERITANCE_ROWS:
        kept = [
            line
            for line in _setup_text().splitlines()
            if not line.lower().startswith(f"| {row.lower()} |")
        ]
        problems = check_adopter_setup("\n".join(kept))
        assert any(repr(row) in problem for problem in problems), row


def test_check_adopter_setup_reports_values_that_are_not_marked_observed() -> None:
    doctored = _setup_text().replace("Observed on", "Known on")
    assert any("Observed on" in problem for problem in check_adopter_setup(doctored))


# The walkthrough record (ADPT-06, D-19): the inheritance table states what a real copy held, on a
# stated date, and publishes no identifier, owner name, repository URL or throwaway name.

WALKTHROUGH_SECTION = """## What a template copy inherits

Observed on 2026-11-27, on a public copy made from the template. Each value was read back.

| Setting | Observed in a copy | Read-back command |
|---------|--------------------|-------------------|
| Files | Copied in one commit | `git ls-files` |
| Rulesets | None | `gh api repos/<owner>/<repo>/rulesets` |
| Workflows on the copy's first commit | The gate ran on it | `gh run list -R <owner>/<repo>` |
"""


def _record(text: str = WALKTHROUGH_SECTION) -> list[str]:
    return check_walkthrough_record(text)


def test_check_walkthrough_record_passes_a_walked_section() -> None:
    assert _record() == []


def test_check_walkthrough_record_reports_a_missing_or_malformed_date() -> None:
    for doctored in (
        WALKTHROUGH_SECTION.replace("Observed on 2026-11-27", "Observed on a Friday"),
        WALKTHROUGH_SECTION.replace("Observed on 2026-11-27", "Seen on 2026-11-27"),
        WALKTHROUGH_SECTION.replace("2026-11-27", "27-11-2026"),
    ):
        assert any("Observed on" in problem for problem in _record(doctored))


def test_check_walkthrough_record_reports_a_row_that_still_says_expected() -> None:
    doctored = WALKTHROUGH_SECTION.replace("| None |", "| Expected: none |")
    assert any("Rulesets" in problem and "expected" in problem for problem in _record(doctored))


def test_check_walkthrough_record_reports_a_row_with_no_observed_value() -> None:
    doctored = WALKTHROUGH_SECTION.replace("| None |", "|  |")
    assert any("Rulesets" in problem for problem in _record(doctored))


def test_check_walkthrough_record_reports_a_missing_first_commit_row() -> None:
    kept = [
        line
        for line in WALKTHROUGH_SECTION.splitlines()
        if not line.startswith("| Workflows on the copy's first commit")
    ]
    assert any("first commit" in problem for problem in _record("\n".join(kept)))


def test_check_walkthrough_record_reports_identifiers_urls_and_names() -> None:
    leaks = {
        "a UUID": "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0",
        "an owner URL": "https://github.com/hartlesskl/dbt-starter",
        "an owner name": "hartlesskl",
        "the throwaway's name": "dbt-starter-template-check",
    }
    for what, leak in leaks.items():
        doctored = WALKTHROUGH_SECTION.replace("| None |", f"| None, see {leak} |")
        assert any(what in problem for problem in _record(doctored)), what


def test_check_walkthrough_record_reports_a_missing_section() -> None:
    assert any("What a template copy inherits" in p for p in _record("# Title\n\ntext\n"))


def test_the_real_adopter_setup_checklist_carries_a_walkthrough_record() -> None:
    assert check_walkthrough_record(_setup_text()) == []


def test_check_adopter_setup_reports_a_name_only_in_plain_words() -> None:
    doctored = _setup_text().replace("`ENABLE_PAGES`", "ENABLE_PAGES")
    assert check_adopter_setup(doctored) == ["the checklist does not name `ENABLE_PAGES`"]


def test_check_adopter_setup_passes_the_real_text_in_a_different_order_of_sections() -> None:
    parts = _setup_text().split("\n## ")
    shuffled = "\n## ".join([parts[0], *reversed(parts[1:])])
    assert check_adopter_setup(shuffled) == []


def test_the_adopter_setup_checklist_holds_no_identifier() -> None:
    guid = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    text = _setup_text()
    assert guid.search(text) is None
    assert "hartlesskl" not in text
