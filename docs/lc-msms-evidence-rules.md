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

## MS1 Morphology And Area Owner

Design rationale is in
[`signal-processing-rationale.md`](product/signal-processing-rationale.md).

### Canonical signal

For profile-covered physical work the signal is exactly
`max(Gaussian(raw; window_points, sigma=window_points/6, unit_sum, zero_pad_same) - max(AsLS_RT(rt_minutes, raw; lam=1 minute^4, p=0.01, n_iter=10), 0), 0)`.

- Gaussian and AsLS are both computed from raw. AsLS is baseline estimation, not
  a second smoothing; its baseline is floored at zero so a negative fit cannot
  manufacture signal.
- A selected segment reaching either acquired-trace edge is unavailable
  (`incomplete_peak_context`) with no numeric facts. Discovery's bounded
  reacquisition can recover the context; unresolved clipped segments stay
  unavailable.
- Persisted evaluations keep their exact identity and are readable only under a
  supported profile. Unsupported profiles are rejected, never reinterpreted; no
  old-algorithm dispatch exists.
- Public profile fields: `signal_processing_method` (`gaussian` only) and
  `signal_processing_window_points` (default 9; odd integer 3–999). The legacy
  `ms1_morphology_smoothing_window_points` is a one-way load-time alias; equal
  values migrate with a deprecation receipt and conflicts fail before RAW.
  `smooth_window`, `smooth_polyorder` and `resolver_mode` cannot alter
  profile-covered facts.

### Lobes, valleys and seeds

- Interior apex candidates use an RT envelope with minimum sigma 0.08 min. Clear
  valleys on the canonical signal separate groups, each with one apex. A
  canonical valley hidden by the coarser envelope must stay below 50% of the
  smaller lobe for one Gaussian support footprint to restore a split; a short
  dropout notch is not enough. Persistence alone does not turn a shallow valley
  into a split.
- Candidate support uses topographic prominence and the three-point noise MAD
  over the prominence bases (and, for shoulder lobes, the contour toward higher
  neighbors). A candidate below four times that MAD is ripple only when its
  shoulder group has an independently supported apex; it cannot truncate that
  envelope.
- Supported seeds need 4% of their shoulder group's maximum, compared after
  clear valleys separate groups and never against the whole acquired context.
- The envelope only supplies lobe/valley evidence; boundaries, apex and area use
  the canonical signal. Shoulder-connected lobes share one envelope and area
  unless a supported clear valley separates them. Multiple canonical lobes above
  the 4% floor keep review evidence. An unresolved shoulder carries
  `physical_shoulder_overlap` into Targeted review reasons and Untargeted
  provisional evidence and cannot claim independent Untargeted quantification.

### Boundaries

- The selected interval runs to the first observed return to 10% of the apex
  residual, bounded by separating valleys. Only an interval reaching an acquired
  edge may use a return at the greater of 10% and the original-signal noise MAD;
  that alternative must have the same apex, be fully interior and lie within the
  original interval. A still-clipped alternative supplies no recovery; this does
  not infer an unobserved boundary.
- Point queries use `baseline_peak_contains_query`; membership and integration
  share the selected bounds, including inter-scan tails. Group-guided recovery
  uses `unique_baseline_peak_in_context`: its center locates the search context
  and need not fall inside the peak; multiple local peaks remain ambiguous and
  non-numeric.

### Qualifying a peak

These are lower bounds, not proof of identity or quantification readiness, and
not evidence of chemical absence when they fail.

- **Original support:** an adjacent pair of original XIC values, after baseline
  subtraction, must reach half-prominence inside the contiguous canonical core
  around the apex, and at least two original observations must support the top.
  Failures give `insufficient_original_xic_support` or
  `insufficient_original_xic_continuity`. Gaussian spread and constant
  background cannot supply support.
- **Censored clusters:** a local hit rate exceeding both flanks fourfold and
  passing a one-sided exact test corrected for trace length marks a censored
  detection cluster. A cluster with an acquired above-baseline run spanning one
  Gaussian window, and more acquired than missing scans in every core window,
  keeps its morphology segmentation. A qualified censored cluster bypasses the
  adjacency and shape checks, uses its observed trapezoid area, and carries
  `local_hit_density_enrichment` and `near_detection_limit`. Censored values are
  not imputed. Lack of enrichment does not reject a peak with adjacent support
  and local contrast.
- **Local contrast:** relief must be at least four times a local fluctuation
  scale. For each interior original point, take its difference from the linear
  interpolation of its neighbors divided by the Euclidean norm of the three
  coefficients (`diff(raw, n=2) / sqrt(6)` on a uniform grid); the scale is the
  unscaled MAD of these over the interval. Relief is the canonical apex minus
  the higher of its nearest left and right valleys between adjacent maxima; a
  side without a neighbor uses the interval endpoint residual. Fewer than three
  points or zero MAD adds no rejection. Failure gives
  `insufficient_local_peak_contrast`. Factor 4 is a minimum contrast policy, not
  a calibrated S/N claim.

### Targeted measurement

- Targeted analytes use `localized_total_envelope` measurements; known ISTDs use
  `reference_localized_contour_peak` geometry. Both need complete-region
  acquired support, with two positive observations as the minimum. Imperfect top
  observations, valley contrast and shape scores do not by themselves withdraw
  supported area. A single acquired impulse cannot become a chromatographic body
  through smoothing.
- The batch owner separates a real chromatographic body from baseline curvature
  on constant or monotonic background, using complete regions, raw support,
  cross-sample evidence and mass-compatible references. A retained measurement
  is not target identity, and a retention-incompatible interpretation does not
  license selecting a smaller residual elsewhere.
- Supported valleys define separate regions; a common reference does not join
  them or add their areas. Targeted selects one existing region with its
  complete integral, never an expected-RT crop. See
  [Targeted Selection](product/targeted-selection.md). Discovery and Backfill do
  not use this Targeted policy.
- Questions do not cross profile-bound segment universes; changed
  interpretations mint new questions and never rewrite saved answers.

### Area and boundary owners

- `ChromPeakSegment` owns the boundary for accepted candidates when a canonical
  segment contains the selected apex. `selected_full_envelope` is a fallback used
  only when no segment boundary exists; its split, neighbor-apex, blank-like,
  edge, width, scan-count and low-S/N guards stay fail-closed.
- Raw trapezoid and standalone AsLS areas may remain audit evidence for excluded
  legacy consumers. Owner Backfill has no legacy fallback: apex, boundaries,
  height, area, scan support and shape share one canonical signal.
- Area differences from legacy integration are accepted expected differences;
  parity is never manufactured by a second calculation. The 9-point default is
  scan-rate sensitive, so other windows are method parameters. Cross-batch
  production claims need a review of raw-versus-Gaussian area ratios and
  smoothing duration; that review never changes selected values.
- Morphology supplies boundary and shape evidence, not identity: RT, target
  window, MS2/NL opportunity, CID-NL evidence and cross-sample gates decide
  acceptance ([Discovery](product/discovery.md#cid-nl-support-relations),
  [decision policy](product/decision-policy.md)). Changes to selected
  hypothesis, ambiguity handling, SUPPORT/HOLD/REJECT, Backfill status, identity
  or write decisions are hard-zero expected differences; a qualitative mismatch
  is HOLD, never a reason to restore legacy computation.

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

## Untargeted Backfill

- Untargeted Backfill uses the shared physical signal owner directly; Targeted
  output parity is not its acceptance baseline.
- Broad Backfill candidate universes are review surfaces, not writer pools. The
  decision unit is the individual rescued cell.
- Broad-window membership, trace labels, local apex presence or scan support
  alone never promote a high-risk rescue. Scan support and trace quality measure
  assessability, not identity.
- Wrong-peak or unresolved-hypothesis blockers stay fail-closed. MS1/QC pattern
  conflict or unexplained RT outside the preferred band keeps a rescue
  review-only even when the peak is quantifiable in the wider query window.
- Pre-Backfill consolidation is a separate identity problem; the Backfill gate
  does not prove a consolidated row is chemically valid.

## ISTD Evidence

- ISTDs with candidate-aligned required CID-NL support and finite positive
  quantitation stay confirmed despite RT drift. RT-prior disagreement alone is
  neither a veto nor a review action; unresolved morphology or overlap can still
  need review.
- Missing required NL leaves an ISTD unconfirmed. The cross-sample Backfill
  route can confirm it with a validated accepted receipt; exclusions,
  identity conflicts and unavailable quantitation still block counting.
- Only direct confirmed observations seed the frozen support group; recovered
  ISTDs are not direct SUPPORT votes.
- RT prior and paired-ISTD agreement are not two independent identity
  observations.

## Targeted Analyte And ISTD Pairs

- Candidate-aligned CID-NL support outranks RT hints and trace quality. A
  configured RT window bounds acquisition but does not veto an eligible peak
  whose apex lies outside it.
- Paired ISTD RT guides localization; RT distance and legacy confidence labels
  are context, not vetoes.
- Analyte `NL_FAIL` and `NO_MS2` keep measured quantities but are not counted.
  Paired RT, area ratio or an observation-only Backfill do not confirm analyte
  identity.
- A DDA trigger without the required product/NL tag is acquisition opportunity,
  not support. Overlapping or boundary evidence stays set-level.
- External MS1-shape or approval files do not change the application's selection
  or count.

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
- Repeated DDA/NL triggers inside one Gaussian15 MS1 peak group are one support
  event with several scans, not several independent supports.
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
