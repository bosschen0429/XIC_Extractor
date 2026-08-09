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
Targeted extraction is a highly constrained specialization of that method, not
a second peer core:

- Untargeted extraction searches a broad candidate space, resolves cross-sample
  identity, performs authority-gated Backfill, and produces the quantitative
  feature-by-sample matrix intended for downstream preprocessing, calibration,
  and statistics.
- Targeted extraction supplies known targets and tighter m/z, RT, role, pairing,
  and evidence conditions up front. Those constraints narrow Discovery and
  preassign cross-sample target identity; Alignment still evaluates whether the
  sample-local hypotheses represent the configured target, and evidence-based
  Backfill may revisit initially uncounted sample/target results. Targeted then
  produces its dedicated CSV/workbook delivery, which does not currently require
  a downstream calibration or statistical-analysis consumer.

The shared core lifecycle is Discovery to Alignment to Backfill. Discovery
methods such as CID-NL, HCD-PI, and Delta Mass find features matching
configured evidence settings. Alignment establishes cross-sample
correspondence: Untargeted constructs owner/group identity, while Targeted may
start from configured target identity and test it with cross-sample evidence.
Backfill revisits initially missing or uncounted cells only when evidence and
authority support recovery; it is not blind imputation or counted Discovery.

The current implementation only partially shares these stage owners. Targeted
uses its own RT, paired-area, and MS1-shape evidence path. The headless limited
`5-hmdC + 5-medC` CLI runs a baseline/support/final rescue, but GUI and direct
API entry points do not generate that support automatically. This is an
implementation parity gap, not a different product lifecycle.

Targeted and Untargeted may apply different priors, projection rules, GUI flows,
and output contracts. Evidence or output authority from one projection does not
grant write authority in the other without an explicit contract, expected-diff
evidence, and focused tests. Different projections do not imply different
evidence, identity, Alignment, or Backfill cores.

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
- `canonical projection`: one workflow-owned mapping from shared evidence and
  decisions to that workflow's product output. Targeted and Untargeted keep
  separate projections; neither projection is the other's oracle.
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
- Backfill must stay authority-gated. Broad Backfill diagnostics or blockers
  cannot be promoted into writes just because they classify rows.
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
