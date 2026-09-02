from dataclasses import replace
from pathlib import Path

import numpy as np

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.targeted_production_workflow import (
    execute_targeted_production_workflow,
)
from xic_extractor.extraction.targeted_service import (
    FinalizedTargetMs1BackfillResult,
    build_targeted_application_request,
)
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.neutral_loss import CandidateMS2Evidence, MS2ProductEvidence
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedPriorContext,
    build_targeted_product_projection,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.signal_processing import find_peak_and_area


def test_retained_trace_reaches_one_finalized_target_result(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 1
    assert (
        finalized[0].completed_projection.row_binding.target.definition.target == target
    )
    assert len(finalized[0].canonical_samples) == 1


def test_neutral_loss_disabled_target_uses_ms1_backfill_result(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 1
    result = finalized[0]
    assert isinstance(result, FinalizedTargetMs1BackfillResult)
    assert result.target == target
    assert len(result.decisions) == 1
    assert result.decisions[0].variant == "numeric"
    assert result.decisions[0].value == output.file_results[0].results[
        target.label
    ].reported_peak_area


def test_neutral_loss_disabled_target_without_selected_ms1_is_no_write(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    original = output.file_results[0].results[target.label]
    output.file_results[0].results[target.label] = replace(
        original,
        selected_hypothesis=None,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    result = finalized[0]
    assert isinstance(result, FinalizedTargetMs1BackfillResult)
    assert result.decisions[0].variant == "no_write"
    assert result.decisions[0].value is None
    assert result.decisions[0].no_write_reason == "ms1_backfill_no_peak"


def production_case(tmp_path: Path) -> tuple[ExtractionConfig, Target, RunOutput]:
    config = _config(tmp_path)
    target = _target()
    raw_path = config.data_dir / "SampleA.raw"
    raw_path.parent.mkdir()
    raw_path.write_bytes(b"fixture")
    rt = np.linspace(1.0, 2.0, 101)
    intensity = 10.0 + 1000.0 * np.exp(-((rt - 1.5) ** 2) / 0.002)
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=target.mz,
        rt=rt,
        intensity=intensity,
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=target.ppm_tol,
    )
    peak_result = find_peak_and_area(rt, intensity, config, preferred_rt=1.5)
    hypotheses = build_peak_hypotheses(
        analysis_mode="targeted",
        common_evidence_source_id="test-retained-trace",
        sample_name="SampleA",
        target_label=target.label,
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        trace_group=targeted_trace_group(
            trace,
            target_label=target.label,
            resolver_mode=config.resolver_mode,
        ),
        count_no_ms2_as_detected=True,
    )
    selected = next(item for item in hypotheses if item.audit.selected)
    output = RunOutput(
        file_results=[
            FileResult(
                sample_name="SampleA",
                results={
                    target.label: ExtractionResult(
                        peak_result=peak_result,
                        nl=None,
                        candidate_ms2_evidence=CandidateMS2Evidence(
                            ms2_present=True,
                            nl_match=True,
                            nl_status="OK",
                            trigger_scan_count=1,
                            strict_nl_scan_count=1,
                            best_loss_ppm=0.0,
                            best_scan_rt=1.5,
                            best_product_base_ratio=0.5,
                            alignment_source="region",
                            best_product_evidence=MS2ProductEvidence(
                                scan_rt=1.5,
                                precursor_mz=250.0,
                                product_mz=134.0,
                                product_intensity=500.0,
                                product_base_ratio=0.5,
                                target_product_ppm=0.0,
                                observed_loss_da=116.0,
                                observed_loss_error_ppm=0.0,
                                scan_number=42,
                            ),
                        ),
                        target_label=target.label,
                        role="Analyte",
                        selected_hypothesis=selected,
                        acquisition_trace=trace,
                        targeted_product_projection=build_targeted_product_projection(
                            TargetedPriorContext(role="Analyte"),
                            rt=selected.integration.rt_apex_min,
                            area=selected.integration.area_raw_counts_seconds,
                            confidence="HIGH",
                            nl_status="OK",
                        ),
                    )
                },
            )
        ],
        diagnostics=[],
    )

    return config, target, output


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "data",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
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
