"""Existing-writer projection from a completed Untargeted CID-NL batch."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.cell_observations import FinalGroupCellObservation
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix, CellStatus
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    ProductionRowDecision,
    ProductionStatus,
    RescueTier,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.peak_detection.evidence_facts import (
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import (
        ProductionCellDecision as OwnerProductionCellDecision,
    )
    from xic_extractor.alignment.owner_backfill import (
        OwnerBackfillPeakFactObservation,
    )

PRESENTATION_FORMULA_VERSION = "cross_sample_owner_arithmetic_mean_v1"


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrixRow:
    """Reduced writer row that keeps finalized identity and presentation facts."""

    feature_family_id: str
    group_hypothesis_id: str
    public_family_id: str
    destination: Literal["matrix", "archive"]
    family_center_mz: float
    family_center_rt: float
    physical_observation_ids: tuple[str, ...]
    lineage_edge_ids: tuple[str, ...]
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
    parent_peak_hypothesis_id: str = ""
    child_peak_hypothesis_ids: str = ""

    @property
    def source_feature_family_ids(self) -> tuple[str, ...]:
        return (self.feature_family_id,)

    @property
    def presentation_formula_version(self) -> str:
        return PRESENTATION_FORMULA_VERSION

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return self.physical_observation_ids

    @property
    def event_member_count(self) -> int:
        return len(self.physical_observation_ids)

    @property
    def evidence(self) -> str:
        return self.evidence_status


@dataclass(frozen=True, slots=True)
class CompletedUntargetBatchWriterInputs:
    matrix: AlignmentMatrix
    production_decisions: ProductionDecisionSet


def project_completed_untarget_batch_writer_inputs(
    completed: CompletedUntargetBatchWorkflowResult,
) -> CompletedUntargetBatchWriterInputs:
    """Translate one successful finalized batch without rerunning decisions."""

    if type(completed) is not CompletedUntargetBatchWorkflowResult:
        raise TypeError("writer projection requires a completed Untarget batch")
    from xic_extractor.alignment.cell_decision.owner import (
        validate_production_cell_decision,
    )

    application = completed.application_result
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

    rows: list[FinalizedUntargetMatrixRow] = []
    cells: list[AlignedCell] = []
    cell_decisions: dict[tuple[str, str], ProductionCellDecision] = {}
    row_decisions: dict[str, ProductionRowDecision] = {}
    seen_row_ids: set[str] = set()
    seen_group_ids: set[str] = set()
    seen_physical_ids: set[str] = set()

    for child in application.children:
        direct_candidate_ids = _direct_candidate_ids(child.scientific_core)
        for projected in child.completed_projection.rows:
            row_id = projected.row_key.untarget_row_key_id
            group_id = projected.row_key.group_hypothesis_id
            if row_id in seen_row_ids or group_id in seen_group_ids:
                raise ValueError("writer projection received duplicate finalized rows")
            seen_row_ids.add(row_id)
            seen_group_ids.add(group_id)

            closed_decisions = tuple(projected.decisions)
            for owner_decision in closed_decisions:
                validate_production_cell_decision(owner_decision)
            decisions_by_sample = {
                (
                    owner_decision.subject.canonical_sample.metadata.raw_stem
                ): owner_decision
                for owner_decision in closed_decisions
            }
            if len(decisions_by_sample) != len(closed_decisions) or set(
                decisions_by_sample
            ) != set(sample_stems):
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
            mz_values, rt_values, physical_ids = _presentation_facts(typed_observations)
            duplicate_ids = seen_physical_ids.intersection(physical_ids)
            if duplicate_ids:
                raise ValueError(
                    "writer projection reused physical observations across rows: "
                    + ", ".join(sorted(duplicate_ids))
                )
            seen_physical_ids.update(physical_ids)
            center_mz = sum(mz_values) / len(mz_values)
            center_rt = sum(rt_values) / len(rt_values)
            if not math.isfinite(center_mz) or not math.isfinite(center_rt):
                raise ValueError("writer projection produced non-finite coordinates")

            row = FinalizedUntargetMatrixRow(
                feature_family_id=row_id,
                group_hypothesis_id=group_id,
                public_family_id=f"FAM{len(rows) + 1:06d}",
                destination=projected.route.destination,
                family_center_mz=center_mz,
                family_center_rt=center_rt,
                physical_observation_ids=physical_ids,
                lineage_edge_ids=tuple(
                    sorted(
                        edge.lineage_edge_id
                        for edge in child.scientific_core.lineage
                        if edge.successor_group_hypothesis_id == group_id
                    )
                ),
            )
            rows.append(row)

            projected_cells = tuple(
                _aligned_cell(
                    row=row,
                    owner_decision=owner_decision,
                    direct_candidate_ids=direct_candidate_ids,
                )
                for owner_decision in owner_decisions
            )
            cells.extend(projected_cells)
            writer_cell_decisions = tuple(
                _writer_cell_decision(
                    row_id=row_id,
                    cell=cell,
                    owner_decision=owner_decision,
                )
                for cell, owner_decision in zip(
                    projected_cells,
                    owner_decisions,
                    strict=True,
                )
            )
            for writer_decision in writer_cell_decisions:
                key = (
                    writer_decision.feature_family_id,
                    writer_decision.sample_stem,
                )
                if key in cell_decisions:
                    raise ValueError("writer projection produced duplicate cells")
                cell_decisions[key] = writer_decision
            row_decisions[row_id] = _writer_row_decision(
                row=row,
                cells=projected_cells,
                decisions=writer_cell_decisions,
                owner_counts=projected.counts,
                route_reason=projected.route.reason,
            )

    matrix = AlignmentMatrix(
        clusters=tuple(rows),
        cells=tuple(cells),
        sample_order=sample_stems,
    )
    expected_keys = {
        (row.feature_family_id, sample_stem)
        for row in rows
        for sample_stem in sample_stems
    }
    if set(cell_decisions) != expected_keys:
        raise ValueError("writer projection omitted or added finalized cells")
    return CompletedUntargetBatchWriterInputs(
        matrix=matrix,
        production_decisions=ProductionDecisionSet(
            cells=cell_decisions,
            rows=row_decisions,
        ),
    )


def _presentation_facts(
    observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[str, ...]]:
    facts: dict[str, tuple[float, float]] = {}
    for observation in observations:
        member_physical_ids = {
            physical_id
            for member in observation.group.member_subjects
            if member.canonical_sample_identity_id
            == observation.canonical_sample_identity_id
            for physical_id in member.physical_observation_ids
        }
        for direct in observation.direct_observations:
            if direct.content.peak_observation_content_id not in member_physical_ids:
                continue
            _add_presentation_fact(
                facts,
                physical_id=direct.content.peak_observation_content_id,
                mz=direct.content.mz,
                rt=direct.content.integration.rt_apex_min,
            )
        for repaired in observation.repaired_observations:
            if not repaired.selected_for_output:
                continue
            receipt = repaired.facts
            if receipt is None:
                raise ValueError(
                    "selected repaired observation lacks a physical fact receipt"
                )
            physical_evaluation = receipt.physical_evaluation
            if physical_evaluation is None:
                raise ValueError(
                    "selected repaired observation lacks a physical evaluation"
                )
            try:
                validate_bounded_physical_peak_evaluation(physical_evaluation)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "selected repaired observation has invalid physical evaluation"
                ) from exc
            physical_facts = physical_evaluation.facts
            if physical_facts is None:
                raise ValueError(
                    "selected repaired observation lacks complete physical facts"
                )
            physical_id = physical_facts.fact_content_id
            _add_presentation_fact(
                facts,
                physical_id=physical_id,
                mz=repaired.cell.backfill_seed_mz,
                rt=repaired.cell.apex_rt,
            )
    if not facts:
        raise ValueError("Matrix row lacks owner-backed Mz/RT observations")
    ordered = tuple(sorted(facts.items()))
    return (
        tuple(values[0] for _physical_id, values in ordered),
        tuple(values[1] for _physical_id, values in ordered),
        tuple(physical_id for physical_id, _values in ordered),
    )


def _add_presentation_fact(
    facts: dict[str, tuple[float, float]],
    *,
    physical_id: str,
    mz: float | None,
    rt: float | None,
) -> None:
    if not isinstance(physical_id, str) or not physical_id.strip():
        raise ValueError("Matrix presentation fact requires physical identity")
    if physical_id in facts:
        raise ValueError(
            "Matrix presentation facts contain duplicate physical identity"
        )
    if (
        isinstance(mz, bool)
        or not isinstance(mz, (int, float))
        or isinstance(rt, bool)
        or not isinstance(rt, (int, float))
        or not math.isfinite(mz)
        or not math.isfinite(rt)
    ):
        raise ValueError("Matrix row lacks finite owner-backed Mz/RT observations")
    value = (float(mz), float(rt))
    facts[physical_id] = value


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
    integration = _selected_integration(owner_decision, evidence)
    source_id, repaired = _selected_cell_source(
        owner_decision,
        evidence,
        direct_candidate_ids=direct_candidate_ids,
    )
    return AlignedCell(
        sample_stem=owner_decision.subject.canonical_sample.metadata.raw_stem,
        cluster_id=row.feature_family_id,
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
        source_raw_file=None,
        reason=owner_decision.source or owner_decision.variant,
        selected_integration=integration,
        backfill_seed_mz=(repaired.cell.backfill_seed_mz if repaired else None),
        backfill_seed_rt=(repaired.cell.backfill_seed_rt if repaired else None),
        backfill_request_rt_min=(
            repaired.cell.backfill_request_rt_min if repaired else None
        ),
        backfill_request_rt_max=(
            repaired.cell.backfill_request_rt_max if repaired else None
        ),
        backfill_request_ppm=(repaired.cell.backfill_request_ppm if repaired else None),
        group_hypothesis_id=row.group_hypothesis_id,
        public_family_id=row.public_family_id,
        group_construction_role=row.group_construction_role,
        group_delivery_role=row.group_delivery_role,
        group_membership_source=row.group_membership_source,
        gap_fill_state=("accepted" if status == "rescued" else "not_applicable"),
        gap_fill_reason=(owner_decision.source if status == "rescued" else ""),
        missing_observation_state=(
            owner_decision.variant if owner_decision.value is None else ""
        ),
    )


def _selected_integration(
    decision: OwnerProductionCellDecision,
    evidence: FinalGroupCellObservation,
) -> IntegrationResult | None:
    integrations = evidence.integrations
    if decision.value is not None:
        if len(integrations) != 1:
            raise ValueError("numeric writer cell requires one exact integration")
        return integrations[0]
    if decision.variant == "not_quantifiable" and len(integrations) == 1:
        return integrations[0]
    return None


def _selected_cell_source(
    decision: OwnerProductionCellDecision,
    evidence: FinalGroupCellObservation,
    *,
    direct_candidate_ids: Mapping[tuple[str, str], str],
) -> tuple[str | None, OwnerBackfillPeakFactObservation | None]:
    if decision.value is None:
        return None, None
    direct = tuple(evidence.direct_observations)
    repaired = tuple(
        item for item in evidence.repaired_observations if item.selected_for_output
    )
    if len(direct) + len(repaired) != 1:
        raise ValueError("numeric writer cell requires one physical source")
    if direct:
        hypothesis_ids = tuple(
            hypothesis.hypothesis_id for hypothesis in evidence.direct_hypotheses
        )
        if len(hypothesis_ids) != 1:
            raise ValueError(
                "direct writer cell requires one source PeakHypothesis identity"
            )
        key = (
            decision.subject.canonical_sample.identity.sample_identity_id,
            hypothesis_ids[0],
        )
        try:
            return direct_candidate_ids[key], None
        except KeyError as exc:
            # A completed recovery can supply direct CID-NL support
            # outside the closed Discovery candidate universe. Keep its physical
            # identity and explicit absent candidate instead of inventing one.
            assessments = evidence.method_assessments
            if len(assessments) == 1 and (
                assessments[0].content.method_subject.composition is not None
                or assessments[0].content.detector_invocation_id
                == "untargeted-cid-nl-backfill-recovery:v1"
            ):
                from xic_extractor.alignment.hypothesis_owner_adapter import (
                    validate_cid_nl_assessment_hypothesis_closure,
                )

                validate_cid_nl_assessment_hypothesis_closure(
                    sample=decision.subject.canonical_sample,
                    assessment=assessments[0],
                    hypothesis=evidence.direct_hypotheses[0],
                )
                return None, None
            raise ValueError(
                "direct writer cell lacks its terminal Discovery candidate identity"
            ) from exc
    selected = repaired[0]
    return None, selected


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
            subject.canonical_sample_identity_id,
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


def _cell_status(
    decision: OwnerProductionCellDecision,
    evidence: FinalGroupCellObservation,
) -> CellStatus:
    if decision.value is not None:
        return "detected" if evidence.direct_observations else "rescued"
    if decision.variant == "not_searched":
        return "unchecked"
    if decision.variant == "unresolved":
        return "ambiguous_ms1_owner"
    if evidence.direct_observations:
        return "detected"
    if any(item.selected_for_output for item in evidence.repaired_observations):
        return "rescued"
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
        rescue_tier: RescueTier = ""
    elif write:
        production_status = "accepted_rescue"
        rescue_tier = "accepted_rescue"
    else:
        production_status = "blank"
        rescue_tier = ""
    return ProductionCellDecision(
        feature_family_id=row_id,
        sample_stem=cell.sample_stem,
        raw_status=cell.status,
        production_status=production_status,
        rescue_tier=rescue_tier,
        write_matrix_value=write,
        matrix_value=owner_decision.value,
        blank_reason="" if write else owner_decision.variant,
    )


def _writer_row_decision(
    *,
    row: FinalizedUntargetMatrixRow,
    cells: tuple[AlignedCell, ...],
    decisions: tuple[ProductionCellDecision, ...],
    owner_counts: object,
    route_reason: str,
) -> ProductionRowDecision:
    numeric_count = sum(item.write_matrix_value for item in decisions)
    if getattr(owner_counts, "numeric_cells", None) != numeric_count:
        raise ValueError("writer row count crossed finalized owner decisions")
    detected_count = sum(cell.status == "detected" for cell in cells)
    accepted_rescue_count = sum(
        item.production_status == "accepted_rescue" for item in decisions
    )
    return ProductionRowDecision(
        feature_family_id=row.feature_family_id,
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
        quantifiable_rescue_count=accepted_rescue_count,
        accepted_cell_count=numeric_count,
        detected_count=detected_count,
        accepted_rescue_count=accepted_rescue_count,
        review_rescue_count=0,
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
    "project_completed_untarget_batch_writer_inputs",
]
