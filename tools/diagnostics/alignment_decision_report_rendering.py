"""HTML section rendering for the alignment decision diagnostic report."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tools.diagnostics.alignment_decision_report_components import (
    _bar_list,
    _details_table,
    _float_value,
    _fmt,
    _h,
    _identity_color,
    _istd_tile,
    _metric_grid,
    _not_provided_html,
    _runtime_text,
    _section,
    _stacked_bar,
    _status_cards,
)
from tools.diagnostics.alignment_decision_report_styles import report_css


def render_html(report: Mapping[str, Any]) -> str:
    verdict = str(report["verdict"])
    title = "Alignment Decision Report"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Alignment Decision Report</title>",
            "<style>",
            report_css(),
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            f"<h1>{_h(title)}</h1>",
            (
                f'<div class="verdict verdict-{_h(verdict.lower())}">'
                f"Run Verdict: {_h(verdict)}</div>"
            ),
            _run_verdict_section(report),
            _istd_section(report["istd"]),
            _cleanliness_section(report["cleanliness"]),
            _economics_section(report["economics"]),
            _rt_normalization_section(report["rt_normalization"]),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def _run_verdict_section(report: Mapping[str, Any]) -> str:
    run = report["run"]
    identity = run["identity_counts"]
    runtime = run["runtime"]
    top_stage_rows = [
        (
            row.get("stage", ""),
            _fmt(row.get("elapsed_sec", "")),
        )
        for row in runtime.get("top_stages", ())
        if isinstance(row, Mapping)
    ]
    runtime_table = ""
    if top_stage_rows:
        runtime_table = "<h3>Runtime Stages</h3>" + _bar_list(
            (
                (str(row[0]), _float_value(row[1], default=0.0), "runtime")
                for row in top_stage_rows
            ),
            empty="Runtime stages not provided.",
        ) + _details_table(
            ("Stage", "Elapsed Sec"),
            top_stage_rows,
            label="Runtime stage table",
        )
    visual = (
        '<div class="visual-panel">'
        + _status_cards(
            (
                ("Matrix Rows", run["matrix_row_count"], "neutral"),
                ("Samples", run["sample_count"], "neutral"),
                ("ISTD Pass", run["istd_pass_count"], "pass"),
                ("ISTD Warn", run["istd_warning_count"], "warn"),
                ("ISTD Known", run["istd_known_count"], "warn"),
                ("ISTD Fail", run["istd_fail_count"], "fail"),
            )
        )
        + "<h3>Identity Mix</h3>"
        + _stacked_bar(
            (
                (
                    "production_family",
                    identity.get("production_family", 0),
                    "production",
                ),
                (
                    "provisional_discovery",
                    identity.get("provisional_discovery", 0),
                    "provisional",
                ),
                ("audit_family", identity.get("audit_family", 0), "audit"),
            )
        )
        + "</div>"
    )
    return _section(
        "Run Verdict",
        visual
        + _metric_grid(
            (
                ("Verdict", report["verdict"]),
                ("Alignment Dir", report["alignment_dir"]),
                ("Matrix Rows", run["matrix_row_count"]),
                ("Samples", run["sample_count"]),
                ("production_family", identity.get("production_family", 0)),
                ("provisional_discovery", identity.get("provisional_discovery", 0)),
                ("audit_family", identity.get("audit_family", 0)),
                ("ISTD Pass", run["istd_pass_count"]),
                ("ISTD Warn", run["istd_warning_count"]),
                ("ISTD Known", run["istd_known_count"]),
                ("ISTD Fail", run["istd_fail_count"]),
                ("Runtime", _runtime_text(runtime)),
            )
        )
        + runtime_table,
    )


def _istd_section(istd: Mapping[str, Any]) -> str:
    if not istd["provided"]:
        return _section("ISTD Benchmark", _not_provided_html(istd["reason"]))
    tiles = "".join(_istd_tile(row) for row in istd["rows"])
    headers = (
        "Target",
        "Status",
        "Known",
        "Selected Family",
        "Primary Hits",
        "RT Mean Delta",
        "RT p95",
        "Spearman",
        "Pearson",
        "Coverage",
        "Failure Modes",
    )
    rows = [
        (
            row["target_label"],
            row["status"],
            "KNOWN" if row["known"] else "",
            row["selected_family"],
            row["primary_hit_count"],
            _fmt(row["rt_mean_delta"]),
            _fmt(row["rt_p95"]),
            _fmt(row["spearman"]),
            _fmt(row["pearson"]),
            row["coverage"],
            row["failure_modes"],
        )
        for row in istd["rows"]
    ]
    return _section(
        "ISTD Benchmark",
        f'<div class="istd-board">{tiles}</div>'
        + _details_table(headers, rows, label="ISTD benchmark table"),
    )


def _cleanliness_section(cleanliness: Mapping[str, Any]) -> str:
    flags = cleanliness["flag_counts"]
    identity = cleanliness["identity_counts"]
    visual = (
        '<div class="visual-panel">'
        + "<h3>Primary Matrix Warning Load</h3>"
        + _bar_list(
            (
                (
                    "Zero-present rows",
                    cleanliness["zero_present_row_count"],
                    "fail",
                ),
                (
                    "duplicate_claim_pressure",
                    flags["duplicate_claim_pressure"],
                    "warn",
                ),
                (
                    "high_backfill_dependency",
                    flags["high_backfill_dependency"],
                    "warn",
                ),
                ("rescue_heavy", flags["rescue_heavy"], "rescue"),
            ),
            empty="No matrix cleanliness warnings.",
        )
        + "<h3>All Review Row Identity Mix</h3>"
        + _stacked_bar(
            (
                (
                    "production_family",
                    identity.get("production_family", 0),
                    "production",
                ),
                (
                    "provisional_discovery",
                    identity.get("provisional_discovery", 0),
                    "provisional",
                ),
                ("audit_family", identity.get("audit_family", 0), "audit"),
            )
        )
        + "</div>"
    )
    metrics = _metric_grid(
        (
            ("Primary Rows", cleanliness["primary_row_count"]),
            ("Review Primary Rows", cleanliness["review_primary_row_count"]),
            ("Zero-present Rows", cleanliness["zero_present_row_count"]),
            ("duplicate_claim_pressure", flags["duplicate_claim_pressure"]),
            ("high_backfill_dependency", flags["high_backfill_dependency"]),
            ("rescue_heavy", flags["rescue_heavy"]),
        )
    )
    rows = [
        (
            row["feature_family_id"],
            row["identity_decision"],
            row["present_rate"],
            row["detected_count"],
            row["accepted_backfill_count"],
            row["row_flags"],
            row["warning"],
        )
        for row in cleanliness["top_warning_rows"]
    ]
    table = _details_table(
        (
            "Feature",
            "Identity",
            "Present Rate",
            "Detected",
            "Accepted Rescue",
            "Flags",
            "Warning",
        ),
        rows,
        empty="No primary cleanliness warnings.",
        label="Top warning rows table",
    )
    return _section("Matrix Cleanliness", visual + metrics + table)


def _economics_section(economics: Mapping[str, Any]) -> str:
    if not economics["provided"]:
        return _section("Backfill Economics", _not_provided_html(economics["reason"]))
    totals = economics["totals"]
    request_total = _float_value(totals.get("request_target_count", 0), default=0.0)
    production = _float_value(
        totals.get("production_request_target_count", 0),
        default=0.0,
    )
    non_primary = _float_value(
        totals.get("non_primary_request_target_count", 0),
        default=0.0,
    )
    rescued = _float_value(totals.get("rescued_target_count", 0), default=0.0)
    absent = _float_value(totals.get("absent_target_count", 0), default=0.0)
    duplicate = _float_value(
        totals.get("duplicate_assigned_target_count", 0),
        default=0.0,
    )
    outcome_other = max(request_total - rescued - absent - duplicate, 0.0)
    visual = (
        '<div class="visual-panel">'
        + "<h3>Request Ownership</h3>"
        + _stacked_bar(
            (
                ("Production", production, "production"),
                ("Non-primary", non_primary, "audit"),
            )
        )
        + "<h3>Request Outcomes</h3>"
        + _stacked_bar(
            (
                ("Rescued", rescued, "production"),
                ("Absent", absent, "fail"),
                ("Duplicate", duplicate, "warn"),
                ("Other", outcome_other, "neutral"),
            )
        )
        + "<h3>Largest Feature Costs</h3>"
        + _bar_list(
            (
                (
                    str(row.get("feature_family_id", "")),
                    _float_value(
                        row.get("request_extract_count_estimate", 0),
                        default=0.0,
                    ),
                    _identity_color(str(row.get("identity_decision", ""))),
                )
                for row in economics["top_expensive_families"][:12]
            ),
            empty="No feature-level economics rows.",
        )
        + "</div>"
    )
    metrics = _metric_grid(
        (
            ("Request Targets", totals.get("request_target_count", 0)),
            ("Extract Estimate", totals.get("request_extract_count_estimate", 0)),
            (
                "Production Requests",
                totals.get("production_request_target_count", 0),
            ),
            ("Non-primary Requests", totals.get("non_primary_request_target_count", 0)),
            ("Rescued Targets", totals.get("rescued_target_count", 0)),
            ("Absent Targets", totals.get("absent_target_count", 0)),
            ("Duplicate Targets", totals.get("duplicate_assigned_target_count", 0)),
        )
    )
    summary_rows = [
        (
            row.get("identity_decision", ""),
            row.get("neutral_loss_tag", ""),
            row.get("include_in_primary_matrix", ""),
            row.get("eligible_group_family_count", ""),
            row.get("request_target_count", ""),
            row.get("request_extract_count_estimate", ""),
            row.get("rescued_target_count", ""),
            row.get("absent_target_count", ""),
            row.get("duplicate_assigned_target_count", ""),
        )
        for row in economics["summary"]
    ]
    feature_rows = [
        (
            row.get("feature_family_id", ""),
            row.get("neutral_loss_tag", ""),
            row.get("identity_decision", ""),
            row.get("include_in_primary_matrix", ""),
            row.get("request_target_count", ""),
            row.get("request_extract_count_estimate", ""),
            row.get("rescued_target_count", ""),
            row.get("absent_target_count", ""),
            row.get("duplicate_assigned_target_count", ""),
            row.get("row_flags", ""),
        )
        for row in economics["top_expensive_families"]
    ]
    return _section(
        "Backfill Economics",
        visual
        + metrics
        + "<h3>By Identity / Tag</h3>"
        + _bar_list(
            (
                (
                    f"{row.get('identity_decision', '')} / "
                    f"{row.get('neutral_loss_tag', '')}",
                    _float_value(row.get("request_target_count", 0), default=0.0),
                    _identity_color(str(row.get("identity_decision", ""))),
                )
                for row in economics["summary"]
            ),
            empty="No eligible backfill requests.",
        )
        + _details_table(
            (
                "Identity",
                "Tag",
                "Primary",
                "Families",
                "Targets",
                "Extracts",
                "Rescued",
                "Absent",
                "Duplicate",
            ),
            summary_rows,
            empty="No eligible backfill requests.",
            label="Backfill identity and tag table",
        )
        + "<h3>Top Expensive Families</h3>"
        + _details_table(
            (
                "Feature",
                "Tag",
                "Identity",
                "Primary",
                "Targets",
                "Extracts",
                "Rescued",
                "Absent",
                "Duplicate",
                "Flags",
            ),
            feature_rows,
            empty="No feature-level economics rows.",
            label="Top expensive families table",
        ),
    )


def _rt_normalization_section(rt_norm: Mapping[str, Any]) -> str:
    if not rt_norm.get("provided"):
        return ""
    loo_rows = [
        (
            str(row.get("target_label", "")),
            _float_value(row.get("p95_abs_error_min", 0), default=0.0),
            "pass" if str(row.get("status", "")).upper() == "PASS" else "warn",
        )
        for row in rt_norm["leave_one_anchor_out"]
    ]
    band_rows: list[tuple[str, float, str]] = []
    for band, counts in rt_norm["rt_band_summary"].items():
        for outcome, tone in (
            ("improved", "production"),
            ("worsened", "fail"),
            ("stable", "neutral"),
            ("unmodelled", "audit"),
        ):
            value = _float_value(counts.get(outcome, 0), default=0.0)
            if value > 0:
                band_rows.append((f"{band} / {outcome}", value, tone))
    metrics = _metric_grid(
        (
            ("Status", rt_norm.get("overall_status", "")),
            ("Reference Source", rt_norm.get("reference_source", "")),
            ("Model Type", rt_norm.get("model_type", "")),
            ("Anchor Labels", rt_norm.get("anchor_label_count", "")),
            ("Modelled Samples", rt_norm.get("modelled_sample_count", "")),
            ("Unmodelled Samples", rt_norm.get("unmodelled_sample_count", "")),
            ("Excluded Anchors", rt_norm.get("excluded_anchor_count", "")),
            (
                "Median RT Range Improvement",
                rt_norm.get("median_rt_range_improvement_min", ""),
            ),
        )
    )
    loo_table_rows = [
        (
            row.get("target_label", ""),
            row.get("status", ""),
            row.get("evaluated_count", ""),
            _fmt(row.get("median_abs_error_min", "")),
            _fmt(row.get("p95_abs_error_min", "")),
            _fmt(row.get("max_abs_error_min", "")),
        )
        for row in rt_norm["leave_one_anchor_out"]
    ]
    return _section(
        "RT Warping Evidence",
        '<div class="visual-panel">'
        + "<h3>RT Band Outcome</h3>"
        + _bar_list(band_rows, empty="No RT band outcome rows.")
        + "<h3>Leave-one-anchor-out p95 error</h3>"
        + _bar_list(loo_rows, empty="No leave-one-anchor-out rows.")
        + "</div>"
        + metrics
        + _details_table(
            (
                "Anchor",
                "Status",
                "Evaluated",
                "Median Abs Error",
                "p95 Abs Error",
                "Max Abs Error",
            ),
            loo_table_rows,
            label="Leave-one-anchor-out table",
            empty="No leave-one-anchor-out rows.",
        ),
    )
