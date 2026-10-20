"""Diagnostic-only evidence gate for product-retained backfill families."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.diagnostics.backfill_overlay import (
    selected_overlay_row as select_backfill_overlay_row,
)
from xic_extractor.tabular_io import (
    bool_value,
    file_sha256,
    optional_int,
    read_tsv_required,
    rows_by_text_field,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "retained_backfill_evidence_gate"
SUPPORT_OVERLAY_VERDICT = "ms1_shape_supports_family_backfill"
MACHINE_SUPPORT_COMPONENT = "high_detected_anchor_low_rescue_machine_support"
MACHINE_SUPPORT_DETECTED_MIN = 20
MACHINE_SUPPORT_BACKFILL_FRACTION_MAX = 0.10
_SAFE_PREFIX_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")

EvidenceGateStatus = Literal[
    "visual_support",
    "machine_support_no_overlay",
    "evidence_conflict",
    "evidence_missing",
    "evidence_inconclusive",
]
RecommendedAction = Literal[
    "track_supported_backfill",
    "track_machine_supported_backfill",
    "review_product_backfill",
    "generate_missing_evidence",
    "review_inconclusive_evidence",
]

RETAINED_BACKFILL_EVIDENCE_GATE_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "seed_group_id",
    "seed_group_basis",
    "seed_mz",
    "seed_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "ppm",
    "product_behavior_state",
    "evidence_gate_status",
    "recommended_action",
    "diagnostic_authority",
    "detected_cell_count",
    "rescued_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_reason",
    "row_flags",
    "support_components",
    "dependent_context",
    "challenge_blockers",
    "missing_evidence",
    "overlay_family_verdict",
    "overlay_png_path",
    "seed_source_samples",
    "source_review_artifact",
    "source_review_sha256",
    "source_cell_artifact",
    "source_cell_sha256",
    "source_matrix_artifact",
    "source_matrix_sha256",
    "source_seed_audit_artifact",
    "source_seed_audit_sha256",
    "source_overlay_artifacts",
    "source_overlay_sha256s",
)
MISSING_OVERLAY_QUEUE_COLUMNS = (
    "rank",
    "feature_family_id",
    "seed_group_id",
    "family_center_mz",
    "family_center_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "suggested_output_prefix",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
    "ppm",
    "product_behavior_state",
    "evidence_gate_status",
    "recommended_action",
    "detected_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "rescued_cell_count",
    "seed_source_samples",
    "row_flags",
    "missing_evidence",
    "suggested_overlay_command_args",
)

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_center_mz",
    "family_center_rt",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_reason",
    "primary_evidence",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "row_flags",
)
CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "gap_fill_state",
    "gap_fill_reason",
    "trace_quality",
    "reason",
)
SEED_AUDIT_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
)
OVERLAY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_verdict",
    "png_path",
)
MATRIX_REQUIRED_COLUMNS: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetainedBackfillGateSourceContext:
    review_path: Path
    review_sha256: str
    cell_path: Path
    cell_sha256: str
    matrix_path: Path
    matrix_sha256: str
    seed_audit_path: Path | None = None
    seed_audit_sha256: str = ""
    overlay_paths: tuple[Path, ...] = ()
    overlay_sha256s: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetainedBackfillGateRow:
    feature_family_id: str
    seed_group_id: str
    seed_group_basis: str
    seed_mz: str
    seed_rt: str
    suggested_rt_min: str
    suggested_rt_max: str
    ppm: str
    family_center_mz: str
    family_center_rt: str
    product_behavior_state: str
    evidence_gate_status: EvidenceGateStatus
    recommended_action: RecommendedAction
    detected_cell_count: int
    backfilled_cell_count: int
    accepted_backfill_count: int
    review_backfill_count: int
    include_in_primary_matrix: bool
    identity_decision: str
    identity_reason: str
    row_flags: tuple[str, ...]
    support_components: tuple[str, ...]
    dependent_context: tuple[str, ...]
    challenge_blockers: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    overlay_family_verdict: str
    overlay_png_path: str
    seed_source_samples: tuple[str, ...]
    source_context: RetainedBackfillGateSourceContext


@dataclass(frozen=True)
class RetainedBackfillGateIndex:
    rows: tuple[RetainedBackfillGateRow, ...]
    summary: dict[str, object]


@dataclass(frozen=True)
class RetainedBackfillGateOutputs:
    tsv: Path
    json: Path
    missing_overlay_queue_tsv: Path
    review_overlay_queue_tsv: Path


@dataclass(frozen=True)
class _SeedGroup:
    seed_group_id: str
    seed_group_basis: str
    samples: tuple[str, ...]
    seed_mz: str = ""
    seed_rt: str = ""
    rt_min: str = ""
    rt_max: str = ""
    ppm: str = ""


def source_context_for_artifacts(
    *,
    review_path: Path,
    cell_path: Path,
    matrix_path: Path,
    seed_audit_path: Path | None = None,
    overlay_paths: Sequence[Path] = (),
) -> RetainedBackfillGateSourceContext:
    return RetainedBackfillGateSourceContext(
        review_path=review_path,
        review_sha256=file_sha256(review_path),
        cell_path=cell_path,
        cell_sha256=file_sha256(cell_path),
        matrix_path=matrix_path,
        matrix_sha256=file_sha256(matrix_path),
        seed_audit_path=seed_audit_path,
        seed_audit_sha256=(
            file_sha256(seed_audit_path) if seed_audit_path is not None else ""
        ),
        overlay_paths=tuple(overlay_paths),
        overlay_sha256s=tuple(file_sha256(path) for path in overlay_paths),
    )


def run_retained_backfill_evidence_gate(
    *,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    alignment_matrix_tsv: Path,
    output_dir: Path,
    backfill_seed_audit_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    source_run_id: str = "",
) -> RetainedBackfillGateOutputs:
    review_rows = _read_required_tsv(alignment_review_tsv, REVIEW_REQUIRED_COLUMNS)
    cell_rows = _read_required_tsv(alignment_cells_tsv, CELL_REQUIRED_COLUMNS)
    _read_required_tsv(alignment_matrix_tsv, MATRIX_REQUIRED_COLUMNS)
    seed_rows = (
        _read_required_tsv(backfill_seed_audit_tsv, SEED_AUDIT_REQUIRED_COLUMNS)
        if backfill_seed_audit_tsv is not None
        else ()
    )
    overlay_rows: list[dict[str, str]] = []
    for path in overlay_batch_summary_tsvs:
        overlay_rows.extend(_read_required_tsv(path, OVERLAY_REQUIRED_COLUMNS))
    source_context = source_context_for_artifacts(
        review_path=alignment_review_tsv,
        cell_path=alignment_cells_tsv,
        matrix_path=alignment_matrix_tsv,
        seed_audit_path=backfill_seed_audit_tsv,
        overlay_paths=overlay_batch_summary_tsvs,
    )
    index = build_retained_backfill_gate_index(
        review_rows=review_rows,
        cell_rows=cell_rows,
        seed_audit_rows=seed_rows,
        overlay_rows=overlay_rows,
        source_context=source_context,
        source_run_id=source_run_id,
    )
    return write_retained_backfill_gate_outputs(output_dir, index)


def build_retained_backfill_gate_index(
    *,
    review_rows: Iterable[Mapping[str, str]],
    cell_rows: Iterable[Mapping[str, str]],
    seed_audit_rows: Iterable[Mapping[str, str]] = (),
    overlay_rows: Iterable[Mapping[str, str]] = (),
    source_context: RetainedBackfillGateSourceContext,
    source_run_id: str = "",
) -> RetainedBackfillGateIndex:
    reviews = [dict(row) for row in review_rows]
    cells = [dict(row) for row in cell_rows]
    seeds = [dict(row) for row in seed_audit_rows]
    overlays = [dict(row) for row in overlay_rows]

    cells_by_family = _group_by_family(cells)
    seed_groups_by_family = _seed_groups_by_family(seeds)
    overlays_by_family = _group_by_family(overlays)
    excluded_family_counts: Counter[str] = Counter()

    gate_rows: list[RetainedBackfillGateRow] = []
    for review in reviews:
        family_id = text_value(review.get("feature_family_id"))
        if not family_id:
            continue
        family_cells = tuple(cells_by_family.get(family_id, ()))
        if _is_detected_zero_backfill_context(review, family_cells):
            excluded_family_counts["detected_zero_family"] += 1
            continue
        if not _is_retained_product_backfill(review, family_cells):
            continue
        if _count_cells(family_cells, "detected") <= 0:
            excluded_family_counts["detected_cell_join_mismatch"] += 1
            continue
        seed_groups = seed_groups_by_family.get(
            family_id,
            (_missing_seed_group(family_id),),
        )
        for seed_group in seed_groups:
            gate_rows.append(
                _evaluate_group(
                    review=review,
                    family_cells=family_cells,
                    seed_group=seed_group,
                    overlay_rows=overlays_by_family.get(family_id, ()),
                    source_context=source_context,
                ),
            )

    gate_rows = sorted(gate_rows, key=_row_sort_key)
    summary = _summary(
        gate_rows,
        excluded_family_counts=excluded_family_counts,
        source_context=source_context,
        source_run_id=source_run_id,
    )
    return RetainedBackfillGateIndex(rows=tuple(gate_rows), summary=summary)


def write_retained_backfill_gate_outputs(
    output_dir: Path,
    index: RetainedBackfillGateIndex,
) -> RetainedBackfillGateOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "alignment_retained_backfill_evidence_gate.tsv"
    json_path = output_dir / "alignment_retained_backfill_evidence_gate.json"
    queue_path = output_dir / "alignment_retained_backfill_missing_overlay_queue.tsv"
    review_queue_path = (
        output_dir / "alignment_retained_backfill_overlay_review_queue.tsv"
    )
    write_tsv(
        tsv_path,
        [_row_as_mapping(row) for row in index.rows],
        RETAINED_BACKFILL_EVIDENCE_GATE_COLUMNS,
        lineterminator="\n",
    )
    queue_candidates = _missing_overlay_queue_candidates(index.rows)
    queue_rows = _missing_overlay_queue_rows(queue_candidates)
    write_tsv(
        queue_path,
        queue_rows,
        MISSING_OVERLAY_QUEUE_COLUMNS,
        lineterminator="\n",
    )
    review_queue_rows = _review_overlay_queue_rows(queue_candidates)
    write_tsv(
        review_queue_path,
        review_queue_rows,
        MISSING_OVERLAY_QUEUE_COLUMNS,
        lineterminator="\n",
    )
    summary = dict(index.summary)
    summary["missing_overlay_queue_count"] = len(queue_rows)
    summary["review_overlay_queue_count"] = len(review_queue_rows)
    json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return RetainedBackfillGateOutputs(
        tsv=tsv_path,
        json=json_path,
        missing_overlay_queue_tsv=queue_path,
        review_overlay_queue_tsv=review_queue_path,
    )


def _evaluate_group(
    *,
    review: Mapping[str, str],
    family_cells: Sequence[Mapping[str, str]],
    seed_group: _SeedGroup,
    overlay_rows: Sequence[Mapping[str, str]],
    source_context: RetainedBackfillGateSourceContext,
) -> RetainedBackfillGateRow:
    family_id = text_value(review.get("feature_family_id"))
    selected_overlay = _selected_overlay_row(
        overlay_rows,
        seed_group_id=seed_group.seed_group_id,
    )
    overlay_verdict = text_value(selected_overlay.get("family_verdict"))
    support_components: list[str] = []
    dependent_context: list[str] = ["retained_product_backfill"]
    challenge_blockers: list[str] = []
    missing_evidence: list[str] = []

    if seed_group.seed_group_basis == "seed_audit":
        support_components.append("seed_request_provenance")
    else:
        missing_evidence.append("missing_seed_provenance")
    if _has_product_cell_backfill_context(family_cells):
        dependent_context.append("product_cell_backfill_context")
    dependent_context.extend(_review_context_labels(review))

    if not selected_overlay:
        if _has_machine_support_without_overlay(review, family_cells):
            support_components.append(MACHINE_SUPPORT_COMPONENT)
            dependent_context.append("overlay_not_required_machine_supported")
        else:
            missing_evidence.append("missing_overlay_evidence")
    elif overlay_verdict == SUPPORT_OVERLAY_VERDICT:
        support_components.append(SUPPORT_OVERLAY_VERDICT)
    elif overlay_verdict:
        challenge_blockers.append(overlay_verdict)
    else:
        missing_evidence.append("missing_overlay_family_verdict")

    status = _status(
        support_components=support_components,
        challenge_blockers=challenge_blockers,
        missing_evidence=missing_evidence,
    )
    return RetainedBackfillGateRow(
        feature_family_id=family_id,
        seed_group_id=seed_group.seed_group_id,
        seed_group_basis=seed_group.seed_group_basis,
        seed_mz=seed_group.seed_mz,
        seed_rt=seed_group.seed_rt,
        suggested_rt_min=seed_group.rt_min,
        suggested_rt_max=seed_group.rt_max,
        ppm=seed_group.ppm,
        family_center_mz=text_value(review.get("family_center_mz")),
        family_center_rt=text_value(review.get("family_center_rt")),
        product_behavior_state=_product_behavior_state(review),
        evidence_gate_status=status,
        recommended_action=_recommended_action(status),
        detected_cell_count=_count_cells(family_cells, "detected"),
        backfilled_cell_count=_count_cells(family_cells, "backfilled"),
        accepted_backfill_count=_int_or_zero(review.get("accepted_backfill_count")),
        review_backfill_count=_int_or_zero(review.get("review_backfill_count")),
        include_in_primary_matrix=bool_value(
            review.get("include_in_primary_matrix"),
        )
        is True,
        identity_decision=text_value(review.get("identity_decision")),
        identity_reason=text_value(review.get("identity_reason")),
        row_flags=tuple(split_semicolon_labels(review.get("row_flags"))),
        support_components=_ordered_unique(support_components),
        dependent_context=_ordered_unique(dependent_context),
        challenge_blockers=_ordered_unique(challenge_blockers),
        missing_evidence=_ordered_unique(missing_evidence),
        overlay_family_verdict=overlay_verdict,
        overlay_png_path=text_value(selected_overlay.get("png_path")),
        seed_source_samples=seed_group.samples,
        source_context=source_context,
    )


def _status(
    *,
    support_components: Sequence[str],
    challenge_blockers: Sequence[str],
    missing_evidence: Sequence[str],
) -> EvidenceGateStatus:
    if challenge_blockers:
        return "evidence_conflict"
    if missing_evidence:
        return "evidence_missing"
    if SUPPORT_OVERLAY_VERDICT in support_components:
        return "visual_support"
    if MACHINE_SUPPORT_COMPONENT in support_components:
        return "machine_support_no_overlay"
    return "evidence_inconclusive"


def _recommended_action(status: EvidenceGateStatus) -> RecommendedAction:
    if status == "visual_support":
        return "track_supported_backfill"
    if status == "machine_support_no_overlay":
        return "track_machine_supported_backfill"
    if status == "evidence_conflict":
        return "review_product_backfill"
    if status == "evidence_missing":
        return "generate_missing_evidence"
    return "review_inconclusive_evidence"


def _is_retained_product_backfill(
    review: Mapping[str, str],
    family_cells: Sequence[Mapping[str, str]],
) -> bool:
    if bool_value(review.get("include_in_primary_matrix")) is not True:
        return False
    if text_value(review.get("identity_decision")) != "production_family":
        return False
    if _detected_count(review) <= 0:
        return False
    if any(
        _int_or_zero(review.get(field)) > 0
        for field in (
            "quantifiable_backfill_count",
            "accepted_backfill_count",
            "review_backfill_count",
        )
    ):
        return True
    return any(text_value(cell.get("status")) == "backfilled" for cell in family_cells)


def _has_machine_support_without_overlay(
    review: Mapping[str, str],
    family_cells: Sequence[Mapping[str, str]],
) -> bool:
    if _int_or_zero(review.get("review_backfill_count")) > 0:
        return False
    if _overlay_required_by_review_flags(review):
        return False
    detected_count = max(
        _detected_count(review),
        _count_cells(family_cells, "detected"),
    )
    backfilled_count = max(
        _int_or_zero(review.get("quantifiable_backfill_count")),
        _int_or_zero(review.get("accepted_backfill_count")),
        _count_cells(family_cells, "backfilled"),
    )
    if detected_count < MACHINE_SUPPORT_DETECTED_MIN:
        return False
    backfill_limit = max(
        5,
        int(round(detected_count * MACHINE_SUPPORT_BACKFILL_FRACTION_MAX)),
    )
    return backfilled_count <= backfill_limit


def _overlay_required_by_review_flags(review: Mapping[str, str]) -> bool:
    flags = set(split_semicolon_labels(review.get("row_flags")))
    overlay_required_flags = {
        "backfill_cell_evidence_required",
        "backfill_rescue_review_only",
        "high_backfill_dependency",
        "missing_independent_backfill_identity_evidence",
        "provisional_retention_candidate",
        "rescue_heavy",
        "single_detected_seed",
    }
    return bool(flags & overlay_required_flags)


def _is_detected_zero_backfill_context(
    review: Mapping[str, str],
    family_cells: Sequence[Mapping[str, str]],
) -> bool:
    if _detected_count(review) != 0:
        return False
    if any(
        _int_or_zero(review.get(field)) > 0
        for field in (
            "quantifiable_backfill_count",
            "accepted_backfill_count",
            "review_backfill_count",
        )
    ):
        return True
    return any(text_value(cell.get("status")) == "backfilled" for cell in family_cells)


def _product_behavior_state(review: Mapping[str, str]) -> str:
    if _int_or_zero(review.get("accepted_backfill_count")) > 0:
        return "product_primary_backfill_accepted"
    if _int_or_zero(review.get("review_backfill_count")) > 0:
        return "product_primary_backfill_review_only"
    return "product_primary_backfilled"


def _review_context_labels(review: Mapping[str, str]) -> tuple[str, ...]:
    labels: list[str] = []
    flags = set(split_semicolon_labels(review.get("row_flags")))
    if "backfill_cell_evidence_required" in flags:
        labels.append("backfill_cell_evidence_required")
    if "backfill_rescue_review_only" in flags:
        labels.append("backfill_rescue_review_only")
    if "missing_independent_backfill_identity_evidence" in flags:
        labels.append("missing_independent_backfill_identity_evidence")
    if text_value(review.get("identity_reason")) == (
        "primary_identity_retained_backfill_review_only"
    ):
        labels.append("primary_identity_retained_backfill_review_only")
    return tuple(labels)


def _has_product_cell_backfill_context(
    family_cells: Sequence[Mapping[str, str]],
) -> bool:
    for cell in family_cells:
        values = (
            cell.get("status"),
            cell.get("gap_fill_state"),
            cell.get("gap_fill_reason"),
            cell.get("trace_quality"),
            cell.get("primary_matrix_area_source"),
            cell.get("primary_matrix_area_reason"),
            cell.get("reason"),
        )
        if any("backfill" in text_value(value).lower() for value in values):
            return True
        if text_value(cell.get("status")) == "backfilled":
            return True
    return False


def _seed_groups_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[_SeedGroup, ...]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    basis_by_group: dict[str, str] = {}
    key_by_group: dict[str, dict[str, str]] = {}
    for row in rows:
        family_id = text_value(row.get("feature_family_id"))
        if not family_id:
            continue
        seed_group_id = _seed_group_id(row)
        grouped.setdefault(family_id, {}).setdefault(seed_group_id, set()).add(
            text_value(row.get("sample_stem")),
        )
        basis_by_group[seed_group_id] = "seed_audit"
        key_by_group.setdefault(
            seed_group_id,
            {
                "seed_mz": text_value(row.get("backfill_seed_mz")),
                "seed_rt": text_value(row.get("backfill_seed_rt")),
                "rt_min": text_value(row.get("backfill_request_rt_min")),
                "rt_max": text_value(row.get("backfill_request_rt_max")),
                "ppm": text_value(row.get("backfill_request_ppm")),
            },
        )
    result: dict[str, tuple[_SeedGroup, ...]] = {}
    for family_id, samples_by_group in grouped.items():
        result[family_id] = tuple(
            _SeedGroup(
                seed_group_id=seed_group_id,
                seed_group_basis=basis_by_group.get(seed_group_id, "seed_audit"),
                samples=tuple(sorted(sample for sample in samples if sample)),
                seed_mz=key_by_group[seed_group_id]["seed_mz"],
                seed_rt=key_by_group[seed_group_id]["seed_rt"],
                rt_min=key_by_group[seed_group_id]["rt_min"],
                rt_max=key_by_group[seed_group_id]["rt_max"],
                ppm=key_by_group[seed_group_id]["ppm"],
            )
            for seed_group_id, samples in sorted(samples_by_group.items())
        )
    return result


def _seed_group_id(row: Mapping[str, str]) -> str:
    family_id = text_value(row.get("feature_family_id"))
    seed_mz = text_value(row.get("backfill_seed_mz")) or "unknown"
    seed_rt = text_value(row.get("backfill_seed_rt")) or "unknown"
    rt_start = text_value(row.get("backfill_request_rt_min")) or "unknown"
    rt_end = text_value(row.get("backfill_request_rt_max")) or "unknown"
    ppm = text_value(row.get("backfill_request_ppm")) or "unknown"
    return (
        f"seed::{family_id}::mz={seed_mz}::rt={seed_rt}::"
        f"window={rt_start}-{rt_end}::ppm={ppm}"
    )


def _missing_seed_group(family_id: str) -> _SeedGroup:
    return _SeedGroup(
        seed_group_id=f"seed::{family_id}::missing",
        seed_group_basis="missing_seed_audit",
        samples=(),
    )


def _selected_overlay_row(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_group_id: str,
) -> Mapping[str, str]:
    return select_backfill_overlay_row(
        rows,
        seed_group_id=seed_group_id,
        support_verdict=SUPPORT_OVERLAY_VERDICT,
    )


def _group_by_family(
    rows: Iterable[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    return rows_by_text_field(rows, "feature_family_id")


def _summary(
    rows: Sequence[RetainedBackfillGateRow],
    *,
    excluded_family_counts: Counter[str],
    source_context: RetainedBackfillGateSourceContext,
    source_run_id: str,
) -> dict[str, object]:
    status_counts = Counter(row.evidence_gate_status for row in rows)
    action_counts = Counter(row.recommended_action for row in rows)
    family_ids = {row.feature_family_id for row in rows}
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_label": "diagnostic_only",
        "source_run_id": source_run_id,
        "row_count": len(rows),
        "family_count": len(family_ids),
        "seed_group_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "excluded_family_counts": dict(sorted(excluded_family_counts.items())),
        "production_ready": False,
        "matrix_contract_changed": False,
        "source_review_artifact": str(source_context.review_path),
        "source_review_sha256": source_context.review_sha256,
        "source_cell_artifact": str(source_context.cell_path),
        "source_cell_sha256": source_context.cell_sha256,
        "source_matrix_artifact": str(source_context.matrix_path),
        "source_matrix_sha256": source_context.matrix_sha256,
        "source_seed_audit_artifact": (
            str(source_context.seed_audit_path)
            if source_context.seed_audit_path is not None
            else ""
        ),
        "source_seed_audit_sha256": source_context.seed_audit_sha256,
        "source_overlay_artifacts": ";".join(
            str(path) for path in source_context.overlay_paths
        ),
        "source_overlay_sha256s": ";".join(source_context.overlay_sha256s),
    }


def _row_as_mapping(row: RetainedBackfillGateRow) -> dict[str, object]:
    source = row.source_context
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": row.feature_family_id,
        "seed_group_id": row.seed_group_id,
        "seed_group_basis": row.seed_group_basis,
        "seed_mz": row.seed_mz,
        "seed_rt": row.seed_rt,
        "suggested_rt_min": row.suggested_rt_min,
        "suggested_rt_max": row.suggested_rt_max,
        "ppm": row.ppm,
        "product_behavior_state": row.product_behavior_state,
        "evidence_gate_status": row.evidence_gate_status,
        "recommended_action": row.recommended_action,
        "diagnostic_authority": "diagnostic_only",
        "detected_cell_count": row.detected_cell_count,
        "rescued_cell_count": row.backfilled_cell_count,
        "accepted_backfill_count": row.accepted_backfill_count,
        "review_backfill_count": row.review_backfill_count,
        "include_in_primary_matrix": row.include_in_primary_matrix,
        "identity_decision": row.identity_decision,
        "identity_reason": row.identity_reason,
        "row_flags": ";".join(row.row_flags),
        "support_components": ";".join(row.support_components),
        "dependent_context": ";".join(row.dependent_context),
        "challenge_blockers": ";".join(row.challenge_blockers),
        "missing_evidence": ";".join(row.missing_evidence),
        "overlay_family_verdict": row.overlay_family_verdict,
        "overlay_png_path": row.overlay_png_path,
        "seed_source_samples": ";".join(row.seed_source_samples),
        "source_review_artifact": str(source.review_path),
        "source_review_sha256": source.review_sha256,
        "source_cell_artifact": str(source.cell_path),
        "source_cell_sha256": source.cell_sha256,
        "source_matrix_artifact": str(source.matrix_path),
        "source_matrix_sha256": source.matrix_sha256,
        "source_seed_audit_artifact": (
            str(source.seed_audit_path) if source.seed_audit_path is not None else ""
        ),
        "source_seed_audit_sha256": source.seed_audit_sha256,
        "source_overlay_artifacts": ";".join(
            str(path) for path in source.overlay_paths
        ),
        "source_overlay_sha256s": ";".join(source.overlay_sha256s),
    }


def _missing_overlay_queue_rows(
    queue_candidates: Sequence[RetainedBackfillGateRow],
) -> list[dict[str, object]]:
    return [
        _missing_overlay_queue_row(row, rank=rank)
        for rank, row in enumerate(queue_candidates, start=1)
    ]


def _review_overlay_queue_rows(
    queue_candidates: Sequence[RetainedBackfillGateRow],
) -> list[dict[str, object]]:
    by_family: dict[str, RetainedBackfillGateRow] = {}
    for row in queue_candidates:
        by_family.setdefault(row.feature_family_id, row)
    return [
        _missing_overlay_queue_row(row, rank=rank)
        for rank, row in enumerate(by_family.values(), start=1)
    ]


def _missing_overlay_queue_candidates(
    rows: Sequence[RetainedBackfillGateRow],
) -> list[RetainedBackfillGateRow]:
    queueable = [
        row
        for row in rows
        if row.evidence_gate_status == "evidence_missing"
        and ("missing_overlay_evidence" in row.missing_evidence)
        and row.seed_group_basis == "seed_audit"
        and row.suggested_rt_min
        and row.suggested_rt_max
        and (row.seed_mz or row.family_center_mz)
    ]
    queueable.sort(key=_overlay_queue_sort_key)
    return queueable


def _missing_overlay_queue_row(
    row: RetainedBackfillGateRow,
    *,
    rank: int,
) -> dict[str, object]:
    output_prefix = _overlay_output_prefix(row, rank)
    ppm = row.ppm or "10"
    args = (
        f"--family-id {row.feature_family_id} "
        f"--mz {row.seed_mz or row.family_center_mz} "
        f"--rt-min {row.suggested_rt_min} "
        f"--rt-max {row.suggested_rt_max} "
        f"--ppm {ppm} "
        f"--output-prefix {output_prefix}"
    )
    return {
        "rank": rank,
        "feature_family_id": row.feature_family_id,
        "seed_group_id": row.seed_group_id,
        "family_center_mz": row.family_center_mz,
        "family_center_rt": row.family_center_rt,
        "suggested_rt_min": row.suggested_rt_min,
        "suggested_rt_max": row.suggested_rt_max,
        "suggested_output_prefix": output_prefix,
        "backfill_seed_mz": row.seed_mz,
        "backfill_seed_rt": row.seed_rt,
        "backfill_request_rt_min": row.suggested_rt_min,
        "backfill_request_rt_max": row.suggested_rt_max,
        "backfill_request_ppm": row.ppm,
        "ppm": ppm,
        "product_behavior_state": row.product_behavior_state,
        "evidence_gate_status": row.evidence_gate_status,
        "recommended_action": row.recommended_action,
        "detected_count": row.detected_cell_count,
        "accepted_backfill_count": row.accepted_backfill_count,
        "review_backfill_count": row.review_backfill_count,
        "rescued_cell_count": row.backfilled_cell_count,
        "seed_source_samples": ";".join(row.seed_source_samples),
        "row_flags": ";".join(row.row_flags),
        "missing_evidence": ";".join(row.missing_evidence),
        "suggested_overlay_command_args": args,
    }


def _overlay_queue_sort_key(row: RetainedBackfillGateRow) -> tuple[int, int, int, str]:
    review_only = 0 if row.product_behavior_state.endswith("_review_only") else 1
    return (
        review_only,
        -row.review_backfill_count,
        -row.backfilled_cell_count,
        row.feature_family_id,
    )


def _overlay_output_prefix(row: RetainedBackfillGateRow, rank: int) -> str:
    family = _safe_slug(row.feature_family_id.lower())
    return f"{rank:03d}_{family}_retained_backfill_missing_overlay"


def _safe_slug(value: str) -> str:
    return _SAFE_PREFIX_CHARS.sub("_", value).strip("._-") or "family"


def _row_sort_key(row: RetainedBackfillGateRow) -> tuple[int, str, str]:
    status_priority = {
        "evidence_conflict": 0,
        "evidence_missing": 1,
        "evidence_inconclusive": 2,
        "machine_support_no_overlay": 3,
        "visual_support": 4,
    }
    return (
        status_priority.get(row.evidence_gate_status, 9),
        row.feature_family_id,
        row.seed_group_id,
    )


def _count_cells(rows: Sequence[Mapping[str, str]], status: str) -> int:
    return sum(1 for row in rows if text_value(row.get("status")) == status)


def _detected_count(review: Mapping[str, str]) -> int:
    return _int_or_zero(review.get("quantifiable_detected_count"))


def _int_or_zero(value: object) -> int:
    return optional_int(value) or 0


def _ordered_unique(labels: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for label in labels:
        text = text_value(label)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _read_required_tsv(
    path: Path,
    required_columns: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    try:
        return read_tsv_required(path, required_columns)
    except FileNotFoundError as exc:
        raise ValueError(f"Required TSV not found: {path}") from exc
