# Targeted Output Reference

The normal completed Targeted run delivers one `xic_results_YYYYMMDD_HHMM.xlsx`
inside its run directory. It opens on `摘要`; `需檢查` follows when some results
differ from their target's majority, then `面積`, `XIC Results`, `Targets`,
`Run Metadata` and `欄位說明`.
Inspector reads this layout. Opening a workbook for review does not modify it or create sidecar files.
The design rules are in [Targeted Workbook](../product/targeted-workbook.md).

## 摘要: start here

The first line is the conclusion: how many results to look at first, or that
every target agrees. The second line gives samples, targets, and Analyte and
ISTD counted totals. One table lists every target with its role, paired ISTD,
counted and total results, the 先看 count and what to confirm.

先看 is the minority outcome of a target (at most 20% of its decisions). A rare
counted result may be a wrong peak; a rare uncounted result may be a missed
peak. Without a clear majority, the saved review notes are the reading order.
The 先看 count links to the target's rows on `需檢查`. This order never changes
saved values or decisions.

## 需檢查

Only the 先看 results, one row each: target, sample, saved outcome, reason,
suggested check, RT and Area. The sample links to its `XIC Results` row. A RAW
file that failed to open is listed here with its error message. Open the
workbook in the workbench to see the XIC for each row.

## 面積

Saved Area per sample (rows, in injection order) and target (columns). Each
analyte column is followed by its paired ISTD, so a ratio is one formula, for
example `=B5/C5`. Grey text is a value the saved decision did not count; it is
kept so the reader can judge it. `ND` is text and is skipped by `AVERAGE`.

## XIC Results

One row per sample-target pair, with repeated sample labels, Excel filters and
frozen identifiers. Not-counted rows read in grey. The first eight columns answer
the everyday review questions; expand the grouped secondary columns only when
more evidence is needed.

### Main columns

| Column | Meaning |
| --- | --- |
| SampleName | Sample identifier from the RAW filename |
| Target | Target label; method inputs and ISTD pairing are in `Targets` |
| Area | Selected shared physical peak's Gaussian-smoothed positive AsLS residual area (counts·seconds) |
| RT | Smoothed peak apex retention time (min) |
| IntSmoothed | Smoothed apex intensity (counts); not an area |
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
| Int / PeakWidth | Raw apex intensity and peak width |

Internal projection code lists are not part of this table. Internal long-row
contracts remain separate. `TargetKey`, `TargetDefinition`, `TargetRowBinding`,
typed no-write reasons and archive references remain internal lifecycle records.

## Targets

One row per configured target. Mirrors the admitted target intent with computed columns.

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

## Run Metadata

`Key`, `說明` and `Value` rows recording settings and provenance for the run.
Value is the last column so a long setting such as `targeted_strategy` stays readable.

| Key | Example value | Description |
| --- | --- | --- |
| config_hash | `a3f2...` | SHA-256 hash of config |
| app_version | `1.2.0` | Application version |
| generated_at | `2026-06-27T10:00:00` | ISO 8601 timestamp |
| signal_processing_method | `gaussian` | Shared physical signal smoothing method |
| signal_processing_window_points | `15` | Gaussian smoothing window (points) |
| targeted_workbook_layout_version | `2` | Delivered workbook presentation layout |
| targeted_output_schema_version | `targeted_output` | Output schema version |

---

## 欄位說明

The meaning and unit of every delivered column, in one visible table.

## Hidden trace sheets

A run with diagnostic events adds the hidden sheet `診斷事件`: one row per event
with `SampleName`, `Target`, `Issue` and the full `Reason`, including events not
attached to a result row. It preserves why a selection was made; it is not another
review verdict. Unhide it in Excel (right-click a sheet tab → Unhide) when tracing
a past decision.

## Embedded decision inputs

A nonempty supplied `Sample Context` is a hidden technical sheet. It preserves
the input table used for sample roles/order rather than requiring an external
sidecar. It is not a new decision or evidence of scientific readiness. An empty
input adds no sheet. `Targets` includes `Sample applicability`; `Run Metadata` includes the
actual method settings, not a link to a separate manifest.
