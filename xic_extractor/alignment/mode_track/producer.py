"""Compose CID-NL direct support into sibling ModeTracks and assignments."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackAssignmentBatch,
    ModeTrackCandidateObservation,
    ModeTrackFitOnceSource,
    ModeTrackModelSet,
    ModeTrackSupportAnchor,
    ModeTrackTrajectoryPolicy,
    assign_mode_tracks,
    fit_mode_track_model_set,
    mint_mode_track_candidate_observation,
    mint_mode_track_fit_once_sample_source,
    mint_mode_track_fit_once_source,
    mint_mode_track_support_anchor,
    validate_mode_track_assignment_batch_for_consumption,
)
from xic_extractor.alignment.mode_track.family_context import (
    FamilyContextRef,
    mint_family_context_ref,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_FAMILY_CONTEXT_POLICY_VERSION = "cid-nl-method-subject-family-v1"
_MS1_FAMILY_CONTEXT_POLICY_VERSION = "target-ms1-family-v1"


class ModeTrackProducerError(ValueError):
    """Raised when direct support cannot close one ModeTrack composition."""


def bootstrap_mode_tracks_from_cid_nl_support(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
    retain_observation_only_candidates: bool = False,
) -> tuple[
    FamilyContextRef,
    ModeTrackModelSet,
    tuple[ModeTrackAssignmentBatch, ...],
    ModeTrackFitOnceSource,
]:
    """Run the existing identity-trajectory and ModeTrack model/assignment
    owners for one bounded CID-NL support universe.
    """

    if type(trajectory_policy) is not ModeTrackTrajectoryPolicy:
        raise ModeTrackProducerError("trajectory_policy must be explicit and typed")
    family_context, anchors, sample_candidates = bootstrap_cid_nl_initial_population(
        processing_owners=processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=alignment_config,
        drift_lookup=drift_lookup,
        retain_observation_only_candidates=retain_observation_only_candidates,
    )
    model_set = fit_mode_track_model_set(
        processing_owners=processing_owners,
        family_context=family_context,
        direct_support_anchors=anchors,
        policy=trajectory_policy,
    )

    batches = []
    sample_sources = []
    for sample, candidates in sample_candidates:
        trajectory_assessment = assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            (sample.metadata.raw_stem,),
        )
        batch = assign_mode_tracks(
            processing_owners=processing_owners,
            sample=sample,
            model_set=model_set,
            candidates=candidates,
            trajectory_assessment=trajectory_assessment,
        )
        validate_mode_track_assignment_batch_for_consumption(
            processing_owners=processing_owners,
            model_set=model_set,
            assignment_batch=batch,
        )
        batches.append(batch)
        sample_sources.append(
            mint_mode_track_fit_once_sample_source(
                processing_owners=processing_owners,
                sample=sample,
                candidates=candidates,
                trajectory_assessment=trajectory_assessment,
            )
        )
    batches_tuple = tuple(sorted(batches, key=lambda batch: batch.assignment_batch_id))
    source = mint_mode_track_fit_once_source(
        processing_owners=processing_owners,
        family_context=family_context,
        model_set=model_set,
        sample_sources=tuple(sample_sources),
        initial_assignment_batches=batches_tuple,
    )
    return family_context, model_set, batches_tuple, source


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
    retain_observation_only_candidates: bool = False,
) -> tuple[
    FamilyContextRef,
    tuple[ModeTrackSupportAnchor, ...],
    tuple[
        tuple[
            ResolvedCanonicalSampleIdentity, tuple[ModeTrackCandidateObservation, ...]
        ],
        ...,
    ],
]:
    """Build initial groups and their physical candidate bindings."""

    if not sample_assessments:
        raise ModeTrackProducerError("sample_assessments must not be empty")
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackProducerError("processing_owners must be owner-validated")
    if not isinstance(alignment_config, AlignmentConfig):
        raise ModeTrackProducerError("alignment_config must be explicit and typed")
    if not isinstance(retain_observation_only_candidates, bool):
        raise ModeTrackProducerError(
            "retain_observation_only_candidates must be boolean"
        )

    method_subject_ids = {
        assessment.content.method_subject.method_subject_id
        for _, assessment in sample_assessments
    }
    if len(method_subject_ids) != 1:
        raise ModeTrackProducerError(
            "all assessments must share one exact CID-NL method subject"
        )

    hypotheses_by_sample: dict[str, tuple[PeakHypothesis, ...]] = {}
    assessment_by_hypothesis: dict[str, CidNlMethodAssessmentRef] = {}
    sample_by_id: dict[str, ResolvedCanonicalSampleIdentity] = {}
    owners: list[SampleLocalMS1Owner] = []
    for sample, assessment in sample_assessments:
        sample_id = sample.identity.sample_identity_id
        hypotheses = compose_peak_hypotheses_from_assessment_universe(
            processing_owners=processing_owners,
            sample=sample,
            assessment=assessment,
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
    if not owners:
        raise ModeTrackProducerError("bootstrap assessments have no peak observations")
    provisional_groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
    )
    if not provisional_groups or any(track.review_only for track in provisional_groups):
        raise ModeTrackProducerError(
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
        raise ModeTrackProducerError(
            "every provisional owner hypothesis must own exactly one group"
        )

    direct_group_ids = {
        provisional_group_ids[hypothesis.hypothesis_id]
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
    }
    if has_direct_support and not direct_group_ids:
        raise ModeTrackProducerError(
            "no provisional group has a direct CID-NL trajectory anchor"
        )
    tracks = provisional_groups
    hypotheses = tuple(
        hypothesis
        for sample_id in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[sample_id]
    )
    method_subject = sample_assessments[0][1].content.method_subject
    mz_delta = (
        method_subject.precursor_mz
        * method_subject.precursor_tolerance_ppm
        / 1_000_000.0
    )
    family_context = mint_family_context_ref(
        mz_interval=(
            method_subject.precursor_mz - mz_delta,
            method_subject.precursor_mz + mz_delta,
        ),
        rt_interval=(
            min(hypothesis.integration.rt_left_min for hypothesis in hypotheses),
            max(hypothesis.integration.rt_right_min for hypothesis in hypotheses),
        ),
        context_policy_version=_FAMILY_CONTEXT_POLICY_VERSION,
        source_lineage_refs=tuple(
            sorted(
                {
                    *(hypothesis.hypothesis_id for hypothesis in hypotheses),
                    *(
                        assessment.artifact.content_identity.artifact_content_id
                        for _, assessment in sample_assessments
                    ),
                }
            )
        ),
        mode_tracks=tracks,
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
    return family_context, anchors, tuple(sample_candidates)


def bootstrap_mode_tracks_from_ms1_hypotheses(
    *,
    processing_owners: ProcessingOwnerReferences,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    sample_hypotheses: tuple[
        tuple[ResolvedCanonicalSampleIdentity, PeakHypothesis], ...
    ],
    family_mz_interval: tuple[float, float],
    family_rt_interval: tuple[float, float],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
) -> tuple[
    FamilyContextRef,
    ModeTrackModelSet,
    tuple[ModeTrackAssignmentBatch, ...],
    ModeTrackFitOnceSource,
]:
    """Run identity-trajectory and ModeTrack model/assignment owners from
    workflow-neutral captured MS1 hypotheses.
    """

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackProducerError("processing_owners must be owner-validated")
    if (
        type(samples) is not tuple
        or not samples
        or any(
            type(sample) is not ResolvedCanonicalSampleIdentity for sample in samples
        )
    ):
        raise ModeTrackProducerError("samples must be an exact non-empty tuple")
    if type(sample_hypotheses) is not tuple:
        raise ModeTrackProducerError("sample_hypotheses must be an exact tuple")
    if type(alignment_config) is not AlignmentConfig:
        raise ModeTrackProducerError("alignment_config must be explicit and typed")
    if type(trajectory_policy) is not ModeTrackTrajectoryPolicy:
        raise ModeTrackProducerError("trajectory_policy must be explicit and typed")
    sample_by_id = {sample.identity.sample_identity_id: sample for sample in samples}
    hypotheses_by_sample: dict[str, list[PeakHypothesis]] = {
        sample_id: [] for sample_id in sample_by_id
    }
    owners: list[SampleLocalMS1Owner] = []
    seen_hypothesis_ids: set[str] = set()
    for sample, hypothesis in sample_hypotheses:
        sample_id = sample.identity.sample_identity_id
        if sample_by_id.get(sample_id) is not sample:
            raise ModeTrackProducerError(
                "MS1 hypothesis sample is outside the exact scope population"
            )
        if hypothesis.hypothesis_id in seen_hypothesis_ids:
            raise ModeTrackProducerError("MS1 bootstrap duplicated a hypothesis")
        seen_hypothesis_ids.add(hypothesis.hypothesis_id)
        hypotheses_by_sample[sample_id].append(hypothesis)
        owners.append(
            owner_from_ms1_observation_hypothesis(
                processing_owners=processing_owners,
                sample=sample,
                hypothesis=hypothesis,
            )
        )

    provisional_groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        drift_lookup=drift_lookup,
    )
    if any(group.review_only for group in provisional_groups):
        raise ModeTrackProducerError(
            "MS1 bootstrap observations did not produce consumable groups"
        )
    family_context = mint_family_context_ref(
        mz_interval=family_mz_interval,
        rt_interval=family_rt_interval,
        context_policy_version=_MS1_FAMILY_CONTEXT_POLICY_VERSION,
        source_lineage_refs=(
            tuple(sorted(seen_hypothesis_ids))
            or (processing_owners.request.processing_request_id,)
        ),
        mode_tracks=provisional_groups,
    )
    model_set = fit_mode_track_model_set(
        processing_owners=processing_owners,
        family_context=family_context,
        direct_support_anchors=(),
        policy=trajectory_policy,
    )

    batches = []
    sample_sources = []
    for sample_id in sorted(sample_by_id):
        sample = sample_by_id[sample_id]
        candidates = tuple(
            mint_mode_track_candidate_observation(
                processing_owners=processing_owners,
                sample=sample,
                peak_hypothesis=hypothesis,
            )
            for hypothesis in sorted(
                hypotheses_by_sample[sample_id],
                key=lambda item: item.hypothesis_id,
            )
        )
        trajectory_assessment = assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            (sample.metadata.raw_stem,),
        )
        batch = assign_mode_tracks(
            processing_owners=processing_owners,
            sample=sample,
            model_set=model_set,
            candidates=candidates,
            trajectory_assessment=trajectory_assessment,
        )
        validate_mode_track_assignment_batch_for_consumption(
            processing_owners=processing_owners,
            model_set=model_set,
            assignment_batch=batch,
        )
        batches.append(batch)
        sample_sources.append(
            mint_mode_track_fit_once_sample_source(
                processing_owners=processing_owners,
                sample=sample,
                candidates=candidates,
                trajectory_assessment=trajectory_assessment,
            )
        )
    batches_tuple = tuple(sorted(batches, key=lambda batch: batch.assignment_batch_id))
    source = mint_mode_track_fit_once_source(
        processing_owners=processing_owners,
        family_context=family_context,
        model_set=model_set,
        sample_sources=tuple(sample_sources),
        initial_assignment_batches=batches_tuple,
    )
    return family_context, model_set, batches_tuple, source


__all__ = [
    "ModeTrackProducerError",
    "bootstrap_mode_tracks_from_cid_nl_support",
    "bootstrap_mode_tracks_from_ms1_hypotheses",
]
