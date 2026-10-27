"""Executable no-write CID-NL assessment → group → cell reference spine."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cell_observation.observations import (
    CellObservationContractError,
    CellObservationRef,
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportContractError,
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    supported_cid_nl_peak_ids,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

VALIDATION_STATUS = "diagnostic_only"


class CidNlEvidenceSpineError(ValueError):
    """Raised when the bounded CellObservationRef reference spine cannot close
    exactly.
    """


@dataclass(frozen=True, slots=True)
class TargetedCellObservationReferenceProjection:
    """Internal Targeted view of an already-minted cell; never a writer DTO."""

    cell: CellObservationRef
    group: CrossSamplePeakGroupHypothesis
    validation_status: str = VALIDATION_STATUS


@dataclass(frozen=True, slots=True)
class UntargetedCellObservationReferenceProjection:
    """Internal Untargeted view of the same cell; never a Matrix DTO."""

    cell: CellObservationRef
    group: CrossSamplePeakGroupHypothesis
    validation_status: str = VALIDATION_STATUS


@dataclass(frozen=True, slots=True)
class CidNlReferenceSpine:
    """One constructed owner spine and its two exact-reference fan-outs."""

    assessments: tuple[CidNlMethodAssessmentRef, ...]
    support_hypotheses: tuple[PeakHypothesis, ...]
    group: CrossSamplePeakGroupHypothesis | None
    cells: tuple[CellObservationRef, ...]
    targeted_projections: tuple[TargetedCellObservationReferenceProjection, ...]
    untargeted_projections: tuple[
        UntargetedCellObservationReferenceProjection,
        ...,
    ]


def build_cid_nl_reference_spine(
    *,
    processing_owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition | None = None,
    method_subject: CidNlMethodSubject | None = None,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
        ...,
    ],
    config: AlignmentConfig | None = None,
) -> CidNlReferenceSpine:
    """Build the CellObservationRef reference spine once.

    Expose two no-write views over the same cell closure.
    """

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlEvidenceSpineError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if (target is None) == (method_subject is None):
        raise CidNlEvidenceSpineError(
            "reference spine requires exactly one Targeted adapter or method_subject"
        )
    subject = (
        cid_nl_method_subject_from_target(target)
        if target is not None
        else method_subject
    )
    assert subject is not None
    if not isinstance(sample_assessments, tuple) or not sample_assessments:
        raise CidNlEvidenceSpineError(
            "sample_assessments must be a non-empty immutable tuple"
        )
    if config is not None and not isinstance(config, AlignmentConfig):
        raise CidNlEvidenceSpineError("config must be AlignmentConfig")
    ordered = tuple(
        sorted(sample_assessments, key=lambda item: item[0].identity.sample_key)
    )
    support_inputs: list[
        tuple[
            ResolvedCanonicalSampleIdentity,
            CidNlMethodAssessmentRef,
            PeakObservationRef,
            PeakHypothesis,
        ]
    ] = []
    for sample, assessment in ordered:
        if assessment.content.method_result != "SUPPORT":
            continue
        selected_peak_ids = supported_cid_nl_peak_ids(assessment)
        if len(selected_peak_ids) != 1:
            raise CidNlEvidenceSpineError(
                "reference-spine SUPPORT must identify exactly one physical peak"
            )
        selected_peak_id = next(iter(selected_peak_ids))
        peak = next(
            candidate
            for candidate in assessment.candidate_peaks
            if candidate.content.peak_observation_content_id == selected_peak_id
        )
        try:
            chromatographic_support = compose_cid_nl_chromatographic_support_ref(
                processing_owners=processing_owners,
                sample=sample,
                scan_support_refs=tuple(
                    ref
                    for ref in assessment.individual_support_refs
                    if ref.content.peak_observation_content_id == selected_peak_id
                ),
                evidence_refs=tuple(
                    ref
                    for ref in assessment.evidence_refs
                    if ref.artifact.content_identity.artifact_content_id
                    in {
                        support.content.cid_nl_evidence_content_id
                        for support in assessment.individual_support_refs
                        if support.content.peak_observation_content_id
                        == selected_peak_id
                    }
                )
                if assessment.content.method_subject.composition
                else (),
            )
            hypothesis = compose_cid_nl_peak_hypothesis(
                processing_owners=processing_owners,
                sample=sample,
                peak_observation=peak,
                chromatographic_support=chromatographic_support,
            )
        except (
            CidNlChromatographicSupportContractError,
            PeakHypothesisContractError,
        ) as exc:
            raise CidNlEvidenceSpineError(str(exc)) from exc
        support_inputs.append((sample, assessment, peak, hypothesis))

    assessments = tuple(assessment for _, assessment in ordered)
    if not support_inputs:
        return CidNlReferenceSpine(
            assessments=assessments,
            support_hypotheses=(),
            group=None,
            cells=(),
            targeted_projections=(),
            untargeted_projections=(),
        )

    try:
        owners = tuple(
            owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
            for sample, assessment, _, hypothesis in support_inputs
        )
    except HypothesisOwnerAdapterError as exc:
        raise CidNlEvidenceSpineError(str(exc)) from exc
    effective_config = config or AlignmentConfig()
    rt_frame = SampleRtFrame()
    groups = construct_cross_sample_peak_group_hypotheses(
        owners, config=effective_config, rt_frame=rt_frame, rules=CID_NL_GROUPING_RULES
    )
    if len(groups) != 1:
        raise CidNlEvidenceSpineError(
            "CellObservationRef reference spine cannot resolve multiple provisional "
            "groups; identity resolution belongs to the identity-trajectory owner"
        )
    group = groups[0]
    try:
        group_context = CidNlCellGroupContext(
            group=group,
            supports=tuple(
                CidNlCellGroupSupport(
                    sample=sample,
                    assessment=assessment,
                    hypothesis=hypothesis,
                )
                for sample, assessment, _, hypothesis in support_inputs
            ),
            config=effective_config,
            rt_frame=rt_frame,
            processing_attempt=processing_owners.attempt,
        )
    except CellObservationContractError as exc:
        raise CidNlEvidenceSpineError(str(exc)) from exc
    hypothesis_by_sample_id = {
        sample.identity.sample_key: (peak, hypothesis)
        for sample, _, peak, hypothesis in support_inputs
    }
    cells: list[CellObservationRef] = []
    for sample, assessment in ordered:
        support_pair = hypothesis_by_sample_id.get(sample.identity.sample_key)
        peak_observation = None
        peak_hypothesis = None
        if support_pair is not None:
            peak_observation, peak_hypothesis = support_pair
        elif (
            assessment.content.method_result == "HOLD"
            and len(assessment.candidate_peaks) == 1
        ):
            peak_observation = assessment.candidate_peaks[0]
        try:
            cell = mint_cell_observation_ref(
                processing_owners=processing_owners,
                sample=sample,
                target=target,
                group_context=group_context,
                method_assessment=assessment,
                peak_observation=peak_observation,
                peak_hypothesis=peak_hypothesis,
            )
        except CellObservationContractError as exc:
            raise CidNlEvidenceSpineError(str(exc)) from exc
        cells.append(cell)
    cell_tuple = tuple(cells)
    targeted = tuple(
        TargetedCellObservationReferenceProjection(cell=cell, group=group)
        for cell in cell_tuple
    )
    untargeted = tuple(
        UntargetedCellObservationReferenceProjection(cell=cell, group=group)
        for cell in cell_tuple
    )
    return CidNlReferenceSpine(
        assessments=assessments,
        support_hypotheses=tuple(hypothesis for _, _, _, hypothesis in support_inputs),
        group=group,
        cells=cell_tuple,
        targeted_projections=targeted,
        untargeted_projections=untargeted,
    )


__all__ = [
    "CidNlEvidenceSpineError",
    "CidNlReferenceSpine",
    "TargetedCellObservationReferenceProjection",
    "UntargetedCellObservationReferenceProjection",
    "build_cid_nl_reference_spine",
]
