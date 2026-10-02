"""Complete grouped CID-NL evidence without runtime or persistence mechanics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
    ConservativeNoRequestProofBuilder,
    build_backfill_evaluation_coverage,
    mint_backfill_planning_epoch,
    mint_backfill_position_id,
)
from xic_extractor.alignment.backfill_request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.cell_group_context import CidNlCellGroupContext
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.cid_nl_predecessor_cells import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.completion import (
    CaptureWorkflowArguments,
    UntargetWorkflowAdapter,
    UntargetWorkflowCompositionRequest,
    compose_untarget_subject,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCandidateUniverse,
    ConservativeBackfillGroupFacts,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_candidate_universe,
)
from xic_extractor.alignment.grouping_bootstrap import (
    InitialGroupingScope,
    RetainedGroupingCandidate,
)
from xic_extractor.alignment.grouping_result import CrossSampleGroupingResult
from xic_extractor.alignment.grouping_workflow import (
    admit_cid_nl_group_backfills,
    contested_backfill_peaks,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
)
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import UntargetedSearchScope
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


@dataclass(frozen=True, slots=True)
class CidNlWorksetGrouping:
    """The existing grouping owner's answer and its exact capture inputs."""

    subjects: tuple[CidNlMethodSubject, ...]
    scope: UntargetedSearchScope
    captures: tuple[SampleLocalCapture, ...]
    capture_inputs: tuple[CaptureWorkflowArguments, ...]
    grouping_scope: InitialGroupingScope
    grouping_result: CrossSampleGroupingResult
    retained_candidates: tuple[RetainedGroupingCandidate, ...]


@dataclass(frozen=True, slots=True)
class CidNlWorksetEvidence:
    """Acquired assessments and runtime-issued completion facts for one workset."""

    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ]
    initial_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ]
    group_backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ]


@dataclass(frozen=True, slots=True)
class _CellCoveragePlan:
    cells: Mapping[tuple[str, str], CellObservationRef]
    proof_positions: tuple[
        tuple[ResolvedCanonicalSampleIdentity, ConservativeBackfillGroupFacts], ...
    ]


@dataclass(frozen=True, slots=True)
class CidNlWorksetCompletion:
    """Prepared science; runtime supplies only the requested completion receipts."""

    _scope: UntargetedSearchScope
    _capture_inputs: tuple[CaptureWorkflowArguments, ...]
    _captures: tuple[SampleLocalCapture, ...]
    _sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ]
    _grouping_result: CrossSampleGroupingResult
    _predecessor_cells: tuple[CellObservationRef, ...]
    _backfill_cells: tuple[CellObservationRef, ...]
    _coverage_plan: _CellCoveragePlan
    _samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    _alignment_config: AlignmentConfig
    _raw_sample_stems: frozenset[str]

    @property
    def completion_requests(
        self,
    ) -> tuple[tuple[ResolvedCanonicalSampleIdentity, CidNlCellGroupContext], ...]:
        """Exact group contexts that need an already-completed runtime query."""
        return tuple(
            (sample, facts.group_context)
            for sample, facts in self._coverage_plan.proof_positions
            if facts.may_attempt_backfill
        )

    def complete(
        self, completed_empty_backfills: tuple[BackfillCompletionProjection, ...]
    ) -> FinalizedUntargetApplicationResult:
        coverage = _close_cell_coverage(
            self._grouping_result,
            self._coverage_plan,
            completed_empty_backfills,
            samples=self._samples,
            alignment_config=self._alignment_config,
            raw_sample_stems=self._raw_sample_stems,
        )
        owners = self._grouping_result.processing_owners
        return compose_untarget_subject(
            UntargetWorkflowCompositionRequest(
                scope=self._scope,
                processing_request=owners.request,
                processing_attempt=owners.attempt,
            ),
            UntargetWorkflowAdapter(
                capture_inputs=self._capture_inputs,
                completed_grouping=self._grouping_result,
                sample_assessments=self._sample_assessments,
                closed_cell_coverage=coverage,
                predecessor_cell_observations=self._predecessor_cells,
                backfill_cell_observations=self._backfill_cells,
            ),
            completed_captures=self._captures,
        )


def prepare_cid_nl_workset(
    grouping: CidNlWorksetGrouping,
    evidence: CidNlWorksetEvidence,
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    alignment_config: AlignmentConfig,
    raw_sample_stems: frozenset[str],
) -> CidNlWorksetCompletion:
    """Complete grouping and cells, then request exact runtime completion facts."""
    grouping_result, predecessor_cells, backfill_cells, backfilled_positions = (
        _complete_cell_observations(grouping, evidence, samples, alignment_config)
    )
    coverage_plan = _prepare_cell_coverage(
        grouping_result,
        predecessor_cells,
        backfilled_positions,
        evidence.group_backfills,
        samples=samples,
    )
    return CidNlWorksetCompletion(
        _scope=grouping.scope,
        _capture_inputs=grouping.capture_inputs,
        _captures=grouping.captures,
        _sample_assessments=evidence.sample_assessments,
        _grouping_result=grouping_result,
        _predecessor_cells=predecessor_cells,
        _backfill_cells=backfill_cells,
        _coverage_plan=coverage_plan,
        _samples=samples,
        _alignment_config=alignment_config,
        _raw_sample_stems=raw_sample_stems,
    )


def _complete_cell_observations(
    grouping: CidNlWorksetGrouping,
    evidence: CidNlWorksetEvidence,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    alignment_config: AlignmentConfig,
) -> tuple[
    CrossSampleGroupingResult,
    tuple[CellObservationRef, ...],
    tuple[CellObservationRef, ...],
    frozenset[tuple[str, str]],
]:
    owners = grouping.grouping_result.processing_owners
    grouping_scope = grouping.grouping_scope
    grouping_result = grouping.grouping_result
    initial_assessments = evidence.initial_assessments
    sample_universe = frozenset(sample.metadata.raw_stem for sample in samples)
    injection_order = {
        sample.metadata.raw_stem: sample.metadata.injection_order
        for sample in samples
        if sample.metadata.injection_order is not None
    }
    members_by_group = {
        group.group_hypothesis_id: frozenset(
            member.canonical_sample_identity_id for member in group.member_subjects
        )
        for group in grouping_scope.groups
    }
    group_backfills = {
        key: pair
        for key, pair in evidence.group_backfills.items()
        if key[1] not in members_by_group[key[0]]
    }
    retained_cell_hypotheses: dict[tuple[str, str], list[PeakHypothesis]] = {}
    for candidate in grouping.retained_candidates:
        assessment = candidate.direct_support_assessment
        if assessment is None:
            continue
        retained_cell_hypotheses.setdefault(
            (
                candidate.canonical_sample_identity_id,
                assessment.artifact.content_identity.artifact_content_id,
            ),
            [],
        ).append(candidate.peak_hypothesis)
    retained_hypotheses = {
        key: tuple(values) for key, values in retained_cell_hypotheses.items()
    }
    grouping_result, backfilled_groups, successor_ids, backfilled_hypotheses = (
        admit_cid_nl_group_backfills(
            result=grouping_result,
            groups=grouping_scope.groups,
            backfills=group_backfills,
            alignment_config=alignment_config,
        )
    )
    backfilled_positions = frozenset(
        (group_id, sample_id)
        for (group_id, sample_id, _), (_, assessment) in group_backfills.items()
        if assessment.content.method_result == "SUPPORT"
    )
    predecessor_cells = compose_cid_nl_predecessor_cells(
        processing_owners=owners,
        groups=grouping_scope.groups,
        sample_assessments=initial_assessments,
        alignment_config=alignment_config,
        drift_lookup=grouping_result.drift_lookup,
        sample_universe=sample_universe,
        injection_order=injection_order,
        backfill_assessments_by_group={
            key: pair
            for key, pair in group_backfills.items()
            if pair[1].content.method_result != "SUPPORT"
        },
        retained_hypotheses_by_assessment=retained_hypotheses,
        contested_backfill_peaks=contested_backfill_peaks(
            groups=backfilled_groups,
            backfills=group_backfills,
            alignment_config=alignment_config,
        ),
    )
    backfill_cells = (
        compose_cid_nl_predecessor_cells(
            processing_owners=owners,
            groups=backfilled_groups,
            sample_assessments=initial_assessments,
            alignment_config=alignment_config,
            drift_lookup=grouping_result.drift_lookup,
            sample_universe=sample_universe,
            injection_order=injection_order,
            backfill_assessments_by_group={
                (successor_ids[group_id], sample_id, subject_id): pair
                for (
                    group_id,
                    sample_id,
                    subject_id,
                ), pair in group_backfills.items()
                if pair[1].content.method_result == "SUPPORT"
            },
            retained_hypotheses_by_assessment={
                **retained_hypotheses,
                **backfilled_hypotheses,
            },
            selected_cells=frozenset(
                (successor_ids[group_id], sample_id)
                for group_id, sample_id in backfilled_positions
            ),
        )
        if backfilled_positions
        else ()
    )
    return grouping_result, predecessor_cells, backfill_cells, backfilled_positions


def _prepare_cell_coverage(
    grouping_result: CrossSampleGroupingResult,
    predecessor_cells: tuple[CellObservationRef, ...],
    backfilled_positions: frozenset[tuple[str, str]],
    group_backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> _CellCoveragePlan:
    cell_by_key = {}
    cells_by_group: dict[str, list[CellObservationRef]] = {}
    for predecessor_cell in predecessor_cells:
        group_id = predecessor_cell.content.group_hypothesis_id
        sample_id = predecessor_cell.content.canonical_sample_identity_id
        cell_by_key[(group_id, sample_id)] = predecessor_cell
        cells_by_group.setdefault(group_id, []).append(predecessor_cell)
    ordered_samples = tuple(
        sorted(samples, key=lambda sample: sample.identity.sample_identity_id)
    )
    proof_positions = []
    for group in grouping_result.initial_population:
        group_cells = tuple(cells_by_group[group.group_hypothesis_id])
        searched_sample_ids = {
            cell.content.canonical_sample_identity_id for cell in group_cells
        }
        facts = assess_conservative_backfill_group(
            group_cells,
            sample_population=samples,
            unsearched_sample_ids=tuple(
                sample.identity.sample_identity_id
                for sample in ordered_samples
                if sample.identity.sample_identity_id not in searched_sample_ids
            ),
        )
        for sample in samples:
            key = (group.group_hypothesis_id, sample.identity.sample_identity_id)
            cell = cell_by_key.get(key)
            if cell is None or key in backfilled_positions:
                continue
            if (
                cell.content.method_result != "HOLD"
                or cell.peak_observation is not None
            ):
                continue
            if facts.may_attempt_backfill:
                if cell.method_assessment.candidate_peaks:
                    continue
                method_subject_id = (
                    cell.method_assessment.content.method_subject.method_subject_id
                )
                if (
                    facts.group_context.group.group_center_rt is None
                    or (*key, method_subject_id) not in group_backfills
                ):
                    continue
            proof_positions.append((sample, facts))
    return _CellCoveragePlan(cell_by_key, tuple(proof_positions))


def _close_cell_coverage(
    grouping_result: CrossSampleGroupingResult,
    plan: _CellCoveragePlan,
    completed_empty_backfills: tuple[BackfillCompletionProjection, ...],
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    alignment_config: AlignmentConfig,
    raw_sample_stems: frozenset[str],
) -> BackfillEvaluationCoverageBatch:
    owners = grouping_result.processing_owners
    repair_groups = grouping_result.initial_population
    cell_by_key = plan.cells
    completions = {
        (
            item.group_identity.group_hypothesis_id,
            item.sample.identity.sample_identity_id,
            item.backfill_assessment.content.method_subject.method_subject_id,
        ): item
        for item in completed_empty_backfills
    }
    builders: dict[str, ConservativeNoRequestProofBuilder] = {}
    no_request_proofs = {}
    for sample, facts in plan.proof_positions:
        group = facts.group_context.group_identity
        key = (group.group_hypothesis_id, sample.identity.sample_identity_id)
        completion = None
        universes: tuple[ConservativeBackfillCandidateUniverse, ...] = ()
        if facts.may_attempt_backfill:
            completion = completions[(*key, facts.group_context.method_subject_id)]
            universes = (
                mint_conservative_backfill_candidate_universe(
                    processing_owners=owners,
                    sample=sample,
                    group_facts=(facts,),
                    candidate_assessment=completion.backfill_assessment,
                ),
            )
        (decision,) = map_conservative_backfill_candidates(
            (facts,), sample=sample, candidate_universes=universes
        )
        builder = builders.get(group.group_hypothesis_id)
        if builder is None:
            builder = ConservativeNoRequestProofBuilder(
                processing_owners=owners, group_identity=group, group_facts=facts
            )
            builders[group.group_hypothesis_id] = builder
        no_request_proofs[key] = builder.mint(
            sample=sample, decision=decision, completion=completion
        )
    positions = tuple(
        BackfillPlanningPosition(
            position_id=mint_backfill_position_id(
                processing_request_id=group.processing_request_id,
                group_hypothesis_id=group.group_hypothesis_id,
                sample_identity_id=sample.identity.sample_identity_id,
            ),
            group_identity=group,
            sample=sample,
            planning_state="not_planned",
            planning_reason=(
                "no_discovery_input"
                if cell is None
                else no_request_proofs[
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ].planning_reason
                if (group.group_hypothesis_id, sample.identity.sample_identity_id)
                in no_request_proofs
                else "local_owner_not_supersedable"
                if cell.content.outside_group is not None
                else "retained_unresolved_candidates"
                if (
                    cell.content.technical_state == "evaluated"
                    and cell.content.method_result == "HOLD"
                    and cell.peak_observation is None
                    and len(cell.method_assessment.candidate_peaks) >= 2
                )
                else "retained_local_owner"
            ),
            primary_logical_requests=(),
            conservative_no_request_proof=no_request_proofs.get(
                (group.group_hypothesis_id, sample.identity.sample_identity_id)
            ),
        )
        for group in repair_groups
        for sample in samples
        for cell in (
            cell_by_key.get(
                (group.group_hypothesis_id, sample.identity.sample_identity_id)
            ),
        )
    )
    epoch = mint_backfill_planning_epoch(
        processing_owners=owners,
        group_identities=repair_groups,
        samples=samples,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="full-audit",
        selected_family_ids=frozenset(),
        positions=positions,
    )
    return build_backfill_evaluation_coverage(epoch, ())
