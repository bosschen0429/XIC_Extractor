"""Build the initial CID-NL grouping population and its physical bindings."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    bind_retained_peak_hypotheses_to_assessment_universe,
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackCandidateObservation,
    ModeTrackSupportAnchor,
    mint_mode_track_candidate_observation,
    mint_mode_track_support_anchor,
)
from xic_extractor.alignment.mode_track.assignment_scope import (
    ModeTrackAssignmentScope,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_resolver_interval,
    validate_cid_nl_alignment_subject_compatibility,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class GroupingBootstrapError(ValueError):
    """Raised when assessment support cannot close the initial population."""


def bootstrap_cid_nl_initial_population(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    injection_order: Mapping[str, int] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    retain_observation_only_candidates: bool = False,
    retained_hypotheses_by_assessment: Mapping[
        tuple[str, str], tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    ModeTrackAssignmentScope,
    tuple[ModeTrackSupportAnchor, ...],
    tuple[
        tuple[
            ResolvedCanonicalSampleIdentity, tuple[ModeTrackCandidateObservation, ...]
        ],
        ...,
    ],
    DriftLookupProtocol | None,
]:
    """Build initial groups and their physical candidate bindings."""

    if not sample_assessments:
        raise GroupingBootstrapError("sample_assessments must not be empty")
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise GroupingBootstrapError("processing_owners must be owner-validated")
    if not isinstance(alignment_config, AlignmentConfig):
        raise GroupingBootstrapError("alignment_config must be explicit and typed")
    if not isinstance(retain_observation_only_candidates, bool):
        raise GroupingBootstrapError(
            "retain_observation_only_candidates must be boolean"
        )

    method_subjects_by_id = {
        assessment.content.method_subject.method_subject_id: (
            assessment.content.method_subject
        )
        for _, assessment in sample_assessments
    }
    method_subjects = tuple(
        sorted(method_subjects_by_id.values(), key=lambda item: item.method_subject_id)
    )
    try:
        validate_cid_nl_alignment_subject_compatibility(method_subjects)
    except CidNlMethodSubjectContractError as exc:
        raise GroupingBootstrapError(str(exc)) from exc

    hypotheses_by_sample: dict[str, tuple[PeakHypothesis, ...]] = {}
    assessment_by_hypothesis: dict[str, CidNlMethodAssessmentRef] = {}
    sample_by_id: dict[str, ResolvedCanonicalSampleIdentity] = {}
    owners: list[SampleLocalMS1Owner] = []
    retained_by_assessment = dict(retained_hypotheses_by_assessment or {})
    consumed_retained_assessment_ids: set[tuple[str, str]] = set()
    for sample, assessment in sample_assessments:
        sample_id = sample.identity.sample_identity_id
        assessment_content_id = assessment.artifact.content_identity.artifact_content_id
        retained_key = (sample_id, assessment_content_id)
        retained_hypotheses = retained_by_assessment.get(retained_key, ())
        if retained_key in retained_by_assessment:
            consumed_retained_assessment_ids.add(retained_key)
        hypotheses = (
            bind_retained_peak_hypotheses_to_assessment_universe(
                processing_owners=processing_owners,
                sample=sample,
                assessment=assessment,
                retained_hypotheses=retained_hypotheses,
            )
            if retained_hypotheses
            else compose_peak_hypotheses_from_assessment_universe(
                processing_owners=processing_owners,
                sample=sample,
                assessment=assessment,
            )
        )
        existing = hypotheses_by_sample.get(sample_id, ())
        known = {hypothesis.hypothesis_id for hypothesis in existing}
        hypotheses_by_sample[sample_id] = existing + tuple(
            hypothesis
            for hypothesis in hypotheses
            if hypothesis.hypothesis_id not in known
        )
        for hypothesis in hypotheses:
            assessment_by_hypothesis.setdefault(hypothesis.hypothesis_id, assessment)
        sample_by_id[sample_id] = sample
    if consumed_retained_assessment_ids != set(retained_by_assessment):
        raise GroupingBootstrapError(
            "retained hypotheses reference an assessment outside the bootstrap input"
        )
    has_direct_support = any(
        hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
    )
    include_observation_only = (
        not retain_observation_only_candidates or not has_direct_support
    )
    for sample_id in sorted(hypotheses_by_sample):
        sample = sample_by_id[sample_id]
        hypotheses = hypotheses_by_sample[sample_id]
        for hypothesis in hypotheses:
            assessment = assessment_by_hypothesis[hypothesis.hypothesis_id]
            if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL:
                owners.append(
                    owner_from_cid_nl_assessment_support(
                        sample=sample,
                        assessment=assessment,
                        hypothesis=hypothesis,
                    )
                )
            elif include_observation_only:
                owners.append(
                    owner_from_cid_nl_assessment_observation(
                        processing_owners=processing_owners,
                        sample=sample,
                        assessment=assessment,
                        hypothesis=hypothesis,
                    )
                )
    searched_no_observation = not owners and all(
        assessment.content.target_snapshot_id is not None
        and assessment.content.technical_state == "evaluated"
        and assessment.content.evaluation_receipt.acquisition_supported
        and assessment.content.evaluation_receipt.search_completed
        and not assessment.content.candidate_peak_content_ids
        for _, assessment in sample_assessments
    )
    if not owners and not searched_no_observation:
        raise GroupingBootstrapError("bootstrap assessments have no peak observations")
    if known_istd_reference is not None:
        if drift_lookup is not None:
            raise ValueError(
                "known ISTD reference cannot be combined with generic drift"
            )
        # The reference is a measured RAW-level identity constraint.  It must
        # be allowed to bind every physical MS1 owner in the retained
        # assessment universe, including MS1-only observations.  Restricting
        # this lookup to DDA SUPPORT owners made the known-ISTD constraint
        # disappear exactly where Discovery had an MS1 peak but no qualifying
        # MS2 relation, leaving those owners ambiguous or outside the known
        # group.
        drift_lookup = known_istd_reference.bind(
            tuple(owners),
            sample_universe=sample_universe,
            injection_order=injection_order,
        )
    provisional_groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        operation_counts_sink=operation_counts_sink,
        bootstrap=True,
    )
    if not provisional_groups and not searched_no_observation:
        raise GroupingBootstrapError(
            "bootstrap observations did not produce consumable cross-sample groups"
        )
    provisional_group_ids = {
        owner_id: track.group_hypothesis_id
        for track in provisional_groups
        for owner_id in track.owner_ids
    }
    expected_owner_ids = {
        hypothesis.hypothesis_id
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if (
            include_observation_only
            or hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        )
    }
    if set(provisional_group_ids) != expected_owner_ids:
        raise GroupingBootstrapError(
            "every provisional owner hypothesis must own exactly one group"
        )

    direct_group_ids = {
        provisional_group_ids[hypothesis.hypothesis_id]
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
    }
    if has_direct_support and not direct_group_ids:
        raise GroupingBootstrapError(
            "no provisional group has a direct CID-NL trajectory anchor"
        )
    tracks = provisional_groups
    hypotheses = tuple(
        hypothesis
        for sample_id in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[sample_id]
    )
    method_subject_intervals = tuple(
        cid_nl_method_subject_resolver_interval(subject) for subject in method_subjects
    )
    assignment_scope = ModeTrackAssignmentScope(
        mz_interval=(
            min(interval[0] for interval in method_subject_intervals),
            max(interval[1] for interval in method_subject_intervals),
        ),
        rt_interval=(
            min(hypothesis.integration.rt_left_min for hypothesis in hypotheses)
            if hypotheses
            else min(
                assessment.content.evaluation_receipt.query_rt_min
                for _, assessment in sample_assessments
            ),
            max(hypothesis.integration.rt_right_min for hypothesis in hypotheses)
            if hypotheses
            else max(
                assessment.content.evaluation_receipt.query_rt_max
                for _, assessment in sample_assessments
            ),
        ),
        groups=tracks,
    )
    track_by_id = {track.group_hypothesis_id: track for track in tracks}
    anchors = tuple(
        mint_mode_track_support_anchor(
            processing_owners=processing_owners,
            track=track_by_id[provisional_group_ids[hypothesis.hypothesis_id]],
            sample=sample_by_id[sample_id],
            direct_support_assessment=assessment_by_hypothesis[
                hypothesis.hypothesis_id
            ],
            peak_hypothesis=hypothesis,
        )
        for sample_id in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[sample_id]
        if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
    )
    sample_candidates = []
    for sample_id in sorted(hypotheses_by_sample):
        sample = sample_by_id[sample_id]
        candidates = tuple(
            mint_mode_track_candidate_observation(
                processing_owners=processing_owners,
                sample=sample,
                peak_hypothesis=hypothesis,
                direct_support_assessment=(
                    assessment_by_hypothesis[hypothesis.hypothesis_id]
                    if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
                    else None
                ),
            )
            for hypothesis in hypotheses_by_sample[sample_id]
        )
        sample_candidates.append((sample, candidates))
    return (
        assignment_scope,
        anchors,
        tuple(sample_candidates),
        drift_lookup,
    )
