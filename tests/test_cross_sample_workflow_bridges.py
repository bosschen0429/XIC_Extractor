"""CrossSample-owned bridges used by the shared alignment workflow."""

from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.cell_observations import (
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleReconciliationContractError,
    apply_mode_track_refinement_run_for_workflow,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
    consume_observation_repair_terminal,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import (
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
)
from xic_extractor.alignment.mode_track.refinement import (
    _REFINEMENT_RUN_MINT_TOKEN,
)
from xic_extractor.alignment.projection.target import (
    project_target_post_cell,
    project_target_pre_cell,
)
from xic_extractor.alignment.result import (
    TargetApplicationResultRequest,
    finalize_alignment_result,
    finalize_scientific_alignment,
)
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    _CaptureOwnerSeal,
    capture_peak_hypotheses,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CID_NL_PROVIDER_ID
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def _captures_for_run(run):
    scope = object.__new__(UntargetedSearchScope)
    candidate_ids = tuple(
        f"workflow-candidate:{index}"
        for index, _owner in enumerate(run.owners, start=1)
    )
    object.__setattr__(scope, "declared_candidate_ids", candidate_ids)
    captures = []
    for candidate_id, owner in zip(candidate_ids, run.owners, strict=True):
        hypothesis = object.__new__(PeakHypothesis)
        object.__setattr__(hypothesis, "hypothesis_id", owner.owner_id)
        object.__setattr__(hypothesis, "integration", owner.selected_integration)
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


def _target_scope_and_captures(fixture, spine, tmp_path: Path):
    method_subject = spine.cells[0].group_context.method_subject
    rt = np.linspace(7.7, 8.3, 61)
    candidate_ids = tuple(
        f"workflow-final:{sample.lookup_token}" for sample in fixture.samples
    )
    bindings = []
    traces = []
    for candidate_id, sample in zip(candidate_ids, fixture.samples, strict=True):
        source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=sample,
        )
        bindings.append(
            _capture_binding(
                candidate_id=candidate_id,
                sample=sample,
                acquisition_source_id=source.acquisition_source_id,
                mz=method_subject.precursor_mz,
                ppm_tol=method_subject.precursor_tolerance_ppm,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
            )
        )
        intensity = np.zeros_like(rt)
        traces.append(
            Trace.from_arrays(
                sample_name=sample.metadata.sample_name,
                mz=method_subject.precursor_mz,
                rt=rt,
                intensity=intensity,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
                ppm_tol=method_subject.precursor_tolerance_ppm,
                source="alignment_workflow_contract_test",
                acquisition_source_id=source.acquisition_source_id,
            )
        )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=tuple(bindings),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    return scope, tuple(
        capture_peak_hypotheses(
            scope=scope,
            candidate_id=candidate_id,
            trace=trace,
        )
        for candidate_id, trace in zip(candidate_ids, traces, strict=True)
    )


def test_workflow_repair_bridges_accept_no_caller_route_or_proposal() -> None:
    assert tuple(
        signature(apply_mode_track_refinement_run_for_workflow).parameters
    ) == ("processing_owners", "captures", "run")
    assert tuple(signature(consume_observation_repair_terminal).parameters) == (
        "context",
        "terminal",
        "repair_result",
        "alignment_config",
        "drift_lookup",
    )
    assert tuple(signature(consume_mode_track_final_attempt).parameters) == (
        "context",
        "post_repair",
        "terminal",
        "final_attempt",
    )


@pytest.mark.parametrize("hold_samples", [(), ("s2", "s4")])
def test_no_receipt_terminal_closes_epoch_zero_without_repair_evidence(
    tmp_path: Path,
    hold_samples: tuple[str, ...],
) -> None:
    from xic_extractor.alignment.cell_decision.owner import (
        decide_target_cell_population,
    )

    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=hold_samples,
    )
    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=lambda _candidate: pytest.fail("blocked repair invoked callback"),
    )
    group = spine.group
    assert group is not None
    identity = validate_cross_sample_peak_group_identity(
        group,
        require_canonical=True,
    )
    population = (identity,)
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=population,
        successors=population,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.request.processing_request_id,
        processing_owners=fixture.owners,
        population=population,
        application=application,
        owners=group.owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(group.owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )

    result = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=None,
        alignment_config=coverage.epoch.alignment_config,
    )

    assert isinstance(result, CrossSampleFinalReceipt)
    assert result.repair_terminal is terminal
    assert result.repair_receipt is None
    assert result.repair_epoch == 0
    assert result.cs2_state.repair_authorization_id == (
        terminal.authorization.authorization_id
    )
    assert result.cs2_state.repair_receipt_id is None
    assert result.cs2_state.repair_outcome == terminal.state
    assert result.mode_track_final_attempt is None
    assert result.final_population == context.population
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="legacy final receipt requires",
    ):
        replace(
            result,
            repair_terminal=None,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="repair epoch must be an exact integer",
    ):
        replace(
            result,
            repair_epoch=False,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=result,
    )
    final_cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=cells,
        repair_receipts=(),
        repaired_observations=(),
    )
    assert final_cells
    assert all(cell.repair_receipts == () for cell in final_cells)
    scope, captures = _target_scope_and_captures(fixture, spine, tmp_path)
    scientific = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=result,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=cells,
    )
    pre_cell = project_target_pre_cell(scientific)
    projection = project_target_post_cell(
        pre_cell,
        decide_target_cell_population(pre_cell),
    )
    application_result = finalize_alignment_result(
        TargetApplicationResultRequest(
            scientific_core=scientific,
            completed_projection=projection,
        )
    )
    assert application_result.scientific_core is scientific
    assert application_result.completed_projection is projection
    assert scientific.final_cross_sample_receipt is result
    assert scientific.repair_receipt is None


def test_mt0_bridge_rejects_missing_capture_owner_closure(tmp_path: Path) -> None:
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment.mode_track.refinement import (
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
    run = bootstrap_mode_track_refinement_from_cid_nl_support(
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

    with pytest.raises(
        ModeTrackRefinementContractError,
        match="must be minted by ModeTrack",
    ):
        replace(run, _mint_token=object())

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="captures do not close",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=(),
            run=run,
        )

    captures = _captures_for_run(run)
    assert apply_mode_track_refinement_run_for_workflow(
        processing_owners=fixture.owners,
        captures=captures,
        run=run,
    )

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="complete candidate order",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=tuple(reversed(captures)),
            run=run,
        )

    crossed_scope = object.__new__(UntargetedSearchScope)
    object.__setattr__(
        crossed_scope,
        "declared_candidate_ids",
        captures[0].scope.declared_candidate_ids,
    )
    crossed = replace(
        captures[0],
        scope=crossed_scope,
        _owner_seal=replace(captures[0]._owner_seal, scope=crossed_scope),
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="crossed their exact SearchScope",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=(crossed, *captures[1:]),
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
        CrossSampleReconciliationContractError,
        match="physical integration",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=captures,
            run=stale_run,
        )

    assert len(run.transactions) > 1
    reordered_run = replace(
        run,
        transactions=tuple(reversed(run.transactions)),
        _mint_token=_REFINEMENT_RUN_MINT_TOKEN,
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="canonical predecessor order",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=captures,
            run=reordered_run,
        )

    first, second, *rest = run.transactions
    crossed_transaction = replace(first, proposal=second.proposal)
    crossed_run = replace(
        run,
        transactions=(crossed_transaction, second, *rest),
        _mint_token=_REFINEMENT_RUN_MINT_TOKEN,
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exact component context",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=captures,
            run=crossed_run,
        )
