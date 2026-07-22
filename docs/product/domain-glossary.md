# XIC Extractor Context

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/domain-glossary.md
Doc exit rule: Update when stable vocabulary, lane boundaries, or authority concepts change.

This file is the stable domain glossary for XIC Extractor. Keep volatile
productization status, counts, tiers, and current blockers in the active
handoff and control plane docs, not here.

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
- `EvidenceVector`: structured evidence used for scoring or model selection.
- `IntegrationResult`: selected boundaries, area, and integration diagnostics.
- `DecisionRecord`: audit/explanation record of how evidence, policy gates,
  blockers, tie-breaks, and projection authority produced a decision. It is not
  an LC-MS `Trace` and not analytical evidence.
- `AuditTrail`: reproducible record of how evidence became a decision.
- `ProductWriter`: the narrow product output writer. It must only consume
  explicitly authorized scopes.

Evidence providers can include CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 shape,
standards, library matches, trace overlays, review labels, and future models.
They feed the evidence spine; they do not directly become matrix write authority.

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

## Product Authority Terms

- `candidate/audit universe`: rows that are visible for review, audit, or
  diagnosis. This is not the same as writable product output.
- `write_ready`: a scope with explicit current product write authority.
- `authority`: the permission boundary for matrix/workbook/value writes.
- `adjudication`: machine classification of a candidate. It can route work, but
  it is not by itself permission to write product output.
- `review packet`: a structured human-review surface. It is not free-form value
  entry and does not automatically grant writer authority.
- `truth lockbox`: independent peak-choice or area labels used to evaluate
  future automation. Lockbox labels do not automatically mutate product output.
- `expected-diff`: explicit evidence that a public output change is intended,
  bounded, and test-covered.

Treat diagnostics, sidecars, and reports as observability unless an authority
contract says otherwise.

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

Use the productization control plane and current handoff for live state. Use
ADRs for durable architecture decisions that need rationale and alternatives.
