"""Canonical panel loading and gate evaluation for row-completion confidence."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.diagnostics.row_completion_confidence_schema import SCHEMA_VERSION
from xic_extractor.tabular_io import read_tsv_with_header, text_value

_PANEL_REQUIRED_COLUMNS = (
    "case_id",
    "case_type",
    "target_label",
    "feature_family_id",
    "sample_stem",
    "expected_outcome",
    "production_safety_expectation",
    "review_utility_expectation",
    "required_artifacts",
    "baseline_binding",
    "manual_review_trigger",
    "reason",
)
_MANIFEST_SCHEMA_VERSION = "row_completion_canonical_panel_manifest"
_MANUAL_REVIEW_TRIGGERS = {"on_warn_fail", "on_any_current", "on_increase"}
_TARGETED_GT_FAILURE_MODES = {"SPLIT", "MISS", "DRIFT", "DUPLICATE"}
_SENTINEL_METRIC_KEYS = {
    "duplicate_only": "duplicate_only_family_count",
    "zero_present": "zero_present_family_count",
    "high_backfill_dependency": "high_backfill_dependency_count",
    "ambiguous_ms1_owner": "ambiguous_ms1_owner_count",
}


@dataclass(frozen=True)
class CanonicalPanelCase:
    case_id: str
    case_type: str
    target_label: str
    feature_family_id: str
    sample_stem: str
    expected_outcome: str
    production_safety_expectation: str
    review_utility_expectation: str
    required_artifacts: str
    baseline_binding: str
    manual_review_trigger: str
    reason: str


@dataclass(frozen=True)
class GatePanelResult:
    status: str
    gate_ok: bool
    production_ready: bool
    reason: str
    manual_review_required: bool
    missing_evidence_code: str


def load_canonical_panel(panel_tsv: Path) -> Sequence[CanonicalPanelCase]:
    _header, rows = read_tsv_with_header(
        panel_tsv,
        required_columns=_PANEL_REQUIRED_COLUMNS,
        encoding="utf-8-sig",
    )
    _validate_row_shapes(panel_tsv)
    return tuple(
        CanonicalPanelCase(
            case_id=text_value(row.get("case_id")),
            case_type=text_value(row.get("case_type")),
            target_label=text_value(row.get("target_label")),
            feature_family_id=text_value(row.get("feature_family_id")),
            sample_stem=text_value(row.get("sample_stem")),
            expected_outcome=text_value(row.get("expected_outcome")),
            production_safety_expectation=text_value(
                row.get("production_safety_expectation")
            ),
            review_utility_expectation=text_value(
                row.get("review_utility_expectation")
            ),
            required_artifacts=text_value(row.get("required_artifacts")),
            baseline_binding=text_value(row.get("baseline_binding")),
            manual_review_trigger=text_value(row.get("manual_review_trigger")),
            reason=text_value(row.get("reason")),
        )
        for row in rows
    )


def evaluate_gate_panel(
    panel_tsv: Path,
    *,
    panel_manifest: Path,
    targeted_gt_dirs: Mapping[str, Path],
    current_sentinel_summary: Mapping[str, object],
    baseline_sentinel_summary: Mapping[str, object] | None,
) -> GatePanelResult:
    panel_cases = load_canonical_panel(panel_tsv)
    manifest_result = _validate_manifest(panel_manifest, panel_cases)
    if manifest_result is not None:
        return manifest_result

    _schema_anchor = SCHEMA_VERSION
    if not _schema_anchor:
        return _inconclusive(
            "row completion confidence schema version missing",
            "unknown_schema_version",
        )

    for case in panel_cases:
        trigger_result = _validate_manual_review_trigger(case)
        if trigger_result is not None:
            return trigger_result

        if case.case_type == "targeted_gt_summary":
            targeted_result = _evaluate_targeted_gt_case(
                case=case,
                targeted_gt_dirs=targeted_gt_dirs,
            )
            if targeted_result is not None:
                return targeted_result
            continue

        sentinel_result = _evaluate_sentinel_case(
            case=case,
            current_sentinel_summary=current_sentinel_summary,
            baseline_sentinel_summary=baseline_sentinel_summary,
        )
        if sentinel_result is not None:
            return sentinel_result

    return GatePanelResult(
        status="PASS",
        gate_ok=True,
        production_ready=False,
        reason=(
            "canonical panel satisfied without targeted GT regression "
            "or sentinel pressure increase"
        ),
        manual_review_required=False,
        missing_evidence_code="",
    )


def _validate_manifest(
    panel_manifest: Path,
    panel_cases: Sequence[CanonicalPanelCase],
) -> GatePanelResult | None:
    try:
        payload = json.loads(panel_manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return _inconclusive(str(exc), "unknown_schema_version")

    schema_version = text_value(payload.get("schema_version"))
    if schema_version != _MANIFEST_SCHEMA_VERSION:
        return _inconclusive(
            "canonical panel manifest schema/version missing or invalid",
            "unknown_schema_version",
        )

    required_case_count = payload.get("required_case_count")
    if not isinstance(required_case_count, int):
        return _inconclusive(
            "canonical panel manifest required_case_count missing",
            "unknown_schema_version",
        )
    if required_case_count != len(panel_cases):
        return _inconclusive(
            "canonical panel manifest required_case_count does not match loaded panel",
            "canonical_panel_case_unbound",
        )
    return None


def _validate_manual_review_trigger(
    case: CanonicalPanelCase,
) -> GatePanelResult | None:
    if case.manual_review_trigger in _MANUAL_REVIEW_TRIGGERS:
        return None
    return _inconclusive(
        (
            f"{case.case_id}: unknown manual_review_trigger "
            f"{case.manual_review_trigger!r}"
        ),
        "canonical_panel_case_unbound",
    )


def _evaluate_targeted_gt_case(
    *,
    case: CanonicalPanelCase,
    targeted_gt_dirs: Mapping[str, Path],
) -> GatePanelResult | None:
    target_dir = targeted_gt_dirs.get(case.target_label)
    if target_dir is None:
        return _inconclusive(
            f"{case.case_id}: targeted GT directory is missing for {case.target_label}",
            "canonical_panel_case_unbound",
        )

    comparison_csv = Path(target_dir) / "comparison.csv"
    if not comparison_csv.is_file():
        return _inconclusive(
            f"{case.case_id}: comparison.csv is missing for {case.target_label}",
            "canonical_panel_case_unbound",
        )

    with comparison_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing_columns = [
            column
            for column in ("sample_stem", "failure_mode")
            if column not in fieldnames
        ]
        if missing_columns:
            return _inconclusive(
                (
                    f"{comparison_csv}: missing required columns: "
                    f"{', '.join(missing_columns)}"
                ),
                "missing_required_column",
            )

        saw_row = False
        for row in reader:
            saw_row = True
            failure_mode = text_value(row.get("failure_mode")).upper()
            if failure_mode == "PASS":
                continue
            if failure_mode in _TARGETED_GT_FAILURE_MODES:
                sample_stem = text_value(row.get("sample_stem")) or "unknown_sample"
                return GatePanelResult(
                    status="FAIL",
                    gate_ok=False,
                    production_ready=False,
                    reason=(
                        "targeted GT regression in "
                        f"{case.target_label} ({sample_stem}: {failure_mode})"
                    ),
                    manual_review_required=True,
                    missing_evidence_code="manual_review_required",
                )
            sample_stem = text_value(row.get("sample_stem")) or "unknown_sample"
            if not failure_mode:
                return _inconclusive(
                    (
                        f"{case.case_id}: blank failure_mode in {case.target_label} "
                        f"for {sample_stem}"
                    ),
                    "metric_source_unavailable",
                )
            return _inconclusive(
                (
                    f"{case.case_id}: unknown failure_mode {failure_mode!r} in "
                    f"{case.target_label} for {sample_stem}"
                ),
                "metric_source_unavailable",
            )
        if not saw_row:
            return _inconclusive(
                f"{case.case_id}: comparison.csv has no rows for {case.target_label}",
                "canonical_panel_case_unbound",
            )
    return None


def _evaluate_sentinel_case(
    *,
    case: CanonicalPanelCase,
    current_sentinel_summary: Mapping[str, object],
    baseline_sentinel_summary: Mapping[str, object] | None,
) -> GatePanelResult | None:
    metric_key = _SENTINEL_METRIC_KEYS.get(case.case_type)
    if metric_key is None:
        return _inconclusive(
            f"{case.case_id}: unknown sentinel case type {case.case_type}",
            "canonical_panel_case_unbound",
        )
    if baseline_sentinel_summary is None:
        return _inconclusive(
            f"{case.case_id}: baseline/current sentinel binding missing",
            "baseline_current_unbound",
        )
    if (
        metric_key not in current_sentinel_summary
        or metric_key not in baseline_sentinel_summary
    ):
        return _inconclusive(
            (
                f"{case.case_id}: sentinel metric {metric_key} "
                "missing from current/baseline summaries"
            ),
            "baseline_current_unbound",
        )

    current_value = _coerce_int(current_sentinel_summary.get(metric_key))
    baseline_value = _coerce_int(baseline_sentinel_summary.get(metric_key))
    if current_value is None or baseline_value is None:
        return _inconclusive(
            f"{case.case_id}: sentinel metric {metric_key} is not numeric",
            "baseline_current_unbound",
        )

    requires_review = False
    if case.manual_review_trigger == "on_any_current":
        requires_review = current_value > 0
    elif case.manual_review_trigger == "on_increase":
        requires_review = current_value > baseline_value
    else:
        return _inconclusive(
            (
                f"{case.case_id}: unknown manual_review_trigger "
                f"{case.manual_review_trigger!r}"
            ),
            "canonical_panel_case_unbound",
        )

    if requires_review:
        return GatePanelResult(
            status="WARN",
            gate_ok=False,
            production_ready=False,
            reason=(
                f"{case.case_id}: sentinel pressure increased for {metric_key} "
                f"(current={current_value}, baseline={baseline_value})"
            ),
            manual_review_required=True,
            missing_evidence_code="manual_review_required",
        )
    return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(text_value(value))
    except ValueError:
        return None


def _inconclusive(reason: str, missing_evidence_code: str) -> GatePanelResult:
    return GatePanelResult(
        status="INCONCLUSIVE",
        gate_ok=False,
        production_ready=False,
        reason=reason,
        manual_review_required=True,
        missing_evidence_code=missing_evidence_code,
    )


def _validate_row_shapes(panel_tsv: Path) -> None:
    with panel_tsv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        expected_width = len(next(reader, []))
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != expected_width:
                raise ValueError(
                    f"{panel_tsv}: malformed row shape at line {line_number}: "
                    f"expected {expected_width} columns, found {len(row)}"
                )
