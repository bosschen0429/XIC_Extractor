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

Two product lanes share the same evidence spine:

- Targeted extraction: target-list driven extraction, targeted MS1 shape
  identity, limited NL_FAIL rescue, and bounded `detected_flagged` output.
- Untargeted extraction: family discovery, Backfill, mechanical adjudication,
  review packets, truth lockbox labels, and authority-controlled writes.

The lanes can reuse evidence, but evidence from one lane does not grant product
write authority in another lane without an explicit contract, expected-diff
evidence, and focused tests.

Untargeted product flow is Discovery to Alignment to Backfill to product matrix.
Discovery methods such as CID-NL, HCD-PI, and Delta Mass find features matching
configured evidence settings. Alignment resolves cross-sample owner/group
identity based on evidence. Backfill fills missing cells only when evidence and
authority support recovery; it is not blind imputation or counted detection.

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

- Limited rescue workflows may produce `detected_flagged` when explicitly
  contracted.
- Do not expand targeted rescue to new analytes or default extraction behavior
  without expected-diff evidence and a product decision.

Untargeted lane:

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
