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
The workbench links the sample result, candidate, retained capture, integration
facts, and available decision records. Focusing a sample scopes its candidate
choices and peak details. Viewing a candidate does not change the recorded
decision. Integration boundaries, peak time, and raw-count area are shown with
the selected capture; that area does not replace the final quantitative value.

Readable evidence records describe the supplied facts and their explicit links.
Exact identifiers remain available on demand for traceability. A link opens a
record only when its content is embedded in this report; an external or missing
reference remains identifiable without claiming its content is available offline.
The report does not infer a decision reason or a candidate relationship from a
display name, nearby mass, or nearby retention time.

Capture stage, signal type, and time axis are distinct. Aligned curves and peak
marks require supplied aligned coordinates; the current CID-NL source does not
provide them. The workbench explains this limitation while showing available
raw-time data. Precursor m/z uses four fixed decimal places, primary RT uses two,
and detailed peak times use three. NULL, numeric zero, a missing single result,
and unavailable curve evidence remain distinct.

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
