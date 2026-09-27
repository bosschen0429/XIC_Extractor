import pickle
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from pytest import MonkeyPatch

import xic_extractor.alignment.backfill_peak_fact as owner_backfill_module
import xic_extractor.alignment.backfill_request_plan as request_plan_module
from tests.test_alignment_owner_clustering import _owner
from xic_extractor.alignment import (
    backfill_acquisition as owner_backfill_acquisition_module,
)
from xic_extractor.alignment import (
    backfill_evaluation as owner_backfill_evaluation_module,
)
from xic_extractor.alignment.backfill_peak_fact import (
    build_backfill_result,
)
from xic_extractor.alignment.backfill_request_plan import (
    build_backfill_request_plan,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.region_audit import PeakRegionAuditSummary
from xic_extractor.processing_identity import build_acquisition_source_identity

_TEST_SIGNAL_PROCESSING_PROFILE = SignalProcessingProfile(window_points=3)


def _backfill_cells(*args, **kwargs):
    return build_backfill_result(*args, **kwargs).cells


def test_owner_backfill_backfills_missing_sample_from_feature_center() -> None:
    source = _dense_peak_source(center=8.50, height=120.0)
    feature = _feature()

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        emit_region_audit=True,
    )

    assert len(cells) == 1
    cell = cells[0]
    assert cell.sample_stem == "sample-b"
    assert cell.cluster_id == feature.feature_family_id
    assert cell.status == "backfilled"
    assert cell.area is not None and cell.area > 0
    assert cell.selected_integration is not None
    assert cell.selected_integration.area_raw_counts_seconds == cell.area
    assert cell.selected_integration.baseline_type == "asls"
    assert cell.selected_integration.area_baseline_corrected is not None
    assert cell.selected_integration.area_ms1_morphology is not None
    assert cell.matrix_area == cell.selected_integration.area_ms1_morphology
    assert cell.reason == "owner-centered MS1 backfill"
    assert cell.trace_quality == "owner_backfill"
    assert cell.group_hypothesis_id == "FAM000001"
    assert cell.public_family_id == "FAM000001"
    assert cell.group_construction_role == "successor_constructor"
    assert cell.group_delivery_role == "owner_aligned_feature_compatibility_facade"
    assert cell.group_membership_source == "owner_aligned_feature_successor_projection"
    assert cell.gap_fill_state == "gap_fill_rescued"
    assert cell.gap_fill_reason == "group_centered_query_detected"
    assert cell.missing_observation_state == "queried_and_detected"
    assert cell.group_claim_state == "unclaimed_or_winner"
    assert cell.scan_support_score == 0.9
    assert cell.region_candidate_count is not None
    assert cell.region_shadow_status == "evaluated"
    assert cell.region_local_mixture_diagnostic
    assert source.calls == [(500.0, 7.5, 9.5, 20.0)]


def test_owner_backfill_shared_peak_facts_preserve_projection_and_work_telemetry(
    tmp_path,
) -> None:
    bound_source, acquisition = _owner_bound_peak_source(
        tmp_path,
        b"owner-backfill-shadow-corpus",
    )
    baseline = build_backfill_result(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _dense_peak_source(center=8.50, height=120.0)},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    candidate = build_backfill_result(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": bound_source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    assert candidate.cells == baseline.cells
    assert candidate.candidate_audit_rows == baseline.candidate_audit_rows
    assert len(baseline.peak_fact_unsealed_evaluation_keys) == 1
    assert bound_source.calls == [(500.0, 7.5, 9.5, 20.0)]
    observation = candidate.peak_fact_observations[0]
    assert observation.logical_request.group_hypothesis_id == "FAM000001"
    assert observation.request_identity.acquisition_source == acquisition
    assert observation.facts is not None
    assert observation.facts.integration == candidate.cells[0].selected_integration
    assert observation.cell.status == "backfilled"
    assert observation.cell.reason == "owner-centered MS1 backfill"
    assert observation.evidence_authority == "evidence_only_no_write_authority"
    assert observation.trace_evaluated is True
    assert observation.fact_elapsed_seconds is not None
    assert observation.fact_elapsed_seconds > 0
    rehydrated_observation = pickle.loads(pickle.dumps(observation))
    assert rehydrated_observation == observation
    owner_backfill_module.validate_backfill_peak_fact_observation(
        rehydrated_observation
    )
    with pytest.raises(ValueError, match="must be minted by Owner Backfill"):
        replace(observation, _mint_token=object())
    assert observation.physical_evaluation is not None
    forged_question = replace(
        observation.physical_evaluation.question,
        selection_subject_id="owner-backfill-subject:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="physical selection subject"):
        replace(
            observation,
            physical_evaluation=replace(
                observation.physical_evaluation,
                question=forged_question,
            ),
            _mint_token=(
                owner_backfill_module._BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN
            ),
        )
    forged_query = replace(
        observation.physical_evaluation.question,
        query_rt_min=observation.physical_evaluation.question.query_rt_min + 0.01,
    )
    with pytest.raises(ValueError, match="physical query RT"):
        replace(
            observation,
            physical_evaluation=replace(
                observation.physical_evaluation,
                question=forged_query,
            ),
            _mint_token=(
                owner_backfill_module._BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN
            ),
        )
    assert candidate.peak_fact_unsealed_evaluation_keys == ()
    assert not hasattr(candidate, "peak_fact_cost")
    assert owner_backfill_acquisition_module._positive_elapsed(0.0) is None
    assert owner_backfill_acquisition_module._positive_elapsed(float("nan")) is None


def test_owner_backfill_fact_receipt_drops_trace_arrays_and_worker_payload() -> None:
    result = build_backfill_result(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": _dense_peak_source(center=8.50, height=120.0)},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )
    assert result.peak_fact_unsealed_evaluation_keys
    receipt = result.candidate_audit_rows[0]
    assert receipt.area is not None
    cell = result.cells[0]
    assert cell.selected_integration is not None
    assert cell.selected_integration.raw_scan_indices == ()


def test_owner_backfill_canonical_path_does_not_call_retired_local_helpers(
    tmp_path,
    monkeypatch,
) -> None:
    source, acquisition = _owner_bound_peak_source(
        tmp_path,
        b"owner-backfill-shadow-disagreement",
    )

    def retired(*args, **kwargs):
        raise AssertionError("retired Owner Backfill helper was called")

    for name in (
        "detect_ms1_trace_peak",
        "find_peak_and_area",
        "integration_from_peak",
        "peak_trace_evidence",
        "_scan_support_score",
    ):
        monkeypatch.setattr(owner_backfill_evaluation_module, name, retired)

    result = build_backfill_result(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    assert len(result.cells) == 1
    assert result.cells[0].status == "backfilled"
    assert result.cells[0].area is not None
    assert result.cells[0].selected_integration is not None
    assert result.cells[0].trace_quality == "owner_backfill"
    assert result.candidate_audit_rows[0].selected_for_output is True
    assert result.candidate_audit_rows[0].candidate_outcome == "detected"
    assert result.peak_fact_observations[0].facts is not None


def test_owner_backfill_canonical_backfills_one_offset_physical_segment(
    tmp_path,
) -> None:
    source, acquisition = _owner_bound_peak_source(
        tmp_path,
        b"owner-backfill-unique-offset-segment",
        center=7.65,
    )

    result = build_backfill_result(
        (_feature(rt=8.5),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=60.0,
        ),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    assert len(result.cells) == 1
    cell = result.cells[0]
    assert cell.status == "backfilled"
    assert cell.apex_rt == pytest.approx(7.65)
    observation = result.peak_fact_observations[0]
    assert observation.logical_request.item.preferred_rt == 8.5
    assert observation.physical_evaluation is not None
    assert len(observation.physical_evaluation.segment_universe.segments) == 1
    assert observation.physical_evaluation.question.query_rt_min == pytest.approx(7.65)


def test_owner_backfill_canonical_rejects_multiple_offset_physical_segments(
    tmp_path,
) -> None:
    rt = np.linspace(7.5, 9.5, 101)
    source, acquisition = _owner_bound_source(
        tmp_path,
        b"owner-backfill-ambiguous-offset-segments",
        FakeBackfillSource(
            rt=rt,
            intensity=(
                120.0 * np.exp(-((rt - 7.8) ** 2) / 0.002)
                + 100.0 * np.exp(-((rt - 9.2) ** 2) / 0.002)
            ),
        ),
    )

    result = build_backfill_result(
        (_feature(rt=8.5),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=60.0,
        ),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    assert result.cells[0].status == "unchecked"
    evaluation = result.peak_fact_observations[0].physical_evaluation
    assert evaluation is not None
    assert evaluation.reason == "no_selection"
    assert len(evaluation.segment_universe.segments) == 2


def test_owner_backfill_canonical_rejects_unique_segment_beyond_preferred_rt(
    tmp_path,
) -> None:
    source, acquisition = _owner_bound_peak_source(
        tmp_path,
        b"owner-backfill-offset-beyond-preferred-rt",
        center=6.9,
    )

    result = build_backfill_result(
        (_feature(rt=8.5),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=180.0,
        ),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        signal_processing_profile=_TEST_SIGNAL_PROCESSING_PROFILE,
    )

    assert result.cells[0].status == "unchecked"
    evaluation = result.peak_fact_observations[0].physical_evaluation
    assert evaluation is not None
    assert evaluation.reason == "no_selection"
    assert len(evaluation.segment_universe.segments) == 1
    assert evaluation.question.query_rt_min == 8.5


def test_owner_backfill_accepts_delivery_contract_feature_without_legacy_dto() -> None:
    source = _dense_peak_source(center=8.50, height=120.0)
    owner = SimpleNamespace(sample_stem="sample-a", owner_area=1000.0)
    feature = SimpleNamespace(
        feature_family_id="FAM_CONTRACT",
        cluster_id="FAM_CONTRACT",
        neutral_loss_tag="NL116",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=383.9526,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        owners=(owner,),
        members=(owner,),
        event_cluster_ids=("OWN-sample-a-000001",),
        event_member_count=1,
        evidence="test_delivery_contract",
        identity_conflict=False,
        review_only=False,
        confirm_local_owners_with_backfill=False,
        backfill_seed_centers=(),
        ambiguous_sample_stem=None,
        ambiguous_candidate_ids=(),
        group_hypothesis_id="GROUP_CONTRACT",
        public_family_id="FAM_CONTRACT",
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
    )

    assert not isinstance(feature, OwnerAlignedFeature)
    assert len(cells) == 1
    assert cells[0].cluster_id == "FAM_CONTRACT"
    assert cells[0].group_hypothesis_id == "GROUP_CONTRACT"
    assert cells[0].public_family_id == "FAM_CONTRACT"
    assert cells[0].group_delivery_role == "successor_delivery_protocol"
    assert cells[0].gap_fill_state == "gap_fill_rescued"
    assert cells[0].sample_stem == "sample-b"
    assert source.calls == [(500.0, 7.5, 9.5, 20.0)]


def test_owner_backfill_region_audit_is_opt_in(monkeypatch) -> None:
    source = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 50.0, 120.0, 50.0, 0.0]),
    )

    def fail_region_audit(*args, **kwargs):
        raise AssertionError("region audit should be debug/validation opt-in")

    monkeypatch.setattr(
        owner_backfill_evaluation_module,
        "build_peak_region_audit_summary",
        fail_region_audit,
    )

    cells = _backfill_cells(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
    )

    assert len(cells) == 1
    assert cells[0].region_candidate_count is None
    assert cells[0].region_shadow_status == ""


def test_owner_backfill_audit_mode_none_skips_all_heavy_audit(monkeypatch) -> None:
    source = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 50.0, 120.0, 50.0, 0.0]),
    )
    calls = {"region": 0}

    def fail_region_audit(*args, **kwargs):
        calls["region"] += 1
        raise AssertionError("heavy audit must not run when audit_evidence_mode=none")

    monkeypatch.setattr(
        owner_backfill_evaluation_module,
        "build_peak_region_audit_summary",
        fail_region_audit,
    )

    cells = _backfill_cells(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), baseline_audit_method="asls"),
        emit_region_audit=True,
        audit_evidence_mode="none",
    )

    assert len(cells) == 1
    assert calls["region"] == 0
    assert cells[0].region_candidate_count is None
    assert cells[0].selected_integration is not None
    assert cells[0].selected_integration.baseline_type == "asls"
    assert cells[0].selected_integration.area_ms1_morphology is not None
    assert cells[0].matrix_area == cells[0].selected_integration.area_ms1_morphology


def test_owner_backfill_region_audit_receives_untargeted_trace_group(
    monkeypatch,
) -> None:
    source = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 50.0, 120.0, 50.0, 0.0]),
    )
    captured = {}

    def fake_region_audit(*args, **kwargs):
        captured["trace_group"] = kwargs["trace_group"]
        return PeakRegionAuditSummary(
            candidate_count=1,
            shadow_status="evaluated",
        )

    monkeypatch.setattr(
        owner_backfill_evaluation_module,
        "build_peak_region_audit_summary",
        fake_region_audit,
    )

    _backfill_cells(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        emit_region_audit=True,
    )

    trace_group = captured["trace_group"]
    assert trace_group.analysis_mode == "untargeted"
    assert trace_group.context_id == "FAM000001"
    assert trace_group.primary_trace.sample_name == "sample-b"
    assert trace_group.primary_trace.ppm_tol == 20.0


def test_owner_backfill_region_audit_honors_family_allowlist(monkeypatch) -> None:
    source = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 50.0, 120.0, 50.0, 0.0]),
    )
    audited: list[str] = []

    def fake_region_audit(*args, **kwargs):
        audited.append(kwargs["trace_group"].context_id)
        return PeakRegionAuditSummary(
            candidate_count=1,
            shadow_status="evaluated",
        )

    monkeypatch.setattr(
        owner_backfill_evaluation_module,
        "build_peak_region_audit_summary",
        fake_region_audit,
    )

    cells = _backfill_cells(
        (
            _feature(feature_family_id="FAM000001", mz=500.0),
            _feature(feature_family_id="FAM000002", mz=501.0),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        emit_region_audit=True,
        region_audit_family_ids=frozenset({"FAM000002"}),
    )

    by_family = {cell.cluster_id: cell for cell in cells}
    assert audited == ["FAM000002"]
    assert by_family["FAM000001"].region_candidate_count is None
    assert by_family["FAM000002"].region_candidate_count == 1


def test_owner_backfill_skips_review_only_identity_conflicts() -> None:
    source = FakeBackfillSource(
        rt=np.array([8.4, 8.5, 8.6]),
        intensity=np.array([0.0, 100.0, 0.0]),
    )

    cells = _backfill_cells(
        (_feature(review_only=True),),
        sample_order=("sample-b",),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert cells == ()
    assert source.calls == []


def test_owner_backfill_skips_features_below_min_detected_sample_gate() -> None:
    source = FakeBackfillSource(
        rt=np.array([8.4, 8.5, 8.6]),
        intensity=np.array([0.0, 100.0, 0.0]),
    )

    cells = _backfill_cells(
        (_feature(),),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=2),
        peak_config=_peak_config(),
    )

    assert cells == ()
    assert source.calls == []


def test_owner_backfill_skips_detected_confirmation_when_owner_cannot_change() -> None:
    source_a = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 50.0, 100.0, 50.0, 0.0]),
    )
    source_b = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 60.0, 120.0, 60.0, 0.0]),
    )
    feature = replace(_feature(), confirm_local_owners_with_backfill=True)

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-a": source_a, "sample-b": source_b},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-b"),
    ]
    assert source_a.calls == []
    assert source_b.calls == [(500.0, 5.5, 11.5, 20.0)]


def test_owner_backfill_confirms_low_detected_preconsolidated_owner() -> None:
    source_a = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 500.0, 1200.0, 500.0, 0.0]),
    )
    source_b = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 60.0, 120.0, 60.0, 0.0]),
    )
    low_owner = replace(_owner("sample-a", "a"), owner_area=100.0)
    typical_owner = replace(_owner("sample-b", "b"), owner_area=1000.0)
    feature = replace(
        _feature(),
        owners=(low_owner, typical_owner),
        confirm_local_owners_with_backfill=True,
    )

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-a": source_a, "sample-b": source_b},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-a"),
    ]
    assert source_a.calls == [(500.0, 5.5, 11.5, 20.0)]
    assert source_b.calls == []


def test_owner_backfill_confirms_any_low_owner_for_duplicate_sample() -> None:
    source_a = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 500.0, 1200.0, 500.0, 0.0]),
    )
    source_b = FakeBackfillSource(
        rt=np.array([8.40, 8.49, 8.50, 8.51, 8.60]),
        intensity=np.array([0.0, 60.0, 120.0, 60.0, 0.0]),
    )
    low_owner = replace(_owner("sample-a", "low"), owner_area=100.0)
    typical_owner = replace(_owner("sample-b", "b"), owner_area=1000.0)
    high_owner = replace(_owner("sample-a", "high"), owner_area=1000.0)
    feature = replace(
        _feature(),
        owners=(low_owner, typical_owner, high_owner),
        confirm_local_owners_with_backfill=True,
    )

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-a": source_a, "sample-b": source_b},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-a"),
    ]
    assert source_a.calls == [(500.0, 5.5, 11.5, 20.0)]
    assert source_b.calls == []


def test_owner_backfill_uses_preconsolidated_seed_centers_and_keeps_best_peak() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.centers: list[float] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            traces = []
            for request in requests:
                center = (request.rt_min + request.rt_max) / 2.0
                self.centers.append(center)
                intensity = 300.0 if round(center, 1) == 8.8 else 100.0
                scale = np.array(
                    [0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 0.75, 0.5, 0.25, 0.1, 0.0],
                )
                traces.append(
                    XICTrace.from_arrays(
                        np.linspace(center - 0.10, center + 0.10, 11),
                        intensity * scale,
                    )
                )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()
    feature = replace(
        _feature(),
        backfill_seed_centers=((500.0, 8.5), (500.0, 8.8)),
    )

    cells = _backfill_cells(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(),
        peak_config=replace(_peak_config(), resolver_min_scans=10),
        raw_xic_batch_size=64,
    )

    assert source.centers == [8.5, 8.8]
    assert len(cells) == 1
    assert cells[0].sample_stem == "sample-b"
    assert cells[0].area is not None and cells[0].area > 0
    assert cells[0].apex_rt == 8.8
    assert cells[0].scan_support_score == 0.9
    assert cells[0].backfill_seed_mz == 500.0
    assert cells[0].backfill_seed_rt == 8.8
    assert np.isclose(cells[0].backfill_request_rt_min, 5.8)
    assert np.isclose(cells[0].backfill_request_rt_max, 11.8)
    assert cells[0].backfill_request_ppm == 20.0


def test_owner_backfill_treats_non_finite_trace_as_unchecked() -> None:
    source = FakeBackfillSource(
        rt=np.array([8.4, 8.5, np.nan]),
        intensity=np.array([0.0, 100.0, 0.0]),
    )

    cells = _backfill_cells(
        (_feature(),),
        sample_order=("sample-b",),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert len(cells) == 1
    cell = cells[0]
    assert cell.sample_stem == "sample-b"
    assert cell.status == "unchecked"
    assert cell.area is None
    assert cell.trace_quality == "owner_backfill_unassessable"
    assert cell.gap_fill_state == "not_filled"
    assert cell.gap_fill_reason == "query_attempt_not_detected"
    assert cell.missing_observation_state == "missing_unchecked"
    assert cell.backfill_seed_mz == 500.0
    assert cell.backfill_seed_rt == 8.5
    assert np.isclose(cell.backfill_request_rt_min, 5.5)
    assert np.isclose(cell.backfill_request_rt_max, 11.5)


def test_owner_backfill_marks_no_peak_query_as_unchecked_outcome() -> None:
    source = FakeBackfillSource(
        rt=np.array([8.4, 8.5, 8.6]),
        intensity=np.array([0.0, 0.0, 0.0]),
    )

    cells = _backfill_cells(
        (_feature(),),
        sample_order=("sample-b",),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
    )

    assert len(cells) == 1
    assert cells[0].status == "unchecked"
    assert cells[0].trace_quality == "owner_backfill_not_detected"
    assert cells[0].gap_fill_state == "not_filled"
    assert cells[0].gap_fill_reason == "query_attempt_not_detected"
    assert cells[0].missing_observation_state == "missing_unchecked"


def test_owner_backfill_result_records_all_candidate_audit_rows() -> None:
    from xic_extractor.xic_models import XICTrace

    class MixedSource:
        def extract_xic_many(self, requests):
            traces = []
            for request in requests:
                if request.mz == 500.0:
                    traces.append(
                        XICTrace.from_arrays(
                            [8.40, 8.50, 8.60],
                            [0.0, 0.0, 0.0],
                        )
                    )
                else:
                    traces.append(
                        XICTrace.from_arrays(
                            [8.40, 8.49, 8.50, 8.51, 8.60],
                            [0.0, 50.0, 120.0, 50.0, 0.0],
                        )
                    )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    feature = replace(
        _feature(),
        backfill_seed_centers=((500.0, 8.5), (501.0, 8.5)),
    )

    result = build_backfill_result(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": MixedSource()},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert [
        (cell.cluster_id, cell.sample_stem, cell.status) for cell in result.cells
    ] == [("FAM000001", "sample-b", "backfilled")]
    assert [
        (row.sample_stem, row.backfill_seed_mz, row.candidate_status)
        for row in result.candidate_audit_rows
    ] == [
        ("sample-b", 500.0, "unchecked"),
        ("sample-b", 501.0, "backfilled"),
    ]
    assert [row.selected_for_output for row in result.candidate_audit_rows] == [
        False,
        True,
    ]
    assert result.candidate_audit_rows[0].candidate_outcome == "not_detected"
    assert result.candidate_audit_rows[1].candidate_outcome == "detected"


def test_owner_backfill_uses_batch_source_and_preserves_feature_major_order() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_sizes.append(len(requests))
            traces = []
            for request in requests:
                center = (request.rt_min + request.rt_max) / 2.0
                traces.append(
                    XICTrace.from_arrays(
                        [
                            center - 0.10,
                            center - 0.01,
                            center,
                            center + 0.01,
                            center + 0.10,
                        ],
                        [0.0, 50.0, 120.0, 50.0, 0.0],
                    )
                )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source_b = BatchSource()
    source_c = BatchSource()
    feature_a = _feature()
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0, rt=8.8)

    cells = _backfill_cells(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_sources={"sample-b": source_b, "sample-c": source_c},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert source_b.batch_sizes == [2]
    assert source_c.batch_sizes == [2]
    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-b"),
        ("FAM000001", "sample-c"),
        ("FAM000002", "sample-b"),
        ("FAM000002", "sample-c"),
    ]


def test_owner_backfill_request_plan_is_sample_bucketed_without_raw_access() -> None:
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = replace(
        _feature(feature_family_id="FAM000002", mz=510.0, rt=10.5),
        backfill_seed_centers=((510.0, 10.5), (511.0, 10.75)),
    )

    plan = build_backfill_request_plan(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_sample_stems=frozenset({"sample-b"}),
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
    )

    assert plan.requests_for_sample("sample-a") == ()
    assert plan.requests_for_sample("sample-c") == ()
    sample_b_requests = plan.requests_for_sample("sample-b")
    assert [
        (
            item[0].feature_family_id,
            item[1],
            item[2].mz,
            item[2].rt_min,
            item[2].rt_max,
            item[2].ppm_tol,
            item[3],
        )
        for item in sample_b_requests
    ] == [
        ("FAM000001", "sample-b", 500.0, 7.5, 9.5, 20.0, 8.5),
        ("FAM000002", "sample-b", 510.0, 9.5, 11.5, 20.0, 10.5),
        ("FAM000002", "sample-b", 511.0, 9.75, 11.75, 20.0, 10.75),
    ]


def test_owner_backfill_request_plan_reuses_seed_centers_per_feature(
    monkeypatch: MonkeyPatch,
) -> None:
    feature = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    calls: list[str] = []

    def fake_seed_centers(
        seen_feature: OwnerAlignedFeature,
    ) -> tuple[tuple[float, float], ...]:
        calls.append(seen_feature.feature_family_id)
        return ((500.0, 8.5), (501.0, 8.75))

    monkeypatch.setattr(
        request_plan_module,
        "backfill_seed_centers",
        fake_seed_centers,
    )

    plan = request_plan_module.build_backfill_request_plan(
        (feature,),
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_sample_stems=frozenset({"sample-b", "sample-c"}),
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
    )

    assert calls == ["FAM000001"]
    assert [
        (item.sample_stem, item.request.mz, item.preferred_rt)
        for item in plan.requests_for_sample("sample-b")
    ] == [
        ("sample-b", 500.0, 8.5),
        ("sample-b", 501.0, 8.75),
    ]
    assert [
        (item.sample_stem, item.request.mz, item.preferred_rt)
        for item in plan.requests_for_sample("sample-c")
    ] == [
        ("sample-c", 500.0, 8.5),
        ("sample-c", 501.0, 8.75),
    ]


def test_owner_backfill_deduplicates_identical_xic_requests() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_sizes.append(len(requests))
            return tuple(
                XICTrace.from_arrays(
                    [
                        (request.rt_min + request.rt_max) / 2.0 - 0.10,
                        (request.rt_min + request.rt_max) / 2.0 - 0.01,
                        (request.rt_min + request.rt_max) / 2.0,
                        (request.rt_min + request.rt_max) / 2.0 + 0.01,
                        (request.rt_min + request.rt_max) / 2.0 + 0.10,
                    ],
                    [0.0, 50.0, 120.0, 50.0, 0.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=500.0, rt=8.5)

    cells = _backfill_cells(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert source.batch_sizes == [1]
    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-b"),
        ("FAM000002", "sample-b"),
    ]


def test_owner_backfill_validates_prefilter_hits_with_secondary_source() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self, traces) -> None:
            self.traces = tuple(traces)
            self.requests = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.requests.append(requests)
            return self.traces[: len(requests)]

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0, rt=10.5)
    prefilter = BatchSource(
        (
            XICTrace.from_arrays(
                [8.40, 8.49, 8.50, 8.51, 8.60],
                [0.0, 50.0, 120.0, 50.0, 0.0],
            ),
            XICTrace.from_arrays(
                [10.40, 10.49, 10.50, 10.51, 10.60],
                [0.0, 0.0, 0.0, 0.0, 0.0],
            ),
        )
    )
    validator = BatchSource(
        (
            XICTrace.from_arrays(
                [8.40, 8.49, 8.50, 8.51, 8.60],
                [0.0, 100.0, 240.0, 100.0, 0.0],
            ),
        )
    )

    cells = _backfill_cells(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": prefilter},
        validation_raw_sources={"sample-b": validator},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert [(cell.cluster_id, cell.sample_stem, cell.status) for cell in cells] == [
        ("FAM000001", "sample-b", "backfilled"),
        ("FAM000002", "sample-b", "unchecked"),
    ]
    assert cells[0].height == 240.0
    assert cells[1].gap_fill_state == "not_filled"
    assert cells[1].gap_fill_reason == "query_attempt_not_detected"
    assert cells[1].missing_observation_state == "missing_unchecked"
    assert len(prefilter.requests[0]) == 2
    assert len(validator.requests[0]) == 1
    assert validator.requests[0][0].mz == 500.0


def test_owner_backfill_validation_pending_deduplicates_fallback_lookup() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self, traces) -> None:
            self.traces = tuple(traces)
            self.requests = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.requests.append(requests)
            return self.traces[: len(requests)]

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    feature = replace(
        _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
        backfill_seed_centers=((500.0, 8.5), (500.0, 8.8)),
    )
    prefilter = BatchSource(
        (
            XICTrace.from_arrays(
                [8.40, 8.49, 8.50, 8.51, 8.60],
                [0.0, 50.0, 120.0, 50.0, 0.0],
            ),
            XICTrace.from_arrays(
                [8.70, 8.79, 8.80, 8.81, 8.90],
                [0.0, 60.0, 130.0, 60.0, 0.0],
            ),
        )
    )
    validator = BatchSource(
        (
            XICTrace.from_arrays(
                [8.40, 8.49, 8.50, 8.51, 8.60],
                [0.0, 100.0, 240.0, 100.0, 0.0],
            ),
            XICTrace.from_arrays(
                [8.70, 8.79, 8.80, 8.81, 8.90],
                [0.0, 110.0, 260.0, 110.0, 0.0],
            ),
        )
    )

    result = build_backfill_result(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": prefilter},
        validation_raw_sources={"sample-b": validator},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
    )

    assert [row.candidate_phase for row in result.candidate_audit_rows] == [
        "prefilter_query",
        "prefilter_query",
        "validation_query",
        "validation_query",
    ]
    assert len(prefilter.requests[0]) == 2
    assert len(validator.requests[0]) == 2
    observed_cells = [
        (cell.cluster_id, cell.sample_stem, cell.status) for cell in result.cells
    ]
    assert observed_cells == [
        ("FAM000001", "sample-b", "backfilled"),
    ]
    assert result.cells[0].height == 260.0


def test_owner_backfill_batches_by_rt_window_without_changing_emit_order() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_centers: list[tuple[float, ...]] = []

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_centers.append(
                tuple(
                    round((request.rt_min + request.rt_max) / 2.0, 3)
                    for request in requests
                )
            )
            traces = []
            for request in requests:
                center = (request.rt_min + request.rt_max) / 2.0
                traces.append(
                    XICTrace.from_arrays(
                        [
                            center - 0.10,
                            center - 0.01,
                            center,
                            center + 0.01,
                            center + 0.10,
                        ],
                        [0.0, 50.0, 120.0, 50.0, 0.0],
                    )
                )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0, rt=10.5)
    feature_c = _feature(feature_family_id="FAM000003", mz=520.0, rt=8.5)

    cells = _backfill_cells(
        (feature_a, feature_b, feature_c),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=2,
    )

    assert source.batch_centers == [(8.5, 8.5), (10.5,)]
    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-b"),
        ("FAM000002", "sample-b"),
        ("FAM000003", "sample-b"),
    ]


def test_owner_backfill_does_not_split_source_scan_window_groups() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_centers: list[tuple[float, ...]] = []

        def scan_window_for_request(self, _request):
            return (100, 200)

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_centers.append(
                tuple(
                    round((request.rt_min + request.rt_max) / 2.0, 3)
                    for request in requests
                )
            )
            traces = []
            for request in requests:
                center = (request.rt_min + request.rt_max) / 2.0
                traces.append(
                    XICTrace.from_arrays(
                        [
                            center - 0.10,
                            center - 0.01,
                            center,
                            center + 0.01,
                            center + 0.10,
                        ],
                        [0.0, 50.0, 120.0, 50.0, 0.0],
                    )
                )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.50)
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0, rt=8.51)
    feature_c = _feature(feature_family_id="FAM000003", mz=520.0, rt=8.52)

    cells = _backfill_cells(
        (feature_a, feature_b, feature_c),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=2,
    )

    assert source.batch_centers == [(8.5, 8.51, 8.52)]
    assert [(cell.cluster_id, cell.sample_stem) for cell in cells] == [
        ("FAM000001", "sample-b"),
        ("FAM000002", "sample-b"),
        ("FAM000003", "sample-b"),
    ]


def test_owner_backfill_falls_back_when_scan_window_unavailable() -> None:
    from xic_extractor.xic_models import XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.batch_centers: list[tuple[float, ...]] = []

        def scan_window_for_request(self, _request):
            raise AttributeError("scan window lookup is unavailable")

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batch_centers.append(
                tuple(
                    round((request.rt_min + request.rt_max) / 2.0, 3)
                    for request in requests
                )
            )
            return tuple(
                XICTrace.from_arrays(
                    [
                        (request.rt_min + request.rt_max) / 2.0 - 0.01,
                        (request.rt_min + request.rt_max) / 2.0,
                        (request.rt_min + request.rt_max) / 2.0 + 0.01,
                    ],
                    [0.0, 120.0, 0.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = BatchSource()

    _backfill_cells(
        (
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            _feature(feature_family_id="FAM000002", mz=510.0, rt=10.5),
            _feature(feature_family_id="FAM000003", mz=520.0, rt=8.5),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=2,
    )

    assert source.batch_centers == [(8.5, 8.5), (10.5,)]


def test_owner_backfill_superwindow_crops_to_original_request_windows() -> None:
    from xic_extractor.xic_models import XICTrace

    class SuperWindowSource:
        def __init__(self) -> None:
            self.batches = []
            self.retention_time_calls: dict[int, int] = {}

        def scan_window_for_request(self, request):
            return (int(round(request.rt_min * 100)), int(round(request.rt_max * 100)))

        def retention_time_for_scan(self, scan_number):
            self.retention_time_calls[scan_number] = (
                self.retention_time_calls.get(scan_number, 0) + 1
            )
            return scan_number / 100.0

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batches.append(requests)
            traces = []
            for request in requests:
                if request.mz == 500.0:
                    traces.append(
                        XICTrace.from_arrays(
                            [7.60, 8.49, 8.50, 8.51, 9.40, 9.95, 10.10],
                            [0.0, 60.0, 120.0, 60.0, 0.0, 1000.0, 0.0],
                        )
                    )
                else:
                    traces.append(
                        XICTrace.from_arrays(
                            [7.75, 8.40, 9.19, 9.20, 9.21, 10.10],
                            [1000.0, 0.0, 65.0, 130.0, 65.0, 0.0],
                        )
                    )
            return tuple(traces)

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = SuperWindowSource()
    feature_a = _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5)
    feature_b = _feature(feature_family_id="FAM000002", mz=510.0, rt=9.2)

    cells = _backfill_cells(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
        owner_backfill_window_strategy="super-window",
        owner_backfill_superwindow_span_factor=2,
    )

    assert len(source.batches) == 1
    assert [(request.rt_min, request.rt_max) for request in source.batches[0]] == [
        (7.5, 10.2),
        (7.5, 10.2),
    ]
    assert source.retention_time_calls == {
        750: 1,
        950: 1,
        820: 1,
        1020: 1,
    }
    assert [(cell.cluster_id, cell.apex_rt) for cell in cells] == [
        ("FAM000001", 8.5),
        ("FAM000002", 9.2),
    ]
    assert np.isclose(cells[0].backfill_request_rt_min, 7.5)
    assert np.isclose(cells[0].backfill_request_rt_max, 9.5)
    assert np.isclose(cells[1].backfill_request_rt_min, 8.2)
    assert np.isclose(cells[1].backfill_request_rt_max, 10.2)


def test_owner_backfill_superwindow_falls_back_to_exact_without_scan_rt_lookup() -> (
    None
):
    from xic_extractor.xic_models import XICTrace

    class ScanOnlySource:
        def __init__(self) -> None:
            self.batches = []

        def scan_window_for_request(self, request):
            return (int(round(request.rt_min * 100)), int(round(request.rt_max * 100)))

        def extract_xic_many(self, requests):
            requests = tuple(requests)
            self.batches.append(requests)
            return tuple(
                XICTrace.from_arrays(
                    [
                        (request.rt_min + request.rt_max) / 2.0 - 0.01,
                        (request.rt_min + request.rt_max) / 2.0,
                        (request.rt_min + request.rt_max) / 2.0 + 0.01,
                    ],
                    [0.0, 120.0, 0.0],
                )
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("batch-capable source should not call extract_xic")

    source = ScanOnlySource()

    _backfill_cells(
        (
            _feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),
            _feature(feature_family_id="FAM000002", mz=510.0, rt=9.2),
        ),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=_peak_config(),
        raw_xic_batch_size=64,
        owner_backfill_window_strategy="super-window",
        owner_backfill_superwindow_span_factor=2,
    )

    assert [(request.rt_min, request.rt_max) for request in source.batches[0]] == [
        (7.5, 9.5),
        (8.2, 10.2),
    ]


class FakeBackfillSource:
    def __init__(self, *, rt, intensity) -> None:
        self.rt = rt
        self.intensity = intensity
        self.calls = []

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        self.calls.append((mz, rt_min, rt_max, ppm_tol))
        return self.rt, self.intensity


def _dense_peak_source(*, center: float, height: float) -> FakeBackfillSource:
    scale = np.array(
        [0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 0.75, 0.5, 0.25, 0.1, 0.0],
    )
    return FakeBackfillSource(
        # Physical peak facts require context beyond both baseline-return scans.
        rt=np.linspace(center - 0.14, center + 0.14, 15),
        intensity=height * np.pad(scale, (2, 2)),
    )


def _owner_bound_peak_source(tmp_path, payload: bytes, *, center: float = 8.50):
    return _owner_bound_source(
        tmp_path,
        payload,
        _dense_peak_source(center=center, height=120.0),
    )


def _owner_bound_source(tmp_path, payload: bytes, source: FakeBackfillSource):
    raw_path = tmp_path / "sample-b.raw"
    raw_path.write_bytes(payload)
    acquisition = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="test-owner-backfill-shadow",
    )
    source.acquisition_source = acquisition
    return source, acquisition


def test_candidate_audit_rows_follow_their_only_destination(tmp_path) -> None:
    """These rows exist only for alignment_owner_backfill_candidate_audit.tsv.

    No output level emits that artifact by default, so collecting one row per
    Backfill request costs the Alignment parent retention for something it then
    discards. The flag must gate collection without touching anything else.
    """

    source, acquisition = _owner_bound_peak_source(tmp_path, b"sample-b")
    features = (_feature(feature_family_id="FAM000001", mz=500.0, rt=8.5),)

    def run(emit: bool):
        return build_backfill_result(
            features,
            sample_order=("sample-b",),
            raw_sources={"sample-b": source},
            expected_acquisition_sources={"sample-b": acquisition},
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            signal_processing_profile=SignalProcessingProfile(window_points=3),
            emit_candidate_audit_rows=emit,
        )

    emitted = run(True)
    suppressed = run(False)

    assert emitted.candidate_audit_rows, "collection must still work when wanted"
    assert suppressed.candidate_audit_rows == ()

    # everything the Matrix and provenance depend on is unchanged
    assert emitted.cells == suppressed.cells
    assert len(emitted.peak_fact_observations) == len(
        suppressed.peak_fact_observations
    )
    assert [o.work_unit_key for o in emitted.peak_fact_observations] == [
        o.work_unit_key for o in suppressed.peak_fact_observations
    ]
    assert emitted.request_reuse_observations == suppressed.request_reuse_observations


def _feature(
    *,
    review_only: bool = False,
    feature_family_id: str = "FAM000001",
    mz: float = 500.0,
    rt: float = 8.5,
) -> OwnerAlignedFeature:
    return OwnerAlignedFeature(
        feature_family_id=feature_family_id,
        neutral_loss_tag="NL116",
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=383.9526,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        owners=(_owner("sample-a", "a", apex_rt=8.5),),
        evidence="single_sample_local_owner",
        review_only=review_only,
        identity_conflict=review_only,
    )


def _peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=".",
        dll_dir=".",
        output_csv="output.csv",
        diagnostics_csv="diagnostics.csv",
        smooth_window=3,
        smooth_polyorder=1,
        ms1_morphology_smoothing_window_points=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )
