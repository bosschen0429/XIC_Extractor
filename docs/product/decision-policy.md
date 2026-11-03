# Decision Policy

Decision policy is the layer that turns typed evidence into workflow-owned
product decisions. It is not a new scoring formula and not a shortcut from
diagnostics into product outputs.

This page exists because "evidence chain" and "score" answer different
questions:

- Evidence explains what was observed and how trustworthy each observation is.
- Score ranks comparable candidates after the hard decision gates are satisfied.
- Workflow policy decides which evidence is required, which conflicts block the
  decision, and which projection may write or count product output.

## Contract

- Evidence providers feed typed facts into `EvidenceVector`,
  `EvidenceDecisionSemantics`, `PeakHypothesis`, `IntegrationResult`, and audit
  records. Evidence providers do not write product matrices directly.
- Decision policy owns gate order. `ProductionCellDecision` is the sole owner
  that converts closed observation evidence into a selected value or typed
  no-write result. If a rule affects selected peak, selected area, counted
  detection, product state, matrix value, or output identity, the rule must be
  explicit and test-covered.
- Score is a ranking and compatibility tool. A high score cannot bypass missing
  required evidence, hard conflicts, unresolved ambiguity, missing provenance,
  or workflow-specific activation gates.
- Targeted and Untargeted workflows share the cell value/no-write owner.
  Detection-count policy must not discard a numeric value from the same closed
  observation and integration evidence. Each workflow owns its search/row
  context and explicit inclusion, count, and review projection.
- Diagnostic, shadow, gallery, review, lockbox, and sidecar evidence can
  explain a decision. Such evidence becomes product authority only through an
  explicit activation/export contract and expected-diff gate.

## Current Transition State

The codebase is not fully `PeakHypothesis`-native yet. Legacy selectors,
confidence fields, `raw_score`, `evidence_score`, and string evidence fields still exist because they are public compatibility
surfaces or migration adapters.

Treat those surfaces as compatibility or audit signals unless another owner
promotes them through typed evidence semantics, workflow projection, parity
evidence, or approved expected-diff records. A compatibility oracle is a
baseline for proving behavior, not the owner of product truth.

## Layers

| Layer | Owns | Must not own |
| --- | --- | --- |
| Typed evidence facts | Observed RT, shape, boundary, MS2, NL, isotope/adduct, standards, library, paired-context, and provenance facts | Product state, counted detection, matrix value |
| Decision semantics | Accepted/review/not-counted/ambiguous/excluded classes, support reasons, conflict reasons, blocker reasons | Workflow-specific writer authority |
| Model selection / ranking | Choosing among comparable `PeakHypothesis` candidates after typed gates | Overriding hard gates or granting matrix writes |
| Cell decision | Selected value or typed no-write for a Target subject or completed Feature × Sample observation | Row identity, grouping, RAW access, or rendering |
| Workflow projection | Row identity/binding and output cell subjects; inclusion, counted detection, product state, reasons, Review Queue/archive routing, and ProductWriter gates | Low-level evidence extraction, Backfill revisit, or value recomputation |
| Audit trail | Human-readable explanation, review queue, expected-diff evidence | Silent product behavior |

Target projection binds its fixed row and constructs `TargetCellSubject` before
value selection, then closes inclusion, counts, reasons, and routing. Untarget
value selection consumes completed `FinalGroupCellObservation` evidence first,
without row identities. One Matrix projection then packages the existing payload
and selected integration as `ProductionCellDecision`, assigns row/cell IDs, and
closes sample order, counts and routing without repeating scientific decisions.

## Common Decision Shape

Every value-changing decision should be explainable in this order:

1. **Unit:** what is being decided: target candidate, `PeakHypothesis`, sample
   cell, cross-sample group, or matrix value.
2. **Required evidence:** typed facts that must be present before the decision
   can be considered.
3. **Blockers:** conflicts, ambiguity, provenance gaps, sample-role exclusions,
   wrong-peak evidence, or missing activation authority.
4. **Support:** facts that strengthen the selected unit but do not independently
   grant authority.
5. **Tie-break:** deterministic ranking among still-comparable candidates.
6. **Projection:** the workflow-owned output decision and the artifact or test
   that authorizes it.

If a decision cannot name these fields, it is not ready to become product
behavior. It can remain diagnostic, shadow, or review-only.

## Quantification, Detection Counts, And Observation Origin

| Concept | Owner and meaning | Public consequence |
| --- | --- | --- |
| Numeric or no-write | `ProductionCellDecision`, from closed observation and integration evidence | Finalized RT/area/bounds are rendered; a typed no-write has no numeric output |
| `accepted_cell_count` | Number of cells accepted for numeric output in the completed Target or Untarget projection | Includes accepted MS1-only cells; is not a direct-detection count |
| Counted detection | Explicit evidence/count policy | Controls detection statistics and the existing trusted-reference consumers, not whether an accepted value is rendered |
| Direct observation / Backfill | The actual capture or Backfill observation evidence | A usable MS1 value does not imply that a Backfill operation ran or that NL was observed |
| NL observation | Configured provider result | Missing NL or missing MS2 is visible evidence context; blank NL configuration remains unconfigured |
| Review | Retained reason and review state | Review can coexist with a numeric value |

Same-feature MS1 Backfill can be numeric without becoming a direct detection.
An observed Target-local peak can likewise be numeric while its count policy is
false. These conclusions use the shared value owner; a writer must not turn
either accepted value into ND by inspecting count, confidence, or review. A
TargetKey or matching search window alone does not mint Group membership or a
Backfill receipt. Actual observation origins remain explicit rather than being
renamed to imply an operation that did not run.

### Targeted quantitative support

For a Target cell, an observed integral is not automatically fit for numeric
publication. The cell-decision owner returns `not_quantifiable` when all three
facts hold: the retained `SignalQuality` reports low S/N, fewer than half of the
selected raw XIC scans are nonzero, and no continuous run contains at least half
of those nonzero scans. Low S/N alone, sparse outer flanks around a continuous
body, or missing quality metadata do not establish this combined failure.
These majority conditions are an operational support policy, not a calibrated
LOD/LOQ or chemical-absence claim.

The selected integration, original trace, and method evidence remain retained.
NL or an ISTD identity cannot override inadequate MS1 quantitative support.
The rule applies only to Target decisions; completed Untargeted group-cell
decisions retain their existing policy. The writer renders this no-write as
blank numeric cells with an explicit not-quantifiable explanation, not zero or
ND. It does not re-evaluate the signal.

## Targeted Confidence / Reason / Counted Detection Precedence

Targeted workbook rows present the retained RT, area, integration boundaries,
`NL Evidence`, and a plain-language `Reason`. These displayed cells do not
decide counted detections or numeric availability. The Targeted product
projection supplies count/review policy. The finalized cell decision supplies
the values. The following layers feed the displayed result:

| Layer | Produces | Resolution point |
| --- | --- | --- |
| Candidate scoring | `peak_result.confidence` / `peak_result.reason` | `peak_detection/candidate_scoring.py` through `peak_detection/peak_selection.py:select_peak_and_area` |
| Hypothesis selection | `selection_decision.projected_confidence` / `projected_reason` | `extraction/result_assembly.py:_result_confidence` and `_result_reason`, when a selection decision exists |
| Stored result | resolved `result.confidence` / `result.reason` | `extraction/result_assembly.py:build_extraction_result` |
| Targeted product projection | `product_state`, `counted_detection`, `projection_reason` | `extraction/result_assembly.py:_targeted_product_projection` |
| Display resolution | workbook / CSV `Confidence` and `Reason` cells | `output/csv_writers.py:_display_confidence` and `_display_reason` |

Invariants:

- Counted detection and detection summaries read `projection.counted_detection`
  and `product_state`, not `Confidence`, neutral loss flags, scores, or caps.
  CSV and workbook numeric columns render the retained finalized peak even
  when `counted_detection` is false. The HTML summary median uses the retained numeric
  population independently of its detection tally.
- `_display_confidence` may demote projected rows to `VERY_LOW`; it must not
  promote rows. If a row should read higher, fix the projection decision.
- When a Targeted product projection exists, `_display_reason` describes the
  retained MS1 measurement and its review reasons through
  `display_measurement_reason`. Otherwise `_display_reason` uses the stored
  `result.reason`. `projection.projection_reason` feeds the separate
  `Projection Reason` column. The workbook preserves the CSV explanation.
- `NL Evidence` distinguishes observed support, no support within tolerance,
  no MS2 spectrum, and an unrequested check. Blank NL configuration is
  `Not requested`, not failed evidence.
- Internal count/product/review codes, raw NL tokens, and confidence remain
  available as advanced workbook columns. They do not replace the main
  measurement and evidence explanation. Missing support or shape concerns do
  not themselves establish that the declared target is absent; the user
  interprets these facts.
- New evidence sources must enter as typed evidence or selection context and be
  reconciled by workflow projection; they must not create another direct display
  or count authority.
- `reproject_extraction_result` must preserve the same candidate scoring,
  selection, projection, and display precedence as `build_extraction_result`.

## Targeted Policy

Targeted selection is hypothesis-driven. The workflow already knows target
identity, role, target window, expected MS2/NL behavior, and paired standards
when they are configured. That knowledge is part of the assay contract, not a
generic evidence provider.

| Decision role | Targeted rule |
| --- | --- |
| Unit | Candidate `PeakHypothesis` for a known target/sample pair |
| Required evidence | Selected candidate or explicit no-peak state, target identity, positive MS1 state when counted, and provenance-valid trace context |
| Hard blockers | Invalid selected hypothesis, hard wrong-peak conflict, missing positive MS1 for counted detection, role-incoherent paired context |
| Support | Candidate-aligned MS2/NL, target RT window, paired ISTD/standard RT context, paired area ratio, morphology, boundary assessability, local S/N |
| Tie-break | Decision class first, then blocker count, abundance demotion, role-aware RT/context, chemical evidence, trace strength, quality penalty, RT distance, abundance, and finally compatibility score only when comparable |
| Projection | `TargetedProductProjection` first owns fixed Target row binding and Target cell-subject construction; `ProductionCellDecision` then supplies the selected value/no-write result; the same projection finally owns product state, counted detection, confidence, reason, Review Queue routing, and Targeted output fields |

Targeted policy may prioritize paired ISTD/standard RT context because the
Targeted method declares that relationship ahead of time. That priority must
remain in Targeted projection or activation policy; it must not leak into
Untargeted identity decisions as if the workflow had known targets.

Targeted-specific rules to keep explicit:

- Positive MS1 means a finite selected RT and positive area. Without that, the
  row or cell must project to explicit no-peak or not-counted state, not counted
  detection by implication.
- Every configured Targeted target is evaluated for each selected analysis sample.
  Sample names and legacy `sample_applicability` metadata do not gate targets or
  paired-area references. Each target declares its own CID-NL tag; Untargeted
  method-level dR/R/MeR selection does not restrict the Targeted list.
- ISTD and analyte missing-MS2/NL behavior are not symmetric. A configured ISTD
  plausible dropout can be counted as `detected_flagged`; an analyte NL fail or
  missing MS2 needs paired support before it can count.
  This is count/trusted-reference eligibility, not a second quantification
  decision. Neither branch can discard the finalized cell owner's numeric
  answer or manufacture a value when that owner returned no-write.
- Paired analyte support is a bundle: coherent MS1, role-aware RT context,
  paired-area support, same-peak support, and no hard quality blocker. No single
  paired fact is standalone product authority.
- `legacy_review_only_projection` and legacy confidence labels are review or
  compatibility context; they do not by themselves create a not-counted product
  decision.

## Untargeted / Backfill Policy

Untargeted alignment is discovery-driven. It does not know target identity
ahead of time, so CrossSample decides from trace, owner, group, identity, and
provenance evidence rather than Targeted labels. Backfill is a separate bounded
ObservationRevisit owner and does not decide membership, selected value, or row
inclusion.

| Decision role | Untargeted / Backfill rule |
| --- | --- |
| Unit | Separate decisions for owner edge, cross-sample group/disposition, ObservationRevisit, Untarget row identity, Group cell value/no-write, and activation output |
| Required evidence | Explicit discovery/alignment input, owner construction, row identity, typed same-peak evidence when claiming same-peak support, and provenance-valid sidecar when used for product support |
| Hard blockers | Ambiguous owner, duplicate loser, wrong-peak conflict, missing matrix identity, stale source hash, unsupported family projection, unapproved authority scope, missing expected-diff gate for new writer behavior |
| Support | Cross-sample coherence, m/z/RT consistency, MS1 pattern, shape and boundary agreement, MS2/NL consistency, standards/library evidence when represented as typed facts |
| Tie-break | Owner-edge evidence, group match quality, drift-corrected RT, m/z/product tolerances, seed support, owner quality, trace quality, duplicate context, deterministic row ordering, and score only within already-eligible comparable candidates |
| Projection | Untarget projection first decides typed row identity and Group cell subjects; `ProductionCellDecision` then decides cell value/no-write; the same projection closes row inclusion and archive routing before ProductWriter/activation authorizes rendering or publication. Backfill supplies observation facts only |

Targeted outputs may be used as benchmark, audit, or falsification evidence for
Untargeted behavior. They must not directly promote Untargeted rows unless a
separate product contract converts that evidence into typed same-peak,
group-hypothesis, or `PeakHypothesis` support.

Untargeted-specific rules to keep explicit:

- Owner-edge score supports group construction only after hard gates pass. It
  cannot override same-sample conflicts, tag mismatch, m/z/product/NL mismatch,
  ambiguous owner, identity conflict, non-detected owner, or Backfill-bridge
  blockers.
- Row promotion requires row identity support and at least one quantifiable
  detected seed. Backfill-only, duplicate-only, ambiguous-only, review-only,
  consolidation-loser, and zero-detected rows fail closed unless an explicit
  authority contract says otherwise.
- A Backfill observation can write only after row identity passes and
  `ProductionCellDecision` independently selects a quantifiable value. If
  revisit evidence is required, the exact owner-backed ObservationRevisit receipt
  must be present; a revisit never grants membership itself.
- Same-peak support is not the same as group membership. It needs selected peak
  geometry, RT or drift-supported compatibility, no MS1 pattern conflict, no
  hypothesis or claim blocker, and product-authorized trace or pattern support.
- MS2/NL, MS1 pattern, QC reference, sample-matrix RT drift, and shape or region audit
  are typed facts. They strengthen or block decisions only through Backfill
  projection and authority fields.
- `feature_id` is a display label. The group and
  hypothesis objects are the authority-ready identity handles when available.
- Broad Backfill remains gated. Diagnostic sidecars and visual overlay
  evidence are not production readiness by themselves.

## Score Position

Score is allowed when it makes a bounded ranking decision easier to inspect:

- rank candidates that already passed the same hard gates;
- provide compatibility parity while legacy public outputs still expose score,
  confidence, reason, or score breakdown;
- calibrate thresholds for future policy, with expected-diff review before any
  public output changes;
- summarize quality within a typed fact, such as shape similarity or MS2
  similarity, when the threshold and provenance are explicit.
- act as one seed-quality feature in alignment, provided seed events, scan
  support, identity constraints, and other gates still pass.

Score is not allowed to:

- make a candidate product truth by itself;
- override wrong-peak, ambiguity, missing-provenance, sample-role, or authority
  blockers;
- replace a lower-score candidate when typed facts produce worse decision
  semantics for the higher-score candidate;
- collapse Targeted and Untargeted policy into one global weighting scheme;
- turn diagnostic-only or shadow-only artifacts into matrix values;
- hide a new product rule behind a numeric threshold without an owner, tests,
  and expected-diff framing.

When a threshold is unavoidable, name it as a gate with a reason and validation
tier instead of treating it as a universal weight.

Names that contain `score` need extra care:

- `raw_score`, `EvidenceScore.confidence`, support labels, concern labels, and
  cap labels are compatibility or audit projections unless wrapped by typed
  evidence semantics and an approved product policy.
- Targeted selection consumes typed candidate facts through
  `select_candidate_by_evidence`; public confidence and reason fields do not
  authorize a parallel selector.
- Alignment `evidence_score` is a seed-quality score feature, not same-peak
  identity proof.
- `legacy_peak_scoring_current_oracle` is a parity baseline, not a product
  owner.

## Decision Record Requirements

New or promoted decision paths should emit or preserve enough information to
reconstruct the policy:

| Field class | Examples |
| --- | --- |
| Unit identity | `target_label`, `sample_key`, `feature_id`, `(feature_id, sample)` cell position |
| Required evidence status | present/missing/inconclusive typed facts and source artifact hashes |
| Blockers | conflict, ambiguity, stale source, unsupported projection, missing activation |
| Support | RT, shape, MS2/NL, standard/library, paired-context, group/coherence support |
| Tie-break basis | score, RT distance, abundance, deterministic order, compatibility fallback |
| Projection authority | ProductionCellDecision, Targeted/Untargeted projection, ProductWriter scope |
| Validation status | synthetic, focused tests, Targeted benchmark, 8RAW, 85RAW, manual review, expected diff |

The decision record can live in different artifacts for different workflows,
but the meaning must remain stable: evidence explains, policy gates, score
ranks, cell decision selects a value/no-write result, projection includes, and
the writer renders. Reserve `Trace` for LC-MS chromatogram signals;
do not use it as shorthand for decision records.
`DecisionRecord.gate` and `DecisionRecord.tie_break` are audit-visible policy
terms, not a generic selection key. Only workflow-owned selection modules may
convert those terms into ordering keys. Audit-visible means a promoted workflow
must preserve the terms in its owning record, sidecar, or public output when
they explain product behavior; it does not mean every current review TSV emits a
complete `DecisionRecord`.
`DecisionRecord.projection_authority` names the workflow owner allowed to
project or count the result. It is not an evidence token, score field, shadow
artifact label, or display writer name. New product authority labels need the
same public-output expected-diff framing as any other behavior-changing writer
contract.

## Red Lines

- Do not say "the score decided the peak." Say which typed facts passed, which
  blockers were absent, and which projection wrote the output.
- Do not let a shared evidence provider carry workflow-specific authority.
- Do not move Targeted-only role policy into shared evidence semantics unless
  the output stays role-neutral or the projection layer still owns authority.
- Do not treat review labels, display tokens, family labels, galleries, or
  lockbox labels as product writes.
- Do not count multiple derived facts as independent evidence when they come
  from the same physical observation.
- Do not change selected peak, selected area, counted detection, matrix values,
  identity sidecars, or output schemas without expected-diff framing and focused
  output tests.

## See Also

- [Evidence spine](evidence-spine.md)
- [Targeted selection](targeted-selection.md)
- [Backfill](backfill.md)
- [Alignment](alignment.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
