import ast
import importlib.util
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_extractor_delegates_backend_orchestration() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.serial_backend")
    assert importlib.util.find_spec("xic_extractor.extraction.process_backend")
    assert importlib.util.find_spec("xic_extractor.extraction.jobs")

    extractor_path = ROOT / "xic_extractor" / "extractor.py"
    function_names = _function_names(extractor_path)

    assert "_run_serial" not in function_names
    assert "_run_process" not in function_names
    assert "_collect_raw_file_results_process" not in function_names
    assert "_collect_istd_prepass_process" not in function_names
    assert len(extractor_path.read_text(encoding="utf-8").splitlines()) <= 850


def test_process_jobs_live_under_extraction_package() -> None:
    from xic_extractor.extraction import jobs

    process_backend_path = ROOT / "xic_extractor" / "extraction" / "process_backend.py"

    assert inspect.getmodule(jobs.run_raw_file_jobs) is jobs
    assert "xic_extractor.execution" not in process_backend_path.read_text(
        encoding="utf-8"
    )


def test_backends_delegate_output_dispatch() -> None:

    extraction_dir = ROOT / "xic_extractor" / "extraction"
    for module_name in ("serial_backend.py", "process_backend.py"):
        source = (extraction_dir / module_name).read_text(encoding="utf-8")
        assert "csv_writers" not in source
        assert "keep_intermediate_csv" not in source


def test_targeted_inputs_owns_raw_path_resolution() -> None:
    extraction_dir = ROOT / "xic_extractor" / "extraction"

    targeted_inputs_source = (extraction_dir / "targeted_inputs.py").read_text(
        encoding="utf-8"
    )
    assert "read_analysis_set(" in targeted_inputs_source

    for module_name in ("pipeline.py", "serial_backend.py", "process_backend.py"):
        source = (extraction_dir / module_name).read_text(encoding="utf-8")
        assert 'glob("*.raw")' not in source
        assert "read_analysis_set" not in source


def test_backends_do_not_depend_on_pipeline() -> None:
    extraction_dir = ROOT / "xic_extractor" / "extraction"

    for module_name in ("serial_backend.py", "process_backend.py"):
        source = (extraction_dir / module_name).read_text(encoding="utf-8")
        assert "xic_extractor.extraction.pipeline" not in source
        assert "resolve_injection_order" not in source


def test_target_extraction_uses_concrete_result_annotations() -> None:
    from xic_extractor.extraction import target_extraction

    assert (
        inspect.signature(target_extraction.extract_raw_file_result).return_annotation
        == "RawFileExtractionResult"
    )
    assert (
        inspect.signature(target_extraction.process_file).return_annotation
        == "tuple[FileResult, list[DiagnosticRecord]]"
    )
    assert (
        inspect.signature(target_extraction.extract_one_target)
        .parameters["results"]
        .annotation
        == "dict[str, ExtractionResult]"
    )


def test_extractor_delegates_pipeline_flow() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.pipeline")

    extractor_path = ROOT / "xic_extractor" / "extractor.py"
    source = extractor_path.read_text(encoding="utf-8")
    function_names = _function_names(extractor_path)

    assert "run_serial" not in source
    assert "run_process" not in source
    assert "write_outputs" not in source
    assert "preflight_raw_reader" not in source
    assert "_resolve_injection_order" not in function_names
    assert "_fallback_injection_order_from_mtime" not in function_names


def test_extractor_has_no_separate_istd_prepass() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.istd_prepass") is None

    extractor_path = ROOT / "xic_extractor" / "extractor.py"
    function_names = _function_names(extractor_path)

    assert "_extract_istd_anchors_only" not in function_names


def test_extractor_delegates_target_extraction() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.target_extraction")

    extractor_path = ROOT / "xic_extractor" / "extractor.py"
    function_names = _function_names(extractor_path)

    assert "_extract_raw_file_result" not in function_names
    assert "_process_file" not in function_names
    assert "_extract_one_target" not in function_names


def test_target_extraction_delegates_diagnostic_evidence_helpers() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.diagnostics")
    assert importlib.util.find_spec("xic_extractor.extraction.targeted_peak_selection")
    assert importlib.util.find_spec("xic_extractor.extraction.result_assembly")

    target_path = ROOT / "xic_extractor" / "extraction" / "target_extraction.py"
    source = target_path.read_text(encoding="utf-8")
    function_names = _function_names(target_path)

    assert "_check_target_nl" not in function_names
    assert "_candidate_ms2_evidence_builder" not in function_names
    assert "assess_targeted_candidate_population(" in source
    assert ".complete_result(" in source
    selection_source = (
        ROOT / "xic_extractor" / "extraction" / "targeted_peak_selection.py"
    ).read_text(encoding="utf-8")
    assert "select_targeted_peak(" in selection_source
    assert "build_extraction_result(" in selection_source


def test_untargeted_identity_paths_exclude_targeted_product_projection() -> None:
    forbidden_terms = (
        "TargetedProductProjection",
        "detected_flagged",
        "Product State",
        "Counted Detection",
        "targeted_review_positive",
    )
    scoped_dirs = (
        ROOT / "xic_extractor" / "alignment",
        ROOT / "xic_extractor" / "discovery",
    )
    findings: list[str] = []
    for scoped_dir in scoped_dirs:
        for path in scoped_dir.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for term in forbidden_terms:
                if term in source:
                    findings.append(f"{path.relative_to(ROOT)}: {term}")

    assert findings == []


def test_extractor_delegates_rt_anchor_and_drift_helpers() -> None:
    assert importlib.util.find_spec("xic_extractor.extraction.rt_windows") is None
    assert importlib.util.find_spec("xic_extractor.extraction.anchors")
    assert importlib.util.find_spec("xic_extractor.extraction.drift")

    extractor_path = ROOT / "xic_extractor" / "extractor.py"
    function_names = _function_names(extractor_path)

    assert "_get_rt_window" not in function_names
    assert "_recover_istd_peak_with_wider_anchor_window" not in function_names
    assert "_estimate_sample_drift" not in function_names
    assert "_paired_anchor_mismatch_diagnostic" not in function_names
    assert "_apply_anchor_mismatch_penalty" not in function_names
    assert "_anchor_mismatch_confidence" not in function_names
    assert "_check_target_nl" not in function_names
    assert "_candidate_ms2_evidence_builder" not in function_names


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
