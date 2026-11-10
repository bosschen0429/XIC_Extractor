"""Executable no-write CID-NL assessment → group → cell reference spine."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cell_observation.observations import (
    CellObservation,
    CellObservationContractError,
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
    mint_cell_observation,
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
    compose_cid_nl_chromatographic_support,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    supported_cid_nl_peaks,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.sample_metadata import SampleMetadata


class CidNlEvidenceSpineError(ValueError):
    """Raised when the bounded CellObservation reference spine cannot close
    exactly.
    """


@dataclass(frozen=True, slots=True)
class TargetedCellObservationProjection:
    """Internal Targeted view of an already-minted cell; never a writer DTO."""

    cell: CellObservation
    group: CrossSamplePeakGroupHypothesis


@dataclass(frozen=True, slots=True)
class UntargetedCellObservationProjection:
    """Internal Untargeted view of the same cell; never a Matrix DTO."""

    cell: CellObservation
    group: CrossSamplePeakGroupHypothesis


@dataclass(frozen=True, slots=True)
class CidNlReferenceSpine:
    """One constructed owner spine and its two exact-reference fan-outs."""

    assessments: tuple[CidNlMethodAssessment, ...]
    support_hypotheses: tuple[PeakHypothesis, ...]
    group: CrossSamplePeakGroupHypothesis | None
    cells: tuple[CellObservation, ...]
    targeted_projections: tuple[TargetedCellObservationProjection, ...]
    untargeted_projections: tuple[
        UntargetedCellObservationProjection,
        ...,
    ]


def build_cid_nl_reference_spine(
    *,
    target: ResolvedTargetDefinition | None = None,
    method_subject: CidNlMethodSubject | None = None,
    sample_assessments: tuple[
        tuple[SampleMetadata, CidNlMethodAssessment],
        ...,
    ],
    config: AlignmentConfig | None = None,
) -> CidNlReferenceSpine:
    """Build the CellObservation reference spine once.

    Expose two no-write views over the same cell closure.
    """

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
    ordered = tuple(sorted(sample_assessments, key=lambda item: item[0].sample_name))
    support_inputs: list[
        tuple[
            SampleMetadata,
            CidNlMethodAssessment,
            PeakObservation,
            PeakHypothesis,
        ]
    ] = []
    for sample, assessment in ordered:
        if assessment.method_result != "SUPPORT":
            continue
        selected_peaks = supported_cid_nl_peaks(assessment)
        if len(selected_peaks) != 1:
            raise CidNlEvidenceSpineError(
                "reference-spine SUPPORT must identify exactly one physical peak"
            )
        selected_peak = next(iter(selected_peaks))
        peak = next(
            candidate
            for candidate in assessment.candidate_peaks
            if candidate == selected_peak
        )
        try:
            chromatographic_support = compose_cid_nl_chromatographic_support(
                sample=sample,
                scan_support_refs=tuple(
                    ref
                    for ref in assessment.individual_support_refs
                    if ref.peak_observation == peak
                ),
                evidence_refs=tuple(
                    ref
                    for ref in assessment.evidence_observations
                    if ref
                    in {
                        support.evidence
                        for support in assessment.individual_support_refs
                        if support.peak_observation == peak
                    }
                )
                if assessment.method_subject.composition
                else (),
            )
            hypothesis = compose_cid_nl_peak_hypothesis(
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
            "CellObservation reference spine cannot resolve multiple provisional "
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
        )
    except CellObservationContractError as exc:
        raise CidNlEvidenceSpineError(str(exc)) from exc
    hypothesis_by_sample_id = {
        sample.sample_name: (peak, hypothesis)
        for sample, _, peak, hypothesis in support_inputs
    }
    cells: list[CellObservation] = []
    for sample, assessment in ordered:
        support_pair = hypothesis_by_sample_id.get(sample.sample_name)
        peak_observation = None
        peak_hypothesis = None
        if support_pair is not None:
            peak_observation, peak_hypothesis = support_pair
        elif (
            assessment.method_result == "HOLD" and len(assessment.candidate_peaks) == 1
        ):
            peak_observation = assessment.candidate_peaks[0]
        try:
            cell = mint_cell_observation(
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
        TargetedCellObservationProjection(cell=cell, group=group) for cell in cell_tuple
    )
    untargeted = tuple(
        UntargetedCellObservationProjection(cell=cell, group=group)
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
    "TargetedCellObservationProjection",
    "UntargetedCellObservationProjection",
    "build_cid_nl_reference_spine",
]
