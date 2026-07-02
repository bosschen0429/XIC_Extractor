# Vendor RAW Evidence Model

This page captures product concepts borrowed from Thermo Xcalibur Qual Browser
and adjacent Thermo RAW tooling. It is not a request to clone Qual Browser, and
it does not make Thermo peak integration the XIC Extractor product contract.

The useful idea is that manual LC-MS review is a reproducible RAW evidence
query: choose which scans are eligible, choose what trace is extracted, choose
the mass and RT window, then apply explicit processing and peak-picking rules.
XIC Extractor should preserve that structure when turning RAW observations into
Discovery candidates, evidence vectors, review packets, and matrix-authorized
decisions.

## Contract

- RAW evidence is a query, not a free-floating column. A valid evidence claim
  needs scan filter, trace type, mass window, RT window, extraction backend,
  processing basis, and decision meaning.
- Scan filter is an evidence-selection predicate. It decides which scans are
  allowed to contribute to a chromatogram, MS2 neutral-loss claim, product-ion
  claim, or pattern claim. It is not merely display metadata.
- Trace type is part of the evidence ontology. Mass-range XIC/EIC, TIC,
  base-peak chromatogram, neutral-loss evidence, and product-ion evidence are
  different evidence views over the same RAW file.
- Multiple views of the same RAW file can all be legitimate, but each view must
  carry lineage. At minimum, distinguish vendor raw trace, smoothed trace,
  detection basis, boundary basis, and quantitation basis.
- Peak detection is a strategy layer, not the underlying truth. Qual Browser
  exposes multiple peak detection algorithms; XIC Extractor can use its own
  model only if its settings and consequences are explicit.
- Manual review and overrides are review-layer facts. They must not silently
  rewrite automatic Discovery, Alignment, Backfill, or ProductWriter rules.
- Evidence providers create typed facts. They do not write matrices directly
  unless a separate activation/export contract grants ProductWriter authority.

## Concept Mapping

| Vendor / Qual Browser concept | XIC Extractor concept | Product rule |
| --- | --- | --- |
| Scan filter | Evidence query predicate | Persist and compare it when evidence depends on scan eligibility. |
| Mass-range chromatogram / EIC | `XICRequest` and vendor XIC trace | Treat as chromatographic evidence for a specific m/z window, not a feature by itself. |
| TIC / base-peak chromatogram | Alternative trace views | Keep separate from mass-range XIC claims; do not mix evidence meanings. |
| Active plot / pinned view context | Evidence query context | Never compare traces unless scan filter, trace type, RT window, mass window, and processing basis are known. |
| Peak detection algorithm settings | Peak model / boundary strategy | Do not claim Qual Browser area parity unless detector settings are intentionally mapped and validated. |
| Chromatogram-to-spectrum linkage | MS2 scan evidence near an RT context | Link CID-NL/HCD-PI claims to scan RT, precursor context, and scan filter lineage. |
| Manual peak add/delete/integration | Review action / override layer | Store as explicit human review evidence, not hidden detector behavior. |

## Discovery Implications

- CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 pattern, shape, standards, and future
  learned models are evidence providers. They should be modeled as evidence
  queries over RAW-derived observations, not as permanent matrix-writing
  authorities.
- Composite Discovery strategies should compose evidence queries on an explicit
  anchor. For example, `pt_pattern + cid_nl(dR)` is an intersection on the same
  base peak, not two independent writers competing for the Matrix.
- When a composite method has a natural seed, use it to reduce the query space.
  CID-NL(dR) can seed base candidates before Pt pattern evidence checks
  `+1.0021`, `+2.0023`, and `+4.0052`.
- A Pt pattern detection needs both layers:
  - a delta-pattern seed showing the expected base-relative MS1 pattern; and
  - chromatographic co-elution evidence showing that the positions behave like
    the same feature group.
- A scan-level proxy may propose candidates or debug missing evidence, but it is
  not the full Pt pattern detector and must not become Matrix authority.

## Provenance Minimum

Any RAW-backed evidence artifact intended for durable review should record:

| Field | Why it matters |
| --- | --- |
| RAW file identity | Reopens the same source data. |
| Extraction backend | Separates Thermo vendor XIC from local scan-index approximations. |
| Scan filter or scan selection rule | Defines which scans were eligible. |
| Trace type | Distinguishes mass-range XIC from TIC, base peak, MS2, neutral-loss, or product-ion views. |
| m/z or mass range plus ppm tolerance | Defines the extracted ion evidence. |
| RT window and scan range when available | Defines the chromatographic context and replay target. |
| Processing basis | States whether decisions used raw trace, Gaussian-smoothed trace, or another transform. |
| Detection / boundary / quantitation basis | Prevents area, height, and accepted/rejected decisions from being conflated. |
| Anchor identity | Explains which feature or m/z position owns related evidence. |
| Decision meaning | States whether the artifact is diagnostic, review evidence, candidate creation, or write-authorized output. |

## Boundaries

- This page does not require a Qual Browser-style UI.
- This page does not require exact agreement with Qual Browser ICIS, Avalon, or
  Genesis integration unless a future validation explicitly targets that.
- This page does not authorize replacing Thermo vendor XIC with local scan-index
  extraction. Local approximations need their own expected-diff and acceptance
  framing.
- This page does not change Discovery, Alignment, Backfill, ProductWriter,
  schema, or matrix behavior by itself.

## Pitfalls

- Treating scan filter as a label after the fact instead of a scan-selection
  rule.
- Comparing raw and smoothed traces without naming which one drove the decision.
- Treating a scan-level pattern hit as chromatographic detection.
- Treating vendor visualization convenience as product authority.
- Letting review packets, diagnostics, or overlays mutate the Matrix without an
  activation/export contract.

## Sources

- Thermo Fisher Scientific, Xcalibur 4.6 Qual Browser User Guide:
  <https://docs.thermofisher.com/v/u/Xcalibur-4.6-Qual-Browser-User-Guide>
- Thermo Fisher Scientific, Xcalibur 4.0 Qual Browser User Guide:
  <https://tools.thermofisher.com/content/sfs/manuals/Man-XCALI-97781-Xcalibur-40-Qual-ManXCALI97781-A-EN.pdf>
- Thermo Fisher Scientific RawFileReader:
  <https://github.com/thermofisherlsms/RawFileReader>
- Thermo Fisher Scientific, MSFileReader Reference Guide (legacy trace-type
  concept reference):
  <https://tools.thermofisher.com/content/sfs/manuals/Man-XCALI-97542-MSFileReader-30-SP1-Ref-ManXCALI97542-A-EN.pdf>
- Thermo Fisher Scientific FreeStyle User Guide, chromatogram and spectrum
  review concepts:
  <https://docs.thermofisher.com/r/FreeStyle-User-guide/enGUID-2DA7CB81-26A1-4287-B228-2CA2BDB51D40>

## See Also

- [Evidence Spine](evidence-spine.md)
- [Discovery](discovery.md)
- [Run Provenance](run-provenance.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
