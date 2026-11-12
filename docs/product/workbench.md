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
- Inspector does not read RAW files, rerun scientific preparation or smooth
  data again. It displays the series its source saved. Scientific packages do
  not import Inspector.
- Missing evidence stays visibly unavailable. Inspector never synthesizes a
  replacement value, and a missing curve is not zero signal.

## Diagnostic curves are not measurements

The workbench can read new XIC curves from RAW beside saved results. These
curves are diagnostic views, not product measurements.

Opening a saved result asks only for its result location. RAW and DLL sources
belong to the selected result's **RAW 曲線設定（選用）**, not the result opener.
Targeted curves still start from the sample row; Untargeted retains its group
sample selection and acquisition-range controls.

- An Untargeted group member without its own retained apex is read at the median
  m/z and apex RT of the group's observed samples. That group-position curve is
  drawn dashed and labelled as such, so a missed peak can be seen. It neither
  selects nor integrates a peak, and the saved empty value stays empty.
- Targeted curves use the workbook's extraction tolerance and signal-processing
  settings over the full acquisition trace. Saved peak bounds are overlays, not
  new integrations.
- Workbook sample names and report sample stems locate RAW files; they do not
  certify the original acquisition. The view and exported HTML state that the
  sample-to-RAW binding is unverified.
- Support checks for an uncounted target with a counted paired ISTD reuse the
  physical candidate gate. Their result is explained beside the plot and does
  not change ND or write values.
- A sample whose window yields no usable signal is named beside the curves.
  The request fails only when no selected sample yields a curve.

## Result states stay distinct

- NULL, numeric zero, literal `ND`, a blank Targeted cell and unavailable curve
  evidence remain distinct in every table, legend and export.
- An explicit `not_searched` cell remains NULL with its recorded explanation.
  It does not create a candidate or query witness.
- Line style marks support origin: solid for Discovery, dotted for Backfill. A
  physical direct-observation cell is not by itself evidence of Discovery
  support.
- Recorded review conditions, such as a related method HOLD, are explanations.
  They are not the final cell decision, and the absence of a review condition is
  not scientific acceptance.

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

- The default compact report keeps values, membership and review reasons and
  omits curve payloads; full audit retains every curve. These are retention
  choices, not separate scientific authorities.
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
- A curve bundle produced by `explain_cid_nl_run --curve-bundle` is a labelled
  new diagnostic extraction. It does not rerun Backfill, choose peaks or replace
  recorded quantification.

## Packaging

`packaging/xic_extractor.spec` defines the frozen-build resource and entry-point
contract. Source tests verify the Python and resource contract only; a frozen
release is verified only by its artifact build and smoke run.

## Verification

| Contract | Focused checks |
| --- | --- |
| Launch, routes, review and on-demand curves | `tests/test_xic_workbench.py`, `tests/test_workbench_analysis.py`, `tests/test_workbench_browser.py` |
| Targeted and Untargeted workbench adapters | `tests/test_workbench_config.py`, `tests/test_workbench_targeted.py`, `tests/test_workbench_untargeted.py` |
| Offline report layout and interaction | `tests/test_xic_inspector_html.py`, `tests/test_xic_inspector_curve_bundle.py` |
| Inspector reads no RAW and is not imported by scientific packages | `tests/test_xic_inspector_import_boundary.py` |
| Targeted workbook review and reading order | `tests/test_targeted_overview.py`, `tests/test_xic_inspector_targeted_workbook.py` |
| Frozen resources | `tests/test_pyinstaller_spec.py` |

## See Also

- [Targeted workbook](targeted-workbook.md) | [Quant Matrix](quant-matrix.md) | [Matrix Curation](matrix-curation.md)
- [Presets](presets.md) | [Sample metadata](sample-metadata-qc.md)
- [Architecture contract](../architecture-contract.md#dependency-direction)
