"""Publish and read the application's single offline user guide.

Source checkouts render the authored user pages lazily. Wheels contain the same
rendered files; frozen applications read the files delivered beside the EXE.
No development documents or repository path is an HTTP resource.
"""

from __future__ import annotations

import mimetypes
import os
import re
import sys
from functools import cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

SOURCE_ROOT = Path(__file__).resolve().parents[1]
START_PAGE = Path("開始使用.html")

EXAMPLES = ("targets.xlsx",)

# Every authored Markdown page, in reading order, with its navigation label.
GUIDE_PAGES = (
    ("README.md", "開始使用"),
    ("targeted.md", "Targeted 分析"),
    ("target-list.md", "目標清單"),
    ("untargeted.md", "Untargeted 分析"),
    ("methods.md", "分析方法"),
    ("results.md", "結果檔案"),
    ("concepts.md", "判讀原理"),
)
THEME_SCRIPT = (
    "try{var t=localStorage.getItem('xic-theme')"
    "||(location.protocol==='file:'?'':'light');"
    "if(t)document.documentElement.dataset.theme=t}catch(e){}"
)


def _markdown_html(source: str, page: str) -> str:
    from markdown_it import MarkdownIt

    renderer = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens = renderer.parse(source)
    used_ids: dict[str, int] = {}
    for i, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        inline = tokens[i + 1]
        text = "".join(
            child.content
            for child in inline.children or []
            if child.type in {"text", "code_inline"}
        )
        slug = re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")
        occurrence = used_ids.get(slug, 0)
        used_ids[slug] = occurrence + 1
        token.attrSet("id", f"{slug}-{occurrence}" if occurrence else slug)
    body = renderer.renderer.render(tokens, renderer.options, {})
    body = body.replace("<table>", '<div class="table"><table>').replace(
        "</table>", "</table></div>"
    )
    title = next((t.content for t in tokens if t.type == "inline"), "XIC Extractor")
    if "XIC Extractor" not in title:
        title += " · XIC Extractor"
    names = [name for name, _ in GUIDE_PAGES]
    links = "".join(
        f'<a href="{name}"{' aria-current="page"' if name == page else ""}>'
        f"{escape(label)}</a>"
        for name, label in GUIDE_PAGES
    )
    pager = ""
    if page in names:
        index = names.index(page)
        if index > 0:
            name, label = GUIDE_PAGES[index - 1]
            pager += (
                f'<a class="prev" href="{name}"><span>上一頁</span>{escape(label)}</a>'
            )
        if index + 1 < len(names):
            name, label = GUIDE_PAGES[index + 1]
            pager += (
                f'<a class="next" href="{name}"><span>下一頁</span>{escape(label)}</a>'
            )
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title>"
        '<link rel="stylesheet" href="assets/guide.css">'
        f"<script>{THEME_SCRIPT}</script></head><body>"
        '<header class="topbar"><a class="brand" href="README.md">'
        "<b>XIC Extractor</b><span>使用說明</span></a>"
        f'<nav aria-label="使用說明">{links}</nav></header>'
        f'<main class="doc">{body}</main>'
        f'<footer class="pager">{pager}</footer></body></html>'
    )


class _OfflineLinks(HTMLParser):
    """Rewrite source links and collect only the assets used by published pages."""

    def __init__(self, source: Path, destinations: dict[Path, Path]):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.destinations = destinations
        self.output: list[str] = []
        self.assets: set[Path] = set()

    def _link(self, value: str, *, resource: bool) -> str:
        url = urlsplit(value)
        if url.scheme or url.netloc:
            if resource and url.scheme != "data":
                raise ValueError(f"Offline guide loads a network resource: {value}")
            return value
        if not url.path:
            return value
        target = Path(os.path.normpath(self.source.parent / unquote(url.path)))
        if target not in self.destinations:
            if target.parts[:3] == ("docs", "user", "assets") and target.suffix in {
                ".png",
                ".svg",
                ".css",
            }:
                self.destinations[target] = target
                self.assets.add(target)
            else:
                raise ValueError(f"Unpublished local link in {self.source}: {value}")
        destination = self.destinations[target]
        relative = os.path.relpath(
            destination, self.destinations[self.source].parent
        ).replace(os.sep, "/")
        return urlunsplit(("", "", quote(relative), url.query, url.fragment))

    def handle_starttag(self, tag, attrs):
        rewritten = []
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                value = self._link(value, resource=name == "src" or tag == "link")
            rewritten.append((name, value))
        attributes = "".join(
            f' {name}="{escape(value, quote=True)}"'
            if value is not None
            else f" {name}"
            for name, value in rewritten
        )
        self.output.append(f"<{tag}{attributes}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.output[-1] = self.output[-1][:-1] + "/>"

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")

    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")

    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")


class _GuideReferences(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.ids: set[str] = set()
        self.references: list[tuple[str, bool]] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value is None:
                continue
            if name == "id":
                self.ids.add(value)
            if name in {"href", "src"}:
                self.references.append((value, name == "src" or tag == "link"))


def render_user_guides(source_root: Path) -> dict[Path, bytes]:
    """Render the selected user pages and their linked assets for publication."""
    destinations = {}
    for name, _ in GUIDE_PAGES:
        source = Path("docs/user") / name
        destinations[source] = (
            START_PAGE if name == "README.md" else source.with_suffix(".html")
        )
    for name in EXAMPLES:
        destinations[Path("xic_extractor/configuration/data") / name] = (
            Path("examples") / name
        )
    rendered = {}
    assets = set()
    for source, destination in list(destinations.items()):
        if destination.parts[0] == "examples":
            continue
        text = (source_root / source).read_text(encoding="utf-8")
        html = _markdown_html(text, source.name)
        links = _OfflineLinks(source, destinations)
        links.feed(html)
        links.close()
        assets.update(links.assets)
        html = "".join(links.output)
        rendered[destination] = html.encode("utf-8")
    for source in assets | {
        Path("xic_extractor/configuration/data") / name for name in EXAMPLES
    }:
        rendered[destinations[source]] = (source_root / source).read_bytes()
    rendered[Path("licenses/Archify.txt")] = (
        source_root / "docs/user/assets/archify-LICENSE.txt"
    ).read_bytes()
    _check_links(rendered)
    return rendered


def _check_links(rendered: dict[Path, bytes]) -> None:
    pages = {
        p: _GuideReferences(data.decode("utf-8"))
        for p, data in rendered.items()
        if p.suffix == ".html"
    }
    for page, links in pages.items():
        for reference, resource in links.references:
            url = urlsplit(reference)
            if url.scheme or url.netloc:
                if resource and url.scheme != "data":
                    raise ValueError(
                        f"Offline guide loads a network resource: {reference}"
                    )
                continue
            target = (
                Path(os.path.normpath(page.parent / unquote(url.path)))
                if url.path
                else page
            )
            if target not in rendered:
                raise ValueError(f"Missing offline file in {page.name}: {reference}")
            if (
                url.fragment
                and target in pages
                and unquote(url.fragment) not in pages[target].ids
            ):
                raise ValueError(f"Missing offline anchor in {page.name}: {reference}")


@cache
def _source_guides() -> dict[Path, bytes]:
    return render_user_guides(SOURCE_ROOT)


def read_user_guide(relative_url: str) -> tuple[bytes, str] | None:
    """Read a public help resource, never an arbitrary application file."""
    value = unquote(relative_url) or START_PAGE.as_posix()
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.drive
        or ".." in relative.parts
        or "\\" in value
    ):
        return None
    allowed = (
        relative == START_PAGE
        or (
            relative.parts[:2] == ("docs", "user")
            and relative.suffix in {".html", ".css", ".png", ".svg"}
        )
        or (
            relative.parts[:1] == ("examples",)
            and relative.name in EXAMPLES
            and len(relative.parts) == 2
        )
    )
    if not allowed:
        return None
    if (
        not getattr(sys, "frozen", False)
        and (SOURCE_ROOT / "docs/user/README.md").is_file()
    ):
        data = _source_guides().get(relative)
        if data is None:
            return None
    else:
        directory = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).with_name("assets") / "help"
        )
        resource = (directory / relative).resolve()
        if not resource.is_relative_to(directory.resolve()) or not resource.is_file():
            return None
        data = resource.read_bytes()
    content_type = mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
    if relative.suffix in {".html", ".css", ".svg"}:
        content_type += "; charset=utf-8"
    return data, content_type
