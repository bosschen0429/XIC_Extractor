# Productization Schemas

Doc placement: repo_support_doc
Doc kind: manifest
Doc lifecycle: active
Repo owner: docs/project-layout.md
Doc exit rule: Retire or rewrite this index after schema contracts are absorbed into a generated registry or replaced by a narrower owner.

Status: routing index

Schemas define machine-checkable JSON contracts and formal validation shapes.
They are not automatic implementation goals, validation result buckets,
review-story galleries, design specs, or historical design archives.

Historical or visual design context belongs in Obsidian or a named validation
artifact lane after its durable product claim is represented in `docs/product/`
or the relevant structured productization owner.

## Active Reading Order

1. Productization status index for maturity/lane state, authority manifest for
   writer scope, structured control schema for allowed keys, and product
   validation contract for promotion semantics.
2. Current handoff for continuation state.
3. Current roadmap/blueprint for phase order.
4. The specific schema named by the active goal.

## Current Public Schemas

Current ProductWriter and dependent-effect state lives in the
[writer manifest](../productization/status/productization_authority_manifest.json)
and [dependent-effect manifest](../productization/status/quantitative_projection_authority_manifest.json);
these authority instances are not schemas.

- `bounded_non_broad_product_lanes.json`: bounded product-lane contract for
  non-broad activation lanes and review scope.
- `cid_nl_tested_tracer_proposal_manifest_schema.json`: diagnostic-only
  Issue #125 contract that freezes every proposal and Targeted/Untargeted
  decision from one specific CID-NL/NL116 tracer attempt before formal holdout
  review. It owns the proposal-conditioned denominator and seal protocol, not
  reference recovery or product authority.
- `cid_nl_formal_holdout_sampling_manifest_schema.json`: diagnostic-only
  Issue #125 contract for blinded challenge, representative, hidden-duplicate,
  and adaptive-supplemental presentations. It keeps `case_id` as the analysis
  unit and prevents duplicates or adaptive rows from inflating confirmatory
  denominators.
- `cid_nl_oracle_lineage_binding_schema.json`: diagnostic-only Issue #125
  adapter into the canonical #123 `AcquisitionSource`, `ProcessingRequest`,
  `ProcessingAttempt`, `EvidenceArtifact`, kernel-object, and immutable review
  identities. It forbids paths, display names, timestamps, and `case_id` from
  substituting for lineage.
- `cid_nl_oracle_review_adjudication_schema.json`: diagnostic-only Issue
  #125 contract for phase-separated blinded sample-local review, sealed
  cross-sample policy review, hidden-duplicate repeatability, unblinded failure
  taxonomy, and append-only adjudication. It keeps `unassessable` distinct from
  pending review and forbids post-reveal label rewriting.
- `cid_nl_reference_universe_manifest_schema.json`: diagnostic-only Issue
  #125 contract for expected-positive controls and independently supported
  sample-local positives. It prevents tested-tracer proposals, legacy calls,
  or spectral-library membership from defining the recovery denominator.
- `cid_nl_oracle_narrow_scope_manifest_schema.json`: diagnostic-only Issue
  #125 contract that freezes candidate NARROW claims, exact membership hashes,
  required gates, allowed overall failures, and precedence before reveal.
- `cid_nl_oracle_statistical_design_manifest_schema.json`: diagnostic-only
  Issue #125 preregistration contract for estimands, finite-census versus
  sampled uncertainty dispatch, grouping keys, threshold parameters, planning
  inputs, incomplete-frame claim downgrades, and reveal timing.
- `production_acceptance_manifest_schema.json`: Backfill
  `ProductionAcceptanceManifest v1` contract. This defines/checks the only
  future Backfill row artifact that may grant `write_authority=true`; it does
  not activate ProductWriter or the default quant matrix.
- `productization_control_plane_schema.json`: current control-plane schema
  for productization lane state, maturity, authority rules, the stable
  compatibility-anchor registry, and dependent-effect lane authority.
The [Quant Matrix activation formats](../../product/quant-matrix.md#activation-formats)
describe explicit versioned and default activation outputs and link to their
executable field, writer, and validation owners.
The [Quant Matrix review and evidence formats](../../product/quant-matrix.md#review-and-evidence-formats)
describe reports, evidence/readiness packets, candidate bundles, downstream
smoke, and activation replay/closeout gates, with their executable owners.

## Rule

If a schema conflicts with its structured state/authority owner, the current
handoff, or the current Backfill quant-matrix product blueprint, stop and
resolve the conflict instead of silently following the older schema.

The Issue #125 CID-NL schema metadata is enforced by
`xic_extractor.discovery.cid_nl_oracle_contract`. Focused negative tests submit
invalid proposal, sampling, reference, review, narrow-scope, synthetic, and
verdict inputs; schema booleans are not treated as validation by themselves.
