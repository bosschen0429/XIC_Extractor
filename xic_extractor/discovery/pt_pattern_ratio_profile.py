"""No-RAW ratio profiling for Pt pattern diagnostic candidates."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
    validate_pt_candidate_evidence_envelope,
)
from xic_extractor.discovery.pt_pattern_ratio_report import (
    render_ratio_profile_html as _render_ratio_profile_html,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_ratio_profile"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_ratio_profile"
AUTHORITY_SCOPE = "ratio_profile_only"
PRIMARY_POPULATION = "primary_population"
BACKGROUND_POPULATION = "background_population"
DEFAULT_PRIMARY_TRACE_RULE_CALL = "accept"
BACKGROUND_TRACE_RULE_CALL = "reject"

RATIO_NAMES = (
    "plus1_over_base_height_ratio",
    "plus2_over_base_height_ratio",
    "plus4_over_base_height_ratio",
    "base_over_plus1_height_ratio",
    "base_over_plus2_height_ratio",
    "base_over_plus4_height_ratio",
    "fh_base_to_plus1_height_ratio",
    "fh_base_to_plus2_height_ratio",
    "fh_base_to_plus4_height_ratio",
    "plus1_over_plus2_height_ratio",
    "plus1_over_plus4_height_ratio",
    "plus1_over_base_area_ratio",
    "plus2_over_base_area_ratio",
    "plus4_over_base_area_ratio",
    "base_over_plus1_area_ratio",
    "base_over_plus2_area_ratio",
    "base_over_plus4_area_ratio",
    "fh_base_to_plus1_area_ratio",
    "fh_base_to_plus2_area_ratio",
    "fh_base_to_plus4_area_ratio",
    "plus1_over_plus2_area_ratio",
    "plus1_over_plus4_area_ratio",
)

RATIO_PROFILE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "profile_id",
    "population_label",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "max_abs_apex_delta_sec",
    "max_abs_shape_shift_sec_to_base",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "base_height",
    "base_area",
    *RATIO_NAMES,
    "ratio_outlier_count",
    "ratio_extreme_columns",
    "ratio_review_note",
)

RATIO_SUMMARY_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "profile_id",
    "population_label",
    "ratio_name",
    "row_count",
    "missing_count",
    "min",
    "q05",
    "q25",
    "median",
    "q75",
    "q95",
    "max",
    "iqr",
    "lower_outlier_fence",
    "upper_outlier_fence",
    "outlier_low_count",
    "outlier_high_count",
    "outlier_count",
)

RATIO_FORMULA_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "profile_id",
    "ratio_name",
    "formula_family",
    "numerator",
    "denominator",
    "measurement_basis",
    "interpretation_note",
)

_CANDIDATE_REQUIRED_COLUMNS = (
    *PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    "candidate_state",
    "base_height",
    "base_area",
    "pt_plus_1_height_ratio_to_base",
    "pt_plus_2_height_ratio_to_base",
    "pt_plus_4_height_ratio_to_base",
    "pt_plus_1_area_ratio_to_base",
    "pt_plus_2_area_ratio_to_base",
    "pt_plus_4_area_ratio_to_base",
)

_EVIDENCE_REQUIRED_COLUMNS = (
    *PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "max_abs_shape_shift_sec_to_base",
)


def run_ratio_profile(
    *,
    candidates_csv: Path,
    evidence_csv: Path,
    output_dir: Path,
    profile_id: str,
    primary_trace_rule_call: str = DEFAULT_PRIMARY_TRACE_RULE_CALL,
) -> dict[str, Path]:
    """Write no-RAW Pt ratio profile artifacts from existing candidate outputs."""

    if not profile_id.strip():
        raise ValueError("profile_id must be non-empty")
    if primary_trace_rule_call != DEFAULT_PRIMARY_TRACE_RULE_CALL:
        raise ValueError(
            "primary_trace_rule_call must be 'accept' because the persisted "
            "primary artifacts are explicitly the accepted population"
        )
    candidates = read_delimited_rows(
        candidates_csv,
        required_columns=_CANDIDATE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    candidate_by_key = _unique_rows_by_key(candidates, candidates_csv)
    evidence_rows = read_delimited_rows(
        evidence_csv,
        required_columns=_EVIDENCE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    evidence_by_key = _unique_rows_by_key(evidence_rows, evidence_csv)
    validate_pt_candidate_evidence_envelope(
        candidate_by_key,
        evidence_by_key,
        evidence_rows=evidence_rows,
    )
    primary_rows: list[dict[str, object]] = []
    background_rows: list[dict[str, object]] = []
    unprofiled_trace_rule_calls: list[str] = []
    for candidate in candidates:
        key = _row_key(candidate)
        evidence = evidence_by_key[key]
        row = _profile_row(candidate, evidence, profile_id=profile_id)
        trace_rule_call = text_value(evidence.get("trace_rule_call"))
        if trace_rule_call == primary_trace_rule_call:
            row["population_label"] = PRIMARY_POPULATION
            primary_rows.append(row)
        elif trace_rule_call == BACKGROUND_TRACE_RULE_CALL:
            row["population_label"] = BACKGROUND_POPULATION
            background_rows.append(row)
        else:
            unprofiled_trace_rule_calls.append(trace_rule_call)

    primary_summary = _summary_rows(
        primary_rows,
        profile_id=profile_id,
        population_label=PRIMARY_POPULATION,
    )
    background_summary = _summary_rows(
        background_rows,
        profile_id=profile_id,
        population_label=BACKGROUND_POPULATION,
    )
    primary_stats = _stats_by_ratio(primary_rows)
    primary_rows = [
        _row_with_outlier_fields(row, primary_stats) for row in primary_rows
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_csv = output_dir / "accepted_ratio_profile.csv"
    summary_csv = output_dir / "accepted_ratio_summary.csv"
    background_summary_csv = output_dir / "rejected_ratio_background_summary.csv"
    formula_csv = output_dir / "ratio_formula_manifest.csv"
    html_path = output_dir / "pt_pattern_ratio_profile.html"
    summary_json = output_dir / "pt_pattern_ratio_profile_summary.json"

    write_delimited_rows(profile_csv, primary_rows, RATIO_PROFILE_COLUMNS)
    write_delimited_rows(summary_csv, primary_summary, RATIO_SUMMARY_COLUMNS)
    write_delimited_rows(
        background_summary_csv,
        background_summary,
        RATIO_SUMMARY_COLUMNS,
    )
    write_delimited_rows(
        formula_csv,
        ratio_formula_manifest_rows(profile_id=profile_id),
        RATIO_FORMULA_COLUMNS,
    )
    html_path.write_text(
        render_ratio_profile_html(
            primary_rows=primary_rows,
            primary_summary=primary_summary,
            background_summary=background_summary,
            formula_rows=ratio_formula_manifest_rows(profile_id=profile_id),
            profile_id=profile_id,
        ),
        encoding="utf-8",
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "profile_id": profile_id,
        "candidates_csv": str(candidates_csv),
        "candidates_csv_sha256": file_sha256(candidates_csv),
        "evidence_csv": str(evidence_csv),
        "evidence_csv_sha256": file_sha256(evidence_csv),
        "candidate_row_count": len(candidates),
        "evidence_row_count": len(evidence_rows),
        "primary_trace_rule_call": primary_trace_rule_call,
        "primary_row_count": len(primary_rows),
        "background_row_count": len(background_rows),
        "unprofiled_row_count": len(unprofiled_trace_rule_calls),
        "unprofiled_trace_rule_call_counts": dict(
            sorted(Counter(unprofiled_trace_rule_calls).items())
        ),
        "missing_evidence_count": 0,
        "opens_raw": False,
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "accepted_ratio_profile_csv": str(profile_csv),
            "accepted_ratio_summary_csv": str(summary_csv),
            "rejected_ratio_background_summary_csv": str(background_summary_csv),
            "ratio_formula_manifest_csv": str(formula_csv),
            "html": str(html_path),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "accepted_ratio_profile_csv": profile_csv,
        "accepted_ratio_summary_csv": summary_csv,
        "rejected_ratio_background_summary_csv": background_summary_csv,
        "ratio_formula_manifest_csv": formula_csv,
        "html": html_path,
        "summary_json": summary_json,
    }


def ratio_formula_manifest_rows(*, profile_id: str) -> tuple[dict[str, object], ...]:
    """Describe every emitted ratio formula without assigning detector authority."""

    rows: list[dict[str, object]] = []
    for measurement_basis in ("height", "area"):
        rows.extend(
            [
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"plus1_over_base_{measurement_basis}_ratio",
                    formula_family="position_over_base",
                    numerator="+1 position",
                    denominator="base position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Direct candidate CSV ratio; useful for observed "
                        "position enrichment relative to base."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"plus2_over_base_{measurement_basis}_ratio",
                    formula_family="position_over_base",
                    numerator="+2 position",
                    denominator="base position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Direct candidate CSV ratio; useful for observed "
                        "position enrichment relative to base."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"plus4_over_base_{measurement_basis}_ratio",
                    formula_family="position_over_base",
                    numerator="+4 position",
                    denominator="base position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Direct candidate CSV ratio; useful for observed "
                        "position enrichment relative to base."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"base_over_plus1_{measurement_basis}_ratio",
                    formula_family="base_over_position",
                    numerator="base position",
                    denominator="+1 position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Base-as-numerator view; matches the historical "
                        "positive delta pair direction."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"base_over_plus2_{measurement_basis}_ratio",
                    formula_family="base_over_position",
                    numerator="base position",
                    denominator="+2 position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Base-as-numerator view; matches the historical "
                        "positive delta pair direction."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"base_over_plus4_{measurement_basis}_ratio",
                    formula_family="base_over_position",
                    numerator="base position",
                    denominator="+4 position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Base-as-numerator view; matches the historical "
                        "positive delta pair direction."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"fh_base_to_plus1_{measurement_basis}_ratio",
                    formula_family="historical_featurehunter_pair",
                    numerator="lower-m/z precursor ion",
                    denominator="higher-m/z precursor ion prime",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Alias for base/+1 retained as historical comparator, "
                        "not Pt-pattern authority."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"fh_base_to_plus2_{measurement_basis}_ratio",
                    formula_family="historical_featurehunter_pair",
                    numerator="lower-m/z precursor ion",
                    denominator="higher-m/z precursor ion prime",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Alias for base/+2 retained as historical comparator, "
                        "not Pt-pattern authority."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"fh_base_to_plus4_{measurement_basis}_ratio",
                    formula_family="historical_featurehunter_pair",
                    numerator="lower-m/z precursor ion",
                    denominator="higher-m/z precursor ion prime",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "Alias for base/+4 retained as historical comparator, "
                        "not Pt-pattern authority."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"plus1_over_plus2_{measurement_basis}_ratio",
                    formula_family="plus1_anchor",
                    numerator="+1 position",
                    denominator="+2 position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "+1-normalized review view because +1 is expected to "
                        "be a strong Pt position."
                    ),
                ),
                _formula_row(
                    profile_id=profile_id,
                    ratio_name=f"plus1_over_plus4_{measurement_basis}_ratio",
                    formula_family="plus1_anchor",
                    numerator="+1 position",
                    denominator="+4 position",
                    measurement_basis=measurement_basis,
                    interpretation_note=(
                        "+1-normalized review view because +1 is expected to "
                        "be a strong Pt position."
                    ),
                ),
            ]
        )
    return tuple(rows)


def _formula_row(
    *,
    profile_id: str,
    ratio_name: str,
    formula_family: str,
    numerator: str,
    denominator: str,
    measurement_basis: str,
    interpretation_note: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "profile_id": profile_id,
        "ratio_name": ratio_name,
        "formula_family": formula_family,
        "numerator": numerator,
        "denominator": denominator,
        "measurement_basis": measurement_basis,
        "interpretation_note": interpretation_note,
    }


def render_ratio_profile_html(
    *,
    primary_rows: Sequence[Mapping[str, object]],
    primary_summary: Sequence[Mapping[str, object]],
    background_summary: Sequence[Mapping[str, object]],
    formula_rows: Sequence[Mapping[str, object]],
    profile_id: str,
) -> str:
    """Render the chart-first diagnostic-only ratio profile page."""

    return _render_ratio_profile_html(
        primary_rows=primary_rows,
        primary_summary=primary_summary,
        background_summary=background_summary,
        formula_rows=formula_rows,
        profile_id=profile_id,
    )


def _profile_row(
    candidate: Mapping[str, str],
    evidence: Mapping[str, str],
    *,
    profile_id: str,
) -> dict[str, object]:
    plus1_height = _optional_bounded_float(
        candidate,
        "pt_plus_1_height_ratio_to_base",
        minimum=0.0,
    )
    plus2_height = _optional_bounded_float(
        candidate,
        "pt_plus_2_height_ratio_to_base",
        minimum=0.0,
    )
    plus4_height = _optional_bounded_float(
        candidate,
        "pt_plus_4_height_ratio_to_base",
        minimum=0.0,
    )
    plus1_area = _optional_bounded_float(
        candidate,
        "pt_plus_1_area_ratio_to_base",
        minimum=0.0,
    )
    plus2_area = _optional_bounded_float(
        candidate,
        "pt_plus_2_area_ratio_to_base",
        minimum=0.0,
    )
    plus4_area = _optional_bounded_float(
        candidate,
        "pt_plus_4_area_ratio_to_base",
        minimum=0.0,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "profile_id": profile_id,
        "population_label": PRIMARY_POPULATION,
        "sample_stem": text_value(candidate.get("sample_stem")),
        "method_label": text_value(candidate.get("method_label")),
        "pattern_id": text_value(candidate.get("pattern_id")),
        "candidate_id": text_value(candidate.get("candidate_id")),
        "base_mz": optional_float(candidate.get("base_mz")),
        "base_rt_apex_min": optional_float(candidate.get("base_rt_apex_min")),
        "max_abs_apex_delta_sec": _optional_bounded_float(
            evidence,
            "max_abs_apex_delta_sec",
            minimum=0.0,
        ),
        "max_abs_shape_shift_sec_to_base": _optional_bounded_float(
            evidence,
            "max_abs_shape_shift_sec_to_base",
            minimum=0.0,
        ),
        "min_pearson_r_to_base": _optional_bounded_float(
            evidence,
            "min_pearson_r_to_base",
            minimum=-1.0,
            maximum=1.0,
        ),
        "min_active_overlap_fraction": _optional_bounded_float(
            evidence,
            "min_active_overlap_fraction",
            minimum=0.0,
            maximum=1.0,
        ),
        "base_height": _optional_bounded_float(
            candidate,
            "base_height",
            minimum=0.0,
        ),
        "base_area": _optional_bounded_float(
            candidate,
            "base_area",
            minimum=0.0,
        ),
        "plus1_over_base_height_ratio": plus1_height,
        "plus2_over_base_height_ratio": plus2_height,
        "plus4_over_base_height_ratio": plus4_height,
        "base_over_plus1_height_ratio": _reciprocal(plus1_height),
        "base_over_plus2_height_ratio": _reciprocal(plus2_height),
        "base_over_plus4_height_ratio": _reciprocal(plus4_height),
        "fh_base_to_plus1_height_ratio": _reciprocal(plus1_height),
        "fh_base_to_plus2_height_ratio": _reciprocal(plus2_height),
        "fh_base_to_plus4_height_ratio": _reciprocal(plus4_height),
        "plus1_over_plus2_height_ratio": _divide(plus1_height, plus2_height),
        "plus1_over_plus4_height_ratio": _divide(plus1_height, plus4_height),
        "plus1_over_base_area_ratio": plus1_area,
        "plus2_over_base_area_ratio": plus2_area,
        "plus4_over_base_area_ratio": plus4_area,
        "base_over_plus1_area_ratio": _reciprocal(plus1_area),
        "base_over_plus2_area_ratio": _reciprocal(plus2_area),
        "base_over_plus4_area_ratio": _reciprocal(plus4_area),
        "fh_base_to_plus1_area_ratio": _reciprocal(plus1_area),
        "fh_base_to_plus2_area_ratio": _reciprocal(plus2_area),
        "fh_base_to_plus4_area_ratio": _reciprocal(plus4_area),
        "plus1_over_plus2_area_ratio": _divide(plus1_area, plus2_area),
        "plus1_over_plus4_area_ratio": _divide(plus1_area, plus4_area),
        "ratio_outlier_count": 0,
        "ratio_extreme_columns": "",
        "ratio_review_note": "",
    }


def _optional_bounded_float(
    row: Mapping[str, object],
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    raw_value = row.get(field)
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return None
    value = optional_float(raw_value)
    if value is None:
        raise ValueError(f"{field} must be a finite number or blank")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum:g}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be <= {maximum:g}")
    return value


def _summary_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    profile_id: str,
    population_label: str,
) -> tuple[dict[str, object], ...]:
    stats = _stats_by_ratio(rows)
    return tuple(
        {
            "schema_version": SCHEMA_VERSION,
            "readiness_tier": READINESS_TIER,
            "evidence_owner": EVIDENCE_OWNER,
            "authority_scope": AUTHORITY_SCOPE,
            "profile_id": profile_id,
            "population_label": population_label,
            "ratio_name": ratio_name,
            **stats[ratio_name].as_row(),
        }
        for ratio_name in RATIO_NAMES
    )


def _stats_by_ratio(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, "_RatioStats"]:
    return {
        ratio_name: _RatioStats.from_values(
            optional_float(row.get(ratio_name)) for row in rows
        )
        for ratio_name in RATIO_NAMES
    }


def _row_with_outlier_fields(
    row: Mapping[str, object],
    stats_by_ratio: Mapping[str, "_RatioStats"],
) -> dict[str, object]:
    flagged: list[str] = []
    for ratio_name in RATIO_NAMES:
        value = optional_float(row.get(ratio_name))
        if value is None:
            continue
        if stats_by_ratio[ratio_name].is_tail_value(value):
            flagged.append(ratio_name)
    updated = dict(row)
    updated["ratio_outlier_count"] = len(flagged)
    updated["ratio_extreme_columns"] = ";".join(flagged)
    updated["ratio_review_note"] = "distribution_tail_review" if flagged else ""
    return updated


class _RatioStats:
    def __init__(self, values: Sequence[float], missing_count: int) -> None:
        self.values = tuple(sorted(values))
        self.missing_count = missing_count
        self.row_count = len(values)
        self.minimum = self.values[0] if self.values else None
        self.q05 = _quantile(self.values, 0.05)
        self.q25 = _quantile(self.values, 0.25)
        self.median = _quantile(self.values, 0.50)
        self.q75 = _quantile(self.values, 0.75)
        self.q95 = _quantile(self.values, 0.95)
        self.maximum = self.values[-1] if self.values else None
        self.iqr = (
            self.q75 - self.q25
            if self.q25 is not None and self.q75 is not None
            else None
        )
        self.lower_fence = (
            self.q25 - 1.5 * self.iqr
            if self.q25 is not None and self.iqr is not None
            else None
        )
        self.upper_fence = (
            self.q75 + 1.5 * self.iqr
            if self.q75 is not None and self.iqr is not None
            else None
        )

    @classmethod
    def from_values(cls, values: Iterable[float | None]) -> "_RatioStats":
        clean_values: list[float] = []
        missing_count = 0
        for value in values:
            if value is None:
                missing_count += 1
            else:
                clean_values.append(value)
        return cls(clean_values, missing_count)

    def as_row(self) -> dict[str, object]:
        low_count = sum(
            1
            for value in self.values
            if self.lower_fence is not None and value < self.lower_fence
        )
        high_count = sum(
            1
            for value in self.values
            if self.upper_fence is not None and value > self.upper_fence
        )
        tail_low_count = sum(
            1 for value in self.values if self.q05 is not None and value < self.q05
        )
        tail_high_count = sum(
            1 for value in self.values if self.q95 is not None and value > self.q95
        )
        low_count = max(low_count, tail_low_count)
        high_count = max(high_count, tail_high_count)
        return {
            "row_count": self.row_count,
            "missing_count": self.missing_count,
            "min": self.minimum,
            "q05": self.q05,
            "q25": self.q25,
            "median": self.median,
            "q75": self.q75,
            "q95": self.q95,
            "max": self.maximum,
            "iqr": self.iqr,
            "lower_outlier_fence": self.lower_fence,
            "upper_outlier_fence": self.upper_fence,
            "outlier_low_count": low_count,
            "outlier_high_count": high_count,
            "outlier_count": low_count + high_count,
        }

    def is_tail_value(self, value: float) -> bool:
        if self.row_count < 4:
            return False
        if self.lower_fence is not None and value < self.lower_fence:
            return True
        if self.upper_fence is not None and value > self.upper_fence:
            return True
        if self.q05 is not None and value < self.q05:
            return True
        return bool(self.q95 is not None and value > self.q95)


def _unique_rows_by_key(
    rows: Sequence[Mapping[str, str]],
    path: Path,
) -> dict[tuple[str, str], Mapping[str, str]]:
    grouped: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = _row_key(row)
        if not all(key):
            raise ValueError(
                f"{path}: sample_stem/candidate_id key must be non-empty: {key!r}"
            )
        if key in grouped:
            raise ValueError(f"{path}: duplicate sample_stem/candidate_id key: {key!r}")
        grouped[key] = row
    return grouped


def _row_key(row: Mapping[str, str]) -> tuple[str, str]:
    return (text_value(row.get("sample_stem")), text_value(row.get("candidate_id")))


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _reciprocal(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return 1 / value
