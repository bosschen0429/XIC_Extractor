"""Executable rollback oracle for the Issue #366 production-route change."""

from __future__ import annotations

import ast
import io
import os
import subprocess
import tarfile
from pathlib import Path

_PR_BASE = "d977a93520d76c6bc0f123915791132b9c044d16"
_ROUTE_FILES = (
    "xic_extractor/extractor.py",
    "xic_extractor/extraction/targeted_service.py",
    "gui/workers/pipeline_worker.py",
    "scripts/run_extraction.py",
)
_PR_ONLY_PRODUCTION_DEPENDENCIES = (
    "xic_extractor/extraction/targeted_output_boundary.py",
    "xic_extractor/extraction/targeted_production_workflow.py",
)
_RETAINED_OWNERS_AND_RENDERERS = (
    "xic_extractor/alignment/workflow.py",
    "xic_extractor/alignment/result/application_result.py",
    "xic_extractor/extraction/output_dispatch.py",
    "xic_extractor/output/excel_pipeline.py",
    "xic_extractor/output/csv_writers.py",
)


def test_reversing_pr_restores_old_production_routes(tmp_path: Path) -> None:
    """Reverse the real PR patch and exercise the restored routing surface."""

    repository = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "reverted"
    checkout.mkdir()
    archive_bytes = _git(repository, "archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        _extract_trusted_repository_archive(archive, checkout)

    patch = _git(repository, "diff", "--binary", f"{_PR_BASE}...HEAD")
    subprocess.run(
        ["git", "apply", "--reverse", "--binary", "--whitespace=nowarn"],
        cwd=checkout,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env={
            **os.environ,
            "GIT_CEILING_DIRECTORIES": str(checkout.parent.resolve()),
        },
    )

    for relative_path in _ROUTE_FILES:
        reverted = (checkout / relative_path).read_text(encoding="utf-8").splitlines()
        base = (
            _git(repository, "show", f"{_PR_BASE}:{relative_path}")
            .decode("utf-8")
            .splitlines()
        )
        assert reverted == base, relative_path

    extractor_source = (checkout / "xic_extractor/extractor.py").read_text(
        encoding="utf-8"
    )
    assert "from xic_extractor.extraction.pipeline import run_pipeline" in (
        extractor_source
    )
    assert "return run_pipeline(" in extractor_source
    assert _passes_extractor_run_to_targeted_service(
        checkout / "gui/workers/pipeline_worker.py"
    )
    assert _passes_extractor_run_to_targeted_service(
        checkout / "scripts/run_extraction.py"
    )

    for relative_path in _PR_ONLY_PRODUCTION_DEPENDENCIES:
        assert not (checkout / relative_path).exists()
    for relative_path in _RETAINED_OWNERS_AND_RENDERERS:
        assert (checkout / relative_path).is_file()


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _extract_trusted_repository_archive(
    archive: tarfile.TarFile,
    destination: Path,
) -> None:
    destination_root = destination.resolve()
    members = archive.getmembers()
    for member in members:
        member_path = (destination / member.name).resolve()
        assert (
            member_path == destination_root or destination_root in member_path.parents
        )
        if member.isdir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue
        assert member.isfile()
        member_path.parent.mkdir(parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        assert extracted is not None
        member_path.write_bytes(extracted.read())


def _passes_extractor_run_to_targeted_service(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not isinstance(function, ast.Name):
            continue
        if function.id != "build_canonical_targeted_application_service":
            continue
        runner = node.args[0]
        if (
            isinstance(runner, ast.Attribute)
            and runner.attr == "run"
            and isinstance(runner.value, ast.Name)
            and runner.value.id == "extractor"
        ):
            return True
    return False
