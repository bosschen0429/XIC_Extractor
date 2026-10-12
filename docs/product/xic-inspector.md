# XIC Inspector

XIC Inspector is the installed, read-only review surface for completed XIC
evidence. It selects a bounded set of evidence items and writes one
self-contained HTML workbench that can be copied and opened offline. The
Inspector does not acquire RAW data, rerun scientific processing, change
scientific decisions, or write Matrix values.

## Local HTML workspace

`xic-extractor`, `xic-workbench <result-folder>`, or
`python -m xic_workbench <result-folder>` starts the unified local HTML
workspace and opens its browser URL. The source argument
is optional; the page can choose a folder, Targeted XLSX, or completed CID-NL
SQLite report.
`--raw-dir` and `--dll-dir` initialize the local source locations; the process
environment's `XIC_RAW_ROOT` and `THERMO_RAWFILE_READER_DLL_DIR` supply defaults.
`--no-open` prints the URL without opening a browser. `--port` selects its
loopback port, and `--config-dir` selects writable application state. Stop the
process with Ctrl+C when finished. Reports should be downloaded before stopping.

The same navigation launches Targeted and Untargeted work. Analysis jobs and
their output actions remain available beside review; they delegate to the
existing application and writer owners described in
[the workbench contract](untargeted-gui.md).

The workspace shows full-run, matched and current-page counts. Exact group ID,
m/z and RT filters use the Inspector's full-population query, in pages of 25.
The population includes both grouped records and ungrouped candidates. Select
an item to inspect its saved evidence, then select a few observed samples and
load their XIC. A sample without retained apex coordinates is shown as
unavailable, not as zero signal. The recorded view and newly acquired curves
have separate tabs; the selected tab's download is a standalone Inspector HTML.

Acquisition is sequential and bounded to the saved observations of the selected
samples. The page displays progress and supports cooperative cancellation
between RAW operations. A failed or cancelled job leaves prior reports usable.
One acquisition runs at a time. The workspace never changes the source evidence
or Matrix, and new curves do not choose peaks, integrate or replace saved values.

The report's sample stem locates each RAW. Its content must match exactly one
acquisition source retained by that report. These older reports do not retain
an independent canonical-sample-to-RAW binding: the page and curve provenance
state this limitation. Missing files and mismatched content fail explicitly.

`xic_workbench.workspace` owns the local session and jobs;
`xic_workbench.acquisition` owns diagnostic reacquisition, shared with the
explanation CLI. `xic_inspector.api.prepare_report` supplies selected evidence
to both the workspace and offline report generation. The renderer loads ordinary
HTML/JavaScript package assets and embeds them into the exported file.
The transport binds only to `127.0.0.1` and serves packaged assets and this
session's reports. It is one local analysis and review session.

### Targeted XLSX review

After a Targeted run, **在結果檢閱開啟** opens its delivered workbook directly.
Alternatively, choose the XLSX in the review source picker or enter its path.
A folder containing exactly one XLSX can also be opened; multiple workbooks
require an explicit file choice. Normal Targeted delivery remains one XLSX:
review creates no report or sidecar beside it and exposes no new export option.

The current workbook has an `Overview` landing sheet and `XIC Results`, with
`Targets`, `Run Metadata` and nonempty `Diagnostics` as supporting references.
The `targeted_output` schema marker and workbook layout version 2 bind the saved
records; other layouts are rejected rather than reinterpreted.

In Excel, `Overview` presents saved counted/not-counted totals, per-target
counts and a counted-result RT scatter plot. Result rows, and that plot's
sample axis, follow the run's resolved injection order: the supplied order or
sample metadata, otherwise RAW modification time. The charts do not
recalculate scientific decisions. `Diagnostics`
is a Traditional Chinese action list with one row per unresolved sample/target,
the saved count outcome, a contextual explanation and links to the measurement.
Original diagnostic events remain intact in an expandable appendix; event text
alone does not override the final count outcome or reopen a confirmed result.
Reference-RT drift, resolved multi-peak selection and confirmed ISTD backfill
remain context rather than standalone review tasks. Unconfirmed identity,
unresolved conflicts and independent peak-quality concerns remain actionable.
The main results show RT, Area and IntSmoothed (smoothed apex intensity);
Area and intensities use scientific notation, and table values are centered;
raw Int and PeakWidth remain available in the grouped detail columns alongside
review reasons. The result and target tables
retain their canonical first-row headers and numeric values, with advanced
columns grouped away. `Run Metadata` adds explanations alongside its unchanged
key/value records. These presentation changes retain layout version 2 and the
Inspector reader contract.

Review starts with the results that differ from the rest of their target.
Within a target, the saved counted outcome shared by at most 20% of its
recorded decisions is the minority: a rare counted result may be a wrong peak,
and a rare not-counted result may be a missed one. An outcome shared by every
sample is the batch fact and needs no review, even when its rows carry saved
review conditions. Without a clear majority, the rows with saved review
conditions remain the reading order. `summarize_targeted_results` owns this
reading order for every consumer; it does not reclassify results.

Opening Targeted results first shows a full-batch overview in three levels.
The heading states how many minority results to read first, beside Analyte and
ISTD counted totals. A target table follows, minority targets first, with each
target's role, counted total and a one-line explanation; a fully counted target
with saved review conditions mentions their number there. Expandable details
and the target page hold the rest. Buttons open all results or only the
minority rows; click a target to narrow the next level. **整批結果總覽** returns
to the full batch, independent of the current filters or page. A collapsed
quantity summary shows per-target median Area and RT range for counted results
only. Missing count decisions are reported as unknown, never inferred from
numeric values.

Choose a target to see one compact sample table: saved Area, RT, counted status
and saved review condition. Rows keep injection order with minority rows marked; filters
show all rows, the minority rows, or the rows with saved review conditions.
Selecting a sample opens its peak bounds, NL evidence, the same sample's paired
ISTD RT, Area and counted status, review conditions and the original selection
text directly under that row. Target and method settings are separate
expandable details.

Literal `ND`, zero and blank values remain distinct.
The workbook is not modified, and its SHA-256 binds subsequent queries to the
opened version; reopen the file after an external edit.

Filter by full target name, sample, saved review condition, target m/z or
saved sample RT. RT filtering matches a recorded numeric RT in the selected
sample scope, not the target's configured search window. The focused sample
detail shows retained peak and review evidence; absence of a review condition
is not scientific acceptance.

Saved-result review does not require RAW or DLL access. The workbook does not
retain point-by-point chromatograms or a verified sample-to-RAW binding, so this
view offers neither reconstructed plots nor on-demand acquisition. Sample names
are workbook labels, not verified acquisition identities. Its review pages are
temporary session views, not additional normal-run outputs.

## Public entry points

The package installs the `xic-inspector` command and provides the equivalent
module entry point. Neither requires the local analysis server:

```console
xic-inspector D:\data\run-output
xic-inspector cid_nl_explanation.sqlite --strategy attention
python -m xic_inspector diagnostic_curves.json --output review.html --no-open
```

- The source is a completed feature-evidence SQLite report, a Targeted workbook,
  a versioned curve-bundle JSON file, or an output folder. A folder resolves to
  its `cid_nl_explanation.sqlite`, or to the one in its `alignment` subfolder.
  Without SQLite evidence, a single XLSX is accepted; multiple XLSX files
  require an explicit path.
  `--report` is an equivalent named form.
- Without `--output`, the report is written as `xic_inspector.html` beside the
  resolved source, the same default as the workbench export.
- A successful report opens in the default browser; `--no-open` only writes it.
- A folder without the evidence file exits with the source-unavailable code 3.

Each run uses one selection strategy:

| Strategy | Purpose |
| --- | --- |
| `sample` | Balance real cross-sample groups by sample coverage, support origin, and recorded state; use ungrouped candidates only when group items do not fill the limit. |
| `attention` | Select items carrying owner-provided review conditions. |
| `query` | Filter one actual evidence-witness chain by sample, candidate, group, precursor m/z, or RT apex, with offset/limit pagination. |

When an Untargeted HTML run publishes an evidence locator, its result view can
open the source in the review workspace or export `xic_inspector.html` beside
the evidence file with an explicit Inspector selection.

- **在工作區檢閱** loads the completed evidence into the shared review view.
- **自訂 Inspector 匯出** uses the same `SelectionRequest` limits, shows source
  sample stems beside their canonical IDs, and produces a standalone HTML file.
- A new run clears the open target.
- Report progress, cancellation, and errors do not change science completion
  or Matrix publication.

`xic_inspector.api` exposes `SelectionRequest`, `ReportResult`,
`generate_report`, progress callbacks, and cancellation for installed callers.
A successful result reports the bound evidence locator, capability, selected
sample scope, and run/eligible/matched/selected counts. Invalid requests,
unavailable or invalid sources, unsafe output paths, output failures, and
cancellation remain distinguishable errors.

The default `sample` strategy uses `cross_sample_sha256`, limit 30, and
seed 0.

- The strategy cycles through the represented group cases:
  - broad coverage: all scoped samples, or all but one, with at least two
    observed samples;
  - other cross-sample coverage;
  - limited coverage.
- Recorded Backfill, reacquisition, Discovery support, and the existing review
  states distinguish the cases.
- Stable hash ranking selects within a case. Adding ungrouped candidates
  cannot displace existing group cases.
- The report retains the algorithm, the seed, and the selected case counts.
- Exact-ID queries remain available for any row, group, or ungrouped
  candidate.

## Lightweight Alignment review

New Alignment explanations default to `alignment_explanation_review`. They
retain the full candidate/group population, grouping reasons, compact method
conclusions and sample cell values. Cell values are stored once per row; full
artifact envelopes and curve arrays are not included by default. Query,
attention and RT selection use the saved observation coordinates and decisions,
so a report without curves remains selectable. Inspector builds detailed cell
and grouping records for selected items after population selection.

New review reports index candidate coordinates, membership and sample scope
at publication. SQLite applies query predicates, counts and offset/limit before
Inspector loads the selected page's witnesses and details.
New reports store row sample membership separately from the packed cell values,
so checking scope does not require decoding all values. Sample and attention
selection use persisted per-sample fact masks, including observed coverage,
attention, exclusion, null/zero values and support origins. Changing sample
scope intersects these masks rather than decoding the retained records/cells.
Seeded balanced sampling still ranks the compact eligible identities to preserve
its exact deterministic algorithm. Detailed reasons, sample
statuses and presentation are built only for selected items and displayed peers.

Use `scripts.run_alignment --explanation-curve-mz-range LOWER:UPPER` to retain
curves only for requested regions (repeatable), or `--explanation-full-audit`
to explicitly pay for the full audit and all curves. These options imply
explanation output. Existing full reports remain readable. Missing unretained
curves are shown as unavailable; they are not interpreted as zero signal.

An existing full report can be reduced without RAW or another Alignment run:

```powershell
.venv/Scripts/python.exe -m tools.diagnostics.explain_cid_nl_run --report <full.sqlite> --compact-to <review.sqlite>
```

The source is preserved. Conversion retains values, membership and review
reasons, omitting curve payloads and duplicated artifact chains. Compact review
cell references are display positions, not scientific decision IDs; full audit
retains the latter. The bound Matrix file must remain available.

An older compact review can gain or upgrade the selection index without losing retained facts
or curves. This writes a new file and requires no RAW access:

```powershell
.venv/Scripts/python.exe -m tools.diagnostics.explain_cid_nl_run --report <review.sqlite> --index-query-to <indexed-review.sqlite>
```

Older reports without the current index remain readable through population
selection. Query-only indexes remain usable for query selection until upgraded.

For a curve not retained in the report, acquire only the requested candidate's
or group's observed windows into a portable bundle:

```powershell
.venv/Scripts/python.exe -m tools.diagnostics.explain_cid_nl_run --report <review.sqlite> --candidate-id <id> --curve-bundle output/selected-curves.json --raw-dir <raw-directory> --dll-dir <dll-directory>
.venv/Scripts/python.exe -m xic_inspector output/selected-curves.json --no-open
```

Use `--group-id` instead of `--candidate-id` for the group's retained candidate
observations. `--curve-padding-min` defaults to 0.5 minutes around each saved
apex; `--curve-ppm` defaults to 5. These are explicitly labelled new diagnostic
extractions from the supplied RAW files, using the current signal profile.
They do not rerun Backfill, choose peaks, integrate areas or replace recorded
quantification. Unobserved samples are not acquired implicitly. The Inspector
itself remains acquisition-free.

## Source and ownership boundary

The common API, selection model, and renderer consume canonical review items,
witnesses, curves, and capability information. Source-specific record kinds,
schema interpretation, and relationship projection stay in
`xic_inspector.sources`. The current source adapter reads finalized pure
Untargeted CID-NL evidence through the read-only metadata and selected-curve
interfaces of the scientific owner.

The second source is `xic_curve_bundle`. A diagnostic can construct
`CurveBundle`, `TransitionCurves`, `CurveBundleTrace`, `ChromatogramCurve`, and
`PeakBounds` from `xic_inspector.sources.curve_bundle`, then call
`write_curve_bundle(bundle, path)`. The writer validates and atomically writes
UTF-8 JSON. Inspector accepts the resulting `.json` through the same CLI and
`generate_report` API. The bundle is bound by its file SHA-256, with no Matrix
dependency. A transition is one selectable review item. The key of a
transition is the group query ID. Each trace ID is a candidate query ID.

- Each transition carries a fixed `review_rt_window` and a `quality_flags`
  JSON object. `quality_flags` preserves transition-level gate labels,
  identity decisions, counts, Δm/z, ΔRT, ratios, and other diagnostic text or
  numeric facts. The item list and summary state the sample and flag counts;
  the flags themselves open through the transition's quality-flag record.
- Each trace carries the sample ID and label, role, RAW identity, extraction
  m/z, ppm and RT window, producer and version, and its own quality flags.
- An available trace carries paired `ChromatogramCurve(rt, intensity)` raw and
  **precomputed Gaussian15** series. An optional baseline uses the same RT
  coordinates.
- Optional `PeakBounds(start, apex, end)` records the reviewed peak in minutes.
  `CurveIntegration` carries the saved raw area in counts·seconds, raw apex RT
  in minutes, and integration method; it supplies the curve's numeric readout.
  Optional `positive_signal` carries the owner's precomputed baseline-positive
  signal on the same RT coordinates; the viewer does not derive it.
- A transition's optional `quantifications` contains one `SampleQuantification`
  per represented sample: its saved formal value and unit. Null is an explicitly
  empty result; an absent entry means no single formal result was supplied.
  This value is independent of the number or roles of displayed curves and of
  count/review flags. An analyte/ISTD overlay therefore retains each curve's own
  integration without replacing the analyte's formal value. Quality flags do
  not supply or override numeric results.
- Producers choose the display window from retained observation context so the
  selected envelope and available flanks remain visible; reference RT windows
  must not crop the retained integration interval.
- A missing-RAW trace has both series absent and an explicit
  `unavailable_reason`.
- Inspector displays the saved series. Inspector does not smooth, acquire RAW,
  or infer a decision.

The retained `smoothed` series of the SQLite source also uses a 15-point
Gaussian profile by default. The SQLite series uses a zero-padding edge
policy. The older diagnostic overlay helper uses an edge-padding policy. A
bundle producer must therefore persist the series it actually reviewed. The
producer must not substitute the SQLite series on the assumption of identical
boundary values.

Scientific owners retain acquisition, signal preparation, scoring, identity,
group/cell decisions, Matrix publication, and evidence serialization. The
Inspector validates the bound Matrix identity of the report before rendering.
The Inspector does not import RAW readers or invoke scientific
preparation/evaluation paths. Core pipeline packages do not import or call the
Inspector.

## Offline report contract

### Self-contained output

- The generated HTML contains its CSS, JavaScript, data, and SVG charts
  inline.
- The HTML has no report-time database, server, CDN, or network dependency.
- Item, sample, and candidate navigation, filters, evidence details, lightbox
  views, and keyboard interaction operate on the embedded selected subset.
- The generated HTML uses the existing atomic replacement and 50 MiB output
  limit for both sources.

### Item list

- Source adapters supply readable titles and explanations of recorded
  outcomes.
- The item list keeps a concise outcome title, status, and readable reason, so
  users can scan results before opening an item.
- Ungrouped excluded candidates remain accessible in a counted, collapsed list
  section.
- An ordinary report initially shows the other items. Explicit queries and
  in-report filters expose matching excluded records.
- A report that contains only excluded candidates explains how to open them.
- This excluded-candidate presentation does not alter the selected population, retained
  evidence, or API selection semantics.

### Item workbench

- An ordinary report initially opens the group or row with the greatest
  observed sample coverage. An explicit query keeps its first matching item.
- The workbench shows, in order: the group summary; all-visible-sample curves
  and integration; sample relationships and cross-sample curves; then all
  sample results and support records.
- These main sections are visible without expanding a disclosure.

### Support origin

- Each sample distinguishes initial Discovery support, reacquired support, and
  Backfill support with its exact evidence references.
- Backfill records are displayed as `Backfill（回填）支持`. Backfill curves are
  displayed as `Backfill（回填）曲線`.
- The charts retain sample colours. The line style marks the support origin:
  solid for initial Discovery, long-dashed for reacquired, and dotted for
  Backfill.
- The sample legend names each origin. An unavailable origin uses a separate
  dash when another sample of the item records an origin. An item with no
  recorded origin draws solid lines, because line style has nothing to
  distinguish.
- A physical direct-observation cell is not by itself evidence of initial
  Discovery support.

### Peer groups and internal pair support

- Recorded initial comparisons can link to a distinct peer group or row in the
  source projection. The workbench has no separate comparison panel for them.
- Peer context does not increase the primary selected count or create a
  scientific relationship.
- Within a final group, optional `internal_pair_support` shows retained
  initial edges whose exact member endpoints both belong to that group.
- Eligible edges are direct support. Review-only edges are not direct support.
- The panel shows sample labels, recorded reasons, scores, and available
  raw/corrected RT deltas.
- Each edge remains one record, even when the edge links more than two
  candidate keys.
- Direct edges can connect through another member. Direct edges do not imply
  that every pair agrees.
- Initial comparison records are not a trace of the final split decision.
- Selecting a graph node or edge scopes the cross-sample curve lanes.
- Lanes follow the saved injection order, including after a pair is selected.
  A missing order is identified explicitly.
- Each lane uses the exact retained support binding of its sample.

### Sample and candidate views

- The workbench links the sample result, candidate, retained capture,
  integration facts, and available decision records.
- Viewing a sample scopes its candidate choices and peak details.
- An explicit return beside the sample heading restores the all-sample view.
  The return does not reset the display settings of the item.
- Viewing a candidate does not change the recorded decision.
- Integration boundaries, peak time, and raw-count area are shown with the
  selected capture. That area does not replace the final quantitative value.

### Evidence records and links

- A readable evidence record first shows its supplied summary and linked
  records.
- Full fields and exact identifiers remain available together on demand. A
  missing summary does not become generic filler.
- A field value that is a serialized object or list is displayed as key-value
  lines or a list, not as raw JSON text.
- Curve provenance, quantitative decisions, and candidate exclusions have
  distinct entry labels.
- An excluded candidate can open its recorded exclusion evidence even when no
  curve is available.
- Closing an evidence record returns to its opening control.
- A link opens a record only when the record content is embedded in this
  report. An external or missing reference remains identifiable. The report
  does not claim that the content of an external or missing reference is
  available offline.
- CID-NL decision and curve records link to related method assessments and
  initial edges through exact sample and candidate keys. Those links do not
  establish formal quantification adoption.
- The report does not infer a decision reason or a candidate relationship from
  a display name, nearby mass, or nearby retention time.

### Curve signals

- Curves default to the saved smoothed signal.
- Selecting raw or another available signal replaces the curve. The selection
  does not overlay raw underneath the curve.
- Cross-sample lanes scale each sample to the maximum of its selected signal.
- Missing selected-signal data does not fall back to raw.
- The display does not prepare or smooth data again.

### Time axes

- Capture stage, signal type, and time axis are distinct.
- Raw-time and aligned-time plots are shown together for comparison, with
  separate zoom controls. When the selected curves have no aligned
  coordinates, the raw-time plot uses the full width and one line states that
  the aligned plot is unavailable.
- Aligned curves and peak marks use the formal calibration saved by the
  producer that built the groups.
- The source adapter applies the stored sample knots to curve and peak times.
  The adapter does not fit again or extrapolate.
- Missing calibration, insufficient sample anchors, and points outside the
  saved domain remain unavailable. Partial coverage is visible.
- Old reports without calibration evidence retain the original-time plot.
- Aligning a plot cannot repair grouping that was created without the intended
  ISTD input.
- The integration readout identifies its raw-time coordinates.
- Precursor m/z uses four fixed decimal places. Primary RT uses two. Detailed
  peak times use three.

### Sample result states

- NULL, numeric zero, a missing single result, and unavailable curve evidence
  remain distinct within the sample results of each item.
- Sample rows and curve legends visibly identify multiple candidates, explicit
  NULL, numeric zero, and unavailable curves for the responsible sample.
- Candidate counts use the distinct supplied candidate identities within that
  sample. Additional captures or processing stages do not add candidates.
- These markers call attention to recorded facts. The markers do not declare
  method failure or replace the quantitative result.
- These sample states do not require separate review items.
- Recorded cell decisions appear under their exact group, row, and sample,
  even when the sample has no candidate.
- An explicit `not_searched` cell remains NULL with its recorded explanation.
  A `not_searched` cell does not create a candidate or a query witness.
- Item explanations expose recorded review conditions, such as a related
  method HOLD and its source reason. Item explanations do not treat those
  conditions as the final cell decision.

### Write safety and limits

- The bundle reader rejects malformed files, missing provenance, and inputs
  over 50 MiB.

- Report generation writes through a temporary file. The generator replaces
  the requested output only after successful completion.
- Cancellation or failure leaves an existing report intact.
- The output cannot alias its source evidence database or bound Matrix.
- The 50 MiB final-file limit fails explicitly. The generator does not drop or
  downsample scientific points to fit the limit.

## Authority

The workbench is a review projection. Its charts, explanations, and diagnostic
states do not grant selected-value, Matrix-writing, activation, or
ProductWriter authority. Missing evidence remains visibly unavailable or
limited according to the source capability; the Inspector does not synthesize
replacement scientific values.
