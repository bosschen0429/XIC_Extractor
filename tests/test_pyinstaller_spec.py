import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest
from PyInstaller.utils.hooks import collect_data_files

from xic_extractor.presets import list_presets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "packaging" / "xic_extractor.spec"
TARGET_TEMPLATE_RELATIVE = (
    Path("xic_extractor") / "configuration" / "data" / "targets.csv"
)
TARGET_TEMPLATE_ARCHIVE_PATH = PurePosixPath(*TARGET_TEMPLATE_RELATIVE.parts)
INSTALLED_WHEEL_PROBE = """
import contextlib
import io
import json
import sys
from importlib.metadata import distributions
from importlib.resources import files
from pathlib import Path

target = Path(sys.argv[1]).resolve()
resource_output = Path(sys.argv[2])
metadata_output = Path(sys.argv[3])
sys.path.insert(0, str(target))

from xic_inspector.rendering.template import HTML_TEMPLATE
from xic_workbench import config_io
from xic_workbench import cli as workbench_cli
from xic_workbench.cli import main as workbench_main
if not Path(workbench_cli.__file__).resolve().is_relative_to(target):
    raise RuntimeError("workbench was not loaded from the installed target")
tokens = ("__INSPECTOR_JS__", "__INSPECTOR_CSS__")
if any(token in HTML_TEMPLATE for token in tokens):
    raise RuntimeError("installed Inspector did not embed its frontend")
if "inspector-data" not in HTML_TEMPLATE:
    raise RuntimeError("installed Inspector did not embed its frontend")
for asset in ("inspector.html", "inspector.js", "inspector.css"):
    if not files("xic_inspector.rendering").joinpath("assets", asset).read_bytes():
        raise RuntimeError(f"installed Inspector asset is empty: {asset}")
for asset in (
    "index.html", "workspace.js", "workspace.css",
    "analysis.html", "analysis.js", "analysis.css",
    "analysis_state.js", "analysis_session.js", "analysis_sample_metadata.js",
    "analysis_curation_session.js",
    "analysis_ui.js", "analysis_setup.js",
    "analysis_targeted.js", "analysis_untargeted.js", "analysis_samples.js",
    "analysis_delta_mass.js", "analysis_row_selection.js",
    "analysis_jobs.js", "analysis_results.js", "analysis_curation.js",
    "analysis_reader.js",
):
    if not files("xic_workbench").joinpath("assets", asset).read_bytes():
        raise RuntimeError(f"installed workspace asset is empty: {asset}")
from xic_extractor.configuration.paths import targets_template_path
if (target / "config" / "settings.example.csv").exists():
    raise RuntimeError("retired settings template was shipped")
if not targets_template_path().read_bytes():
    raise RuntimeError("installed targets template is empty")
resource_output.write_bytes(targets_template_path().read_bytes())
if config_io.CONFIG_DIR.resolve().is_relative_to(target):
    raise RuntimeError("installed workbench would write settings into site-packages")
if not targets_template_path().resolve().is_relative_to(target / "xic_extractor"):
    raise RuntimeError("template is not owned by the installed package")
from xic_extractor.configuration.targeted_method_files import (
    load_targeted_method, load_target_table,
)
examples = targets_template_path().parent
from xic_workbench.user_guide import read_user_guide
guide = read_user_guide("")
if guide is None or "XIC Extractor 使用說明" not in guide[0].decode("utf-8"):
    raise RuntimeError("installed workbench is missing its offline guide")
method = load_targeted_method(examples / "targeted-method.xlsx")
target_rows = load_target_table(examples / "targets.xlsx")
if not method.targets or list(method.target_rows) != target_rows:
    raise RuntimeError("installed XLSX examples are missing or inconsistent")
installed_distribution = next(
    distribution for distribution in distributions(path=[str(target)])
    if distribution.metadata["Name"] == "xic-extractor"
)
primary_entrypoint = next(
    entrypoint for entrypoint in installed_distribution.entry_points
    if entrypoint.group == "console_scripts" and entrypoint.name == "xic-extractor"
)
if primary_entrypoint.value != "xic_workbench.cli:main":
    raise RuntimeError(f"unexpected xic-extractor entry: {primary_entrypoint.value}")
if primary_entrypoint.load() is not workbench_main:
    raise RuntimeError("xic-extractor did not load the workbench entry")
with contextlib.redirect_stdout(io.StringIO()):
    try:
        workbench_main(["--help"])
    except SystemExit as exc:
        if exc.code != 0:
            raise
if any(name == "PyQt6" or name.startswith("PyQt6.") for name in sys.modules):
    raise RuntimeError("HTML workspace imported Qt")
metadata_output.write_text(
    json.dumps(
        {
            "module_file": str(Path(workbench_cli.__file__).resolve()),
            "config_dir": str(config_io.CONFIG_DIR),
            "xic_extractor_entry": primary_entrypoint.value,
        }
    ),
    encoding="utf-8",
)
"""
PUBLIC_ACTIVATION_INSTALLED_WHEEL_PROBE = """
import contextlib
import io
import json
import sys
from importlib.metadata import distributions
from pathlib import Path

target = Path(sys.argv[1]).resolve()
metadata_output = Path(sys.argv[2])
sys.path.insert(0, str(target))

from xic_extractor.applications.untargeted.application import run_untargeted_application
from xic_extractor.output.alignment.tsv_writer import write_alignment_tsvs
from xic_extractor.alignment.completion import complete_analysis
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.alignment import (
    complete_untargeted_alignment,
)
from xic_extractor.untargeted_execution.input_planning import (
    is_pure_single_provider_cid_nl,
)
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses,
)
from xic_extractor.extraction.targeted_peak_selection import (
    TargetedPeakSelection,
    select_targeted_peak,
)
from xic_extractor.configuration.execution_profile import load_execution_profile
from xic_extractor.execution.partitions import PartitionPipelineScheduler
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.execution.workers import WorkerAdmissionPool, execute_process_jobs
from xic_extractor.extraction.jobs import RawFileJob
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import UntargetedPartitionJob
from xic_extractor.untargeted_execution.partition.request import (
    build_partition_context,
)
from xic_extractor.untargeted_execution.partition.workers import (
    _SampleInitialWorkerRequest,
)
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    UntargetedCidNlBackfillTarget,
)
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    CidNlRawSource,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    CidNlAcquisitionResult,
)
from xic_extractor.presets import bind_cid_nl_recipe, load_preset

preset = load_preset("dna_dr")
recipe = bind_cid_nl_recipe(preset, "align_only")
if not is_pure_single_provider_cid_nl(recipe.discovery_settings):
    raise RuntimeError("installed public CID-NL preset did not satisfy its predicate")

public_objects = {
    "bind_cid_nl_recipe": bind_cid_nl_recipe,
    "run_untargeted_application": run_untargeted_application,
    "complete_untargeted_alignment": complete_untargeted_alignment,
    "is_pure_single_provider_cid_nl": is_pure_single_provider_cid_nl,
    "load_preset": load_preset,
    "write_alignment_tsvs": write_alignment_tsvs,
}
module_files = {}
installed_objects = {
    **public_objects,
    "build_targeted_peak_hypotheses": build_targeted_peak_hypotheses,
    "TargetedPeakSelection": TargetedPeakSelection,
    "select_targeted_peak": select_targeted_peak,
    "complete_analysis": complete_analysis,
    "CompletedUntargetBatchWorkflowResult": CompletedUntargetBatchWorkflowResult,
    "load_execution_profile": load_execution_profile,
    "PartitionPipelineScheduler": PartitionPipelineScheduler,
    "StreamedResultStore": StreamedResultStore,
    "WorkerAdmissionPool": WorkerAdmissionPool,
    "execute_process_jobs": execute_process_jobs,
    "RawFileJob": RawFileJob,
    "execute_partition": execute_partition,
    "UntargetedPartitionJob": UntargetedPartitionJob,
    "build_partition_context": build_partition_context,
    "_SampleInitialWorkerRequest": _SampleInitialWorkerRequest,
    "CidNlAcquisitionResult": CidNlAcquisitionResult,
    "UntargetedCidNlBackfillTarget": UntargetedCidNlBackfillTarget,
    "CidNlRawSource": CidNlRawSource,
}
for public_name, public_object in installed_objects.items():
    module = sys.modules[public_object.__module__]
    module_file = Path(module.__file__).resolve()
    if not module_file.is_relative_to(target):
        raise RuntimeError(
            f"{public_name} was not loaded from installed target: {module_file}"
        )
    module_files[public_name] = str(module_file)

installed_distribution = next(
    distribution for distribution in distributions(path=[str(target)])
    if distribution.metadata["Name"] == "xic-extractor"
)
cli_help_names = [
    "xic-align-cli", "xic-discovery-cli", "xic-extractor-cli",
]
for cli_name in cli_help_names:
    entrypoint = next(
        entrypoint for entrypoint in installed_distribution.entry_points
        if entrypoint.group == "console_scripts" and entrypoint.name == cli_name
    )
    cli = entrypoint.load()
    module_file = Path(sys.modules[cli.__module__].__file__).resolve()
    if not module_file.is_relative_to(target):
        raise RuntimeError(f"{cli_name} did not load from installed target")
    sys.argv = [cli_name, "--help"]
    with contextlib.redirect_stdout(io.StringIO()) as help_output:
        try:
            cli()
        except SystemExit as exc:
            if exc.code != 0:
                raise
        else:
            raise RuntimeError(f"{cli_name} did not complete --help")
    if "usage:" not in help_output.getvalue():
        raise RuntimeError(f"{cli_name} did not expose CLI help")

metadata_output.write_text(
    json.dumps(
        {
            "public_names": sorted(public_objects),
            "module_files": module_files,
            "cli_help_names": cli_help_names,
            "preset": preset.name,
            "route": recipe.route,
        }
    ),
    encoding="utf-8",
)
"""
PACKAGING_SOURCE_DIRECTORIES = (
    "config",
    "docs",
    "packaging",
    "scripts",
    "tools",
    "xic_extractor",
    "xic_inspector",
    "xic_workbench",
)


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL search order")
def test_pyinstaller_system_icu_wins_over_foreign_path_dll(monkeypatch, tmp_path):
    from PyInstaller.depend.bindepend import resolve_library_path

    system_dir = Path(os.environ["SystemRoot"]) / "System32"
    # An unrelated but valid PE image reproduces a same-name foreign DLL on PATH.
    foreign_dir = tmp_path / "foreign-tool"
    foreign_dir.mkdir()
    shutil.copy2(system_dir / "kernel32.dll", foreign_dir / "icuuc.dll")
    monkeypatch.setenv("PATH", str(foreign_dir) + os.pathsep + os.environ["PATH"])

    class AnalysisReached(Exception):
        pass

    def inspect_dependency_search(*args, **kwargs):
        resolved = resolve_library_path("icuuc.dll")
        assert resolved is not None
        assert Path(resolved).resolve() == (system_dir / "icuuc.dll").resolve()
        raise AnalysisReached

    with pytest.raises(AnalysisReached):
        runpy.run_path(
            str(SPEC_PATH),
            init_globals={"Analysis": inspect_dependency_search},
        )


@pytest.fixture(scope="module")
def packaging_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Leave room for isolated build environments and their package paths.
    temp_root = tmp_path_factory.mktemp("pkg")
    source_root = temp_root / "source"
    source_root.mkdir()

    shutil.copy2(PROJECT_ROOT / "pyproject.toml", source_root / "pyproject.toml")
    shutil.copy2(PROJECT_ROOT / "MANIFEST.in", source_root / "MANIFEST.in")
    for directory in PACKAGING_SOURCE_DIRECTORIES:
        if directory == "config":
            # A package must not depend on or copy a developer's ignored state.
            (source_root / "config").mkdir()
            shutil.copy2(
                PROJECT_ROOT / "config/env.example", source_root / "config/env.example"
            )
            continue
        shutil.copytree(
            PROJECT_ROOT / directory,
            source_root / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyd", "*.so"),
        )

    return source_root


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
    source_bytes = (PROJECT_ROOT / TARGET_TEMPLATE_RELATIVE).read_bytes()

    assert resource_bytes == source_bytes


def test_primary_package_routes_to_html_workbench_without_qt() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["scripts"]["xic-extractor"] == ("xic_workbench.cli:main")
    assert not any(
        dependency.casefold().startswith(("pyqt6", "pyside6"))
        for dependency in project["project"]["dependencies"]
    )
    assert "gui*" not in project["tool"]["setuptools"]["packages"]["find"]["include"]
    assert project["tool"]["setuptools"]["package-data"][
        "xic_extractor.configuration"
    ] == ["data/*.csv", "data/*.json", "data/*.xlsx"]
    assert "data-files" not in project["tool"]["setuptools"]


def test_installed_config_defaults_to_user_local_state(monkeypatch, tmp_path) -> None:
    import xic_extractor.configuration.paths as config_io

    package_parent = tmp_path / "site-packages"
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(config_io, "_PACKAGE_PARENT", package_parent)
    monkeypatch.delattr(config_io.sys, "frozen", raising=False)
    monkeypatch.setattr(config_io.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert config_io.runtime_root() == local_app_data / "XICExtractor"


def test_development_and_frozen_config_roots_stay_with_the_application(
    monkeypatch, tmp_path
) -> None:
    import xic_extractor.configuration.paths as config_io

    development_root = tmp_path / "source"
    (development_root / "config").mkdir(parents=True)
    (development_root / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config_io, "_PACKAGE_PARENT", development_root)
    monkeypatch.delattr(config_io.sys, "frozen", raising=False)
    assert config_io.runtime_root() == development_root

    executable = tmp_path / "delivered" / "XIC_Extractor.exe"
    monkeypatch.setattr(config_io.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_io.sys, "executable", str(executable))
    assert config_io.runtime_root() == executable.parent


def test_pyinstaller_spec_includes_dynamic_raw_reader_imports() -> None:
    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    hiddenimports_match = re.search(
        r"hiddenimports=\[(?P<body>.*?)\],",
        spec_text,
        flags=re.DOTALL,
    )
    assert hiddenimports_match is not None
    hiddenimports = set(re.findall(r'"([^"]+)"', hiddenimports_match.group("body")))

    assert {"pythonnet", "clr"} <= hiddenimports


def test_pyinstaller_spec_starts_html_workbench_and_excludes_qt() -> None:
    spec_text = SPEC_PATH.read_text(encoding="utf-8")

    assert (
        'Analysis(\n    [os.path.join(ROOT, "xic_workbench", "__main__.py")]'
        in spec_text
    )
    assert 'excludes=["PyQt6", "PySide6"]' in spec_text
    assert '"PyQt6.' not in spec_text
    assert '"PySide6.' not in spec_text


def test_pyinstaller_spec_includes_inspector_api() -> None:
    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    assert '"xic_inspector.api"' in spec_text
    assert '"xic_inspector.sources.cid_nl"' in spec_text
    assert '"xic_inspector.rendering.html_builder"' in spec_text


def test_pyinstaller_collects_inspector_frontend() -> None:
    collected = {
        (Path(source).name, Path(destination).as_posix())
        for source, destination in collect_data_files(
            "xic_inspector.rendering", includes=["assets/*"]
        )
    }
    assert collected == {
        ("delta_mass.js", "xic_inspector/rendering/assets"),
        ("inspector.html", "xic_inspector/rendering/assets"),
        ("inspector.js", "xic_inspector/rendering/assets"),
        ("inspector.css", "xic_inspector/rendering/assets"),
    }


def test_pyinstaller_collects_workbench_frontend() -> None:
    collected = {
        (Path(source).name, Path(destination).as_posix())
        for source, destination in collect_data_files(
            "xic_workbench",
            includes=["assets/*.html", "assets/*.js", "assets/*.css"],
        )
    }
    assert collected == {
        ("analysis.css", "xic_workbench/assets"),
        ("analysis.html", "xic_workbench/assets"),
        ("analysis.js", "xic_workbench/assets"),
        ("analysis_state.js", "xic_workbench/assets"),
        ("analysis_session.js", "xic_workbench/assets"),
        ("analysis_sample_metadata.js", "xic_workbench/assets"),
        ("analysis_curation_session.js", "xic_workbench/assets"),
        ("analysis_ui.js", "xic_workbench/assets"),
        ("analysis_setup.js", "xic_workbench/assets"),
        ("analysis_targeted.js", "xic_workbench/assets"),
        ("analysis_delta_mass.js", "xic_workbench/assets"),
        ("analysis_row_selection.js", "xic_workbench/assets"),
        ("analysis_untargeted.js", "xic_workbench/assets"),
        ("analysis_samples.js", "xic_workbench/assets"),
        ("analysis_jobs.js", "xic_workbench/assets"),
        ("analysis_results.js", "xic_workbench/assets"),
        ("analysis_curation.js", "xic_workbench/assets"),
        ("analysis_reader.js", "xic_workbench/assets"),
        ("index.html", "xic_workbench/assets"),
        ("workspace.css", "xic_workbench/assets"),
        ("workspace.js", "xic_workbench/assets"),
    }


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


def test_pyinstaller_collects_discovery_default_background_library() -> None:
    datas = collect_data_files(
        "xic_extractor.discovery",
        includes=["data/*"],
    )
    collected = {
        (Path(source).name, Path(destination).as_posix())
        for source, destination in datas
    }

    assert (
        "default_background_library_15min.json.gz",
        "xic_extractor/discovery/data",
    ) in collected


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_built_wheel_contains_workbench_and_matching_target_template(
    packaging_source: Path,
) -> None:
    artifact_root = packaging_source.parent / "wheel-artifacts"
    uv_cache_root = packaging_source.parent / "uv-cache"
    artifact_root.mkdir()
    _run_packaging_command(
        (
            "uv",
            "build",
            "--cache-dir",
            str(uv_cache_root),
            "--wheel",
            "--out-dir",
            str(artifact_root),
            "--python",
            sys.executable,
            str(packaging_source),
        ),
        cwd=packaging_source.parent,
        timeout_seconds=300,
    )

    wheels = list(artifact_root.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        runtime_config = json.loads(
            wheel.read(
                "xic_extractor/configuration/data/thermo_dotnet.runtimeconfig.json"
            )
        )
        assert (
            runtime_config["runtimeOptions"]["framework"]["name"]
            == "Microsoft.NETCore.App"
        )
        entry_points = next(
            name
            for name in wheel.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        )
        assert (
            "xic-install-thermo-reader = scripts.install_thermo_reader:main"
            in wheel.read(entry_points).decode()
        )
        resource_members = [
            PurePosixPath(member)
            for member in wheel.namelist()
            if PurePosixPath(member).name.casefold()
            == TARGET_TEMPLATE_RELATIVE.name.casefold()
        ]
        help_example = PurePosixPath("xic_workbench/assets/help/examples/targets.csv")
        assert set(resource_members) == {TARGET_TEMPLATE_ARCHIVE_PATH, help_example}
        resource_bytes = wheel.read(TARGET_TEMPLATE_ARCHIVE_PATH.as_posix())
        assert wheel.read(help_example.as_posix()) == resource_bytes

    _assert_resource_bytes_match_source(resource_bytes)

    install_target = packaging_source.parent / "installed-wheel"
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
        cwd=packaging_source.parent,
        timeout_seconds=300,
    )
    installed_resource = packaging_source.parent / "installed-targets.csv"
    probe_metadata = packaging_source.parent / "installed-probe.json"
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
        cwd=packaging_source.parent,
        timeout_seconds=60,
    )

    metadata = json.loads(probe_metadata.read_text(encoding="utf-8"))
    assert metadata["xic_extractor_entry"] == "xic_workbench.cli:main"
    assert (
        Path(metadata["module_file"]).resolve().is_relative_to(install_target.resolve())
    )
    _assert_resource_bytes_match_source(installed_resource.read_bytes())


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_built_wheel_exposes_public_cid_nl_activation_surface(
    packaging_source: Path,
) -> None:
    artifact_root = packaging_source.parent / "public-activation-wheel-artifacts"
    uv_cache_root = packaging_source.parent / "uv-public"
    artifact_root.mkdir()
    _run_packaging_command(
        (
            "uv",
            "build",
            "--cache-dir",
            str(uv_cache_root),
            "--wheel",
            "--out-dir",
            str(artifact_root),
            "--python",
            sys.executable,
            str(packaging_source),
        ),
        cwd=packaging_source.parent,
        timeout_seconds=300,
    )

    wheels = list(artifact_root.glob("*.whl"))
    assert len(wheels) == 1
    install_target = packaging_source.parent / "public-activation-installed-wheel"
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
        cwd=packaging_source.parent,
        timeout_seconds=300,
    )

    probe_metadata = packaging_source.parent / "public-activation-installed-probe.json"
    _run_packaging_command(
        (
            sys.executable,
            "-I",
            "-c",
            PUBLIC_ACTIVATION_INSTALLED_WHEEL_PROBE,
            str(install_target),
            str(probe_metadata),
        ),
        cwd=packaging_source.parent,
        timeout_seconds=60,
    )

    metadata = json.loads(probe_metadata.read_text(encoding="utf-8"))
    assert metadata["public_names"] == [
        "bind_cid_nl_recipe",
        "complete_untargeted_alignment",
        "is_pure_single_provider_cid_nl",
        "load_preset",
        "run_untargeted_application",
        "write_alignment_tsvs",
    ]
    assert metadata["preset"] == "DNA dR"
    assert metadata["route"] == "align_only"
    assert metadata["cli_help_names"] == [
        "xic-align-cli",
        "xic-discovery-cli",
        "xic-extractor-cli",
    ]
    for module_file in metadata["module_files"].values():
        assert Path(module_file).resolve().is_relative_to(install_target.resolve())


@pytest.mark.packaging_build
@pytest.mark.serial_ci
def test_final_pyinstaller_archive_runs_html_workbench_smoke(
    packaging_source: Path,
) -> None:
    artifact_root = packaging_source.parent / "pyinstaller-artifacts"
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
            str(packaging_source / "packaging" / "xic_extractor.spec"),
        ),
        cwd=packaging_source,
        timeout_seconds=900,
    )

    final_output = dist_root / "XIC_Extractor"
    assert final_output.is_dir()
    templates = list(
        final_output.glob("**/xic_extractor/configuration/data/targets.csv")
    )
    assert len(templates) == 1
    assert (
        templates[0].read_bytes()
        == (PROJECT_ROOT / "xic_extractor/configuration/data/targets.csv").read_bytes()
    )
    assert not list(final_output.rglob("settings.example.csv"))
    assert not list(final_output.rglob("targets.example.csv"))
    for name in ("targeted-method.xlsx", "targets.xlsx"):
        examples = list(
            final_output.glob(f"**/xic_extractor/configuration/data/{name}")
        )
        assert len(examples) == 1
        assert (
            examples[0].read_bytes()
            == (PROJECT_ROOT / "xic_extractor/configuration/data" / name).read_bytes()
        )
    # Exercise the archive that would be delivered, rather than rebuilding a probe.
    version = tomllib.loads(
        (packaging_source / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    archive_path = artifact_root / f"XIC_Extractor-Windows-v{version}-dev.zip"
    _run_packaging_command(
        (
            sys.executable,
            "-m",
            "tools.distribution",
            str(final_output),
            str(archive_path),
        ),
        cwd=packaging_source,
        timeout_seconds=60,
    )
    archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    extracted_root = artifact_root / "delivered-extracted"
    shutil.unpack_archive(archive_path, extracted_root)
    smoke_report = extracted_root / "smoke.json"
    application = extracted_root / archive_path.stem
    assert (application / "開始使用.html").is_file()
    assert (application / "docs/user/targeted-extraction.guide.html").is_file()
    assert (application / "docs/user/untargeted-discovery.html").is_file()
    assert not list(application.rglob("*.py"))
    assert not (application / "_internal/scripts").exists()
    executable = application / "XIC_Extractor.exe"
    _run_packaging_command(
        (str(executable), "--smoke-test", str(smoke_report)),
        cwd=extracted_root,
        timeout_seconds=60,
    )
    report = json.loads(smoke_report.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["frozen"] and report["server_started"] and report["preset_roundtrip"]
    assert report["user_guide"]
    assert Path(report["executable"]).resolve() == executable.resolve()
    assert report["resources"]["target_rows"] > 0
    assert report["resources"]["background_ions"] > 0
    assert Path(report["preset_path"]).is_file()
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == archive_hash
    print(f"Delivered archive smoke report: {smoke_report}")


def _build_workflow_script(step_id: str) -> str:
    text = (PROJECT_ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    step = text.split(f"        id: {step_id}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell release workflow")
@pytest.mark.parametrize(
    "case",
    ("release", "wrong_version", "expression_tag", "branch", "expression_branch"),
)
def test_package_metadata_treats_git_refs_as_data(tmp_path, case):
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    ref_name = {
        "release": f"v{version}",
        "wrong_version": f"v{version}-wrong",
        "expression_tag": f"v$(Write-Warning(781)){version}",
        "branch": "master",
        "expression_branch": "topic/$(Write-Warning(781))",
    }[case]
    is_tag = case not in {"branch", "expression_branch"}
    ref = f"refs/{'tags' if is_tag else 'heads'}/{ref_name}"
    source = _build_workflow_script("meta")
    # Run the Windows workflow's shell logic with this OS's test interpreter.
    source = source.replace(
        r".\.venv\Scripts\python.exe", "'" + sys.executable.replace("'", "''") + "'"
    )
    # Model Actions' source interpolation so the regression reaches the shell.
    source = source.replace("${{ github.ref }}", ref).replace(
        "${{ github.ref_name }}", ref_name
    )
    script = tmp_path / "metadata.ps1"
    script.write_text(source, encoding="utf-8")
    output = tmp_path / "github-output.txt"
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "GITHUB_REF": ref,
            "GITHUB_REF_NAME": ref_name,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert "WARNING: 781" not in completed.stdout
    accepted = case not in {"wrong_version", "expression_tag"}
    assert (completed.returncode == 0) is accepted, completed.stderr
    if accepted:
        package_version = f"v{version}" + ("" if is_tag else "-dev")
        assert output.read_text(encoding="utf-8-sig").splitlines() == [
            f"version={package_version}",
            f"artifact_stem=XIC_Extractor-Windows-{package_version}",
        ]
    else:
        assert not output.exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell release workflow")
@pytest.mark.parametrize(
    ("runs", "accepted"),
    (
        ([{"id": 1, "head_sha": "release-sha", "conclusion": "success"}], True),
        ([{"id": 2, "head_sha": "release-sha", "conclusion": "failure"}], False),
        ([{"id": 3, "head_sha": "other-sha", "conclusion": "success"}], False),
        ([], False),
    ),
)
def test_release_gate_requires_successful_ci_for_exact_sha(tmp_path, runs, accepted):
    response = tmp_path / "ci-response.json"
    response.write_text(json.dumps({"workflow_runs": runs}), encoding="utf-8")
    script = tmp_path / "verify-ci.ps1"
    script.write_text(
        """
function Invoke-RestMethod {
    param($Uri, $Headers)
    if ($Uri -notmatch '/actions/workflows/ci.yml/runs\\?head_sha=release-sha&') {
        throw "CI gate queried the wrong workflow or SHA"
    }
    Get-Content -LiteralPath $env:CI_RESPONSE -Raw | ConvertFrom-Json
}
"""
        + _build_workflow_script("verify_ci"),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        env={
            **os.environ,
            "CI_RESPONSE": str(response),
            "GITHUB_API_URL": "https://api.github.com",
            "GITHUB_REPOSITORY": "example/repository",
            "GITHUB_SHA": "release-sha",
            "GH_TOKEN": "test-token",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert (completed.returncode == 0) is accepted, completed.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell release workflow")
@pytest.mark.parametrize(
    ("outcome", "accepted"),
    (("success", True), ("nonzero", False), ("timeout", False), ("failed", False)),
)
def test_delivered_archive_gate_rejects_failed_or_timed_out_smoke(
    tmp_path, outcome, accepted
):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "fixture.zip").write_bytes(b"the archive selected for delivery")
    report = tmp_path / "response.json"
    report.write_text(
        json.dumps(
            {
                "status": "failed" if outcome == "failed" else "passed",
                "frozen": True,
                "server_started": True,
                "preset_roundtrip": True,
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "smoke-archive.ps1"
    script.write_text(
        """
function Expand-Archive {
    param($LiteralPath, $DestinationPath)
    New-Item -ItemType Directory -Path $DestinationPath | Out-Null
    Copy-Item $env:SMOKE_RESPONSE (Join-Path $DestinationPath "smoke.json")
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    $process = [pscustomobject]@{ ExitCode = [int]$env:SMOKE_EXIT; Id = 1234 }
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
        param($Timeout)
        return $env:SMOKE_OUTCOME -ne "timeout"
    }
    return $process
}
function Stop-Process {
    param($Id, [switch]$Force)
}
"""
        + _build_workflow_script("smoke_archive"),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "ARTIFACT_STEM": "fixture",
            "SMOKE_RESPONSE": str(report),
            "SMOKE_EXIT": "7" if outcome == "nonzero" else "0",
            "SMOKE_OUTCOME": outcome,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert (completed.returncode == 0) is accepted, completed.stderr
    if outcome == "nonzero":
        assert "exit 7" in completed.stderr
    elif outcome == "timeout":
        assert "timed out" in completed.stderr
    elif outcome == "failed":
        assert "did not complete" in completed.stderr
