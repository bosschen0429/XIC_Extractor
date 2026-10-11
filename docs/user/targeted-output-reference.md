# Targeted Output Reference

The normal completed Targeted run delivers one `xic_results_YYYYMMDD_HHMM.xlsx`
inside its run directory. Workbook layout 2 opens on `Overview`, followed by
`XIC Results`. `Targets`, `Run Metadata` and the nonempty `Diagnostics` appendix
are supporting references, not the first reading layer.
Inspector can read this layout and legacy workbooks with a separate Review Queue.
Opening a workbook for review does not modify it or create sidecar files.

## Overview: start here

The combined overview/summary answers three questions before showing row details:

1. How many samples, targets and sample-target results are represented?
2. How many saved results count as detections, and how many need review?
3. Which targets should be inspected first, and why?

Targets with review conditions appear first. Links lead to their sample rows in
`XIC Results`; filter that table by Target or Review. Counted detections and review
conditions may overlap. Missing legacy detection decisions remain unknown, not
inferred from Area, NL or review status. No review condition is not a batch pass.

A collapsed row group contains per-target median Area and RT range for counted
detections only. Expand it when assessing quantities. Reference links at the
bottom lead to target settings, run metadata and, when present, diagnostic text.
There is no separate Summary dashboard or duplicated Review Queue table.

## XIC Results

One row per sample-target pair, with repeated sample labels, Excel filters and
frozen identifiers. The first seven columns answer the everyday review questions;
expand the grouped secondary columns only when more evidence is needed.

### Main columns

| Column | Meaning |
| --- | --- |
| SampleName | Sample identifier from the RAW filename |
| Target | Target label; method inputs and ISTD pairing are in `Targets` |
| Area | Selected shared physical peak's Gaussian-smoothed positive AsLS residual area (counts·seconds) |
| RT | Smoothed peak apex retention time (min) |
| Counted Detection | Finished Targeted owner's counted-detection decision |
| Review | Saved review routing: `Review`, `Check`, or blank when no review condition is recorded |
| Review Reason | Short explanation of the primary review condition |

Literal `ND`, numeric zero, and blank cells are different values. A retained
numeric area does not necessarily count as a detection. Review routing is also
separate: a counted detection can still need attention. Presentation does not
recompute any of these decisions. Filter `Review` to find the worklist, or open
the workbook in Inspector and use its attention filter.

### Secondary columns (grouped, hidden by default)

| Column | Meaning |
| --- | --- |
| PeakStart / PeakEnd | Saved integration boundaries in minutes |
| NL Evidence | Human-readable neutral-loss evidence |
| Reason | Full recorded explanation of the result |
| Review Evidence | Additional review evidence, including diagnostic details not recoverable from the short reason alone |
| Role | `Analyte` or `ISTD`; retained for established ISTD readers |
| Group | Sample grouping |
| NL | Recorded neutral-loss status; retained for existing evidence readers |
| Confidence | Recorded confidence; not an independent detection or publication gate |

Raw/smoothed intensities, derived peak width, duplicated ISTD pairing and internal
projection code lists are not part of this presentation table. Internal long-row
contracts remain separate. `TargetKey`, `TargetDefinition`, `TargetRowBinding`,
typed no-write reasons and archive references remain internal lifecycle records.

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

## Diagnostics (when nonempty)

Supporting records with `SampleName`, `Target`, `Issue` and the full supplied
`Reason`, including records not attached to a result row. The appendix preserves
traceability; it is not another review verdict. `Review Evidence` is a concise
row-level guide, not a replacement for this diagnostic text.

## Run Metadata

Key-value pairs recording settings and provenance for the run.

| Key | Example value | Description |
| --- | --- | --- |
| config_hash | `a3f2...` | SHA-256 hash of config |
| app_version | `1.2.0` | Application version |
| generated_at | `2026-06-27T10:00:00` | ISO 8601 timestamp |
| signal_processing_method | `gaussian` | Shared physical signal smoothing method |
| signal_processing_window_points | `15` | Gaussian smoothing window (points) |
| targeted_workbook_layout_version | `2` | Delivered workbook presentation layout |
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
