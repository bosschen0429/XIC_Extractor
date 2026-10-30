# LC-MS/MS Evidence Rules

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/lc-msms-evidence-rules.md
Doc exit rule: Keep active as the durable LC-MS/MS evidence semantics owner; retire only after a replacement product evidence contract absorbs these rules.

This document owns XIC Extractor's durable interpretation rules for LC-MS/MS
evidence. `AGENTS.md` keeps only the short guardrails; this file carries the
full domain contract.

## Core Rule

Prefer evidence chains over single-metric authority. RT, CWT, WIS, iRT, local
minima, RT models, shape similarity, product ions, neutral losses, adducts, and
in-source fragments are evidence inputs. None should silently overrule the
selected peak or matrix identity by itself.

Any evidence source that changes production behavior needs explicit config or
contract, machine-readable reason/status fields, and regression tests.

## Matrix Authority Boundary

Evidence providers do not write the product matrix directly. Backfill, MS2/NL,
RT, morphology, standards, library matches, and future learned models are inputs
to evidence, hypothesis, model-selection, audit, or activation contracts.
[Writer Authority](product/productization.md#writer-authority) owns how write
authority is granted.

This document owns durable evidence semantics, not live maturity tier, active
lane, or current writer counts. Read lane/readiness from
`docs/superpowers/validation/productization_status_index.tsv`, writer scope
from `docs/superpowers/productization/status/productization_authority_manifest.json`, and
promotion semantics from `docs/agent/product-validation-contract.md`.

## Physical Peak View Equivalence

`peak_detection.physical_peak_equivalence` owns the in-memory comparison used
by Alignment claim resolution. It reports `same`, `distinct` or `unresolved`;
none is a chemical identification. Observation content IDs still identify exact
measurements, not all views of a physical peak. Comparison never changes a
selected integral or grants membership.

- Views must share an acquisition source and overlapping actual extraction m/z
  intervals. Sample attribution remains with the owning workflow.
- Same-apex views are equivalent when their acquired samples are identical
  in the common selected peak interval. Small flank differences are also allowed
  when both selected intervals retain exactly the same raw peak maximum, the
  overlap contains at least five matching acquisition times, and the integral of
  their absolute signal difference is at most 5% of the integral of their
  pointwise maximum signal in that overlap. This is an amplitude-preserving
  comparison tolerance, not an
  error estimate or a replacement integral; neither trace is rescaled.
  Differing apices still require identical measured
  overlap surrounding both apices and the physical segment owner assigning both
  queries to one lobe in their combined context. Resolved separate lobes stay
  distinct. Nearby RT, overlapping bounds, similar area or high correlation
  alone prove neither identity nor separation.
- Alignment applies role-specific occupancy to this evidence: existing Direct
  values remain; competing eligible Backfills do not create duplicate members
  or numeric cells. Unresolved comparison evidence does not establish a
  competing claim: same-apex overlapping windows can contain different ions.
  Archive rows do not occupy Matrix peaks.

## MS1 Morphology And Area Owner

For the first-principles design rationale behind smoothed-everywhere trace
processing, see [`product/signal-processing-rationale.md`](product/signal-processing-rationale.md).

- For profile-covered physical work, the canonical signal is exactly
  `max(Gaussian(raw; window_points, sigma=window_points/6, unit_sum,
  zero_pad_same) - max(AsLS_RT(rt_minutes, raw; lam=1 minute^4, p=0.01, n_iter=10), 0), 0)`. Gaussian and
  AsLS are both computed from raw independently. AsLS is fixed baseline
  estimation, not a second smoothing method. Its physical baseline is bounded
  below by zero so a negative fitted baseline cannot manufacture peak signal.
  A selected segment that reaches either acquired-trace edge is unavailable
  (`incomplete_peak_context`) and carries no numeric physical facts. Discovery
  uses its existing bounded reacquisition pass to recover missing context;
  unresolved clipped segments remain unavailable. The profile binds the formula;
  the physical question and segment universe bind the selection policy. Persisted
  evaluations retain their exact identity and are readable only under supported
  profile semantics. Unsupported profiles are rejected, not silently reinterpreted
  by the current method. No migration or old-algorithm dispatch is provided.
- Before constructing physical intervals, interior apex candidates use an RT
  envelope with minimum sigma 0.08 minutes. Supported clear valleys on the
  canonical signal preserve separated groups, each with one apex representative.
  A canonical valley hidden by the coarser envelope must stay below 50% of
  the smaller lobe for one configured Gaussian support footprint before it can
  restore a split. A short dropout notch is insufficient.
  Candidate support uses topographic prominence and the original three-point
  noise MAD over the prominence bases and, for subordinate shoulder lobes,
  the contour toward adjacent higher lobes. A candidate with prominence below four times that MAD
  is subordinate ripple only when its shoulder-connected group contains an
  independently supported apex. It cannot truncate that envelope. Clear valleys
  separate groups; entirely unsupported groups and edge maxima remain for the
  normal qualification gates. This does not discard a separate weak component
  to resolve a window's ambiguity or alter the canonical quantitative signal.
  Supported seeds also need 4% of their shoulder-connected group's maximum.
  Clear valleys separate groups before that comparison. Seed eligibility does
  not depend on a fraction of the entire acquired context maximum. The envelope only
  supplies lobe/valley evidence; boundary walks, final apex, and area use the
  canonical signal. Shoulder-connected lobes share one quantitative envelope
  and area unless a supported clear valley separates them. Persistence alone
  does not turn a shallow valley into a split. Sustained canonical clear splits retain
  their canonical valley. Multiple canonical lobes above the 4% prominence floor retain review
  evidence even when they cannot claim independent quantitative intervals.
  An unresolved shoulder carries `physical_shoulder_overlap`
  into Targeted review reasons and Untargeted provisional evidence; it cannot
  claim independent Untargeted owner quantification.
- Current physical selection uses the canonical interval through the first
  observed return to 10% of the apex residual, bounded by separating valleys.
  Only an interval reaching an acquired edge may use an observed return at
  the greater of that same 10% level and original-signal noise MAD.
  This alternative must have the same apex, be fully
  interior, and lie within the original interval. Already complete intervals
  retain their exact boundaries and values; every original candidate remains
  in the universe. A still-clipped or unmatched alternative supplies no recovery.
  This bounded tolerance addresses near-zero bridges caused by context-sensitive
  baseline estimation; it does not infer an unobserved outer boundary.
  Point queries use `baseline_peak_contains_query`; membership and integration
  share the selected bounds, including inter-scan peak tails. Group-guided
  recovery uses `unique_baseline_peak_in_context`: its center locates the bounded search
  context and need not fall inside the peak, but multiple local peaks remain
  ambiguous and nonnumeric. Ordinary peaks require an adjacent pair of original
  XIC values, after owner-baseline subtraction, reaching the half-prominence
  level inside the contiguous canonical core around the selected apex. The
  core uses whole-interval prominence; low neighbors outside it cannot certify
  an impulse. At least two such original observations must support the top.
  Missing support gives `insufficient_original_xic_support`; no adjacent pair
  gives `insufficient_original_xic_continuity`. Gaussian spread and constant
  background cannot supply independent acquired support.
  Local hit density identifies additional censored detection clusters: the
  observed hit rate must exceed both local flanks fourfold and pass a one-sided
  exact test corrected for trace length. A cluster containing an acquired run
  above baseline spanning one configured Gaussian window, with acquired scans
  outnumbering missing scans in every core window, retains its morphology
  segmentation. This continuous-core check governs censored-cluster replacement;
  it does not govern admission of an existing morphology peak. Lack of hit-density
  enrichment does not reject a morphology peak with original adjacent support
  and local contrast. A qualified censored
  cluster bypasses adjacency and intensity-shape checks, uses its observed
  trapezoid area, and carries `local_hit_density_enrichment` and
  `near_detection_limit`. Censored values are not imputed.
  A selected peak also needs local relief at least four times a local fluctuation
  scale. For each interior original point, subtract its value from the linear
  interpolation between its immediate neighbors and divide by the Euclidean
  norm of those three coefficients. Use the unscaled MAD of these differences
  over the selected interval. On a uniform RT grid the differences are
  `diff(raw, n=2) / sqrt(6)`; RT-weighted interpolation also removes linear
  trends on irregular grids. Fewer than three points or zero MAD supplies no
  additional noise rejection; original-support requirements still apply.
  Relief is the canonical apex height minus the higher of its nearest left
  and right valleys. Valleys lie between adjacent canonical maxima before
  filtering or merging, not at distant whole-envelope minima. A flat top is
  one maximum; a side without a neighboring maximum uses the existing
  interval endpoint's actual residual. Insufficient relief gives
  `insufficient_local_peak_contrast`, without numeric facts or integration.
  This uses the already prepared arrays; it does not refit the baseline or
  smoothing or interpret smoothing bias as measurement noise. Background
  offset and intensity units cannot supply local relief
  relative to fluctuations. The fixed factor 4 is a minimum contrast policy,
  not a detection significance or universally calibrated S/N claim.
  For these qualified-peak policies, these are lower bounds, not proof of chemical
  identity or quantification readiness. Discovery and bounded Backfill keep
  these local-contrast policies; they do not establish chemical absence.

- Targeted analytes (both MS1-only and CID-NL) use
  `localized_total_envelope` measurements. Known ISTDs retain
  `reference_localized_contour_peak` geometry. Both use complete-region acquired
  support rather than requiring an ideal half-prominence top. Two acquired
  positive observations are a minimal measurement condition, not a calibrated
  detection significance. Missing or imperfect top observations, local-valley
  contrast and shape scores do not by themselves withdraw supported body area.
  Physical measurements and method relations remain retained before the final
  identity interpretation. An acquired single impulse cannot manufacture a
  quantitative chromatographic body through Gaussian smoothing.

  Targeted's batch owner then distinguishes an observed chromatographic body
  from baseline curvature on constant/monotonic raw background. It uses the
  complete regions, raw support and contours, cross-sample evidence and ordered
  mass-compatible references. A retained physical measurement is not by itself
  target identity. Reference-compatible uncertainty can retain provisional area;
  a retention-incompatible interpretation is not a license to select a smaller
  residual elsewhere. The original regions remain inspectable.

  Valleys propose separate regions; only touching acquired bodies with a common
  reference interpretation can be joined. The complete canonical total is
  integrated, never an expected-RT crop. Disconnected regions are not joined.
  Shared or unresolved totals carry review explanations and do not establish
  pure-constituent area. See [Targeted Selection](product/targeted-selection.md)
  for contextual selection and the distinction between plausible and directly
  supported RT transfer. Discovery and bounded Backfill are not silently
  migrated to this Targeted interpretation policy.

  Questions cannot cross profile-bound segment universes. Changed regional
  interpretations mint current physical questions; saved answers are never
  rewritten in place.
- The canonical public profile fields are `signal_processing_method`
  (`gaussian` only) and `signal_processing_window_points` (default 9; non-bool
  odd integer 3–999). The legacy settings spelling
  `ms1_morphology_smoothing_window_points` is a one-way load-time alias only;
  equal canonical/legacy values migrate with a deprecation receipt and conflicts
  fail before RAW. `smooth_window`, `smooth_polyorder`, and `resolver_mode` are
  not aliases and cannot alter profile-covered facts.
- Raw trapezoid area and standalone AsLS baseline-corrected area may remain
  compatibility or audit evidence for explicitly excluded legacy consumers.
  Covered Owner Backfill work has no raw/legacy compatibility fallback: apex,
  boundaries, height, area, scan support, and relative shape share one profile-
  bound canonical signal and fact identity.
- Canonical Gaussian morphology supplies boundary and MS1 shape/pattern
  evidence. It is not identity authority by itself: paired RT, target window,
  product/NL/MS2 acquisition opportunity, method-specific CID-NL evidence, and
  CrossSample identity gates still decide whether a candidate is accepted,
  not counted, ambiguous, or excluded; see the [evidence spine](product/evidence-spine.md)
  and [shared processing kernel](product/shared-processing-kernel.md).
- Canonical Gaussian(raw)-nonnegative-AsLS(raw) positive area is the selected product
  direction for the bounded Owner Backfill migration, with profile/fact/value
  provenance. Numeric area differences from legacy integration are accepted
  quantitative expected differences; parity must not be manufactured by a
  second calculation. The default 9-point smoothing is scan-rate sensitive, and non-default
  windows must be treated as method parameters. Production-ready claims across
  batches must include a method-appropriate review of raw-vs-Gaussian area
  ratios and estimated smoothing duration, using retained numerical evidence.
  No particular retired candidate-table tool is required. This review is
  observational only; it must not mutate selected boundaries, selected area,
  confidence, presence, workbook output, or matrix values.
- Formula-driven apex, boundary, height, scan-support, and relative-shape
  differences are qualitative. They require bounded current-head real-RAW and
  independent manual-EIC evidence before production activation. Selected
  hypothesis, duplicate/ambiguity handling, SUPPORT/HOLD/REJECT/unavailable,
  Backfill status, activation, row/cell identity, and write/blank decisions are
  hard-zero expected differences. A qualitative mismatch is HOLD, never a
  reason to restore legacy computation to force parity.
- For accepted candidates, `ChromPeakSegment` is the active chromatographic
  boundary owner when a canonical morphology segment contains the selected
  apex. It can replace an over-wide legacy resolver interval with an explicit
  segment boundary and canonical positive AsLS residual area.
- `selected_full_envelope` remains a diagnostic/fallback boundary source. It may
  promote only when no segment-native boundary is available; its
  split/neighbor-apex, stronger context-apex, blank-like, edge/tail, width,
  scan-count, and low-S/N guards remain fail-closed review or externalization
  paths.

## RT And Identity

- Cross-sample RT or m/z dispersion alone does not establish separate isomers;
  inspect within-sample peaks and method-specific MS2/NL evidence. Lack of
  distinct peaks within a sample does not prove chemical identity either.
- MS1 shape alone must not dismiss a shoulder or weak peak supported by
  method-specific MS2/NL evidence. Such support does not authorize quantitation
  without reliable bounds from the physical owner.
- Treat RT as contextual evidence, not a single hard identity veto.
- Large, reproducible, or unmodeled RT shifts may trigger ambiguity or
  confidence demotion, especially when inconsistent with biological ISTD
  transfer evidence.
- RT alone must not prove analyte absence or override co-eluting,
  candidate-aligned MS1/MS2 evidence unless an explicit hard RT exclusion policy
  exists.

## Untargeted Backfill Evidence Gate

- Owner backfill may enumerate rescued cells for review, plotting, and
  diagnostics, but high-risk rescue promotion must not rely on broad-window
  membership, `owner_backfill` trace labels, local apex presence, or scan support
  alone.
- Untargeted Owner Backfill is an independent first consumer of the shared
  physical signal owner; it does not require a Targeted-first migration or use
  Targeted output parity as its acceptance baseline. Current normal Untargeted
  Discovery, Alignment owner-build, and Owner Backfill routes consume the
  canonical profile-bound physical owner. Targeted remains a separately bounded
  migration, the automatic GUI gallery is parked, and retained Standard Peak
  diagnostics must not redefine this Backfill gate.
- Broad Backfill candidate universes are audit/review surfaces, not writer
  pools. Quality blockers, quality explanations, round-trip reintegration
  checks, ISTD comparisons, review packets, and lockbox membership may raise
  questions or provide evidence, but they do not grant ProductWriter authority
  without the activation/export chain described above.
- Treat `family` as a review/search container, not the promotion unit. The
  same-peak evidence unit is the detected anchor peak/seed group, and the
  product decision unit is the individual rescued cell.
- Legacy family/window MS1 shape support is review context. Product MS1 pattern
  support for a rescued cell requires a typed same-peak reason such as
  `family_ms1_overlay_anchor_peak_own_max_shape_supported`, which means the
  rescued cell was compared to the nearest detected anchor peak after scaling
  each trace to its own local max. The sidecar evidence level must remain
  `trace_constellation`; `sample_constellation` and
  `sample_boundary_constellation` are context unless promoted under a separate
  contract. Every projected backfill evidence sidecar row that is used as
  product support, including MS1, QC, RT-drift, and optional Candidate MS2 rows,
  must also be product-authorized
  (`diagnostic_only=FALSE` plus `product_authority_status=product_authorized`,
  `product_authority_scope=feature_family_sample`, and a non-empty
  `product_authority_source`) before projection can feed product promotion.
  Projection writes product-authority provenance into the rescued-cell
  `backfill_*_product_authority_*` fields, and downstream backfill promotion
  treats naked supportive `backfill_*` fields without that provenance as
  review-only/stale context.
  The current MS1 own-max authority floor is strict: observed anchor own-max
  shape similarity must be above `0.5` by default, and an allowlist threshold can
  tighten but not lower that floor without a new approved contract. This avoids
  treating a different peak inside the same broad family window as backfill
  evidence. Review-only MS2 context may be displayed for interpretation, but it
  is not required for backfill promotion. Duplicate product sidecar rows for the
  same `(feature_family_id, sample_stem)` are not ordered preferences; they are
  an ambiguous evidence state and must fail closed.
- Product-authorized MS1 own-max rows must also prove artifact provenance before
  projection: the recorded `family_ms1_overlay_trace_data_json` must resolve to
  a readable overlay JSON, declare the same top-level `family_id`, contain exactly
  one matching `sample_stem` trace with usable RAW RT/intensity vectors and
  own-max similarity, match the reviewed allowlist path/hash, and record the
  overlay JSON sha256 in the authorized sidecar. Missing, stale, mismatched,
  duplicate, out-of-bundle, or vectorless overlay artifacts remain review-only.
- Product-authorized Candidate MS2 rows must prove source-row provenance before
  projection: the reviewed allowlist must match the source row's canonical
  SHA256 plus expected `candidate_ms2_pattern_status`,
  `candidate_ms2_evidence_level`, and `ms2_alignment_source`. Only
  `supportive` or `partial_support` rows at `sample_candidate_aligned` or
  `sample_boundary_aligned` can become product-authorized. `not_observed` rows
  may document DDA opportunity or non-dispositive missing-NL context. Candidate
  MS2 is auxiliary context for rescued/backfilled cells because those cells
  would already be detected if they had the required NL tag themselves.
- A rescued cell can support high/weak backfill-dependent matrix promotion only
  when the typed backfill projection explains the required evidence groups:
  preferred-RT proximity or compatible drift/iRT correction; same-peak anchor
  MS1 own-max pattern support; and a positive selected matrix value for the
  rescued cell. QC pattern support, Candidate MS2 rows, and DDA-missing-NL
  context may remain visible as review context or supporting provenance, but
  they do not replace anchor MS1 same-peak evidence and are not required when
  MS1 same-peak evidence is product-authorized.
- Wrong-peak or unresolved hypothesis blockers remain fail-closed for rescued
  cells. Legacy Standard Peak family duplicate or multi-claim labels are
  retained diagnostic context only; they cannot weaken current owner-derived
  identity, ambiguity, primary-loser, mode-split, cross-mode-rescue, or
  `consolidation_no_go` blockers. Keep relevant provenance in audit fields, but
  require a current owner-valid production rule before any such row can write.
- Scan support and trace quality remain assessability/coverage evidence. They
  may block low-quality rescue, but they are not independent identity evidence.
- `machine_shift_aware_standard_peak_gate` and
  `standard_peak_gate_supported` are historical/offline diagnostic labels, not
  current product-authority sources. T5 retired their authority-bundle and
  source-Matrix publication consumers. Manual Standard Peak examples remain
  calibration oracle rows rather than a product whitelist, and current-file
  hashes prove packet self-consistency only. Any future use in a product rule
  requires a new owner-bound expected-diff and promotion contract; retained
  labels cannot revive the retired writer path.
- MS1/QC pattern conflict, wrong-peak hypothesis evidence, or unexplained RT
  outside the preferred RT band keeps the rescue review-only even if the peak is
  quantifiable inside the broader owner-backfill query window. Candidate-aligned
  MS2 conflict is review context unless it is tied to a wrong-peak/hypothesis
  blocker; it is not an automatic veto of MS1 same-peak backfill support.
- Pre-backfill consolidation remains a separate high-risk identity-row problem.
  Do not use the backfill evidence gate as proof that a 180 sec consolidation
  row is chemically valid.

## ISTD Evidence

- ISTDs with candidate-aligned required CID-NL support and finite positive
  quantitation remain confirmed despite RT drift. RT-prior disagreement alone
  creates neither an identity veto nor a review action. Unresolved morphology
  or overlap concerns can still require measurement review.
- Missing required NL initially leaves an ISTD unconfirmed, even when expected
  present and MS1-coherent. The existing cross-sample Backfill route can confirm
  its identity using a validated accepted receipt. Exclusions, unresolved
  identity conflicts, and unavailable quantitation still block counting.
  Successful confirmation resolves the missing-direct-NL review; the recovery
  route remains support provenance, not a new warning by itself.
- Only direct confirmed observations seed the frozen support group. Recovered
  ISTDs do not become direct SUPPORT votes.
- Paired ISTD RT remains matrix-specific localization evidence. RT prior and
  paired ISTD agreement are not two independent identity observations.

## Targeted Analyte / STD Pair Evidence

- Candidate-aligned CID-NL support takes precedence over RT hints and trace
  quality. A configured RT window bounds acquisition context but does not veto
  an otherwise eligible retained peak solely because its apex lies outside the
  hint. This does not authorize unbounded RAW acquisition.
- Paired ISTD RT guides candidate localization. RT distance and legacy
  confidence labels remain source context, not identity vetoes or standalone
  review actions. A supported and quantifiable peak can still require review
  for an unresolved typed measurement-quality concern.
- Analyte `NL_FAIL` and `NO_MS2` retain measured quantities but remain
  not-counted. Paired RT, area ratio, and an accepted observation-only Backfill
  do not confer analyte identity confirmation.
- DDA triggering without the required product/NL tag is acquisition opportunity,
  not identity support. Method evidence is attributed against the full retained
  physical candidate universe; overlapping or boundary evidence remains
  set-level rather than supporting an individual peak.
- The single-pass application owns the completed result. External MS1-shape
  support and approval files do not authorize a different selection or count.

## Missing MS2 / Product / Neutral-Loss Evidence

- Treat missing DDA MS2/product/NL evidence as `not_observed` by default.
- Use missing evidence as negative evidence only when acquisition opportunity,
  local sensitivity, precursor selection or scan coverage, and comparable
  positive controls show that the evidence should have been observable.
- The presence of a DDA trigger alone does not establish those conditions.
  A scan-free peak must not gain chemical ranking priority over a reference peak
  whose acquired MS2 lacks qualified target support. Only positive evidence
  assigned to the same target and physical peak earns that priority.

## Product Ions, Neutral Losses, Adducts, And Fragments

- Product ions, neutral losses, adducts, and in-source fragments are candidate
  evidence only when co-eluting, boundary-aligned, and assigned to the same
  precursor or candidate.
- Repeated DDA/NL triggers inside one selected Gaussian15 MS1 peak group are one
  chromatographic support event with multiple scans. They may improve trace
  diagnostics, but must not be treated as multiple independent MS1 peak supports.
- Shared class fragments or common neutral losses support class or substructure
  confidence, not analyte-specific proof by themselves.

## Source Roles

Keep source roles explicit when manifests, method docs, or target registries
declare them:

- SDO/LEK are dedicated clean standards and are expected only in their own
  standard samples.
- MixSTDs are non-biological clean standards containing ISTDs and external
  standards.
- Non-ISTD MixSTD targets are external standards; do not use them as required
  biological-sample anchors unless explicitly spiked or validated.

Clean standards can describe instrument behavior and support authentic reference
checks, RT-aware preview, and audit or library work. They cannot alone justify
production correction of biological matrices.

Calibration-derived production RT/area/scoring/matrix gate changes require
current-code biological transfer evidence, row-level coverage or exclusion
policy, and machine-readable GO/NO-GO blockers.

## Audit Versus Production

- Keep audit and production gates separate.
- Extrapolated, sparse, missing, or low-coverage evidence stays review-only until
  an explicit production policy exists.
- When manual EIC/MS2 review contradicts a diagnostic label, investigate whether
  the shared evidence rule, diagnostic wording, or reviewed row is wrong.
- Fix the shared rule and add regression tests when diagnostic logic is wrong;
  do not encode one-off sample or target exceptions as the primary solution.
- Canonical region audit projects the selected physical segment. It does not
  propose merges or grant selected-peak, selected-area, or writer authority.

## Targeted And Untargeted Boundary

- Targeted and untargeted workflows may use different priors and reporting, but
  shared evidence concepts such as traces, candidates, boundaries, regions,
  integration audit, and product/NL evidence should use common low-level models
  when this prevents schema drift.
- Do not force shared concrete implementations before the semantics match.
- Targeted outputs may serve as benchmarks, validation evidence, or shared
  low-level evidence, but must not leak target labels or targeted pass/fail logic
  into untargeted production matrix identity unless an approved contract says so.
- Lessons learned from targeted validation should feed back into untargeted
  evidence calibration at the rule level: candidate completeness, anchor-aware
  RT context, candidate-aligned product/NL evidence, morphology review semantics,
  and legacy-score retirement. Do not copy targeted labels or per-row fixes into
  untargeted identity.
- Untargeted behavior should be optimized against known table-level outcomes as
  a global consistency problem, not a patch queue. A targeted-backed correction
  must name the shared evidence rule it changes, the known cases it improves,
  and the guard cases that prove it did not merely overfit one target/sample.
- In untargeted shared-identity activation, RAW-overlay or raw-mode grouping is a
  hypothesis source, not a permanent veto. It must not change product labels by
  itself, but it also must not mask a named wrong-peak conflict or a complete
  positive evidence chain with machine-observed MS1 shape/pattern and
  candidate-aligned MS2/NL support.
- `family_projection` rows are unresolved projection rows, not canonical
  identity proof. A canonical-only matrix surface may exclude them and report
  excluded row/cell counts, but that exclusion remains incomplete scope and must
  not pass a complete row-identity gate. The full legacy family matrix is
  canonical only after projection rows are replaced by explicit PeakHypothesis
  assignments or a reviewed product contract says those rows are out of scope.
