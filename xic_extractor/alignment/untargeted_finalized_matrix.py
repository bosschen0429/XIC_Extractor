"""Writer rows from a completed Untargeted CID-NL batch.

Each row keeps its group's presentation facts and the decision owner's cell
decisions in writer sample order; writers read the cells directly.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
)
from xic_extractor.peak_detection.observations import PeakObservationContent

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )

PRESENTATION_FORMULA_VERSION = "cross_sample_owner_arithmetic_mean"
# Mean of Discovery's apex MS1 readings of the row's direct members.
APEX_READING_CENTER_MZ_BASIS = "discovery_ms1_apex_mean"

CellOrigin = Literal["detected", "backfilled"]


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrixRow:
    """One finalized group with its presentation facts and cell decisions.

    ``cells`` follow the writer sample order. ``source_candidate_ids`` names the
    Discovery candidate of each numeric cell read from a direct observation.
    """

    feature_id: str
    group_hypothesis_id: str
    destination: Literal["matrix", "archive"]
    family_center_mz: float
    family_center_rt: float
    physical_peak_keys: tuple[str, ...]
    family_product_mz: float | None = None
    family_observed_neutral_loss_da: float | None = None
    neutral_loss_tag: str = ""
    has_anchor: bool = False
    members: tuple[object, ...] = ()
    row_identity_basis: str = "no_split_peak_hypothesis"
    split_evaluation_status: str = "complete_no_product_ready_split"
    projection_status: str = "not_projection"
    center_mz_basis: str = PRESENTATION_FORMULA_VERSION
    center_rt_basis: str = PRESENTATION_FORMULA_VERSION
    center_weight_basis: str = "unique_owner_observations_equal_weight"
    membership_risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = ()
    route_reason: str = ""
    cells: tuple[ProductionCellDecision, ...] = ()
    source_candidate_ids: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AlignmentMatrix:
    clusters: tuple[FinalizedUntargetMatrixRow, ...]
    sample_order: tuple[str, ...]


def feature_id_label(ordinal: int) -> str:
    """Report-scoped label of the ``ordinal``-th published group (1-based)."""

    return f"F{ordinal:06d}"


def project_completed_untarget_batch_writer_inputs(
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[FinalizedUntargetApplicationResult], None] | None = None,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
) -> AlignmentMatrix:
    """Materialize writer rows for consumers that require a complete matrix."""

    return AlignmentMatrix(
        clusters=tuple(
            iter_completed_untarget_batch_writer_inputs(
                completed,
                consume_child=consume_child,
                ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
                membership_risks_by_owner=membership_risks_by_owner,
            )
        ),
        sample_order=completed_untarget_batch_sample_order(completed),
    )


def completed_untarget_batch_sample_order(
    completed: FinalizedUntargetBatchApplicationResult,
) -> tuple[str, ...]:
    """Return the canonical output columns, including for an empty batch."""

    if type(completed) is not FinalizedUntargetBatchApplicationResult:
        raise TypeError("writer projection requires a completed Untarget batch")
    application = completed
    canonical_samples = tuple(application.canonical_samples)
    injection_orders = tuple(
        sample.metadata.injection_order for sample in canonical_samples
    )
    if any(
        isinstance(order, bool) or not isinstance(order, int) or order < 1
        for order in injection_orders
    ) or len(set(injection_orders)) != len(injection_orders):
        raise ValueError(
            "writer projection requires unique positive sample injection order"
        )
    ordered_samples = tuple(
        sorted(
            canonical_samples,
            key=lambda sample: cast(int, sample.metadata.injection_order),
        )
    )
    sample_stems = tuple(sample.metadata.raw_stem for sample in ordered_samples)
    if not sample_stems or len(sample_stems) != len(set(sample_stems)):
        raise ValueError("writer projection requires unique canonical sample stems")
    return sample_stems


def iter_completed_untarget_batch_writer_inputs(
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[FinalizedUntargetApplicationResult], None] | None = None,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
) -> Iterator[FinalizedUntargetMatrixRow]:
    """Project one finalized row at a time while retaining only identity census.

    ``ms1_apex_mz_by_candidate`` holds Discovery's apex MS1 readings. A row
    with a reading on any direct member reports their mean as its m/z; the
    readings change no grouping, trace or cell value.
    """

    sample_stems = completed_untarget_batch_sample_order(completed)
    sample_set = frozenset(sample_stems)
    application = completed

    seen_group_ids: set[str] = set()
    seen_physical_peaks: set[PeakObservationContent] = set()

    for child in application.children:
        direct_candidate_ids = _direct_candidate_ids(child.scientific_core)
        candidate_by_physical_id = (
            _direct_candidate_ids_by_physical_id(child.scientific_core)
            if ms1_apex_mz_by_candidate
            else {}
        )
        for projected in child.completed_projection.rows:
            group_id = projected.group.group_hypothesis_id
            if group_id in seen_group_ids:
                raise ValueError("writer projection received duplicate finalized rows")
            seen_group_ids.add(group_id)

            closed_decisions = tuple(projected.decisions)
            decisions_by_sample = {
                (
                    owner_decision.subject.canonical_sample.metadata.raw_stem
                ): owner_decision
                for owner_decision in closed_decisions
            }
            if (
                len(decisions_by_sample) != len(closed_decisions)
                or set(decisions_by_sample) != sample_set
            ):
                raise ValueError("writer projection crossed canonical sample order")
            owner_decisions = tuple(
                decisions_by_sample[sample_stem] for sample_stem in sample_stems
            )

            observations = tuple(
                owner_decision.evidence for owner_decision in owner_decisions
            )
            if any(
                type(item) is not FinalGroupCellObservation for item in observations
            ):
                raise TypeError("writer projection requires final group-cell evidence")
            typed_observations = tuple(
                item for item in observations if type(item) is FinalGroupCellObservation
            )
            mz_values, rt_values, physical_peaks = _presentation_facts(
                typed_observations
            )
            duplicate_peaks = seen_physical_peaks.intersection(physical_peaks)
            if duplicate_peaks:
                raise ValueError(
                    "writer projection reused physical observations across rows: "
                    + ", ".join(
                        sorted(peak.physical_peak_key for peak in duplicate_peaks)
                    )
                )
            seen_physical_peaks.update(physical_peaks)
            physical_ids = tuple(peak.physical_peak_key for peak in physical_peaks)
            center_mz = sum(mz_values) / len(mz_values)
            center_mz_basis = PRESENTATION_FORMULA_VERSION
            readings = _apex_readings(
                physical_peaks, candidate_by_physical_id, ms1_apex_mz_by_candidate
            )
            if readings:
                center_mz = sum(readings) / len(readings)
                center_mz_basis = APEX_READING_CENTER_MZ_BASIS
            center_rt = sum(rt_values) / len(rt_values)
            if not math.isfinite(center_mz) or not math.isfinite(center_rt):
                raise ValueError("writer projection produced non-finite coordinates")

            (
                neutral_loss_tag,
                family_product_mz,
                family_observed_neutral_loss_da,
                has_anchor,
            ) = _group_facts(typed_observations)
            yield FinalizedUntargetMatrixRow(
                feature_id=feature_id_label(len(seen_group_ids)),
                group_hypothesis_id=group_id,
                destination=projected.route.destination,
                family_center_mz=center_mz,
                family_center_rt=center_rt,
                center_mz_basis=center_mz_basis,
                physical_peak_keys=physical_ids,
                family_product_mz=family_product_mz,
                family_observed_neutral_loss_da=family_observed_neutral_loss_da,
                neutral_loss_tag=neutral_loss_tag,
                has_anchor=has_anchor,
                membership_risks=tuple(
                    (member.sample_key, risk)
                    for member in typed_observations[0].group.member_subjects
                    if (
                        risk := (membership_risks_by_owner or {}).get(member.subject_id)
                    )
                    is not None
                )
                + tuple(
                    (sample_id, risk)
                    for risk_group, sample_id, risk in (
                        child.scientific_core.grouping_result.backfill_membership_risks
                    )
                    if risk_group == group_id
                ),
                route_reason=projected.route.reason,
                cells=owner_decisions,
                source_candidate_ids=tuple(
                    (sample_stem, candidate_id)
                    for sample_stem, owner_decision, observation in zip(
                        sample_stems, owner_decisions, typed_observations, strict=True
                    )
                    if (
                        candidate_id := _selected_cell_source(
                            owner_decision,
                            observation,
                            direct_candidate_ids=direct_candidate_ids,
                        )
                    )
                    is not None
                ),
            )
        if consume_child is not None:
            consume_child(child)


def _group_facts(
    observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[str, float | None, float | None, bool]:
    """Carry the predecessor groups' tag, fragment, loss and anchor to the row."""

    groups = {
        id(cell.group_context.group): cell.group_context.group
        for observation in observations
        for cell in observation.effective_cells
    }.values()
    tags = sorted(
        {group.neutral_loss_tag for group in groups if group.neutral_loss_tag}
    )
    products = [
        group.group_product_mz for group in groups if group.group_product_mz is not None
    ]
    losses = [
        group.group_observed_neutral_loss_da
        for group in groups
        if group.group_observed_neutral_loss_da is not None
    ]
    return (
        ";".join(tags),
        sum(products) / len(products) if products else None,
        sum(losses) / len(losses) if losses else None,
        any(group.has_anchor for group in groups),
    )


def _presentation_facts(
    observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[PeakObservationContent, ...]]:
    facts: dict[PeakObservationContent, tuple[float, float]] = {}
    for observation in observations:
        member_peaks = {
            peak
            for member in observation.group.member_subjects
            if member.sample_key == observation.sample_key
            for peak in member.physical_peaks
        }
        for direct in observation.direct_observations:
            peak = direct.content.physical_identity
            if peak not in member_peaks:
                continue
            if peak in facts:
                raise ValueError(
                    "Matrix presentation facts contain duplicate physical identity"
                )
            mz, rt = direct.content.mz, direct.content.integration.rt_apex_min
            if not math.isfinite(mz) or not math.isfinite(rt):
                raise ValueError(
                    "Matrix row lacks finite owner-backed Mz/RT observations"
                )
            facts[peak] = (float(mz), float(rt))
    if not facts:
        raise ValueError("Matrix row lacks owner-backed Mz/RT observations")
    ordered = tuple(sorted(facts.items(), key=lambda item: item[0].measured_order_key))
    return (
        tuple(values[0] for _peak, values in ordered),
        tuple(values[1] for _peak, values in ordered),
        tuple(peak for peak, _values in ordered),
    )


def _selected_cell_source(
    decision: ProductionCellDecision,
    evidence: FinalGroupCellObservation,
    *,
    direct_candidate_ids: Mapping[tuple[str, str], str],
) -> str | None:
    if decision.value is None or evidence.ms1_only_backfill_observations:
        return None
    direct = tuple(evidence.direct_observations)
    if len(direct) != 1:
        raise ValueError("numeric writer cell requires one physical source")
    hypothesis_ids = tuple(
        hypothesis.hypothesis_id for hypothesis in evidence.direct_hypotheses
    )
    if len(hypothesis_ids) != 1:
        raise ValueError(
            "direct writer cell requires one source PeakHypothesis identity"
        )
    key = (
        decision.subject.canonical_sample.identity.sample_key,
        hypothesis_ids[0],
    )
    # Recovery provenance is already closed by the observation/hypothesis owners.
    # A source outside Discovery has no candidate ID; the physical source remains
    # retained on the completed observation.
    return direct_candidate_ids.get(key)


def _direct_candidate_ids(
    scientific_core: FinalizedScientificAlignment,
) -> dict[tuple[str, str], str]:
    if type(scientific_core) is not FinalizedScientificAlignment:
        raise TypeError("writer projection requires an exact finalized scientific core")
    candidate_ids: dict[tuple[str, str], str] = {}
    observed_subjects: dict[tuple[str, str], ObservedSearchSubject] = {}
    for subject in scientific_core.terminal_subjects:
        if type(subject) is not ObservedSearchSubject:
            continue
        key = (
            subject.sample_key,
            subject.hypothesis.hypothesis_id,
        )
        previous = observed_subjects.get(key)
        if previous is not None and previous.hypothesis != subject.hypothesis:
            raise ValueError(
                "finalized scientific core conflicts on a direct physical identity"
            )
        observed_subjects[key] = subject
        candidate_ids[key] = min(
            candidate_ids.get(key, subject.candidate_id), subject.candidate_id
        )
    return candidate_ids


def _direct_candidate_ids_by_physical_id(
    scientific_core: FinalizedScientificAlignment,
) -> dict[PeakObservationContent, str]:
    candidate_ids: dict[PeakObservationContent, str] = {}
    for subject in scientific_core.terminal_subjects:
        if type(subject) is not ObservedSearchSubject:
            continue
        for ref in subject.hypothesis.peak_observation_refs:
            physical_id = ref.content.physical_identity
            candidate_ids[physical_id] = min(
                candidate_ids.get(physical_id, subject.candidate_id),
                subject.candidate_id,
            )
    return candidate_ids


def _apex_readings(
    physical_ids: tuple[PeakObservationContent, ...],
    candidate_by_physical_id: Mapping[PeakObservationContent, str],
    ms1_apex_mz_by_candidate: Mapping[str, float] | None,
) -> tuple[float, ...]:
    if not ms1_apex_mz_by_candidate:
        return ()
    readings = []
    for physical_id in physical_ids:
        candidate_id = candidate_by_physical_id.get(physical_id)
        reading = (
            None if candidate_id is None else ms1_apex_mz_by_candidate.get(candidate_id)
        )
        if reading is not None and math.isfinite(reading):
            readings.append(float(reading))
    return tuple(readings)


def cell_origin(decision: ProductionCellDecision) -> CellOrigin | None:
    """Where the cell's peak came from, or ``None`` when it reports no peak.

    A numeric value read from an MS1-only Backfill peak is ``backfilled``; one
    read from the group's own direct observation is ``detected``. A no-write
    cell that still retains a direct observation reports ``detected``.
    """

    evidence = cast(FinalGroupCellObservation, decision.evidence)
    if decision.value is not None:
        if evidence.direct_observations and not (
            evidence.ms1_only_backfill_observations
        ):
            return "detected"
        return "backfilled"
    if evidence.direct_observations and decision.variant not in {
        "not_searched",
        "unresolved",
    }:
        return "detected"
    return None


def cell_status(decision: ProductionCellDecision) -> str:
    """The cell's origin, or its no-write state when it reports no peak."""

    return cell_origin(decision) or decision.variant


def backfill_query_bounds(
    decision: ProductionCellDecision,
) -> tuple[float | None, float | None]:
    """The RT window of the cell's single Backfill search, when it has one."""

    evidence = cast(FinalGroupCellObservation, decision.evidence)
    bounds = {
        (
            assessment.content.evaluation_receipt.query_rt_min,
            assessment.content.evaluation_receipt.query_rt_max,
        )
        for assessment in evidence.method_assessments
        if assessment.content.detector_invocation_id == "untargeted-cid-nl-backfill"
    }
    return bounds.pop() if len(bounds) == 1 else (None, None)


__all__ = [
    "AlignmentMatrix",
    "CellOrigin",
    "FinalizedUntargetMatrixRow",
    "PRESENTATION_FORMULA_VERSION",
    "backfill_query_bounds",
    "cell_origin",
    "cell_status",
    "completed_untarget_batch_sample_order",
    "iter_completed_untarget_batch_writer_inputs",
    "project_completed_untarget_batch_writer_inputs",
]
