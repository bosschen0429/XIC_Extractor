"""No-RAW ratio profiling for Pt pattern diagnostic candidates."""

from __future__ import annotations

import html
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    optional_float,
    read_delimited_rows,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_ratio_profile.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_ratio_profile"
AUTHORITY_SCOPE = "ratio_profile_only"
PRIMARY_POPULATION = "primary_population"
BACKGROUND_POPULATION = "background_population"
DEFAULT_PRIMARY_TRACE_RULE_CALL = "accept"

RATIO_NAMES = (
    "plus1_over_base_height_ratio",
    "plus2_over_base_height_ratio",
    "plus4_over_base_height_ratio",
    "fh_base_to_plus1_height_ratio",
    "fh_base_to_plus2_height_ratio",
    "fh_base_to_plus4_height_ratio",
    "plus1_over_plus2_height_ratio",
    "plus1_over_plus4_height_ratio",
    "plus1_over_base_area_ratio",
    "plus2_over_base_area_ratio",
    "plus4_over_base_area_ratio",
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

_CANDIDATE_REQUIRED_COLUMNS = (
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
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
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "trace_rule_call",
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
    candidates = read_delimited_rows(
        candidates_csv,
        required_columns=_CANDIDATE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    evidence_rows = read_delimited_rows(
        evidence_csv,
        required_columns=_EVIDENCE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    evidence_by_key = _unique_rows_by_key(evidence_rows, evidence_csv)
    primary_rows: list[dict[str, object]] = []
    background_rows: list[dict[str, object]] = []
    missing_evidence_count = 0
    for candidate in candidates:
        key = _row_key(candidate)
        evidence = evidence_by_key.get(key)
        if evidence is None:
            missing_evidence_count += 1
            continue
        row = _profile_row(candidate, evidence, profile_id=profile_id)
        if text_value(evidence.get("trace_rule_call")) == primary_trace_rule_call:
            row["population_label"] = PRIMARY_POPULATION
            primary_rows.append(row)
        else:
            row["population_label"] = BACKGROUND_POPULATION
            background_rows.append(row)

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
    html_path = output_dir / "pt_pattern_ratio_profile.html"
    summary_json = output_dir / "pt_pattern_ratio_profile_summary.json"

    write_delimited_rows(profile_csv, primary_rows, RATIO_PROFILE_COLUMNS)
    write_delimited_rows(summary_csv, primary_summary, RATIO_SUMMARY_COLUMNS)
    write_delimited_rows(
        background_summary_csv,
        background_summary,
        RATIO_SUMMARY_COLUMNS,
    )
    html_path.write_text(
        render_ratio_profile_html(
            primary_rows=primary_rows,
            primary_summary=primary_summary,
            background_summary=background_summary,
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
        "missing_evidence_count": missing_evidence_count,
        "opens_raw": False,
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "accepted_ratio_profile_csv": str(profile_csv),
            "accepted_ratio_summary_csv": str(summary_csv),
            "rejected_ratio_background_summary_csv": str(background_summary_csv),
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
        "html": html_path,
        "summary_json": summary_json,
    }


def render_ratio_profile_html(
    *,
    primary_rows: Sequence[Mapping[str, object]],
    primary_summary: Sequence[Mapping[str, object]],
    background_summary: Sequence[Mapping[str, object]],
    profile_id: str,
) -> str:
    """Render a static diagnostic-only ratio profile page."""

    outlier_rows = [
        row for row in primary_rows if text_value(row.get("ratio_outlier_count")) != "0"
    ]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Pt Pattern Ratio Profile</title>",
            "<style>",
            _html_css(),
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            "<header>",
            "<h1>Pt Pattern Ratio Profile</h1>",
            f"<p>{_e(profile_id)} · {READINESS_TIER} · ratio profile only</p>",
            "</header>",
            '<section class="summary-grid">',
            _metric_card("primary population", len(primary_rows)),
            _metric_card(
                "background population",
                _population_count(background_summary),
            ),
            _metric_card("tail review rows", len(outlier_rows)),
            "</section>",
            "<section>",
            "<h2>Distribution Summary</h2>",
            _summary_table(primary_summary, background_summary),
            "</section>",
            "<section>",
            "<h2>RT / Ratio Scatter</h2>",
            _scatter_svg(primary_rows),
            "</section>",
            "<section>",
            "<h2>Tail Review Rows</h2>",
            _outlier_table(outlier_rows),
            "</section>",
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _profile_row(
    candidate: Mapping[str, str],
    evidence: Mapping[str, str],
    *,
    profile_id: str,
) -> dict[str, object]:
    plus1_height = optional_float(candidate.get("pt_plus_1_height_ratio_to_base"))
    plus2_height = optional_float(candidate.get("pt_plus_2_height_ratio_to_base"))
    plus4_height = optional_float(candidate.get("pt_plus_4_height_ratio_to_base"))
    plus1_area = optional_float(candidate.get("pt_plus_1_area_ratio_to_base"))
    plus2_area = optional_float(candidate.get("pt_plus_2_area_ratio_to_base"))
    plus4_area = optional_float(candidate.get("pt_plus_4_area_ratio_to_base"))
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
        "max_abs_apex_delta_sec": optional_float(
            evidence.get("max_abs_apex_delta_sec")
        ),
        "max_abs_shape_shift_sec_to_base": optional_float(
            evidence.get("max_abs_shape_shift_sec_to_base")
        ),
        "min_pearson_r_to_base": optional_float(
            evidence.get("min_pearson_r_to_base")
        ),
        "min_active_overlap_fraction": optional_float(
            evidence.get("min_active_overlap_fraction")
        ),
        "base_height": optional_float(candidate.get("base_height")),
        "base_area": optional_float(candidate.get("base_area")),
        "plus1_over_base_height_ratio": plus1_height,
        "plus2_over_base_height_ratio": plus2_height,
        "plus4_over_base_height_ratio": plus4_height,
        "fh_base_to_plus1_height_ratio": _reciprocal(plus1_height),
        "fh_base_to_plus2_height_ratio": _reciprocal(plus2_height),
        "fh_base_to_plus4_height_ratio": _reciprocal(plus4_height),
        "plus1_over_plus2_height_ratio": _divide(plus1_height, plus2_height),
        "plus1_over_plus4_height_ratio": _divide(plus1_height, plus4_height),
        "plus1_over_base_area_ratio": plus1_area,
        "plus2_over_base_area_ratio": plus2_area,
        "plus4_over_base_area_ratio": plus4_area,
        "fh_base_to_plus1_area_ratio": _reciprocal(plus1_area),
        "fh_base_to_plus2_area_ratio": _reciprocal(plus2_area),
        "fh_base_to_plus4_area_ratio": _reciprocal(plus4_area),
        "plus1_over_plus2_area_ratio": _divide(plus1_area, plus2_area),
        "plus1_over_plus4_area_ratio": _divide(plus1_area, plus4_area),
        "ratio_outlier_count": 0,
        "ratio_extreme_columns": "",
        "ratio_review_note": "",
    }


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
        if key in grouped:
            raise ValueError(
                f"{path}: duplicate sample_stem/candidate_id key: {key!r}"
            )
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


def _population_count(rows: Sequence[Mapping[str, object]]) -> int:
    if not rows:
        return 0
    return int(optional_float(rows[0].get("row_count")) or 0)


def _summary_table(
    primary_summary: Sequence[Mapping[str, object]],
    background_summary: Sequence[Mapping[str, object]],
) -> str:
    rows = [
        row
        for row in (*primary_summary, *background_summary)
        if text_value(row.get("ratio_name")).endswith("_height_ratio")
    ]
    header = (
        "<tr><th>population</th><th>ratio</th><th>n</th><th>median</th>"
        "<th>q25</th><th>q75</th><th>q95</th><th>tail rows</th></tr>"
    )
    body = "\n".join(
        "<tr>"
        f"<td>{_e(_display_population(row.get('population_label')))}</td>"
        f"<td>{_e(row.get('ratio_name'))}</td>"
        f"<td>{_e(row.get('row_count'))}</td>"
        f"<td>{_e(row.get('median'))}</td>"
        f"<td>{_e(row.get('q25'))}</td>"
        f"<td>{_e(row.get('q75'))}</td>"
        f"<td>{_e(row.get('q95'))}</td>"
        f"<td>{_e(row.get('outlier_count'))}</td>"
        "</tr>"
        for row in rows
    )
    return f'<table class="data-table">{header}<tbody>{body}</tbody></table>'


def _outlier_table(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return "<p>No tail review rows in the primary population.</p>"
    header = (
        "<tr><th>candidate</th><th>m/z</th><th>RT min</th>"
        "<th>tail count</th><th>ratio fields</th></tr>"
    )
    body = "\n".join(
        "<tr>"
        f"<td>{_e(row.get('candidate_id'))}</td>"
        f"<td>{_e(row.get('base_mz'))}</td>"
        f"<td>{_e(row.get('base_rt_apex_min'))}</td>"
        f"<td>{_e(row.get('ratio_outlier_count'))}</td>"
        f"<td>{_e(row.get('ratio_extreme_columns'))}</td>"
        "</tr>"
        for row in rows[:50]
    )
    return f'<table class="data-table">{header}<tbody>{body}</tbody></table>'


def _scatter_svg(rows: Sequence[Mapping[str, object]]) -> str:
    points = [
        (
            optional_float(row.get("base_rt_apex_min")),
            optional_float(row.get("plus1_over_plus4_height_ratio")),
        )
        for row in rows
    ]
    points = [(x, y) for x, y in points if x is not None and y is not None]
    if not points:
        return "<p>No plottable ratio values.</p>"
    xs = [point[0] for point in points]
    ys = [math.log10(point[1]) if point[1] > 0 else 0 for point in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    plot_width = 820
    plot_height = 280
    left_pad = 52
    top_pad = 24
    inner_width = plot_width - 84
    inner_height = plot_height - 64
    circles = []
    for x_value, y_ratio in points:
        y_value = math.log10(y_ratio) if y_ratio > 0 else 0
        x = left_pad + _scale(x_value, x_min, x_max, inner_width)
        y = top_pad + inner_height - _scale(y_value, y_min, y_max, inner_height)
        circles.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" />')
    return (
        f'<svg viewBox="0 0 {plot_width} {plot_height}" '
        'role="img" aria-label="RT by plus one over plus four height ratio">'
        f'<rect x="0" y="0" width="{plot_width}" height="{plot_height}" />'
        f'<line x1="{left_pad}" y1="{top_pad + inner_height}" '
        f'x2="{left_pad + inner_width}" y2="{top_pad + inner_height}" />'
        f'<line x1="{left_pad}" y1="{top_pad}" '
        f'x2="{left_pad}" y2="{top_pad + inner_height}" />'
        + "".join(circles)
        + '<text x="52" y="268">RT min</text>'
        + '<text x="52" y="18">log10(+1/+4 height)</text>'
        + "</svg>"
    )


def _scale(value: float, minimum: float, maximum: float, size: float) -> float:
    if math.isclose(minimum, maximum):
        return size / 2
    return (value - minimum) / (maximum - minimum) * size


def _metric_card(label: str, value: int) -> str:
    return (
        '<article class="metric-card">'
        f"<strong>{_e(value)}</strong><span>{_e(label)}</span>"
        "</article>"
    )


def _display_population(value: object) -> str:
    label = text_value(value)
    if label == PRIMARY_POPULATION:
        return "primary population"
    if label == BACKGROUND_POPULATION:
        return "background population"
    return label


def _e(value: object) -> str:
    return html.escape(format_diagnostic_value(value))


def _html_css() -> str:
    return """
:root {
  color: #1f2933;
  background: #f5f7f8;
  font-family: Inter, Segoe UI, Arial, sans-serif;
}
body { margin: 0; }
main { max-width: 1160px; margin: 0 auto; padding: 28px; }
header { margin-bottom: 20px; }
h1 { margin: 0 0 6px; font-size: 30px; letter-spacing: 0; }
h2 { margin-top: 30px; font-size: 20px; letter-spacing: 0; }
p { color: #52606d; }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
.metric-card {
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  background: #ffffff;
  padding: 16px;
}
.metric-card strong {
  display: block;
  font-size: 28px;
  line-height: 1.1;
}
.metric-card span {
  display: block;
  margin-top: 6px;
  color: #52606d;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  background: #ffffff;
  border: 1px solid #d9e2ec;
}
.data-table th,
.data-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #e4e7eb;
  text-align: left;
  font-size: 13px;
}
.data-table th { background: #eef2f4; color: #35424f; }
svg {
  display: block;
  width: 100%;
  height: auto;
  background: #ffffff;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
}
svg rect { fill: #ffffff; }
svg line { stroke: #9fb3c8; stroke-width: 1; }
svg circle { fill: #1f7a8c; opacity: 0.72; }
svg text { fill: #52606d; font-size: 13px; }
"""
