"""The data credit has one canonical text, and every echo agrees with it (D-02, D-03).

`docs/data-terms.md` is the canonical credit. Each source module may declare `DATA_TERMS` with the
keys `name`, `link`, `access_date`, `disclaimer` and `credit_line`. For every source that does, the
link, access date and disclaimer must appear word for word in the canonical file, `name` and `link`
must be the source's `DOCS_ATTRIBUTION`, and `credit_line` must be built from them. The epilog that
`data_terms_epilog` builds and the `load --help` text must hold every credit line and the license
note, and the canonical file must state the license note outside the example markers, so it still
reads correctly after the example is removed.

The file names no source and no example, so it stays in a copy and passes there.
"""

from __future__ import annotations

import html
import types

from support.dbt_cli import REPO_ROOT

from starter_cli import demo, load, site
from starter_cli.project import LICENSE_NOTE, data_terms_epilog, discover_sources

CANONICAL = REPO_ROOT / "docs" / "data-terms.md"
KEYS = ("name", "link", "access_date", "disclaimer", "credit_line")
# Built from parts, so this file holds no marker text that the removal handle would read.
BEGIN = "example:" + "begin"
END = "example:" + "end"


def strip_blocks(text: str) -> str:
    """Return `text` without the lines from a begin marker to its end marker."""
    kept = []
    inside = False
    for line in text.splitlines():
        if BEGIN in line:
            inside = True
        elif END in line:
            inside = False
        elif not inside:
            kept.append(line)
    return "\n".join(kept) + "\n"


def data_terms_problems(
    sources: dict[str, types.ModuleType],
    canonical: str,
    epilog: str,
    help_text: str,
    demo_help: str | None = None,
    page: str | None = None,
) -> list[str]:
    """Return every place a credit echo disagrees with the canonical file.

    `sources` are the discovered source modules, `canonical` the text of docs/data-terms.md,
    `epilog` what `data_terms_epilog(sources)` returned, and `help_text` the `load --help` text.
    `demo_help` is the `demo --help` text and `page` the Pages root page; an echo that is `None`
    is not checked, so a caller names each one it holds.
    """
    problems: list[str] = []
    if LICENSE_NOTE not in strip_blocks(canonical):
        problems.append("docs/data-terms.md: the license note is missing outside the markers")
    if LICENSE_NOTE not in epilog:
        problems.append("epilog: the license note is missing")
    if LICENSE_NOTE not in help_text:
        problems.append("load --help: the license note is missing")
    if demo_help is not None and LICENSE_NOTE not in demo_help:
        problems.append("demo --help: the license note is missing")
    if page is not None and html.escape(LICENSE_NOTE) not in page:
        problems.append("the Pages root page: the license note is missing")
    for name, module in sorted(sources.items()):
        terms = getattr(module, "DATA_TERMS", None)
        if terms is None:
            continue
        missing = [key for key in KEYS if not terms.get(key)]
        if missing:
            problems.append(f"{name}: DATA_TERMS lacks {', '.join(missing)}")
            continue
        if (terms["name"], terms["link"]) != tuple(getattr(module, "DOCS_ATTRIBUTION", ())):
            problems.append(f"{name}: DATA_TERMS name and link are not DOCS_ATTRIBUTION")
        for key in ("link", "access_date", "disclaimer"):
            if terms[key] not in canonical:
                problems.append(f"{name}: docs/data-terms.md does not hold the {key} word for word")
        for key in ("name", "link", "access_date", "disclaimer"):
            if terms[key] not in terms["credit_line"]:
                problems.append(f"{name}: credit_line is not built from the {key}")
        if terms["credit_line"] not in epilog:
            problems.append(f"{name}: the epilog does not hold the credit line")
        if terms["credit_line"] not in help_text:
            problems.append(f"{name}: load --help does not hold the credit line")
        if demo_help is not None and terms["credit_line"] not in demo_help:
            problems.append(f"{name}: demo --help does not hold the credit line")
        if page is not None and html.escape(terms["credit_line"]) not in page:
            problems.append(f"{name}: the Pages root page does not hold the credit line")
    return problems


def _problems_now() -> list[str]:
    sources = discover_sources()
    epilog = data_terms_epilog(sources)
    help_text = load.build_parser(sources).format_help()
    return data_terms_problems(
        sources,
        CANONICAL.read_text(encoding="utf-8"),
        epilog,
        help_text,
        demo_help=demo.build_parser(sources).format_help(),
        page=site.render_index(site.attributions(sources), site.credit_lines(sources)),
    )


def _fake_source(**terms: str) -> types.ModuleType:
    module = types.ModuleType("fake_source")
    module.SOURCE = "fake"
    module.ENTITIES = {}
    module.DOCS_ATTRIBUTION = ("Fake Data", "https://example.org/fake")
    base = {
        "name": "Fake Data",
        "link": "https://example.org/fake",
        "access_date": "2026-01-02",
        "disclaimer": "The publisher gives no guarantee.",
    }
    base.update(terms)
    base.setdefault(
        "credit_line",
        f'{base["name"]}, {base["link"]} (accessed {base["access_date"]}). "{base["disclaimer"]}"',
    )
    module.DATA_TERMS = base
    return module


def _canonical_for(**terms: str) -> str:
    fake = _fake_source(**terms).DATA_TERMS
    return (
        f"# Data terms\n\n{LICENSE_NOTE}\n\n<!-- {BEGIN} -->\n## Fake\n"
        f"{fake['link']} {fake['access_date']} {fake['disclaimer']}\n<!-- {END} -->\n"
    )


def _problems_for(canonical: str, module: types.ModuleType) -> list[str]:
    sources = {"fake": module}
    epilog = data_terms_epilog(sources)
    help_text = load.build_parser(sources).format_help()
    return data_terms_problems(
        sources,
        canonical,
        epilog,
        help_text,
        demo_help=demo.build_parser(sources).format_help(),
        page=site.render_index(site.attributions(sources), site.credit_lines(sources)),
    )


def test_the_real_credit_echoes_agree_with_the_canonical_file() -> None:
    assert _problems_now() == []


def test_the_real_tree_has_a_source_that_declares_data_terms() -> None:
    declared = [name for name, mod in discover_sources().items() if hasattr(mod, "DATA_TERMS")]
    assert declared, "no source declares DATA_TERMS, so the echo check would pass vacuously"


def test_a_consistent_fake_source_has_no_problem() -> None:
    assert _problems_for(_canonical_for(), _fake_source()) == []


def test_a_disclaimer_changed_in_the_canonical_text_is_a_problem() -> None:
    canonical = _canonical_for().replace("no guarantee", "a guarantee")
    problems = _problems_for(canonical, _fake_source())
    assert any("disclaimer word for word" in problem for problem in problems)


def test_an_access_date_changed_in_the_canonical_text_is_a_problem() -> None:
    canonical = _canonical_for().replace("2026-01-02", "2026-01-03")
    assert any("access_date" in p for p in _problems_for(canonical, _fake_source()))


def test_a_link_changed_in_the_canonical_text_is_a_problem() -> None:
    canonical = _canonical_for().replace("example.org/fake", "example.org/other")
    assert any("link word for word" in p for p in _problems_for(canonical, _fake_source()))


def test_a_license_note_only_inside_the_markers_is_a_problem() -> None:
    canonical = f"# Data terms\n\n<!-- {BEGIN} -->\n{LICENSE_NOTE}\n<!-- {END} -->\n"
    problems = _problems_for(canonical, _fake_source())
    assert any("outside the markers" in problem for problem in problems)


def test_a_credit_line_not_built_from_its_parts_is_a_problem() -> None:
    module = _fake_source(credit_line="Fake Data, https://example.org/fake")
    problems = _problems_for(_canonical_for(), module)
    assert any("credit_line is not built from the access_date" in p for p in problems)


def test_a_name_or_link_that_differs_from_the_docs_attribution_is_a_problem() -> None:
    module = _fake_source()
    module.DOCS_ATTRIBUTION = ("Other Data", "https://example.org/fake")
    assert any("DOCS_ATTRIBUTION" in p for p in _problems_for(_canonical_for(), module))


def test_a_missing_key_is_a_problem() -> None:
    module = _fake_source()
    del module.DATA_TERMS["disclaimer"]
    assert any("lacks disclaimer" in p for p in _problems_for(_canonical_for(), module))


def test_an_echo_that_drops_the_credit_line_or_the_note_is_a_problem() -> None:
    module = _fake_source()
    problems = data_terms_problems({"fake": module}, _canonical_for(), "", "")
    assert any("epilog does not hold" in p for p in problems)
    assert any("load --help does not hold" in p for p in problems)
    assert any("epilog: the license note" in p for p in problems)
    assert any("load --help: the license note" in p for p in problems)


def test_a_demo_help_that_drops_the_credit_line_or_the_note_is_a_problem() -> None:
    module = _fake_source()
    sources = {"fake": module}
    epilog = data_terms_epilog(sources)
    help_text = load.build_parser(sources).format_help()
    problems = data_terms_problems(sources, _canonical_for(), epilog, help_text, demo_help="x")
    assert any("demo --help does not hold the credit line" in p for p in problems)
    assert any("demo --help: the license note" in p for p in problems)
    assert not any("Pages" in p for p in problems)


def test_a_root_page_that_drops_the_disclaimer_or_the_note_is_a_problem() -> None:
    module = _fake_source()
    sources = {"fake": module}
    epilog = data_terms_epilog(sources)
    help_text = load.build_parser(sources).format_help()
    page = site.render_index([("Fake Data", "https://example.org/fake")], [])
    problems = data_terms_problems(sources, _canonical_for(), epilog, help_text, page=page)
    assert any("Pages root page does not hold the credit line" in p for p in problems)
    assert not any("license note is missing" in p and "Pages" in p for p in problems)
    bare = data_terms_problems(sources, _canonical_for(), epilog, help_text, page="")
    assert any("Pages root page: the license note" in p for p in bare)


def test_the_real_demo_help_and_root_page_hold_every_credit_line() -> None:
    sources = discover_sources()
    lines = [m.DATA_TERMS["credit_line"] for m in sources.values() if hasattr(m, "DATA_TERMS")]
    assert lines
    help_text = demo.build_parser(sources).format_help()
    page = site.render_index(site.attributions(sources), site.credit_lines(sources))
    for line in lines:
        assert line in help_text
        assert html.escape(line) in page


def test_a_source_without_data_terms_adds_no_credit_but_the_note_stays() -> None:
    bare = types.ModuleType("bare")
    bare.SOURCE = "bare"
    bare.ENTITIES = {}
    assert data_terms_epilog({"bare": bare}) == LICENSE_NOTE


def test_the_epilog_lists_credit_lines_in_source_name_order() -> None:
    first, second = _fake_source(), _fake_source(name="Fake Data")
    second.DATA_TERMS = {**second.DATA_TERMS, "credit_line": "second credit"}
    first.DATA_TERMS = {**first.DATA_TERMS, "credit_line": "first credit"}
    epilog = data_terms_epilog({"b": second, "a": first})
    assert epilog.splitlines() == ["first credit", "second credit", LICENSE_NOTE]


def test_strip_blocks_removes_only_the_text_between_the_markers() -> None:
    text = f"before\n<!-- {BEGIN} -->\ninside\n<!-- {END} -->\nafter\n"
    assert strip_blocks(text) == "before\nafter\n"
