# XIC Inspector

XIC Inspector is the installed, read-only review surface for completed XIC
evidence. It selects a bounded set of evidence items and writes one
self-contained HTML workbench that can be copied and opened offline. The
Inspector does not acquire RAW data, rerun scientific processing, change
scientific decisions, or write Matrix values.

## Public entry points

The package installs the `xic-inspector` command and provides the equivalent
module entry point:

```console
xic-inspector --report cid_nl_explanation.sqlite --output review.html --strategy sample
python -m xic_inspector --report cid_nl_explanation.sqlite --output review.html --strategy attention
```

Both entries accept a completed feature-evidence SQLite report, an output path,
and one selection strategy:

| Strategy | Purpose |
| --- | --- |
| `sample` | Balance real cross-sample groups by sample coverage, support origin, and recorded state; use ungrouped candidates only when group items do not fill the limit. |
| `attention` | Select items carrying owner-provided review conditions. |
| `query` | Filter one actual evidence-witness chain by sample, candidate, group, precursor m/z, or RT apex, with offset/limit pagination. |

`xic_inspector.api` exposes `SelectionRequest`, `ReportResult`,
`generate_report`, progress callbacks, and cancellation for installed callers.
A successful result reports the bound evidence locator, capability, selected
sample scope, and run/eligible/matched/selected counts. Invalid requests,
unavailable or invalid sources, unsafe output paths, output failures, and
cancellation remain distinguishable errors.

The default sample uses `cross_sample_sha256_v2`, limit 30 and seed 0. It cycles
through represented group cases: broad coverage (all or all but one scoped
sample, with at least two observed samples), other cross-sample coverage, and
limited coverage. Recorded recovery, reacquisition, discovery support, and
the existing review states distinguish cases. Stable hash ranking selects
within a case; adding ungrouped candidates cannot displace existing group
cases. The report retains the algorithm, seed and selected case counts.
Exact-ID queries remain available for any row, group or ungrouped candidate.

## Source and ownership boundary

The common API, selection model, and renderer consume canonical review items,
witnesses, curves, and capability information. Source-specific record kinds,
schema interpretation, and relationship projection stay in
`xic_inspector.sources`. The current source adapter reads finalized pure
Untargeted CID-NL evidence through the scientific owner's read-only metadata and
selected-curve interfaces.

Scientific owners retain acquisition, signal preparation, scoring, identity,
group/cell decisions, Matrix publication, and evidence serialization. The
Inspector validates the report's bound Matrix identity before rendering and
does not import RAW readers or invoke scientific preparation/evaluation paths.
Core pipeline packages do not import or call the Inspector.

## Offline report contract

The generated HTML contains its CSS, JavaScript, data, and SVG charts inline.
It has no report-time database, server, CDN, or network dependency. Item,
sample, and candidate navigation; filters; evidence details; lightbox views;
and keyboard interaction operate on the embedded selected subset.

Source adapters supply readable titles and explanations of recorded outcomes.
The item list keeps a concise outcome title, status, and readable reason so that
users can scan results before opening an item.
Ungrouped excluded candidates remain accessible in a counted, collapsed list
section. Ordinary reports initially show other items; explicit queries and
in-report filters expose matching excluded records. A report containing only
excluded candidates explains how to open them. This presentation does not alter
the selected population, retained evidence, or API selection semantics.
An ordinary report initially opens its group or row with the greatest observed
sample coverage. Explicit queries keep their first matching item. Group summaries
show observed coverage and recorded initial/final membership; each sample
distinguishes initial discovery support, reacquired support, and method recovery
with its exact evidence references. A physical direct-observation cell is not
by itself evidence of initial discovery support.
Recorded initial comparisons can link to a distinct peer group or row.
Each selected primary embeds curves for at most one exact peer and exposes the
total peer count and queryable references. Peer context does not increase the
primary selected count or create a scientific relationship.
Within a final group, optional `internal_pair_support` shows retained initial
edges whose exact member endpoints both belong to that group. Eligible edges
are direct support; review-only edges are not direct support. The panel shows
sample labels, recorded reasons, scores, and available raw/corrected RT deltas.
Each edge remains one record even when it links more than two candidate keys.
Direct edges may connect through another member; they do not imply every pair
agrees. Initial comparison records are not a trace of the final split decision.
The workbench links the sample result, candidate, retained capture, integration
facts, and available decision records. Viewing a sample scopes its candidate
choices and peak details. An explicit return beside the sample heading restores
the all-sample view without resetting the item's display settings. Viewing a
candidate does not change the recorded decision. Integration boundaries, peak
time, and raw-count area are shown with the selected capture; that area does
not replace the final quantitative value.

Readable evidence records first show their supplied summary and linked records.
Full fields and exact identifiers remain available together on demand; missing
summaries do not become generic filler. Curve provenance, quantitative decisions,
and candidate exclusions have distinct entry labels. Excluded candidates can
open their recorded exclusion evidence even when no curve is available.
Closing evidence returns to its
opening control. A link opens a record only when its content is embedded in
this report; an external or missing reference remains identifiable without
claiming its content is available offline. CID-NL decision and curve records
link to related method assessments and initial edges through exact sample and
candidate keys; those links do not establish formal quantification adoption.
The report does not infer a decision reason or a candidate relationship from a
display name, nearby mass, or nearby retention time.

Capture stage, signal type, and time axis are distinct. Raw-time and aligned-time
plots are shown together for comparison, with separate zoom controls. Aligned
curves and peak marks use the formal calibration saved by the producer that
built the groups. The source adapter applies its stored sample knots to curve
and peak times without fitting again or extrapolating. Missing calibration,
insufficient sample anchors, and points outside the saved domain remain
unavailable; partial coverage is visible. Old reports without this evidence
retain the raw plot. Aligning a plot cannot repair grouping that was created
without the intended ISTD input. The integration readout identifies its
raw-time coordinates. Precursor m/z uses four fixed decimal places, primary RT uses two,
and detailed peak times use three. NULL, numeric zero, a missing single result,
and unavailable curve evidence remain distinct within each item's sample
results. Sample rows and curve legends visibly identify multiple candidates,
explicit NULL, numeric zero, and unavailable curves for the responsible sample.
Candidate counts use distinct supplied candidate identities within that sample;
additional captures or processing stages do not add candidates. These markers
call attention to recorded facts without declaring method failure or replacing
the quantitative result. These sample states do not require separate review items.
Recorded cell decisions appear under their exact group, row, and sample even
when the sample has no candidate. An explicit `not_searched` cell remains NULL
with its recorded explanation; it does not create a candidate or a query witness.
Item explanations expose recorded review conditions, such as a related method
HOLD and its source reason, without treating them as the final cell decision.

Report generation writes through a temporary file and replaces the requested
output only after successful completion. Cancellation or failure leaves an
existing report intact. The output cannot alias its source evidence database or
bound Matrix. The 50 MiB final-file limit fails explicitly rather than dropping
or downsampling scientific points.

## Authority

The workbench is a review projection. Its charts, explanations, and diagnostic
states do not grant selected-value, Matrix-writing, activation, or
ProductWriter authority. Missing evidence remains visibly unavailable or
limited according to the source capability; the Inspector does not synthesize
replacement scientific values.
