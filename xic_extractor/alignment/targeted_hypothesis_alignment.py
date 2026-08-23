"""Targeted diagnostic selection adapter into existing cross-sample Alignment."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
    group_member_subject_from_peak_hypothesis,
)
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.discovery.targeted_candidate_set import TargetedCandidateSetRef
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.selection_decision import (
    DiagnosticPeakHypothesisSelectionError,
    DiagnosticPeakHypothesisSelectionRef,
    resolve_diagnostic_peak_hypothesis_selection,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class TargetedHypothesisAlignmentError(ValueError):
    """Raised when selected hypothesis identity cannot enter Alignment exactly."""


@dataclass(frozen=True, slots=True)
class TargetedHypothesisAlignmentInput:
    """One sample-target selection plus its complete diagnostic candidate set."""

    sample: ResolvedCanonicalSampleIdentity
    target: ResolvedTargetDefinition
    candidate_set: TargetedCandidateSetRef
    selection: DiagnosticPeakHypothesisSelectionRef

    def __post_init__(self) -> None:
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise TargetedHypothesisAlignmentError(
                "sample must be an owner-resolved canonical identity"
            )
        if not isinstance(self.target, ResolvedTargetDefinition):
            raise TargetedHypothesisAlignmentError(
                "target must be an owner-resolved target definition"
            )
        if not isinstance(self.selection, DiagnosticPeakHypothesisSelectionRef):
            raise TargetedHypothesisAlignmentError(
                "selection must be an immutable diagnostic selection ref"
            )
        if not isinstance(self.candidate_set, TargetedCandidateSetRef):
            raise TargetedHypothesisAlignmentError(
                "candidate_set must be an immutable Discovery receipt"
            )
        if (
            self.selection.canonical_sample_identity_id
            != self.sample.identity.sample_identity_id
            or self.candidate_set.canonical_sample_identity_id
            != self.sample.identity.sample_identity_id
        ):
            raise TargetedHypothesisAlignmentError(
                "selection references a different canonical sample"
            )
        if (
            self.selection.target_snapshot_id != self.target.snapshot.snapshot_id
            or self.candidate_set.target_snapshot_id != self.target.snapshot.snapshot_id
            or self.selection.target_key_id
            != self.target.definition.target_key.target_key_id
            or self.candidate_set.target_key_id
            != self.target.definition.target_key.target_key_id
            or self.selection.target_definition_id
            != self.target.definition.target_definition_id
            or self.candidate_set.target_definition_id
            != self.target.definition.target_definition_id
        ):
            raise TargetedHypothesisAlignmentError(
                "selection references a different target definition"
            )
        try:
            resolve_diagnostic_peak_hypothesis_selection(
                self.selection,
                self.candidate_set,
            )
        except DiagnosticPeakHypothesisSelectionError as exc:
            raise TargetedHypothesisAlignmentError(str(exc)) from exc


def align_selected_targeted_hypotheses(
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    *,
    target: ResolvedTargetDefinition,
    config: AlignmentConfig,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Group selected hypotheses while preserving each original hypothesis ID."""

    if not isinstance(inputs, tuple):
        raise TargetedHypothesisAlignmentError("inputs must be an immutable tuple")
    if not isinstance(target, ResolvedTargetDefinition):
        raise TargetedHypothesisAlignmentError(
            "target must be an owner-resolved target definition"
        )
    if not isinstance(config, AlignmentConfig):
        raise TargetedHypothesisAlignmentError("config must be AlignmentConfig")
    sample_ids: set[str] = set()
    owners: list[SampleLocalMS1Owner] = []
    selected_ids: set[str] = set()
    for item in inputs:
        if not isinstance(item, TargetedHypothesisAlignmentInput):
            raise TargetedHypothesisAlignmentError(
                "inputs must contain TargetedHypothesisAlignmentInput values"
            )
        if (
            item.target.definition.target_definition_id
            != (target.definition.target_definition_id)
            or item.target.definition.target_key.target_key_id
            != (target.definition.target_key.target_key_id)
            or item.target.snapshot.snapshot_id != target.snapshot.snapshot_id
        ):
            raise TargetedHypothesisAlignmentError(
                "all Alignment inputs must use the exact requested target definition"
            )
        sample_id = item.sample.identity.sample_identity_id
        if sample_id in sample_ids:
            raise TargetedHypothesisAlignmentError(
                "Alignment inputs contain a duplicate sample-target cell"
            )
        sample_ids.add(sample_id)
        try:
            selected = resolve_diagnostic_peak_hypothesis_selection(
                item.selection,
                item.candidate_set,
            )
        except DiagnosticPeakHypothesisSelectionError as exc:
            raise TargetedHypothesisAlignmentError(str(exc)) from exc
        if selected is None:
            continue
        if selected.hypothesis_id in selected_ids:
            raise TargetedHypothesisAlignmentError(
                "selected hypothesis identity cannot appear in multiple cells"
            )
        selected_ids.add(selected.hypothesis_id)
        owners.append(_owner_from_selected_hypothesis(item, selected))

    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=config,
    )
    grouped_ids = tuple(owner_id for group in groups for owner_id in group.owner_ids)
    if tuple(sorted(grouped_ids)) != tuple(sorted(selected_ids)):
        raise TargetedHypothesisAlignmentError(
            "Alignment output lost or invented selected hypothesis identity"
        )
    return groups


def _owner_from_selected_hypothesis(
    item: TargetedHypothesisAlignmentInput,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    observation = hypothesis.peak_observation_refs[0]
    support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    if support is None:
        raise TargetedHypothesisAlignmentError(
            "selected CID-NL hypothesis is missing chromatographic support"
        )
    integration = observation.content.integration
    target_value = item.target.definition.target
    neutral_loss = target_value.neutral_loss_da
    if neutral_loss is None:
        raise TargetedHypothesisAlignmentError(
            "Targeted CID-NL Alignment requires configured neutral loss"
        )
    event = IdentityEvent(
        candidate_id=hypothesis.hypothesis_id,
        sample_stem=item.sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        neutral_loss_tag=f"cid_nl:{neutral_loss:.12g}",
        precursor_mz=observation.content.mz,
        product_mz=observation.content.mz - neutral_loss,
        observed_neutral_loss_da=neutral_loss,
        seed_rt=integration.rt_apex_min,
        evidence_score=100,
        seed_event_count=support.content.strict_nl_scan_count,
    )
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=item.sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        precursor_mz=observation.content.mz,
        owner_apex_rt=integration.rt_apex_min,
        owner_peak_start_rt=integration.rt_left_min,
        owner_peak_end_rt=integration.rt_right_min,
        owner_area=integration.area_raw_counts_seconds,
        owner_height=integration.height_raw,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="diagnostic_unique_hypothesis_selection",
        selected_integration=integration,
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_request_id=item.candidate_set.processing_request_id,
            sample=item.sample,
            hypothesis=hypothesis,
        ),
    )
