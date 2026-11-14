"""User-visible contents of the Windows distribution, without rebuilding Python."""

import re
import shutil
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from tools.distribution import assemble_distribution

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GuideLinks(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.ids: set[str] = set()
        self.references: list[tuple[str, bool]] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id":
                self.ids.add(value)
            if name in {"href", "src"}:
                self.references.append((value, name == "src" or tag == "link"))


@pytest.fixture
def application_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "XIC_Extractor.exe").write_bytes(b"application")
    (bundle / "_internal" / "runtime.dll").write_bytes(b"dependency")
    # Only the app payload is published, even if the build folder has extras.
    (bundle / "developer-notes.txt").write_text("not a user document")
    return bundle


def test_distribution_has_offline_guides_examples_and_no_developer_tree(
    application_bundle: Path, tmp_path: Path
) -> None:
    archive = assemble_distribution(
        application_bundle, tmp_path / "XIC_Extractor-Windows-test.zip"
    )
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(unpacked)
    app = unpacked / archive.stem
    assert {p.name for p in app.iterdir()} == {
        "XIC_Extractor.exe",
        "開始使用.html",
        "_internal",
        "docs",
        "examples",
        "licenses",
        "output",
    }
    assert (app / "XIC_Extractor.exe").read_bytes() == b"application"
    assert (app / "_internal/runtime.dll").read_bytes() == b"dependency"
    assert not (app / "docs/user/system-guide.html").exists()
    assert not (app / "docs/agent").exists()
    assert not (app / "docs/product").exists()
    assert not (app / "docs/development").exists()
    assert not (app / "docs/user/index.html").exists()
    assert not (app / "docs/user/readers-index.html").exists()
    assert not list(app.rglob("*.architecture.json"))
    assert not list(app.rglob("*.py"))
    assert (app / "licenses/Archify.txt").read_bytes() == (
        PROJECT_ROOT / "docs/user/assets/archify-LICENSE.txt"
    ).read_bytes()
    assert "PyInstaller" in (app / "licenses/Third-party.txt").read_text("utf-8")
    assert any((app / "licenses/numpy").rglob("LICENSE*"))
    for name in ("targets.xlsx",):
        assert (app / "examples" / name).read_bytes() == (
            PROJECT_ROOT / "xic_extractor/configuration/data" / name
        ).read_bytes()

    # Follow the rendered links, not the renderer's destination table. This
    # catches missing images, Markdown/HTML name collisions and dead anchors.
    pages = {
        p.resolve(): GuideLinks(p.read_text(encoding="utf-8"))
        for p in app.rglob("*.html")
    }
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "uv run" not in text
        assert "development-reference" not in text
        assert "source checkout" not in text
    failures = []
    for page, links in pages.items():
        for reference, resource in links.references:
            url = urlsplit(reference)
            if url.scheme or url.netloc:
                if resource and url.scheme != "data":
                    failures.append(f"Network resource: {page.name}: {reference}")
                continue
            target = (page.parent / unquote(url.path)).resolve() if url.path else page
            if not target.is_relative_to(app.resolve()) or not target.exists():
                failures.append(f"Missing file: {page.name}: {reference}")
            elif url.fragment and target in pages:
                if unquote(url.fragment) not in pages[target].ids:
                    failures.append(f"Missing anchor: {page.name}: {reference}")
    assert not failures, "\n".join(failures)
    # Every page shares one stylesheet and lists every page in reading order.
    targeted = (app / "docs/user/targeted.html").read_text("utf-8")
    assert "assets/guide.css" in targeted
    assert "assets/targeted-workspace.png" in targeted
    for page in pages:
        nav = re.search(
            r'<nav aria-label="使用說明">(.*?)</nav>', page.read_text("utf-8")
        )
        assert nav and nav.group(1).count("<a ") == len(pages)
    assert not list((app / "docs").rglob("*.md"))


@pytest.mark.parametrize(
    "source_name", ("scripts/bootstrap.py", "xic_extractor/core.py")
)
def test_distribution_rejects_source_copied_as_runtime_data(
    application_bundle: Path, tmp_path: Path, source_name: str
) -> None:
    source = application_bundle / "_internal" / source_name
    source.parent.mkdir(parents=True)
    source.write_text("# development source", encoding="utf-8")
    with pytest.raises(ValueError, match="Python source"):
        assemble_distribution(application_bundle, tmp_path / "app.zip")
    assert not (tmp_path / "app.zip").exists()


def test_distribution_fails_when_a_documented_image_is_missing(
    application_bundle: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    shutil.copytree(PROJECT_ROOT / "docs", source / "docs")
    shutil.copytree(PROJECT_ROOT / "packaging", source / "packaging")
    shutil.copytree(
        PROJECT_ROOT / "xic_extractor/configuration/data",
        source / "xic_extractor/configuration/data",
    )
    (source / "docs/user/assets/targeted-workspace.png").unlink()
    with pytest.raises(FileNotFoundError):
        assemble_distribution(
            application_bundle, tmp_path / "app.zip", source_root=source
        )
    assert not (tmp_path / "app.zip").exists()
