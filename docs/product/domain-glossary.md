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

XIC has one core method lifecycle. Untargeted extraction is its general form;
Targeted extraction is a constrained specialization of that method, not a
second peer core:

`SearchScope -> Discovery/capture -> PeakHypothesis -> CrossSample -> ModeTrack proposal/application -> optional ObservationRepair -> CrossSample reconciliation -> optional one final ModeTrack proposal/application -> FinalizedScientificAlignment -> projection binding/subjects -> ProductionCellDecision -> projection completion -> result finalizer`

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
  peak-capture, cross-sample, trajectory, repair, or cell-decision core.

Discovery methods such as CID-NL, HCD-PI, and Delta Mass produce sample-local
evidence and hypotheses. `CrossSample` owns group membership, identity,
disposition, and lineage. `ModeTrack` may propose trajectory-based refinements,
but only `CrossSample` can apply them. Backfill performs a bounded,
independently traceable `ObservationRepair`; it does not create the first hit,
mint a group, select a product value, or grant writer authority.
The workflow-owned projection first creates typed Target/Group row bindings and
cell subjects; `ProductionCellDecision` alone selects the value or no-write
result; the same projection then owns row inclusion, counts/reasons, Review
Queue/archive routing, and delivery shape. These are two ordered operations of
one projection owner, not two owners.

The current implementation only partially shares these stage owners. Targeted
uses its own RT, paired-area, and MS1-shape evidence path. The headless limited
`5-hmdC + 5-medC` CLI runs a baseline/support/final rescue, but GUI and direct
API entry points do not generate that support automatically. This is an
implementation parity gap, not a different product lifecycle.

Targeted and Untargeted may apply different search scopes, priors, projection
rules, GUI flows, and output contracts. Evidence or output authority from one
projection does not grant write authority in the other without an explicit
contract, expected-diff evidence, and focused tests. Different projections do
not imply different capture, group, ModeTrack, ObservationRepair, or cell-
decision implementations.

## Evidence Spine

Use these terms consistently:

- `Trace`: intensity over retention time for a feature, target, or candidate.
- `TraceGroup`: related traces that should be compared together.
- `PeakHypothesis`: a proposed peak identity, retention-time window, and
  integration basis.
- `selected hypothesis`: the chosen `PeakHypothesis` and its audit context.
  Selection identifies the candidate used by a workflow projection; it does
  not by itself grant counted-detection, Matrix, workbook, or writer authority.
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
- `identity-bearing member subject`: canonical sample identity plus a
  discriminated union of owner-backed `PeakHypothesis` identity or owner-backed
  physical-observation identity. The canonical variant tag is part of identity;
  wrappers over the same physical observation canonicalize to one subject unless
  explicit different tags deliberately preserve distinct identities.
- `GroupDispositionDecision`: the CrossSample-owned, mutually exclusive
  disposition of one finalized group: `established`, `provisional`,
  `unresolved`, or `rejected`. Disposition does not decide output inclusion.
- `ModeTrack`: one request-bound trajectory model over immutable CrossSample
  evidence. It proposes assessable refinements; it does not mint groups,
  re-integrate traces, repair observations, or write values.
- `ObservationRepair`: one bounded Backfill decision that may produce an
  owner-backed repaired observation for a pre-existing Target or group cell.
  It is not imputation, group membership, or product-value authority.
- `ProductionCellDecision`: the sole core owner of the selected numeric value
  or typed no-write result for one `TargetCellSubject` or `GroupCellSubject`.
  Observations retain measurement facts; they do not select the delivered
  value themselves.
- `FinalizedScientificAlignment`: immutable shared core containing request
  identity, finalized groups, dispositions, membership/lineage, and the closed
  refinement/repair receipts needed by a workflow projection. It is not an
  output artifact or mutable Matrix.
- `TargetRowBinding`: Target projection decision that binds one fixed
  `TargetKey` row to zero or one uniquely resolved finalized group, or records
  an unresolved multiple-group state. It does not rescan RAW or rank peaks.
- `workflow projection`: the Target or Untarget owner with two ordered
  operations: pre-cell row binding/key plus cell-subject construction, then
  post-cell inclusion/count/reason and Review Queue/archive routing. It is not
  two owners and does not select the cell value.
- `UntargetRowKey`: Untargeted projection-owned row identity derived from one
  finalized canonical group. It is distinct from `group_hypothesis_id` and
  from legacy `FAM...` display labels.
- `final Matrix`: the immutable, canonical grid produced only after cell and
  row-inclusion decisions close. Pre-final observations or owner matrices must
  use a different typed name and must not cross the final Matrix interface.
- `ChromPeakSegment`: scoped product-candidate boundary unit used by
  quantitation-context decisions.
- `ProductWriter`: the exclusive approved code path for product-matrix
  mutations. It consumes only explicitly authorized scopes and never derives
  writer authority from evidence, diagnostics, or its own implementation.

Evidence providers can include CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 shape,
standards, library matches, trace overlays, review labels, and future models.
They feed the evidence spine; they do not directly become matrix write authority.

Related evidence and analysis terms:

- `sidecar`: accompanying TSV, JSON, or similar metadata that explains or
  audits a primary output without inheriting its writer authority.
- `provenance`: the inputs, effective settings, runtime, and decision path
  needed to identify or reproduce how an artifact or decision was produced.
- `seed evidence`: initial MS1/MS2 evidence that triggers candidate generation
  in Discovery. A seed is not same-peak proof or counted detection.
- `family`: legacy review/search container for related candidate or aligned
  evidence. Family membership is not same-peak proof or product authority.
- `canonical projection`: one workflow owner with two ordered mappings. Its
  pre-cell operation maps `FinalizedScientificAlignment` to workflow row
  identity/binding and explicit cell subjects; after
  `ProductionCellDecision`, its post-cell operation maps typed cell decisions to
  inclusion, counts/reasons, routing, and product output. Targeted and
  Untargeted keep separate projections; neither projection is the other's
  oracle.
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
  feature discovery. It creates candidates and review evidence, not direct
  Matrix-write authority.
- `HCD`: Higher-energy Collision Dissociation. Its product-ion audit is a review
  surface unless a separate contract grants stronger decision meaning.
- `DDA`: Data-Dependent Acquisition, where MS2 scan triggering depends on MS1
  observations and instrument acquisition policy.
- `ISTD`: internal standard, a spiked reference used for RT calibration,
  paired-area context, and method QC.
- `NL`: neutral loss, the mass difference between a precursor ion and a product
  ion after fragmentation.

## Product Authority Terms

- `candidate/audit universe`: rows that are visible for review, audit, or
  diagnosis. This is not the same as writable product output.
- `write_ready`: a scope with explicit current product write authority.
- `authority`: the permission boundary for matrix/workbook/value writes.
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
  entry and does not automatically grant writer authority.
- `truth lockbox` (`lockbox`): independent peak-choice or area labels used to
  evaluate future automation. Lockbox labels do not automatically mutate
  product output.
- `expected-diff`: explicit evidence that a public output change is intended,
  bounded, and test-covered.

Treat diagnostics, sidecars, and reports as observability unless an authority
contract says otherwise.

## Shared Contract Identifiers

- `sample_metadata_v1`: canonical schema for sample identity, injection order,
  role, batch, and QC context.
- `review_action_v1`: typed action-import schema for converting human review
  decisions into machine-checkable plans without silently editing product
  values.
- `method_manifest.json`: machine-readable run envelope for effective input
  hashes, settings, runtime, invocation context, and output pointers.
- `validation-minimal`: lightweight Alignment validation output profile. It is
  an artifact surface, not production-readiness evidence by itself.

## Processing Lineage Terms

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
- `DeliveryCellKey`: projection-owned stable position for one sample and
  Targeted target or Untargeted matrix row. It is not a kernel observation or
  an ordinal group label.
- `TargetedDeliveryCellKey`: canonical sample identity plus `TargetKey`. Exact
  target-definition and projection-policy versions belong to the delivery
  revision, not the stable cell position.
- `UntargetedDeliveryCellKey`: canonical sample identity plus
  `UntargetRowKey`. A legacy family label, m/z/RT display text, or current row
  number is not this identity.
- `DeliveryRevision`: immutable proposed or sealed value/null for one delivery
  cell, linked to the producing attempt and kernel decision.
- `DeliverySeal`: immutable manifest proving that a complete delivery revision
  set and its output artifacts passed lineage closure and the owning writer
  authority.

These terms are internal lineage identities, not extra user-visible review
states. Avoid the overloaded term `run_id` when the intended meaning is
request, attempt, artifact bundle, or delivery revision.

## Lane Boundaries

Targeted lane:

- Target lists and method settings constrain the shared lifecycle; they do not
  create a separate peak-identity or recovery system.
- Limited rescue workflows may produce `detected_flagged` when explicitly
  contracted.
- Treat the targeted result as its own delivery contract. Do not require an
  analytical downstream reader when no such product consumer exists.
- Do not expand targeted rescue to new analytes or default extraction behavior
  without expected-diff evidence and a product decision.

Untargeted lane:

- The feature-by-sample quantitative matrix is the downstream analytical
  handoff. Its sample identity and order may share method-document-derived
  metadata with Targeted delivery without sharing its output schema.
- ObservationRepair must stay bounded and owner-backed. Broad Backfill
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
