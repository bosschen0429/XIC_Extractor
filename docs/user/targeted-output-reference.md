# Targeted Output Reference

Column-level reference for every sheet in the Targeted Extraction Excel workbook.
The workbook is written to `<output_dir>/xic_results_YYYYMMDD_HHMM.xlsx`.

`TargetKey`, `TargetDefinition`, `TargetRowBinding`, typed no-write reasons,
and archive references are internal lifecycle records. The Review Queue is
the user-facing location for Targeted unresolved and diagnostic reasons.

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
| Area | float | Selected shared physical peak's Gaussian-smoothed positive AsLS residual area (counts·seconds) |
| NL | string | Neutral loss status: `OK`, `WARN_*`, `NL_FAIL`, `NO_MS2` |
| IntSmoothed | float | Selected physical peak's Gaussian-smoothed positive AsLS residual apex intensity |
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
| Area | float | Selected peak area (counts·seconds) |
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
| signal_processing_method | `gaussian` | Shared physical signal smoothing method |
| signal_processing_window_points | `15` | Gaussian smoothing window (points) |
| targeted_output_schema_version | `targeted_output` | Output schema version |
| limited_identity_gate | `pass` | Completed method-specific gate, when the limited workflow runs |

---

## Embedded decision inputs

Nonempty supplied `Sample Context`, `Identity Support`, and `Selection Approvals`
are hidden technical sheets. They preserve the input tables used for sample roles/
order and admitted evidence, rather than requiring external sidecars. They are
not new decisions or evidence of scientific readiness. Empty inputs do not add
sheets. `Targets` includes `Sample applicability`; `Run Metadata` includes the
actual method settings, not a link to a separate manifest.
