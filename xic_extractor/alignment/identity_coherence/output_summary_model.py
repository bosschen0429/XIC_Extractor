from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import CellEvidenceResult, IdentityDecisionSummary
from .output_formatting import (
    control_pass_is_true,
    enum_value,
    format_tsv_value,
)
from .output_models import (
    IdentityCoherenceOutputContext,
    IdentityCoherenceOutputRecord,
)
from .output_validation import validate_output_record


@dataclass(frozen=True)
class IdentityCoherenceSummaryModel:
    input_row_count: int
    projected_full_cohort_identity_request_count: int | str
    raw_xic_request_count: int | str
    xic_point_count: int | str
    forbidden_seen_count: int
    tier1_count: int
    tier2_count: int
    tier2_fallback_count: int
    tier3_count: int
    infrastructure_blocked_count: int
    data_quality_reject_count: int
    min_total_coherent_samples: object
    min_non_seed_coherent_samples: object
    min_non_seed_tier12_identity_samples: object
    assessed_non_seed_cell_count: int
    missing_shape_basis_count: int
    missing_width_basis_count: int
    positive_control_pass_fraction: str
    decoy_correctly_rejected_count: int | str
    request_identity_completeness_status_counts: Counter[str]
    request_candidate_identity_status_counts: Counter[str]
    seed_gate_class_counts: Counter[str]
    decision_counts: Counter[str]
    rt_only_cell_identity_tier_counts: Counter[str]
    shape_reference_basis_counts: Counter[str]
    width_status_counts: Counter[str]
    weak_basis_reason_counts: Counter[str]
    control_type_counts: Counter[str]
    control_status_counts: Counter[str]
    control_pass_counts: Counter[str]
    positive_control_mapping_status_counts: Counter[str]
    decoy_generation_method_counts: Counter[str]
    control_failure_reason_counts: Counter[str]
    engineering_go_no_go_rows: tuple[str, ...]


def build_identity_coherence_summary_model(
    records: Sequence[IdentityCoherenceOutputRecord],
    *,
    context: IdentityCoherenceOutputContext,
    control_rows: Sequence[Mapping[str, object]] = (),
) -> IdentityCoherenceSummaryModel:
    validated = tuple(validate_output_record(record) for record in records)
    decision_rows = [record.row_result.decision for record in validated]
    cell_rows = [
        cell
        for record in validated
        for cell in record.row_result.cells
    ]
    request_rows = [record.seed_gate.resolved_request for record in validated]
    positive_rows = [
        row
        for row in control_rows
        if format_tsv_value(row.get("control_type")) == "positive_targeted_istd"
    ]
    positive_pass_count = sum(
        1 for row in positive_rows if control_pass_is_true(row.get("control_pass"))
    )
    decoy_rows = [
        row
        for row in control_rows
        if format_tsv_value(row.get("control_type")) == "identity_decoy"
    ]
    decoy_correctly_rejected_count = sum(
        1 for row in decoy_rows if control_pass_is_true(row.get("control_pass"))
    )
    assessed_sample_total = total_assessed_sample_count(decision_rows)

    return IdentityCoherenceSummaryModel(
        input_row_count=len(validated),
        projected_full_cohort_identity_request_count=_not_assessed_if_none(
            context.projected_full_cohort_identity_request_count
        ),
        raw_xic_request_count=_not_assessed_if_none(context.raw_xic_request_count),
        xic_point_count=_not_assessed_if_none(context.xic_point_count),
        forbidden_seen_count=sum(
            1 for row in decision_rows if row.forbidden_evidence_seen
        ),
        tier1_count=_sum_decision_field(
            decision_rows,
            "tier1_fragment_confirmed_sample_count",
        ),
        tier2_count=_sum_decision_field(
            decision_rows,
            "tier2_shape_supported_sample_count",
        ),
        tier2_fallback_count=_sum_decision_field(
            decision_rows,
            "tier2_seed_shape_fallback_sample_count",
        ),
        tier3_count=_sum_decision_field(
            decision_rows,
            "tier3_width_only_sample_count",
        ),
        infrastructure_blocked_count=_sum_decision_field(
            decision_rows,
            "infrastructure_blocked_sample_count",
        ),
        data_quality_reject_count=_sum_decision_field(
            decision_rows,
            "data_quality_reject_sample_count",
        ),
        min_total_coherent_samples=_first_threshold(
            decision_rows,
            "min_total_coherent_samples",
        ),
        min_non_seed_coherent_samples=_first_threshold(
            decision_rows,
            "min_non_seed_coherent_samples",
        ),
        min_non_seed_tier12_identity_samples=_first_threshold(
            decision_rows,
            "min_non_seed_tier12_identity_samples",
        ),
        assessed_non_seed_cell_count=len(cell_rows),
        missing_shape_basis_count=_cell_status_count(
            cell_rows,
            "shape_status",
            "not_assessed",
        ),
        missing_width_basis_count=_cell_status_count(
            cell_rows,
            "width_status",
            "not_assessed",
        ),
        positive_control_pass_fraction=(
            "not_assessed"
            if not positive_rows
            else f"{positive_pass_count / len(positive_rows):.12g}"
        ),
        decoy_correctly_rejected_count=(
            "not_assessed"
            if not decoy_rows
            else decoy_correctly_rejected_count
        ),
        request_identity_completeness_status_counts=Counter(
            str(enum_value(row.request_identity_completeness_status))
            for row in request_rows
        ),
        request_candidate_identity_status_counts=Counter(
            str(enum_value(row.request_candidate_identity_status))
            for row in request_rows
        ),
        seed_gate_class_counts=Counter(
            str(enum_value(row.seed_gate_class)) for row in decision_rows
        ),
        decision_counts=Counter(
            str(enum_value(row.decision)) for row in decision_rows
        ),
        rt_only_cell_identity_tier_counts=Counter(
            str(enum_value(cell.cell_identity_tier))
            for cell in cell_rows
            if enum_value(cell.cell_identity_tier) == "rt_only"
        ),
        shape_reference_basis_counts=Counter(
            str(enum_value(cell.shape_reference_basis))
            for cell in cell_rows
        ),
        width_status_counts=Counter(
            str(enum_value(cell.width_status)) for cell in cell_rows
        ),
        weak_basis_reason_counts=Counter(
            str(enum_value(row.weak_basis_reason)) for row in decision_rows
        ),
        control_type_counts=Counter(
            format_tsv_value(row.get("control_type")) for row in control_rows
        ),
        control_status_counts=Counter(
            format_tsv_value(row.get("control_status")) for row in control_rows
        ),
        control_pass_counts=Counter(
            format_tsv_value(row.get("control_pass")) for row in control_rows
        ),
        positive_control_mapping_status_counts=Counter(
            format_tsv_value(row.get("positive_control_mapping_status"))
            for row in control_rows
        ),
        decoy_generation_method_counts=Counter(
            format_tsv_value(row.get("decoy_generation_method"))
            for row in decoy_rows
        ),
        control_failure_reason_counts=Counter(
            format_tsv_value(row.get("control_failure_reason"))
            for row in control_rows
            if format_tsv_value(row.get("control_failure_reason"))
        ),
        engineering_go_no_go_rows=tuple(
            engineering_go_no_go_rows(
                decision_rows,
                context=context,
                assessed_sample_total=assessed_sample_total,
            )
        ),
    )


def engineering_go_no_go_rows(
    decision_rows: Sequence[IdentityDecisionSummary],
    *,
    context: IdentityCoherenceOutputContext,
    assessed_sample_total: int,
) -> list[str]:
    blocked_count = _sum_decision_field(
        decision_rows,
        "infrastructure_blocked_sample_count",
    )
    blocked_fraction = (
        0.0 if assessed_sample_total <= 0 else blocked_count / assessed_sample_total
    )
    rows = [
        "| Check | Decision | Basis |",
        "| --- | --- | --- |",
        (
            "| evidence_firewall | Proceed | "
            "`promotion_used_forbidden_evidence = false` |"
        ),
        _status_row(
            "firewall_fixture",
            context.firewall_fixture_status,
            pass_basis="firewall A/B fixture passed",
        ),
        _status_row(
            "spawn_payload_smoke",
            context.spawn_payload_smoke_status,
            pass_basis="spawn payload smoke passed",
        ),
    ]
    if assessed_sample_total <= 0:
        rows.append(
            "| infrastructure_blocked_fraction | Not assessed | "
            "`assessed_sample_total` not assessed |"
        )
    else:
        max_blocked_fraction = context.max_infrastructure_blocked_fraction
        blocked_basis = (
            f"`{blocked_count} / {assessed_sample_total} = "
            f"{blocked_fraction:.12g}`"
        )
        if blocked_fraction <= max_blocked_fraction:
            rows.append(
                "| infrastructure_blocked_fraction | Proceed | "
                f"{blocked_basis} <= `{max_blocked_fraction}` |"
            )
        else:
            rows.append(
                "| infrastructure_blocked_fraction | Pivot | "
                f"{blocked_basis} exceeds `{max_blocked_fraction}` |"
            )

    projected = context.projected_full_cohort_identity_request_count
    max_projected = context.max_projected_full_cohort_identity_xic_requests
    if projected is None:
        rows.append(
            "| projected_full_cohort_identity_xic_requests | No-Go for full cohort | "
            "`projected_full_cohort_identity_request_count` not assessed |"
        )
    elif max_projected is None:
        rows.append(
            "| projected_full_cohort_identity_xic_requests | No-Go for full cohort | "
            "`max_projected_full_cohort_identity_xic_requests` not provided |"
        )
    elif projected <= max_projected:
        rows.append(
            "| projected_full_cohort_identity_xic_requests | Proceed | "
            f"`{projected}` <= `{max_projected}` |"
        )
    else:
        rows.append(
            "| projected_full_cohort_identity_xic_requests | Pivot | "
            f"`{projected}` exceeds `{max_projected}` |"
        )
    rows.append("")
    return rows


def total_assessed_sample_count(
    decision_rows: Sequence[IdentityDecisionSummary],
) -> int:
    return sum(_assessed_sample_count_for_decision(row) for row in decision_rows)


def _not_assessed_if_none(value: int | None) -> int | str:
    if value is None:
        return "not_assessed"
    return value


def _assessed_sample_count_for_decision(
    row: IdentityDecisionSummary,
) -> int:
    if row.coherent_fraction in {None, 0}:
        return 0
    coherent_fraction = row.coherent_fraction
    if coherent_fraction is None:
        return 0
    return max(1, round(row.total_coherent_sample_count / coherent_fraction))


def _status_row(name: str, status: str, *, pass_basis: str) -> str:
    normalized = "" if status is None else str(status).strip()
    if normalized == "pass":
        return f"| {name} | Proceed | {pass_basis} |"
    if normalized == "not_assessed" or not normalized:
        return f"| {name} | Not assessed | `{name}` not assessed |"
    return f"| {name} | Pivot | `{normalized}` |"


def _sum_decision_field(
    rows: Sequence[IdentityDecisionSummary],
    field_name: str,
) -> int:
    return sum(int(getattr(row, field_name)) for row in rows)


def _first_threshold(
    rows: Sequence[IdentityDecisionSummary],
    field_name: str,
) -> object:
    if not rows:
        return "not_assessed"
    values = {getattr(row, field_name) for row in rows}
    if len(values) != 1:
        raise ValueError(f"mixed {field_name} values in summary rows")
    return getattr(rows[0], field_name)


def _cell_status_count(
    rows: list[CellEvidenceResult],
    field_name: str,
    value: str,
) -> int:
    return sum(
        1 for row in rows
        if enum_value(getattr(row, field_name)) == value
    )
