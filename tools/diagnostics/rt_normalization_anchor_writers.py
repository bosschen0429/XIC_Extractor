"""Output writers for the RT normalization anchor diagnostic."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from tools.diagnostics.rt_normalization_anchor_models import (
    FamilyRtSummary,
    LeaveOneAnchorOutSummary,
    RtNormalizationOutputs,
    RtNormalizationResult,
)
from xic_extractor.alignment.rt_normalization import AnchorResidual, SampleRtModel
from xic_extractor.tabular_io import write_tsv

SUMMARY_COLUMNS = ("metric", "value")
SAMPLE_COLUMNS = (
    "sample_stem",
    "model_type",
    "anchor_count",
    "excluded_anchor_count",
    "slope",
    "intercept",
    "anchor_median_abs_residual_min",
    "anchor_max_abs_residual_min",
)
ANCHOR_COLUMNS = (
    "sample_stem",
    "target_label",
    "reference_rt_min",
    "observed_rt_min",
    "normalized_rt_min",
    "normalized_residual_min",
    "used_in_model",
    "anchor_status",
)
LEAVE_ONE_OUT_COLUMNS = (
    "target_label",
    "evaluated_count",
    "median_abs_error_min",
    "p95_abs_error_min",
    "max_abs_error_min",
    "status",
)
FAMILY_COLUMNS = (
    "feature_family_id",
    "include_in_primary_matrix",
    "family_center_mz",
    "family_center_rt",
    "raw_cell_count",
    "modelled_cell_count",
    "unmodelled_cell_count",
    "raw_rt_range_min",
    "normalized_rt_range_min",
    "rt_range_improvement_min",
    "raw_rt_median_min",
    "normalized_rt_median_min",
    "rt_band",
    "normalized_rt_support",
    "anchor_scope",
    "anchor_support_level",
    "local_residual_window_min",
)


def write_rt_normalization_outputs(
    outputs: RtNormalizationOutputs,
    result: RtNormalizationResult,
) -> None:
    _write_tsv(outputs.summary_tsv, SUMMARY_COLUMNS, _summary_rows(result))
    _write_tsv(outputs.sample_tsv, SAMPLE_COLUMNS, _sample_rows(result.samples))
    _write_tsv(outputs.anchor_tsv, ANCHOR_COLUMNS, _anchor_rows(result.anchors))
    _write_tsv(
        outputs.leave_one_out_tsv,
        LEAVE_ONE_OUT_COLUMNS,
        _leave_one_out_rows(result.leave_one_anchor_out),
    )
    _write_tsv(outputs.family_tsv, FAMILY_COLUMNS, _family_rows(result.families))
    _write_json(outputs.json_path, _json_payload(result))
    _write_markdown(outputs.markdown_path, result)


def _summary_rows(result: RtNormalizationResult) -> list[dict[str, object]]:
    payload = _json_payload(result)
    return [{"metric": key, "value": value} for key, value in payload.items()]


def _sample_rows(samples: Sequence[SampleRtModel]) -> list[dict[str, object]]:
    return [asdict(sample) for sample in samples]


def _anchor_rows(anchors: Sequence[AnchorResidual]) -> list[dict[str, object]]:
    return [asdict(anchor) for anchor in anchors]


def _leave_one_out_rows(
    rows: Sequence[LeaveOneAnchorOutSummary],
) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def _family_rows(families: Sequence[FamilyRtSummary]) -> list[dict[str, object]]:
    return [asdict(family) for family in families]


def _json_payload(result: RtNormalizationResult) -> dict[str, object]:
    return {
        "overall_status": result.overall_status,
        "reference_source": result.reference_source,
        "model_type": result.model_type,
        "anchor_residual_max_min": result.anchor_residual_max_min,
        "anchor_label_count": result.anchor_label_count,
        "sample_count": result.sample_count,
        "modelled_sample_count": result.modelled_sample_count,
        "unmodelled_sample_count": result.unmodelled_sample_count,
        "excluded_anchor_count": result.excluded_anchor_count,
        "family_count": result.family_count,
        "families_improved_count": result.families_improved_count,
        "families_worsened_count": result.families_worsened_count,
        "median_raw_rt_range_min": result.median_raw_rt_range_min,
        "median_normalized_rt_range_min": result.median_normalized_rt_range_min,
        "median_rt_range_improvement_min": result.median_rt_range_improvement_min,
        "rt_band_summary": result.rt_band_summary,
        "leave_one_anchor_out": [asdict(row) for row in result.leave_one_anchor_out],
    }


def _write_markdown(path: Path, result: RtNormalizationResult) -> None:
    lines = [
        "# RT Normalization Anchor Diagnostic",
        "",
        f"Overall status: {result.overall_status}",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for row in _summary_rows(result):
        lines.append(f"| {row['metric']} | {_format_value(row['value'])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    write_tsv(path, rows, fieldnames, formatter=_format_value)


def _format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.6g}"
    return str(value)
