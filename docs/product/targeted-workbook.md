# Targeted Workbook

The delivered Targeted XLSX is a reading document answering, in order: can
this batch be used and which results need a look first; what simple
calculation can be done here (means, analyte/ISTD ratios, CV); and which
settings and version produced it. It is also the saved record the workbench and
tools read, and the run's only artifact. The code and its tests own the sheets,
columns, labels and formats; this page keeps the boundaries and reasons. The
[Targeted guide](../user/targeted.md#excel-結果) explains every column and
label to users.

## Boundaries

- **Readers bind named headers**, not column positions or a layout tag; human
  sheets can change layout but never saved values or decisions.
- **Writers render, never decide.** Neither writer nor Inspector reruns a
  scientific decision; provenance rendering never recomputes evidence.
- **The workbook does not reproduce a run.** `target_config_hash`, a
  timestamp, an output path or a successful exit is not a processing identity,
  and no RAW bytes are snapshotted.
- **A saved method is intent, not a receipt.** Admission freezes settings,
  targets and injection-order input, so editing a method cannot change a queued
  or running run.

## Content rules

- **Fixed labels, never sentences.** A label names an observed fact and its
  column gives its role, so the same fact is the same word in either column;
  numbers stay on `診斷事件` and the peak is read in the XIC view.
- **A missing neutral loss stays a doubt** when identity comes from elsewhere,
  because the method expects one; it is the `依據` only when it decided the
  result.
- **The minority outcome needs a look first**: within a target, a rare counted
  result may be a wrong peak and a rare not-counted result may be a missed one.
  An outcome every sample shares is a batch fact.
- **`面積` projects every saved Area**, zero, ND and blank included,
  independent of counted detection; a withheld `不足以定量` value is blank, never zero or ND, and its
  diagnostic integral is not substituted.
- **Every diagnostic event is kept** on the hidden `診斷事件` sheet for tracing
  a past decision, because the XIC view shows the peak but not why it was
  chosen.

## Presentation

| Rule | Why |
| --- | --- |
| One value per cell; no merged cells; one header row per table | Sorting, filtering and formulas work on every table |
| Numbers stay numbers; ND stays literal text | `AVERAGE` and ratios skip ND instead of failing |
| No gridlines, fills or stripes; two text inks; one accent colour only on a cell that needs a look | Non-data ink competes with values; the landing worklist is already the exception |
| Visible headers are plain words | No sheet exists only to translate another |
| No charts, KPI cards or decorative layout | The summary table already carries the counts |
| Excel tables with frozen headers; wide sheets print across pages | Native navigation at 1 or 200 targets; text is never shrunk to fit |

Each Excel Table owns its filter: adding a worksheet filter over the same range
makes desktop Excel repair the file and remove the Table.
