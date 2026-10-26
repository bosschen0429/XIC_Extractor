import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from xic_extractor import thermo_reader_install as install


def _package(member: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
        archive.writestr("lib/netstandard2.0/Unused.dll", b"other target")
    return buffer.getvalue()


@pytest.fixture
def packages(monkeypatch):
    # The network boundary: two pinned packages served from memory.
    served = {
        "https://example.test/reader.nupkg": _package(
            "lib/net8.0/Reader.dll", b"reader"
        ),
        "https://example.test/data.nupkg": _package("lib/net8.0/Data.dll", b"data"),
    }
    monkeypatch.setattr(
        install,
        "_PACKAGES",
        tuple(
            install._Package(url, hashlib.sha256(body).hexdigest(), member)
            for (url, body), member in zip(
                served.items(),
                ("lib/net8.0/Reader.dll", "lib/net8.0/Data.dll"),
                strict=True,
            )
        ),
    )
    return served


def test_install_requires_license_acceptance_before_any_download(tmp_path: Path):
    def fetch(url: str) -> bytes:
        raise AssertionError(f"downloaded before acceptance: {url}")

    with pytest.raises(install.ThermoReaderInstallError, match="License.doc"):
        install.install_thermo_reader(
            tmp_path / "reader", accept_license=False, fetch=fetch
        )
    assert list(tmp_path.iterdir()) == []


def test_install_places_only_the_net8_assemblies_and_replaces_old_files(
    tmp_path: Path, packages
):
    target = tmp_path / "reader"
    target.mkdir()
    (target / "Stale.dll").write_bytes(b"old")

    result = install.install_thermo_reader(
        target, accept_license=True, fetch=packages.__getitem__
    )

    assert result == target
    assert {path.name: path.read_bytes() for path in target.iterdir()} == {
        "Reader.dll": b"reader",
        "Data.dll": b"data",
    }
    assert list(tmp_path.iterdir()) == [target]


def test_tampered_download_leaves_the_existing_install_untouched(
    tmp_path: Path, packages
):
    target = tmp_path / "reader"
    target.mkdir()
    (target / "Reader.dll").write_bytes(b"working")
    served = dict(packages)
    served["https://example.test/data.nupkg"] = _package(
        "lib/net8.0/Data.dll", b"tampered"
    )

    with pytest.raises(install.ThermoReaderInstallError, match="SHA-256"):
        install.install_thermo_reader(
            target, accept_license=True, fetch=served.__getitem__
        )

    assert [path.name for path in tmp_path.iterdir()] == ["reader"]
    assert (target / "Reader.dll").read_bytes() == b"working"
