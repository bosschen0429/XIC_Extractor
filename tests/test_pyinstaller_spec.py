import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

import pytest
from PyInstaller.utils.hooks import collect_data_files

from xic_extractor.presets import list_presets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_RESOURCE_RELATIVE = (
    Path("xic_extractor")
    / "alignment"
    / "data"
    / "issue256_quantitative_projection_authority_v1.json"
)
AUTHORITY_RESOURCE_ARCHIVE_PATH = PurePosixPath(*AUTHORITY_RESOURCE_RELATIVE.parts)
AUTHORITY_RESOURCE_SHA256 = (
    "15b74bc79dc9501210acd5f761c86b4a7128db83e74a62bf3e97b7beb1572ed5"
)
INSTALLED_WHEEL_PROBE = """
import json
import sys
from importlib.resources import files
from pathlib import Path

target = Path(sys.argv[1]).resolve()
resource_output = Path(sys.argv[2])
metadata_output = Path(sys.argv[3])
sys.path.insert(0, str(target))

from xic_extractor.alignment import quantitative_projection_authority as owner

authority = owner.load_quantitative_projection_authority_for_diagnostic()
module_file = Path(owner.__file__).resolve()
if not module_file.is_relative_to(target):
    message = (
        "owner module was not loaded from installed target: "
        f"{module_file}"
    )
    raise RuntimeError(message)
resource = files("xic_extractor.alignment").joinpath(
    "data", "issue256_quantitative_projection_authority_v1.json"
)
resource_output.write_bytes(resource.read_bytes())
metadata_output.write_text(
    json.dumps(
        {
            "authority_id": authority.authority_id,
            "module_file": str(module_file),
        }
    ),
    encoding="utf-8",
)
"""
ISSUE367_INSTALLED_WHEEL_PROBE = """
import json
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
metadata_output = Path(sys.argv[2])
sys.path.insert(0, str(target))

from xic_extractor.alignment.pipeline import run_alignment
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.extraction.untargeted_cid_nl_alignment import (
    complete_untargeted_cid_nl_alignment,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    is_pure_single_provider_cid_nl,
)
from xic_extractor.presets import bind_cid_nl_recipe, load_preset

preset = load_preset("dna_dr")
recipe = bind_cid_nl_recipe(preset, "align_only")
if not is_pure_single_provider_cid_nl(recipe.discovery_settings):
    raise RuntimeError("installed public CID-NL preset did not satisfy its predicate")

public_objects = {
    "bind_cid_nl_recipe": bind_cid_nl_recipe,
    "run_alignment": run_alignment,
    "complete_untargeted_cid_nl_alignment": complete_untargeted_cid_nl_alignment,
    "is_pure_single_provider_cid_nl": is_pure_single_provider_cid_nl,
    "load_preset": load_preset,
    "project_completed_untarget_batch_writer_inputs": (
        project_completed_untarget_batch_writer_inputs
    ),
}
module_files = {}
for public_name, public_object in public_objects.items():
    module = sys.modules[public_object.__module__]
    module_file = Path(module.__file__).resolve()
    if not module_file.is_relative_to(target):
        raise RuntimeError(
            f"{public_name} was not loaded from installed target: {module_file}"
        )
    module_files[public_name] = str(module_file)

metadata_output.write_text(
    json.dumps(
        {
            "public_names": sorted(public_objects),
            "module_files": module_files,
            "preset": preset.name,
            "route": recipe.route,
        }
    ),
    encoding="utf-8",
)
"""
PACKAGING_SOURCE_DIRECTORIES = (
    "assets",
    "config",
    "gui",
    "scripts",
    "tools",
    "xic_extractor",
)


@pytest.fixture(scope="module")
def issue256_packaging_source() -> Iterator[Path]:
    task_output_root = PROJECT_ROOT / "output"
    task_output_root.mkdir(exist_ok=True)
    temp_root = (
        task_output_root / f"xic-issue256-packaging-artifacts-{uuid.uuid4().hex}"
    )
    temp_root.mkdir()
    try:
        source_root = temp_root / "source"
        source_root.mkdir()

        for filename in ("pyproject.toml", "xic_extractor.spec"):
            shutil.copy2(PROJECT_ROOT / filename, source_root / filename)
        for directory in PACKAGING_SOURCE_DIRECTORIES:
            shutil.copytree(
                PROJECT_ROOT / directory,
                source_root / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

        yield source_root
    finally:
        resolved_temp_root = temp_root.resolve()
        assert resolved_temp_root.parent == task_output_root.resolve()
        assert resolved_temp_root.name.startswith("xic-issue256-packaging-artifacts-")
        shutil.rmtree(resolved_temp_root)


def _run_packaging_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    assert completed.returncode == 0, completed.stdout[-12_000:]


def _assert_resource_bytes_match_source(resource_bytes: bytes) -> None:
    source_bytes = (PROJECT_ROOT / AUTHORITY_RESOURCE_RELATIVE).read_bytes()

    assert hashlib.sha256(source_bytes).hexdigest() == AUTHORITY_RESOURCE_SHA256
    assert resource_bytes == source_bytes
    assert hashlib.sha256(resource_bytes).hexdigest() == AUTHORITY_RESOURCE_SHA256


def test_pyinstaller_spec_includes_dynamic_raw_reader_imports() -> None:
    spec_text = Path("xic_extractor.spec").read_text(encoding="utf-8")
    hiddenimports_match = re.search(
        r"hiddenimports=\[(?P<body>.*?)\],",
        spec_text,
        flags=re.DOTALL,
    )
    assert hiddenimports_match is not None
    hiddenimports = set(re.findall(r'"([^"]+)"', hiddenimports_match.group("body")))

    assert {"pythonnet", "clr"} <= hiddenimports


def test_pyinstaller_spec_includes_builtin_preset_tomls() -> None:
    spec_text = Path("xic_extractor.spec").read_text(encoding="utf-8")

    assert "collect_data_files" in spec_text
    assert '"xic_extractor.presets"' in spec_text
    assert '"data/*.toml"' in spec_text
    assert "+ preset_datas" in spec_text


def test_pyinstaller_collects_builtin_preset_tomls() -> None:
    datas = collect_data_files("xic_extractor.presets", includes=["data/*.toml"])
    collected = {
        (Path(source).name, Path(destination).as_posix())
        for source, destination in datas
    }

    expected_names = {f"{preset}.toml" for preset in list_presets()}
    collected_names = {name for name, _destination in collected}

    assert expected_names <= collected_names
    assert all(
        destination == "xic_extractor/presets/data"
        for name, destination in collected
        if name in expected_names
    )


def test_pyinstaller_spec_includes_quantitative_projection_authority() -> None:
    spec_text = Path("xic_extractor.spec").read_text(encoding="utf-8")

    assert "authority_datas = collect_data_files(" in spec_text
    assert '"xic_extractor.alignment"' in spec_text
    assert (
        'includes=["data/issue256_quantitative_projection_authority_v1.json"]'
        in spec_text
    )
    assert "+ authority_datas" in spec_text


def test_pyinstaller_collects_quantitative_projection_authority() -> None:
    datas = collect_data_files(
        "xic_extractor.alignment",
        includes=["data/issue256_quantitative_projection_authority_v1.json"],
    )
    collected = {
        (Path(source).name, Path(destination).as_posix())
        for source, destination in datas
    }

    assert collected == {
        (
            "issue256_quantitative_projection_authority_v1.json",
            "xic_extractor/alignment/data",
        )
    }


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_built_wheel_contains_one_byte_identical_quantitative_authority(
    issue256_packaging_source: Path,
) -> None:
    artifact_root = issue256_packaging_source.parent / "wheel-artifacts"
    uv_cache_root = issue256_packaging_source.parent / "uv-cache"
    artifact_root.mkdir()
    _run_packaging_command(
        (
            "uv",
            "build",
            "--cache-dir",
            str(uv_cache_root),
            "--wheel",
            "--no-build-isolation",
            "--out-dir",
            str(artifact_root),
            "--python",
            sys.executable,
            str(issue256_packaging_source),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=300,
    )

    wheels = list(artifact_root.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        resource_members = [
            PurePosixPath(member)
            for member in wheel.namelist()
            if PurePosixPath(member).name.casefold()
            == AUTHORITY_RESOURCE_RELATIVE.name.casefold()
        ]
        assert resource_members == [AUTHORITY_RESOURCE_ARCHIVE_PATH]
        resource_bytes = wheel.read(resource_members[0].as_posix())

    _assert_resource_bytes_match_source(resource_bytes)

    install_target = issue256_packaging_source.parent / "installed-wheel"
    _run_packaging_command(
        (
            "uv",
            "pip",
            "install",
            "--cache-dir",
            str(uv_cache_root),
            "--python",
            sys.executable,
            "--target",
            str(install_target),
            "--no-deps",
            str(wheels[0]),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=300,
    )
    installed_resource = issue256_packaging_source.parent / "installed-resource.json"
    probe_metadata = issue256_packaging_source.parent / "installed-probe.json"
    _run_packaging_command(
        (
            sys.executable,
            "-I",
            "-c",
            INSTALLED_WHEEL_PROBE,
            str(install_target),
            str(installed_resource),
            str(probe_metadata),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=60,
    )

    source_bytes = (PROJECT_ROOT / AUTHORITY_RESOURCE_RELATIVE).read_bytes()
    source_payload = json.loads(source_bytes)
    metadata = json.loads(probe_metadata.read_text(encoding="utf-8"))
    assert metadata["authority_id"] == source_payload["authority_id"]
    assert (
        Path(metadata["module_file"]).resolve().is_relative_to(install_target.resolve())
    )
    _assert_resource_bytes_match_source(installed_resource.read_bytes())


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_built_wheel_exposes_issue367_public_activation_surface(
    issue256_packaging_source: Path,
) -> None:
    artifact_root = issue256_packaging_source.parent / "issue367-wheel-artifacts"
    uv_cache_root = issue256_packaging_source.parent / "issue367-uv-cache"
    artifact_root.mkdir()
    _run_packaging_command(
        (
            "uv",
            "build",
            "--cache-dir",
            str(uv_cache_root),
            "--wheel",
            "--no-build-isolation",
            "--out-dir",
            str(artifact_root),
            "--python",
            sys.executable,
            str(issue256_packaging_source),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=300,
    )

    wheels = list(artifact_root.glob("*.whl"))
    assert len(wheels) == 1
    install_target = issue256_packaging_source.parent / "issue367-installed-wheel"
    _run_packaging_command(
        (
            "uv",
            "pip",
            "install",
            "--cache-dir",
            str(uv_cache_root),
            "--python",
            sys.executable,
            "--target",
            str(install_target),
            "--no-deps",
            str(wheels[0]),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=300,
    )

    probe_metadata = issue256_packaging_source.parent / "issue367-installed-probe.json"
    _run_packaging_command(
        (
            sys.executable,
            "-I",
            "-c",
            ISSUE367_INSTALLED_WHEEL_PROBE,
            str(install_target),
            str(probe_metadata),
        ),
        cwd=issue256_packaging_source.parent,
        timeout_seconds=60,
    )

    metadata = json.loads(probe_metadata.read_text(encoding="utf-8"))
    assert metadata["public_names"] == [
        "bind_cid_nl_recipe",
        "complete_untargeted_cid_nl_alignment",
        "is_pure_single_provider_cid_nl",
        "load_preset",
        "project_completed_untarget_batch_writer_inputs",
        "run_alignment",
    ]
    assert metadata["preset"] == "DNA dR"
    assert metadata["route"] == "align_only"
    for module_file in metadata["module_files"].values():
        assert Path(module_file).resolve().is_relative_to(install_target.resolve())


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_final_pyinstaller_output_contains_one_byte_identical_quantitative_authority(
    issue256_packaging_source: Path,
) -> None:
    artifact_root = issue256_packaging_source.parent / "pyinstaller-artifacts"
    dist_root = artifact_root / "dist"
    work_root = artifact_root / "work"
    dist_root.mkdir(parents=True)
    work_root.mkdir()
    _run_packaging_command(
        (
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--log-level",
            "WARN",
            "--distpath",
            str(dist_root),
            "--workpath",
            str(work_root),
            str(issue256_packaging_source / "xic_extractor.spec"),
        ),
        cwd=issue256_packaging_source,
        timeout_seconds=900,
    )

    final_output = dist_root / "XIC_Extractor"
    assert final_output.is_dir()
    resource_paths = [
        path
        for path in final_output.rglob("*")
        if path.is_file()
        and path.name.casefold() == AUTHORITY_RESOURCE_RELATIVE.name.casefold()
    ]
    assert len(resource_paths) == 1
    resource_relative_to_final = PurePosixPath(
        resource_paths[0].relative_to(final_output).as_posix()
    )
    assert resource_relative_to_final.parts[
        -len(AUTHORITY_RESOURCE_ARCHIVE_PATH.parts) :
    ] == (AUTHORITY_RESOURCE_ARCHIVE_PATH.parts)
    _assert_resource_bytes_match_source(resource_paths[0].read_bytes())
