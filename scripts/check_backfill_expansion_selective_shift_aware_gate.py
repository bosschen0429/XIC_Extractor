"""Build/check a selective shift-aware gate for Backfill expansion cells.

This diagnostic replays the current 666-cell Backfill expansion candidate
packet without RAW reads. It replaces the old question "does the whole family
pass one shift-aware threshold?" with "does this source family / sample cell
point to the selected PeakHypothesis?".

The checker grants no ProductWriter authority and writes no default matrix,
workbook, GUI, selected area, or counted-detection state.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_backfill_expansion_expected_diff_provenance import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_EXPECTED_DIFF_OUTPUT_DIR,
)
from scripts.check_backfill_expansion_raw_overlay_trace_identity import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_RAW_TRACE_OUTPUT_DIR,
)
from scripts.check_backfill_expansion_sample_local_ms1_evidence import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_SAMPLE_LOCAL_OUTPUT_DIR,
)
from scripts.validation_artifact_contracts import (  # noqa: E402
    check_summary_artifact_hashes,
    is_declared_externalized_artifact_path,
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    optional_float,
    read_tsv_required,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "backfill_expansion_selective_shift_aware_gate_v1"
CHECK_SCHEMA_VERSION = "backfill_expansion_selective_shift_aware_gate_check_v1"
PACKET_SCOPE = "backfill_expansion_candidate_replay_666_cells"
EXPECTED_CANDIDATE_CELL_COUNT = 666
DEFAULT_SUPPORT_MIN_SHAPE_R = 0.90
DEFAULT_ATTENTION_MIN_SHAPE_R = 0.85
DEFAULT_OWN_MAX_THRESHOLD = 0.5
SOURCE_FAMILY_RE = re.compile(r"(?:^|;)\s*source_family=([^;]+)")

DEFAULT_DOCS_DIR = (
    ROOT
    / "docs/superpowers/validation/"
    "backfill_expansion_selective_shift_aware_gate_v1"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "output/validation/backfill_expansion_selective_shift_aware_gate_v1"
)
DEFAULT_FULL_CHAIN_OUTPUT_DIR = (
    ROOT / "output/validation/backfill_expansion_full_evidence_chain_v1"
)
DEFAULT_EXPECTED_DIFF_TSV = (
    DEFAULT_EXPECTED_DIFF_OUTPUT_DIR / "inputs/expected_diff.tsv"
)
DEFAULT_RAW_TRACE_CELLS_TSV = (
    DEFAULT_RAW_TRACE_OUTPUT_DIR
    / "backfill_expansion_raw_overlay_trace_identity_cells.tsv"
)
DEFAULT_SHIFT_AWARE_BATCH_SUMMARY_JSON = (
    DEFAULT_FULL_CHAIN_OUTPUT_DIR
    / "shift_aware_alignment_experiment/"
    "family_ms1_alignment_experiment_batch_summary.json"
)
DEFAULT_SHIFT_AWARE_STANDARD_PEAK_GATE_TSV = (
    DEFAULT_FULL_CHAIN_OUTPUT_DIR
    / "shift_aware_standard_peak_gate/shift_aware_standard_peak_gate_calibration.tsv"
)
DEFAULT_SAMPLE_LOCAL_CELLS_TSV = (
    DEFAULT_SAMPLE_LOCAL_OUTPUT_DIR
    / "backfill_expansion_sample_local_ms1_evidence_cells.tsv"
)
DEFAULT_BATCH_SUMMARY_TSV = (
    ROOT
    / "output/validation/backfill_expansion_full_evidence_chain_v1/"
    "shift_aware_alignment_experiment/"
    "family_ms1_alignment_experiment_batch_summary.tsv"
)
DEFAULT_CELLS_TSV = (
    DEFAULT_OUTPUT_DIR / "backfill_expansion_selective_shift_aware_gate_cells.tsv"
)
DEFAULT_SUMMARY_JSON = (
    DEFAULT_DOCS_DIR / "backfill_expansion_selective_shift_aware_gate_summary.json"
)
DEFAULT_CHECKS_TSV = (
    DEFAULT_DOCS_DIR / "backfill_expansion_selective_shift_aware_gate_checks.tsv"
)
DEFAULT_ROW_MANIFEST_TSV = (
    DEFAULT_DOCS_DIR
    / "backfill_expansion_selective_shift_aware_gate_row_manifest.tsv"
)

EXPECTED_DIFF_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "expected_matrix_effect",
)
SAMPLE_LOCAL_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "alignment_cell_evidence_status",
    "alignment_cell_status",
    "production_cell_status",
    "identity_decision",
    "neutral_loss_tag",
    "cell_evidence_reason",
)
RAW_TRACE_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "trace_status",
    "absolute_own_max_shape_similarity",
    "raw_trace_gate_status",
)
STANDARD_GATE_COLUMNS = (
    "feature_family_id",
    "family_verdict",
    "standard_peak_gate_call",
    "standard_peak_gate_reasons",
    "standard_peak_gate_blockers",
    "min_shape_r_after_best_shift",
    "max_shape_r_after_best_shift",
)
BATCH_SUMMARY_COLUMNS = (
    "feature_family_id",
    "source_best_shift_summary_tsv",
)
BEST_SHIFT_COLUMNS = (
    "feature_family_id",
    "source_family",
    "is_reference",
    "trace_count",
    "detected_count",
    "median_cell_apex_rt",
    "shift_to_reference_sec",
    "shape_similarity_to_reference_after_group_shift",
)
CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "observed",
    "expected",
    "notes",
)
ROW_MANIFEST_COLUMNS = (
    "schema_version",
    "packet_scope",
    "peak_hypothesis_id",
    "candidate_cell_count",
    "source_family_count",
    "standard_peak_boundary_pass_cell_count",
    "source_family_shift_supported_cell_count",
    "source_family_attention_cell_count",
    "own_max_metric_supported_cell_count",
    "selective_evidence_pass_cell_count",
    "held_cell_count",
    "primary_blocker",
    "source_family_status_counts",
    "product_authority_effect",
    "next_gate",
)
CELL_COLUMNS = (
    "schema_version",
    "packet_scope",
    "peak_hypothesis_id",
    "sample_stem",
    "source_family",
    "expected_diff_status",
    "sample_local_status",
    "raw_trace_status",
    "standard_peak_boundary_status",
    "whole_family_gate_call",
    "source_family_shift_status",
    "source_family_shape_r",
    "source_family_shift_sec",
    "own_max_metric_status",
    "own_max_metric_value",
    "selective_evidence_status",
    "primary_blocker",
    "secondary_blockers",
    "product_authority_effect",
    "next_gate",
)


def build_backfill_expansion_selective_shift_aware_gate(
    *,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    expected_diff_tsv: Path = DEFAULT_EXPECTED_DIFF_TSV,
    sample_local_cells_tsv: Path = DEFAULT_SAMPLE_LOCAL_CELLS_TSV,
    raw_trace_cells_tsv: Path = DEFAULT_RAW_TRACE_CELLS_TSV,
    shift_aware_batch_summary_json: Path = DEFAULT_SHIFT_AWARE_BATCH_SUMMARY_JSON,
    shift_aware_batch_summary_tsv: Path = DEFAULT_BATCH_SUMMARY_TSV,
    standard_peak_gate_tsv: Path = DEFAULT_SHIFT_AWARE_STANDARD_PEAK_GATE_TSV,
    support_min_shape_r: float = DEFAULT_SUPPORT_MIN_SHAPE_R,
    attention_min_shape_r: float = DEFAULT_ATTENTION_MIN_SHAPE_R,
    own_max_threshold: float = DEFAULT_OWN_MAX_THRESHOLD,
    cells_tsv: Path = DEFAULT_CELLS_TSV,
) -> dict[str, Any]:
    if support_min_shape_r < attention_min_shape_r:
        raise ValueError("support_min_shape_r must be >= attention_min_shape_r")

    expected_rows = read_tsv_required(expected_diff_tsv, EXPECTED_DIFF_COLUMNS)
    sample_rows = read_tsv_required(sample_local_cells_tsv, SAMPLE_LOCAL_COLUMNS)
    raw_rows = read_tsv_required(raw_trace_cells_tsv, RAW_TRACE_COLUMNS)
    gate_rows = read_tsv_required(standard_peak_gate_tsv, STANDARD_GATE_COLUMNS)
    batch_rows = read_tsv_required(shift_aware_batch_summary_tsv, BATCH_SUMMARY_COLUMNS)

    expected_by_key = _unique_by_cell_key(expected_rows, "peak_hypothesis_id")
    sample_by_key = _unique_by_cell_key(sample_rows, "peak_hypothesis_id")
    raw_by_key = _unique_by_cell_key(raw_rows, "peak_hypothesis_id")
    gate_by_family = _by_text_field(gate_rows, "feature_family_id")
    shift_by_family_source = _load_best_shift_by_family_source(batch_rows)

    cell_rows = _build_cell_rows(
        expected_rows=expected_rows,
        expected_by_key=expected_by_key,
        sample_by_key=sample_by_key,
        raw_by_key=raw_by_key,
        gate_by_family=gate_by_family,
        shift_by_family_source=shift_by_family_source,
        support_min_shape_r=support_min_shape_r,
        attention_min_shape_r=attention_min_shape_r,
        own_max_threshold=own_max_threshold,
    )
    row_manifest = _build_row_manifest(cell_rows)
    checks = _build_checks(
        cell_rows=cell_rows,
        row_manifest=row_manifest,
        support_min_shape_r=support_min_shape_r,
        attention_min_shape_r=attention_min_shape_r,
        shift_aware_batch_summary_json=shift_aware_batch_summary_json,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(cells_tsv, cell_rows, CELL_COLUMNS, extrasaction="raise")
    checks_tsv = docs_dir / DEFAULT_CHECKS_TSV.name
    row_manifest_tsv = docs_dir / DEFAULT_ROW_MANIFEST_TSV.name
    summary_json = docs_dir / DEFAULT_SUMMARY_JSON.name
    write_tsv(checks_tsv, checks, CHECK_COLUMNS, extrasaction="raise")
    write_tsv(
        row_manifest_tsv,
        row_manifest,
        ROW_MANIFEST_COLUMNS,
        extrasaction="raise",
    )

    payload = _summary_payload(
        checks=checks,
        cell_rows=cell_rows,
        row_manifest=row_manifest,
        support_min_shape_r=support_min_shape_r,
        attention_min_shape_r=attention_min_shape_r,
        own_max_threshold=own_max_threshold,
        input_paths={
            "expected_diff_tsv": expected_diff_tsv,
            "sample_local_cells_tsv": sample_local_cells_tsv,
            "raw_trace_cells_tsv": raw_trace_cells_tsv,
            "shift_aware_batch_summary_json": shift_aware_batch_summary_json,
            "shift_aware_batch_summary_tsv": shift_aware_batch_summary_tsv,
            "standard_peak_gate_tsv": standard_peak_gate_tsv,
        },
        output_paths={
            "cells_tsv": cells_tsv,
            "checks_tsv": checks_tsv,
            "row_manifest_tsv": row_manifest_tsv,
        },
    )
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def validate_backfill_expansion_selective_shift_aware_gate(
    *,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
    checks_tsv: Path = DEFAULT_CHECKS_TSV,
    row_manifest_tsv: Path = DEFAULT_ROW_MANIFEST_TSV,
    cells_tsv: Path = DEFAULT_CELLS_TSV,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read summary JSON: {exc}"]
    if not isinstance(payload, Mapping):
        return ["summary JSON must contain an object"]

    _check_summary(payload, problems)
    _check_checks_tsv(checks_tsv, payload, problems)
    _check_manifest_tsv(row_manifest_tsv, payload, problems)
    if cells_tsv.exists():
        _check_cells_tsv(cells_tsv, payload, problems)
    elif not is_declared_externalized_artifact_path(
        payload,
        "cells_tsv",
        cells_tsv,
        root=ROOT,
    ):
        problems.append(f"cells TSV missing: {cells_tsv}")
    _check_artifact_hashes(payload, problems)
    return problems


def _build_cell_rows(
    *,
    expected_rows: Sequence[Mapping[str, str]],
    expected_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    sample_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    raw_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    gate_by_family: Mapping[str, Mapping[str, str]],
    shift_by_family_source: Mapping[tuple[str, str], Mapping[str, str]],
    support_min_shape_r: float,
    attention_min_shape_r: float,
    own_max_threshold: float,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for expected in expected_rows:
        family = text_value(expected.get("peak_hypothesis_id"))
        sample = text_value(expected.get("sample_stem"))
        key = (family, sample)
        sample_row = sample_by_key.get(key)
        raw_row = raw_by_key.get(key)
        gate = gate_by_family.get(family)
        source_family = _extract_source_family(
            text_value((sample_row or {}).get("cell_evidence_reason")),
        )
        shift_row = shift_by_family_source.get((family, source_family))

        expected_status = _expected_status(expected_by_key.get(key))
        sample_status = _sample_local_status(sample_row)
        raw_status = _raw_trace_status(raw_row)
        boundary_status = _standard_peak_boundary_status(gate)
        shift_status, shape_r, shift_sec = _source_family_shift_status(
            source_family=source_family,
            shift_row=shift_row,
            support_min_shape_r=support_min_shape_r,
            attention_min_shape_r=attention_min_shape_r,
        )
        own_status, own_value = _own_max_status(raw_row, own_max_threshold)
        blockers = _blockers(
            expected_status=expected_status,
            sample_status=sample_status,
            raw_status=raw_status,
            boundary_status=boundary_status,
            shift_status=shift_status,
            own_status=own_status,
        )
        selective_status = "pass" if not blockers else "held"
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "packet_scope": PACKET_SCOPE,
                "peak_hypothesis_id": family,
                "sample_stem": sample,
                "source_family": source_family,
                "expected_diff_status": expected_status,
                "sample_local_status": sample_status,
                "raw_trace_status": raw_status,
                "standard_peak_boundary_status": boundary_status,
                "whole_family_gate_call": text_value(
                    (gate or {}).get("standard_peak_gate_call"),
                ),
                "source_family_shift_status": shift_status,
                "source_family_shape_r": shape_r,
                "source_family_shift_sec": shift_sec,
                "own_max_metric_status": own_status,
                "own_max_metric_value": own_value,
                "selective_evidence_status": selective_status,
                "primary_blocker": blockers[0] if blockers else "",
                "secondary_blockers": ";".join(blockers[1:]),
                "product_authority_effect": "diagnostic_only_no_write_authority",
                "next_gate": (
                    "eligible_for_expected_diff_authority_design"
                    if selective_status == "pass"
                    else "resolve_selective_evidence_before_product_authority"
                ),
            },
        )
    return rows


def _build_row_manifest(
    cell_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    by_family: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in cell_rows:
        by_family[text_value(row.get("peak_hypothesis_id"))].append(row)

    manifest: list[dict[str, str]] = []
    for family in sorted(by_family):
        rows = by_family[family]
        blockers = Counter(
            text_value(row.get("primary_blocker"))
            for row in rows
            if text_value(row.get("primary_blocker"))
        )
        source_status_counts = Counter(
            text_value(row.get("source_family_shift_status")) for row in rows
        )
        source_families = {
            text_value(row.get("source_family"))
            for row in rows
            if text_value(row.get("source_family"))
        }
        manifest.append(
            {
                "schema_version": SCHEMA_VERSION,
                "packet_scope": PACKET_SCOPE,
                "peak_hypothesis_id": family,
                "candidate_cell_count": str(len(rows)),
                "source_family_count": str(len(source_families)),
                "standard_peak_boundary_pass_cell_count": _count_status(
                    rows,
                    "standard_peak_boundary_status",
                    "pass",
                ),
                "source_family_shift_supported_cell_count": _count_status(
                    rows,
                    "source_family_shift_status",
                    "pass",
                ),
                "source_family_attention_cell_count": _count_status(
                    rows,
                    "source_family_shift_status",
                    "attention_only",
                ),
                "own_max_metric_supported_cell_count": _count_status(
                    rows,
                    "own_max_metric_status",
                    "pass",
                ),
                "selective_evidence_pass_cell_count": _count_status(
                    rows,
                    "selective_evidence_status",
                    "pass",
                ),
                "held_cell_count": _count_status(
                    rows,
                    "selective_evidence_status",
                    "held",
                ),
                "primary_blocker": blockers.most_common(1)[0][0]
                if blockers
                else "",
                "source_family_status_counts": _format_counter(
                    source_status_counts,
                ),
                "product_authority_effect": "diagnostic_only_no_write_authority",
                "next_gate": "selective_gate_review_or_expected_diff_design",
            },
        )
    return manifest


def _build_checks(
    *,
    cell_rows: Sequence[Mapping[str, str]],
    row_manifest: Sequence[Mapping[str, str]],
    support_min_shape_r: float,
    attention_min_shape_r: float,
    shift_aware_batch_summary_json: Path,
) -> list[dict[str, str]]:
    key_count = len(
        {
            (row["peak_hypothesis_id"], row["sample_stem"])
            for row in cell_rows
        },
    )
    batch_summary = _read_json_object(shift_aware_batch_summary_json)
    pass_count = _count_status(cell_rows, "selective_evidence_status", "pass")
    held_count = _count_status(cell_rows, "selective_evidence_status", "held")
    return [
        _check(
            "candidate_scope_count",
            len(cell_rows),
            EXPECTED_CANDIDATE_CELL_COUNT,
        ),
        _check("candidate_keyset_unique", key_count, len(cell_rows)),
        _check("row_manifest_family_count", len(row_manifest), 20),
        _check(
            "shift_aware_batch_success",
            batch_summary.get("successful_shift_aware_row_count"),
            20,
        ),
        _check(
            "support_threshold_at_or_above_attention_threshold",
            support_min_shape_r >= attention_min_shape_r,
            True,
        ),
        _check(
            "selective_counts_sum_to_candidate_count",
            int(pass_count) + int(held_count),
            len(cell_rows),
        ),
        _check("no_product_writer_authority", True, True),
        _check("no_default_matrix_or_workbook_change", True, True),
    ]


def _summary_payload(
    *,
    checks: Sequence[Mapping[str, str]],
    cell_rows: Sequence[Mapping[str, str]],
    row_manifest: Sequence[Mapping[str, str]],
    support_min_shape_r: float,
    attention_min_shape_r: float,
    own_max_threshold: float,
    input_paths: Mapping[str, Path],
    output_paths: Mapping[str, Path],
) -> dict[str, Any]:
    blocker_counts = Counter(
        text_value(row.get("primary_blocker"))
        for row in cell_rows
        if text_value(row.get("primary_blocker"))
    )
    source_status_counts = Counter(
        text_value(row.get("source_family_shift_status")) for row in cell_rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_status": "diagnostic_only_selective_gate_replay",
        "packet_scope": PACKET_SCOPE,
        "support_min_shape_r": support_min_shape_r,
        "attention_min_shape_r": attention_min_shape_r,
        "own_max_threshold": own_max_threshold,
        "candidate_cell_count": len(cell_rows),
        "candidate_peak_count": len(row_manifest),
        "standard_peak_boundary_pass_cell_count": int(
            _count_status(cell_rows, "standard_peak_boundary_status", "pass"),
        ),
        "source_family_shift_supported_cell_count": int(
            _count_status(cell_rows, "source_family_shift_status", "pass"),
        ),
        "source_family_attention_cell_count": int(
            _count_status(cell_rows, "source_family_shift_status", "attention_only"),
        ),
        "own_max_metric_supported_cell_count": int(
            _count_status(cell_rows, "own_max_metric_status", "pass"),
        ),
        "selective_evidence_pass_cell_count": int(
            _count_status(cell_rows, "selective_evidence_status", "pass"),
        ),
        "held_cell_count": int(
            _count_status(cell_rows, "selective_evidence_status", "held"),
        ),
        "primary_blocker_counts": dict(sorted(blocker_counts.items())),
        "source_family_shift_status_counts": dict(sorted(source_status_counts.items())),
        "write_authority": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "raw_or_85raw_ran_by_checker": False,
        "authority_statement": (
            "This selective shift-aware replay is diagnostic evidence only. "
            "It can identify cells eligible for a later expected-diff design, "
            "but it grants no ProductWriter authority."
        ),
        "checks": {row["check_id"]: row["status"] for row in checks},
        "input_artifacts": {
            name: _artifact(path) for name, path in sorted(input_paths.items())
        },
        "artifacts": {
            name: _artifact(path) for name, path in sorted(output_paths.items())
        },
    }


def _extract_source_family(reason: str) -> str:
    match = SOURCE_FAMILY_RE.search(reason)
    if match is None:
        return ""
    return match.group(1).strip()


def _expected_status(row: Mapping[str, str] | None) -> str:
    if row is None:
        return "missing"
    if text_value(row.get("expected_matrix_effect")) != "write_accepted_backfill":
        return "unexpected_effect"
    return "pass"


def _sample_local_status(row: Mapping[str, str] | None) -> str:
    if row is None:
        return "missing"
    if text_value(row.get("alignment_cell_evidence_status")) != "present":
        return "evidence_not_present"
    if text_value(row.get("alignment_cell_status")) != "rescued":
        return "alignment_not_rescued"
    if text_value(row.get("production_cell_status")) != "review_rescue":
        return "production_cell_not_review_rescue"
    if text_value(row.get("identity_decision")) != "production_family":
        return "identity_not_production_family"
    if not _extract_source_family(text_value(row.get("cell_evidence_reason"))):
        return "source_family_missing"
    return "pass"


def _raw_trace_status(row: Mapping[str, str] | None) -> str:
    if row is None:
        return "missing"
    if text_value(row.get("trace_status")) != "rescued":
        return "trace_not_rescued"
    if (
        text_value(row.get("raw_trace_gate_status"))
        != "raw_trace_observed_expected_diff_candidate"
    ):
        return "raw_trace_gate_not_candidate"
    return "pass"


def _standard_peak_boundary_status(row: Mapping[str, str] | None) -> str:
    if row is None:
        return "missing"
    if text_value(row.get("family_verdict")) != "ms1_shape_supports_family_backfill":
        return "family_verdict_not_supported"
    reasons = text_value(row.get("standard_peak_gate_reasons"))
    if "family_overlay_gaussian_smoothed_standard_peak_supported" not in reasons:
        return "standard_peak_boundary_not_supported"
    return "pass"


def _source_family_shift_status(
    *,
    source_family: str,
    shift_row: Mapping[str, str] | None,
    support_min_shape_r: float,
    attention_min_shape_r: float,
) -> tuple[str, str, str]:
    if not source_family:
        return "missing_source_family", "", ""
    if shift_row is None:
        return "missing_shift_summary", "", ""
    shape_r = optional_float(
        shift_row.get("shape_similarity_to_reference_after_group_shift"),
    )
    if shape_r is None:
        return "missing_shape_r", "", ""
    shape_text = f"{shape_r:.4f}"
    shift_sec = text_value(shift_row.get("shift_to_reference_sec"))
    if shape_r >= support_min_shape_r:
        return "pass", shape_text, shift_sec
    if shape_r >= attention_min_shape_r:
        return "attention_only", shape_text, shift_sec
    return "not_same_hypothesis", shape_text, shift_sec


def _own_max_status(
    row: Mapping[str, str] | None,
    own_max_threshold: float,
) -> tuple[str, str]:
    if row is None:
        return "missing", ""
    value = optional_float(row.get("absolute_own_max_shape_similarity"))
    if value is None:
        return "missing", ""
    formatted = f"{value:.6g}"
    if value <= own_max_threshold:
        return "below_threshold", formatted
    return "pass", formatted


def _blockers(
    *,
    expected_status: str,
    sample_status: str,
    raw_status: str,
    boundary_status: str,
    shift_status: str,
    own_status: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if expected_status != "pass":
        blockers.append("expected_diff_" + expected_status)
    if sample_status != "pass":
        blockers.append("sample_local_" + sample_status)
    if raw_status != "pass":
        blockers.append("raw_trace_" + raw_status)
    if boundary_status != "pass":
        blockers.append("standard_peak_boundary_" + boundary_status)
    if shift_status != "pass":
        blockers.append("source_family_shift_" + shift_status)
    if own_status != "pass":
        blockers.append("own_max_metric_" + own_status)
    return tuple(blockers)


def _load_best_shift_by_family_source(
    batch_rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for batch_row in batch_rows:
        path = _resolve_artifact_path(
            text_value(batch_row.get("source_best_shift_summary_tsv")),
        )
        if not path:
            continue
        for row in read_tsv_required(path, BEST_SHIFT_COLUMNS):
            family = text_value(row.get("feature_family_id"))
            source_family = text_value(row.get("source_family"))
            if family and source_family:
                by_key[(family, source_family)] = row
    return by_key


def _unique_by_cell_key(
    rows: Sequence[Mapping[str, str]],
    family_field: str,
) -> dict[tuple[str, str], Mapping[str, str]]:
    by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (
            text_value(row.get(family_field)),
            text_value(row.get("sample_stem")),
        )
        if not all(key):
            continue
        if key in by_key:
            raise ValueError(f"duplicate key: {key[0]}/{key[1]}")
        by_key[key] = row
    return by_key


def _by_text_field(
    rows: Sequence[Mapping[str, str]],
    field: str,
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        key = text_value(row.get(field))
        if key:
            result[key] = row
    return result


def _count_status(
    rows: Sequence[Mapping[str, str]],
    field: str,
    value: str,
) -> str:
    return str(sum(1 for row in rows if text_value(row.get(field)) == value))


def _format_counter(counter: Counter[str]) -> str:
    return ";".join(f"{key}={counter[key]}" for key in sorted(counter))


def _check(
    check_id: str,
    observed: object,
    expected: object,
    *,
    notes: str = "",
) -> dict[str, str]:
    observed_text = text_value(observed)
    expected_text = text_value(expected)
    return {
        "schema_version": CHECK_SCHEMA_VERSION,
        "check_id": check_id,
        "status": "pass" if observed_text == expected_text else "fail",
        "observed": observed_text,
        "expected": expected_text,
        "notes": notes,
    }


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": _repo_relpath(resolved),
        "sha256": file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _repo_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("/", "\\")
    except ValueError:
        return str(path)


def _resolve_artifact_path(path_text: str) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _read_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _check_summary(payload: Mapping[str, Any], problems: list[str]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("summary schema_version mismatch")
    if payload.get("packet_scope") != PACKET_SCOPE:
        problems.append("summary packet_scope mismatch")
    if payload.get("candidate_cell_count") != EXPECTED_CANDIDATE_CELL_COUNT:
        problems.append("summary candidate_cell_count mismatch")
    for field in (
        "write_authority",
        "product_writer_changed",
        "default_quant_matrix_changed",
        "workbook_or_gui_changed",
        "selected_peak_area_or_counting_changed",
        "raw_or_85raw_ran_by_checker",
    ):
        if payload.get(field) is not False:
            problems.append(f"summary {field} must be false")


def _check_checks_tsv(
    checks_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(checks_tsv, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read checks TSV: {exc}")
        return
    failing = [row["check_id"] for row in rows if row.get("status") != "pass"]
    if failing:
        problems.append("checks must all pass: " + ";".join(failing))
    summary_checks = payload.get("checks")
    if isinstance(summary_checks, Mapping):
        for row in rows:
            check_id = text_value(row.get("check_id"))
            if summary_checks.get(check_id) != "pass":
                problems.append(f"summary check {check_id} must pass")


def _check_manifest_tsv(
    row_manifest_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(row_manifest_tsv, ROW_MANIFEST_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read row manifest TSV: {exc}")
        return
    if len(rows) != payload.get("candidate_peak_count"):
        problems.append("row manifest candidate peak count mismatch")
    pass_sum = sum(
        int(row.get("selective_evidence_pass_cell_count") or 0) for row in rows
    )
    if pass_sum != payload.get("selective_evidence_pass_cell_count"):
        problems.append("row manifest selective pass count mismatch")
    held_sum = sum(int(row.get("held_cell_count") or 0) for row in rows)
    if held_sum != payload.get("held_cell_count"):
        problems.append("row manifest held count mismatch")


def _check_cells_tsv(
    cells_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(cells_tsv, CELL_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read cells TSV: {exc}")
        return
    if len(rows) != payload.get("candidate_cell_count"):
        problems.append("cells TSV candidate count mismatch")
    pass_count = sum(
        1 for row in rows if row.get("selective_evidence_status") == "pass"
    )
    if pass_count != payload.get("selective_evidence_pass_cell_count"):
        problems.append("cells TSV selective pass count mismatch")
    effects = {row.get("product_authority_effect") for row in rows}
    if effects != {"diagnostic_only_no_write_authority"}:
        problems.append("cells TSV product_authority_effect mismatch")


def _check_artifact_hashes(payload: Mapping[str, Any], problems: list[str]) -> None:
    check_summary_artifact_hashes(
        payload,
        root=ROOT,
        problems=problems,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--support-min-shape-r",
        type=float,
        default=DEFAULT_SUPPORT_MIN_SHAPE_R,
    )
    parser.add_argument(
        "--attention-min-shape-r",
        type=float,
        default=DEFAULT_ATTENTION_MIN_SHAPE_R,
    )
    parser.add_argument(
        "--own-max-threshold",
        type=float,
        default=DEFAULT_OWN_MAX_THRESHOLD,
    )
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--checks-tsv", type=Path, default=DEFAULT_CHECKS_TSV)
    parser.add_argument(
        "--row-manifest-tsv",
        type=Path,
        default=DEFAULT_ROW_MANIFEST_TSV,
    )
    parser.add_argument("--cells-tsv", type=Path, default=DEFAULT_CELLS_TSV)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if not args.check_only:
            build_backfill_expansion_selective_shift_aware_gate(
                support_min_shape_r=args.support_min_shape_r,
                attention_min_shape_r=args.attention_min_shape_r,
                own_max_threshold=args.own_max_threshold,
                cells_tsv=args.cells_tsv,
            )
        problems = validate_backfill_expansion_selective_shift_aware_gate(
            summary_json=args.summary_json,
            checks_tsv=args.checks_tsv,
            row_manifest_tsv=args.row_manifest_tsv,
            cells_tsv=args.cells_tsv,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    print(
        "Backfill expansion selective shift-aware summary: "
        f"{args.summary_json}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
