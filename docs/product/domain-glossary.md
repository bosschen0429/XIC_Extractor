# XIC Extractor Domain Glossary

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/domain-glossary.md
Doc exit rule: Update when stable vocabulary, lane boundaries, or authority concepts change.

This file is the single stable vocabulary owner for XIC Extractor. Keep
volatile productization status, counts, current tier assignments, and current
blockers in the status index, authority manifest, and active handoff, not here.

## Product Shape

XIC Extractor is an LC-MS evidence and model-selection system. It should make
peak choice, integration, review, and export decisions traceable through
evidence rather than through dataset-specific shortcuts.

The three core product stages are **Discovery → Alignment → Backfill（回填）**.
Backfill is the canonical name for the bounded missing-cell revisit, in product
text and in implementation identifiers. `ObservationRevisit` describes the local
revisit, not a separate receipt API. Backfill is conditional: cells with sufficient
existing observations need no revisit.

XIC has one shared scientific processing lifecycle. Untargeted extraction is
its general application; Targeted extraction is a constrained application,
not a second peer core:

`SearchScope -> provider-specific Discovery/capture -> shared observation/evidence contract -> PeakHypothesis/owner evidence -> constrained CrossSample grouping -> stable CrossSampleGroupingResult -> optional bounded Backfill/ObservationRevisit -> FinalizedScientificAlignment`

Target then binds its fixed row, selects cell answers and completes projection.
Untarget selects Feature × Sample answers before projecting rows/cells. Both
finish through the existing result finalizer.

- Both applications may use a broad candidate-discovery policy. Here, `broad`
  describes which evidence relationships the method may inspect, such as dR,
  R, or MeR; it does not mean Untargeted-only. Targeted narrows the allowed
  target/row universe, not necessarily the evidence-provider mechanics used to
  find a candidate.
- Untargeted searches the configured evidence space, retains all qualifying
  finalized groups, and projects established groups into the quantitative
  feature-by-sample matrix for downstream preprocessing, calibration, and
  statistics.
- Targeted supplies an exact `TargetListSnapshot` and a constrained
  `TargetSearchScope`. It retains one fixed output row per `TargetKey` and
  discards unrelated groups from that delivery. It does not require a second
  peak-capture, cross-sample, trajectory, Backfill, or cell-decision core.

Discovery providers such as CID-NL, HCD-PI, and Delta Mass interpret their
method-specific inputs and produce sample-local evidence and hypotheses through
the shared contract. Downstream owners may use typed provider provenance,
capabilities, or an explicit interpretation/integration strategy when required;
shared does not mean method-blind. CrossSample grouping owns group membership,
identity, disposition, and lineage. It returns one immutable
`CrossSampleGroupingResult`; the same object is consumed before bounded
Backfill and through finalization. Backfill uses an already established Target or group
context to make one bounded revisit of a missing cell and may
return an observation or typed no-observation result. The current Untargeted
CID-NL production route uses the conservative Backfill decision and completed
cell evidence. Backfill may
produce the first usable observation for that cell,
but it cannot originate the Target/group, mint membership, or select a
product value.
The workflow-owned projection first creates typed Target/Group row bindings and
cell subjects; `ProductionCellDecision` alone selects the value or no-write
result; the same projection then owns row inclusion, counts/reasons, Review
Queue/archive routing, and delivery shape. These are two ordered operations of
one projection owner, not two owners.

Targeted and Untargeted may apply different search scopes, priors, projection
rules, application flows, and output contracts. Evidence or output authority from one
projection does not grant write authority in the other without an explicit
contract, expected-diff evidence, and focused tests. Different projections do
not imply different capture, group, ObservationRevisit, or cell-decision
implementations.

## Evidence Spine

Use these terms consistently:

- `Trace`: intensity over retention time for a feature, target, or candidate.
- `TraceGroup`: related traces that should be compared together.
- `PeakHypothesis`: a proposed peak identity, retention-time window, and
  integration basis.
- `selected hypothesis`: the chosen `PeakHypothesis` and its audit context.
  Selection identifies the candidate used by a workflow projection; it does
  not by itself grant counted-detection or writer authority.
- `EvidenceVector`: structured evidence used for scoring or model selection.
- `EvidenceDecisionSemantics`: typed interpretation of evidence support,
  conflict, exclusion, ambiguity, or review meaning. It is not product-write
  authority.
- `IntegrationResult`: selected boundaries, area, and integration diagnostics.
- `DecisionRecord`: audit/explanation record of how evidence, policy gates,
  blockers, tie-breaks, and projection authority produced a decision. It is not
  an LC-MS `Trace` and not analytical evidence.
- `AuditTrail`: reproducible record of how evidence became a decision.
- `CrossSamplePeakGroupHypothesis` (`cross-sample group hypothesis`): successor
  cross-sample owner/group identity, exposed through `group_hypothesis_id`. It
  does not own per-peak integration truth or product projection.
- `CrossSampleGroupingResult`: one immutable request-bound result containing
  the exact processing owners, accepted complete-partition transition, and
  prepared drift lookup. Initial members stay fixed through bounded Backfill;
  CrossSample alone can admit unique missing-sample SUPPORT and return the
  successor result used for cell decisions and finalization.
- `CrossSamplePartitionTransition`: the CrossSample-owned mathematical
  before/after transition between two complete group partitions. It is the
  application record for accepted initial refinement or bounded membership
  admission, not a mutable
  successor context or a second grouping lifecycle.
- `CID-NL method subject`: the exact method-specific acquisition/assessment
  question retained in an assessment's provenance. It is not a cross-sample
  feature or a Matrix row.
- `CID-NL alignment workset`: compatible method subjects scheduled and composed
  together so their observations can reach the same CrossSample decision.
  Workset membership is computational scope, not chemical identity. Alignment
  owns its compatibility and Backfill-binding policy in [alignment.md](alignment.md).
- `identity-bearing member subject`: canonical sample identity plus an
  owner-backed `PeakHypothesis` identity and its physical observations. The
  kind tag is part of identity; equivalent hypothesis wrappers over the same
  physical observation canonicalize to one subject.
- `CrossSampleGroupDisposition`: the CrossSample-owned, mutually exclusive
  disposition of one finalized group: `established`, `provisional`,
  `unresolved`, or `rejected`. Disposition does not decide output inclusion.
- `ObservationRevisit`: one bounded local Backfill
  revisit of a pre-existing Target or group cell. The revisit may produce its
  first usable observation.
  It is not imputation, group membership, or product-value authority.
- `ProductionCellDecision`: the output representation of the cell-decision
  owner's numeric value or typed no-write, selected integration and provenance.
  Target decisions use `TargetCellSubject`; Untarget decisions are completed
  from `FinalGroupCellObservation` before projection adds `GroupCellSubject`
  and the published ID. Observations do not select delivered values themselves.
- `FinalizedScientificAlignment`: immutable shared core containing the stable
  grouping result, dispositions, membership/lineage, and completed cell evidence
  needed by a workflow projection. It is not an output artifact or mutable
  Matrix.
- `TargetRowBinding`: Target projection decision that binds one fixed
  `TargetKey` row to the eligible finalized groups needed by its sample cells.
  Multiple groups are unresolved only when they compete for the same sample
  cell. It does not rescan RAW or rank peaks.
- `workflow projection`: the workflow owner of output row/cell identities,
  inclusion/count/reason and Review Queue/archive routing. Target binds its fixed
  row before cell decisions; Untarget projects completed Feature × Sample
  answers once. Neither projection selects the cell value.
- `UntargetRowKey`: Untargeted projection-owned row identity derived from one
  finalized canonical group. It is distinct from `group_hypothesis_id` and
  from legacy `FAM...` display labels.
- `final Matrix`: the immutable, canonical grid produced only after cell and
  row-inclusion decisions close. Pre-final observations or owner matrices must
  use a different typed name and must not cross the final Matrix interface.
- `ChromPeakSegment`: scoped product-candidate boundary unit used by
  quantitation-context decisions.
- `ProductWriter`: the exclusive approved code path for product-matrix
  mutations; see [Writer Authority](productization.md#writer-authority).

Evidence providers can include CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 shape,
standards, library matches, trace overlays, review labels, and future models.
They feed the evidence spine.

Related evidence and analysis terms:

- `Discovery record`: a retained sample-local observation and its qualification
  result. A record in the Discovery candidate CSV does not by itself qualify
  as a feature for cross-sample grouping. The persisted population contains all
  MS1-feature-backed candidates and non-feature observations whose precursor
  basis is `scan_precursor` or `mixed`. Physical facts persist only for the
  feature-backed population.
- `Alignment feature candidate`: a Discovery observation with eligible MS1
  feature support. Its physical peak may participate in cross-sample grouping;
  an NL match alone does not grant that standing.
- `review-only NL observation`: retained NL evidence without current eligible
  MS1 feature standing. `review_only_orphan_nl` includes both observations
  without an accepted MS1 peak and records demoted by blank qualification;
  the state is not proof of random noise. A locally relevant MS2 observation
  can support an existing feature without becoming a grouping candidate.
  Non-feature `product_plus_neutral_loss` observations, including ambiguous
  co-isolation observations, are classified in memory but are not persisted.
- `sidecar`: accompanying TSV, JSON, or similar metadata that explains or
  audits a primary output.
- `provenance`: the inputs, effective settings, runtime, and decision path
  needed to identify or reproduce how an artifact or decision was produced.
- `seed evidence`: initial MS1/MS2 evidence that triggers candidate generation
  in Discovery. A seed is not same-peak proof or counted detection.
- `family`: legacy review/search container for related candidate or aligned
  evidence. Family membership is not same-peak proof or product authority.
- `canonical projection`: the workflow-specific mapping from completed owners
  to output identities, inclusion, counts/reasons, routing, and product output.
  Target retains its fixed-row binding before cell decisions; Untarget completes
  Feature × Sample answers before assigning row/cell identities. Their
  projections remain separate; neither is the other's oracle.
- `morphology`: peak-shape characteristics such as width, symmetry, boundaries,
  and quality flags used as evidence for selection or integration.
- `evidence-chain packet`: structured source evidence assembled for review,
  validation, or approval. The packet is evidence, not approval itself.
- `RAW-tier evidence label`: validation-depth label naming the actual
  lane-specified fixture or real-sample review used for a claim. Historical
  CID-NL 8RAW/85RAW fixtures are not universal gates for Pt pattern or other
  methods.

Use family and instance names consistently. `delta_mass` is a Discovery strategy
family for MS1 delta-pattern evidence; `pt` / `Pt pattern` is the first concrete
pattern in that family; `pt_pattern` is a user-facing method or preset label.
Composites such as `pt_pattern + cid_nl(dR)` intersect evidence providers and
do not become product authority by themselves.

For Pt pattern, distinguish the science target from the current operational
proxy. The science target is a single Pt isotope envelope represented by
base/+1/+2/+4 positions. The operational proxy is the observable evidence shape:
those four positions appear as a sample-local full pattern and pass close-apex
co-elution support. Ratio fit, isotope-envelope overlap, and co-eluting
interference are unresolved review evidence until a bounded oracle proves how
they should change the decision. In V1, a close-apex full-pattern candidate can
still be called `detected` when ratios look unusual; ratio and collision
concerns explain ambiguity and review priority, not the detected call itself.

## LC-MS/MS Terms

- `XIC`: extracted-ion chromatogram, the signal intensity over time for a
  specified m/z window.
- `CID-NL`: Collision-Induced Dissociation Neutral Loss, a fragmentation
  pattern used as Discovery seed evidence. Targeted may constrain its candidate
  universe while Untargeted applies it broadly.
- `Delta Mass`: Discovery strategy family for configured MS1 mass-difference
  patterns.
- `Pt pattern`: the first built-in `delta_mass` pattern for Pt-related MS1
  feature discovery. It creates candidates and review evidence.
- `HCD`: Higher-energy Collision Dissociation. Its product-ion audit is a review
  surface unless a separate contract grants stronger decision meaning.
- `DDA`: Data-Dependent Acquisition, where MS2 scan triggering depends on MS1
  observations and instrument acquisition policy.
- `Batch RT warp`: a frozen per-injection mapping fitted from agreed bootstrap
  anchors across the full processing request, with residual neighbors restricted
  to the declared acquisition batch. Execution partition size does not define
  its scientific scope. Unsupported calibration is not measured zero drift.
- `Local feature RT trend`: the RT of one feature estimated from its nearby
  injections, distinct from the shared RT-dependent sample warp.
- `Backfill query RT`: the target sample's observed-coordinate request center.
  It controls extraction bounds and preferred peak location; it does not replace
  the frozen group's `group_center_rt`.
- `ISTD`: internal standard, a spiked reference used for RT calibration,
  paired-area context, and method QC.
- `NL`: neutral loss, the mass difference between a precursor ion and a product
  ion after fragmentation.
- `sample matrix`: the chemical background of a sample, such as a clean
  solvent standard or a biological tissue extract. A sample-matrix effect
  changes RT or response. Do not shorten it to `matrix`; `Matrix` is the
  product output.
- `SDO/LEK`: the lab's fixed clean-standard set for instrument checks. The set
  contains two standards, SDO and LEK. Code and file names spell it `sdolek`
  (for example `run_sdolek_pipeline`, `instrument_qc_sdolek_trend.tsv`).

## Product Authority Terms

- `candidate/audit universe`: rows that are visible for review, audit, or
  diagnosis. This is not the same as writable product output.
- `write_ready`: a row-level `policy_decision` value in the Backfill
  generated-policy artifact. It names rows a writer scope may cover; the
  label grants no write authority unless that scope is registered in the
  authority manifest.
- `authority`: the permission boundary for matrix/workbook/value writes;
  [Writer Authority](productization.md#writer-authority) owns how it is granted.
- `authority manifest`: machine-checkable record of the exact ProductWriter
  scopes and cells currently authorized to write.
- `maturity tier` (preferred term: `lane readiness status`): persisted current
  state for a structured product lane. The seven-value vocabulary is owned by
  `docs/agent/product-validation-contract.md` and enforced by the productization
  control-plane schema; current assignments live in the status index.
- `validation evidence status`: source-qualified claim about what a branch,
  run, or evidence packet verified. Its Evidence labels are owned by
  `docs/product/README.md` and are not persisted in the productization status
  index.
- `artifact contract validation ceiling`: owner-scoped invariant limiting what
  a typed artifact can claim. A fixed value such as `shadow_ready` paired with
  no product authority is not lane state or a promotion transition.
- `owner-specific gate verdict`: a gate-local or compound token such as
  `contract_ready_science_inconclusive`. It is interpreted only by its owner;
  it is not a member of either shared status vocabulary.
- `activation contract`: explicit evidence, tests, authority, and expected-diff
  conditions required before diagnostic or shadow behavior may affect product
  values.
- `parity`: requirement that a replacement preserve the declared observable
  contract unless an explicit expected diff authorizes a change.
- `promotion gate`: the evidence and approval conditions required before a
  capability advances to a stronger maturity or authority state.
- `active lane`: the current priority development area and WIP assignment
  recorded in the status index and current branch handoff.
- `adjudication`: machine classification of a candidate. It can route work, but
  it is not by itself permission to write product output.
- `review packet`: a structured human-review surface. It is not free-form value
  entry.
- `truth lockbox` (`lockbox`): independent peak-choice or area labels used to
  evaluate future automation. Lockbox labels do not automatically mutate
  product output.
- `expected-diff`: explicit evidence that a public output change is intended,
  bounded, and test-covered.

Treat diagnostics, sidecars, and reports as observability unless an authority
contract says otherwise.

## Shared Contract Identifiers

- `sample_metadata`: canonical schema for sample identity, injection order,
  role, batch, and QC context.
- `review_action`: typed action-import schema for converting human review
  decisions into machine-checkable plans without silently editing product
  values.
- `validation-minimal`: lightweight Alignment validation output profile. It is
  an artifact surface, not production-readiness evidence by itself.

## Processing Lineage Terms

These names describe both current identity models and reserved delivery
contracts. See [Result Traceability](processing-lineage.md#current-status) for
implementation status; defining a term does not establish product capability.

- `AcquisitionSource`: immutable identity for one RAW, mzML, or other
  acquisition source and its native observation namespace. A path or sample
  display name is not sufficient identity.
- `ProcessingRequest`: one normalized scientific computation request over
  identified sources, effective evidence/method settings, workflow
  constraints, and contract versions.
- `ProcessingAttempt`: one execution of a `ProcessingRequest`. Retries create
  new attempts and never overwrite earlier attempt identity.
- `EvidenceArtifact`: one immutable typed evidence instance identified by its
  producer attempt, semantic role, schema, and content digest.
- `DeliveryCellKey`: projection-owned stable Targeted cell position under the
  [delivery identity contract](processing-lineage.md#delivery-identities), not
  a kernel observation or an ordinal group label.
- `TargetedDeliveryCellKey`: Targeted name for `DeliveryCellKey`. Exact
  target-definition and projection-policy versions belong to the delivery
  revision, not the stable cell position.
- `UntargetedDeliveryCellKey`: not part of the planned delivery contract; see the
  [Untargeted delivery decision](processing-lineage.md#workflow-specific-delivery).
- `DeliveryRevision`: immutable proposed or sealed Targeted value/null, linked
  to the producing attempt and kernel decision.
- `DeliverySeal`: immutable authority-approved manifest for Targeted revisions
  or a complete Untargeted Matrix and retained ledger; see
  [delivery identities](processing-lineage.md#delivery-identities).

These terms are internal lineage identities, not extra user-visible review
states. Avoid the overloaded term `run_id` when the intended meaning is
request, attempt, artifact bundle, or delivery revision.

## Lane Boundaries

Targeted lane:

- Target lists and method settings constrain the shared lifecycle; they do not
  create a separate peak-identity or Backfill system.
- Limited rescue workflows may produce `detected_flagged` when explicitly
  contracted.
- Treat the Targeted result as its own delivery contract. Do not require an
  analytical downstream reader when no such product consumer exists.
- Do not expand Targeted rescue to new analytes or default extraction behavior
  without expected-diff evidence and a product decision.

Untargeted lane:

- The feature-by-sample quantitative matrix is the downstream analytical
  handoff. Its sample identity and order may share method-document-derived
  metadata with Targeted delivery without sharing its output schema.
- ObservationRevisit must stay bounded and owner-backed. Broad Backfill
  diagnostics or blockers cannot select values merely because they classify
  rows; only `ProductionCellDecision` and the owning projection can authorize a
  delivery value.
- Review and lockbox assets are designed to reduce manual ambiguity and build
  truth evidence before new automation writes product output.

Shared lane:

- Sample metadata, alignment metadata, QC roles, blanks, batches, and matrix
  annotations may be surfaced as metadata. They must not change quant output or
  counted detection without a separate expected-diff gate.
- Public output surfaces include CLI flags, config keys, TSV/CSV schemas,
  workbook sheets, run metadata, matrix identity, selected peak, selected area,
  and counted detection.

## One Word, One Meaning

A word earns its place in an identifier when removing it changes the meaning.
New internal names use one word per concept. Persisted spellings below remain
stable because they identify existing data or public interfaces.

| Word | Meaning | Not this word |
| --- | --- | --- |
| `backfill` | the bounded missing-cell revisit stage; the missing-cell definition per workflow is in [Shared Processing Kernel](shared-processing-kernel.md#backfill) | — |
| `Matrix` | the finalized product-facing numeric feature-by-sample grid; see [Quant Matrix](quant-matrix.md) | a sample matrix; a pre-final cell collection |
| `sample matrix` | the chemical background of a sample (clean standard, biological extract) | the product `Matrix` |
| `SDO/LEK` | the lab's two-standard instrument-check set (SDO and LEK); `sdolek` in code | `SDOLEK` in prose |
| `revisit` | one context-directed Backfill attempt, represented by the retained `ObservationRevisit*` receipt types | a repair of a previously observed peak |
| `recovery` | resuming or closing a run after a runtime failure (`recover_after_coordinator_loss`, `recovery_marker`) | Backfill; candidate search; oracle recall |
| `relaxed search` | the peak-detection search near a preferred RT with relaxed thresholds (`peak_detection/relaxed_search.py`) | — |
| `recall` | the fraction of oracle reference compounds a run finds | — |

The `recovery`, `repair`, and `rescue` words must not describe Backfill in
prose either. Use them only in the senses in this table or in the preserved
spellings below.

The surviving `owner` senses are the sample-local MS1 peak to which candidates
are attributed (`SampleLocalMS1Owner`, `owner_apex_rt`), processing authority
(`processing_owners`, `capture_owner`), and module responsibility
(`owner_build_xic_backend`). The adjective formerly attached to internal
Backfill types, functions, constants, and modules adds no meaning and is removed.

`physical` means the MS1-measured ion or chromatographic peak itself, as opposed
to CID-NL/MS2 evidence about it. It never implies chemical identity or CID-NL
support. The word names four views of the same MS1 peak in one sample, so a
name should say which view it means:

| Name in code | Plain meaning | Kind |
| --- | --- | --- |
| physical peak (`PeakObservationRef` content, see [evidence spine](evidence-spine.md)) | the MS1 peak itself | a thing |
| `peak_detection/physical_peak_*` evaluation | finding and judging that peak on its XIC | a step |
| `discovery/physical_fact_*`, `discovery_physical_facts.json` | Discovery's saved copy of that judgement (see [Discovery](discovery.md)) | a record |
| `physical_mz` | the measured MS1 mass used to extract the peak; not the precursor key that replays MS2 evidence | a number |

Persisted and public exceptions retain their exact spelling:

| Spelling | Why it remains |
| --- | --- |
| `preferred_rt_recovery`, `recovery_claim_label` | Existing proposal-source and oracle labels |
| `xic.observation-repair-*`, `observation_repair_*`, `repair_epoch`, `no_repair`, `repaired` | Typed identity prefixes, schema names, fields, and hash inputs in retained revisit receipts |
| `owner_backfill_xic_backend`, `owner_backfill_window_strategy`, `owner_backfill_superwindow_span_factor`, `owner_backfill_min_detected_samples` | Existing config and run-metadata keys |
| `owner_backfill_cell_content_id`, `owner_backfill_peak_fact_provenance_row_count`, `owner_backfill_peak_fact_provenance_tsv`, `owner_backfill_evaluation_coverage`, `owner_backfill_candidate_audit_rows` | Content identity, metadata, and public run/output fields |
| `owner_finalized_cell_decision`, `owner_finalized`, `owner_rehydrated`, `owner_bounded_continuity` and their longer diagnostic forms | Existing metadata, decision, and diagnostic values |
| `alignment_owner_backfill_*`, `owner_backfill_revisit`, `alignment.owner_backfill` | Existing artifact names, source labels, and timing stage |
| `rescue_quantifiable`, `rescued_cell_*`, `cross_mode_rescue_*`, `q_rescue` | Existing quality, diagnostic TSV/JSON, and decision-record keys |
| `ms1_feature_nl_rescued` | Discovery NL evidence state, not a Backfill cell status |

The MS2 boundary search's `boundary_rescue` names a distinct peak-detection
fallback and stays outside Backfill vocabulary.

## Maintenance

Update this file when a stable domain term, lane boundary, or authority concept
changes. Do not update it for every validation result, count, temporary blocker,
or current task status.

Do not create another hand-maintained product glossary. A user-facing glossary,
if needed, belongs under `docs/user/` as a reader-specific projection that links
back to this vocabulary owner.

Use the productization status index, authority manifest, promotion contract,
and current handoff for live state. Use ADRs for durable architecture decisions
that need rationale and alternatives.
