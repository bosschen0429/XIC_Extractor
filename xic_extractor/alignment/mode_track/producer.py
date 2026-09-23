"""Compose CID-NL direct support into sibling ModeTracks and assignments."""

from __future__ import annotations

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackAssignmentBatch,
    ModeTrackFitOnceSource,
    ModeTrackModelSet,
    ModeTrackTrajectoryPolicy,
    assign_mode_tracks,
    fit_mode_track_model_set,
    mint_mode_track_candidate_observation,
    mint_mode_track_fit_once_sample_source,
    mint_mode_track_fit_once_source,
    validate_mode_track_assignment_batch_for_consumption,
)
from xic_extractor.alignment.mode_track.assignment_scope import (
    ModeTrackAssignmentScope,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


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
    ModeTrackAssignmentScope,
    ModeTrackModelSet,
    tuple[ModeTrackAssignmentBatch, ...],
    ModeTrackFitOnceSource,
]:
    """Run the existing identity-trajectory and ModeTrack model/assignment
    owners for one bounded CID-NL support universe.
    """

    if type(trajectory_policy) is not ModeTrackTrajectoryPolicy:
        raise ModeTrackProducerError("trajectory_policy must be explicit and typed")
    from xic_extractor.alignment.grouping_bootstrap import (
        GroupingBootstrapError,
        bootstrap_cid_nl_initial_population,
    )

    try:
        assignment_scope, anchors, sample_candidates, _ = (
            bootstrap_cid_nl_initial_population(
                processing_owners=processing_owners,
                sample_assessments=sample_assessments,
                alignment_config=alignment_config,
                drift_lookup=drift_lookup,
                retain_observation_only_candidates=retain_observation_only_candidates,
            )
        )
    except GroupingBootstrapError as exc:
        raise ModeTrackProducerError(str(exc)) from exc
    model_set = fit_mode_track_model_set(
        processing_owners=processing_owners,
        assignment_scope=assignment_scope,
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
        model_set=model_set,
        sample_sources=tuple(sample_sources),
        initial_assignment_batches=batches_tuple,
    )
    return assignment_scope, model_set, batches_tuple, source


def bootstrap_mode_tracks_from_ms1_hypotheses(
    *,
    processing_owners: ProcessingOwnerReferences,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    sample_hypotheses: tuple[
        tuple[ResolvedCanonicalSampleIdentity, PeakHypothesis], ...
    ],
    scope_mz_interval: tuple[float, float],
    scope_rt_interval: tuple[float, float],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
) -> tuple[
    ModeTrackAssignmentScope,
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
    assignment_scope = ModeTrackAssignmentScope(
        mz_interval=scope_mz_interval,
        rt_interval=scope_rt_interval,
        groups=provisional_groups,
    )
    model_set = fit_mode_track_model_set(
        processing_owners=processing_owners,
        assignment_scope=assignment_scope,
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
        model_set=model_set,
        sample_sources=tuple(sample_sources),
        initial_assignment_batches=batches_tuple,
    )
    return assignment_scope, model_set, batches_tuple, source


__all__ = [
    "ModeTrackProducerError",
    "bootstrap_mode_tracks_from_cid_nl_support",
    "bootstrap_mode_tracks_from_ms1_hypotheses",
]
