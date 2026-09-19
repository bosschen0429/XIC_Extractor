# Targeted Output Reference

Column-level reference for every sheet in the Targeted Extraction Excel workbook.
The workbook is written to `<output_dir>/<batch_name>_xic_results.xlsx`.

This page documents the current public workbook, not the frozen internal
architecture migration. `TargetKey`, `TargetDefinition`, `TargetRowBinding`,
typed no-write reasons, and archive references remain internal until a
separately versioned schema/expected-diff promotion changes these columns. The
existing Review Queue remains the user-facing location for Targeted unresolved
and diagnostic reasons.

## Overview Sheet

Summary dashboard — no tabular columns. Displays:

- Sample count, target count, review item count, diagnostics count
- Top 5 targets and top 5 samples by review items

---

## XIC Results

One row per sample-target pair. This is the primary quantitative output.

### Always-Visible Columns

| Column | Type | Description |
| --- | --- | --- |
| SampleName | string | Sample identifier (from RAW filename) |
| Group | string | Sample group (e.g., QC, Sample) |
| Target | string | Target compound label |
| Role | string | `Analyte` or `ISTD` |
| ISTD Pair | string | Paired internal standard label |
| RT | float | Smoothed peak apex retention time (min) |
| Area | float | Gaussian15-smoothed positive AsLS residual area |
| NL | string | Neutral loss status: `OK`, `WARN_*`, `NL_FAIL`, `NO_MS2` |
| IntSmoothed | float | Existing selected peak's Gaussian-smoothed apex intensity |
| Confidence | string | `HIGH`, `MEDIUM`, `LOW`, or `VERY_LOW` |
| Reason | string | Human-readable explanation of the result |
| Product State | string | Product quantification state |
| Counted Detection | string | Whether this row counts as a detection |
| Review State | string | Review flag status |

### Advanced Columns (hidden by default)

| Column | Type | Description |
| --- | --- | --- |
| Int | float | Raw apex intensity |
| PeakStart | float | Peak start RT (min) |
| PeakEnd | float | Peak end RT (min) |
| PeakWidth | float | Peak width (min) |
| Projection Reason | string | Reason for projection assignment |
| Projection Support Reasons | string | Supporting evidence |
| Projection Review Reasons | string | Review-related reasons |
| Projection Conflict Reasons | string | Conflicting factors |
| Projection Not Counted Reasons | string | Reasons not counted |
| Projection Exclusion Reasons | string | Exclusion reasons |
| Legacy Authority Status | string | Legacy authority status |
| Benchmark Eligibility State | string | Benchmark eligibility |

`Int` retains the selected owner's raw apex height. `IntSmoothed` renders that
same selection's existing smoothed height; the writer does not smooth again or
change RT, Area, peak selection, or counting. Non-counted rows render both
intensities as `ND`, and file errors render both as `ERROR`. Long CSV adds
`IntSmoothed` immediately after `Int`; wide CSV adds `<Target>_IntSmoothed`.
These additive fields are versioned as `targeted_output` and
`targeted_long_csv`. They do not establish chemical identity or readiness.

---

## Summary

One row per target. Aggregates detection statistics across all samples.

| Column | Type | Description |
| --- | --- | --- |
| Target | string | Target label |
| Role | string | `Analyte` or `ISTD` |
| ISTD Pair | string | Paired ISTD (if applicable) |
| Detected | int | Samples with detection |
| Total | int | Total sample count |
| Detection % | string | Detection percentage |
| Median Area (detected) | float | Median area of detected samples |
| Mean RT | float | Mean retention time across detections |
| Area / ISTD ratio (paired detected) | string | Ratio with CV (if ISTD-paired) |
| RT Delta vs ISTD | string | RT difference from ISTD (if paired) |
| NL OK | int | Count of `OK` neutral loss |
| NL WARN | int | Count of `WARN_*` neutral loss |
| NL FAIL | int | Count of `NL_FAIL` |
| NO MS2 | int | Count of `NO_MS2` |
| Confidence HIGH | int | High confidence count |
| Confidence MEDIUM | int | Medium confidence count |
| Confidence LOW | int | Low confidence count |
| Confidence VERY_LOW | int | Very low confidence count |
| Flagged Rows | int | Rows flagged for review |
| Flagged % | float | Percentage flagged |
| MS2/NL Flags | int | MS2 or NL flag count |
| Low Confidence Rows | int | Low confidence row count |

---

## Review Queue

One row per flagged sample-target pair. Sorted by priority.

| Column | Type | Description |
| --- | --- | --- |
| Priority | int | 1 = high, 2 = medium, 3+ = low |
| Sample | string | Sample name |
| Target | string | Target label |
| Role | string | `Analyte` or `ISTD` |
| Status | string | Detection status |
| Why | string | Reason for review flag |
| RT | float | Retention time (min) |
| Area | float | Peak area |
| Action | string | Recommended action |
| Issue Count | int | Number of issues |
| Evidence | string | Supporting evidence |

---

## Targets

One row per configured target. Mirrors `targets.csv` with computed columns.

| Column | Type | Description |
| --- | --- | --- |
| Label | string | Target identifier |
| Role | string | `ISTD` or `Analyte` |
| ISTD Pair | string | Associated ISTD target |
| m/z | float | Mass-to-charge ratio |
| RT min | float | RT window start (min) |
| RT max | float | RT window end (min) |
| ppm tol | float | PPM tolerance |
| NL (Da) | float | Neutral loss (Da) |
| Expected product m/z | float | `m/z - NL` |
| NL ppm warn | float | NL PPM warning threshold |
| NL ppm max | float | NL PPM maximum threshold |

---

## Diagnostics

One row per detected issue. Useful for identifying systematic problems.

| Column | Type | Description |
| --- | --- | --- |
| SampleName | string | Sample name |
| Target | string | Target label |
| Issue | string | Issue type (`NL_FAIL`, `NO_MS2`, `PEAK_NOT_FOUND`, etc.) |
| Reason | string | Detailed explanation |

---

## Run Metadata

Key-value pairs recording settings and provenance for the run.

| Key | Example value | Description |
| --- | --- | --- |
| config_hash | `a3f2...` | SHA-256 hash of config |
| app_version | `1.2.0` | Application version |
| generated_at | `2026-06-27T10:00:00` | ISO 8601 timestamp |
| resolver_mode | `region_first_safe_merge` | Peak separation algorithm |
| smooth_window | `15` | Smoothing window (points) |
| smooth_polyorder | `3` | Polynomial order |
| targeted_output_schema_version | `targeted_output` | Output schema version |
| method_manifest_sha256 | `b7c4...` | Method manifest hash |

---

## Score Breakdown (optional)

Enabled by setting `emit_score_breakdown=true`. One row per detection with
fine-grained scoring components.

Key columns include: `symmetry`, `local_sn`, `nl_support`, `rt_prior`,
`rt_centrality`, `noise_shape`, `peak_width`, `Quality Penalty`,
`Total Severity`, `Base Score`, `Positive Points`, `Negative Points`,
`Raw Score`, `Final Confidence`, `Quality Flags`, `Caps`, `Support`,
`Concerns`.

The exact column set depends on the active scoring model and may change
between versions.
