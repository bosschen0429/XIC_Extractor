# Workbench And Review

The local HTML workbench is where users set up, run and review Targeted and
Untargeted analyses. XIC Inspector supplies its read-only evidence selection
and the shared review renderer, which also produces self-contained offline HTML
reports. This page owns what review may and may not mean. The
[Targeted guide](../user/targeted-extraction.md) and
[Untargeted guide](../user/untargeted-discovery.md) own operation; `--help` on
`xic-workbench`, `xic-inspector` and `tools.diagnostics.explain_cid_nl_run`
owns command arguments.

## CLI and HTML use one path

The HTML workbench is the primary human surface; script CLIs remain
reproducible automation and diagnostic surfaces. Both call the same application
and scientific owners, so the same effective request has the same scientific
behavior. The GUI runs complete Untargeted analysis with Backfill enabled;
single-RAW Discovery, Alignment-only and disabling Backfill remain CLI uses.
Worker counts come from the shared execution profile, not saved GUI settings.

Setup presents data, method/targets, then samples and internal standards. Import
and file tools are collapsible; sample conflicts, exclusions and method-specific
internal-standard applicability remain visible. The sample step is one line of
role and exclusion counts plus the 上機資訊 file; **調整** opens the sample
editor. It lists RAW names in File Explorer order (digits by value) without
changing the saved injection order; the user selects rows (drag, Ctrl, Shift,
or search, which selects every match) and applies a role or exclusion with one
click or the key shown on each role button, and exports the sample data.

The page shows either the setup of the next analysis or one opened result,
never both. A finished run opens its result, with overview, review and
downloads; **返回設定** returns to setup. Opening existing results uses that same
result view without RAW or DLL access. Each GUI run writes a new
`<date>_<time>_<kind>_<RAW folder>` folder under the chosen result location, so
no run overwrites another.
Matrix curation is optional and previews the existing owner's decisions before
writing a separate matrix; it never overwrites the original matrix.

Applying an edited method changes the browser's batch draft, not its saved
method file. **保存批次設定** or starting analysis saves the draft; **另存方法檔**
exports a reusable method without applying it. **開始新批次** clears RAW and
acquisition-information inputs and the displayed samples, retaining the method,
targets/internal standards and output location. Confirmed sample annotations
remain bound to their RAW folder, not carried into another folder.

## Review does not change results

- Review reads completed evidence. Opening a result, filtering, loading curves,
  changing plot ranges or exporting a report does not choose peaks, integrate,
  change counted detection, or write workbook or Matrix values.
- The workbench dispatches existing application, writer, Matrix curation and
  Inspector owners. Its forms and local JSON settings are not a second
  scientific owner, and a custom-method label is not a new method schema.
- Downloads serve the files the run published. Review never rebuilds the
  Targeted XLSX or Untargeted Matrix from Inspector data.
- An opened Targeted workbook is bound by its SHA-256. An external edit
  requires reopening the file.
- Inspector does not read RAW files or rerun scientific preparation. It
  displays the series it is given; for a saved Untargeted curve the workbench
  recomputes smoothing and baseline from the saved raw curve with the run's
  signal-processing settings. Scientific packages do not import Inspector.
- Missing evidence stays visibly unavailable. Inspector never synthesizes a
  replacement value, and a missing curve is not zero signal.

## Review curves are not new measurements

An Untargeted sample with a Matrix value shows the curve its run saved: the RT
and raw intensity of the extraction window that gave the value, with the run's
mass recalibration already applied. Selecting a feature shows these samples at
once, without RAW or DLL. Smoothing and baseline are recomputed for display;
they reproduce the run's processed signal and change no value.

RAW is read only for a sample without a saved curve that the user checks and
loads: an empty cell, or a candidate outside the Matrix. These reads are new
diagnostic views, not product measurements. RAW and DLL sources belong to the selected result's
**RAW 曲線**, not the result opener. Targeted curves always read
RAW and start from the sample row.

- A sample is read from the RAW file of the same name with the run's saved mass
  recalibration. The file is not checked against the analysed acquisition; a
  curve is a review view and changes no result.
- An Untargeted group member without its own retained apex is read at the median
  m/z and apex RT of the group's observed samples. That group-position curve is
  drawn as a thin grey line for the empty sample, so a missed peak can be seen. It neither
  selects nor integrates a peak, and the saved empty value stays empty.
- Targeted curves use the workbook's extraction tolerance and signal-processing
  settings over the full acquisition trace. Saved peak bounds are overlays, not
  new integrations. The Targeted view states that the workbook keeps no RAW
  fingerprint.
- Support checks for an uncounted target with a counted paired ISTD reuse the
  physical candidate gate. Their result is explained beside the plot and does
  not change ND or write values.
- A sample whose window yields no usable signal is named beside the curves.
  The request fails only when no selected sample yields a curve.

Reading never locks review. A list from a search, filter, page, reset, the
overview or an opened result shows its items and opens one only when it has
exactly one; **上一筆／下一筆** and the arrow keys in the list open the next item.
Any new selection, search or page cancels a running RAW read and proceeds;
**取消** stops a read within seconds.

## Result states stay distinct

- NULL, numeric zero, literal `ND`, a blank Targeted cell and unavailable curve
  evidence remain distinct in every table, legend and export.
- An explicit `not_searched` cell remains NULL with its recorded explanation.
  It does not create a candidate or query witness.
- Line style marks the extraction stage of the peak providing the Matrix value:
  sample-coloured solid lines for detect, sample-coloured dashed lines for
  backfill, and thin grey lines for loaded empty samples. MS2 support does not
  determine extraction stage.
- A group opens on one summary line of detect, backfill and empty counts, drawn
  with those line styles so it is also the legend. The sample table follows,
  then the curves. Each row gives the Area, its origin swatch, and only what
  needs reading: why an empty cell is empty, a limitation of that sample's
  curves, or a recorded risk. Left-empty cells with several possible peaks are
  named 多峰留空 everywhere and take no warning tone.
- Ordinary MS1-only backfill and a related method HOLD are not by themselves
  risks. Low SNR, weak RT support and recorded pairing competition stay on the
  row. Competition names what the alternative would do with this peak: the
  samples and native apex RTs it would join, or leaving this group. An
  unselected alternative is not assigned a published feature ID. Internal
  records stay in SQLite. No recorded risk is not a claim of scientific
  acceptance.
- Older current-format results remain readable with their saved origins;
  values with no recorded origin use dotted lines, not detect's solid lines.
  Rerun Alignment to correct missing backfill origins. Review never rewrites
  them or infers the extraction stage from method evidence.

## Reading order

Review starts with results that differ from the rest of their target. Within a
target, the saved counted outcome shared by at most 20% of recorded decisions is
the minority: a rare counted result may be a wrong peak, and a rare not-counted
result may be a missed one. An outcome shared by every sample is a batch fact
and needs no review. Without a clear majority, rows with saved review
conditions set the reading order. `summarize_targeted_results` owns this order
for the workbench and the workbook summary, so their 先看 counts match. It does
not reclassify results.

For Untargeted reports, the overview leads with the Matrix. Groups with an
explicitly unresolved saved decision leave those samples blank; the overview
states their count as context, one action away, not as a required review step.
Ordinary missing values are not treated as abnormal or not detected. The default order uses unresolved-cell count, then numeric sample
coverage; it is not a scientific score. The approximate m/z search ranks by
distance and applies no implicit mass tolerance, so a match does not confirm
compound identity.

## Retained evidence and diagnostics

Delta Mass Discovery has a separate sample-local review source, not an Alignment
result. Its selected-candidate JSON supports shared-scale RAW XICs and individual
MS2 spectra in the same Inspector. The
[candidate review contract](discovery.md#read-only-candidate-review) owns the
new-read versus saved-answer distinction; the diagnostic export CLI owns RAW
access. This does not add a Delta Mass Matrix or an analysis form in Workbench.

`alignment_explanation.sqlite` is the shared, method-neutral Alignment
evidence file. Its content is `diagnostic_only`: it records request/attempt
provenance, effective configuration, retained candidate/group/cell facts and
the published Matrix hash. It cannot authorize a writer or prove readiness.

- The default compact report keeps values, membership and review reasons, and
  the saved raw curve of each cell with a value; full audit retains every curve
  with its processed series. These are retention choices, not separate
  scientific authorities.
- Repeated membership owner facts and compared peer groups are stored once.
  Readers restore every selected group, alternative partition, score and reason;
  this is lossless storage, not a reduction in review evidence.
- Readers reject obsolete layouts instead of upgrading them. Rerun Alignment to
  produce a current report.
- Explanation publication follows successful Matrix writing. If explanation IO
  fails, the command fails and the Matrix can remain without a report.
- Aligned-time plots replay the final frozen `SampleRtFrame` calibration, never the bootstrap
  ISTD-reference fit. Raw and processed curves, apex and integration bounds use
  the same [graded query](rt-calibration.md#graded-query-support).
  A sample with no estimate displays raw RT and is explicitly labelled as such.
  Injection order is retained independently of ISTD-reference provenance.
  Display alignment cannot repair grouping that was built without the intended
  ISTD input.
- `ms1_apex_mz` is the measured MS1 apex m/z for that candidate and sample.
  Missing measurements stay null; they are never replaced by the row center or
  precursor m/z.
- A curve bundle produced by `explain_cid_nl_run --curve-bundle` holds the same
  review curves: saved curves where the run kept them and labelled RAW reads
  for the other samples. It does not rerun Backfill, choose peaks or replace
  recorded quantification.

## Packaging

`packaging/xic_extractor.spec` defines the frozen-build resource and entry-point
contract. Source tests verify the Python and resource contract only; a frozen
release is verified only by its artifact build and smoke run.

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

- [Targeted workbook](targeted-workbook.md) | [Quant Matrix](quant-matrix.md) | [Matrix Curation](matrix-curation.md)
- [Presets](presets.md) | [Sample metadata](sample-metadata-qc.md)
- [Architecture contract](../architecture-contract.md#dependency-direction)
