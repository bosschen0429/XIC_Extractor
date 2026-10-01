# XIC Inspector

XIC Inspector is the installed, read-only review surface for completed XIC
evidence. It selects a bounded set of evidence items and writes one
self-contained HTML workbench that can be copied and opened offline. The
Inspector does not acquire RAW data, rerun scientific processing, change
scientific decisions, or write Matrix values.

## Public entry points

The package installs the `xic-inspector` command and provides the equivalent
module entry point. Neither requires the Extractor GUI:

```console
xic-inspector D:\data\run-output
xic-inspector cid_nl_explanation.sqlite --strategy attention
python -m xic_inspector diagnostic_curves.json --output review.html --no-open
```

- The source is a completed feature-evidence SQLite report, a versioned
  curve-bundle JSON file, or an alignment output folder. A folder resolves to
  its `cid_nl_explanation.sqlite`, or to the one in its `alignment` subfolder.
  `--report` is an equivalent named form.
- Without `--output`, the report is written as `xic_inspector.html` beside the
  resolved source, the same default as the GUI export.
- A successful report opens in the default browser; `--no-open` only writes it.
- A folder without the evidence file exits with the source-unavailable code 3.

Each run uses one selection strategy:

| Strategy | Purpose |
| --- | --- |
| `sample` | Balance real cross-sample groups by sample coverage, support origin, and recorded state; use ungrouped candidates only when group items do not fill the limit. |
| `attention` | Select items carrying owner-provided review conditions. |
| `query` | Filter one actual evidence-witness chain by sample, candidate, group, precursor m/z, or RT apex, with offset/limit pagination. |

End users open the Inspector without commands. When an Untargeted GUI run
publishes an evidence locator, the GUI writes the default `sample` report as
`xic_inspector.html` beside the evidence file, so the HTML can also be opened
from the output folder.

- **開啟 Inspector** sits with the other result openers and opens the report
  of the current result, even after its source files are removed.
- **自訂匯出…** opens the settings dialog. The dialog uses the same
  `SelectionRequest` defaults and limits, shows source sample stems beside
  their canonical IDs, and its **Export and Open** opens the HTML.
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
selection reduce the needed decision facts once. Detailed reasons, sample
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

An older compact review can gain the query index without losing retained facts
or curves. This writes a new file and requires no RAW access:

```powershell
.venv/Scripts/python.exe -m tools.diagnostics.explain_cid_nl_run --report <review.sqlite> --index-query-to <indexed-review.sqlite>
```

Older reports without the index remain readable through population selection.

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
