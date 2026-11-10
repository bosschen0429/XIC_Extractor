"""Maintain method feature envelopes and assess an owner against their witnesses."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence, evaluate_owner_edge
from xic_extractor.alignment.grouping_evidence import (
    envelope_fraction,
    peak_correspondence_conflict,
)
from xic_extractor.alignment.grouping_rules import GroupingRules, MethodFeatureEnvelope
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import RtFrame

from .models import DecisionDirection
from .sample_assignment import _Candidate


@dataclass(slots=True)
class _FeatureTrack:
    key: str
    seed: SampleLocalMS1Owner
    last: SampleLocalMS1Owner
    sample_stems: set[str]
    precursor_min: float
    precursor_max: float
    method: MethodFeatureEnvelope
    members: list[SampleLocalMS1Owner]

    @classmethod
    def from_owner(
        cls,
        owner: SampleLocalMS1Owner,
        *,
        rules: GroupingRules,
        direction: DecisionDirection,
    ) -> _FeatureTrack:
        return cls(
            key=f"{direction}:{owner.owner_id}",
            seed=owner,
            last=owner,
            sample_stems={owner.sample_stem},
            precursor_min=owner.peak.mz,
            precursor_max=owner.peak.mz,
            method=rules.feature_envelope(owner),
            members=[owner],
        )

    def add(self, owner: SampleLocalMS1Owner) -> None:
        self.method.add(owner)
        self.members.append(owner)
        self.sample_stems.add(owner.sample_stem)
        self.precursor_min = min(self.precursor_min, owner.peak.mz)
        self.precursor_max = max(self.precursor_max, owner.peak.mz)
        self.last = owner

    @property
    def witness_owners(self) -> tuple[SampleLocalMS1Owner, ...]:
        if self.seed.owner_id == self.last.owner_id:
            return (self.last,)
        return (self.last, self.seed)


def _feature_rt_may_support(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    config: AlignmentConfig,
    rt_frame: RtFrame,
) -> bool:
    """Exclude only tracks whose seed AND last cannot support either RT basis."""
    for witness in track.witness_owners:
        raw_delta = (
            abs(
                owner.peak.integration.rt_apex_min
                - witness.peak.integration.rt_apex_min
            )
            * 60.0
        )
        if not math.isfinite(raw_delta) or raw_delta <= config.preferred_rt_sec:
            return True
        if rt_frame.has_drift:
            corrected = rt_frame.corrected_pair_delta_sec(owner, witness)
            if (
                corrected is None
                or not math.isfinite(corrected)
                or corrected <= config.preferred_rt_sec
            ):
                return True
    return False


def _compare_owner_to_feature(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
) -> _Candidate:
    counts["feature_summary_comparisons"] += 1
    hard_reason, mass_fraction = _feature_hard_reason(
        owner,
        track,
        config,
        rt_frame=rt_frame,
    )
    witnesses = track.witness_owners
    witness_ids = tuple(witness.owner_id for witness in witnesses)
    if hard_reason:
        return _Candidate(
            track.key, None, "proven_incompatible", hard_reason, witness_ids
        )

    evidence: list[OwnerEdgeEvidence] = []
    for witness in witnesses:
        left, right = sorted((owner, witness), key=lambda item: item.owner_id)
        evidence.append(
            evaluate_owner_edge(
                left,
                right,
                config=config,
                rt_frame=rt_frame,
                method_evidence=rules.pair_evidence(left, right, config),
            )
        )
        counts["owner_edge_evaluations"] += 1
    supported = tuple(
        item
        for item in evidence
        if item.decision == "strong_edge"
        or (
            item.decision == "weak_edge"
            and not item.failure_reason
            and item.rt_basis == "raw"
            and item.rt_raw_delta_sec <= config.preferred_rt_sec
            and item.owner_quality == "clean"
            and item.seed_support_level == "moderate"
        )
    )
    if not supported:
        reason = ";".join(sorted({item.reason for item in evidence}))
        return _Candidate(
            track.key,
            None,
            "evaluated_undecided",
            reason or "feature_evidence_not_decisive",
            witness_ids,
        )
    best = min(
        supported,
        key=lambda item: (
            _edge_cost(item, mass_fraction, config),
            item.left_owner_id,
            item.right_owner_id,
        ),
    )
    cost = _edge_cost(best, mass_fraction, config)
    selected_witness_owner_id = (
        best.right_owner_id
        if best.left_owner_id == owner.owner_id
        else best.left_owner_id
    )
    return _Candidate(
        track.key,
        cost,
        "supported",
        best.reason,
        witness_ids,
        selected_witness_owner_id=selected_witness_owner_id,
        rt_basis=best.rt_basis,
        rt_raw_delta_sec=best.rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=best.rt_drift_corrected_delta_sec,
    )


def _feature_hard_reason(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    config: AlignmentConfig,
    *,
    rt_frame: RtFrame | None = None,
) -> tuple[str, float]:
    if owner.sample_stem in track.sample_stems:
        return "same_sample", math.inf
    identity_failure, measurement_failure = track.method.failures(owner, config)
    if identity_failure:
        return identity_failure, math.inf
    mass_fraction = envelope_fraction(
        track.precursor_min, track.precursor_max, owner.peak.mz, config.max_ppm
    )
    if mass_fraction > 1.0:
        return "precursor_envelope", mass_fraction
    if peak_correspondence_conflict(
        (owner,),
        track.members,
        config,
        rt_frame=rt_frame,
    ):
        return "peak_correspondence_mismatch", mass_fraction
    return measurement_failure, mass_fraction


def _edge_cost(
    edge: OwnerEdgeEvidence,
    mass_fraction: float,
    config: AlignmentConfig,
) -> float:
    rt_delta = (
        edge.rt_drift_corrected_delta_sec
        if edge.rt_basis == "drift_corrected"
        and edge.rt_drift_corrected_delta_sec is not None
        else edge.rt_raw_delta_sec
    )
    rt_fraction = min(rt_delta / config.preferred_rt_sec, 1.0)
    score_penalty = max(0.0, 100.0 - edge.score) / 100.0
    return min(
        0.999999, 0.70 * rt_fraction + 0.20 * mass_fraction + 0.10 * score_penalty
    )
