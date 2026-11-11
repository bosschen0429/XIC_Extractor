from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.peak_detection.observations as observation_owner
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    mint_peak_observation,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True)
class _IdentityFixture:
    sample: SampleMetadata
    source: AcquisitionSourceIdentity


def test_query_provenance_does_not_change_selected_physical_content(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    narrow_trace = _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(identity.source),
    )
    wide_trace = _trace(
        rt=(7.5, 7.7, 7.9, 8.0, 8.1, 8.3, 8.5),
        intensity=(0.0, 0.0, 50.0, 500.0, 50.0, 0.0, 0.0),
        rt_min=7.5,
        rt_max=8.5,
        acquisition_source_id=_source_id(identity.source),
    )

    narrow = mint_peak_observation(
        sample=identity.sample,
        trace=narrow_trace,
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    wide = mint_peak_observation(
        sample=identity.sample,
        trace=wide_trace,
        integration=_integration(raw_scan_indices=(2, 3, 4)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    np.testing.assert_array_equal(narrow.source_trace.rt, narrow_trace.rt)
    np.testing.assert_array_equal(wide.source_trace.rt, wide_trace.rt)
    np.testing.assert_array_equal(wide.source_trace.intensity, wide_trace.intensity)
    assert len(wide.source_trace.rt) > len(wide.rt)
    assert narrow == wide
    # Each XIC query is reported at its own locator.
    assert narrow.physical_peak_key != wide.physical_peak_key
    assert narrow.generation_provenance != wide.generation_provenance


def test_one_peak_integrated_in_two_windows_has_two_locators(tmp_path: Path) -> None:
    # Backfill can read one peak's scans in two RT windows; the baseline each
    # window sees gives a different corrected area for the same raw signal.
    identity = _identity_fixture(tmp_path)
    rt = (7.5, 7.7, 7.9, 8.0, 8.1, 8.3, 8.5)
    intensity = (0.0, 0.0, 50.0, 500.0, 50.0, 0.0, 0.0)

    def measured(rt_min: float, area_baseline_corrected: float) -> PeakObservation:
        return mint_peak_observation(
            sample=identity.sample,
            trace=_trace(
                rt=rt,
                intensity=intensity,
                rt_min=rt_min,
                rt_max=rt_min + 2.0,
                acquisition_source_id=_source_id(identity.source),
            ),
            integration=replace(
                _integration(raw_scan_indices=(2, 3, 4)),
                area_baseline_corrected=area_baseline_corrected,
            ),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )

    first = measured(6.9, 90.0)
    second = measured(6.95, 95.0)
    again = measured(6.9, 90.0)

    assert first != second
    assert first.physical_peak_key != second.physical_peak_key
    assert first.physical_peak_key == again.physical_peak_key


def test_selected_signal_mutation_changes_content(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    original = _mint_standard_reference(identity.source, identity.sample)
    mutated_trace = _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 501.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(identity.source),
    )

    mutated = mint_peak_observation(
        sample=identity.sample,
        trace=mutated_trace,
        integration=_integration(
            raw_scan_indices=(1, 2, 3),
            apex_intensity=501.0,
            area_raw_counts_seconds=3306.0,
        ),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert original.physical_identity != mutated.physical_identity


def test_integration_fact_mutation_changes_content_identity(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)
    original = _mint_standard_reference(identity.source, identity.sample)
    changed_integration = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
        integration_method="alternate_integration",
    )

    changed = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=changed_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert original.physical_identity != changed.physical_identity


def test_equivalent_numeric_integration_inputs_have_one_content_identity(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    float_integration = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
    )
    integer_integration = replace(
        float_integration,
        height_raw=500,
        height_smoothed=500,
        area_raw_counts_seconds=3300,
    )

    float_ref = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=float_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    integer_ref = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=integer_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert integer_integration == float_integration
    assert integer_ref == float_ref
    assert integer_ref.physical_peak_key == (float_ref.physical_peak_key)


def test_integration_result_collapses_signed_zero() -> None:
    positive = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
        area_uncertainty=0.0,
    )
    negative = replace(positive, area_uncertainty=-0.0)

    assert negative == positive
    assert negative.area_uncertainty is not None
    assert math.copysign(1.0, negative.area_uncertainty) == 1.0


def test_signed_zero_trace_values_have_one_observation_identity(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    integration = replace(
        _integration(raw_scan_indices=(0, 1, 2, 3, 4)),
        rt_left_min=7.7,
        rt_right_min=8.3,
        rt_width_min=0.6,
        area_raw_counts_seconds=3900.0,
    )
    positive_trace = _standard_trace(identity.source)
    signed_zero_intensity = positive_trace.intensity.copy()
    signed_zero_intensity[0] = -0.0
    signed_zero_trace = replace(
        positive_trace,
        intensity=signed_zero_intensity,
    )

    positive = mint_peak_observation(
        sample=identity.sample,
        trace=positive_trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    negative = mint_peak_observation(
        sample=identity.sample,
        trace=signed_zero_trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert negative == positive


def test_owner_boundary_between_scan_points_maps_to_selected_trace(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    trace = _standard_trace(identity.source)
    peak = PeakResult(
        rt=8.0,
        intensity=500.0,
        intensity_smoothed=480.0,
        area=3300.0,
        peak_start=7.94,
        peak_end=8.06,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=8.0,
        selection_apex_intensity=480.0,
        selection_apex_index=2,
        raw_apex_rt=8.0,
        raw_apex_intensity=500.0,
        raw_apex_index=2,
        prominence=450.0,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=trace.scan_count,
        max_smoothed=480.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    integration = build_peak_hypotheses(
        sample_name=trace.sample_name,
        target_label="test-only-target",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=result,
        rt=trace.rt,
        intensity=trace.intensity,
    )[0].integration

    reference = mint_peak_observation(
        sample=identity.sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert integration.raw_scan_indices == (1, 2, 3)
    assert reference.rt == (7.9, 8.0, 8.1)
    assert reference.integration.rt_left_min == 7.94
    assert reference.integration.rt_right_min == 8.06


def test_proposal_source_reorder_is_canonicalized(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)

    forward = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="consensus",
        proposal_sources=("boundary_resolver", "local_minimum"),
    )
    reversed_order = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="consensus",
        proposal_sources=("local_minimum", "boundary_resolver"),
    )

    assert forward == reversed_order


def test_resolver_change_preserves_content_but_changes_generation_provenance(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)

    local_minimum = _mint_standard_reference(identity.source, identity.sample)
    arbitrated = mint_peak_observation(
        sample=identity.sample,
        trace=_standard_trace(identity.source),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="arbitrated",
        proposal_sources=("local_minimum",),
    )

    assert local_minimum == arbitrated
    assert local_minimum.generation_provenance != arbitrated.generation_provenance


def _identity_fixture(
    tmp_path: Path,
    *,
    sample_key: str = "sample-a",
    sample_name: str = "Sample A",
) -> _IdentityFixture:
    source_path = tmp_path / f"{sample_key}.synthetic"
    source_path.write_bytes(f"synthetic-no-raw-{sample_key}".encode())
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace=f"synthetic:{sample_key}",
    )
    return _IdentityFixture(
        sample=SampleMetadata(sample_name=sample_name, raw_stem=sample_key),
        source=source,
    )


def _standard_trace(source: AcquisitionSourceIdentity) -> Trace:
    return _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(source),
    )


def _mint_standard_reference(
    source: AcquisitionSourceIdentity,
    sample: SampleMetadata,
) -> PeakObservation:
    return mint_peak_observation(
        sample=sample,
        trace=_standard_trace(source),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )


def _trace(
    *,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    rt_min: float,
    rt_max: float,
    sample_name: str = "sample-a",
    acquisition_source_id: str = "",
) -> Trace:
    return Trace.from_arrays(
        sample_name=sample_name,
        mz=500.0,
        rt=rt,
        intensity=intensity,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=10.0,
        source="synthetic_characterization",
        acquisition_source_id=acquisition_source_id,
    )


def _source_id(source: AcquisitionSourceIdentity) -> str:
    return source.acquisition_source_id


def _integration(
    *,
    raw_scan_indices: tuple[int, ...],
    apex_intensity: float = 500.0,
    area_raw_counts_seconds: float = 3300.0,
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=7.9,
        rt_apex_min=8.0,
        rt_right_min=8.1,
        raw_apex_rt_min=8.0,
        rt_width_min=0.2,
        height_raw=apex_intensity,
        height_smoothed=apex_intensity,
        area_raw_counts_seconds=area_raw_counts_seconds,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        raw_scan_indices=raw_scan_indices,
    )


def test_advisory_quality_does_not_change_physical_content_identity(tmp_path):
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    identity = _identity_fixture(tmp_path)
    old = _mint_standard_reference(identity.source, identity.sample)
    quality = SignalQuality(
        "available", noise_sigma=10.0, signal_to_noise=2.0, low_signal_to_noise=True
    )
    enriched = replace(
        old,
        integration=replace(old.integration, signal_quality=quality),
    )
    assert enriched.physical_peak_key == old.physical_peak_key
    assert enriched.physical_identity == old.physical_identity
    assert observation_owner.index_physical_peaks((old, enriched)) == {
        old.physical_identity: enriched
    }


@pytest.mark.parametrize(
    "reuse_trace_provenance", ["none", "observation", "acquisition"]
)
def test_retained_candidate_batch_preserves_observation_values(
    tmp_path,
    monkeypatch,
    reuse_trace_provenance,
):
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )

    identity = _identity_fixture(tmp_path)
    rt = np.linspace(1.0, 2.0, 101)
    raw = 10 + 1000 * np.exp(-((rt - 1.3) ** 2) / 0.0005)
    raw += 300 * np.exp(-((rt - 1.7) ** 2) / 0.0005)
    trace = _trace(
        rt=tuple(rt),
        intensity=tuple(raw),
        rt_min=1.0,
        rt_max=2.0,
        acquisition_source_id=_source_id(identity.source),
    )
    profile = SignalProcessingProfile(method="gaussian", window_points=9)
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    evaluations = tuple(
        evaluate_physical_peak(
            prepared,
            query_rt_min=query,
            request_context_rt_start_min=trace.rt_min,
            request_context_rt_end_min=trace.rt_max,
        )
        for query in (1.3, 1.7)
    )
    expected = tuple(
        mint_peak_observation(
            sample=identity.sample,
            trace=trace,
            integration=observation_owner.physical_peak_observation_integration(
                evaluation
            ),
            resolver_mode="local_minimum",
            proposal_sources=("canonical_physical_segment",),
            physical_evaluation=evaluation,
            signal_processing_profile=profile,
        )
        for evaluation in evaluations
    )
    acquired_provenance = (
        observation_owner.prepare_peak_observation_provenance(
            trace=trace,
            selected_indices=expected[0].generation_provenance.selected_trace_indices,
            resolver_mode="local_minimum",
            proposal_sources=("canonical_physical_segment",),
        )
        if reuse_trace_provenance == "acquisition"
        else None
    )
    actual = observation_owner.mint_physical_peak_observations(
        sample=identity.sample,
        trace=trace,
        evaluations=evaluations,
        signal_processing_profile=profile,
        resolver_mode="local_minimum",
        trace_observation=(
            expected[0] if reuse_trace_provenance == "observation" else None
        ),
        acquired_trace_provenance=acquired_provenance,
    )
    assert len(actual) == 2
    for reference, projected in zip(expected, actual, strict=True):
        assert reference == projected
        assert reference.generation_provenance == projected.generation_provenance
    assert (
        actual[0].integration.area_ms1_morphology
        > actual[1].integration.area_ms1_morphology
    )
