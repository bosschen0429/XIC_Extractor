import ast
from pathlib import Path


def test_alignment_exports_plan2_public_api_names():
    import xic_extractor.alignment as alignment

    assert alignment.__all__ == (
        "AlignmentConfig",
        "AlignmentCluster",
        "AlignedCell",
        "AlignmentMatrix",
        "CellStatus",
    )
    assert {
        name for name in dir(alignment) if not name.startswith("_")
    } == set(alignment.__all__)


def test_discovery_csv_and_alignment_tsv_do_not_import_raw_reader():
    for module_path in ("discovery/csv_reader.py", "alignment/tsv_writer.py"):
        path = Path(__file__).parents[1] / "xic_extractor" / module_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations = [
            imported_name
            for node in ast.walk(tree)
            for imported_name in _imported_module_names(node)
            if imported_name.startswith("xic_extractor.raw_reader")
        ]

        assert violations == []


def test_alignment_process_backend_delegates_execution_mechanics():
    root = Path(__file__).parents[1] / "xic_extractor" / "alignment"
    process_backend_source = (root / "process_backend.py").read_text(
        encoding="utf-8"
    )
    process_execution_source = (root / "process_execution.py").read_text(
        encoding="utf-8"
    )

    assert "from xic_extractor.alignment.process_execution import" in (
        process_backend_source
    )
    assert "ProcessPoolExecutor" not in process_backend_source
    assert "def _run_process_jobs" not in process_backend_source
    assert "def _validate_payload_value" not in process_backend_source
    assert "class _TimedProcessRawSource" not in process_backend_source

    assert "def run_process_jobs" in process_execution_source
    assert "def process_job_payload_size_bytes" in process_execution_source
    assert "class TimedProcessRawSource" in process_execution_source


def test_alignment_import_normalization_resolves_relative_process_backend():
    tree = ast.parse(
        "from .process_backend import AlignmentProcessExecutionError\n"
        "from . import process_backend\n"
    )

    imported_names = {
        imported_name
        for node in ast.walk(tree)
        for imported_name in _alignment_imported_module_names(node)
    }

    assert "xic_extractor.alignment.process_backend" in imported_names


def test_alignment_process_backend_delegates_identity_trace_process():
    root = Path(__file__).parents[1] / "xic_extractor" / "alignment"
    process_backend_source = (root / "process_backend.py").read_text(
        encoding="utf-8"
    )
    identity_trace_source = (root / "identity_trace_process.py").read_text(
        encoding="utf-8"
    )

    assert "from xic_extractor.alignment.identity_trace_process import" in (
        process_backend_source
    )
    assert "def _identity_trace_" not in process_backend_source
    assert "class IdentityTraceSampleJob" not in process_backend_source
    assert "from xic_extractor.raw_reader import open_raw" in process_backend_source
    assert "from xic_extractor.raw_reader import open_raw" not in identity_trace_source

    assert "def run_identity_trace_process" in identity_trace_source
    assert "def extract_identity_trace_sample_from_raw" in identity_trace_source
    assert "class IdentityTraceSampleJob" in identity_trace_source


def test_alignment_ownership_module_stays_domain_focused():
    source = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "ownership.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "xic_extractor.alignment.tsv_writer",
        "scripts.run_alignment",
        "openpyxl",
        "csv.",
    )
    for token in forbidden:
        assert token not in source


def test_alignment_edge_scoring_stays_domain_focused():
    path = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "edge_scoring.py"
    )
    banned_roots = (
        "gui",
        "openpyxl",
        "scripts",
        "xic_extractor.alignment.xlsx_writer",
        "xic_extractor.alignment.process_backend",
        "xic_extractor.raw_reader",
    )

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = [
        imported_name
        for node in ast.walk(tree)
        for imported_name in _imported_module_names(node)
        if imported_name.startswith(banned_roots)
    ]

    assert violations == []


def _imported_module_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        imported_names = [node.module] if node.module else []
        imported_names.extend(
            f"{node.module}.{alias.name}"
            for alias in node.names
            if node.module
        )
        return imported_names
    return []


def _alignment_imported_module_names(node):
    if not isinstance(node, ast.ImportFrom) or node.level == 0:
        return _imported_module_names(node)

    package_parts = ["xic_extractor", "alignment"]
    parent_count = node.level - 1
    if parent_count:
        package_parts = package_parts[:-parent_count]
    base_module = ".".join(package_parts)
    resolved_module = (
        f"{base_module}.{node.module}" if node.module else base_module
    )
    imported_names = [resolved_module]
    imported_names.extend(
        f"{resolved_module}.{alias.name}" for alias in node.names
    )
    return imported_names
