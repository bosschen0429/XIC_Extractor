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

For Backfill and other explicit matrix activations, current write authority is
granted only through the authority manifest, status index, expected diff, and
promotion contract. In current terminology, a `ProductionAcceptanceManifest`
can grant per-cell write authority only when the later activation path binds it
to expected diff, provenance, and the current productization authority scope.
`QuantMatrixVersion` / default activation outputs are the writer surface. A
candidate row, `write_ready` label, quality blocker, sidecar, replay, or review
packet outside that activation chain is not matrix authority by itself.

This document owns durable evidence semantics, not live maturity tier, active
lane, or current writer counts. Read lane/readiness from
`docs/superpowers/validation/productization_status_index.tsv`, writer scope
from `docs/superpowers/schemas/productization_authority_manifest.json`, and
promotion semantics from `docs/agent/product-validation-contract.md`.

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
  evaluations replay their recorded policy and retain their exact identity.
- Before constructing physical intervals, interior apex candidates use an RT
  envelope with minimum sigma 0.08 minutes. Supported clear valleys on the
  canonical signal preserve separated groups, each with one apex representative.
  A canonical valley hidden by the coarser envelope must stay below 30% of
  the smaller lobe for one configured Gaussian support footprint before it can
  restore a split. A short dropout notch is insufficient.
  Candidate support uses topographic prominence and the original three-point
  noise MAD over the prominence bases and, for subordinate shoulder lobes,
  the contour toward adjacent higher lobes. A candidate with prominence below twice that MAD
  is subordinate ripple only when its shoulder-connected group contains an
  independently supported apex. It cannot truncate that envelope. Clear valleys
  separate groups; entirely unsupported groups and edge maxima remain for the
  normal qualification gates. This does not discard a separate weak component
  to resolve a window's ambiguity or alter the canonical quantitative signal.
  Supported seeds also need 4% of their shoulder-connected group's maximum.
  Clear valleys separate groups before that comparison. Seed eligibility does
  not depend on a fraction of the entire acquired context maximum. The envelope only
  supplies lobe/valley evidence; boundary walks, final apex, and area use the
  canonical signal. Shoulder-connected lobes share one complete envelope and
  area unless independently supported coarse lobes have a persistent shallow
  valley across one configured smoothing footprint. Those lobes retain separate
  intervals at the observed valley. Multiple canonical lobes above the 4% prominence floor retain review
  evidence even when they cannot claim independent quantitative intervals.
  An unresolved shoulder carries `physical_shoulder_overlap`
  into Targeted review reasons and Untargeted provisional evidence; it cannot
  claim independent Untargeted owner quantification.
- Current physical selection then uses the complete positive peak interval
  through the first baseline-return samples, bounded by adjacent valleys.
  Only an interval reaching an acquired edge may use an observed return at
  the greater of 0.1% of its apex residual and original-signal noise MAD.
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
  ambiguous and nonnumeric. Both current policies require at least two original
  XIC values strictly greater than the owner baseline within the uniquely selected, complete
  interval. Fewer points give `insufficient_original_xic_support` with no numeric
  physical facts. Those original values must also include at least one adjacent
  pair strictly greater than that baseline; otherwise the answer is unavailable with
  `insufficient_original_xic_continuity`. Gaussian spread cannot supply
  independent sampling support or join isolated impulses into acquired
  continuity, and a constant background cannot supply that support. Fresh
  answers also reject `fragmented_original_xic_support` when
  the selected original interval has at least three maximal positive runs
  separated by zeros **and** severe original jaggedness. Jaggedness is the sum
  of absolute second differences divided by the original intensity range and
  selected point count; severe means greater than 0.5, the existing
  `noise_shape_severity` hard level. This narrow conjunction belongs to the
  versioned physical policy; changing that threshold requires policy review
  and versioning. One interruption, smooth low-level fragments around a main
  peak, or jaggedness without repeated zero interruptions does not trigger it.
  Baseline crossings are not missing acquired samples and do not count as zero
  interruptions for this fragmentation check.
  A selected peak also needs local relief at least twice a local fluctuation
  scale. For each interior original point, subtract its value from the linear
  interpolation between its immediate neighbors and divide by the Euclidean
  norm of those three coefficients. Use the unscaled MAD of these differences
  over the selected interval. On a uniform RT grid the differences are
  `diff(raw, n=2) / sqrt(6)`; RT-weighted interpolation also removes linear
  trends on irregular grids. Fewer than three points or zero MAD supplies no
  additional noise rejection; original-support requirements still apply.
  Relief is the canonical apex
  height minus the higher of the minima on its two sides, including the apex
  and the recorded interval endpoints. Insufficient relief gives
  `insufficient_local_peak_contrast`, without numeric facts or integration.
  This uses the already prepared arrays; it does not refit the baseline or
  smoothing or interpret smoothing bias as measurement noise. Background
  offset and intensity units cannot supply local relief
  relative to fluctuations. The fixed factor 2 is a minimum contrast policy,
  not a detection significance or universally calibrated S/N claim.
  These checks are necessary lower bounds, not sufficient proof of chemical
  identity or quantification readiness. Qualification neither
  changes integration nor removes weak segments to resolve ambiguity. Fresh
  policies use segment-universe-v3; questions cannot cross boundary-policy
  families. Recorded point/window receipts retain above-zero acquired
  support and fragmentation checks, without baseline-relative support or local
  contrast qualification. Recorded point/window receipts retain the adjacency-only
  requirement and edge recovery with segment-universe, without the fragmented
  support check. Recorded point/window receipts retain exact-zero returns and
  the adjacency requirement with segment-universe. Recorded point/window
  receipts retain the minimum-two-positive-points rule without the adjacency
  requirement. Recorded point/window receipts replay without either
  original-support rule. Legacy
  `closed_segment_contains_query` receipts retain their original 2%-height
  bounds. Explicit fresh evaluation uses the current policy and new answer
  identity; replay never rewrites a saved answer.
- The canonical public profile fields are `signal_processing_method`
  (`gaussian` only) and `signal_processing_window_points` (default 15; non-bool
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
- Gaussian15 morphology improves boundary readability and MS1 shape/pattern
  evidence. It is not identity authority by itself: paired RT, target window,
  product/NL/MS2 acquisition opportunity, and untargeted identity-coherence
  guards still decide whether a candidate is accepted, not counted, ambiguous,
  or excluded.
- Canonical Gaussian(raw)-nonnegative-AsLS(raw) positive area is the selected product
  direction for the bounded Owner Backfill migration, with profile/fact/value
  provenance. Numeric area differences from legacy integration are accepted
  quantitative expected differences; parity must not be manufactured by a
  second calculation. The default 15-point smoothing is scan-rate sensitive, and non-default
  windows must be treated as method parameters. Production-ready claims across
  batches must include a `gaussian15_area_pressure_audit.py` review of
  raw-vs-Gaussian area ratios and estimated smoothing duration. This audit is
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
  boundary owner when a Gaussian15 morphology segment contains the selected
  apex. It can replace an over-wide legacy resolver interval with an explicit
  segment boundary and Gaussian15 positive AsLS residual area.
- `selected_full_envelope` remains a diagnostic/fallback boundary source. It may
  promote only when no segment-native boundary is available; its
  split/neighbor-apex, stronger context-apex, blank-like, edge/tail, width,
  scan-count, and low-S/N guards remain fail-closed review or externalization
  paths.

## RT And Identity

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

- For ISTDs, when a coherent evidence chain exists, such as aligned MS1 peak
  shape/area plus candidate-aligned NL/product/MS2/trace evidence, do not
  downgrade only because of RT prior, RT window, or centrality concerns.
- Legacy morphology, shape, or trace-quality labels must not become hard
  product vetoes for ISTDs when finite positive RT/area, candidate-aligned
  product/NL/MS2 support, and role-aware RT context support the same selected
  peak. Keep the label visible as review evidence instead.
- If this changes, add an explicit contract doc and regression tests.
- When biological samples receive ISTDs, those ISTDs are the primary transfer
  evidence for real-matrix RT/response behavior because they share the sample
  matrix, ion suppression/enhancement, RT drift, and sample-prep context.
- RT prior support and paired ISTD RT support are one role-aware RT evidence
  group when they describe the same selected candidate. They may remain visible
  as separate typed facts, but product projection must not count
  `rt_prior_close`, `paired_istd_aligned`, and `paired_istd_rt_close` as three
  independent physical observations.

## Targeted Analyte / STD Pair Evidence

- For targeted analytes or STDs with an ISTD pair, anchor-guided selection must
  prefer a complete candidate peak near the ISTD-informed or product/NL-informed
  reference over a random small nearest peak.
- In paired targeted mode, the paired ISTD RT is the primary biological-matrix
  transfer anchor. A target-specific NL/product anchor may support the paired
  analyte/STD candidate only when it is close to the paired ISTD RT. A distant
  target-specific NL/product anchor must not move the extraction window away
  from the ISTD-supported RT region by itself, because that silently converts a
  random DDA/NL event into peak identity authority.
- When no learned analyte-vs-ISTD delta RT is available, a paired ISTD RT that
  falls inside the target's configured RT window is the fallback target reference
  for candidate search. This keeps Gaussian15/ChromPeakSegment MS1 candidates in
  scope before any MS2/NL-triggered window narrowing is allowed to influence
  selection.
- ISTD-centered fallback opens the candidate search/review window only. It must
  not hard-backfill a missing paired STD/analyte into a counted detection; the
  selected row still needs the analyte/STD product policy to project as counted.
- Legacy morphology, shape, trace-quality, or `VERY_LOW` labels are review
  evidence, not direct product authority, when the selected analyte peak has
  finite positive RT/area, candidate-aligned product/NL/MS2 support, targeted
  anchor selection context, and no anchor, RT, or NL/product conflict.
- Analyte `NL_FAIL` and `NO_MS2` remain not-counted unless the active paired
  analyte rescue policy has all required support. Pair evidence must not
  silently convert missing or failed product evidence into a counted detection.
- The current targeted MS1 own-max opt-in verdict is limited to the headless
  explicit `limited_5hmdc_5medc` support-TSV workflow. It may project
  reviewed `5-hmdC`/`5-medC` `NL_FAIL` candidates to `detected_flagged` only
  under the support-TSV key-set and expected-diff gate. Default automatic
  rescue, GUI rescue, and broader-target rescue remain blocked.
- DDA MS2 trigger without the key target NL/product tag is acquisition
  opportunity evidence, not analyte identity support. When the key NL/product
  tag is missing or failed, analyte rescue must rely on coherent MS1 peak
  evidence plus paired ISTD RT and paired area-ratio support. Paired RT alone is
  review evidence, not counted-detection authority.
- When a Gaussian15/ChromPeakSegment MS1 peak group is available, targeted
  MS2/NL evidence must be scoped to that selected MS1 peak group before it can
  support the candidate. Strict NL scans outside the selected Gaussian15 MS1
  group are diagnostic/conflict context for another chromatographic event, not
  active support for the current candidate.
- Active paired area-ratio support is a run-level typed projection from
  `xic_extractor.extraction.paired_area_ratio_projection`. Its active reference
  range is leave-one-sample-out median +/- 3 scaled MAD target area / paired
  ISTD area from counted target detections only, using the Gaussian15 morphology
  area when available. The old leave-one-out min/max range is retained as a
  reference/debug projection only. Reported but not-counted target rows do not
  seed the active range.
- In paired biological-matrix analyte review, candidates more than 1.0 min from
  the paired ISTD RT are treated as paired-RT mismatches unless strict
  candidate-aligned NL/product evidence justifies review. Without strict
  NL/product support, that mismatch is a not-counted policy cap.
- Avoid adding more fixed RT-delta constants as product truth without current
  biological-matrix validation. If another distance threshold becomes production
  policy, it needs its own contract, artifact, and regression tests.
- Treat legacy scoring thresholds as calibration-sensitive heuristics. They may
  rank or annotate candidates, but they must not by themselves create a hard
  targeted detection or absence decision.

## Missing MS2 / Product / Neutral-Loss Evidence

- Treat missing DDA MS2/product/NL evidence as `not_observed` by default.
- Use missing evidence as negative evidence only when acquisition opportunity,
  local sensitivity, precursor selection or scan coverage, and comparable
  positive controls show that the evidence should have been observable.

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
- Region-selection verdicts other than the existing adjacent-WIS
  `safe_merge_eligible` class are not product authority. `wider_boundary_preferred`,
  `neighbor_apex_preferred`, and `split_supported` remain
  `behavior_change_required` until a future oracle and public behavior addendum
  promote or retire them.

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
