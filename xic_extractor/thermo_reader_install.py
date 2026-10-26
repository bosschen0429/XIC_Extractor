"""Install the Thermo RawFileReader 8 assemblies that the RAW reader loads."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

THERMO_READER_VERSION = "8.0.42"
THERMO_LICENSE_URL = (
    "https://github.com/thermofisherlsms/RawFileReader/blob/"
    "04353016fa6047d0525938f42020725bdeaadc2c/License.doc"
)
_THERMO_NET8_LIBS = (
    "https://raw.githubusercontent.com/thermofisherlsms/RawFileReader/"
    "47eeda0d54a9ca28ace8e6548b7c01b6843207c2/Libs/NetCore/Net8"
)


@dataclass(frozen=True)
class _Package:
    url: str
    sha256: str
    member: str


# Thermo publishes RawFileReader outside NuGet; OpenMcdf is its NuGet dependency.
_PACKAGES = (
    _Package(
        f"{_THERMO_NET8_LIBS}/thermofisher.commoncore.rawfilereader.8.0.42.nupkg",
        "af259d466e24227ba6706872330a0736d6fc2f552ad9d46a78a4697f0f62e1a6",
        "lib/net8.0/ThermoFisher.CommonCore.RawFileReader.dll",
    ),
    _Package(
        f"{_THERMO_NET8_LIBS}/thermofisher.commoncore.data.8.0.42.nupkg",
        "0b1d2fff2f81532e0c56471b22bc0e2c0c6685f37aa43b0b134ad82ae75ae444",
        "lib/net8.0/ThermoFisher.CommonCore.Data.dll",
    ),
    _Package(
        "https://api.nuget.org/v3-flatcontainer/openmcdf/3.1.4/openmcdf.3.1.4.nupkg",
        "fa9290d5491361b7c69c5fbe7ce1c6be5a08f7d6d2ad4e8d2940f80224b73cc4",
        "lib/net8.0/OpenMcdf.dll",
    ),
)


class ThermoReaderInstallError(ValueError):
    """The RawFileReader assemblies could not be installed."""


def default_thermo_reader_dir() -> Path:
    """Per-user folder; installing needs no administrator rights."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "XIC_Extractor" / f"RawFileReader-{THERMO_READER_VERSION}"


def install_thermo_reader(
    destination: Path | None = None,
    *,
    accept_license: bool,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """Download the pinned packages, verify them, and place their DLLs together.

    Download and verify every package before changing installed DLLs. Only the
    reader's files are replaced; a custom destination may hold unrelated data.
    """
    if not accept_license:
        raise ThermoReaderInstallError(
            f"Accept the Thermo RawFileReader license first: {THERMO_LICENSE_URL}"
        )
    read = fetch or _download
    target = destination or default_thermo_reader_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for package in _PACKAGES:
            payload = read(package.url)
            if hashlib.sha256(payload).hexdigest() != package.sha256:
                raise ThermoReaderInstallError(
                    f"Download does not match its pinned SHA-256: {package.url}"
                )
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                (staging / Path(package.member).name).write_bytes(
                    archive.read(package.member)
                )
        target.mkdir(exist_ok=True)
        for dll in staging.iterdir():
            dll.replace(target / dll.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _download(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return bytes(response.read())
