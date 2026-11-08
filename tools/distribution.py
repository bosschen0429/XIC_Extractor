"""Assemble the Windows application ZIP, including its offline user guides.

The release workflow and clean-source packaging test use this same entry point.
This is build tooling; none of it is needed by the delivered application.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import tomllib
from html import escape
from html.parser import HTMLParser
from importlib.metadata import distribution
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_PAGE = Path("開始使用.html")

# This is the publication selection, not a copy of the repository's docs tree.
USER_PAGES = (
    "README.md",
    "readers-index.html",
    "targeted-extraction.md",
    "targeted-extraction.html",
    "targeted-config-reference.md",
    "targeted-output-reference.md",
    "untargeted-discovery.md",
    "untargeted-discovery.html",
    "untargeted-preset-guide.md",
    "untargeted-output-reference.md",
    "core-modules-explainer.html",
    "processing-guide.html",
    "raw-to-final-matrix-product-story.html",
)
PRODUCT_PAGES = (
    "README.md",
    "alignment.md",
    "backfill.md",
    "decision-policy.md",
    "discovery.md",
    "domain-glossary.md",
    "instrument-qc-calibration.md",
    "matrix-curation.md",
    "presets.md",
    "quant-matrix.md",
    "sample-metadata-qc.md",
    "signal-processing.md",
    "targeted-selection.md",
    "targeted-workbook.md",
    "untargeted-method.md",
    "workbench.md",
)
EXAMPLES = ("targeted-method.xlsx", "targets.xlsx", "targets.csv")
PAGE_STYLE = """
body { margin: 2rem auto; padding: 0 1.5rem; max-width: 76rem;
  font: 17px/1.7 system-ui, 'Microsoft JhengHei', sans-serif; color: #243342; }
a { color: #145b90; } pre { padding: 1rem; overflow: auto; background: #f2f5f7; }
code { font-size: .9em; } table { border-collapse: collapse; width: 100%; }
th, td { padding: .5rem; border: 1px solid #ccd4dc; text-align: left; }
img, svg { max-width: 100%; height: auto; } h1, h2, h3 { line-height: 1.3; }
.development-reference { color: #67717a; }
"""


def _markdown_html(source: str) -> str:
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
    title = next((t.content for t in tokens if t.type == "inline"), "XIC Extractor")
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{PAGE_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


class _OfflineLinks(HTMLParser):
    """Rewrite source links and collect only the assets used by published pages."""

    def __init__(self, source: Path, destinations: dict[Path, Path]):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.destinations = destinations
        self.output: list[str] = []
        self.assets: set[Path] = set()
        self.anchor_tags: list[str] = []

    def _link(self, value: str, *, resource: bool) -> str | None:
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
            elif not resource and (
                target.parts[:2] == ("docs", "agent")
                or target.parts[0] in {"xic_extractor", "tools", "scripts"}
                or target
                in {
                    Path("AGENTS.md"),
                    Path("docs/architecture-contract.md"),
                    Path("docs/user/system-guide.html"),
                    Path("docs/user/assets/README.md"),
                }
            ):
                return None
            else:
                raise ValueError(f"Unpublished local link in {self.source}: {value}")
        destination = self.destinations[target]
        relative = os.path.relpath(
            destination, self.destinations[self.source].parent
        ).replace(os.sep, "/")
        # The repo README's source-install anchors do not apply to the app package.
        fragment = "" if target == Path("README.md") else url.fragment
        return urlunsplit(("", "", quote(relative), url.query, fragment))

    def handle_starttag(self, tag, attrs):
        rewritten = []
        omitted = False
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                value = self._link(value, resource=name == "src" or tag == "link")
                if value is None:
                    omitted = True
                    continue
            rewritten.append((name, value))
        if tag == "a":
            tag = "span" if omitted else "a"
            self.anchor_tags.append(tag)
            if omitted:
                rewritten += [
                    ("class", "development-reference"),
                    ("title", "開發參考，不隨使用者版本提供"),
                ]
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
        if tag == "a":
            tag = self.anchor_tags.pop()
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


def _write_guides(source_root: Path, stage: Path) -> None:
    destinations = {Path("packaging/quick-start.md"): START_PAGE}
    for directory, pages in (("user", USER_PAGES), ("product", PRODUCT_PAGES)):
        for name in pages:
            source = Path("docs") / directory / name
            if name == "README.md":
                destination = source.with_name("index.html")
            elif source.suffix == ".md":
                # Illustrated HTML and detailed Markdown guides share a stem.
                destination = source.with_suffix(".guide.html")
            else:
                destination = source
            destinations[source] = destination
    for name in EXAMPLES:
        destinations[Path("xic_extractor/configuration/data") / name] = (
            Path("examples") / name
        )
    destinations[Path("README.md")] = START_PAGE
    assets: set[Path] = set()
    for source, destination in list(destinations.items()):
        if source == Path("README.md") or destination.parts[0] == "examples":
            continue
        text = (source_root / source).read_text(encoding="utf-8")
        if source == Path("docs/product/README.md"):
            # The index's contributor policy is not part of the end-user guide.
            text = text.split("## Authority Boundary", 1)[0]
        html = _markdown_html(text) if source.suffix == ".md" else text
        links = _OfflineLinks(source, destinations)
        links.feed(html)
        links.close()
        assets.update(links.assets)
        home = quote(
            os.path.relpath(START_PAGE, destination.parent).replace(os.sep, "/")
        )
        index = quote(
            os.path.relpath(
                destinations[Path("docs/user/README.md")], destination.parent
            ).replace(os.sep, "/")
        )
        nav = (
            '<nav style="padding:12px;border-bottom:1px solid #ccd4dc;'
            'font:16px system-ui;background:#fff;color:#243342">'
            f'<a href="{home}">開始使用</a> · '
            f'<a href="{index}">離線指南</a></nav>'
        )
        html = re.sub(
            r"(<body\b[^>]*>)",
            r"\g<1>" + nav,
            "".join(links.output),
            count=1,
        )
        target = stage / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    for source in assets | {
        Path("xic_extractor/configuration/data") / name for name in EXAMPLES
    }:
        target = stage / destinations[source]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / source, target)


def _write_licenses(source_root: Path, stage: Path) -> None:
    licenses = stage / "licenses"
    licenses.mkdir()
    shutil.copy2(
        source_root / "docs/user/assets/archify-LICENSE.txt", licenses / "Archify.txt"
    )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python.txt")
    metadata = tomllib.loads(
        (source_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    pending = [Requirement(s) for s in metadata["project"]["dependencies"]]
    pending.append(Requirement("pyinstaller"))  # Includes the bootloader exception.
    seen: set[tuple[str, tuple[str, ...]]] = set()
    notices: dict[str, str] = {"Python": sys.version + "\n" + sys.copyright}
    while pending:
        requirement = pending.pop()
        key = (requirement.name.lower(), tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        package = distribution(requirement.name)
        name = package.metadata["Name"]
        notices[name] = f"{name} {package.version}\n" + (
            package.metadata.get("License-Expression")
            or package.metadata.get("License")
            or ""
        )
        for file in package.files or ():
            if file.suffix.lower() not in {"", ".txt", ".md", ".rst"}:
                continue
            if "licenses" not in file.parts and not file.name.upper().startswith(
                ("LICENSE", "COPYING", "NOTICE", "AUTHORS")
            ):
                continue
            parts = file.parts
            if "licenses" in parts:
                parts = parts[parts.index("licenses") + 1 :]
            elif any(p.endswith(".dist-info") for p in parts):
                parts = parts[1:]
            target = licenses / name / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(package.locate_file(file)), target)
        for dependency in package.requires or ():
            child = Requirement(dependency)
            if child.marker is None or any(
                child.marker.evaluate({"extra": extra})
                for extra in {"", *requirement.extras}
            ):
                pending.append(child)
    (licenses / "Third-party.txt").write_text(
        "\n\n".join(notices[name] for name in sorted(notices)), encoding="utf-8"
    )


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


def _check_offline_guides(stage: Path) -> None:
    pages = {
        p.resolve(): _GuideReferences(p.read_text(encoding="utf-8"))
        for p in stage.rglob("*.html")
        if "_internal" not in p.relative_to(stage).parts
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
            target = (page.parent / unquote(url.path)).resolve() if url.path else page
            if not target.is_relative_to(stage.resolve()) or not target.exists():
                raise ValueError(f"Missing offline file in {page.name}: {reference}")
            if url.fragment and target in pages:
                if unquote(url.fragment) not in pages[target].ids:
                    raise ValueError(
                        f"Missing offline anchor in {page.name}: {reference}"
                    )


def assemble_distribution(
    bundle_dir: Path, archive_path: Path, *, source_root: Path = PROJECT_ROOT
) -> Path:
    """Publish the built application and selected guides as one portable ZIP.

    The bundle is PyInstaller's one-folder Windows output. Existing archives are
    replaced only after assembly succeeds. Project/development source is never
    a data resource; ordinary Python imports stay compiled inside the bundle.
    """
    archive_path = archive_path.resolve()
    if archive_path.suffix != ".zip":
        raise ValueError("The distribution archive must have a .zip extension")
    source_files = [
        p.relative_to(bundle_dir)
        for p in bundle_dir.rglob("*")
        if p.suffix.lower() in {".py", ".pyw", ".pyi"}
    ]
    if source_files:
        raise ValueError(f"Python source in application bundle: {source_files[:5]}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="xic-distribution-", dir=archive_path.parent
    ) as tmp:
        stage = Path(tmp) / archive_path.stem
        stage.mkdir()
        shutil.copy2(bundle_dir / "XIC_Extractor.exe", stage / "XIC_Extractor.exe")
        shutil.copytree(bundle_dir / "_internal", stage / "_internal")
        (stage / "output").mkdir()
        _write_guides(source_root, stage)
        _write_licenses(source_root, stage)
        _check_offline_guides(stage)
        archive = shutil.make_archive(
            str(Path(tmp) / "assembled"), "zip", tmp, stage.name
        )
        Path(archive).replace(archive_path)
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path, help="PyInstaller one-folder output")
    parser.add_argument("archive_path", type=Path, help="Destination Windows .zip")
    args = parser.parse_args()
    print(assemble_distribution(args.bundle_dir, args.archive_path))


if __name__ == "__main__":
    main()
