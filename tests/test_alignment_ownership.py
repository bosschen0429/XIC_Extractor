from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.ownership import (
    AlignmentCrossSampleIdentityContext,
    build_sample_local_owners,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeed
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


def test_same_sample_same_resolved_peak_becomes_one_owner():
    candidates = (
        _candidate("s1#6095", seed_rt=12.5927, evidence_score=80),
        _candidate("s1#6096", seed_rt=12.5940, evidence_score=70),
    )
    source = FakeXICSource(
        rt=np.array([12.55, 12.58, 12.593, 12.61, 12.64], dtype=float),
        intensity=np.array([0.0, 30.0, 100.0, 30.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    owner = result.owners[0]
    assert owner.owner_id == "OWN-s1-000001"
    assert owner.primary_identity_event.candidate_id == "s1#6095"
    assert [event.candidate_id for event in owner.supporting_events] == ["s1#6096"]
    assert owner.assignment_reason == "owner_exact_apex_match"
    assert owner.quantification_eligible is True
    assert [(a.candidate_id, a.assignment_status) for a in result.assignments] == [
        ("s1#6095", "primary"),
        ("s1#6096", "supporting"),
    ]


def test_owner_build_threads_exact_physical_member_subject_to_cross_sample(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "s1.raw"
    source_path.write_bytes(b"synthetic-owner-context")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic",
        observation_namespace="alignment-owner:s1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="s1",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="alignment-owner-test-v1",
        processing_algorithm_version="alignment-owner-test-v1",
        processing_contract_version="alignment-owner-test-v1",
        scientific_inputs={"candidate_universe": ["s1#context"]},
    )
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="s1", raw_stem="s1", injection_order=1),),
        identity_namespace="alignment-owner-test-v1",
    )
    sample = resolve_canonical_sample_identity(snapshot, "s1")
    context = AlignmentCrossSampleIdentityContext(
        processing_owners=compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=snapshot,
            sample_resolutions=(sample,),
        ),
        samples_by_stem={"s1": sample},
    )
    candidate = DiscoveryCandidate.from_values(
        raw_file=source_path,
        sample_stem="s1",
        precursor_mz=242.114,
        product_mz=126.066,
        observed_neutral_loss_da=116.048,
        best_seed=DiscoverySeed(
            raw_file=source_path,
            sample_stem="s1",
            scan_number=6095,
            rt=12.59,
            precursor_mz=242.114,
            product_mz=126.066,
            product_intensity=100.0,
            neutral_loss_tag="DNA_dR",
            configured_neutral_loss_da=116.048,
            observed_neutral_loss_da=116.048,
            observed_loss_error_ppm=0.0,
        ),
        seed_scan_ids=(6095,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.048,
        neutral_loss_mass_error_ppm=0.0,
        rt_seed_min=12.58,
        rt_seed_max=12.60,
        ms1_search_rt_min=12.55,
        ms1_search_rt_max=12.64,
        ms1_seed_delta_min=0.0,
        ms1_peak_rt_start=12.58,
        ms1_peak_rt_end=12.61,
        ms1_height=100.0,
        ms1_trace_quality="GOOD",
        seed_event_count=1,
        ms1_peak_found=True,
        ms1_apex_rt=12.593,
        ms1_area=117.0,
        ms2_product_max_intensity=100.0,
        review_priority="HIGH",
        reason="owner-context fixture",
    )
    result = build_sample_local_owners(
        (candidate,),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([12.55, 12.58, 12.593, 12.61, 12.64], dtype=float),
                intensity=np.array([0.0, 30.0, 100.0, 30.0, 0.0], dtype=float),
            ),
        },
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
        cross_sample_identity_context=context,
    )

    owner = result.owners[0]
    assert owner.owner_apex_rt == pytest.approx(12.593)
    assert owner.owner_area == pytest.approx(117.0)
    assert owner.owner_height == pytest.approx(100.0)
    assert owner.owner_peak_start_rt == pytest.approx(12.58)
    assert owner.owner_peak_end_rt == pytest.approx(12.61)
    assert owner.cross_sample_member_subject is not None
    assert (
        owner.cross_sample_member_subject.processing_request_id
        == request.processing_request_id
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        result.owners,
        config=AlignmentConfig(),
    )
    assert groups[0].disposition == "provisional"
    assert groups[0].group_hypothesis_id.startswith("xic.cross-sample-group.v1:")


def test_same_peak_primary_order_uses_resolved_sort_key_not_input_order() -> None:
    candidates = (
        _candidate("s1#low", seed_rt=12.5940, evidence_score=70),
        _candidate("s1#high", seed_rt=12.5927, evidence_score=80),
    )
    source = FakeXICSource(
        rt=np.array([12.55, 12.58, 12.593, 12.61, 12.64], dtype=float),
        intensity=np.array([0.0, 30.0, 100.0, 30.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
    )

    owner = result.owners[0]
    assert owner.primary_identity_event.candidate_id == "s1#high"
    assert [event.candidate_id for event in owner.supporting_events] == ["s1#low"]
    assert [(a.candidate_id, a.assignment_status) for a in result.assignments] == [
        ("s1#high", "primary"),
        ("s1#low", "supporting"),
    ]


def test_tail_events_on_one_peak_become_supporting_events():
    candidates = (
        _candidate("s1#7001", mz=251.084, seed_rt=8.50, evidence_score=85),
        _candidate("s1#7002", mz=251.0841, seed_rt=8.62, evidence_score=60),
        _candidate("s1#7003", mz=251.0842, seed_rt=8.66, evidence_score=55),
    )
    source = FakeXICSource(
        rt=np.array([8.40, 8.48, 8.52, 8.58, 8.64, 8.70], dtype=float),
        intensity=np.array([0.0, 80.0, 100.0, 50.0, 20.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_tail_seed_guard_sec=30.0),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    assert result.owners[0].event_candidate_ids == ("s1#7001", "s1#7002", "s1#7003")
    assert [assignment.reason for assignment in result.assignments] == [
        "primary_identity_event",
        "owner_tail_assignment",
        "owner_tail_assignment",
    ]


def test_unresolved_doublet_becomes_ambiguous_owner_record():
    candidates = (
        _candidate("s1#8001", mz=296.074, seed_rt=10.00, evidence_score=80),
        _candidate("s1#8002", mz=296.0741, seed_rt=10.08, evidence_score=78),
    )
    source = FakeXICSource(
        rt=np.array([9.94, 10.00, 10.04, 10.08, 10.14], dtype=float),
        intensity=np.array([0.0, 100.0, 70.0, 95.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
        peak_resolver=FakePeakResolver(
            {
                "s1#8001": (10.00, 9.96, 10.05, 1000.0, 100.0),
                "s1#8002": (10.08, 10.03, 10.12, 950.0, 95.0),
            },
        ),
    )

    assert result.owners == ()
    assert len(result.ambiguous_records) == 1
    assert result.ambiguous_records[0].reason == "owner_multiplet_ambiguity"
    assert {assignment.assignment_status for assignment in result.assignments} == {
        "ambiguous",
    }


def test_same_local_peak_with_different_nl_tags_is_one_owner_with_identity_conflict():
    candidates = (
        _candidate("s1#9001", neutral_loss_tag="DNA_dR", seed_rt=12.5927),
        _candidate("s1#9002", neutral_loss_tag="DNA_base_loss", seed_rt=12.5940),
    )
    source = FakeXICSource(
        rt=np.array([12.55, 12.58, 12.593, 12.61, 12.64], dtype=float),
        intensity=np.array([0.0, 30.0, 100.0, 30.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    assert result.owners[0].identity_conflict is True
    assert result.owners[0].event_candidate_ids == ("s1#9001", "s1#9002")


def test_missing_raw_source_records_unresolved_assignment():
    result = build_sample_local_owners(
        (_candidate("s1#9999"),),
        raw_sources={},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert result.owners == ()
    assignments = [
        (a.candidate_id, a.assignment_status, a.reason) for a in result.assignments
    ]
    assert assignments == [
        ("s1#9999", "unresolved", "missing_raw_source"),
    ]


def test_peak_not_found_records_unresolved_assignment():
    result = build_sample_local_owners(
        (_candidate("s1#9998"),),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([12.55, 12.58, 12.593, 12.61, 12.64], dtype=float),
                intensity=np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=float),
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert result.owners == ()
    assignments = [
        (a.candidate_id, a.assignment_status, a.reason) for a in result.assignments
    ]
    assert assignments == [
        ("s1#9998", "unresolved", "peak_not_found"),
    ]


def test_build_sample_local_owners_uses_batch_source_when_available() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_sizes.append(len(requests))
            return tuple(
                XICTrace.from_arrays(
                    [request.rt_min, 8.0, request.rt_max],
                    [0.0, 100.0, 0.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()
    candidates = (
        _candidate("s1#batch-1", mz=258.0, seed_rt=8.0),
        _candidate("s1#batch-2", mz=259.0, seed_rt=8.1),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        peak_resolver=_always_peak_at_seed,
        raw_xic_batch_size=64,
    )

    assert source.batch_sizes == [2]
    assert len(result.assignments) == 2


def test_build_sample_local_owners_keeps_same_scan_window_in_one_batch() -> None:
    from xic_extractor.xic_models import XICTrace

    class ScanWindowBatchSource:
        def __init__(self) -> None:
            self.batch_windows: list[list[tuple[int, int]]] = []

        def scan_window_for_request(self, request):
            return int(request.rt_min * 100), int(request.rt_max * 100)

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_windows.append(
                [self.scan_window_for_request(request) for request in requests]
            )
            return tuple(
                XICTrace.from_arrays(
                    [request.rt_min, request.rt_max],
                    [100.0, 100.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = ScanWindowBatchSource()
    candidates = (
        _candidate(
            "s1#same-window-a",
            seed_rt=2.0,
            ms1_search_rt_min=1.0,
            ms1_search_rt_max=3.0,
        ),
        _candidate(
            "s1#other-window",
            seed_rt=11.0,
            ms1_search_rt_min=10.0,
            ms1_search_rt_max=12.0,
        ),
        _candidate(
            "s1#same-window-b",
            mz=243.114,
            seed_rt=2.1,
            ms1_search_rt_min=1.0,
            ms1_search_rt_max=3.0,
        ),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        peak_resolver=_always_peak_at_seed,
        raw_xic_batch_size=2,
    )

    assert source.batch_windows == [
        [(100, 300), (100, 300)],
        [(1000, 1200)],
    ]
    assert {assignment.candidate_id for assignment in result.assignments} == {
        "s1#same-window-a",
        "s1#other-window",
        "s1#same-window-b",
    }


def test_build_sample_local_owners_deduplicates_identical_xic_requests() -> None:
    from xic_extractor.xic_models import XICTrace

    class DedupBatchSource:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_sizes.append(len(requests))
            return tuple(
                XICTrace.from_arrays(
                    [request.rt_min, request.rt_max],
                    [100.0, 100.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = DedupBatchSource()
    candidates = (
        _candidate(
            "s1#duplicate-a",
            seed_rt=2.0,
            ms1_search_rt_min=1.0,
            ms1_search_rt_max=3.0,
        ),
        _candidate(
            "s1#duplicate-b",
            seed_rt=2.0,
            ms1_search_rt_min=1.0,
            ms1_search_rt_max=3.0,
        ),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        peak_resolver=_always_peak_at_seed,
        raw_xic_batch_size=64,
    )

    assert source.batch_sizes == [1]
    assert len(result.assignments) == 2


def test_sample_local_owner_uses_candidate_anchored_quantitation_context() -> None:
    candidate = _candidate(
        "s1#bounded",
        seed_rt=9.10,
        ms1_apex_rt=9.08,
        ms1_peak_rt_start=9.00,
        ms1_peak_rt_end=9.20,
    )
    source = FakeXICSource(
        rt=np.array([8.90, 9.00, 9.08, 9.20, 9.30], dtype=float),
        intensity=np.array([0.0, 30.0, 100.0, 30.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        (candidate,),
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(max_rt_sec=180.0),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    _mz, rt_min, rt_max, _ppm = source.calls[0]
    assert rt_min == pytest.approx(8.60)
    assert rt_max == pytest.approx(9.60)


@pytest.mark.parametrize(
    ("candidate_kwargs", "expected_rt_min", "expected_rt_max"),
    [
        (
            {
                "seed_rt": 9.50,
                "ms1_apex_rt": 9.10,
                "ms1_peak_rt_start": 9.00,
                "ms1_peak_rt_end": 9.20,
            },
            8.60,
            9.60,
        ),
        (
            {
                "seed_rt": 8.90,
                "ms1_search_rt_min": 8.70,
                "ms1_search_rt_max": 9.10,
            },
            8.70,
            9.10,
        ),
        (
            {"seed_rt": 8.90},
            6.90,
            10.90,
        ),
    ],
)
def test_sample_local_owner_rt_window_fallback_order(
    candidate_kwargs,
    expected_rt_min,
    expected_rt_max,
) -> None:
    candidate = _candidate("s1#window-order", **candidate_kwargs)
    source = FakeXICSource(
        rt=np.array([8.80, 8.90, 9.00], dtype=float),
        intensity=np.array([0.0, 100.0, 0.0], dtype=float),
    )

    build_sample_local_owners(
        (candidate,),
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(max_rt_sec=120.0),
        peak_config=_peak_config(),
        peak_resolver=_always_peak_at_seed,
    )

    _mz, rt_min, rt_max, _ppm = source.calls[0]
    assert rt_min == pytest.approx(expected_rt_min)
    assert rt_max == pytest.approx(expected_rt_max)


def test_sample_local_owner_gets_region_audit_when_enabled() -> None:
    candidates = (_candidate("s1#region", seed_rt=8.0),)
    source = FakeXICSource(
        rt=np.array([7.88, 7.94, 8.00, 8.06, 8.12], dtype=float),
        intensity=np.array([0.0, 40.0, 120.0, 40.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_region_audit=True,
    )

    assert len(result.owners) == 1
    region_audit = result.owners[0].region_audit
    assert region_audit is not None
    assert region_audit.candidate_count is not None
    assert region_audit.selected_proposal_sources


def test_sample_local_owner_region_audit_honors_candidate_allowlist() -> None:
    candidates = (_candidate("s1#region", seed_rt=8.0),)
    source = FakeXICSource(
        rt=np.array([7.88, 7.94, 8.00, 8.06, 8.12], dtype=float),
        intensity=np.array([0.0, 40.0, 120.0, 40.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_region_audit=True,
        region_audit_family_ids=frozenset({"other"}),
    )

    assert len(result.owners) == 1
    assert result.owners[0].region_audit is None


def test_endpoint_apex_owner_is_retained_but_not_quantification_eligible() -> None:
    result = build_sample_local_owners(
        (_candidate("s1#edge-apex", seed_rt=8.0),),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([7.8, 7.9, 8.0, 8.1, 8.2, 8.3], dtype=float),
                intensity=np.array([100.0, 80.0, 50.0, 20.0, 10.0, 8.0]),
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    owner = result.owners[0]
    assert owner.quantification_eligible is False
    assert owner.quantification_exclusion_reason == (
        "owner_apex_on_integration_boundary"
    )


def test_right_endpoint_apex_owner_is_not_quantification_eligible() -> None:
    result = build_sample_local_owners(
        (_candidate("s1#right-edge", seed_rt=8.0),),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([7.8, 7.9, 8.0, 8.1, 8.2, 8.3], dtype=float),
                intensity=np.array([8.0, 10.0, 20.0, 50.0, 80.0, 100.0]),
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert len(result.owners) == 1
    assert result.owners[0].quantification_eligible is False


def test_custom_peak_resolver_without_region_audit_remains_supported() -> None:
    result = build_sample_local_owners(
        (_candidate("s1#custom", seed_rt=8.0),),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([7.9, 8.0, 8.1], dtype=float),
                intensity=np.array([0.0, 100.0, 0.0], dtype=float),
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        peak_resolver=_always_peak_at_seed,
        emit_region_audit=True,
    )

    assert len(result.owners) == 1
    assert result.owners[0].region_audit is None


def test_selected_integration_endpoint_censors_scalar_interior_owner() -> None:
    result = build_sample_local_owners(
        (_candidate("s1#integration-edge", seed_rt=8.0),),
        raw_sources={
            "s1": FakeXICSource(
                rt=np.array([7.9, 8.0, 8.1], dtype=float),
                intensity=np.array([0.0, 100.0, 0.0], dtype=float),
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        peak_resolver=_scalar_interior_endpoint_integration,
    )

    assert len(result.owners) == 1
    owner = result.owners[0]
    assert owner.quantification_eligible is False
    assert owner.quantification_exclusion_reason == (
        "owner_apex_on_integration_boundary"
    )


class FakeXICSource:
    def __init__(self, *, rt, intensity):
        self.rt = rt
        self.intensity = intensity
        self.calls = []

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        self.calls.append((mz, rt_min, rt_max, ppm_tol))
        return self.rt, self.intensity


class FakePeakResolver:
    def __init__(self, peaks):
        self.peaks = peaks

    def __call__(self, candidate, rt_array, intensity_array, peak_config, seed_rt):
        from xic_extractor.alignment.ownership import ResolvedPeak

        rt, start, end, area, height = self.peaks[candidate.candidate_id]
        return ResolvedPeak(
            rt=rt,
            peak_start=start,
            peak_end=end,
            area=area,
            intensity=height,
        )


def _always_peak_at_seed(candidate, rt_array, intensity_array, peak_config, seed_rt):
    from xic_extractor.alignment.ownership import ResolvedPeak

    return ResolvedPeak(
        rt=seed_rt,
        peak_start=float(rt_array[0]),
        peak_end=float(rt_array[-1]),
        area=100.0,
        intensity=100.0,
    )


def _scalar_interior_endpoint_integration(
    candidate,
    rt_array,
    intensity_array,
    peak_config,
    seed_rt,
):
    from xic_extractor.alignment.ownership import ResolvedPeak

    return ResolvedPeak(
        rt=8.05,
        peak_start=8.0,
        peak_end=8.1,
        area=100.0,
        intensity=100.0,
        selected_integration=IntegrationResult(
            rt_left_min=8.0,
            rt_apex_min=8.0,
            rt_right_min=8.1,
            raw_apex_rt_min=8.0,
            rt_width_min=0.1,
            height_raw=100.0,
            height_smoothed=100.0,
            area_raw_counts_seconds=100.0,
            boundary_sources=("test_endpoint_integration",),
        ),
    )


def _candidate(
    candidate_id,
    *,
    sample_stem="s1",
    raw_file="s1.raw",
    neutral_loss_tag="DNA_dR",
    mz=242.114,
    product_mz=126.066,
    observed_loss=116.048,
    seed_rt=12.5927,
    evidence_score=80,
    seed_event_count=2,
    ms1_apex_rt=None,
    ms1_peak_rt_start=None,
    ms1_peak_rt_end=None,
    ms1_search_rt_min=None,
    ms1_search_rt_max=None,
):
    return SimpleNamespace(
        candidate_id=candidate_id,
        sample_stem=sample_stem,
        raw_file=Path(raw_file),
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_loss,
        best_seed_rt=seed_rt,
        ms1_apex_rt=ms1_apex_rt,
        ms1_peak_rt_start=ms1_peak_rt_start,
        ms1_peak_rt_end=ms1_peak_rt_end,
        ms1_search_rt_min=ms1_search_rt_min,
        ms1_search_rt_max=ms1_search_rt_max,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )


def _peak_config():
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=3,
        smooth_polyorder=1,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
        resolver_chrom_threshold=0.0,
        resolver_min_search_range_min=0.04,
        resolver_min_relative_height=0.0,
        resolver_min_absolute_height=0.0,
        resolver_min_ratio_top_edge=0.0,
        resolver_peak_duration_min=0.0,
        resolver_peak_duration_max=2.0,
        resolver_min_scans=1,
    )


def test_one_peak_seeded_by_two_ms2_scans_is_not_an_unresolved_doublet():
    """Seeds 42 s apart across one 27 s peak resolve 5 s apart, not as two peaks."""
    candidates = (
        _candidate("s1#9001", mz=269.1436, seed_rt=25.40, evidence_score=80),
        _candidate("s1#9002", mz=269.1437, seed_rt=26.10, evidence_score=78),
    )
    source = FakeXICSource(
        rt=np.array([25.5, 25.7, 25.8, 25.9, 26.1], dtype=float),
        intensity=np.array([0.0, 100.0, 90.0, 78.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
        peak_resolver=FakePeakResolver(
            {
                "s1#9001": (25.75, 25.52, 25.98, 1000.0, 100.0),
                "s1#9002": (25.83, 25.60, 26.06, 950.0, 78.0),
            },
        ),
    )

    assert result.ambiguous_records == ()
    assert result.owners != ()


def test_apex_separation_near_a_full_peak_width_stays_ambiguous():
    """The rule still fires where the trace could actually show two apexes."""
    candidates = (
        _candidate("s1#9101", mz=269.1436, seed_rt=25.40, evidence_score=80),
        _candidate("s1#9102", mz=269.1437, seed_rt=25.60, evidence_score=78),
    )
    source = FakeXICSource(
        rt=np.array([25.5, 25.7, 25.8, 25.9, 26.1], dtype=float),
        intensity=np.array([0.0, 100.0, 90.0, 78.0, 0.0], dtype=float),
    )

    result = build_sample_local_owners(
        candidates,
        raw_sources={"s1": source},
        alignment_config=AlignmentConfig(owner_apex_close_sec=2.0),
        peak_config=_peak_config(),
        peak_resolver=FakePeakResolver(
            {
                "s1#9101": (25.75, 25.70, 25.80, 1000.0, 100.0),
                "s1#9102": (25.83, 25.78, 25.88, 950.0, 78.0),
            },
        ),
    )

    assert result.owners == ()
    assert len(result.ambiguous_records) == 1
    assert result.ambiguous_records[0].reason == "owner_multiplet_ambiguity"
