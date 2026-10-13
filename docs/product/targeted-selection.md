# Targeted Selection

Targeted CID-NL uses the same quadrature precursor/product m/z window as
Discovery when it evaluates a configured neutral loss. `nl_ppm_max` and the
CID-NL support policy tolerance are per-ion ppm inputs to that window. The
reported observed-loss error in ppm remains a descriptive measurement.

Targeted selection is the Targeted workflow projection for known targets.
Targeted selection does four things:

1. Targeted selection constrains the shared scientific lifecycle with an exact
   target-list snapshot.
2. Targeted selection binds finalized groups to fixed target rows.
3. Targeted selection applies Targeted inclusion/counting and typed
   reason/routing policy.
4. Targeted selection closes references to the existing Review Queue and the
   delivery-shape decisions.

Existing CSV/workbook/review surfaces render human-facing reasons from those
typed decisions. Targeted selection does not require a second capture,
CrossSample, Backfill, or selected-value implementation.

## Contract

### Reference input diagnostics

`tools.diagnostics.check_reference_consistency` checks target CSV references
on demand, using the existing configuration reader and RAW acquisition owner.
The JSON manifest explicitly names each method/group, target CSV, and optional
RAW files. The reference RT is the target search-window midpoint.
Multiple groups use leave-one-out monotonic interpolation of other shared
targets, including ISTDs. For each directed group pair, anchors with the
largest supported own leave-one-out residual above the policy threshold are
iteratively excluded while at least three anchors remain. Excluded targets
still receive predictions and warnings; exclusion does not declare a reference
wrong. Unsupported endpoints are not extrapolated.
Single-group input skips mapping and checks only RAW peak-cluster positions.

The diagnostic emits `reference_consistency.json` and `reference_review.tsv`.
The JSON records screened anchors, pairwise predictions, and the median/p90
absolute residual distribution over group/target median predictions. Mapping
residuals above the policy threshold receive informational warnings. High
warnings require RAW multi-cluster ordinal disagreement, a reference outside
every detected cluster's half-prominence extent and beyond the apex-match
tolerance, or a mapping prediction matching a different RAW cluster. A matching
single peak cannot produce a high warning.
Ordinals compare only equal cluster counts, with one vote per group and a
majority of all peer groups; incomparable groups abstain. Gaussian smoothing,
minimum prominence, and minimum half-prominence width reject narrow impulses
before assigning ordinals. These are diagnostic positions, not selected peaks.
Missing RAW evidence remains unavailable. CLI help owns policy defaults and
acquisition bounds; the JSON preserves the effective policy and input provenance.
These outputs have `diagnostic_only` evidence status and no selected-peak,
reference-editing, quantitation, or ProductWriter authority.

### Selected-hypothesis grouping

Targeted groups its already selected hypotheses through the existing CrossSample
owner. CID-NL support, observation-only
selections, and MS1-only targets retain their sample-local identities and values.
Grouping does not reselect peaks or add a cohort RT review policy.

### Candidate selection

The existing CID-NL method-assessment owner attributes acquired events against
the complete physical candidate universe within the Targeted search scope
before selection. Strictly interior, uniquely attributable evidence supports
an individual peak; boundary and overlapping relations remain set-level.
Selection consumes this answer rather than treating a candidate-local best
MS2 product as proof of unique support.

`peak_detection.candidate_selection.select_candidate_by_evidence` chooses the
candidate once from typed
candidate evidence and physical eligibility. Chemical support precedes
localization hints and trace quality; contextual RT and deterministic abundance
break ties within the supported evidence ordering. Expected RT and paired-ISTD shifts guide identity, not acquisition. Exact-mass
MS1 candidate coverage spans the sample acquisition interval, including targets
without MS2. The complete scan population is read once per sample; each tag is
parsed once by Discovery, with shared parent-MS1 verification and scan-event
resolution within each target’s m/z population before candidate-local CID-NL
assessment. Different recorded precursors from one scan remain available to
their respective targets.

Within the same chemical-support tier, an ISTD candidate meeting the existing
anchor completeness and reference-proximity rule precedes a remote candidate,
even if the remote candidate has stronger MS2 trace support. A matching NL at
another chromatographic position does not make the two peaks interchangeable.
This preference does not crop acquisition, confirm an unsupported nearby peak,
or reject supported drift when no qualified reference-local candidate exists.

For named Target identity, own CID-NL support requires a compatible recorded
scan precursor or vendor monoisotopic reference. A parent-MS1 ion found by
searching product + configured loss establishes mass/isolation compatibility,
not which coisolated ion produced the fragment. Such inferred observations
remain in the method evidence but cannot independently select or identify a
Target peak. The shared relation owner applies this reference requirement
consistently during selection and formal support binding; Discovery retains
its independent-mass candidate scope. Paired-ISTD and accepted Backfill identity
remain governed by their existing owners, not a new RT gate.

A recorded vendor monoisotopic reference can support CID-NL even when the
monoisotopic m/z lies outside the nominal isolation window: the selected ion
must be inside that window, have recorded charge 1, and match a positive integer
carbon-isotope offset from the vendor monoisotopic m/z within the shared
precursor mass policy (20 ppm). Missing geometry or charge, an unrelated mass
offset, and a product-plus-loss inference do not qualify for this route. The
reported monoisotopic isolation membership remains `outside_nominal`; eligibility
does not rewrite geometry or prove exclusive fragment parentage. Existing
product/NL mass, intensity, target attribution, and peak-local checks still apply.

Strong recorded-precursor-supported, quantifiable peaks remain counted despite RT drift.
This does not override a counted paired ISTD: a peak outside its paired-position
tolerance is ND, even with neutral-loss support. Chemical support alone does not
resolve that positional contradiction.
RT-prior disagreement and legacy confidence labels alone do not create product
review actions. Unresolved morphology or overlap concerns can require quality
review independently of identity. Confidence and reason labels describe source
evidence; they do not start a second candidate ranking.

The Targeted handoff retains all candidate physical evaluations, acquired
method products, the search receipt and the relation answer. Production binds
that same answer to exact sample/event/peak provenance without another relation
decision. Only the selected candidate enters the fixed Target row; the method
assessment still records its complete candidate and event universe.

`extraction.targeted_peak_selection` owns this ordered candidate assessment,
including exact-precursor membership, support-to-candidate mapping and the
selected method index. Acquisition supplies retained arrays and an acquired
MS2 window, then consumes the completed result instead of separately assembling
those coupled facts. The owner evaluates and ranks in memory; it does not read RAW.

Sample applicability
controls inclusion, not permission to replace the selected candidate. A
hypothesis is an evidence representation, not a competing selection owner.
The physical owner may withdraw an unavailable measurement, but does not
substitute another candidate.

For an NL-required analyte, counting needs a chemical identity: its own
finalized CID-NL-supported hypothesis, or the identity of its paired
isotope-labelled ISTD. A positive numeric cell, legacy confidence, pair/ratio
label or acquisition-stage detection flag cannot supply that authority. The
existing product projection renders the finalized method answer and retained
review facts. ISTD expected-presence, MS1-only, RT and quality policies remain
separate.

#### Paired-ISTD identity

An isotope-labelled ISTD co-elutes with its analyte, so a counted ISTD confers
chemical identity on the analyte's MS1 peak at the expected position. The
analyte's own neutral loss is then not required, whether its MS2 was missing or
was acquired without the configured loss. `paired_istd_positions` in the
Targeted projection owner applies the rule, and the publication boundary
composes it once every Target of the batch has rendered. All of these must hold:

1. The paired ISTD is counted in the same sample, by its own CID-NL support or
   by accepted ISTD Backfill.
2. The analyte has a positive, coherent MS1 peak (`ms1_peak_present`,
   `ms1_coherent`, `trace_coherent`), or accepted Backfill located it
   (`cross_sample_backfill_localization`; acceptance already qualified the
   recovered peak, whose first capture may describe another peak). It has no
   conflict or exclusion, and its only not-counted reasons are missing direct
   NL/MS2.
3. Its apex RT minus the ISTD's agrees with the paired position. When at least
   two confirmed pairs agree with the configured expected-RT offset within
   12 s, their median offset and the larger of 12 s and the p90 residual refine
   that position and tolerance. Contradictory counted peaks cannot calibrate
   away their own mismatch. Otherwise the counted ISTD plus the configured
   analyte-minus-ISTD expected-RT offset is the reference, with a 12 s tolerance.
   Batch confirmation count is not an identity gate: both cases use the same
   current-sample ISTD, peak-quality and blocker requirements above.

The result records `paired_istd_identity` among its support reasons, so it
remains distinguishable from NL-confirmed detection. Morphology, overlap and
other review reasons still flag it.

The same position also rejects. When the paired ISTD is counted, any analyte
peak outside that tolerance is another signal, not this analyte, even if its own
neutral loss had made it counted. Its RT, area and bounds are published as ND
(`analyte_peak_off_paired_istd_position`) and counted detection is false. The
measured peak remains an `OFF_ISTD_POSITION` diagnostic event and its candidate
evidence remains available for inspection; another peak is not silently selected.
`paired_istd_positions` returns both identity and rejection from one position
assessment. Being inside the window is not sufficient by itself: the peak-quality
and blocker requirements still apply. An uncounted or missing ISTD cannot impose
this paired-position rejection.

Cohort Backfill is a distinct, owner-bound recovery after direct reference
observations are available. The [shared Backfill route](backfill.md#targeted-route)
can attribute a unique qualified retained MS1 peak using confirmed peers and
paired ISTD RT. Its receipt may replace an observation-only capture; it does
not rerank a directly supported selection or promote HOLD to confirmation.

Regression coverage protects the selected candidate, contextual RT, chemical
and morphology evidence, no-candidate behavior and rendered measurements.
Tests passing alone do not establish scientific readiness; method-changing
selection rules still need the method-appropriate oracle.

### Physical evaluation

The Targeted selection module consumes an acquired TraceGroup, candidate-local MS2
evidence, and the shared prepared signal and segment universe. It completes the
physical answer and synchronizes the final peak, hypothesis, and selection
decision before returning them. RAW acquisition owns trace construction and
MS2 reads; it consumes the completed selection without withdrawing or rebuilding
those facts itself.

- Acquisition enumerates the shared physical segment universe for the
  configured signal profile.
- Each candidate's physical evaluation supplies its boundary and counts·seconds
  area. Targeted scoring and identity evidence choose a component from these
  retained answers; the selected evaluation is reused by the handoff.
- Profile-bound capture retains that evaluation through finalization.
  Profile-bound capture does not run a second boundary resolver or
  integration.
- RT, bounds, and width are expressed in minutes.
- When identity selection chooses no component, the physical question retains
  the acquired context and full segment universe with no admissible selection;
  capture preserves the unavailable answer instead of selecting a broad tail.
- Exact-mass MS1 acquisition covers the complete acquisition RT bounds. No
  expected-RT window, DDA anchor crop, or reacquisition expands or truncates the
  physical candidate population.

### Target identity and search scope

- `expected_rt_min` is a method identity reference, not an acquisition window.
- A credible current-sample paired ISTD transfers observed drift while preserving
  the analyte/ISTD expected-RT offset. Missing or untrusted pairs use the analyte
  reference plus available current-sample drift.
- An analyte with no configured ISTD pair uses that method reference as a
  selection anchor. Among independently supported peaks, a complete nearby
  candidate is not displaced only by denser MS2 support on a distant isomer.
  This does not impose a hard RT exclusion or grant chemical identity from RT.
- CID-NL evidence comes from the shared Discovery chemistry and mass policy for
  the row's `dR`, `R`, or `MeR` tag. Recorded-precursor and product-plus-neutral-loss
  inference retain their evidence basis and parent-MS1 support requirements.
- Each retained physical candidate receives one completed target-scoped MS2
  answer from the shared CID-NL relation. Ranking, qualitative support, trace
  diagnostics and the published `NL Evidence` consume that answer. A neighbouring
  precursor's matching loss cannot supply trace strength or a positive label.
- Only positive, individually attributed CID-NL support receives chemical ranking
  priority. No MS2 and MS2 without qualified support are both unconfirmed; neither
  has an automatic identity advantage over the other. In their absence, the
  ordinary physical and RT/ISTD evidence decides candidate localization.
- Final selection, including Backfill, selects the corresponding retained MS2
  answer as well as the peak. A no-write cell has no selected-peak positive NL
  claim. Publication never falls back to a whole-run neutral-loss observation.
- A target-matched recorded precursor with unqualified isolation or product
  evidence is reported as `Target-peak CID-NL not supported`, with the diagnostic
  cause retained. `No target-matched MS2 in peak` describes acquisition opportunity,
  not chemical absence. Neither label suppresses an independently accepted ISTD
  Backfill or paired-ISTD identity, nor creates direct CID-NL support.
- Identity ranking does not alter physical bounds or area. Full-trace shared
  baseline estimation can change quantitation relative to a cropped trace.
- Product Targeted output is a workflow-owned projection from evidence and
  selection decisions.
- Shared evidence providers inform Targeted product state. Shared evidence
  providers do not choose Targeted product state.
- `TargetKey` is the stable logical row identity.
- `TargetDefinition` is the exact immutable scientific revision.
- `TargetListSnapshot` is the exact ordered request input.
- A same-label definition change preserves the key. The same change alters the
  definition identity and the snapshot identity.
- Duplicate labels fail closed.
- `TargetSearchScope` narrows the allowed target universe.
  `TargetSearchScope` can still ask the shared Discovery/capture method to
  inspect broad evidence relationships such as dR, R, or MeR.
- Broad candidate discovery is not Untargeted-only.

### Row binding

- `TargetRowBinding` retains the qualified finalized groups that each fixed
  Target row needs.
- If zero groups qualify, the row stays with a typed state.
- Groups that cover distinct samples bind the same `TargetKey` row.
- Multiple groups that compete for one sample remain unresolved with stable
  Review Queue references.
- `TargetRowBinding` does not rescan RAW, rank by area, change integration, or
  choose an arbitrary winner.
- A Target row can bind a unique established or provisional group.
- An owner-backed direct local observation can produce a Target cell value.
  This rule holds even when the group is provisional or the observation is not
  a group member.
- A sample without its own owner-backed direct local observation cannot get
  a Target cell value from singleton or RT-prior evidence of other samples.
  Such a sample requires its own direct local evidence. The Target cell
  decision takes no revisit receipt input.

### Cell decision order

1. Targeted selection owns `TargetRowBinding` and constructs
   `TargetCellSubject`.
2. `ProductionCellDecision` owns the selected numeric value or the typed
   no-write result.
3. The same Targeted owner then closes inclusion, counted-detection policy,
   typed reason facts, and Review Queue routing. The Targeted owner must not
   recompute the value.

### Numeric output and evidence

- Finalized CSV/workbook RT, area, and bounds render the cell owner's numeric
  answer even when `Counted Detection` is false, except a paired analyte peak
  outside its [paired-ISTD position](#paired-istd-identity).
- Count, confidence, and review remain separate evidence/policy fields.
- A typed no-write clears the retained peak before rendering.
- Missing NL alone is not another writer veto.
- Accepted MS1-only cells in Untargeted output follow the same distinction.
- The main workbook table shows measurements, counted status and review
  reasons. Secondary columns retain integration boundaries, `NL Evidence`
  and the full plain-language `Reason`.
- Internal projection codes remain in the internal long-row model, not the
  delivered workbook. The workbook consumes the finished owner decisions;
  its display layout does not change scientific or count policy.

### Detection and review policy

- Targeted NL matching excludes scans whose recorded vendor charge is not 1;
  unavailable charge remains eligible. `NO_MS2` means no method-eligible
  precursor scan, not necessarily no acquired MS2 scan. If only rejected-charge
  scans are present, `valid_ms2_scan_count` still records them but
  `matched_scan_count` is zero. The existing Targeted product projection owns
  counted detection; `count_no_ms2_as_detected` applies only to legacy outputs
  without that projection and cannot override it.
- Region, boundary, and selected-envelope logic provide candidate-evaluation
  evidence.
- Before region, boundary, or selected-envelope logic changes any selected
  peak, area, confidence, reason, product state, counted detection, workbook
  value, or matrix value, the change requires expected-diff review.
- Selected-hypothesis public behavior must preserve compatibility
  projections unless an explicit schema/versioning plan changes the
  downstream contracts.
- A positive, coherent analyte peak can sit inside the configured search scope
  with candidate-associated MS2/NL support. Such a peak keeps
  RT-prior/window-position conflicts as review, independent of the sample
  applicability category.
- RT disagreement alone does not make that peak ND.
- Sample exclusions, paired-anchor conflicts, hard quality blockers, and
  unresolved quantitative boundaries still apply to that peak.

### Current-sample paired RT reference

For an analyte with a credible paired ISTD in this sample, the RT reference is
the analyte's expected RT plus the observed ISTD shift from its own expected RT. The
configured difference is retained, including nonzero isotope-labelled offsets;
pairing does not force identical apex times.

Candidate scoring uses this same reference as its paired-ISTD RT prior and
uses the complete acquisition span for the window-position evidence. Agreement can support
ranking; shared sample drift does not incur an unshifted-window penalty.
The RT prior is not an additional chemical identity or NL confirmation.

Missing or untrusted paired ISTD observations do not become anchors. The
fallback remains the analyte expected RT plus the available sample
drift. Neither fallback nor RT agreement establishes chemical identity. Humans
still provide target identities, pairings and method-appropriate expected RT;
these cannot be assumed portable across batches or methods.

The reference is computed directly in memory for the current sample. External
RT-prior/pair calibration files and rolling ISTD prepasses are not analysis
inputs. A different sample cannot replace missing current-sample identity support.

### Counting and product selection

- One extraction pass decides each row. Analyte `NL_FAIL` or `NO_MS2` rows
  keep their measured RT, area and bounds; they are counted only through
  [paired-ISTD identity](#paired-istd-identity). There is no MS1-shape rescue
  that re-counts such rows.

## Surfaces

| Surface | Role |
| --- | --- |
| Targeted CSV/workbook fields | Public output for target decisions |
| RT / Area / PeakStart / PeakEnd | Retained cell-owner measurements |
| `NL Evidence` / `Reason` | Human-readable observations and review context |
| `Product State` / `Counted Detection` / `Review State` | Advanced product-policy fields; do not veto a retained numeric value |
| Confidence | Advanced review context rendered from typed projection decisions |
| Region and selected-envelope candidates | Boundary and candidate-evaluation evidence |
| Current-sample paired RT reference | Method offset transferred by a credible observed ISTD; no additional writer authority |
| Expected-diff packets | Required gate for output-changing behavior |

## Boundaries

- **Consumes:** owner-issued `TargetSearchScope`.
- **Owns:** fixed `TargetKey` row projection, pre-cell
  `TargetRowBinding`/`TargetCellSubject`, post-cell inclusion,
  typed confidence/reason/product-state/counting decisions, existing Review Queue
  routing, and target-pair RT rules.
- **Selected value:** `ProductionCellDecision` owns the selected numeric value.
  Targeted projection renders that decision and its retained physical
  evidence. Targeted projection does not rerun peak selection or integration.
- **Does not own:** Backfill/Discovery writer scope, the current selected peak
  of a specific target, full review-action apply behavior, or maturity tier
  decisions.
- Targeted authority does not extend to Untargeted pipelines. Untargeted
  authority does not extend to Targeted pipelines.

## Verification

- Focused tests for selected peak, area, confidence, reason, product state, and counted detection.
- Expected-diff packet for workbook/CSV or matrix-impacting changes.
- Schema/versioning review when output fields or compatibility projection change.
- RAW-tier evidence label matching the product claim.
- Structured productization-owner update when maturity, lane, or authority
  changes: status index for lane state, authority manifest for writer scope,
  and the promotion contract for readiness evidence.
- The bounded CID-NL `targeted_output` delivery claims and current test
  dispositions are indexed in the
  [public-output claim catalog](../superpowers/validation/cid_nl_public_output_claim_catalog.tsv).
  The catalog points to these owners. The catalog does not grant
  selected-value, counted-detection, ProductWriter, or Matrix authority.

## Pitfalls

- Treating diagnostic boundary proposals as final selected boundaries.
- Promoting target-pair RT support without explicit activation.
- Letting selected-hypothesis refactors silently change public reason or counted-detection behavior.
- Reusing Backfill or Discovery authority for Targeted selection.
- Treating configured Target identity as proof of a local observation or group
  membership.
- Creating a second Target-only capture, trajectory, Backfill, or cell-decision
  implementation because the output rows differ.

## See Also

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md) | [Evidence spine](evidence-spine.md)
- [Quantitation context](quantitation-context.md) | [Review roundtrip](review-roundtrip.md)
- [Decision policy](decision-policy.md)

## Retired candidate audit outputs

`emit_peak_candidates` and its candidate, boundary, region shadow, selected-envelope,
and target-pair RT auto-reselection TSV outputs are removed. Remove this key from
existing settings CSVs; there is no compatibility alias. Candidate-sidecar
verification and the dedicated shadow/pressure review commands are unavailable.
Use [XIC Inspector](xic-inspector.md) for visual review of its supported retained
evidence sources. Inspector does not apply candidate switches or generate RT
reselection comparison tables. Formal peak selection and numerical output remain
owner-controlled.
