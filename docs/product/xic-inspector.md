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
| `sample` | Deterministically sample a bounded cross-section of evidence items. |
| `attention` | Select items carrying owner-provided review conditions. |
| `query` | Filter one actual evidence-witness chain by sample, candidate, group, precursor m/z, or RT apex, with offset/limit pagination. |

`xic_inspector.api` exposes `SelectionRequest`, `ReportResult`,
`generate_report`, progress callbacks, and cancellation for installed callers.
A successful result reports the bound evidence locator, capability, selected
sample scope, and run/eligible/matched/selected counts. Invalid requests,
unavailable or invalid sources, unsafe output paths, output failures, and
cancellation remain distinguishable errors.

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
When an ordinary report includes a Matrix row, it initially opens that row.
Explicit queries keep their first matching item. Sampling remains a cross-section
of evidence, so a known group query is the direct entry for comparing its samples.
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
curves and peak marks require supplied aligned coordinates; the current CID-NL
source does not provide them. The aligned section explains this limitation
while the raw plot remains available. The integration readout identifies its
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
