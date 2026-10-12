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
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportContractError,
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentContractError,
    CidNlMethodAssessmentRef,
    supported_cid_nl_peak_ids,
    validate_cid_nl_method_assessment_binding,
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
PRODUCT_AUTHORITY = "none"


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
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        _validate_projection(self.cell, self.group)
        if self.validation_status != VALIDATION_STATUS:
            raise CidNlEvidenceSpineError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise CidNlEvidenceSpineError(
                "diagnostic reference projection cannot grant product authority"
            )


@dataclass(frozen=True, slots=True)
class UntargetedCellObservationReferenceProjection:
    """Internal Untargeted view of the same cell; never a Matrix DTO."""

    cell: CellObservationRef
    group: CrossSamplePeakGroupHypothesis
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        _validate_projection(self.cell, self.group)
        if self.validation_status != VALIDATION_STATUS:
            raise CidNlEvidenceSpineError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise CidNlEvidenceSpineError(
                "diagnostic reference projection cannot grant product authority"
            )


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

    def __post_init__(self) -> None:
        if (
            not isinstance(self.assessments, tuple)
            or not self.assessments
            or any(
                not isinstance(item, CidNlMethodAssessmentRef)
                for item in self.assessments
            )
        ):
            raise CidNlEvidenceSpineError(
                "reference spine requires owner-backed assessments"
            )
        assessment_sample_ids = tuple(
            assessment.content.canonical_sample_identity_id
            for assessment in self.assessments
        )
        if assessment_sample_ids != tuple(sorted(set(assessment_sample_ids))):
            raise CidNlEvidenceSpineError(
                "reference spine assessments must be unique and canonical-sorted"
            )
        if not isinstance(self.support_hypotheses, tuple) or any(
            not isinstance(item, PeakHypothesis) or not item.is_owner_backed
            for item in self.support_hypotheses
        ):
            raise CidNlEvidenceSpineError(
                "reference spine support hypotheses must be owner-backed"
            )
        hypothesis_ids = tuple(
            hypothesis.hypothesis_id for hypothesis in self.support_hypotheses
        )
        if len(set(hypothesis_ids)) != len(hypothesis_ids):
            raise CidNlEvidenceSpineError(
                "reference spine support hypotheses must be unique"
            )
        if self.group is None:
            if (
                self.support_hypotheses
                or self.cells
                or self.targeted_projections
                or self.untargeted_projections
            ):
                raise CidNlEvidenceSpineError(
                    "group-less assessments cannot invent hypotheses, cells, or "
                    "projections"
                )
            return
        if not isinstance(self.group, CrossSamplePeakGroupHypothesis):
            raise CidNlEvidenceSpineError(
                "reference spine group must be an existing provisional group"
            )
        if set(self.group.owner_ids) != set(hypothesis_ids):
            raise CidNlEvidenceSpineError(
                "reference spine group and support hypotheses do not close"
            )
        canonical_group_owner_ids = tuple(sorted(self.group.owner_ids))
        if (
            not isinstance(self.cells, tuple)
            or any(not isinstance(item, CellObservationRef) for item in self.cells)
            or len(self.cells) != len(self.assessments)
        ):
            raise CidNlEvidenceSpineError(
                "reference spine requires one cell per assessment"
            )
        for assessment, cell in zip(self.assessments, self.cells, strict=True):
            if cell.method_assessment is not assessment:
                raise CidNlEvidenceSpineError(
                    "reference spine cell remints or crosses its assessment"
                )
            if cell.content.group_hypothesis_id != self.group.group_hypothesis_id:
                raise CidNlEvidenceSpineError(
                    "reference spine cell belongs to a different group"
                )
            if cell.content.group_owner_ids != canonical_group_owner_ids:
                raise CidNlEvidenceSpineError(
                    "reference spine cell group membership was substituted"
                )
            if cell.group_context.group is not self.group:
                raise CidNlEvidenceSpineError(
                    "reference spine cell does not reuse the exact group context"
                )
        cell_hypotheses = tuple(
            cell.peak_hypothesis
            for cell in self.cells
            if cell.peak_hypothesis is not None
        )
        if len(cell_hypotheses) != len(self.support_hypotheses) or any(
            cell_hypothesis is not support_hypothesis
            for cell_hypothesis, support_hypothesis in zip(
                cell_hypotheses,
                self.support_hypotheses,
                strict=True,
            )
        ):
            raise CidNlEvidenceSpineError(
                "reference spine cells and support hypotheses do not close"
            )
        if len(self.cells) != len(self.targeted_projections) or len(self.cells) != len(
            self.untargeted_projections
        ):
            raise CidNlEvidenceSpineError(
                "dual reference projections must cover each cell exactly once"
            )
        for cell, targeted, untargeted in zip(
            self.cells,
            self.targeted_projections,
            self.untargeted_projections,
            strict=True,
        ):
            if targeted.cell is not cell or untargeted.cell is not cell:
                raise CidNlEvidenceSpineError(
                    "dual projections must fan out the same exact cell reference"
                )
            if targeted.group is not self.group or untargeted.group is not self.group:
                raise CidNlEvidenceSpineError(
                    "dual projections must fan out the same exact group reference"
                )


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
    ordered = _validate_sample_assessments(
        processing_owners=processing_owners,
        target=target,
        method_subject=subject,
        sample_assessments=sample_assessments,
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
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=effective_config,
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
            processing_attempt=processing_owners.attempt,
        )
    except CellObservationContractError as exc:
        raise CidNlEvidenceSpineError(str(exc)) from exc
    hypothesis_by_sample_id = {
        sample.identity.sample_identity_id: (peak, hypothesis)
        for sample, _, peak, hypothesis in support_inputs
    }
    cells: list[CellObservationRef] = []
    for sample, assessment in ordered:
        support_pair = hypothesis_by_sample_id.get(sample.identity.sample_identity_id)
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


def _validate_sample_assessments(
    *,
    processing_owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition | None,
    method_subject: CidNlMethodSubject,
    sample_assessments: object,
) -> tuple[tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...]:
    if not isinstance(sample_assessments, tuple):
        raise CidNlEvidenceSpineError("sample_assessments must be a tuple")
    typed: list[tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef]] = []
    sample_ids: set[str] = set()
    for item in sample_assessments:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], ResolvedCanonicalSampleIdentity)
            or not isinstance(item[1], CidNlMethodAssessmentRef)
        ):
            raise CidNlEvidenceSpineError(
                "sample_assessments must contain exact sample/assessment pairs"
            )
        sample, assessment = item
        sample_id = sample.identity.sample_identity_id
        if sample_id in sample_ids:
            raise CidNlEvidenceSpineError(
                "sample_assessments contains a duplicate sample cell"
            )
        sample_ids.add(sample_id)
        try:
            if target is None:
                validate_cid_nl_method_assessment_binding(
                    assessment,
                    processing_owners=processing_owners,
                    sample=sample,
                    method_subject=method_subject,
                )
            else:
                validate_cid_nl_method_assessment_binding(
                    assessment,
                    processing_owners=processing_owners,
                    sample=sample,
                    target=target,
                )
        except CidNlMethodAssessmentContractError as exc:
            raise CidNlEvidenceSpineError(str(exc)) from exc
        typed.append((sample, assessment))
    return tuple(sorted(typed, key=lambda item: item[0].identity.sample_identity_id))


def _validate_projection(
    cell: object,
    group: object,
) -> None:
    if not isinstance(cell, CellObservationRef):
        raise CidNlEvidenceSpineError(
            "reference projection requires an owner-backed CellObservation"
        )
    if not isinstance(group, CrossSamplePeakGroupHypothesis):
        raise CidNlEvidenceSpineError(
            "reference projection requires an existing provisional group"
        )
    if cell.group_context.group is not group:
        raise CidNlEvidenceSpineError(
            "reference projection must reuse the cell's exact group context"
        )
    if cell.content.group_hypothesis_id != group.group_hypothesis_id:
        raise CidNlEvidenceSpineError(
            "reference projection cell belongs to a different group"
        )
    if cell.content.group_owner_ids != tuple(sorted(group.owner_ids)):
        raise CidNlEvidenceSpineError(
            "reference projection cell group membership was substituted"
        )


__all__ = [
    "CidNlEvidenceSpineError",
    "CidNlReferenceSpine",
    "TargetedCellObservationReferenceProjection",
    "UntargetedCellObservationReferenceProjection",
    "build_cid_nl_reference_spine",
]
