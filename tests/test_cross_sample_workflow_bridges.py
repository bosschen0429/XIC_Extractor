"""CrossSample-owned bridges used by the shared alignment workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingContractError,
)
from xic_extractor.alignment.grouping_workflow import (
    apply_mode_track_refinement_run_for_workflow,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import (
    AcceptedModeTrackRefinement,
    ModeTrackRefinementContractError,
)
from xic_extractor.alignment.mode_track.refinement import (
    _REFINEMENT_RUN_MINT_TOKEN,
)
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    _CaptureOwnerSeal,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    UntargetedSearchScope,
)


def _captures_for_run(run, *, include_retained_candidates: bool = False):
    hypotheses = (
        tuple(
            candidate.peak_hypothesis
            for sample_source in run.fit_once_source.sample_sources
            for candidate in sample_source.candidates
        )
        if include_retained_candidates
        else tuple(
            SimpleNamespace(
                hypothesis_id=owner.owner_id,
                integration=owner.selected_integration,
            )
            for owner in run.owners
        )
    )
    scope = object.__new__(UntargetedSearchScope)
    candidate_ids = tuple(
        f"workflow-candidate:{index}"
        for index, _hypothesis in enumerate(hypotheses, start=1)
    )
    object.__setattr__(
        scope,
        "declared_candidate_ids",
        candidate_ids,
    )
    object.__setattr__(
        scope,
        "capture_bindings",
        tuple(SimpleNamespace(candidate_id=item) for item in candidate_ids),
    )
    captures = []
    for candidate_id, source_hypothesis in zip(
        candidate_ids,
        hypotheses,
        strict=True,
    ):
        hypothesis = object.__new__(PeakHypothesis)
        object.__setattr__(
            hypothesis,
            "hypothesis_id",
            source_hypothesis.hypothesis_id,
        )
        object.__setattr__(
            hypothesis,
            "integration",
            source_hypothesis.integration,
        )
        trace = object()
        trace_group = object()
        detection = object()
        hypotheses = (hypothesis,)
        seal = _CaptureOwnerSeal(
            scope,
            candidate_id,
            trace,
            trace_group,
            detection,
            hypotheses,
            hypothesis,
            hypothesis,
        )
        captures.append(
            SampleLocalCapture(
                seal,
                scope,
                candidate_id,
                trace,
                trace_group,
                detection,
                hypotheses,
                hypothesis,
                hypothesis,
            )
        )
    return tuple(captures)


def test_initial_grouping_preserves_complete_components(tmp_path: Path) -> None:
    from tests.cid_nl_peak_support_no_raw_fixture import (
        cid_nl_evidence,
        cid_nl_peak,
        cid_nl_support_policy,
        cid_nl_unique_support,
    )
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        bootstrap_mode_track_refinement_from_cid_nl_support,
    )
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlEvaluationReceipt,
        mint_cid_nl_method_assessment_ref,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("early", "late", "remote")
    )
    assessments = []
    for stem, apexes in (
        ("early", (7.7, 8.1)),
        ("late", (7.72, 8.12)),
        ("remote", (8.91,)),
    ):
        peaks = tuple(
            cid_nl_peak(
                fixture, stem, apex_rt=rt, rt_left_min=rt - 0.1, rt_right_min=rt + 0.1
            )
            for rt in apexes
        )
        evidence = tuple(
            cid_nl_evidence(fixture, stem, scan_number=index, scan_rt_min=rt)
            for index, rt in enumerate(apexes, start=201)
        )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample(stem),
            target=fixture.target,
            detector_invocation_id=f"dual:{stem}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=7.5,
                query_rt_max=9.2,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                scanned_event_count=len(peaks),
                reason_code=None,
            ),
            candidate_peaks=peaks,
            evidence_refs=evidence,
            individual_support_refs=tuple(
                cid_nl_unique_support(
                    fixture,
                    stem,
                    peak=peak,
                    evidence=event,
                    candidate_peaks=peaks,
                )
                for peak, event in zip(peaks, evidence, strict=True)
            ),
        )
        assessments.append((fixture.sample(stem), assessment))
    run = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=tuple(assessments),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1, preferred_rt_sec=30),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory",
            band_policy_version="fixed-band",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        retain_observation_only_candidates=True,
    )
    assert sorted(
        sum(
            len(group.member_subjects)
            for group in transaction.application_context.predecessors
        )
        for transaction in run.transactions
    ) == [1, 2, 2]
    for transaction in run.transactions:
        receipt = transaction.application_context.grouping_receipt
        assert receipt is not None
        assert receipt.group_member_sets == tuple(
            sorted(
                tuple(
                    sorted(member.member_subject_id for member in group.member_subjects)
                )
                for group in transaction.application_context.predecessors
            )
        )
        assert transaction.trajectory_input.initial_grouping_receipt_id == (
            receipt.receipt_id
        )
    captures = _captures_for_run(run, include_retained_candidates=True)
    context = apply_mode_track_refinement_run_for_workflow(
        processing_owners=fixture.owners,
        captures=captures,
        run=run,
    )
    assert context.partition_transition.predecessors == run.predecessors
    assert {
        member.member_subject_id
        for group in context.final_population
        for member in group.member_subjects
    } == {
        member.member_subject_id
        for group in run.predecessors
        for member in group.member_subjects
    }


def test_initial_grouping_closes_mixed_population_and_rejects_crossed_inputs(
    tmp_path: Path,
) -> None:
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        bootstrap_mode_track_refinement_from_cid_nl_support,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("early",)
    )
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    run = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory",
            band_policy_version="fixed-band",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        retain_observation_only_candidates=True,
    )

    with pytest.raises(
        ModeTrackRefinementContractError,
        match="must be minted by ModeTrack",
    ):
        replace(run, _mint_token=object())

    with pytest.raises(
        CrossSampleGroupingContractError,
        match="captures do not close",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=(),
            run=run,
        )

    captures = _captures_for_run(run)
    context = apply_mode_track_refinement_run_for_workflow(
        processing_owners=fixture.owners,
        captures=captures,
        run=run,
    )
    retained_captures = _captures_for_run(run, include_retained_candidates=True)
    source_closed_context = apply_mode_track_refinement_run_for_workflow(
        processing_owners=fixture.owners,
        captures=retained_captures,
        run=run,
    )
    assert source_closed_context == context
    assert tuple(type(transaction.proposal) for transaction in run.transactions) == (
        AcceptedModeTrackRefinement,
    )
    assert context.partition_transition.predecessors == run.predecessors
    assert context.final_population == context.partition_transition.successors

    incomplete_scope = object.__new__(UntargetedSearchScope)
    object.__setattr__(
        incomplete_scope,
        "declared_candidate_ids",
        (*captures[0].scope.declared_candidate_ids, "missing:candidate"),
    )
    object.__setattr__(
        incomplete_scope,
        "capture_bindings",
        captures[0].scope.capture_bindings,
    )
    incomplete_captures = tuple(
        replace(
            capture,
            scope=incomplete_scope,
            _owner_seal=replace(capture._owner_seal, scope=incomplete_scope),
        )
        for capture in captures
    )
    with pytest.raises(
        CrossSampleGroupingContractError,
        match="declared candidate order",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=incomplete_captures,
            run=run,
        )

    with pytest.raises(
        CrossSampleGroupingContractError,
        match="declared candidate order",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=tuple(reversed(retained_captures)),
            run=run,
        )

    crossed_scope = object.__new__(UntargetedSearchScope)
    object.__setattr__(
        crossed_scope,
        "declared_candidate_ids",
        retained_captures[0].scope.declared_candidate_ids,
    )
    crossed = replace(
        retained_captures[0],
        scope=crossed_scope,
        _owner_seal=replace(
            retained_captures[0]._owner_seal,
            scope=crossed_scope,
        ),
    )
    with pytest.raises(
        CrossSampleGroupingContractError,
        match="crossed their exact SearchScope",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=(crossed, *retained_captures[1:]),
            run=run,
        )

    owner = run.owners[0]
    stale_owner = replace(owner, owner_area=owner.owner_area + 1.0)
    stale_run = replace(
        run,
        owners=(stale_owner, *run.owners[1:]),
        _mint_token=_REFINEMENT_RUN_MINT_TOKEN,
    )
    with pytest.raises(
        CrossSampleGroupingContractError,
        match="physical integration",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=captures,
            run=stale_run,
        )

    assert len(run.transactions) == 1
