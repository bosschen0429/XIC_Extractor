# Targeted Workbook

The delivered Targeted XLSX is a reading document. It answers three questions,
in this order: can this batch be used and which results need a look first; what
simple calculation can be done here (means, analyte/ISTD ratios, CV); and which
settings and version produced it. Targeted results rarely leave the workbook for
downstream statistics; the Untargeted `matrix.tsv` owns that role. Per-peak
inspection belongs to the workbench XIC view, not the XLSX.

## Sheets

| Sheet | Role | Reader |
| --- | --- | --- |
| `摘要` | One conclusion line, one batch fact line, one table of targets | Human (landing sheet) |
| `需檢查` | Only the results counted in 先看, one row each; absent when there are none | Human |
| `面積` | Saved Area per sample × target; each analyte followed by its paired ISTD | Human, in-sheet calculation |
| `XIC Results` | One row per sample-target result | Inspector, ISTD reference, human |
| `Targets` | Admitted target intent | Inspector, human |
| `Run Metadata` | `Key`, `說明`, `Value` | Inspector, workbook compare |
| `欄位說明` | Meaning and unit of every delivered column | Human |
| `診斷事件` | Every diagnostic event (`SampleName`, `Target`, `Issue`, `Reason`), hidden; only when nonempty | Traceability |
| `Sample Context` | Supplied sample order table, hidden; only when supplied | Traceability |

`XIC Results`, `Targets` and `Run Metadata` are reader contracts: their names,
first-row headers and `targeted_workbook_layout_version` 2 stay stable. Readers
bind headers, not positions. Human sheets may change layout without a version
change; they never change saved values or decisions.

## Content rules

- `需先看` is the minority outcome of each target (at most 20% of recorded
  decisions), or, without a clear majority, the targets' saved review conditions.
  The summary, the worklist and the workbench count the same rows.
- A minority outcome without a saved review condition states the saved decision
  against the majority. It adds no scientific flag.
- A diagnostic event without a delivered result (a RAW file that failed to open)
  stays on `需檢查` with its message, because no XIC view can explain it.
- Every diagnostic event is kept as literal text on the hidden `診斷事件` sheet.
  The delivered XLSX is the run's only artifact, and the XIC view shows the peak
  but not why a selection was made. The sheet is for tracing a past decision,
  not for reading the batch.
- `面積` projects every saved Area, including zero, ND and blank, independent of
  counted detection. It calculates no ratio.
- A review reason names the evidence the saved decision rests on, and the
  suggested check names the one thing that could overturn it. A counted analyte
  rests on its MS1 peak at the paired ISTD position (or on observed neutral
  loss); an uncounted one names the failed MS1 check or the missing identity
  source. Under CID a missing neutral loss or MS2 scan is a given fact, so it is
  never the reason or the check. An answer that follows from the evidence stays
  quiet: a counted result without a saved concern is not listed.
- Reasons and checks use plain domain words (RT, MS1, XIC, ISTD, 中性丟失) and
  point to the XIC view; internal codes stay out of the workbook.

## Presentation rules

| Rule | Why |
| --- | --- |
| One value per cell; no merged cells; one header row per table | Sorting, filtering and formulas work on every table |
| Numbers stay numbers; ND stays literal text | `AVERAGE` and ratios skip ND instead of failing |
| Numbers right-aligned, text left-aligned | Columns scan by magnitude |
| No gridlines, fills or stripes; one hairline between rows | Non-data ink competes with values |
| Two text inks; grey marks not counted or ND | Grey repeats `Counted Detection`, which remains its own column |
| One accent colour, used only for 先看 | The exception is the only thing that stands out |
| Area and intensities in `0.00E+00`; RT in `0.00` | Large values compare by exponent; storage keeps full precision |
| Column meanings on `欄位說明`, not hover comments | Documentation is visible and printable |
| No charts, KPI cards or decorative layout | The summary table already carries the counts |
| Frozen headers, Excel tables, repeated print titles | Native navigation at 1 or 200 targets |
| Wide sheets print across pages | Text is never shrunk to fit one page |

## Owners

- Writer: `xic_extractor.output.workbook_builder` and its `sheet_*` modules.
- Focus and worklist: `xic_extractor.output.targeted_overview`.
- Decisive evidence and review wording: `xic_extractor.output.review_queue_model`.
- Saved-workbook reader: `xic_inspector.sources.targeted_workbook`
  ([XIC Inspector](xic-inspector.md#targeted-xlsx-review)).
- Column meanings for users: [Targeted Output Reference](../user/targeted-output-reference.md).
