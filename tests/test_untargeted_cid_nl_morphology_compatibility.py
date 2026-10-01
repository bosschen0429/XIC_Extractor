from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
    _source_for,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raw,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.acquisition_contract import BatchXICAdapter
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.ms1_backfill import (
    _backfill_ms1_candidates_with_acquisition,
)
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.execution.attempt import start_runtime_attempt
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
    CidNlAcquisitionError,
    adapt_current_discovery_sample_subject,
    method_label,
)
from xic_extractor.untargeted_execution.partition.execution import (
    execute_attempt,
    runtime_call_cost,
)
from xic_extractor.untargeted_execution.partition.models import UntargetedPartitionJob
from xic_extractor.untargeted_execution.partition.request import (
    build_partition_context,
)
from xic_extractor.xic_models import XICRequest


@pytest.mark.parametrize(
    ("candidate_field", "receipt_field"),
    (
        ("ms1_area", "area_raw_counts_seconds"),
        ("ms1_height", "height_raw"),
    ),
)
@pytest.mark.parametrize("mutation", ("missing", "drift"))
def test_adapter_parks_unresolved_morphology_receipt_compatibility(
    tmp_path: Path,
    candidate_field: str,
    receipt_field: str,
    mutation: str,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )[0]
    current_value = getattr(candidate, candidate_field)
    assert current_value is not None
    replacement = None if mutation == "missing" else current_value * 1.1
    unresolved = replace(candidate, **{candidate_field: replacement})
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        CidNlAcquisitionError,
        match=(f"morphology_receipt_compatibility_unresolved: {receipt_field}"),
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(unresolved,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


@pytest.mark.parametrize(
    ("candidate_field", "receipt_field", "integration_field"),
    (
        ("ms1_area", "area_raw_counts_seconds", "area_raw_counts_seconds"),
        ("ms1_height", "height_raw", "height_raw"),
    ),
)
def test_adapter_parks_owner_observed_morphology_divergence(
    tmp_path: Path,
    candidate_field: str,
    receipt_field: str,
    integration_field: str,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw(
        (8.0,),
        acquisition_source=_source_for(fixture, "S1"),
    )
    source_raw.intensity = source_raw.intensity + 250.0
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates, evaluation = _canonical_physical_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )
    assert evaluation.facts is not None
    assert evaluation.integration is not None
    candidate = candidates[0]
    adapter_integration = _adapter_integration(
        source_raw,
        candidate,
        settings=settings,
        peak_config=peak_config,
    )
    assert getattr(candidate, candidate_field) != pytest.approx(
        getattr(adapter_integration, integration_field)
    )
    coherent_fields = {
        "ms1_apex_rt": adapter_integration.rt_apex_min,
        "ms1_peak_rt_start": adapter_integration.rt_left_min,
        "ms1_peak_rt_end": adapter_integration.rt_right_min,
    }
    if receipt_field == "height_raw":
        coherent_fields["ms1_area"] = adapter_integration.area_raw_counts_seconds
    else:
        coherent_fields["ms1_height"] = adapter_integration.height_raw
    candidate = replace(candidate, **coherent_fields)
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        CidNlAcquisitionError,
        match=(f"morphology_receipt_compatibility_unresolved: {receipt_field}"),
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(candidate,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_runtime_retains_morphology_park_reason_and_call_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"morphology-compatibility-park:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    source_raw = _FakeRaw((8.0,), acquisition_source=source)
    source_raw.intensity = source_raw.intensity + 250.0
    candidates, evaluation = _canonical_physical_candidates(
        source_raw,
        raw_file=raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    assert evaluation.facts is not None
    assert evaluation.integration is not None
    assert evaluation.facts.area_counts_seconds != pytest.approx(
        evaluation.integration.area_raw_counts_seconds
    )
    adapter_integration = _adapter_integration(
        source_raw,
        candidates[0],
        settings=settings,
        peak_config=peak_config,
    )
    candidates = (
        replace(
            candidates[0],
            ms1_apex_rt=adapter_integration.rt_apex_min,
            ms1_peak_rt_start=adapter_integration.rt_left_min,
            ms1_peak_rt_end=adapter_integration.rt_right_min,
            ms1_height=adapter_integration.height_raw,
        ),
    )
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = build_partition_context(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        sample_identity_namespace="study:morphology-compatibility-park-test",
    )
    _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )
    job = UntargetedPartitionJob(
        processing_owners=context.processing_owners,
        sample=context.samples[0],
        method_subject=context.method_subject,
        candidates=context.candidates_by_sample["S1"],
        discovery_receipt=context.discovery_receipts_by_sample["S1"],
        settings=context.settings,
        peak_config=context.peak_config,
        raw_path=raw_path,
        dll_dir=tmp_path,
        work_unit_key="untargeted-cid-nl:S1",
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=(job.work_unit_key,),
        owner_completeness_attestation="untargeted-cid-nl-morphology-park-test",
    )

    result = execute_attempt(
        runtime,
        (job,),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "morphology-park.runtime.json",
            "untargeted-cid-nl-morphology:park-test",
        ),
    )

    assert result.succeeded is False
    assert result.sample_results == ()
    settlement = result.finalization.coordinator.state.runtime.settlements[0]
    assert settlement.failure_detail is not None
    assert (
        "morphology_receipt_compatibility_unresolved: area_raw_counts_seconds"
    ) in settlement.failure_detail.message
    expected_trace_point_count = sum(
        candidates[0].ms1_search_rt_min <= float(rt) <= candidates[0].ms1_search_rt_max
        for rt in source_raw.rt
    )
    assert runtime_call_cost(result.finalization) == {
        "raw_open_count": 1,
        "logical_xic_request_count": 1,
        "xic_batch_count": 1,
        "adapter_chromatogram_call_count": 1,
        "trace_point_count": expected_trace_point_count,
    }


def _canonical_physical_candidates(
    raw: _FakeRaw,
    *,
    raw_file: Path,
    settings,
    peak_config,
):
    assert raw.acquisition_source is not None
    seeds = collect_strict_nl_seeds(raw, raw_file=raw_file, settings=settings)
    groups = group_discovery_seeds(seeds, settings=settings)
    result = _backfill_ms1_candidates_with_acquisition(
        cast(BatchXICAdapter, raw),
        groups,
        acquisition_source=raw.acquisition_source,
        run_spec_id="processing-run:" + ("0" * 64),
        settings=settings,
        peak_config=peak_config,
        signal_processing_profile=SignalProcessingProfile(window_points=15),
    )
    return result.candidates, result.physical_evaluations[0].evaluation


def _adapter_integration(raw, candidate, *, settings, peak_config):
    trace = raw.extract_xic_many(
        (
            XICRequest(
                mz=candidate.precursor_mz,
                rt_min=candidate.ms1_search_rt_min,
                rt_max=candidate.ms1_search_rt_max,
                ppm_tol=settings.precursor_mz_tolerance_ppm,
            ),
        )
    )[0]
    detection = detect_ms1_trace_peak(
        trace.rt,
        trace.intensity,
        peak_config=peak_config,
        preferred_rt=candidate.best_seed_rt,
        peak_finder=find_peak_and_area,
    )
    assert detection.status == "detected"
    assert detection.result is not None
    hypotheses = build_peak_hypotheses(
        analysis_mode="untargeted",
        common_evidence_source_id=candidate.candidate_id,
        sample_name=candidate.sample_stem,
        target_label=method_label(candidate),
        role="Analyte",
        istd_pair="",
        resolver_mode=settings.resolver_mode,
        peak_result=detection.result,
        rt=detection.rt,
        intensity=detection.intensity,
        baseline_integration_method="asls",
        ms1_morphology_smoothing_window_points=(
            peak_config.ms1_morphology_smoothing_window_points
        ),
    )
    selected = tuple(item for item in hypotheses if item.audit.selected)
    assert len(selected) == 1
    return selected[0].integration
