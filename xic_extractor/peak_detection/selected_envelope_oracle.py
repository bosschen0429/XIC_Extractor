from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.peak_detection.selected_envelope import (
    SelectedEnvelopeBoundaryEvaluation,
    TraceInterval,
)

OracleStatus = Literal["expert_reviewed", "benchmark_control_only"]
OracleSource = Literal[
    "manual_overlay",
    "expert_overlay",
    "manual_2raw",
    "targeted_workbook_control",
]
OracleWinner = Literal[
    "selected_envelope",
    "resolver_interval",
    "tie",
    "not_assessed",
]
OracleVerdict = Literal[
    "selected_envelope_closer",
    "resolver_interval_closer",
    "tie",
    "benchmark_control_only",
]
OracleGateDecision = Literal["promote", "no_go", "externalize", "defer"]


@dataclass(frozen=True)
class BoundaryOracle:
    oracle_row_id: str
    selected_candidate_id: str
    oracle_status: OracleStatus
    oracle_source: OracleSource
    rt_start_min: float
    rt_end_min: float
    area_baseline_corrected: float | None
    shape_class: str
    acceptable_boundary_delta_min: float = 0.1
    acceptable_area_relative_error: float = 0.1
    required_plot_path: str = ""


@dataclass(frozen=True)
class SelectedEnvelopeOracleComparison:
    oracle_row_id: str
    selected_candidate_id: str
    oracle_status: OracleStatus
    oracle_source: OracleSource
    shape_class: str
    resolver_boundary_error_min: float | None
    selected_envelope_boundary_error_min: float | None
    resolver_area_relative_error: float | None
    selected_envelope_area_relative_error: float | None
    selected_envelope_boundary_within_tolerance: bool | None
    selected_envelope_area_within_tolerance: bool | None
    boundary_winner: OracleWinner
    area_winner: OracleWinner
    verdict: OracleVerdict
    required_plot_path: str


def compare_selected_envelope_to_oracle(
    evaluation: SelectedEnvelopeBoundaryEvaluation,
    oracle: BoundaryOracle,
) -> SelectedEnvelopeOracleComparison:
    if not oracle.oracle_row_id:
        raise ValueError("oracle_row_id is required")
    if evaluation.selected_candidate_id != oracle.selected_candidate_id:
        raise ValueError("selected_candidate_id mismatch for boundary oracle")
    _validate_oracle_status_source(oracle)

    if oracle.oracle_status == "benchmark_control_only":
        return SelectedEnvelopeOracleComparison(
            oracle_row_id=oracle.oracle_row_id,
            selected_candidate_id=oracle.selected_candidate_id,
            oracle_status=oracle.oracle_status,
            oracle_source=oracle.oracle_source,
            shape_class=oracle.shape_class,
            resolver_boundary_error_min=None,
            selected_envelope_boundary_error_min=None,
            resolver_area_relative_error=None,
            selected_envelope_area_relative_error=None,
            selected_envelope_boundary_within_tolerance=None,
            selected_envelope_area_within_tolerance=None,
            boundary_winner="not_assessed",
            area_winner="not_assessed",
            verdict="benchmark_control_only",
            required_plot_path=oracle.required_plot_path,
        )

    resolver_boundary_error = _boundary_error_min(
        evaluation.resolver_interval,
        oracle,
    )
    selected_boundary_error = _boundary_error_min(
        evaluation.selected_envelope_interval,
        oracle,
    )
    resolver_area_error = _relative_area_error(
        evaluation.asls_area_old_interval,
        oracle.area_baseline_corrected,
    )
    selected_area_error = _relative_area_error(
        evaluation.asls_area_selected_envelope,
        oracle.area_baseline_corrected,
    )
    boundary_winner = _winner(
        selected_boundary_error,
        resolver_boundary_error,
        selected_label="selected_envelope",
        resolver_label="resolver_interval",
    )
    area_winner = _winner(
        selected_area_error,
        resolver_area_error,
        selected_label="selected_envelope",
        resolver_label="resolver_interval",
    )
    return SelectedEnvelopeOracleComparison(
        oracle_row_id=oracle.oracle_row_id,
        selected_candidate_id=oracle.selected_candidate_id,
        oracle_status=oracle.oracle_status,
        oracle_source=oracle.oracle_source,
        shape_class=oracle.shape_class,
        resolver_boundary_error_min=resolver_boundary_error,
        selected_envelope_boundary_error_min=selected_boundary_error,
        resolver_area_relative_error=resolver_area_error,
        selected_envelope_area_relative_error=selected_area_error,
        selected_envelope_boundary_within_tolerance=(
            selected_boundary_error <= oracle.acceptable_boundary_delta_min
        ),
        selected_envelope_area_within_tolerance=(
            selected_area_error is not None
            and selected_area_error <= oracle.acceptable_area_relative_error
        ),
        boundary_winner=boundary_winner,
        area_winner=area_winner,
        verdict=_verdict(boundary_winner, area_winner),
        required_plot_path=oracle.required_plot_path,
    )


def build_selected_envelope_oracle_manifest(
    comparisons: tuple[SelectedEnvelopeOracleComparison, ...],
) -> dict[str, str]:
    expert_rows = tuple(
        comparison
        for comparison in comparisons
        if comparison.oracle_status == "expert_reviewed"
    )
    benchmark_rows = tuple(
        comparison
        for comparison in comparisons
        if comparison.oracle_status == "benchmark_control_only"
    )
    if not expert_rows:
        return {
            "gate_decision": "defer",
            "expert_oracle_row_count": "0",
            "benchmark_control_row_count": str(len(benchmark_rows)),
            "selected_envelope_closer_count": "0",
            "resolver_interval_closer_count": "0",
            "blocked_reasons": "no_boundary_oracle_rows",
            "next_gate": "bounded_follow_up_required",
        }
    resolver_closer_count = sum(
        1
        for comparison in expert_rows
        if comparison.verdict == "resolver_interval_closer"
    )
    selected_closer_count = sum(
        1
        for comparison in expert_rows
        if comparison.verdict == "selected_envelope_closer"
    )
    selected_supported_count = sum(
        1 for comparison in expert_rows if _supports_selected_envelope(comparison)
    )
    if resolver_closer_count:
        decision: OracleGateDecision = "no_go"
        blocked_reasons = "resolver_interval_closer_to_oracle"
    elif selected_supported_count == 0:
        decision = "defer"
        blocked_reasons = "no_selected_envelope_oracle_support"
    else:
        decision = "promote"
        blocked_reasons = ""
    return {
        "gate_decision": decision,
        "expert_oracle_row_count": str(len(expert_rows)),
        "benchmark_control_row_count": str(len(benchmark_rows)),
        "selected_envelope_closer_count": str(selected_closer_count),
        "resolver_interval_closer_count": str(resolver_closer_count),
        "blocked_reasons": blocked_reasons,
        "next_gate": _next_gate(decision),
    }


def _supports_selected_envelope(
    comparison: SelectedEnvelopeOracleComparison,
) -> bool:
    return (
        comparison.verdict == "selected_envelope_closer"
        and comparison.selected_envelope_boundary_within_tolerance is True
        and comparison.selected_envelope_area_within_tolerance is True
    )


def _validate_oracle_status_source(oracle: BoundaryOracle) -> None:
    if oracle.oracle_status == "benchmark_control_only":
        if oracle.oracle_source != "targeted_workbook_control":
            raise ValueError(
                "benchmark_control_only rows must use targeted_workbook_control"
            )
        return
    if oracle.oracle_source == "targeted_workbook_control":
        raise ValueError("targeted workbook area cannot be a boundary oracle")


def _boundary_error_min(interval: TraceInterval, oracle: BoundaryOracle) -> float:
    return abs(interval.rt_start_min - oracle.rt_start_min) + abs(
        interval.rt_end_min - oracle.rt_end_min
    )


def _relative_area_error(
    value: float | None,
    oracle_area: float | None,
) -> float | None:
    if value is None or oracle_area is None or oracle_area <= 0.0:
        return None
    return abs(value - oracle_area) / oracle_area


def _winner(
    selected_error: float | None,
    resolver_error: float | None,
    *,
    selected_label: Literal["selected_envelope"],
    resolver_label: Literal["resolver_interval"],
) -> OracleWinner:
    if selected_error is None or resolver_error is None:
        return "not_assessed"
    if selected_error < resolver_error:
        return selected_label
    if resolver_error < selected_error:
        return resolver_label
    return "tie"


def _verdict(
    boundary_winner: OracleWinner,
    area_winner: OracleWinner,
) -> OracleVerdict:
    if boundary_winner == "resolver_interval" or area_winner == "resolver_interval":
        return "resolver_interval_closer"
    if boundary_winner == "selected_envelope" or area_winner == "selected_envelope":
        return "selected_envelope_closer"
    return "tie"


def _next_gate(decision: OracleGateDecision) -> str:
    if decision == "promote":
        return "changed_row_review"
    if decision == "no_go":
        return "stop_selected_envelope_product_path"
    if decision == "externalize":
        return "diagnostic_review_only"
    return "bounded_follow_up_required"
