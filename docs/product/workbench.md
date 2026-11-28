# Workbench And Review

The local HTML Workbench is where users set up, run and review Targeted and
Untargeted analyses. XIC Inspector supplies its read-only evidence selection and
the shared review renderer, which also produces self-contained offline HTML
reports. This page owns what review may and may not mean. The
[Targeted guide](../user/targeted.md) and
[Untargeted guide](../user/untargeted.md) own operation; `--help` on
`xic-workbench`, `xic-inspector` and `tools.diagnostics.explain_cid_nl_run`
owns command arguments.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| Do the GUI and CLI behave differently? | No; both call the same owners, so one request gives one scientific behavior. | [One path](#cli-and-html-use-one-path) |
| Can review change a result? | No; review reads completed evidence and never selects, integrates, counts or writes. | [Review does not change results](#review-does-not-change-results) |
| Is a review curve a measurement? | No; saved curves reproduce the run's signal, and RAW reads are new diagnostic views. | [Review curves](#review-curves-are-not-new-measurements) |
| Are NULL, zero and ND interchangeable? | No; they stay distinct in every table, legend and export. | [Result states](#result-states-stay-distinct) |
| What does review show first? | The minority outcome within a Target; for Untargeted, the Matrix. | [Reading order](#reading-order) |
| What does the explanation file prove? | It records the decisions for review; it authorizes no writer and proves no readiness. | [Retained evidence](#retained-evidence-and-diagnostics) |

## CLI and HTML use one path

The HTML Workbench is the primary human surface; script CLIs are reproducible
automation and diagnostic surfaces. Both call the same application and
scientific owners, so the same effective request has the same behavior.

- The GUI runs complete Untargeted analysis with Backfill enabled; single-RAW
  Discovery, Alignment-only and disabling Backfill are CLI uses. Worker counts
  come from the shared execution profile, not saved GUI settings.
- Setup presents data, method/targets, then samples and internal standards. A
  target list has one XLSX format (`Targets`, plus Delta Mass sheets when
  used); its import is one line with an example and its export sits in the
  target editor, and Untargeted method file tools are collapsible. Sample
  conflicts, exclusions, settings warnings and method-specific ISTD
  applicability stay visible.
- The sample step is one line of role and exclusion counts plus the 上機資訊
  file; **調整** opens the editor, which lists RAW names in File Explorer order
  (digits by value) without changing the saved injection order. Rows are
  selected by drag, Ctrl, Shift or search (which selects every match), and a
  role or exclusion is applied with one click or the key on each role button.
  Confirmed sample annotations stay bound to their RAW folder.
- The page shows either the next analysis's setup or one opened result, never
  both. A finished run opens its result (overview, review, downloads);
  **返回設定** returns to setup. Opening an existing result uses the same view
  without RAW or DLL access. Each GUI run writes a new
  `<date>_<time>_<kind>_<RAW folder>` folder, so no run overwrites another.
- Matrix curation is optional, previews the curation owner's decisions, and
  writes a separate matrix without overwriting the original.

## Review does not change results

- Review reads completed evidence. Opening a result, filtering, loading curves,
  changing plot ranges or exporting a report does not choose peaks, integrate,
  change counted detection, or write workbook or Matrix values.
- The Workbench dispatches the application, writer, Matrix curation and
  Inspector owners. Its forms and local JSON settings are not a second
  scientific owner, and a custom-method label is not a new method schema.
- Downloads serve the files the run published. Review never rebuilds the
  Targeted XLSX or Untargeted Matrix from Inspector data.
- An opened Targeted workbook is bound by its SHA-256; an external edit
  requires reopening the file.
- Inspector does not read RAW or rerun scientific preparation; it displays the
  series it is given, switches no candidate and builds no RT reselection
  table. Scientific packages do not import Inspector.
- Missing evidence stays visibly unavailable. Inspector never synthesizes a
  replacement value, and a missing curve is not zero signal.

## Review curves are not new measurements

An Untargeted sample with a Matrix value shows the curve its run saved: the RT
and raw intensity of the extraction window that gave the value, with the run's
mass recalibration applied, shown for all samples at once without RAW or DLL.
The Workbench recomputes smoothing and baseline from that raw curve with the
run's signal-processing settings; they reproduce the processed signal and
change no value.

- **Views.** The view opens peak-centred: each curve shifts so its apex sits at
  0 s (the midpoint of its saved bounds when no apex was kept), at real
  intensity, spanning the saved bounds; a curve with neither is not centred and
  is named beside the plot. **原始 RT** and **校正 RT** show recorded times with
  each apex marked; **全部** shows the whole saved windows.
- **RAW reads.** RAW is read only for a sample without a saved curve that the
  user checks and loads: an empty cell, or a candidate outside the Matrix.
  These are new diagnostic views, not product measurements. A sample is read
  from the RAW file of the same name with the saved mass recalibration, not
  checked against the analysed acquisition. RAW and DLL sources belong to the
  selected result's **RAW 曲線**, not the result opener.
- **Empty members.** An Untargeted member without its own retained apex is read
  at the median m/z and apex RT of the group's observed samples and drawn as a
  thin grey line, so a missed peak can be seen; it neither selects nor
  integrates a peak, and the empty value stays empty.
- **Targeted.** Targeted curves always read RAW, start from the sample row and
  step through the rows the target list shows (all, or 需先看), repeating each
  row's `依據` and `疑點`. They use the workbook's extraction tolerance and
  signal-processing settings over the full acquisition trace; saved bounds are
  overlays, not integrations. The view states that the workbook keeps no RAW
  fingerprint. Support checks for an uncounted target with a counted paired
  ISTD reuse the physical candidate gate, are explained beside the plot, and do
  not change ND or write values.
- **Failures.** A sample whose window yields no usable signal is named beside
  the curves; the request fails only when no selected sample yields a curve.
- **Never locked.** A list from a search, filter, page, reset, the overview or
  an opened result opens an item only when it has exactly one;
  **上一筆／下一筆** and the arrow keys open the next. Any new selection,
  search or page cancels a running RAW read, and **取消** stops one within
  seconds.

## Result states stay distinct

- NULL, numeric zero, literal `ND`, a blank Targeted cell and unavailable curve
  evidence stay distinct in every table, legend and export.
- An explicit `not_searched` cell stays NULL with its recorded explanation and
  creates no candidate or query witness.
- Line style marks the extraction stage of the peak giving the Matrix value:
  sample-coloured solid for detect, sample-coloured dashed for backfill, thin
  grey for loaded empty samples, dotted for a value with no recorded origin.
  MS2 support does not determine the stage, and review never infers it from
  method evidence.
- A group opens on one summary line of detect, backfill and empty counts, drawn
  in those line styles so it is also the legend, then the sample table, then
  the curves. Each row gives the Area, its origin swatch, and only what needs
  reading: why an empty cell is empty, a limitation of that sample's curves, or
  a recorded risk. Left-empty cells with several possible peaks are named
  多峰留空 everywhere, without a warning tone.
- Ordinary MS1-only backfill and a related method HOLD are not risks by
  themselves. Low SNR, weak RT support and recorded pairing competition stay on
  the row; competition names what the alternative would do with this peak (the
  samples and native apex RTs it would join, or leaving this group). An
  unselected alternative gets no published feature ID, and internal records
  stay in SQLite. No recorded risk is not a claim of scientific acceptance.

## Reading order

Review starts with results that differ from the rest of their target. Within a
target, the saved counted outcome shared by at most 20% of recorded decisions
is the minority: a rare counted result may be a wrong peak, and a rare
not-counted result may be a missed one. An outcome shared by every sample is a
batch fact needing no review. Without a clear majority, rows whose saved labels
need a look ([Targeted workbook](targeted-workbook.md#content-rules)) set the
order. One owner computes this order for the Workbench and the workbook
summary, so their 先看 counts match; it does not reclassify results.

For Untargeted reports the overview leads with the Matrix. Groups with an
explicitly unresolved saved decision leave those samples blank; the overview
gives their count as context, one action away, not a required review step.
Ordinary missing values are not treated as abnormal or not detected. The
default order uses unresolved-cell count, then numeric sample coverage; it is
not a scientific score. Approximate m/z search ranks by distance with no
implicit tolerance, so a match does not confirm identity.

When the result folder holds completed curations, review follows the latest by
folder name and names it beside the title; producing a new curation switches
the open review to it. Opening a curation folder itself keeps that curation and
reads evidence and curves from the result its summary records. Overview
counts, coverage, Matrix list and download then cover only kept features;
removed ones are under **整理時移除**, beside unadopted groups, each with one
line from its `feature_decisions.tsv` row. A result without curation is
reviewed as published.

## Retained evidence and diagnostics

`alignment_explanation.sqlite` is the shared, method-neutral Alignment evidence
file for Matrix review, Matrix curation and the records that did not enter the
Matrix. It records the attempt, the bound Matrix and its hash, samples, peaks,
candidates with their exclusions, final groups with their members, and cells.
It cannot authorize a writer or prove readiness.

- **Contents.** Values, review reasons, risk flags and the saved raw curve of
  each valued cell; other samples' RAW curves are requested without changing
  the result. A cell's pairing competition keeps the peers each alternative
  would give its peak. Scores, RT estimates, method assessments and other
  decision inputs are not in the report; a debug-level run's replay snapshots
  hold them. Which candidates explain a group is a stored relation written
  once, selected by the query strategy.
- **Format.** The schema text is the format; a file whose schema differs is
  rejected, not upgraded, and Alignment must be rerun. Publication follows
  successful Matrix writing; if explanation IO fails, the command fails and the
  Matrix can remain without a report.
- **Aligned time.** Plots replay the final frozen calibration, never the
  bootstrap ISTD-reference fit; curves, apex and bounds use the same
  [graded query](rt-calibration.md#why-it-works-this-way). A sample with no
  estimate shows raw RT, labelled as such. Injection order is kept
  independently of ISTD-reference provenance. Display alignment cannot repair
  grouping built without the intended ISTD input.
- **Apex m/z.** `ms1_apex_mz` is the measured MS1 apex m/z for that candidate
  and sample; a missing one stays null, never the row center or precursor m/z.
- **Curve bundle.** `explain_cid_nl_run --curve-bundle` holds the same review
  curves: saved curves where kept, labelled RAW reads for other samples. It
  does not rerun Backfill, choose peaks or replace quantification.
- **Neighbouring rows.** A row's review also carries the valued apexes of every
  published row whose center m/z lies within its ppm. When samples' calibrated
  apex lies more than 3.5 min from the median of their 8 nearest injections in
  that row, the view gives their count and plots injection order against
  calibrated apex for every row, this row coloured and the others grey, with
  those samples circled. The median follows a row drifting with injection
  order, so drift alone is not circled. Below the plot, the farthest three and
  the sample nearest the median apex show one XIC each across all rows' apexes,
  labelled by row; clicking a point adds or removes its sample. The circle
  chooses what opens first and decides nothing. These XICs are RAW reads, so a
  bundle has them only when `explain_cid_nl_run --group-id` gets `--raw-dir`;
  Workbench shows the plot and circled samples without them.
- **Delta Mass.** Delta Mass Discovery has its own sample-local review source,
  not an Alignment result: selected-candidate JSON with shared-scale RAW XICs
  and MS2 spectra in the same Inspector. The
  [candidate review contract](discovery.md#read-only-candidate-review) owns
  the new-read versus saved-answer distinction. There is no Delta Mass Matrix
  or analysis form in Workbench.

## Browser and desktop boundaries

Constraints the code does not state:

- Offline report tokens are substituted in one pass, or a value can become a
  host placeholder. SVG references stay local to their mounted plot; a modal's
  tooltip belongs in the dialog's top layer, not the plot's stacking context.
- The loopback server closes a connection with an unread or unsupported body
  rather than parse its tail as the next request, consumes a bounded body
  before rejecting its headers to avoid a Windows reset during upload, and
  never acknowledges an undelivered completion.
- State-file reads and writes are serialized because an outstanding Windows
  read can deny replacement and concurrent read-modify-write can lose an edit;
  read-only previews do not hold that lock.
- A frozen release is verified only by its artifact build and smoke run;
  source tests cover the Python and resource contract.

## Verification

| Contract | Focused checks |
| --- | --- |
| Launch, routes, review and on-demand curves | `tests/test_xic_workbench.py`, `tests/test_workbench_analysis.py`, `tests/test_workbench_browser.py` |
| Saved review curves and RAW reads for the rest | `tests/test_review_curves.py` |
| Targeted and Untargeted workbench adapters | `tests/test_workbench_config.py`, `tests/test_workbench_targeted.py`, `tests/test_workbench_untargeted.py` |
| Offline report layout and interaction | `tests/test_xic_inspector_html.py`, `tests/test_xic_inspector_curve_bundle.py` |
| Inspector reads no RAW and is not imported by scientific packages | `tests/test_xic_inspector_import_boundary.py` |
| Targeted workbook review and reading order | `tests/test_targeted_overview.py`, `tests/test_xic_inspector_targeted_workbook.py` |
| Frozen resources | `tests/test_pyinstaller_spec.py` |

## See Also

[Targeted workbook](targeted-workbook.md) · [Quant Matrix](quant-matrix.md) ·
[Matrix Curation](matrix-curation.md) · [Presets](presets.md) ·
[Sample metadata](sample-metadata-qc.md) ·
[Dependency direction](../architecture/dependency-direction.md)

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Targeted reading order and 先看 counts | `summarize_targeted_results` |
| Aligned-time replay | `SampleRtFrame` |
