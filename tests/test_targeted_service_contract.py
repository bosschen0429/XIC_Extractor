import ast
import pickle
from dataclasses import replace
from pathlib import Path

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    build_canonical_targeted_request,
    build_targeted_application_request,
)
from xic_extractor.extractor import RunOutput
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecord
from xic_extractor.processing_lineage import AcquisitionSourceRef
from xic_extractor.rt_prior_library import LibraryEntry


def test_cli_and_gui_use_same_targeted_application_service() -> None:
    for path in (
        Path("scripts/run_extraction.py"),
        Path("gui/workers/pipeline_worker.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "extractor"
        ]
        assert calls == [], f"{path} bypasses TargetedApplicationService"


def test_headless_service_preserves_inputs_and_callbacks() -> None:
    config = _config()
    targets = [_target()]
    calls: dict[str, object] = {}
    output = RunOutput(file_results=[], diagnostics=[])

    def runner(actual_config, actual_targets, **kwargs):
        calls["config"] = actual_config
        calls["targets"] = actual_targets
        calls.update(kwargs)
        return output

    def progress(_done: int, _total: int, _name: str) -> None:
        return None

    def should_stop() -> bool:
        return False
    request = build_targeted_application_request(
        config,
        targets,
        progress_callback=progress,
        should_stop=should_stop,
    )

    assert TargetedApplicationService(runner).execute(request) is output
    assert calls["config"] is config
    assert calls["targets"] == targets
    assert calls["progress_callback"] is progress
    assert calls["should_stop"] is should_stop


def test_equal_adapters_produce_same_pickle_safe_canonical_request() -> None:
    config = replace(
        _config(),
        config_hash="config-sha",
        target_config_hash="targets-sha",
    )
    sources = (
        AcquisitionSourceRef("source-b", "thermo_raw", "controller=1"),
        AcquisitionSourceRef("source-a", "thermo_raw", "controller=1"),
    )
    cli_request = build_targeted_application_request(
        config,
        [_target()],
        progress_callback=lambda *_args: None,
    )
    gui_request = build_targeted_application_request(
        config,
        [_target()],
        should_stop=lambda: False,
    )

    cli_canonical = build_canonical_targeted_request(
        cli_request,
        sources=sources,
        preset_id="cid-dR",
    )
    gui_canonical = build_canonical_targeted_request(
        gui_request,
        sources=tuple(reversed(sources)),
        preset_id="cid-dR",
    )

    assert cli_canonical == gui_canonical
    assert pickle.loads(pickle.dumps(cli_canonical)) == cli_canonical
    assert cli_canonical.acquisition_sources == tuple(
        sorted(sources, key=lambda source: source.source_id)
    )
    assert cli_canonical.processing_request.identity_basis.acquisition_sources == (
        cli_canonical.acquisition_sources
    )


def test_canonical_request_identity_changes_with_source_namespace() -> None:
    config = replace(
        _config(),
        config_hash="config-sha",
        target_config_hash="targets-sha",
    )
    request = build_targeted_application_request(config, [_target()])
    first = build_canonical_targeted_request(
        request,
        sources=(
            AcquisitionSourceRef("source-a", "thermo_raw", "controller=1"),
        ),
        preset_id="cid-dR",
    )
    second = build_canonical_targeted_request(
        request,
        sources=(
            AcquisitionSourceRef("source-a", "thermo_raw", "controller=2"),
        ),
        preset_id="cid-dR",
    )

    assert first.processing_request.request_id != second.processing_request.request_id


def test_canonical_request_identity_includes_scoring_inputs() -> None:
    config = replace(
        _config(),
        config_hash="config-sha",
        target_config_hash="targets-sha",
    )
    sources = (
        AcquisitionSourceRef("source-a", "thermo_raw", "controller=1"),
    )
    requests = (
        build_targeted_application_request(config, [_target()]),
        build_targeted_application_request(
            config,
            [_target()],
            injection_order={"Sample A": 1},
        ),
        build_targeted_application_request(
            config,
            [_target()],
            rt_prior_library={("Analyte", "analyte"): _library_entry()},
        ),
        build_targeted_application_request(
            config,
            [_target()],
            model_selection_expected_diff_approvals={
                "row-1": _approval_record()
            },
        ),
    )

    request_ids = {
        build_canonical_targeted_request(
            request,
            sources=sources,
            preset_id="cid-dR",
        ).processing_request.request_id
        for request in requests
    }

    assert len(request_ids) == len(requests)


def test_canonical_scoring_input_identity_is_mapping_order_independent() -> None:
    config = replace(
        _config(),
        config_hash="config-sha",
        target_config_hash="targets-sha",
    )
    sources = (
        AcquisitionSourceRef("source-a", "thermo_raw", "controller=1"),
    )
    first = build_targeted_application_request(
        config,
        [_target()],
        injection_order={"Sample B": 2, "Sample A": 1},
    )
    second = build_targeted_application_request(
        config,
        [_target()],
        injection_order={"Sample A": 1, "Sample B": 2},
    )

    assert build_canonical_targeted_request(
        first,
        sources=sources,
        preset_id="cid-dR",
    ) == build_canonical_targeted_request(
        second,
        sources=sources,
        preset_id="cid-dR",
    )


def test_contract_modules_do_not_import_gui_writer_or_raw_adapter() -> None:
    forbidden_prefixes = (
        "gui",
        "scripts",
        "xic_extractor.output",
        "xic_extractor.raw_reader",
    )
    for path in (
        Path("xic_extractor/acquisition_contract.py"),
        Path("xic_extractor/processing_lineage.py"),
        Path("xic_extractor/execution_runtime_contract.py"),
        Path("xic_extractor/extraction/targeted_service.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = _imports(tree)
        assert not any(
            imported.startswith(forbidden_prefixes) for imported in imports
        ), f"{path}: forbidden dependency {imports}"


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("data"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )


def _library_entry() -> LibraryEntry:
    return LibraryEntry(
        config_hash="config-sha",
        target_label="Analyte",
        role="analyte",
        istd_pair="ISTD",
        median_delta_rt=0.1,
        sigma_delta_rt=0.01,
        median_abs_rt=None,
        sigma_abs_rt=None,
        n_samples=8,
        updated_at="2026-07-26T00:00:00Z",
    )


def _approval_record() -> ExpectedDiffApprovalRecord:
    return ExpectedDiffApprovalRecord(
        stable_row_id="row-1",
        sample_name="Sample A",
        target_label="Analyte",
        legacy_selected_candidate_id="legacy-1",
        successor_selected_candidate_id="successor-1",
        public_outputs_touched=("selected rt",),
        matrix_value_impact="none",
        evidence_sources=("manual_review",),
        evidence_summary="reviewed",
        validation_tier="targeted_benchmark",
        reviewer_role="owner",
        reviewer_verdict="approved",
        final_label="expected_diff",
    )
