from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.output_rows import escape_excel_formula
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    OwnerAssignment,
)
from xic_extractor.tabular_io import write_tsv


def write_event_to_ms1_owner_tsv(
    path: Path,
    assignments: tuple[OwnerAssignment, ...],
) -> Path:
    rows = [
        {
            "candidate_id": assignment.candidate_id,
            "owner_id": assignment.owner_id or "",
            "assignment_status": assignment.assignment_status,
            "reason": assignment.reason,
        }
        for assignment in assignments
    ]
    return _write_tsv(
        path,
        ("candidate_id", "owner_id", "assignment_status", "reason"),
        rows,
    )


def write_ambiguous_ms1_owners_tsv(
    path: Path,
    records: tuple[AmbiguousOwnerRecord, ...],
) -> Path:
    rows = [
        {
            "ambiguity_id": record.ambiguity_id,
            "sample_stem": record.sample_stem,
            "candidate_ids": ";".join(record.candidate_ids),
            "reason": record.reason,
        }
        for record in records
    ]
    return _write_tsv(
        path,
        ("ambiguity_id", "sample_stem", "candidate_ids", "reason"),
        rows,
    )


def write_owner_edge_evidence_tsv(
    path: Path,
    edges: Sequence[OwnerEdgeEvidence],
) -> Path:
    fieldnames = (
        "left_owner_id",
        "right_owner_id",
        "left_sample_stem",
        "right_sample_stem",
        "neutral_loss_tag",
        "left_precursor_mz",
        "right_precursor_mz",
        "left_rt_min",
        "right_rt_min",
        "decision",
        "failure_reason",
        "rt_raw_delta_sec",
        "rt_drift_corrected_delta_sec",
        "drift_prior_source",
        "injection_order_gap",
        "owner_quality",
        "seed_support_level",
        "duplicate_context",
        "score",
        "reason",
        "rt_basis",
    )
    rows = [
        {
            "left_owner_id": edge.left_owner_id,
            "right_owner_id": edge.right_owner_id,
            "left_sample_stem": edge.left_sample_stem,
            "right_sample_stem": edge.right_sample_stem,
            "neutral_loss_tag": edge.neutral_loss_tag,
            "left_precursor_mz": _format_float(edge.left_precursor_mz),
            "right_precursor_mz": _format_float(edge.right_precursor_mz),
            "left_rt_min": _format_float(edge.left_rt_min),
            "right_rt_min": _format_float(edge.right_rt_min),
            "decision": edge.decision,
            "failure_reason": edge.failure_reason,
            "rt_raw_delta_sec": _format_float(edge.rt_raw_delta_sec),
            "rt_drift_corrected_delta_sec": _format_optional_float(
                edge.rt_drift_corrected_delta_sec,
            ),
            "drift_prior_source": edge.drift_prior_source,
            "rt_basis": edge.rt_basis,
            "injection_order_gap": (
                ""
                if edge.injection_order_gap is None
                else str(edge.injection_order_gap)
            ),
            "owner_quality": edge.owner_quality,
            "seed_support_level": edge.seed_support_level,
            "duplicate_context": edge.duplicate_context,
            "score": str(edge.score),
            "reason": edge.reason,
        }
        for edge in edges
    ]
    return _write_tsv(path, fieldnames, rows)


def _format_float(value: float) -> str:
    return f"{value:.6g}"


def _format_optional_float(value: float | None) -> str:
    return "" if value is None else _format_float(value)


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        rows,
        fieldnames,
        formatter=escape_excel_formula,
        lineterminator="\n",
    )
    return path
