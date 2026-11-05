"""Existing-writer projection from a completed Untargeted CID-NL batch."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
    CellStatus,
)
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.production_decisions import (
    BackfillTier,
    ProductionCellDecision,
    ProductionDecisionSet,
    ProductionRowDecision,
    ProductionStatus,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
)
from xic_extractor.peak_detection.observations import PeakObservationContent

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import (
        ProductionCellDecision as OwnerProductionCellDecision,
    )
    from xic_extractor.alignment.projection.untarget.matrix import (
        UntargetRowDecisionCounts,
    )
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )

PRESENTATION_FORMULA_VERSION = "cross_sample_owner_arithmetic_mean"
# Mean of Discovery's apex MS1 readings of the row's direct members.
APEX_READING_CENTER_MZ_BASIS = "discovery_ms1_apex_mean"


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrixRow:
    """Reduced writer row that keeps finalized identity and presentation facts."""

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
    group_construction_role: str = "finalized_untargeted_cid_nl"
    group_delivery_role: str = "finalized_owner_projection"
    group_membership_source: str = "completed_untarget_batch"
    row_identity_basis: str = "no_split_peak_hypothesis"
    split_evaluation_status: str = "complete_no_product_ready_split"
    projection_status: str = "not_projection"
    center_mz_basis: str = PRESENTATION_FORMULA_VERSION
    center_rt_basis: str = PRESENTATION_FORMULA_VERSION
    center_weight_basis: str = "unique_owner_observations_equal_weight"
    evidence_status: str = "finalized_untargeted_owner_complete"
    membership_risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = ()

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return self.physical_peak_keys

    @property
    def event_member_count(self) -> int:
        return len(self.physical_peak_keys)

    @property
    def evidence(self) -> str:
        return self.evidence_status


@dataclass(frozen=True, slots=True)
class CompletedUntargetBatchWriterInputs:
    matrix: AlignmentMatrix
    production_decisions: ProductionDecisionSet


def feature_id_label(ordinal: int) -> str:
    """Report-scoped label of the ``ordinal``-th published group (1-based)."""

    return f"F{ordinal:06d}"


def project_completed_untarget_batch_writer_inputs(
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[FinalizedUntargetApplicationResult], None] | None = None,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
) -> CompletedUntargetBatchWriterInputs:
    """Materialize writer inputs for consumers that require a complete matrix."""

    rows: list[AlignmentCluster | AlignmentRowLike] = []
    cells: list[AlignedCell] = []
    cell_decisions = {}
    row_decisions = {}
    for projected in iter_completed_untarget_batch_writer_inputs(
        completed,
        consume_child=consume_child,
        ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
        membership_risks_by_owner=membership_risks_by_owner,
    ):
        rows.extend(projected.matrix.clusters)
        cells.extend(projected.matrix.cells)
        cell_decisions.update(projected.production_decisions.cells)
        row_decisions.update(projected.production_decisions.rows)
    return CompletedUntargetBatchWriterInputs(
        matrix=AlignmentMatrix(
            clusters=tuple(rows),
            cells=tuple(cells),
            sample_order=completed_untarget_batch_sample_order(completed),
        ),
        production_decisions=ProductionDecisionSet(
            cells=cell_decisions, rows=row_decisions
        ),
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
) -> Iterator[CompletedUntargetBatchWriterInputs]:
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
            row = FinalizedUntargetMatrixRow(
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
            )
            projected_cells = tuple(
                _aligned_cell(
                    row=row,
                    owner_decision=owner_decision,
                    direct_candidate_ids=direct_candidate_ids,
                )
                for owner_decision in owner_decisions
            )
            writer_cell_decisions = tuple(
                _writer_cell_decision(
                    row_id=row.feature_id,
                    cell=cell,
                    owner_decision=owner_decision,
                )
                for cell, owner_decision in zip(
                    projected_cells,
                    owner_decisions,
                    strict=True,
                )
            )
            row_decision = _writer_row_decision(
                row=row,
                cells=projected_cells,
                decisions=writer_cell_decisions,
                owner_counts=projected.counts,
                route_reason=projected.route.reason,
            )
            yield CompletedUntargetBatchWriterInputs(
                matrix=AlignmentMatrix(
                    clusters=(row,),
                    cells=projected_cells,
                    sample_order=sample_stems,
                ),
                production_decisions=ProductionDecisionSet(
                    cells={
                        (decision.feature_id, decision.sample_stem): decision
                        for decision in writer_cell_decisions
                    },
                    rows={row.feature_id: row_decision},
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


def _aligned_cell(
    *,
    row: FinalizedUntargetMatrixRow,
    owner_decision: OwnerProductionCellDecision,
    direct_candidate_ids: Mapping[tuple[str, str], str],
) -> AlignedCell:
    evidence = owner_decision.evidence
    if type(evidence) is not FinalGroupCellObservation:
        raise TypeError("writer cell requires final group-cell evidence")
    status = _cell_status(owner_decision, evidence)
    integration = owner_decision.selected_integration
    source_id = _selected_cell_source(
        owner_decision,
        evidence,
        direct_candidate_ids=direct_candidate_ids,
    )
    query_bounds = {
        (
            assessment.content.evaluation_receipt.query_rt_min,
            assessment.content.evaluation_receipt.query_rt_max,
        )
        for assessment in evidence.method_assessments
        if assessment.content.detector_invocation_id == "untargeted-cid-nl-backfill"
    }
    request_rt = query_bounds.pop() if len(query_bounds) == 1 else (None, None)
    return AlignedCell(
        sample_stem=owner_decision.subject.canonical_sample.metadata.raw_stem,
        cluster_id=row.feature_id,
        status=status,
        area=owner_decision.value,
        apex_rt=(
            integration.rt_apex_min
            if owner_decision.value is not None and integration is not None
            else None
        ),
        height=(integration.height_raw if integration is not None else None),
        peak_start_rt=(integration.rt_left_min if integration is not None else None),
        peak_end_rt=(integration.rt_right_min if integration is not None else None),
        rt_delta_sec=None,
        trace_quality="finalized_owner_decision",
        scan_support_score=None,
        source_candidate_id=source_id,
        reason=owner_decision.source or owner_decision.variant,
        selected_integration=integration,
        backfill_seed_mz=None,
        backfill_seed_rt=None,
        backfill_request_rt_min=request_rt[0],
        backfill_request_rt_max=request_rt[1],
        backfill_request_ppm=None,
        group_hypothesis_id=row.group_hypothesis_id,
        feature_id=row.feature_id,
        group_construction_role=row.group_construction_role,
        group_delivery_role=row.group_delivery_role,
        group_membership_source=row.group_membership_source,
        gap_fill_state=("accepted" if status == "backfilled" else "not_applicable"),
        gap_fill_reason=(
            ""
            if status != "backfilled"
            else "ms1_only_backfill_observation"
            if evidence.ms1_only_backfill_observations
            else owner_decision.source
        ),
        missing_observation_state=(
            owner_decision.variant if owner_decision.value is None else ""
        ),
    )


def _selected_cell_source(
    decision: OwnerProductionCellDecision,
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


def _cell_status(
    decision: OwnerProductionCellDecision,
    evidence: FinalGroupCellObservation,
) -> CellStatus:
    if decision.value is not None:
        if evidence.ms1_only_backfill_observations:
            return "backfilled"
        return "detected" if evidence.direct_observations else "backfilled"
    if decision.variant == "not_searched":
        return "unchecked"
    if decision.variant == "unresolved":
        return "ambiguous_ms1_owner"
    if evidence.direct_observations:
        return "detected"
    return "absent"


def _writer_cell_decision(
    *,
    row_id: str,
    cell: AlignedCell,
    owner_decision: OwnerProductionCellDecision,
) -> ProductionCellDecision:
    write = owner_decision.value is not None
    if write and cell.status == "detected":
        production_status: ProductionStatus = "detected"
        backfill_tier: BackfillTier = ""
    elif write:
        production_status = "accepted_backfill"
        backfill_tier = "accepted_backfill"
    else:
        production_status = "blank"
        backfill_tier = ""
    return ProductionCellDecision(
        feature_id=row_id,
        sample_stem=cell.sample_stem,
        raw_status=cell.status,
        production_status=production_status,
        backfill_tier=backfill_tier,
        write_matrix_value=write,
        matrix_value=owner_decision.value,
        blank_reason="" if write else owner_decision.variant,
    )


def _writer_row_decision(
    *,
    row: FinalizedUntargetMatrixRow,
    cells: tuple[AlignedCell, ...],
    decisions: tuple[ProductionCellDecision, ...],
    owner_counts: UntargetRowDecisionCounts,
    route_reason: str,
) -> ProductionRowDecision:
    numeric_count = sum(item.write_matrix_value for item in decisions)
    if owner_counts.numeric_cells != numeric_count:
        raise ValueError("writer row count crossed finalized owner decisions")
    detected_count = sum(cell.status == "detected" for cell in cells)
    accepted_backfill_count = sum(
        item.production_status == "accepted_backfill" for item in decisions
    )
    return ProductionRowDecision(
        feature_id=row.feature_id,
        include_in_primary_matrix=row.destination == "matrix",
        identity_decision=(
            "finalized_matrix" if row.destination == "matrix" else "finalized_archive"
        ),
        identity_confidence="owner_finalized",
        primary_evidence="completed_untarget_batch",
        identity_reason=route_reason,
        quantifiable_detected_count=sum(
            item.write_matrix_value and cell.status == "detected"
            for cell, item in zip(cells, decisions, strict=True)
        ),
        quantifiable_backfill_count=accepted_backfill_count,
        accepted_cell_count=numeric_count,
        detected_count=detected_count,
        accepted_backfill_count=accepted_backfill_count,
        review_backfill_count=0,
        duplicate_assigned_count=sum(
            cell.status == "duplicate_assigned" for cell in cells
        ),
        ambiguous_ms1_owner_count=sum(
            cell.status == "ambiguous_ms1_owner" for cell in cells
        ),
        row_flags=(
            f"finalized_route:{row.destination}",
            PRESENTATION_FORMULA_VERSION,
        ),
    )


__all__ = [
    "CompletedUntargetBatchWriterInputs",
    "FinalizedUntargetMatrixRow",
    "PRESENTATION_FORMULA_VERSION",
    "completed_untarget_batch_sample_order",
    "iter_completed_untarget_batch_writer_inputs",
    "project_completed_untarget_batch_writer_inputs",
]
