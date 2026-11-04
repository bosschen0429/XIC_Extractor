# LC-MS/MS Evidence Rules

How XIC Extractor interprets LC-MS/MS evidence.

## Core Rule

Prefer evidence chains over any single metric. RT, CWT, WIS, iRT, local minima,
RT models, shape similarity, product ions, neutral losses, adducts and in-source
fragments are evidence inputs; none silently overrules the selected peak or
Matrix identity on its own. An evidence source that changes production behavior
needs explicit config or contract, machine-readable reason/status fields and
regression tests.

Evidence providers never write the Matrix directly;
[Writer Authority](product/decision-policy.md#writer-authority) decides how
values are published. Readiness and expected-diff rules are in
[`product-validation-contract.md`](agent/product-validation-contract.md).

This page holds the interpretation principles shared by every workflow. The
detailed rules live with their owners:

| Topic | Owner |
| --- | --- |
| Canonical signal, peak qualification, boundaries and area | [Signal processing](product/signal-processing-rationale.md) |
| Targeted measurement, paired ISTD and analyte counting | [Targeted Selection](product/targeted-selection.md) |
| ISTD recovery and Backfill support | [Backfill](product/backfill.md) |
| Hard blockers, write and no-write decisions | [Decision policy](product/decision-policy.md) |
| CID-NL support events | [Discovery](product/discovery.md#cid-nl-support-relations) |

## Physical Peak View Equivalence

`peak_detection.physical_peak_equivalence` compares views for Alignment claim
resolution and reports `same`, `distinct` or `unresolved`; none is a chemical
identification. Comparison never changes a selected integral or grants
membership.

- Views must share an acquisition source and overlapping extraction m/z
  intervals.
- Same-apex views are equivalent when their samples are identical in the common
  selected interval. Small flank differences are allowed when both intervals
  keep exactly the same raw peak maximum, the overlap has at least five matching
  acquisition times, and the integral of the absolute signal difference is at
  most 5% of the integral of the pointwise maximum in that overlap. This is a
  comparison tolerance, not an error estimate; neither trace is rescaled.
- Different apices need identical measured overlap around both apices and the
  physical segment owner assigning both queries to one lobe. Resolved separate
  lobes stay distinct. Nearby RT, overlapping bounds, similar area or high
  correlation alone prove neither identity nor separation.
- Alignment applies occupancy on top: existing Direct values remain; competing
  eligible Backfills do not create duplicate members or cells. `unresolved`
  does not establish a competing claim, since same-apex windows can contain
  different ions. Archive rows do not occupy Matrix peaks.

## RT And Identity

- RT is contextual evidence, not a hard identity veto. It must not prove absence
  or override co-eluting, candidate-aligned MS1/MS2 evidence unless an explicit
  hard RT exclusion policy exists.
- Large, reproducible or unmodeled RT shifts may trigger ambiguity or confidence
  demotion, especially when inconsistent with ISTD transfer.
- Cross-sample RT or m/z dispersion alone does not establish separate isomers,
  and lack of distinct peaks within a sample does not prove identity; inspect
  within-sample peaks and method MS2/NL evidence.
- MS1 shape alone must not dismiss a shoulder or weak peak with method MS2/NL
  support; that support does not authorize quantitation without reliable
  physical bounds.

## Missing MS2, Product Or Neutral-Loss Evidence

Missing DDA MS2/product/NL evidence is `not_observed` by default. It is negative
evidence only when acquisition opportunity, local sensitivity, precursor
selection or scan coverage, and comparable positive controls show it should have
been observed; a DDA trigger alone does not show that. A scan-free peak never
gains ranking priority over a reference peak whose MS2 lacks target support; only
positive evidence on the same target and peak earns priority.

## RAW Evidence Is A Query

A RAW-backed claim answers one reproducible query, so it keeps its scan filter,
trace type, mass window, RT window, extraction backend and processing basis.

- The scan filter selects which scans contribute; it is not display metadata.
- Mass-range XIC, TIC, base-peak, neutral-loss and product-ion views are
  different evidence; compare traces only when both queries are known.
- Name which view drove a decision: raw, smoothed, detection, boundary or
  quantitation basis.
- Peak detection is a strategy over the query result. Vendor integration (ICIS,
  Avalon, Genesis) parity is not claimed without deliberately mapped settings.
- Manual review and overrides are review-layer facts; they never rewrite
  automatic rules.
- Compare acquisition methods by their role: a high-resolution full-scan MS1 run
  can be Discovery evidence while a lower-resolution DDA run supports found
  bases. Absent DDA MS2 is not negative evidence unless the inclusion list shows
  the candidate should have been queried.

A zero in an extracted trace does not prove ion absence. Inspect the acquired
centroid stream, adjacent scans and scan mode before changing the mass window or
peak algorithm.

Sources: [Xcalibur 4.6 Qual Browser User Guide](https://docs.thermofisher.com/v/u/Xcalibur-4.6-Qual-Browser-User-Guide)
and [Thermo RawFileReader](https://github.com/thermofisherlsms/RawFileReader).

## Product Ions, Neutral Losses, Adducts And Fragments

- They are evidence only when co-eluting, boundary-aligned and assigned to the
  same precursor or candidate.
- Shared class fragments or common neutral losses support class confidence, not
  analyte-specific proof.

## Source Roles

- SDO/LEK are dedicated clean standards, expected only in their own samples.
- MixSTDs are non-biological clean standards with ISTDs and external standards.
  Non-ISTD MixSTD targets are external standards, not biological anchors unless
  spiked or validated.

Clean standards support instrument interpretation, reference checks and library
work, but cannot alone justify production correction of biological matrices.
Calibration-derived production changes need current-code biological transfer
evidence, row-level coverage or exclusion policy, and machine-readable GO/NO-GO
blockers.

## Audit Versus Production

- Keep audit and production gates separate. Sparse, extrapolated or
  low-coverage evidence stays review-only until a production policy exists.
- When manual EIC/MS2 review contradicts a diagnostic label, find whether the
  shared rule, the wording or the reviewed row is wrong. Fix the shared rule with
  regression tests; do not encode one-off sample or target exceptions.
- Canonical region audit projects the selected segment; it does not propose
  merges or grant authority.

## Targeted And Untargeted Boundary

- Shared evidence concepts (traces, candidates, boundaries, regions, integration
  audit, product/NL evidence) use common low-level models when that prevents
  schema drift, but only once their semantics match.
- Targeted outputs can serve as benchmarks or shared evidence, but target labels
  and Targeted pass/fail logic never leak into Untargeted Matrix identity
  without an approved contract.
- Targeted lessons feed Untargeted at the rule level. A Targeted-backed
  correction names the shared rule it changes, the cases it improves and the
  guard cases showing it does not overfit one target or sample.
- In Untargeted identity, RAW-overlay or raw-mode grouping is a hypothesis
  source: it does not change labels by itself, nor mask a named wrong-peak
  conflict or a complete positive evidence chain.
