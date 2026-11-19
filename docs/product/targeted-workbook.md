# Targeted Workbook

The delivered Targeted XLSX is a reading document. It answers three questions,
in this order: can this batch be used and which results need a look first; what
simple calculation can be done here (means, analyte/ISTD ratios, CV); and which
settings and version produced it. Targeted results rarely leave the workbook for
downstream statistics; the Untargeted `matrix.tsv` owns that role. Per-peak
inspection belongs to the workbench XIC view, not the XLSX.

The same file is also the saved record the workbench and tools read. The
sheets a reader looks things up in are visible: `摘要`, `面積`, every result in
`XIC Results` and the targets in `Targets`. Settings, Delta Mass evidence and
trace records stay in the file, hidden. Visible columns are plain enough that
no sheet exists to translate them.

## Sheets

| Sheet | Role | Reader |
| --- | --- | --- |
| `摘要` | Conclusion line; batch fact line (ISTD status names only an ISTD not counted in every sample) ending with version and date; the results counted in 先看, one row each; then one row per analyte with its counted samples | Human (landing sheet) |
| `面積` | Saved Area per sample × target; analytes sharing an ISTD sit together, followed by that ISTD | Human, in-sheet calculation |
| `XIC Results` | One row per sample-target result | Human, Inspector, ISTD reference, workbook compare |
| `Targets` | Admitted target intent, including `Specificity` | Human, Inspector, ISTD reference, workbook compare |
| `Delta Mass` | Delta Mass ions of each target, with the list-shared thresholds; hidden; only with Delta Mass targets | Inspector, reproducibility |
| `Method Evidence` | Same-peak candidate/condition statuses and acceptance; hidden; only with Delta Mass targets | Inspector |
| `Delta Mass Evidence` | Per-position pattern answers and selected-candidate flags; hidden; only with Delta Mass clauses | Inspector |
| `Run Metadata` | `Key`, `說明`, `Value`; hidden | Inspector, workbook compare |
| `診斷事件` | Every diagnostic event (`SampleName`, `Target`, `Issue`, `Reason`); hidden; only when nonempty | Traceability |
| `Sample Context` | Supplied sample order table; hidden; only when supplied | Traceability |

`XIC Results`, `Targets` and `Run Metadata` are reader contracts: readers bind
their named headers and the unversioned `targeted_output` format, not column
positions or a numeric layout tag. Workbooks carrying the retired layout tag
must be regenerated. Human sheets may change layout; they never change saved
values or decisions.

An `XIC Results` row holds only what belongs to that result. A target's role
is read from `Targets`; a nonempty `Review Reason` is the review mark; the
neutral-loss observation is `NL Evidence` alone.

`Targets.Specificity` states each target's specificity and parameters, for
example `MS1 only`, `CID-NL 116.0474` or `CID-NL 116.0474 + Delta Mass +1.0034`.
For a Delta Mass target, `Method Evidence` records each sample, target,
candidate interval, selected flag, acceptance (every condition must match), and
clause status: `matched`, `not_matched`, `not_acquired`, or `not_evaluated`
when no candidate could be assessed. CID-NL clauses are labelled by their loss,
for example `CID-NL 116.0474`.

`Delta Mass` has one row per target ion, like the method file's sheet: `Target`,
`Position`, `Delta m/z`, `Ratio Min`, `Ratio Max`, `Compare Shape`, followed by
the list-shared `m/z Tolerance ppm` and co-elution/shape thresholds repeated on
each row. Inspector reads the ions from this table.

Delta Mass details record XIC query masses, not measured centroids. Height ratios
and shape comparisons reference the quantitation ion; ratios remain annotation.
`Apex Delta to Quant sec` is each ion's apex RT minus the quantitation ion's apex RT.
Inspector shows **特異性條件結果** and collapsible **Delta Mass 配對明細** from
these saved answers. CID-NL clauses of Delta Mass targets are reported in
`Method Evidence`,
not fabricated legacy NL columns. Neither writer nor Inspector reruns scientific
decisions.

## Content rules

- `需先看` is the minority outcome of each target (at most 20% of recorded
  decisions), or, without a clear majority, the targets' saved review conditions.
  The worklist on `摘要` and the workbench count the same result rows; the
  headline counts every worklist row.
- A minority outcome without a saved review condition states the saved decision
  against the majority. It adds no scientific flag.
- A diagnostic event without a delivered result (a RAW file that failed to open)
  stays on the worklist with its message in the reason, because no XIC view can
  explain it.
- Every diagnostic event is kept as literal text on the hidden `診斷事件` sheet.
  The delivered XLSX is the run's only artifact, and the XIC view shows the peak
  but not why a selection was made. The sheet is for tracing a past decision,
  not for reading the batch.
- `面積` projects every saved Area, including zero, ND and blank, independent of
  counted detection. It calculates no ratio.
- A finalized `not_quantifiable` decision leaves the numeric cells blank and
  explains that signal was observed but cannot support reliable quantity.
  It is not rendered as zero or ND. The original XIC remains reviewable;
  its diagnostic integral is not substituted for the withheld value.
- A review reason names the evidence the saved decision rests on; the reader
  checks it in the XIC view, so the workbook adds no separate suggested check.
  A counted analyte
  rests on its MS1 peak at the paired ISTD position (or on observed neutral
  loss); an uncounted one names the failed MS1 check or the missing identity
  source. Under CID a missing neutral loss or MS2 scan is a given fact, so it is
  never the reason or the review level. An answer that follows from
  the evidence stays quiet: a counted result without a saved concern (a missing
  neutral loss is none) is not listed.
- `XIC Results` `Reason` (the workbench's 原始選峰紀錄) states the same basis
  first, then other saved concerns; a missing neutral loss is not listed. The
  workbench shows `NL Evidence` up front only when a neutral loss was observed.
- Reasons use plain domain words (RT, MS1, XIC, ISTD, 中性丟失); internal codes
  stay out of the visible sheets.

## Run Metadata

Normal Targeted delivery is this one XLSX. No HTML, CSV/TSV or manifest
sidecar is published, and Targeted runs have no replay entry point.

- `Run Metadata` records the actual method settings, runtime configuration and
  config fragments. `Targets` records target identity, ranges, pairing and
  sample applicability.
- `target_config_hash` is a target-list display fragment. It is not a complete
  method record and does not reproduce a run. A timestamp, output path or
  successful exit is not a processing identity either.
- A saved Targeted method is reusable intent, not a run receipt. Admission
  freezes settings, targets and the supplied injection-order input in a private
  temporary workspace, so editing a method cannot change a queued or running
  run.
- A supplied sample context table is embedded as the hidden `Sample Context`
  sheet. It preserves the values behind decisions, not a byte-for-byte replay
  archive. RAW bytes are never snapshotted into the workbook.
- Provenance rendering never recomputes evidence or changes selected peaks,
  areas, counted detections or values.

## Presentation rules

| Rule | Why |
| --- | --- |
| One value per cell; no merged cells; one header row per table | Sorting, filtering and formulas work on every table |
| Numbers stay numbers; ND stays literal text | `AVERAGE` and ratios skip ND instead of failing |
| Numbers right-aligned, text left-aligned | Columns scan by magnitude |
| No gridlines, fills or stripes; one hairline between rows | Non-data ink competes with values |
| Two text inks; grey marks not counted or ND | Grey repeats `Counted Detection`, which remains its own column |
| One accent colour, used only for `Review Reason` in `XIC Results` | The landing sheet's worklist is already the exception |
| Area and intensities in `0.00E+00`; RT in `0.00` | Large values compare by exponent; storage keeps full precision |
| Visible headers are plain words; record-sheet columns are documented in the user guide | No sheet exists only to translate another |
| No charts, KPI cards or decorative layout | The summary table already carries the counts |
| Excel tables; frozen headers and repeated print titles on single-table sheets | Native navigation at 1 or 200 targets |
| Wide sheets print across pages | Text is never shrunk to fit one page |

## Owners

- Writer: `xic_extractor.output.workbook_builder` and its `sheet_*` modules.
- Focus and worklist: `xic_extractor.output.targeted_overview`.
- Decisive evidence wording: `xic_extractor.output.reason_display`; review
  routing: `xic_extractor.output.review_queue_model`.
- Saved-workbook reader: `xic_inspector.sources.targeted_workbook`
  ([Workbench and review](workbench.md)).
- Column meanings for users: [Targeted guide](../user/targeted.md#excel-結果).
