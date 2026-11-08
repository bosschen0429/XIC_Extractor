"""Assemble the Windows application ZIP, including its offline user guides.

The release workflow and clean-source packaging test use this same entry point.
This is build tooling; none of it is needed by the delivered application.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement

from xic_workbench.user_guide import render_user_guides

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_licenses(source_root: Path, stage: Path) -> None:
    licenses = stage / "licenses"
    licenses.mkdir(exist_ok=True)
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
        for relative, data in render_user_guides(source_root).items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _write_licenses(source_root, stage)
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
