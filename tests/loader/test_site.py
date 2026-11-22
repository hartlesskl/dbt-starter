"""Site assembly and the raw-file cache key, offline: no network, no DuckDB, no real `site/`."""

from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from starter_cli import site
from starter_cli.landing import EntitySpec

STATIC_BYTES = b"<html><body>the static dbt docs, with \xc3\xa9 and a script</body></html>"
ATTRIBUTION = ("Acme Public Parts Register", "https://example.org/parts")

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
    return {"acme": make_source(DOCS_ATTRIBUTION=ATTRIBUTION)}


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
