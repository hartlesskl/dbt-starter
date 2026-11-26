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

A link to a generic file that an adopter later deletes is not caught here; their own gate catches
it. The checks are pure functions over text and a tracked-path list. Each has a doctored case that
must report its problem, so a checker that finds nothing is not read as a clean guide.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from typing import NamedTuple

from markdown_it import MarkdownIt
from support.dbt_cli import REPO_ROOT

from starter_cli import markers
from starter_cli.markers import MarkerError, strip_example_blocks
from starter_cli.remove_example import UNVERIFIED_STATUS

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
    assert example_mentions(raw), "the template README names the example, so the check has input"
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
