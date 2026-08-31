from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_real_data_runtime import _discovery_receipts
from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
    _mode_track_runtime_jobs,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_runtime_jobs_bind_raw_by_stem_after_injection_order_sort(
    tmp_path: Path,
) -> None:
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
    settings = _settings()
    candidates_by_sample = {"S1": (), "S2": ()}
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=2,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=1,
                sample_role="qc",
            ),
        ),
        method_subject=mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            neutral_loss_da=116.0474,
        ),
        settings=settings,
        peak_config=_peak_config(tmp_path),
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="mode-track-order-test",
    )

    jobs = _mode_track_runtime_jobs(context=context, dll_dir=tmp_path)

    assert [job.sample.metadata.raw_stem for job in jobs] == ["S2", "S1"]
    assert [job.raw_path.stem for job in jobs] == ["S2", "S1"]
    assert [job.work_unit_key for job in jobs] == [
        "untargeted-cid-nl:S2",
        "untargeted-cid-nl:S1",
    ]


def test_cid_nl_workflow_seam_applies_accepted_components_through_cross_sample(
    tmp_path: Path,
) -> None:
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_mode_track_assignment import (
        _different_request_same_sources,
    )
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.mode_track import (
        InsufficientModeTrackTraining,
        ModeTrackRefinementContractError,
        ModeTrackTrajectoryPolicy,
        bootstrap_mode_track_refinement_from_cid_nl_support,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
        _apply_mode_track_refinement_transactions,
        _workflow_call_cost,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("early",))
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    result = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )

    applications = _apply_mode_track_refinement_transactions(result.transactions)
    assert applications
    assert all(
        application.reason == "unchanged_membership_no_lineage"
        and tuple(
            group.group_hypothesis_id for group in application.predecessors
        )
        == tuple(group.group_hypothesis_id for group in application.successors)
        and application.lineage == ()
        for application in applications
    )


    assert _workflow_call_cost(
        discovery_raw_open_count=1,
        m7_call_cost={
            "raw_open_count": 1,
            "xic_batch_count": 1,
            "logical_xic_request_count": 2,
            "adapter_chromatogram_call_count": 1,
            "trace_point_count": 3,
        },
    )["total_raw_open_count"] == 2
    insufficient = tuple(
        transaction.proposal
        for transaction in result.transactions
        if type(transaction.proposal) is InsufficientModeTrackTraining
    )
    assert insufficient
    assert all(
        proposal.reason_codes == ("no_direct_support_anchor",)
        for proposal in insufficient
    )
    assert {
        anchor_id
        for transaction in result.transactions
        for anchor_id in transaction.trajectory_input.anchor_ids
    } == {
        anchor.anchor_id
        for track in result.model_set.tracks
        for anchor in track.anchors
    }

    crossed_owners = _different_request_same_sources(fixture)
    crossed_fixture = replace(
        fixture,
        request=crossed_owners.request,
        attempt=crossed_owners.attempt,
        owners=crossed_owners,
    )
    crossed_assessment = _assessment_with_observation_only_peak(
        crossed_fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    crossed_result = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=crossed_fixture.owners,
        sample_assessments=(
            (crossed_fixture.sample("early"), crossed_assessment),
        ),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    anchored_transaction = next(
        transaction
        for transaction in result.transactions
        if transaction.trajectory_input.anchor_ids
    )
    foreign_anchor = next(
        anchor
        for track in crossed_result.model_set.tracks
        for anchor in track.anchors
    )
    predecessor_ids = {
        group.group_hypothesis_id
        for group in anchored_transaction.application_context.predecessors
    }
    component_owners = tuple(
        owner
        for group in result.family_context.mode_tracks
        if group.group_hypothesis_id in predecessor_ids
        for owner in group.owners
    )
    with pytest.raises(ModeTrackRefinementContractError, match="crossed its request"):
        mint_mode_track_trajectory_input(
            predecessors=anchored_transaction.application_context.predecessors,
            owners=component_owners,
            classified_edges=(
                anchored_transaction.application_context.classified_edges
            ),
            anchors=(foreign_anchor,),
            injection_order=dict(
                anchored_transaction.trajectory_input.injection_order
            ),
            evidence_profile=anchored_transaction.trajectory_input.evidence_profile,
            budget=anchored_transaction.application_context.budget,
        )


def test_refinement_bootstrap_forwards_exact_drift_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.alignment.mode_track.refinement as refinement_owner
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.mode_track import (
        ModeTrackTrajectoryPolicy,
        bootstrap_mode_track_refinement_from_cid_nl_support,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("early",))
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )

    class DriftLookup:
        source = "none"

        def sample_delta_min(self, _sample_stem: str) -> None:
            return None

        def injection_order(self, _sample_stem: str) -> None:
            return None

    drift_lookup = DriftLookup()
    seen = []
    classify = refinement_owner.classify_cross_sample_initial_neighborhood_edge

    def observe(**kwargs):
        seen.append(kwargs["drift_lookup"])
        return classify(**kwargs)

    monkeypatch.setattr(
        refinement_owner,
        "classify_cross_sample_initial_neighborhood_edge",
        observe,
    )
    bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        drift_lookup=drift_lookup,
    )

    assert seen
    assert all(item is drift_lookup for item in seen)


def test_public_workflow_returns_cross_sample_application_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.mode_track import (
        ModeTrackTrajectoryPolicy,
        bootstrap_mode_track_refinement_from_cid_nl_support,
    )
    from xic_extractor.extraction import (
        untargeted_cid_nl_mode_track_workflow as workflow,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("early",))
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    refinement = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    raw_path = tmp_path / "early.raw"
    raw_path.write_bytes(b"synthetic")
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    discovery_index = tmp_path / "discovery-index.csv"
    receipt = object()
    runtime_job = SimpleNamespace(work_unit_key="untargeted-cid-nl:early")
    runtime_result = SimpleNamespace(
        succeeded=True,
        sample_results=(
            SimpleNamespace(
                adapter_result=SimpleNamespace(
                    assessment_input=SimpleNamespace(assessment=assessment)
                )
            ),
        ),
        finalization=object(),
    )
    projection = object()

    monkeypatch.setattr(
        workflow,
        "run_discovery_batch",
        lambda *args, **kwargs: SimpleNamespace(batch_index_csv=discovery_index),
    )
    monkeypatch.setattr(
        workflow,
        "read_discovery_batch_index",
        lambda *args, **kwargs: SimpleNamespace(
            candidate_receipts={"early": receipt},
            candidate_csvs={"early": tmp_path / "candidates.csv"},
        ),
    )
    monkeypatch.setattr(
        workflow,
        "read_owner_bound_discovery_candidates_csv",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        workflow,
        "build_untargeted_cid_nl_real_data_context",
        lambda **kwargs: SimpleNamespace(
            processing_owners=fixture.owners,
            samples=(fixture.sample("early"),),
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_mode_track_runtime_jobs",
        lambda **kwargs: (runtime_job,),
    )
    monkeypatch.setattr(workflow, "start_runtime_attempt", lambda **kwargs: object())
    monkeypatch.setattr(
        workflow,
        "execute_untargeted_cid_nl_real_data_runtime",
        lambda *args, **kwargs: runtime_result,
    )
    monkeypatch.setattr(
        workflow,
        "untargeted_cid_nl_runtime_call_cost",
        lambda finalization: {
            "raw_open_count": 1,
            "xic_batch_count": 1,
            "logical_xic_request_count": 2,
            "adapter_chromatogram_call_count": 1,
            "trace_point_count": 3,
        },
    )
    monkeypatch.setattr(
        workflow,
        "bootstrap_mode_track_refinement_from_cid_nl_support",
        lambda **kwargs: refinement,
    )
    monkeypatch.setattr(
        workflow,
        "assess_mode_track_conservative_backfill_groups",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        workflow,
        "build_untargeted_cid_nl_mode_track_projection",
        lambda **kwargs: projection,
    )

    run = workflow.run_untargeted_cid_nl_mode_track_workflow(
        raw_files=(raw_path,),
        sample_metadata=(fixture.sample("early").metadata,),
        dll_dir=dll_dir,
        output_dir=tmp_path / "workflow-output",
        settings=_settings(),
        precursor_mz=500.0,
        source_artifact_relpath="diagnostic/source.tsv",
        sample_identity_namespace="issue-303-public-workflow",
    )

    assert run.refinement_transactions == refinement.transactions
    assert run.cross_sample_applications == (
        workflow._apply_mode_track_refinement_transactions(refinement.transactions)
    )
    assert run.projection is projection
    assert run.call_cost["total_raw_open_count"] == 2
