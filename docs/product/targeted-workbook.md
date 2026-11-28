# Targeted Workbook

The delivered Targeted XLSX is a reading document answering, in order: can
this batch be used and which results need a look first; what simple
calculation can be done here (means, analyte/ISTD ratios, CV); and which
settings and version produced it. Downstream statistics belong to the
Untargeted `matrix.tsv`, and per-peak inspection to the workbench XIC view.

The same file is the saved record the workbench and tools read; settings,
Delta Mass evidence and trace records stay in it, hidden.

| Question | Rule | Detail |
| --- | --- | --- |
| Which sheets are visible, and what do readers bind? | `摘要`, `面積`, `XIC Results`, `Targets`; readers bind named headers, not positions. | [Sheets](#sheets) |
| What does a result say? | One fixed `依據` label and zero or more `疑點` labels. | [Content rules](#content-rules) |
| What needs a look first? | Each target's minority outcome. | [Content rules](#content-rules) |
| What else is delivered? | Nothing; the XLSX is the only artifact. | [Run Metadata](#run-metadata) |

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
positions or a numeric layout tag. Human sheets can change layout; they never
change saved values or decisions.

An `XIC Results` row holds only what belongs to that result; a target's role
is read from `Targets`. `依據` and `疑點` hold fixed labels from one table, and
whether a row needs a look is derived from them, so no flag column exists.

`Targets.Specificity` states each target's specificity and parameters, for
example `MS1 only`, `CID-NL 116.0474` or `CID-NL 116.0474 + Delta Mass +1.0034`.
For a Delta Mass target, `Method Evidence` records each sample, target,
candidate interval, selected flag, acceptance (all conditions match) and
clause status: `matched`, `not_matched`, `not_acquired`, or `not_evaluated`
when no candidate could be assessed. CID-NL clauses are labelled by their loss,
for example `CID-NL 116.0474`.

`Delta Mass` has one row per target ion, like the method file's sheet: `Target`,
`Position`, `Delta m/z`, `Ratio Min`, `Ratio Max`, `Compare Shape`, followed by
the list-shared `m/z Tolerance ppm` and co-elution/shape thresholds repeated on
each row. Inspector reads the ions from this table.

Delta Mass details record XIC query masses, not measured centroids. Height
ratios and shape comparisons reference the quantitation ion; ratios stay
annotation. `Apex Delta to Quant sec` is each ion's apex RT minus the
quantitation ion's. Inspector shows **特異性條件結果** and collapsible
**Delta Mass 配對明細** from these saved answers; CID-NL clauses of Delta Mass
targets are reported in `Method Evidence`, not in fabricated NL columns.
Neither writer nor Inspector reruns scientific decisions.

## Content rules

- Each result carries fixed labels, never generated sentences. `依據` holds
  exactly one label: the observation the saved result rests on. `疑點` holds
  zero or more labels joined by `、`: what is left uncertain. A label names an
  observed fact and its column gives its role, so the same fact is the same
  word in either column, and an observation `依據` already names is not
  repeated in `疑點`. Labels carry no numbers; the one parameter is the partner
  targets in `共用離子區段（A / B）`. Exact RT, area and offsets stay on
  `診斷事件`, and the peak is read in the XIC view.
- A result needs a look when its `依據` label is marked for a look or its
  `疑點` is nonempty. Each label has one fixed mark; the one exception is
  `無可用 MS1 峰`, which needs a look only for an ISTD, read from `Targets`.
- Identity is named by its source: the paired ISTD position (`ISTD 位置`),
  the target's own neutral loss (`NL 確認`), Backfill for an ISTD (`回填`), the
  specificity clauses (`特異性條件`) or, when no neutral loss is required, the
  MS1 peak (`僅 MS1 峰`). The method expects a neutral loss, so when identity
  comes from elsewhere a missing neutral loss or MS2 scan stays a doubt
  (`未見 NL`, `無 MS2`, `NL 偏差大`); it is the `依據` only when it decided the
  result.
- `需先看` is the minority outcome of each target (at most 20% of recorded
  decisions), or, without a clear majority, the results that need a look.
  The worklist on `摘要` and the workbench count the same result rows; the
  headline counts every worklist row. A worklist row shows the saved labels;
  a minority outcome adds no flag of its own.
- A RAW acquisition failure stops the formal Targeted run before publication.
  When rendering previously recorded diagnostics, a `FILE_ERROR` event without
  a result appears on the worklist as `RAW 失敗`; its message is on `診斷事件`.
- Every diagnostic event is kept as literal text on the hidden `診斷事件` sheet,
  for tracing a past decision rather than reading the batch: the XLSX is the
  run's only artifact, and the XIC view shows the peak but not why it was
  chosen.
- `面積` projects every saved Area, including zero, ND and blank, independent of
  counted detection. It calculates no ratio.
- A finalized `not_quantifiable` decision leaves the numeric cells blank with
  `依據` `不足以定量`: signal was observed but cannot support reliable quantity.
  It is not rendered as zero or ND. The original XIC remains reviewable;
  its diagnostic integral is not substituted for the withheld value.
- Labels use plain domain words (RT, MS1, ISTD, NL), explained in the user
  guide; internal codes stay off the visible sheets.

## Run Metadata

Normal Targeted delivery is this one XLSX. No HTML, CSV/TSV or manifest
sidecar is published, and Targeted runs have no replay entry point.

- `Run Metadata` records the actual method settings, runtime configuration and
  config fragments. `Targets` records target identity, ranges, pairing and
  sample applicability.
- `target_config_hash` is a target-list display fragment, not a complete
  method record; neither it nor a timestamp, output path or successful exit
  reproduces or identifies a run.
- A saved Targeted method is reusable intent, not a run receipt. Admission
  freezes settings, targets and the supplied injection-order input in a private
  temporary workspace, so editing a method cannot change a queued or running
  run.
- A supplied sample context table is embedded as the hidden `Sample Context`
  sheet: the values behind decisions, not a byte-for-byte replay archive. RAW
  bytes are never snapshotted into the workbook.

## Presentation rules

| Rule | Why |
| --- | --- |
| One value per cell; no merged cells; one header row per table | Sorting, filtering and formulas work on every table |
| Numbers stay numbers; ND stays literal text | `AVERAGE` and ratios skip ND instead of failing |
| Numbers right-aligned, text left-aligned | Columns scan by magnitude |
| No gridlines, fills or stripes; one hairline between rows | Non-data ink competes with values |
| Two text inks; grey marks not counted or ND | Grey repeats `Counted Detection`, which remains its own column |
| One accent colour, used only on the `依據` or `疑點` cell that needs a look in `XIC Results` | The landing sheet's worklist is already the exception |
| Area and intensities in `0.00E+00`; RT in `0.00` | Large values compare by exponent; storage keeps full precision |
| Visible headers are plain words; record-sheet columns are documented in the user guide | No sheet exists only to translate another |
| No charts, KPI cards or decorative layout | The summary table already carries the counts |
| Excel tables; frozen headers and repeated print titles on single-table sheets | Native navigation at 1 or 200 targets |
| Wide sheets print across pages | Text is never shrunk to fit one page |

Each Excel Table owns its filter. Adding a worksheet filter over the same
range makes desktop Excel repair the file and remove the Table.

## Owners

- Writer: `xic_extractor.output.workbook_builder` and its `sheet_*` modules.
- Focus and worklist: `xic_extractor.output.targeted_overview`.
- Result labels and their look marks: `xic_extractor.output.result_labels`.
- Saved-workbook reader: `xic_inspector.sources.targeted_workbook`
  ([Workbench and review](workbench.md)).
- Column meanings for users: [Targeted guide](../user/targeted.md#excel-結果).
