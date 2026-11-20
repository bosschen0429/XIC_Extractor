"""Pure Target/Untarget completion over acquired evidence and scientific owners."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias, cast

from xic_extractor.alignment.backfill.conservative import (
    ConservativeBackfillCellDecision,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
)
from xic_extractor.alignment.cell_observation.observations import CellObservation
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.grouping_workflow import (
    build_targeted_cross_sample_groups,
)
from xic_extractor.alignment.projection.target.post_cell import (
    project_target_post_cell,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    project_target_pre_cell,
)
from xic_extractor.alignment.projection.untarget.matrix import (
    project_untarget_matrix,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
    UntargetedCandidateExclusion,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    TerminalSearchObservation,
    finalize_scientific_alignment,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.sample_metadata import SampleMetadata


class AlignmentWorkflowContractError(ValueError):
    """Raised when the shared workflow cannot preserve exact owner truth."""


class WorkflowStageFailure(Exception):
    """A completion stage failed; ``stage`` names it and ``error`` is the cause."""

    def __init__(self, stage: WorkflowStage, error: Exception) -> None:
        super().__init__(str(error))
        self.stage = stage
        self.error = error


class WorkflowStage(str, Enum):
    RUNTIME = "runtime"
    CROSS_SAMPLE_GROUPING = "cross_sample_grouping"
    SCIENTIFIC_FINAL = "scientific_final"
    PRE_CELL_PROJECTION = "pre_cell_projection"
    CELL_DECISION = "cell_decision"
    POST_CELL_PROJECTION = "post_cell_projection"
    APPLICATION_RESULT = "application_result"


@dataclass(frozen=True, slots=True)
class TargetWorkflowRequest:
    scope: TargetSearchScope


@dataclass(frozen=True, slots=True)
class UntargetWorkflowCompositionRequest:
    """Untarget scientific composition inputs independent of runtime closure."""

    scope: UntargetedSearchScope
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...] = ()


WorkflowRequest: TypeAlias = TargetWorkflowRequest | UntargetWorkflowCompositionRequest


@dataclass(frozen=True, slots=True)
class _WorkflowAdapterFields:
    terminal_subjects: tuple[TerminalSearchObservation, ...]
    predecessor_cell_observations: tuple[CellObservation, ...]
    rt_frame: SampleRtFrame
    backfill_cell_observations: tuple[CellObservation, ...] = ()


@dataclass(frozen=True)
class TargetWorkflowAdapter(_WorkflowAdapterFields):
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...] = (
        field(kw_only=True)
    )
    alignment_config: AlignmentConfig = field(kw_only=True)
    targeted_backfill_decisions: tuple[ConservativeBackfillCellDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class UntargetWorkflowAdapter(_WorkflowAdapterFields):
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...] = (
        field(kw_only=True)
    )
    completed_grouping: CrossSampleGroupingResult | None = None
    closed_cell_coverage: BackfillEvaluationCoverageBatch | None = None


WorkflowAdapter: TypeAlias = TargetWorkflowAdapter | UntargetWorkflowAdapter


def compose_untarget_subject(
    request: UntargetWorkflowCompositionRequest,
    adapter: UntargetWorkflowAdapter,
) -> FinalizedUntargetApplicationResult:
    """Close one bounded subject workset without asserting runtime completion."""
    if type(request) is not UntargetWorkflowCompositionRequest:
        raise AlignmentWorkflowContractError(
            "Subject composition requires exact inputs"
        )
    subjects = set(_untarget_scope_memberships(request.scope).values())
    provider_subjects = {
        provider.method_subject_id for provider in request.scope.evidence_providers
    }
    if subjects != provider_subjects or request.excluded_candidates:
        raise AlignmentWorkflowContractError(
            "Subject composition requires one exact included subject workset"
        )
    try:
        result = complete_analysis(request, adapter)
    except WorkflowStageFailure as failure:
        raise AlignmentWorkflowContractError(
            f"Untarget subject composition failed at {failure.stage.value}: "
            f"{failure.error}"
        ) from failure.error
    if type(result) is not FinalizedUntargetApplicationResult:
        raise AlignmentWorkflowContractError(
            "Subject composition returned crossed result"
        )
    return result


def _compose_application_result(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
    stage = WorkflowStage.CROSS_SAMPLE_GROUPING
    try:
        if type(adapter) is TargetWorkflowAdapter:
            context = build_targeted_cross_sample_groups(
                samples=request.scope.samples,
                terminal_subjects=adapter.terminal_subjects,
                sample_assessments=adapter.sample_assessments,
                alignment_config=adapter.alignment_config,
                rt_frame=adapter.rt_frame,
            )
        else:
            untarget_adapter = cast(UntargetWorkflowAdapter, adapter)
            # The adapter contract admits Untarget only with completed grouping.
            assert untarget_adapter.completed_grouping is not None
            context = untarget_adapter.completed_grouping
        closed_coverage = (
            adapter.closed_cell_coverage
            if type(adapter) is UntargetWorkflowAdapter
            else None
        )
        if type(request) is not TargetWorkflowRequest and closed_coverage is None:
            raise AlignmentWorkflowContractError(
                "Untarget requires closed cell coverage"
            )
        final_receipt = context
        handoff = None
        if closed_coverage is not None:
            stage = WorkflowStage.SCIENTIFIC_FINAL
            handoff = build_cross_sample_association_lineage_handoff(
                coverage=closed_coverage,
                final_receipt=final_receipt,
            )
        scientific = finalize_scientific_alignment(
            scope=request.scope,
            terminal_subjects=adapter.terminal_subjects,
            grouping_result=final_receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=(
                adapter.predecessor_cell_observations
                if closed_coverage is not None
                else ()
            ),
            backfill_cell_observations=adapter.backfill_cell_observations,
            targeted_backfill_decisions=(
                adapter.targeted_backfill_decisions
                if type(adapter) is TargetWorkflowAdapter
                else ()
            ),
        )
        return _finish_variant(
            request=request,
            scientific=scientific,
        )

    except WorkflowStageFailure:
        raise
    except Exception as exc:
        raise WorkflowStageFailure(stage, exc) from exc


def _untarget_scope_memberships(scope: UntargetedSearchScope) -> dict[str, str]:
    memberships: dict[str, str] = {}
    for binding in scope.capture_bindings:
        candidate_id = binding.candidate_id
        subject_id = binding.method_subject_id
        if not isinstance(candidate_id, str) or not isinstance(subject_id, str):
            raise AlignmentWorkflowContractError(
                "Untarget batch capture membership is incomplete"
            )
        if candidate_id in memberships:
            raise AlignmentWorkflowContractError(
                "Untarget batch capture membership contains duplicate candidates"
            )
        memberships[candidate_id] = subject_id
    return memberships


def _finish_variant(
    *,
    request: WorkflowRequest,
    scientific: FinalizedScientificAlignment,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
    stage = WorkflowStage.PRE_CELL_PROJECTION
    try:
        if type(request) is TargetWorkflowRequest:
            target_pre_cell = project_target_pre_cell(scientific)
            stage = WorkflowStage.CELL_DECISION
            from xic_extractor.alignment.cell_decision.owner import (
                decide_target_cell_population,
            )

            target_decisions = decide_target_cell_population(target_pre_cell)
            stage = WorkflowStage.POST_CELL_PROJECTION
            target_projection = project_target_post_cell(
                target_pre_cell, target_decisions
            )
            stage = WorkflowStage.APPLICATION_RESULT
            return FinalizedTargetApplicationResult(
                scientific_core=scientific,
                completed_projection=target_projection,
            )
        stage = WorkflowStage.CELL_DECISION
        from xic_extractor.alignment.cell_decision.owner import (
            decide_group_cell_population,
        )

        untarget_decisions = decide_group_cell_population(scientific)
        stage = WorkflowStage.POST_CELL_PROJECTION
        group_cells = project_untarget_matrix(scientific, untarget_decisions)
        stage = WorkflowStage.APPLICATION_RESULT
        return FinalizedUntargetApplicationResult(
            scientific_core=scientific,
            group_cells=group_cells,
        )
    except Exception as exc:
        raise WorkflowStageFailure(stage, exc) from exc


def complete_analysis(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
    """Complete acquired evidence without invoking runtime or persistence."""
    return _compose_application_result(request, adapter)


__all__ = (
    "AlignmentWorkflowContractError",
    "TargetWorkflowAdapter",
    "TargetWorkflowRequest",
    "UntargetWorkflowAdapter",
    "UntargetWorkflowCompositionRequest",
    "WorkflowAdapter",
    "WorkflowRequest",
    "WorkflowStage",
    "WorkflowStageFailure",
    "complete_analysis",
    "compose_untarget_subject",
)
