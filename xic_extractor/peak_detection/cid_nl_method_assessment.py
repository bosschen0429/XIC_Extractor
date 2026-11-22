"""Complete, owner-backed settlement for one CID-NL method attempt.

Positive measured evidence and one-to-one support keep their existing owners.
This module closes the complete attempt so technical state, completed negative
searches, HOLD, and REJECT do not need fake ``PeakHypothesis`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.configuration.models import Target
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelation,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_METHOD_ID,
    CID_NL_METHOD_VERSION,
    CID_NL_PROVIDER_ID,
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportPolicy,
    bind_dda_cid_nl_peak_support,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.precursor_evidence import PrecursorLossAssessment
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.sample_metadata import SampleMetadata

CidNlTechnicalState: TypeAlias = Literal[
    "evaluated",
    "unsupported",
    "failed",
    "not_evaluated",
]
CidNlMethodResult: TypeAlias = Literal["SUPPORT", "HOLD", "REJECT"]


class CidNlMethodAssessmentContractError(ValueError):
    """Raised when one complete CID-NL method attempt cannot be closed."""


@dataclass(frozen=True, slots=True)
class CidNlCandidateInterval:
    """Physical geometry needed for event attribution, before artifact binding."""

    mz: float
    rt_left_min: float
    rt_right_min: float


@dataclass(frozen=True, slots=True)
class CidNlEventQuery:
    """Measured precursor answer and support interval for one acquired event."""

    precursor: PrecursorLossAssessment
    scan_rt_min: float
    support_rt_min: float


@dataclass(frozen=True, slots=True)
class CidNlMethodRelations:
    """One relation answer reused by selection and later provenance binding.

    ``set_relations`` pairs each set-level event with its candidate component:
    the candidates containing it plus every m/z-compatible candidate whose
    interval touches that component.
    """

    candidate_intervals: tuple[CidNlCandidateInterval, ...]
    event_queries: tuple[CidNlEventQuery, ...]
    policy: DdaCidNlPeakSupportPolicy
    individual_relations: tuple[tuple[int, int], ...]
    set_relations: tuple[tuple[int, tuple[int, ...]], ...]
    set_related_candidate_indexes: frozenset[int]

    def __deepcopy__(self, memo: dict[int, object]) -> CidNlMethodRelations:
        return self

    @property
    def supported_candidate_indexes(self) -> frozenset[int]:
        return frozenset(candidate for _event, candidate in self.individual_relations)

    @property
    def set_relation_event_indexes(self) -> tuple[int, ...]:
        return tuple(event for event, _members in self.set_relations)


def evaluate_cid_nl_method_relations(
    *,
    candidate_intervals: tuple[CidNlCandidateInterval, ...],
    event_queries: tuple[CidNlEventQuery, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> CidNlMethodRelations:
    """Attribute the complete in-memory universe without request/attempt IDs.

    Only a strictly interior event in a non-overlapping compatible interval
    supports an individual peak. Boundary and overlap evidence stays set-level.
    """
    individual: list[tuple[int, int]] = []
    set_relations: list[tuple[int, tuple[int, ...]]] = []
    set_related: set[int] = set()
    for event_index, event in enumerate(event_queries):
        precursor = event.precursor
        if not policy.accepts_precursor(precursor) or precursor.reference_mz is None:
            continue
        compatible = tuple(
            index
            for index, peak in enumerate(candidate_intervals)
            if abs(precursor.reference_mz - peak.mz) <= policy.precursor_tolerance_da
        )
        containing = tuple(
            index
            for index in compatible
            if candidate_intervals[index].rt_left_min <= event.support_rt_min
            and event.scan_rt_min <= candidate_intervals[index].rt_right_min
        )
        if not containing:
            continue
        selected_index = containing[0]
        selected = candidate_intervals[selected_index]
        strictly_unique = (
            len(containing) == 1
            and selected.rt_left_min < event.scan_rt_min < selected.rt_right_min
            and not any(
                index != selected_index
                and selected.rt_left_min < candidate_intervals[index].rt_right_min
                and candidate_intervals[index].rt_left_min < selected.rt_right_min
                for index in compatible
            )
        )
        if strictly_unique:
            individual.append((event_index, selected_index))
            continue
        set_related.update(containing)
        component = set(containing)
        changed = True
        while changed:
            changed = False
            for index in compatible:
                peak = candidate_intervals[index]
                if index not in component and any(
                    peak.rt_left_min <= candidate_intervals[member].rt_right_min
                    and candidate_intervals[member].rt_left_min <= peak.rt_right_min
                    for member in component
                ):
                    component.add(index)
                    changed = True
        set_relations.append((event_index, tuple(sorted(component))))
    return CidNlMethodRelations(
        candidate_intervals=candidate_intervals,
        event_queries=event_queries,
        policy=policy,
        individual_relations=tuple(individual),
        set_relations=tuple(set_relations),
        set_related_candidate_indexes=frozenset(set_related),
    )


@dataclass(frozen=True, slots=True)
class CidNlEvaluationReceipt:
    """Search receipt; an unattempted declared-scan query may have no RT bounds."""

    query_rt_min: float | None
    query_rt_max: float | None
    support_policy: DdaCidNlPeakSupportPolicy
    acquisition_supported: bool
    search_completed: bool
    reason_code: str | None
    evidence_scope: Literal["subject_window", "declared_scans"] = "subject_window"
    physical_query_mz: float | None = None
    supplemental_scan_keys: tuple[tuple[int, float], ...] = ()

    def __post_init__(self) -> None:
        for name in ("query_rt_min", "query_rt_max", "physical_query_mz"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, float(value) or 0.0)


@dataclass(frozen=True, slots=True)
class CidNlMethodAssessment:
    """One complete method attempt: its technical and method-result axes.

    Equality uses the attempt's values only; the scan-level relations that
    decided the result travel with it without changing that equality.
    """

    sample_key: str
    acquisition_source_id: str
    provider_id: str
    method_id: str
    method_version: str
    method_subject: CidNlMethodSubject
    detector_invocation_id: str
    candidate_peaks: tuple[PeakObservation, ...]
    evaluation_receipt: CidNlEvaluationReceipt
    evidence_observations: tuple[CidNlEvidenceObservation, ...]
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    reason_codes: tuple[str, ...]
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...] = field(compare=False)
    set_relation_refs: tuple[DdaCidNlCandidateSetRelation, ...] = field(compare=False)


def resolve_cid_nl_method_relations(
    *,
    sample: SampleMetadata,
    evidence_refs: tuple[CidNlEvidenceObservation, ...],
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
    relations: CidNlMethodRelations | None = None,
) -> tuple[
    tuple[DdaCidNlPeakSupport, ...],
    tuple[DdaCidNlCandidateSetRelation, ...],
]:
    """Bind the shared relation answer to exact evidence and physical artifacts."""
    intervals = tuple(
        CidNlCandidateInterval(
            peak.mz,
            peak.rt_left_min,
            peak.rt_right_min,
        )
        for peak in candidate_peaks
    )
    queries = tuple(
        CidNlEventQuery(
            evidence.assess_precursor_loss(policy.neutral_loss_tolerance_ppm),
            evidence.scan_rt_min,
            evidence.support_rt_min,
        )
        for evidence in evidence_refs
    )
    if relations is None:
        relations = evaluate_cid_nl_method_relations(
            candidate_intervals=intervals, event_queries=queries, policy=policy
        )
    ordered_candidates = tuple(sorted(candidate_peaks, key=_measured_order))
    individual_refs = tuple(
        bind_dda_cid_nl_peak_support(
            sample=sample,
            evidence=evidence_refs[event_index],
            precursor=queries[event_index].precursor,
            peak_observation=candidate_peaks[candidate_index],
            candidate_peaks=ordered_candidates,
            policy=policy,
        )
        for event_index, candidate_index in relations.individual_relations
    )
    set_refs = tuple(
        DdaCidNlCandidateSetRelation(
            sample_key=sample.sample_name,
            evidence=evidence_refs[event_index],
            candidate_peaks=ordered_candidates,
            matched_candidates=tuple(
                sorted(
                    (candidate_peaks[index] for index in members),
                    key=_measured_order,
                )
            ),
            acquisition_mode=evidence_refs[event_index].acquisition_mode,
            dda_scan_number=evidence_refs[event_index].scan_number,
            scan_rt_min=evidence_refs[event_index].scan_rt_min,
            policy=policy,
        )
        for event_index, members in relations.set_relations
    )
    return individual_refs, set_refs


def mint_cid_nl_method_assessment(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    target: Target | None = None,
    method_subject: CidNlMethodSubject | None = None,
    detector_invocation_id: str,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservation, ...] = (),
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelation, ...] = (),
    technical_state: CidNlTechnicalState = "evaluated",
) -> CidNlMethodAssessment:
    """Close one complete CID-NL attempt without granting product authority."""

    subject = _resolve_method_subject(
        target=target,
        method_subject=method_subject,
    )
    candidates = _canonical_candidates(candidate_peaks)
    evidence = _canonical_evidence_refs(evidence_refs)
    individual = _canonical_individual_refs(individual_support_refs)
    set_relations = _canonical_set_refs(set_relation_refs)
    method_result, reason_codes = _method_result(
        method_subject=subject,
        technical_state=technical_state,
        evaluation_receipt=evaluation_receipt,
        candidate_peaks=candidates,
        evidence_refs=evidence,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )
    return CidNlMethodAssessment(
        sample_key=sample.sample_name,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        provider_id=CID_NL_PROVIDER_ID,
        method_id=CID_NL_METHOD_ID,
        method_version=CID_NL_METHOD_VERSION,
        method_subject=subject,
        detector_invocation_id=detector_invocation_id,
        candidate_peaks=candidates,
        evaluation_receipt=evaluation_receipt,
        evidence_observations=evidence,
        technical_state=technical_state,
        method_result=method_result,
        reason_codes=reason_codes,
        individual_support_refs=individual,
        set_relation_refs=set_relations,
    )


def _resolve_method_subject(
    *,
    target: Target | None,
    method_subject: CidNlMethodSubject | None,
) -> CidNlMethodSubject:
    if (target is None) == (method_subject is None):
        raise CidNlMethodAssessmentContractError(
            "assessment requires exactly one Targeted adapter or method_subject"
        )
    if target is not None:
        try:
            return cid_nl_method_subject_from_target(target)
        except CidNlMethodSubjectContractError as exc:
            raise CidNlMethodAssessmentContractError(str(exc)) from exc
    assert method_subject is not None
    return method_subject


def _method_result(
    *,
    method_subject: CidNlMethodSubject,
    technical_state: CidNlTechnicalState,
    evaluation_receipt: CidNlEvaluationReceipt,
    candidate_peaks: tuple[PeakObservation, ...],
    evidence_refs: tuple[CidNlEvidenceObservation, ...],
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...],
    set_relation_refs: tuple[DdaCidNlCandidateSetRelation, ...],
) -> tuple[CidNlMethodResult | None, tuple[str, ...]]:
    if technical_state != "evaluated":
        assert evaluation_receipt.reason_code is not None
        return None, (evaluation_receipt.reason_code,)
    if _supported_peaks(method_subject, individual_support_refs):
        return "SUPPORT", ("unique_support_within_declared_candidate_universe",)
    if method_subject.composition is not None and individual_support_refs:
        return "HOLD", ("incomplete_composite_requirement",)
    if set_relation_refs:
        return "HOLD", ("set_level_relation",)
    if not candidate_peaks and any(
        evaluation_receipt.support_policy.accepts_precursor(
            evidence.assess_precursor_loss(
                evaluation_receipt.support_policy.neutral_loss_tolerance_ppm
            )
        )
        for evidence in evidence_refs
    ):
        return "REJECT", ("qualifying_cid_nl_without_usable_ms1_peak",)
    if candidate_peaks:
        return "HOLD", ("no_unique_qualifying_relation",)
    return "HOLD", ("insufficient_method_evidence",)


def is_cid_nl_missing_ms1_rejection(assessment: CidNlMethodAssessment) -> bool:
    """Recognize a completed rejection; group owners still authorize a revisit."""
    return (
        assessment.technical_state == "evaluated"
        and assessment.method_result == "REJECT"
        and assessment.reason_codes == ("qualifying_cid_nl_without_usable_ms1_peak",)
        and not assessment.candidate_peaks
    )


def is_ms1_only_cid_nl_backfill_assessment(
    assessment: CidNlMethodAssessment,
) -> bool:
    """Recognize a group-bounded Backfill that found one MS1 peak and no CID-NL.

    The group owner authorized the revisit; the result grants no detection.
    """
    return (
        assessment.detector_invocation_id == "untargeted-cid-nl-backfill"
        and assessment.technical_state == "evaluated"
        and assessment.method_result == "HOLD"
        and assessment.reason_codes == ("no_unique_qualifying_relation",)
        and len(assessment.candidate_peaks) == 1
        and not assessment.evidence_observations
    )


def is_unsearched_cid_nl_discovery_assessment(
    assessment: CidNlMethodAssessment,
) -> bool:
    """Recognize missing eligible input, without granting recovery authority."""
    return (
        assessment.technical_state == "not_evaluated"
        and assessment.reason_codes == ("no_search_eligible_discovery_input",)
    )


def supported_cid_nl_peaks(
    assessment: CidNlMethodAssessment,
) -> frozenset[PeakObservation]:
    """Physical peaks that satisfy the complete owner-bound requirement."""
    if assessment.technical_state != "evaluated":
        return frozenset()
    return _supported_peaks(
        assessment.method_subject,
        assessment.individual_support_refs,
    )


def cid_nl_support_requires_supplement(
    assessment: CidNlMethodAssessment, peak: PeakObservation
) -> bool:
    """Whether the direct hypothesis needs local confirmation to meet its method."""
    supplemental = assessment.evaluation_receipt.supplemental_scan_keys
    return bool(supplemental) and peak not in _supported_peaks(
        assessment.method_subject,
        tuple(
            ref
            for ref in assessment.individual_support_refs
            if ref.peak_observation == peak
            and (
                ref.dda_scan_number,
                ref.configured_neutral_loss_da,
            )
            not in supplemental
        ),
    )


def _supported_peaks(
    subject: CidNlMethodSubject,
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...],
) -> frozenset[PeakObservation]:
    losses_by_peak: dict[PeakObservation, set[float]] = {}
    for support in individual_support_refs:
        losses_by_peak.setdefault(support.peak_observation, set()).add(
            support.evidence.configured_neutral_loss_da
        )
    required = set(subject.configured_losses)
    return frozenset(
        peak
        for peak, losses in losses_by_peak.items()
        if subject.composition is None
        or subject.composition.combine_mode == "union"
        or required.issubset(losses)
    )


def _measured_order(
    peak: PeakObservation,
) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
    return peak.measured_order_key


def _canonical_candidates(
    value: tuple[PeakObservation, ...],
) -> tuple[PeakObservation, ...]:
    refs = tuple(sorted(value, key=_measured_order))
    _require_unique_refs(
        tuple(item.physical_peak_key for item in refs),
        field="candidate peak content",
    )
    return refs


def _canonical_evidence_refs(
    value: tuple[CidNlEvidenceObservation, ...],
) -> tuple[CidNlEvidenceObservation, ...]:
    return _canonical_refs(value, field="evidence_refs")


def _canonical_individual_refs(
    value: tuple[DdaCidNlPeakSupport, ...],
) -> tuple[DdaCidNlPeakSupport, ...]:
    return _canonical_refs(value, field="individual_support_refs")


def _canonical_set_refs(
    value: tuple[DdaCidNlCandidateSetRelation, ...],
) -> tuple[DdaCidNlCandidateSetRelation, ...]:
    return _canonical_refs(value, field="set_relation_refs")


def _canonical_refs(value: tuple, *, field: str) -> tuple:

    def event_key(
        item: CidNlEvidenceObservation
        | DdaCidNlPeakSupport
        | DdaCidNlCandidateSetRelation,
    ) -> tuple:
        observation = (
            item if isinstance(item, CidNlEvidenceObservation) else item.evidence
        )
        return (
            observation.sample_key,
            observation.scan_number,
            observation.configured_neutral_loss_da,
            observation.scan_rt_min,
            observation.scan_precursor_mz,
            observation.product_mz,
        )

    refs = tuple(sorted(value, key=event_key))
    keys = tuple(event_key(item) for item in refs)
    if len(set(keys)) != len(keys):
        raise CidNlMethodAssessmentContractError(
            f"duplicate acquisition scan or ambiguous {field} measured events"
        )
    return refs


def _require_unique_refs(values: tuple[str, ...], *, field: str) -> None:
    if len(set(values)) != len(values):
        raise CidNlMethodAssessmentContractError(f"duplicate {field} refs")


__all__ = [
    "CID_NL_METHOD_ID",
    "CID_NL_METHOD_VERSION",
    "CID_NL_PROVIDER_ID",
    "CidNlCandidateInterval",
    "CidNlEvaluationReceipt",
    "CidNlEventQuery",
    "CidNlMethodAssessment",
    "CidNlMethodAssessmentContractError",
    "CidNlMethodResult",
    "CidNlMethodRelations",
    "CidNlTechnicalState",
    "evaluate_cid_nl_method_relations",
    "mint_cid_nl_method_assessment",
    "resolve_cid_nl_method_relations",
]
