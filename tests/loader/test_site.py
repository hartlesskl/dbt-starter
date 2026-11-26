"""Site assembly and the raw-file cache key, offline: no network, no DuckDB, no real `site/`."""

from __future__ import annotations

import html
import re
import types
from pathlib import Path

import pytest

from starter_cli import site
from starter_cli.landing import EntitySpec
from starter_cli.markers import BEGIN, END, MarkerError, example_blocks, strip_example_blocks
from starter_cli.project import LICENSE_NOTE

STATIC_BYTES = b"<html><body>the static dbt docs, with \xc3\xa9 and a script</body></html>"
ATTRIBUTION = ("Acme Public Parts Register", "https://example.org/parts")
TERMS = {
    "name": ATTRIBUTION[0],
    "link": ATTRIBUTION[1],
    "access_date": "2026-01-02",
    "disclaimer": "The register gives no guarantee.",
    "credit_line": (
        "Acme Public Parts Register, https://example.org/parts (accessed 2026-01-02). "
        'The page says: "The register gives no guarantee."'
    ),
}

SINGLE = EntitySpec(
    source="acme",
    entity="widgets",
    url="https://example.invalid/widgets.csv",
    file_name="widgets.csv",
    columns=("code", "name"),
    header_prefix=b'"Code"',
)


def monthly(months: tuple[str, ...]) -> EntitySpec:
    return EntitySpec(
        source="acme",
        entity="parts",
        url="https://example.invalid/parts_{month}.parquet",
        file_name="parts_{month}.parquet",
        columns=("code",),
        header_prefix=b"PAR1",
        file_format="parquet",
        default_months=months,
    )


def make_source(months: tuple[str, ...] = ("2024-01",), **extra) -> types.SimpleNamespace:
    entities = {"widgets": SINGLE, "parts": monthly(months)}
    return types.SimpleNamespace(SOURCE="acme", ENTITIES=entities, **extra)


@pytest.fixture
def sources() -> dict:
    return {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION, DATA_TERMS=TERMS)}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A fake checkout holding the static docs that `uv run demo` would have written."""
    (tmp_path / "target").mkdir()
    (tmp_path / site.STATIC_INDEX).write_bytes(STATIC_BYTES)
    return tmp_path


def test_assemble_copies_the_static_docs_byte_for_byte(root, sources) -> None:
    folder = site.assemble(root, sources)
    assert folder == root / "site"
    assert (folder / "dbt-docs" / "index.html").read_bytes() == STATIC_BYTES


def test_the_root_page_links_the_docs_relatively_and_names_the_source_and_engine(
    root, sources
) -> None:
    page = (site.assemble(root, sources) / "index.html").read_text(encoding="utf-8")
    assert 'href="dbt-docs/"' in page
    assert "Acme Public Parts Register" in page
    assert 'href="https://example.org/parts"' in page
    assert "DuckDB" in page
    assert 'href="/' not in page
    assert 'src="/' not in page
    assert "<script" not in page


def test_the_root_page_is_a_complete_html5_page(root, sources) -> None:
    page = (site.assemble(root, sources) / "index.html").read_text(encoding="utf-8")
    assert page.lower().startswith("<!doctype html>")
    assert '<html lang="en">' in page
    assert '<meta charset="utf-8">' in page
    assert "viewport" in page
    assert "<title>dbt-starter docs</title>" in page


def test_attribution_text_is_escaped(root) -> None:
    hostile = ('<script>alert(1)</script> & "co"', "https://example.org/a?x=1&y=2")
    page = site.assemble(root, {"acme": make_source(DOCS_ATTRIBUTION=hostile)}) / "index.html"
    text = page.read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "x=1&amp;y=2" in text


def test_the_root_page_holds_each_disclaimer_with_its_access_date_and_the_license_note(
    root, sources
) -> None:
    page = (site.assemble(root, sources) / "index.html").read_text(encoding="utf-8")
    assert "The register gives no guarantee." in page
    assert "2026-01-02" in page
    assert html.escape(LICENSE_NOTE) in page


def test_the_root_page_prints_the_license_note_even_when_no_source_declares_terms(root) -> None:
    sources = {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION)}
    page = (site.assemble(root, sources) / "index.html").read_text(encoding="utf-8")
    assert html.escape(LICENSE_NOTE) in page
    assert "The register gives no guarantee." not in page


def test_a_disclaimer_holding_markup_is_escaped_on_the_root_page(root) -> None:
    hostile = {**TERMS, "credit_line": "<script>alert(2)</script> & more <b>x</b>"}
    sources = {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION, DATA_TERMS=hostile)}
    text = (site.assemble(root, sources) / "index.html").read_text(encoding="utf-8")
    assert "<script" not in text
    assert "<b>x</b>" not in text
    assert "&lt;script&gt;alert(2)&lt;/script&gt; &amp; more &lt;b&gt;x&lt;/b&gt;" in text


def test_a_declared_data_terms_without_a_credit_line_is_refused_by_name(root) -> None:
    broken = {**TERMS, "credit_line": ""}
    sources = {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION, DATA_TERMS=broken)}
    with pytest.raises(site.SiteError, match="acme"):
        site.assemble(root, sources)


def test_a_missing_static_file_names_the_demo_command(tmp_path, sources) -> None:
    with pytest.raises(site.SiteError, match="uv run demo"):
        site.assemble(tmp_path, sources)
    assert not (tmp_path / "site").exists()


def test_main_turns_a_missing_static_file_into_exit_2_with_the_message_on_stderr(
    tmp_path, sources, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(site, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(site, "discover_sources", lambda: sources)
    assert site.main([]) == 2
    captured = capsys.readouterr()
    assert "uv run demo" in captured.err
    assert captured.out == ""


def test_a_source_without_an_attribution_is_refused_by_name(root) -> None:
    with pytest.raises(site.SiteError, match="acme"):
        site.assemble(root, {"acme": make_source()})
    assert not (root / "site").exists()


@pytest.mark.parametrize(
    "bad",
    [
        ("Name", "http://example.org/parts"),
        ("Name", "ftp://example.org/parts"),
        ("", "https://example.org/parts"),
        ("Name", ""),
        ("Name",),
        "https://example.org/parts",
        None,
    ],
)
def test_an_attribution_that_is_not_a_name_and_an_https_link_is_refused(root, bad) -> None:
    with pytest.raises(site.SiteError, match="acme"):
        site.assemble(root, {"acme": make_source(DOCS_ATTRIBUTION=bad)})


def test_no_sources_at_all_is_refused(root) -> None:
    with pytest.raises(site.SiteError):
        site.assemble(root, {})


def test_main_turns_an_undeclared_source_into_exit_2_naming_it(root, monkeypatch, capsys) -> None:
    monkeypatch.setattr(site, "repo_root", lambda: root)
    monkeypatch.setattr(site, "discover_sources", lambda: {"acme": make_source()})
    assert site.main([]) == 2
    assert "acme" in capsys.readouterr().err


def test_a_second_assemble_replaces_the_previous_site(root, sources) -> None:
    folder = site.assemble(root, sources)
    stale = folder / "stale.txt"
    stale.write_text("old")
    site.assemble(root, sources)
    assert not stale.exists()
    assert (folder / "index.html").is_file()


def test_a_site_folder_that_resolves_outside_the_root_is_refused(
    root, sources, tmp_path_factory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "keep.txt").write_text("keep")
    (root / "site").symlink_to(outside, target_is_directory=True)
    with pytest.raises(site.SiteError):
        site.assemble(root, sources)
    assert (outside / "keep.txt").read_text() == "keep"


def test_main_prints_one_line_naming_both_files_and_returns_0(
    root, sources, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(site, "repo_root", lambda: root)
    monkeypatch.setattr(site, "discover_sources", lambda: sources)
    assert site.main([]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert "index.html" in lines[0]
    assert "dbt-docs" in lines[0]


def test_the_cache_key_has_the_documented_shape_and_is_stable(sources) -> None:
    key = site.raw_cache_key(sources)
    assert re.fullmatch(r"raw-v1-[0-9a-f]{16}", key)
    assert site.raw_cache_key(sources) == key


def test_the_cache_key_does_not_depend_on_the_attribution() -> None:
    plain = {"acme": make_source()}
    declared = {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION)}
    assert site.raw_cache_key(plain) == site.raw_cache_key(declared)


def test_the_cache_key_changes_when_a_default_month_changes() -> None:
    first = site.raw_cache_key({"acme": make_source(("2024-01",))})
    second = site.raw_cache_key({"acme": make_source(("2024-02",))})
    third = site.raw_cache_key({"acme": make_source(("2024-01", "2024-02"))})
    assert len({first, second, third}) == 3


def test_the_cache_key_changes_when_a_single_file_name_changes() -> None:
    renamed = types.SimpleNamespace(
        SOURCE="acme",
        ENTITIES={
            "widgets": EntitySpec(
                source="acme",
                entity="widgets",
                url="https://example.invalid/widgets2.csv",
                file_name="widgets2.csv",
                columns=("code", "name"),
                header_prefix=b'"Code"',
            ),
            "parts": monthly(("2024-01",)),
        },
    )
    assert site.raw_cache_key({"acme": renamed}) != site.raw_cache_key({"acme": make_source()})


def test_the_cache_key_ignores_the_order_the_entities_are_declared_in() -> None:
    forward = make_source()
    backward = types.SimpleNamespace(
        SOURCE="acme", ENTITIES=dict(reversed(list(forward.ENTITIES.items())))
    )
    assert site.raw_cache_key({"acme": forward}) == site.raw_cache_key({"acme": backward})


def test_the_cache_key_flag_prints_exactly_the_key_and_returns_0(
    sources, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(site, "discover_sources", lambda: sources)
    assert site.main(["--cache-key"]) == 0
    assert capsys.readouterr().out == site.raw_cache_key(sources) + "\n"


def test_the_cache_key_flag_needs_no_static_docs_and_no_attribution(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(site, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(site, "discover_sources", lambda: {"acme": make_source()})
    assert site.main(["--cache-key"]) == 0
    assert capsys.readouterr().out.startswith("raw-v1-")


# --- the guides: docs/guide/*.md rendered into site/guide/ (D-12) ---------------------------------

HTTPS_REMOTE = "https://github.com/acme-org/starter.git"
SSH_REMOTE = "ssh://git@github.com/acme-org/starter.git"
SCP_REMOTE = "git@github.com:acme-org/starter.git"
BLOB = "https://github.com/acme-org/starter/blob/HEAD/"


def write_guide(root: Path, name: str, text: str) -> None:
    folder = root / "docs" / "guide"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def hrefs(page: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', page)


def guide_page(root: Path, sources: dict, name: str = "index.html", remote=None) -> str:
    folder = site.assemble(root, sources, remote)
    return (folder / "guide" / name).read_text(encoding="utf-8")


def test_guide_pages_are_written_beside_the_dbt_docs(root, sources) -> None:
    write_guide(root, "index.md", "# Guides\n\nHello.\n")
    write_guide(root, "adopt.md", "# Adopt\n")
    folder = site.assemble(root, sources)
    assert (folder / "guide" / "index.html").is_file()
    assert (folder / "guide" / "adopt.html").is_file()
    assert (folder / "dbt-docs" / "index.html").read_bytes() == STATIC_BYTES


def test_the_root_page_links_the_guides_only_when_there_are_guides(root, sources) -> None:
    folder = site.assemble(root, sources)
    assert 'href="guide/"' not in (folder / "index.html").read_text(encoding="utf-8")
    assert not (folder / "guide").exists()
    write_guide(root, "index.md", "# Guides\n")
    folder = site.assemble(root, sources)
    assert 'href="guide/"' in (folder / "index.html").read_text(encoding="utf-8")


def test_a_guide_page_is_a_complete_page_with_a_nav_line_and_its_title(root, sources) -> None:
    write_guide(root, "index.md", "# The <Guide> & more\n\nText.\n")
    page = guide_page(root, sources)
    assert page.lower().startswith("<!doctype html>")
    assert "<title>The &lt;Guide&gt; &amp; more</title>" in page
    assert 'href="../"' in page
    assert 'href="../dbt-docs/"' in page
    assert "<h1" in page


def test_a_javascript_link_renders_without_an_href(root, sources) -> None:
    write_guide(root, "index.md", "[click](javascript:alert(1)) and [also](JaVaScRiPt:alert(2))\n")
    page = guide_page(root, sources)
    assert not any(h.lower().startswith("javascript") for h in hrefs(page))
    assert "javascript:alert" not in "".join(hrefs(page))
    assert "<a href" not in page.split("<body>")[1].split("\n", 2)[2]


@pytest.mark.parametrize(
    "link",
    ["data:text/html,hi", "mailto:a@example.org", "vbscript:x", "file:///etc/passwd"],
)
def test_any_other_scheme_is_dropped_and_its_text_kept(root, sources, link) -> None:
    write_guide(root, "index.md", f"[kept text]({link})\n")
    page = guide_page(root, sources)
    assert "kept text" in page
    assert hrefs(page) == ["../", "../dbt-docs/"]


def test_a_sibling_guide_link_becomes_a_page_link_and_keeps_its_anchor(root, sources) -> None:
    write_guide(root, "index.md", "[a](other.md#part) [b](other.md) [c](#here)\n")
    write_guide(root, "other.md", "# Other\n")
    assert hrefs(guide_page(root, sources))[2:] == ["other.html#part", "other.html", "#here"]


@pytest.mark.parametrize("remote", [HTTPS_REMOTE, SSH_REMOTE, SCP_REMOTE])
def test_a_repo_link_becomes_the_blob_link_for_each_github_remote_form(
    root, sources, remote
) -> None:
    write_guide(root, "index.md", "[terms](../data-terms.md) [dir](../decisions/)\n")
    found = hrefs(guide_page(root, sources, remote=remote))
    assert found[2:] == [f"{BLOB}docs/data-terms.md", f"{BLOB}docs/decisions"]


def test_a_repo_link_is_plain_text_with_no_github_remote(root, sources) -> None:
    write_guide(root, "index.md", "[terms](../data-terms.md)\n")
    page = guide_page(root, sources, remote=None)
    assert "terms" in page
    assert "data-terms" not in page
    assert "github.com" not in page


def test_a_repo_link_is_plain_text_for_a_remote_that_is_not_on_github(root, sources) -> None:
    write_guide(root, "index.md", "[terms](../data-terms.md)\n")
    page = guide_page(root, sources, remote="https://gitlab.example.org/acme/starter.git")
    assert "data-terms" not in page


def test_a_link_that_climbs_out_of_the_repository_is_dropped(root, sources) -> None:
    write_guide(root, "index.md", "[up](../../../etc/passwd)\n")
    page = guide_page(root, sources, remote=HTTPS_REMOTE)
    assert "passwd" not in "".join(hrefs(page))


def test_https_and_http_links_pass_through(root, sources) -> None:
    write_guide(root, "index.md", "[a](https://example.org/x?a=1&b=2) [b](http://example.org/y)\n")
    assert hrefs(guide_page(root, sources))[2:] == [
        "https://example.org/x?a=1&amp;b=2",
        "http://example.org/y",
    ]


def test_no_emitted_href_starts_with_a_slash(root, sources) -> None:
    write_guide(
        root,
        "index.md",
        "[a](/abs/path.md) [b](//evil.example/x) [c](/) [d](other.md) [e](../x.md)\n",
    )
    write_guide(root, "other.md", "# Other\n")
    page = guide_page(root, sources, remote=HTTPS_REMOTE)
    assert not [h for h in hrefs(page) if h.startswith("/")]
    assert 'src="' not in page


def test_a_raw_script_in_the_markdown_is_escaped(root, sources) -> None:
    write_guide(
        root,
        "index.md",
        "# T\n\n<script>alert(1)</script>\n\ntext <img src=x onerror=alert(2)> more\n",
    )
    page = guide_page(root, sources)
    assert "<script" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page


def test_an_image_is_replaced_by_its_text_so_no_asset_is_loaded(root, sources) -> None:
    write_guide(root, "index.md", "![the alt](https://example.org/pic.png)\n")
    page = guide_page(root, sources)
    assert "<img" not in page
    assert "pic.png" not in page
    assert "the alt" in page


def test_a_table_and_a_fence_render(root, sources) -> None:
    write_guide(root, "index.md", "| a | b |\n|---|---|\n| 1 | 2 |\n\n```sql\nselect 1\n```\n")
    page = guide_page(root, sources)
    assert "<table>" in page
    assert "<pre><code" in page


def test_headings_get_github_style_ids_so_anchors_resolve(root, sources) -> None:
    write_guide(root, "index.md", "# Top\n\n## Two words\n\n## Two words\n")
    page = guide_page(root, sources)
    assert 'id="two-words"' in page
    assert 'id="two-words-1"' in page


def test_a_marker_block_is_absent_from_the_output(root, sources) -> None:
    write_guide(
        root,
        "index.md",
        f"# T\n\nbefore\n\n<!-- {BEGIN} -->\nonly for the example\n<!-- {END} -->\n\nafter\n",
    )
    page = guide_page(root, sources)
    assert "before" in page
    assert "after" in page
    assert "only for the example" not in page
    assert BEGIN not in page
    assert END not in page


def test_an_unclosed_marker_block_in_a_guide_is_refused_by_name(root, sources) -> None:
    write_guide(root, "index.md", f"<!-- {BEGIN} -->\ntext\n")
    with pytest.raises(site.SiteError, match="index.md"):
        site.assemble(root, sources)


def test_a_missing_renderer_reports_the_hint(root, sources, monkeypatch) -> None:
    write_guide(root, "index.md", "# Guides\n")
    monkeypatch.setitem(__import__("sys").modules, "markdown_it", None)
    with pytest.raises(site.SiteError) as caught:
        site.assemble(root, sources)
    assert str(caught.value) == site.MARKDOWN_HINT
    assert "uv sync" in site.MARKDOWN_HINT


def test_no_guides_does_not_need_the_renderer(root, sources, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "markdown_it", None)
    assert site.render_guides(root, None) == {}
    site.assemble(root, sources)


def test_main_notes_when_there_are_no_guides_and_keeps_stdout_to_one_line(
    root, sources, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(site, "repo_root", lambda: root)
    monkeypatch.setattr(site, "discover_sources", lambda: sources)
    monkeypatch.setattr(site, "git_remote_url", lambda _root: None)
    assert site.main([]) == 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    assert "no guides" in captured.err


def test_the_real_guide_index_renders_with_relative_links_only(sources) -> None:
    from starter_cli.project import repo_root

    pages = site.render_guides(repo_root(), HTTPS_REMOTE)
    assert "index.html" in pages
    assert [h for h in hrefs(pages["index.html"]) if h.startswith("/")] == []


# --- starter_cli.markers: the one marker definition ----------------------------------------------

FORMS = [
    ("<!-- {} -->", "<!-- {} -->"),
    ("# {}", "# {}"),
    ("{{#- {} -#}}", "{{#- {} -#}}"),
    ("{{# {} #}}", "{{# {} #}}"),
]


@pytest.mark.parametrize(("begin_form", "end_form"), FORMS)
def test_each_marker_form_strips_with_its_two_marker_lines(begin_form, end_form) -> None:
    text = f"keep one\n{begin_form.format(BEGIN)}\ndrop\n{end_form.format(END)}\nkeep two\n"
    assert example_blocks(text) == [(2, 4)]
    assert strip_example_blocks(text) == "keep one\nkeep two\n"


def test_an_indented_marker_is_a_marker() -> None:
    text = f"a:\n    # {BEGIN}\n    b: 1\n    # {END}\nc: 2\n"
    assert strip_example_blocks(text) == "a:\nc: 2\n"


def test_marker_text_inside_a_string_literal_on_a_code_line_is_not_a_marker() -> None:
    text = f'BEGIN = "{BEGIN}"\nEND = "{END}"\nprint("# {BEGIN}")\nx = 1  # {END}\n'
    assert example_blocks(text) == []
    assert strip_example_blocks(text) == text


def test_a_marker_mentioned_in_prose_is_not_a_marker() -> None:
    text = f"Wrap it in `<!-- {BEGIN} -->` and `<!-- {END} -->` lines.\n"
    assert example_blocks(text) == []


def test_a_nested_block_raises() -> None:
    with pytest.raises(MarkerError, match="inside the block opened on 1"):
        example_blocks(f"# {BEGIN}\n# {BEGIN}\n# {END}\n")


def test_an_unclosed_block_raises() -> None:
    with pytest.raises(MarkerError, match="never closed"):
        strip_example_blocks(f"a\n# {BEGIN}\nb\n")


def test_an_unopened_end_raises() -> None:
    with pytest.raises(MarkerError, match="no .* before it"):
        example_blocks(f"a\n# {END}\n")


def test_stripping_collapses_the_blank_runs_it_leaves_and_keeps_one_trailing_newline() -> None:
    text = f"one\n\n\n<!-- {BEGIN} -->\nx\n<!-- {END} -->\n\n\n\ntwo\n\n\n"
    assert strip_example_blocks(text) == "one\n\n\ntwo\n"


def test_text_without_a_block_comes_back_unchanged() -> None:
    assert strip_example_blocks("plain\n\n\n\ntext\n") == "plain\n\n\n\ntext\n"


def test_two_blocks_in_one_file_are_both_found() -> None:
    text = f"# {BEGIN}\na\n# {END}\nb\n# {BEGIN}\nc\n# {END}\n"
    assert example_blocks(text) == [(1, 3), (5, 7)]
    assert strip_example_blocks(text) == "b\n"
