"""Helpers shared by test_targeted_production_workflow and other test modules."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, MS2ProductEvidence
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.extraction.targeted_inputs import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.extraction.targeted_peak_evidence import build_targeted_peak_evidence
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent, Ms2Window
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
    CidNlEvaluationReceipt,
    CidNlEventQuery,
    evaluate_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support import DdaCidNlPeakSupportPolicy
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.observations import (
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.processing_identity import (
    build_local_acquisition_source_identity,
)


def production_case(
    tmp_path: Path,
    *,
    sample_names: tuple[str, ...] = ("SampleA",),
    sample_peak_scales: dict[str, float] | None = None,
    secondary_peak: bool = False,
) -> tuple[ExtractionConfig, Target, RunOutput]:
    config = _config(tmp_path)
    target = _target()
    config.data_dir.mkdir()
    file_results = []
    scales = sample_peak_scales or {}
    for sample_name in sample_names:
        raw_path = config.data_dir / f"{sample_name}.raw"
        raw_path.write_bytes(sample_name.encode())
        source = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        scale = scales.get(sample_name, 1.0)
        rt = np.linspace(1.0, 2.0, 101)
        intensity = (10.0 + 1000.0 * np.exp(-((rt - 1.5) ** 2) / 0.002)) * scale
        if secondary_peak:
            intensity += 300.0 * scale * np.exp(-((rt - 1.8) ** 2) / 0.0005)
        trace = Trace.from_arrays(
            sample_name=sample_name,
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
            sample_name=sample_name,
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
        profile = resolve_signal_processing_profile(
            signal_processing_method=config.signal_processing_method,
            signal_processing_window_points=config.signal_processing_window_points,
        )
        physical_evaluation = evaluate_physical_peak(
            prepare_physical_signal(rt, intensity, profile=profile),
            query_rt_min=selected.integration.rt_apex_min,
            request_context_rt_start_min=trace.rt_min,
            request_context_rt_end_min=trace.rt_max,
        )
        assert physical_evaluation.integration is not None
        selected = replace(
            selected,
            integration=physical_peak_observation_integration(physical_evaluation),
        )
        file_results.append(
            FileResult(
                sample_name=sample_name,
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
                                acquisition_source_id=source.acquisition_source_id,
                            ),
                        ),
                        target_label=target.label,
                        role="Analyte",
                        selected_hypothesis=selected,
                        acquisition_trace=trace,
                        physical_evaluation=physical_evaluation,
                        targeted_product_projection=build_targeted_product_projection(
                            TargetedProductContext(role="Analyte"),
                            rt=selected.integration.rt_apex_min,
                            area=selected.integration.area_raw_counts_seconds,
                        ),
                    )
                },
            )
        )
    output = RunOutput(
        file_results=file_results,
        diagnostics=[],
    )
    for file_result in output.file_results:
        result = file_result.results[target.label]
        alternatives = ()
        if secondary_peak:
            alternatives = (
                _physical_evaluation(
                    result.acquisition_trace, config, query_rt_min=1.8
                ),
            )
        file_result.results[target.label] = with_method_universe(
            result, target, additional_evaluations=alternatives
        )

    return config, target, output


def with_method_universe(
    result: ExtractionResult, target: Target, *, additional_evaluations=()
) -> ExtractionResult:
    """Complete the one-peak/one-event acquisition fixture before finalization."""
    evaluation = result.physical_evaluation
    evaluations = (
        (evaluation,)
        if evaluation is not None and evaluation.integration is not None
        else ()
    ) + additional_evaluations
    policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=target.mz * target.ppm_tol / 1e6,
        neutral_loss_tolerance_ppm=target.nl_ppm_max or 20.0,
    )
    candidate_evidence = result.candidate_ms2_evidence
    product = (
        None if candidate_evidence is None else candidate_evidence.best_product_evidence
    )
    products = (
        (product,)
        if product is not None
        and abs(product.precursor_mz - target.mz) <= policy.precursor_tolerance_da
        else ()
    )
    intervals = tuple(
        CidNlCandidateInterval(
            target.mz, item.integration.rt_left_min, item.integration.rt_right_min
        )
        for item in evaluations
    )
    relations = evaluate_cid_nl_method_relations(
        candidate_intervals=intervals,
        event_queries=tuple(
            CidNlEventQuery(
                item.assess_precursor_loss(
                    target.neutral_loss_da or 116.0, policy.neutral_loss_tolerance_ppm
                ),
                item.scan_rt,
                item.scan_rt,
            )
            for item in products
        ),
        policy=policy,
    )
    answers = build_targeted_peak_evidence(
        target=target,
        window=Ms2Window(
            "fixture",
            target.rt_min,
            target.rt_max,
            tuple(
                Ms2ScanEvent(
                    Ms2Scan(
                        p.scan_number or 1,
                        p.scan_rt,
                        p.scan_precursor_mz or p.precursor_mz,
                        np.array([p.product_mz]),
                        np.array([p.product_intensity]),
                        p.product_intensity / p.product_base_ratio,
                        p.precursor_metadata,
                    ),
                    None,
                    p.scan_number or 1,
                )
                for p in products
            ),
        ),
        products=products,
        relations=relations,
        candidate_apex_rts=tuple(e.integration.rt_apex_min for e in evaluations),
    )
    return replace(
        result,
        candidate_physical_evaluations=evaluations,
        cid_nl_products=products,
        cid_nl_evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=target.rt_min,
            query_rt_max=target.rt_max,
            support_policy=policy,
            acquisition_supported=True,
            search_completed=True,
            reason_code=None,
        ),
        cid_nl_relations=relations,
        cid_nl_candidate_evidence=answers,
        candidate_ms2_evidence=answers[0] if answers else None,
        selected_cid_nl_candidate_index=0 if evaluations else None,
    )


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


def _physical_evaluation(
    trace: Trace,
    config: ExtractionConfig,
    *,
    query_rt_min: float,
):
    profile = resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )
    return evaluate_physical_peak(
        prepare_physical_signal(trace.rt, trace.intensity, profile=profile),
        query_rt_min=query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
    )
