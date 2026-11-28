# Decision Policy

Decision policy turns typed evidence into workflow-owned product decisions. It
is not a scoring formula and not a shortcut from diagnostics into outputs.
Evidence explains what was observed and how trustworthy it is; score ranks
comparable candidates after the hard gates pass; workflow policy decides which
evidence is required, which conflicts block, and which projection can write or
count output.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| Who selects a cell's value? | Only the cell decision, as a value or one typed no-write reason; writers render it. | [Writer authority](#writer-authority), [No-write reasons](#no-write-reasons) |
| Can a score decide a peak? | No; it ranks candidates that already passed the same hard gates. | [Score position](#score-position) |
| Is RT an identity veto? | No; RT is contextual evidence unless an explicit hard RT exclusion exists. | [RT and identity](#rt-and-identity) |
| Does "not counted" blank a value? | No; counting, review and value are separate, and a writer cannot turn an accepted value into ND. | [Value, count and origin](#value-count-and-origin) |
| When is a Target value withheld? | When low S/N, sparse scans and no majority continuous run all hold: `not_quantifiable`. | [Targeted quantitative support](#targeted-quantitative-support) |
| What may Targeted know that Untargeted may not? | Target identity, role, window and paired standards; that priority stays in Targeted projection. | [Targeted policy](#targeted-policy), [Untargeted and Backfill policy](#untargeted-and-backfill-policy) |

## Evidence And Gates

- **Evidence chains over single metrics.** RT, CWT, WIS, iRT, local minima, RT
  models, shape similarity, product ions, neutral losses, adducts and
  in-source fragments are evidence inputs; none silently overrules the
  selected peak or Matrix identity on its own. An evidence source that changes
  production behavior needs explicit config or contract and machine-readable
  reason/status fields.
- **Providers feed typed facts.** Providers write into `EvidenceVector`,
  `EvidenceDecisionSemantics`, `PeakHypothesis`, `IntegrationResult` and audit
  records, never product matrices.
- **Gate order.** Decision policy owns gate order. `ProductionCellDecision` is
  the sole owner converting closed observation evidence into a selected value
  or typed no-write. A rule affecting selected peak, area, counted detection,
  product state, Matrix value or output identity must be explicit and
  test-covered.
- **Score.** A high score cannot bypass missing required evidence, hard
  conflicts, unresolved ambiguity, missing provenance or activation gates.
- **Shared value owner.** Targeted and Untargeted share the value/no-write
  owner. Detection-count policy must not discard a numeric value from the same
  closed evidence. Each workflow owns its search/row context and its
  inclusion, count and review projection.
- **Diagnostics.** Diagnostic, shadow, gallery, review, lockbox and sidecar
  evidence can explain a decision; it becomes product authority only through
  an explicit activation/export contract and expected-diff gate.

## RT And Identity

- RT is contextual evidence, not a hard identity veto. It must not prove
  absence or override co-eluting, candidate-aligned MS1/MS2 evidence unless an
  explicit hard RT exclusion policy exists.
- Large, reproducible or unmodeled RT shifts can trigger ambiguity or
  localization demotion, especially when inconsistent with ISTD transfer.
- Cross-sample RT or m/z dispersion alone does not establish separate isomers,
  and lack of distinct peaks within a sample does not prove identity; inspect
  within-sample peaks and method MS2/NL evidence.
- MS1 shape alone must not dismiss a shoulder or weak peak with method MS2/NL
  support; that support does not authorize quantitation without reliable
  physical bounds.

## Writer Authority

Only the decision owner selects a cell's value and state. A writer renders that
decision; it does not recompute it, and it does not take a value or state from
review rows, manifests, counts or diagnostics.

The Targeted workbook and the Untargeted Matrix are published only by their
workflow writers; Targeted and Untargeted keep their own writers. Output is
published only after the write completed; a failed write leaves the previous
output in place.

Evidence providers, candidate rows, write-ready labels, quality blockers,
sidecars, replays, review packets, galleries and diagnostics can explain or
gate a decision, but none grants writer authority. A new writer path needs its
own contract, expected diff and focused tests under the
[Expected-Diff Contract](../agent/product-validation-contract.md#expected-diff-contract).

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

## Value, Count And Origin

| Concept | Owner and meaning | Public consequence |
| --- | --- | --- |
| Numeric or no-write | `ProductionCellDecision`, from closed observation and integration evidence | Finalized RT/area/bounds are rendered; a typed no-write has no numeric output |
| `accepted_cell_count` | Number of cells accepted for numeric output in the completed Target or Untarget projection | Includes accepted MS1-only cells; is not a direct-detection count |
| Counted detection | Explicit evidence/count policy | Controls detection statistics and trusted-reference consumers, not whether an accepted value is rendered |
| Direct observation / Backfill | The actual capture or Backfill observation evidence | A usable MS1 value does not imply that a Backfill operation ran or that NL was observed |
| NL observation | Configured provider result | Missing NL or missing MS2 is visible evidence context; blank NL configuration remains unconfigured |
| Review | Retained reason and review state | Review can coexist with a numeric value |

- Same-feature MS1 Backfill can be numeric without becoming a direct
  detection. A group can ask its Backfill question of an already measured MS1
  peak: it reuses the measurement, not the Discovery decision, leaves the
  original CID/NL evidence unchanged, and competes for row ownership like a
  newly measured Backfill.
- An observed Target-local peak can be numeric while its count policy is
  false. A writer must not turn either accepted value into ND by inspecting
  count or review.
- A Target definition or matching search window alone does not mint group
  membership or a Backfill receipt. Observation origins stay explicit rather
  than renamed to imply an operation that did not run.

### Targeted quantitative support

For a Target cell, an observed integral is not automatically fit for numeric
publication. The cell decision returns `not_quantifiable` when all three hold:
the retained signal quality reports low S/N, fewer than half of the selected
raw XIC scans are nonzero, and no continuous run contains at least half of
those nonzero scans.

- Low S/N alone, sparse outer flanks around a continuous body, or missing
  quality metadata do not establish this combined failure. These majority
  conditions are an operational support policy, not a calibrated LOD/LOQ or
  chemical-absence claim.
- The selected integration, original trace and method evidence stay retained.
  NL or an ISTD identity cannot override inadequate MS1 support.
- The rule applies only to Target decisions. The writer renders it as blank
  numeric cells with the `不足以定量` label, not zero or ND, without
  re-evaluating the signal.

## Targeted Policy

Targeted selection is hypothesis-driven: the workflow knows target identity,
role, window, expected MS2/NL behavior and configured paired standards. That
knowledge is part of the assay contract, not a generic evidence provider, and
its priority on paired ISTD/standard RT context stays in Targeted projection
or activation policy; it must not leak into Untargeted identity decisions.

| Decision role | Targeted rule |
| --- | --- |
| Unit | Candidate `PeakHypothesis` for a known target/sample pair |
| Required evidence | Selected candidate or explicit no-peak state, target identity, positive MS1 state when counted, and provenance-valid trace context |
| Hard blockers | Invalid selected hypothesis, hard wrong-peak conflict, missing positive MS1 for counted detection, role-incoherent paired context |
| Support | Candidate-aligned MS2/NL, target RT window, paired ISTD/standard RT context, paired area ratio, morphology, boundary assessability, local S/N |
| Tie-break | Typed chemical support, role/reference localization and selection evidence tier (including paired-ISTD mismatch demotion), then blocker count and measured ties: trace strength, quality, RT distance and abundance; diagnostic scores do not reselect the peak |
| Projection | `TargetedProductProjection` first owns fixed Target row binding and Target cell-subject construction; `ProductionCellDecision` then supplies the selected value/no-write result; the same projection finally owns product state, counted detection, reason, Review Queue routing, and Targeted output fields |

- **Positive MS1** means a finite selected RT and positive area. Without it,
  the row or cell projects to an explicit no-peak or not-counted state, never
  counted detection by implication.
- **Every target is evaluated** for each selected analysis sample. Sample names
  do not gate targets or paired-area references. Each target declares its own
  CID-NL loss; Untargeted method-level neutral-loss selection does not restrict
  the list.
- **ISTD and analyte are not symmetric.** A configured ISTD's plausible
  dropout can count as `detected_flagged`; an analyte NL fail or missing MS2
  needs paired support to count. This is count/trusted-reference eligibility,
  not a second quantification: neither branch can discard the cell decision's
  value or manufacture one when it returned no-write.
- **Paired analyte support is a bundle:** coherent MS1, role-aware RT context,
  paired-area support, same-peak support and no hard quality blocker. No single
  paired fact is standalone authority.

### Labels and counted detection

Targeted rows present retained RT, area, boundaries and the fixed labels `依據`
and `疑點`. The cell decision owns numeric availability; the Targeted product
projection alone owns counted detection and review, and writers render its
answer rather than grading source evidence again.

- Counted detection and summaries read `projection.counted_detection` and
  `product_state`, not NL flags, scores or caps. CSV and workbook numeric
  columns render the retained peak even when counting is false. The HTML
  summary median uses the retained numeric population independently of its
  detection tally.
- `result_labels` maps saved projection codes and the selected peak's NL
  observation to one `依據` label and zero or more `疑點` labels. Every
  savable reason code has a label or is declared hidden; an unknown code fails
  the write instead of disappearing. `projection.projection_reason` feeds the
  separate `Projection Reason` column.
- When identity comes from the paired ISTD or Backfill, the missing NL or MS2
  stays a review reason, read as a `疑點`, without changing counting or value.
- A target without a configured neutral loss has no NL label; its identity is
  `僅 MS1 峰` or its specificity clauses.
- Internal count/product/review codes and raw NL tokens are advanced evidence;
  they do not replace the main measurement and explanation. Missing support or
  shape concerns do not establish absence.
- Candidate evidence semantics describe support, conflicts and review context;
  they assign no second Confidence grade. Paired-ISTD RT mismatch without
  strict NL support demotes candidate localization in `candidate_selection`,
  independently of counting; the paired-position policy still applies.
- New evidence sources enter as typed evidence or selection context reconciled
  by the projection, not as direct display or count authority.

## Untargeted And Backfill Policy

Untargeted alignment is discovery-driven: without known targets, CrossSample
decides from trace, owner, group, identity and provenance evidence, not
Targeted labels. Backfill is a separate bounded revisit owner (the
`ObservationRevisit` receipt) and decides no membership, value or row
inclusion.

| Decision role | Untargeted / Backfill rule |
| --- | --- |
| Unit | Separate decisions for owner edge, cross-sample group/disposition, ObservationRevisit, Untarget row identity, Group cell value/no-write, and activation output |
| Required evidence | Explicit discovery/alignment input, owner construction, row identity, typed same-peak evidence when claiming same-peak support, and provenance-valid sidecar when used for product support |
| Hard blockers | Ambiguous owner, duplicate loser, wrong-peak conflict, missing matrix identity, stale source hash, unsupported family projection, unapproved authority scope, missing expected-diff gate for new writer behavior |
| Support | Cross-sample coherence, m/z/RT consistency, MS1 pattern, shape and boundary agreement, MS2/NL consistency, standards/library evidence when represented as typed facts |
| Tie-break | Owner-edge evidence, group match quality, drift-corrected RT, m/z/product tolerances, seed support, owner quality, trace quality, duplicate context, deterministic row ordering, and score only within already-eligible comparable candidates |
| Projection | Untarget projection first decides typed row identity and Group cell subjects; `ProductionCellDecision` then decides cell value/no-write; the same projection closes row inclusion and archive routing before the writer or activation renders or publishes. Backfill supplies observation facts only |

- Targeted outputs can serve as benchmark, audit or falsification evidence for
  Untargeted behavior, but promote no Untargeted row unless a separate
  contract turns that evidence into typed same-peak, group-hypothesis or
  `PeakHypothesis` support.
- Owner-edge score supports group construction only after hard gates pass. It
  cannot override same-sample conflicts, tag mismatch, m/z/product/NL
  mismatch, ambiguous owner, identity conflict, non-detected owner or
  Backfill-bridge blockers.
- Row promotion needs row identity support and at least one quantifiable
  detected seed. Backfill-only, duplicate-only, ambiguous-only, review-only,
  consolidation-loser and zero-detected rows fail closed unless an explicit
  authority contract says otherwise.
- A Backfill observation writes only after row identity passes and the cell
  decision independently selects a quantifiable value. If revisit evidence is
  required, the exact owner-backed revisit receipt must be present; a revisit
  never grants membership.
- Same-peak support is not group membership. It needs selected peak geometry,
  RT or drift-supported compatibility, no MS1 pattern conflict, no hypothesis
  or claim blocker, and product-authorized trace or pattern support.
- MS2/NL, MS1 pattern, QC reference, sample-matrix RT drift and shape or region
  audit are typed facts that strengthen or block decisions only through
  Backfill projection and authority fields.
- `feature_id` is a display label; the group and hypothesis objects are the
  identity handles.
- Broad Backfill stays gated. Diagnostic sidecars and visual overlays are not
  production readiness by themselves.

## Score Position

Score can:

- rank candidates that already passed the same hard gates;
- describe source evidence in diagnostic reasons and score breakdowns;
- calibrate thresholds for future policy, with expected-diff review before any
  public output changes;
- summarize quality within a typed fact, such as shape or MS2 similarity, when
  threshold and provenance are explicit;
- act as one seed-quality feature in alignment while seed events, scan
  support, identity constraints and other gates still pass.

Score cannot:

- make a candidate product truth by itself;
- override wrong-peak, ambiguity, missing-provenance, sample-role or authority
  blockers;
- replace a lower-score candidate when typed facts give the higher-score
  candidate worse decision semantics;
- collapse Targeted and Untargeted policy into one global weighting;
- turn diagnostic-only or shadow-only artifacts into Matrix values;
- hide a new product rule behind a numeric threshold without an owner, tests
  and expected-diff framing.

An unavoidable threshold is named as a gate with a reason and validation tier,
not a universal weight. Names containing `score` need care: `raw_score`,
support, concern and cap labels are compatibility or audit projections unless
wrapped by typed evidence semantics and an approved policy; Targeted selection
consumes typed facts through `select_candidate_by_evidence`, and displayed
reasons authorize no parallel selector; Alignment `evidence_score` is a
seed-quality feature, not same-peak proof.

## Decision Records

A new or promoted decision path must emit or preserve enough to reconstruct
its policy:

| Field class | Examples |
| --- | --- |
| Unit identity | `target_label`, `sample_key`, `feature_id`, `(feature_id, sample)` cell position |
| Required evidence status | present/missing/inconclusive typed facts and source artifact hashes |
| Blockers | conflict, ambiguity, stale source, unsupported projection, missing activation |
| Support | RT, shape, MS2/NL, standard/library, paired-context, group/coherence support |
| Tie-break basis | score, RT distance, abundance, deterministic order, compatibility fallback |
| Projection authority | ProductionCellDecision, Targeted/Untargeted projection, writer scope |
| Validation status | synthetic, focused tests, Targeted benchmark, 8RAW, 85RAW, manual review, expected diff |

- The record can live in different artifacts per workflow, but its meaning is
  stable: evidence explains, policy gates, score ranks, the cell decision
  selects a value or no-write, projection includes, and the writer renders.
  `Trace` means an LC-MS chromatogram, never a decision record.
- `DecisionRecord.gate` and `DecisionRecord.tie_break` are audit-visible policy
  terms, not a generic selection key; only workflow-owned selection modules
  turn them into ordering keys. A promoted workflow keeps them in its owning
  record, sidecar or output when they explain behavior; not every review TSV
  emits a complete `DecisionRecord`.
- `DecisionRecord.projection_authority` names the workflow owner allowed to
  project or count the result; it is not an evidence token, score field,
  shadow label or writer name. A new authority label needs the same
  expected-diff framing as any behavior-changing writer contract.

## Red Lines

- Do not say "the score decided the peak." Say which typed facts passed, which
  blockers were absent, and which projection wrote the output.
- Do not let a shared evidence provider carry workflow-specific authority.
- Do not move Targeted-only role policy into shared evidence semantics unless
  the output stays role-neutral or the projection layer still owns authority.
- Do not treat review labels, display tokens, family labels, galleries or
  lockbox labels as product writes.
- Do not count multiple derived facts as independent evidence when they come
  from the same physical observation.
- Do not change selected peak, selected area, counted detection, Matrix values,
  identity sidecars or output schemas without expected-diff framing and
  focused output tests.

## See Also

[Discovery](discovery.md) · [Targeted selection](targeted-selection.md) ·
[Backfill](backfill.md) · [Alignment](alignment.md) ·
[Architecture](../architecture/README.md)
