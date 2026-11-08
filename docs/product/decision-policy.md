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

- Prefer evidence chains over any single metric. RT, CWT, WIS, iRT, local
  minima, RT models, shape similarity, product ions, neutral losses, adducts and
  in-source fragments are evidence inputs; none silently overrules the selected
  peak or Matrix identity on its own. An evidence source that changes
  production behavior needs explicit config or contract and machine-readable
  reason/status fields.
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

## RT And Identity

- RT is contextual evidence, not a hard identity veto. It must not prove absence
  or override co-eluting, candidate-aligned MS1/MS2 evidence unless an explicit
  hard RT exclusion policy exists.
- Large, reproducible or unmodeled RT shifts may trigger ambiguity or localization
  demotion, especially when inconsistent with ISTD transfer.
- Cross-sample RT or m/z dispersion alone does not establish separate isomers,
  and lack of distinct peaks within a sample does not prove identity; inspect
  within-sample peaks and method MS2/NL evidence.
- MS1 shape alone must not dismiss a shoulder or weak peak with method MS2/NL
  support; that support does not authorize quantitation without reliable
  physical bounds.

## Writer Authority

The Targeted workbook and the Untargeted Matrix are published only by their
workflow writers, under
[Writer Transaction Authority](../architecture-contract.md#writer-transaction-authority).
Writers render finalized cell decisions; they never recompute them.

Evidence providers, candidate rows, write-ready labels, quality blockers,
sidecars, replays, review packets, galleries and diagnostics can explain or
gate a decision, but none grants writer authority. A new writer path needs its
own contract, expected diff and focused tests under the
[Expected-Diff Contract](../agent/product-validation-contract.md#expected-diff-contract).
Topic pages link here instead of restating this rule.

## No-Write Reasons

A cell decision either selects a value or returns one typed no-write reason.
The closed set is:

| Reason | Meaning |
| --- | --- |
| `not_searched` | No search ran for this cell |
| `searched_no_observation` | A completed search found no candidate peak |
| `unresolved` | Candidate evidence exists but does not resolve to one supported peak (for CID-NL, `HOLD` with candidate peaks) |
| `rejected_observation` | The method rejected the cell (for CID-NL, `REJECT`) |
| `not_quantifiable` | An observed peak cannot support a reliable quantity (below) |
| `unavailable` | The evaluation was technically unsupported or failed |

Human-facing text is rendered from these typed facts and is never parsed back
into scientific logic. Failure, ambiguity or missing positive evidence never
serializes as chemical absence, zero or a selected value.

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
either accepted value into ND by inspecting count or review. A
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

## Targeted Measurement / Reason / Counted Detection Precedence

Targeted workbook rows present retained RT, area, integration boundaries,
`NL Evidence`, and a plain-language `Reason`. The finalized cell decision owns
numeric availability. The Targeted product projection is the single owner of
counted detection and review; writers render its answer rather than deriving
another grade from source evidence.

Invariants:

- Counted detection and summaries read `projection.counted_detection` and
  `product_state`, not neutral loss flags, scores, or caps. CSV and workbook
  numeric columns render the retained finalized peak even when counting is
  false. The HTML summary median uses the retained numeric population
  independently of its detection tally.
- `_display_reason` describes the retained MS1 measurement and projection
  review reasons through `display_measurement_reason`. Without a projection,
  diagnostic rows use the stored `result.reason`. `projection.projection_reason`
  feeds the separate `Projection Reason` column.
- `NL Evidence` distinguishes observed support, no support within tolerance,
  no MS2 spectrum, and an unrequested check. Blank NL configuration is
  `Not requested`, not failed evidence.
- Internal count/product/review codes and raw NL tokens remain advanced
  evidence. They do not replace the main measurement and evidence explanation.
  Missing support or shape concerns do not themselves establish absence.
- Candidate evidence semantics describe support, conflicts and review context;
  they do not assign a second Confidence grade. Paired-ISTD RT mismatch without
  strict NL support demotes candidate localization in `candidate_selection`,
  independently of counting. The finalized paired-position policy still applies.
- New evidence sources enter as typed evidence or selection context and are
  reconciled by workflow projection, not a direct display or count authority.
- `reproject_extraction_result` preserves the same selection, projection, and
  display precedence as `build_extraction_result`.

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
| Tie-break | Typed chemical support, role/reference localization and selection evidence tier (including paired-ISTD mismatch demotion), then blocker count and measured ties: trace strength, quality, RT distance and abundance; diagnostic scores do not reselect the peak |
| Projection | `TargetedProductProjection` first owns fixed Target row binding and Target cell-subject construction; `ProductionCellDecision` then supplies the selected value/no-write result; the same projection finally owns product state, counted detection, reason, Review Queue routing, and Targeted output fields |

Targeted policy may prioritize paired ISTD/standard RT context because the
Targeted method declares that relationship ahead of time. That priority must
remain in Targeted projection or activation policy; it must not leak into
Untargeted identity decisions as if the workflow had known targets.

Targeted-specific rules to keep explicit:

- Positive MS1 means a finite selected RT and positive area. Without that, the
  row or cell must project to explicit no-peak or not-counted state, not counted
  detection by implication.
- Every configured Targeted target is evaluated for each selected analysis sample.
  Sample names do not gate targets or paired-area references. Each target
  declares its own CID-NL tag; Untargeted
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
- describe source evidence in diagnostic reasons and score breakdowns;
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

- `raw_score`, support labels, concern labels, and
  cap labels are compatibility or audit projections unless wrapped by typed
  evidence semantics and an approved product policy.
- Targeted selection consumes typed candidate facts through
  `select_candidate_by_evidence`; displayed reason fields do not
  authorize a parallel selector.
- Alignment `evidence_score` is a seed-quality score feature, not same-peak
  identity proof.

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

- [Discovery](discovery.md)
- [Targeted selection](targeted-selection.md)
- [Backfill](backfill.md)
- [Alignment](alignment.md)
- [Architecture contract](../architecture-contract.md)
