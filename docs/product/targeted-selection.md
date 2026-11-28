# Targeted Selection

Targeted selection connects observed evidence to the Targets requested for a
batch: it constrains the shared lifecycle with an exact target list, binds
finalized groups to fixed Target rows, and closes inclusion, counted detection
and review reasons. A target-list entry is a search request, not evidence that
the Target was observed. For operation and result reading, start with the
[Targeted guide](../user/targeted.md#看結果).

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| What does a Target ask for? | MS1 only, CID-NL, Delta Mass, or both, set by two target columns. CID-NL alone ranks by its assessment; with Delta Mass, every declared condition must match the same peak. | [Target method](#target-method) |
| Which peaks are candidates? | Every physical region over the sample's complete acquisition RT range, retained until all samples are acquired. | [Candidate population](#candidate-population) |
| How is one candidate chosen? | Individually attributed CID-NL support from a recorded precursor ranks first; otherwise physical and RT/ISTD evidence localize. | [Candidate selection](#candidate-selection) |
| Where is a Target expected? | At its expected RT, shifted by the current sample's credible paired ISTD or available drift. Expected RT is an identity prior, not a search window. | [RT reference](#rt-reference) |
| When does an analyte without its own NL count? | When its paired ISTD is counted in the same sample and its MS1 peak sits at the paired position. A peak outside that position is ND. | [Paired-ISTD identity](#paired-istd-identity) |
| How do groups become rows? | A unique group binds the Target row; competing groups for one sample stay unresolved. | [Row binding and cell decision](#row-binding-and-cell-decision) |
| What does the workbook show? | The cell decision's RT, area and bounds even when not counted, with `依據` and `疑點`. | [Output and review](#output-and-review) |

## Contract

### Target method

A Target's specificity follows from `cid_nl_loss_da` and `delta_mass_mz`;
there are no named methods.

- **MS1 only.** Both columns blank. Intake uses the 20 ppm target mass
  tolerance and leaves neutral loss unconfigured. Missing MS2 is not chemical
  identity evidence.
- **CID-NL.** `cid_nl_loss_da` is a neutral loss in Da below the target m/z,
  searched under the shared CID-NL mass policy; 116.0474 (dR), 132.0423 (R)
  and 146.0579 (MeR) are suggestions, not names. It uses the same quadrature
  precursor/product m/z window as Discovery; `nl_ppm_max` and the CID-NL
  support tolerance are per-ion ppm inputs to it. The reported observed-loss
  ppm error is descriptive.
- **Delta Mass.** `delta_mass_mz` lists signed companion-ion m/z offsets
  relative to the quantitation ion (`base`) at zero, not neutral-mass
  differences. Users do not choose a quantitation position or arrange a base
  ion. Each Target keeps its own ion names, ratio ranges and shape comparison;
  m/z tolerance and co-elution/shape thresholds are shared by the list.
  Co-elution, ratios and shape reference the quantitation ion. Ratios are
  annotations, not admission gates. Position XIC windows must not overlap.
  Settings normalize the lowest mass internally.
- **Composition.** CID-NL alone keeps the Targeted CID-NL assessment
  (ranking support). With Delta Mass, every declared condition must match the
  same peak; there is no any-of combination. Each clause answers on the **same
  sample-local physical quantitation candidate**, and evidence from other peaks
  or samples cannot satisfy AND. OR does not require unacquired clauses to
  pass. HCD-PI is not implemented.

Each condition answers `matched`, `not_matched` or `not_acquired` (no target
MS2 inside that candidate). Missing MS2 is not a failed chemical measurement
but cannot satisfy a CID-NL condition. Only method-admitted candidates enter
selection; an unmet method never falls back to MS1 only. Custom compositions do
not invent CID-NL receipts or receive CID-NL Backfill. Every candidate-level
condition answer stays in the output.

XIC windows establish bounded mass support, not measured centroids or zero mass
error. Configurability and synthetic regression coverage do not establish
chemical identity or readiness (`diagnostic_only`).

### Candidate population

Targeted keeps the complete acquired population until batch comparison.

- Exact-mass MS1 acquisition covers the complete acquisition RT bounds,
  including Targets without MS2. No expected-RT window, DDA anchor crop or
  reacquisition expands or truncates the candidate population. Acquisition
  enumerates the shared segment universe for the configured signal profile.
- RAW acquisition and signal preparation run once per requested trace; batch
  interpretation reuses those arrays and the retained precursor/product and
  scan-opportunity facts. It neither reopens RAW nor refits the baseline.
- Analyte selection waits until all acquired samples are available. Known ISTD
  anchoring stays sample-local; batch interpretation does not replace an
  established anchor.
- The full-acquisition region universe stays complete even when background
  lobes exceed the generic capture budget, so background complexity cannot
  empty the candidate set. Discovery and Backfill keep their own bounded
  enumeration.
- The MS2 window carries spectra only for events whose recorded or
  isolation-inferred precursor can reach a declared CID-NL Target's precursor
  tolerance; every other MS2 event in range still counts toward the search
  receipt's scanned events.
- Rejected interpretations keep their curves, region geometry, method products
  and physical evaluations for inspection.

#### Measurable regions

Measurement availability, identity plausibility and integration uncertainty
are separate decisions.

- A complete region with acquired body support stays measurable despite a
  ragged, flat or split top. A smoothed single impulse is not chromatographic
  support. Constant or monotonic raw background cannot become a body because
  baseline subtraction produces an arch.
- Valleys stay regional evidence even above baseline. The Targeted proposal
  policy uses a 15% sustained valley drop and the profile's minimum boundary
  duration. Touching lobes are not joined merely because they share a target
  reference.
- A CID-NL event on a valley is set-level evidence: both lobes stay
  candidates and neither gets individual support from it.
- A valley-bounded region is not replaced by a sum of its neighbors, and
  selection does not search combinations of regions or change their CID-NL
  universe. A region the signal owner did not split stays one measurement,
  poor top included.
- Integration is the physical owner's complete region in counts·seconds, not a
  reference-window crop; RT, bounds and width are in minutes. Top quality
  describes risk rather than erasing area. Neither a shared total nor a
  positive NL establishes pure constituent area. Identity ranking does not
  alter bounds or area; full-trace baseline estimation can differ from a
  cropped trace. [Signal processing](signal-processing.md) owns the signal and
  units.
- Analytes use `localized_total_envelope` measurements. Known ISTDs keep
  reference-first localization and `reference_localized_contour_peak`
  geometry, with support assessed on the body, not a half-prominence core.
  Discovery and Backfill keep their own qualification policies.
- When identity selection chooses no component, the physical answer keeps the
  acquired context and segment universe with no admissible selection; capture
  does not substitute a broad tail. The physical owner can withdraw an
  unavailable measurement but does not substitute another candidate.
- Capture keeps the selected physical evaluation through finalization; it runs
  no second boundary resolver or integration.

A measured region is not guaranteed a value: the
[Targeted quantitative-support policy](decision-policy.md#targeted-quantitative-support)
can withhold an unreliable low-S/N, sparsely acquired value while keeping its
trace and diagnostic integral, without changing selection, boundaries or
evidence attribution.

### Candidate selection

Selection consumes the CID-NL method relation computed over the complete
candidate universe in the Targeted search scope, not a candidate-local best MS2
product.

- **Ranking priority.** Only positive, individually attributed CID-NL support
  gets chemical priority. No MS2 and MS2 without qualified support are both
  unconfirmed; neither outranks the other. Without such support, physical and
  RT/ISTD evidence decide localization. Positive CID-NL is evidence, not
  permission to accept an incompatible chromatographic interpretation.
- **Nearby candidate.** For an analyte without an ISTD pair, a complete nearby
  candidate is not displaced only by denser MS2 support on a distant isomer.
  This is not a hard RT exclusion and grants no identity from RT.
- **Scoring.** Comparison uses whole-body acquired support, normalized contours,
  half-height spans, cross-sample positions and mass-compatible reference
  order. Scores describe the chosen measurement; review-blocker count cannot
  select a cleaner substitute, and scoring does not select again.
- **Retained alternatives.** Peer proposals and final selection keep weaker
  reference-supported bodies beside prominent alternatives; a duplicate sample
  cannot turn an abundance filter into an identity veto. Supported sample
  displacement is a ranking prior, not a hard RT window.
- **Available peaks.** Retention-compatible, recorded-precursor-supported,
  quantifiable peaks stay available despite a poor top or uncertain moderate
  RT displacement.
- **Shared regions.** Target count does not force peak count. If different
  Targets can only share one ion region, both keep provisional values with
  linked review reasons and no independent counted assignment. Duplicate assays
  with the same RT get a shared-quantitation warning, not an identity conflict.

Final selection, including Backfill, selects the matching retained MS2 answer
with the peak. A no-write cell carries no selected-peak positive NL claim, and
publication never falls back to a whole-run NL observation. The handoff keeps
every candidate's physical evaluation, method products, search receipt and
relation answer, bound to exact sample/event/peak provenance. Only the selected
candidate enters the Target row; the method assessment records the complete
candidate and event universe. Targeted groups selected hypotheses through the
shared CrossSample owner; grouping keeps sample-local identities and values,
does not reselect peaks, and applies no cohort RT review.

#### CID-NL support for a Target

A Target peak's CID-NL support comes from the relation answer, attributed
per candidate.

- **Attribution.** Strictly interior, uniquely attributable evidence supports
  an individual peak; boundary and overlapping relations stay set-level. Each
  retained candidate gets one target-scoped MS2 answer; ranking, support, trace
  diagnostics and published NL labels all read it. A neighbouring precursor's
  matching loss cannot supply trace strength or a positive label. If
  contextual admission changes the population, the relation is recomputed from
  the retained events.
- **Recorded precursor.** Own CID-NL support for a named Target requires a
  compatible recorded scan precursor or vendor monoisotopic reference. A
  parent-MS1 ion found as product + loss shows mass/isolation compatibility,
  not which coisolated ion fragmented; it stays in the evidence but cannot
  select or identify a Target peak. The same requirement applies in selection
  and in formal support binding; Discovery keeps its independent-mass scope.
- **Vendor monoisotopic route.** A recorded vendor monoisotopic reference
  supports CID-NL even outside the nominal isolation window when the selected
  ion is inside the window, has charge 1, and matches a positive integer
  carbon-isotope offset within the shared 10 ppm precursor policy. Missing
  geometry or charge, an unrelated offset, or product + loss inference do not
  qualify. Isolation membership stays `outside_nominal`; this does not prove
  exclusive parentage, and product/NL mass, intensity, attribution and
  peak-local checks still apply.
- **Charge.** NL matching excludes scans whose recorded charge is not 1;
  unavailable charge stays eligible. `NO_MS2` means no method-eligible
  precursor scan; with only rejected-charge scans, `valid_ms2_scan_count`
  counts them and `matched_scan_count` is zero.

#### ISTD identity

ISTD selection compares measured bodies before giving chemistry priority.

- An unconfirmed body can challenge the reference-nearest chemically supported
  body when it meets the anchor-selection prominence ratio, judged on whole-body
  support rather than global MS1 intensity.
- Each body's bounds give its nearest and farthest distance from the reference
  RT. A body wholly farther away than another does not compete on chemical
  support alone. Overlapping ranges keep chemical preference, then reference
  apex proximity, then body support.
- This is neither a fixed RT gate nor a nearest-apex rule: weak residues cannot
  displace a confirmed standard, and genuine drift stays possible.
- Selection never changes a candidate's NL answer or promotes an unconfirmed
  peak to a credible anchor. A positive loss on an identity-rejected alternate
  cannot mark the selected unconfirmed ISTD as supported and bypass its
  Backfill assessment.
- The selection answer carries the chosen region and its admissible candidate
  scope; method assessment consumes that scope without rebuilding reference
  identity.

#### Cross-sample correspondence

Correspondence also reads unselected regions.

- Signed sample-pair displacement intervals come from matched, independently
  corroborated reference-linked bodies, keeping multiple drift modes and
  boundary uncertainty. The same drift must not be added again as a symmetric
  allowance. Lying outside a supported mode is not by itself impossibility.
- If every directly supported peer comparison places a candidate in an
  alternate ion band, it cannot substitute for an absent or unquantifiable
  Target: no numeric result, with a correspondence explanation. A single
  acquired point can locate an alternate band without becoming MS1 quantity.
  Missing transfer evidence alone does not trigger this; paired ISTD
  position stays with its own owner.
- Unresolved multi-body totals are measurements, not RT calibrators.
- A body incompatible with the retention context is not replaced by a small
  background residual. Plausible but uncertain mappings keep a provisional
  value and specific review reasons. Without enough RT calibration, a
  measurable candidate can stay provisional and reports that RT plausibility
  is not established. There is no universal 0.75-minute or 20-minute rule; this
  is an operational model, not a chemical-identification claim.

### RT reference

`expected_rt_min` is an identity prior, not an acquisition window or verified
truth.

- With a credible paired ISTD in this sample, the reference is the analyte's
  expected RT plus that ISTD's observed shift from its own expected RT. The
  configured analyte–ISTD offset is kept, including nonzero isotope-label
  offsets; pairing does not force identical apexes.
- A missing or untrusted pair does not become an anchor; the reference is the
  analyte's expected RT plus the available sample drift.
- Candidate scoring uses this reference as its RT prior and the complete
  acquisition span for window position; agreement can support ranking, and
  shared drift incurs no unshifted-window penalty.
- The reference is computed in memory for the current sample. External
  RT-prior or pair calibration files and rolling ISTD prepasses are not inputs,
  and another sample cannot replace missing current-sample identity support.
- Neither the reference, its fallback nor RT agreement establishes identity.
  Humans supply identities, pairings and method-appropriate expected RTs, which
  are not portable across batches or methods.

### Paired-ISTD identity

An NL-required analyte counts only with a chemical identity: its own finalized
CID-NL-supported hypothesis, or its counted isotope-labelled ISTD. A positive
cell, a pair/ratio label or an acquisition-stage flag cannot supply it, and no
MS1-shape rule re-counts `NL_FAIL` or `NO_MS2` rows. An ISTD co-elutes with
its analyte, so a counted ISTD confers identity on the analyte's MS1 peak at
the paired position, whether the analyte's MS2 was missing or lacked the loss.
The rule is applied once every Target in the batch has rendered. All must hold:

1. The paired ISTD is counted in the same sample, by its own CID-NL support or
   accepted ISTD Backfill.
2. The analyte has a positive, coherent MS1 peak (`ms1_peak_present`,
   `ms1_coherent`, `trace_coherent`), or accepted Backfill located it
   (`cross_sample_backfill_localization`; acceptance already qualified the
   recovered peak, whose first capture can describe another peak). It has no
   conflict or exclusion, and its only not-counted reasons are missing direct
   NL/MS2.
3. Its apex RT minus the ISTD's agrees with the paired position. When at least
   two confirmed pairs agree with the configured expected-RT offset within
   12 s, their median offset and the larger of 12 s and the p90 residual set
   the position and tolerance; contradictory counted peaks cannot calibrate
   away their own mismatch. Otherwise the counted ISTD plus the configured
   offset is the reference, with 12 s tolerance. Batch confirmation count is
   not an identity gate: both cases use the same current-sample ISTD,
   peak-quality and blocker requirements.

The result records `paired_istd_identity` among its support reasons, so it
stays distinct from NL-confirmed detection; morphology and other reasons still
flag it.

The same position rejects. With a counted ISTD, an analyte peak outside the
tolerance is another signal, even if its own NL had counted it: RT, area and
bounds are ND (`analyte_peak_off_paired_istd_position`), counted detection is
false, and the peak stays an `OFF_ISTD_POSITION` diagnostic event with its
evidence; no other peak is silently selected. Chemical support alone does not
resolve this contradiction. Being inside the window is not sufficient: quality
and blocker requirements still apply. An uncounted or missing ISTD imposes no
rejection.

Cohort Backfill is a separate recovery once direct reference observations
exist: the [shared Backfill route](backfill.md#targeted-route) attributes a
unique qualified retained MS1 peak from confirmed peers and paired ISTD RT. Its
receipt can replace an observation-only capture; it does not rerank a directly
supported selection or promote HOLD to confirmation.

### Row binding and cell decision

Each Target has one fixed row, and decisions close in this order:

1. Targeted selection binds the qualified finalized groups the row needs.
   Groups covering distinct samples bind the same row; zero groups leave a
   typed state; groups competing for one sample stay unresolved with stable
   Review Queue references. Binding does not rescan RAW, rank by area, change
   integration or pick an arbitrary winner.
2. The cell decision selects the value or a typed no-write. An owner-backed
   direct local observation can give a value even when its group is
   provisional or it is not a member. A sample without its own direct
   observation cannot get a value from other samples' singleton or RT-prior
   evidence; the cell decision takes no revisit receipt.
3. Targeted selection then closes inclusion, counted detection, typed reasons
   and Review Queue routing without recomputing the value.

Duplicate target labels fail closed.

### Output and review

- CSV/workbook RT, area and bounds render the cell decision even when
  `Counted Detection` is false, except an analyte peak off its
  [paired-ISTD position](#paired-istd-identity). A typed no-write clears the
  peak before rendering. Missing NL alone is not a writer veto; accepted
  MS1-only Untargeted cells follow the same distinction.
- Counting and review render one product projection, which counted detection
  belongs to; `count_no_ms2_as_detected` cannot override it. Shared evidence
  providers inform that state but do not choose it.
- The main table shows measurements, counted status and the fixed labels
  `依據` (what the result rests on) and `疑點` (what stays uncertain); secondary
  columns keep integration boundaries. Internal projection codes stay out of
  the workbook, and its layout does not change scientific or count policy.
  `Product State`, `Counted Detection` and `Review State` are advanced policy
  fields and do not veto a retained value.
- A completed ND without an independent conflict or exclusion needs no review
  note; missing MS1 signal and rejected off-pair candidates stay diagnostic. A
  retained peak with unresolved identity or quality, or a missing spiked ISTD,
  needs review.
- A positive, coherent analyte peak in scope with candidate-associated MS2/NL
  support keeps RT-prior and window-position conflicts as review, whatever the
  sample category; RT disagreement alone does not make it ND. Exclusions,
  paired-anchor conflicts, hard quality blockers and unresolved boundaries
  still apply.
- RT and score labels alone create no review action. Batch-context
  displacement, missing calibration, viable competition and uncertain transfer
  are separate explanations. A finalized paired ISTD identity resolves those
  localization concerns, but not morphology, signal-gap or mixed-area
  concerns, nor a missing NL or MS2, which stays a doubt. Unresolved
  morphology can require quality review independently of identity. Reason labels
  describe evidence and never start a second ranking.
- `Target-peak CID-NL not supported` marks a target-matched recorded precursor
  with unqualified isolation or product evidence, with its cause retained.
  `No target-matched MS2 in peak` describes acquisition opportunity, not
  absence. Neither suppresses accepted ISTD Backfill or paired-ISTD identity,
  nor creates direct support.

Before region, boundary or envelope logic changes a selected peak, area,
reason, product state, counted detection, workbook or Matrix value, the change
needs expected-diff review; selected-hypothesis public behavior changes only
with an explicit schema/versioning plan, and the claim needs a matching
RAW-tier evidence label. Passing tests do not establish scientific readiness;
method-changing selection rules need the method-appropriate oracle.

## Reference Input Diagnostic

An on-demand diagnostic checks target CSV reference RTs (the search-window
midpoint) across method groups by leave-one-out monotonic mapping of shared
targets, and against RAW peak-cluster positions. Mapping residuals give
informational warnings; only RAW cluster disagreement or a mapping onto a
different RAW cluster gives a high warning. Its outputs are `diagnostic_only`
and carry no selection, reference-editing or quantitation authority.
[`test_diagnostic_reference_consistency.py`](../../tests/test_diagnostic_reference_consistency.py)
pins the exclusion, voting and warning rules; `--help` owns the defaults.

## Boundaries

- **Consumes:** the owner-issued target search scope. It narrows the Target
  universe and can still ask the shared method to inspect broad relationships
  such as dR, R or MeR.
- **Owns:** the fixed Target row projection, row binding and cell subject,
  post-cell inclusion, reasons, product state, counting, Review Queue routing
  and target-pair RT rules.
- **Selected value:** the cell decision owns it; Targeted projection renders it
  and does not rerun selection or integration.
- **Does not own:** Backfill or Discovery writer scope, full review-action
  apply behavior, or maturity tiers. Targeted and Untargeted authority do not
  extend to each other.
- [Workbench](workbench.md) shows retained evidence for review; Inspector does
  not switch candidates or build RT reselection tables.

## Pitfalls

- Treating diagnostic boundary proposals as final selected boundaries.
- Promoting target-pair RT support without explicit activation.
- Letting selected-hypothesis refactors silently change reasons or counted
  detection.
- Treating configured Target identity as proof of a local observation or
  membership.
- Creating a second Target-only capture, trajectory, Backfill or cell-decision
  implementation because the output rows differ.

## Verification

- [`test_targeted_peak_selection.py`](../../tests/test_targeted_peak_selection.py)
- [`test_targeted_specificity.py`](../../tests/test_targeted_specificity.py)
- [`test_targeted_delta_mass.py`](../../tests/test_targeted_delta_mass.py)
- [`test_targeted_product_projection.py`](../../tests/test_targeted_product_projection.py)
- [`test_target_pre_cell_projection.py`](../../tests/test_target_pre_cell_projection.py)
- [`test_target_post_cell_projection.py`](../../tests/test_target_post_cell_projection.py)
- [`test_targeted_production_workflow.py`](../../tests/test_targeted_production_workflow.py)

## See Also

[CID-NL support relations](discovery.md#cid-nl-support-relations) ·
[Quantitation context](signal-processing.md#quantitation-context) ·
[Decision policy](decision-policy.md)

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Per-Target specificity | `configuration.targeted_identification` |
| Retained population comparison, shared groupings and retention uncertainty | `peak_detection.targeted_context` |
| Ordered candidate assessment, exact-precursor membership, scoring | `extraction.targeted_peak_selection` |
| Batch selection among retained regions | `extraction.targeted_batch_selection` |
| MS2 window precursor scope | `targeted_ms2_precursor_scope` in `extraction.targeted_peak_selection` |
| Cell value or typed no-write | `ProductionCellDecision` |
| Paired-ISTD identity and rejection | `paired_istd_positions` in `extraction.targeted_product_projection` |
| Row binding and cell subject | `alignment.projection.target.pre_cell` |
| Reference input diagnostic | `tools.diagnostics.check_reference_consistency` |

RAW acquisition supplies retained arrays and the MS2 window, then consumes the
completed selection; the selection owner works in memory and does not read RAW.
