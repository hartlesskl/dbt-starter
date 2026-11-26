"""`uv run site`: assemble the GitHub Pages site from the static dbt docs the demo writes.

Layout of the folder this command writes (`site/`, gitignored build output):

    site/index.html             a small root page: what the docs are, the data they were built
                                from, and a link to the docs
    site/dbt-docs/index.html    the single-file docs from `dbt docs generate --static`
    site/guide/<name>.html      each `docs/guide/<name>.md`, rendered with markdown-it-py (present
                                only when the checkout has guides)

Every link on the pages is relative, because a project site is served under `/<repository>/`, so
an absolute `/dbt-docs/` would point at the owner's site root and not at this repository's pages.

The guides are rendered with the raw HTML of the Markdown switched off, so a guide cannot inject a
script, and no page loads an asset from another host. Links in a guide follow one rule: `http` and
`https` links pass through, a link to a sibling guide becomes a link to its page, and any other
relative link becomes a GitHub link to that file in this repository (built from the origin remote),
or plain text when the remote is not a GitHub one. A link with any other scheme, such as
`javascript:`, is dropped and its text kept. Example marker blocks are cut out before rendering
(`starter_cli.markers`), so a guide never shows a marker line.

The command is generic: it never names a data source. Each source module declares the public data
it was built from in `DOCS_ATTRIBUTION = (name, https link)`, and a source that declares none is
refused. An adopter who swaps in private data must not publish its metadata by accident, so docs
of undeclared data are never assembled (D-12, Pitfall 29).

`uv run site --cache-key` prints the key the Pages workflow uses to cache the downloaded files. It
is derived only from the default file names of every source entity, so it changes when a default
month or file name changes and not otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import posixpath
import re
import shutil
import sys
import types
from pathlib import Path
from urllib.parse import quote, urlsplit

from starter_cli.markers import MarkerError, strip_example_blocks
from starter_cli.project import (
    LICENSE_NOTE,
    discover_sources,
    git_remote_url,
    github_repo,
    repo_root,
)

SITE_DIR = "site"
DOCS_DIR = "dbt-docs"
STATIC_INDEX = "target/static_index.html"
# Bump this to discard every cached download at once (a corrupted cache entry, a changed
# download path): the version is part of the key, so the next run starts from an empty cache.
CACHE_VERSION = "v1"
GUIDE_DIR = "guide"
GUIDE_SOURCE = "docs/guide"
MARKDOWN_HINT = (
    "the Markdown renderer (markdown-it-py) is not installed, so the guides cannot be rendered. "
    "Run `uv sync`: the dev group holds it."
)
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class SiteError(Exception):
    """The site cannot be assembled; the message says what to do."""


def attributions(sources: dict[str, types.ModuleType]) -> list[tuple[str, str]]:
    """Return each source's `DOCS_ATTRIBUTION` as (name, https link), in source-name order.

    A source without a declaration, or with anything but a non-empty name and an `https://` link,
    is an error that names it: published docs must credit public data.
    """
    found: list[tuple[str, str]] = []
    for source, module in sorted(sources.items()):
        declared = getattr(module, "DOCS_ATTRIBUTION", None)
        valid = (
            isinstance(declared, tuple)
            and len(declared) == 2
            and all(isinstance(part, str) and part.strip() for part in declared)
            and declared[1].startswith("https://")
        )
        if not valid:
            raise SiteError(
                f"source {source!r} does not declare DOCS_ATTRIBUTION = (name, https link), so "
                "its docs are not published. Declare the public data it comes from, or leave "
                "the Pages workflow off."
            )
        found.append((declared[0], declared[1]))
    if not found:
        raise SiteError("no data source was found, so there is nothing to credit on the site.")
    return found


def credit_lines(sources: dict[str, types.ModuleType]) -> list[str]:
    """Return each source's `DATA_TERMS["credit_line"]`, in source-name order.

    A source that declares no `DATA_TERMS` adds none. One that declares it without a non-empty
    credit line is an error that names it, because a page must not silently drop a credit.
    """
    found: list[str] = []
    for source, module in sorted(sources.items()):
        if not hasattr(module, "DATA_TERMS"):
            continue
        line = module.DATA_TERMS.get("credit_line") if isinstance(module.DATA_TERMS, dict) else None
        if not isinstance(line, str) or not line.strip():
            raise SiteError(
                f"source {source!r} declares DATA_TERMS without a credit_line, so its docs are "
                "not published. Add the credit line, as docs/data-terms.md describes."
            )
        found.append(line)
    return found


def render_index(
    credits: list[tuple[str, str]], terms: list[str] | None = None, guides: bool = False
) -> str:
    """Return the root page: no script, no external asset and no absolute path.

    `terms` are the sources' credit lines (link, access date and disclaimer), printed before the
    code-only license note. Every text is HTML-escaped, so a disclaimer holding markup is shown
    as text. `guides` adds the link to the rendered guides.
    """
    guide_link = '\n    <p><a href="guide/">Read the guides</a></p>' if guides else ""
    items = "\n".join(
        f'      <li><a href="{html.escape(url, quote=True)}">{html.escape(name, quote=True)}</a></li>'
        for name, url in credits
    )
    notes = "\n".join(f"    <p>{html.escape(line, quote=True)}</p>" for line in terms or [])
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>dbt-starter docs</title>
  </head>
  <body>
    <h1>dbt-starter docs</h1>
    <p>These docs were generated by <code>dbt docs generate --static</code> from a real build on
    DuckDB of the public data below.</p>
    <ul>
{items}
    </ul>
{notes}
    <p>{html.escape(LICENSE_NOTE, quote=True)}</p>
    <p><a href="dbt-docs/">Open the dbt docs</a></p>{guide_link}
  </body>
</html>
"""


def rewrite_link(
    href: str, blob_base: str | None, guides: frozenset[str] = frozenset()
) -> str | None:
    """Return the href a guide link gets on the site, or None when the link is not kept.

    `blob_base` is `https://github.com/<owner>/<repo>/blob/HEAD/` or None without a GitHub
    remote; `guides` holds the file names in `docs/guide`. `http` and `https` pass through, `#a`
    stays, `x.md` or `x.md#a` naming a guide becomes `x.html` (plus the anchor), and any other
    relative path is resolved against `docs/guide/` to a repository path and becomes a blob link,
    or None without a base. Any other scheme (`javascript:`, `data:`, `mailto:`), an absolute path
    and a path that climbs out of the repository give None, so no emitted href starts with `/`.
    """
    href = href.strip()
    if not href:
        return None
    scheme = _SCHEME_RE.match(href)
    if scheme:
        return href if scheme.group(0).lower() in {"http:", "https:"} else None
    if href.startswith(("/", "\\")):
        return None
    parts = urlsplit(href)
    anchor = f"#{parts.fragment}" if parts.fragment else ""
    if not parts.path:
        return anchor or None
    resolved = posixpath.normpath(posixpath.join(GUIDE_SOURCE, parts.path))
    if resolved.startswith(("..", "/")):
        return None
    folder, _, name = resolved.rpartition("/")
    if folder == GUIDE_SOURCE and name in guides:
        return f"{name[: -len('.md')]}.html{anchor}"
    if blob_base is None:
        return None
    return f"{blob_base}{quote(resolved)}{anchor}"


def _slug(text: str, seen: dict[str, int]) -> str:
    """Return the heading id GitHub would give `text`: lower case, no punctuation, hyphens."""
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-") or "section"
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def _prepare_tokens(tokens: list, blob_base: str | None, guides: frozenset[str]) -> str:
    """Rewrite links, drop images to their text and give headings ids; return the page title."""
    from markdown_it.token import Token

    seen: dict[str, int] = {}
    title = ""
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and index + 1 < len(tokens):
            text = tokens[index + 1].content
            token.attrSet("id", _slug(text, seen))
            if token.tag == "h1" and not title:
                title = text
        if token.type != "inline" or not token.children:
            continue
        kept = []
        dropped: list[bool] = []
        for child in token.children:
            if child.type == "link_open":
                target = rewrite_link(str(child.attrGet("href") or ""), blob_base, guides)
                dropped.append(target is None)
                if target is None:
                    continue
                child.attrs = {"href": target}
            elif child.type == "link_close":
                if dropped.pop() if dropped else False:
                    continue
            elif child.type == "image":
                text_token = Token("text", "", 0)
                text_token.content = child.content
                child = text_token
            kept.append(child)
        token.children = kept
    return title


def _guide_page(title: str, body: str) -> str:
    """Wrap rendered guide HTML in the same minimal page as the root page, plus a nav line."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title, quote=True)}</title>
  </head>
  <body>
    <p><a href="../">Home</a> | <a href="../dbt-docs/">dbt docs</a></p>
{body}  </body>
</html>
"""


def render_guides(root: Path, remote: str | None) -> dict[str, str]:
    """Render every `docs/guide/*.md` and return {`<name>.html`: page}, in name order.

    No guide folder, or no Markdown file in it, gives an empty dict. `remote` is the origin URL
    used to build GitHub links for files that are not guides. A missing renderer is a SiteError
    that says to run `uv sync`, and a marker block that is nested or unclosed names the guide.
    """
    source = root / GUIDE_SOURCE
    files = sorted(source.glob("*.md")) if source.is_dir() else []
    if not files:
        return {}
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise SiteError(MARKDOWN_HINT) from error
    found = github_repo(remote)
    blob_base = None if found is None else f"https://github.com/{found[0]}/{found[1]}/blob/HEAD/"
    guides = frozenset(path.name for path in files)
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    pages: dict[str, str] = {}
    for path in files:
        try:
            text = strip_example_blocks(path.read_text(encoding="utf-8"))
        except (MarkerError, UnicodeDecodeError) as error:
            raise SiteError(f"{GUIDE_SOURCE}/{path.name}: {error}") from error
        tokens = renderer.parse(text)
        title = _prepare_tokens(tokens, blob_base, guides) or path.stem
        body = renderer.renderer.render(tokens, renderer.options, {})
        pages[f"{path.stem}.html"] = _guide_page(title, body)
    return pages


def _output_folder(root: Path) -> Path:
    """Return `<root>/site`, refusing a path that resolves outside the checkout or is the root."""
    folder = root / SITE_DIR
    resolved = folder.resolve()
    base = root.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise SiteError(f"{folder} resolves to {resolved}, outside the checkout {base}; refusing.")
    return folder


def assemble(root: Path, sources: dict[str, types.ModuleType], remote: str | None = None) -> Path:
    """Write `<root>/site` from `<root>/target/static_index.html` and return the folder.

    The rendered guides go to `site/guide/`, and the root page links them only when there are
    some. `remote` is the origin URL for the GitHub links in the guides.
    """
    credits = attributions(sources)
    terms = credit_lines(sources)
    guides = render_guides(root, remote)
    static = root / STATIC_INDEX
    if not static.is_file():
        raise SiteError(f"{STATIC_INDEX} not found. Run `uv run demo` first: it writes the docs.")
    folder = _output_folder(root)
    if folder.is_symlink():
        folder.unlink()
    elif folder.exists():
        shutil.rmtree(folder)
    (folder / DOCS_DIR).mkdir(parents=True)
    shutil.copyfile(static, folder / DOCS_DIR / "index.html")
    if guides:
        (folder / GUIDE_DIR).mkdir()
        for name, page in guides.items():
            (folder / GUIDE_DIR / name).write_text(page, encoding="utf-8")
    (folder / "index.html").write_text(render_index(credits, terms, bool(guides)), encoding="utf-8")
    return folder


def raw_cache_key(sources: dict[str, types.ModuleType]) -> str:
    """Return the key for the cache of downloaded files, from default file names only."""
    names: list[str] = []
    for _, module in sorted(sources.items()):
        for _, spec in sorted(module.ENTITIES.items()):
            if spec.is_monthly:
                names.extend(spec.file_name_for(month) for month in spec.default_months)
            else:
                names.append(spec.file_name)
    digest = hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()
    return f"raw-{CACHE_VERSION}-{digest[:16]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="site",
        description=(
            "Assemble the Pages site (site/index.html, site/dbt-docs/index.html and the rendered "
            "guides in site/guide/) from the static docs that `uv run demo` writes, or with "
            "--cache-key print the key of the download cache."
        ),
    )
    parser.add_argument(
        "--cache-key",
        action="store_true",
        help="print the cache key for the downloaded files and exit",
    )
    args = parser.parse_args(argv)

    sources = discover_sources()
    if args.cache_key:
        print(raw_cache_key(sources))
        return 0
    try:
        root = repo_root()
        folder = assemble(root, sources, git_remote_url(root))
    except SiteError as error:
        print(f"site: {error}", file=sys.stderr)
        return 2
    print(f"site: wrote {folder / 'index.html'} and {folder / DOCS_DIR / 'index.html'}")
    if not (folder / GUIDE_DIR).is_dir():
        print(f"site: no guides found in {GUIDE_SOURCE}/, so none were rendered", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
