# Backfill

Backfill is the authority-gated path for filling accepted quantification values
into product-facing outputs. It is not a shortcut from diagnostics, overlays,
or candidate tables into the matrix.

## Contract

- Backfill acceptance means accepted quantification values, not automatic truth
  claims about every candidate feature.
- Backfill is evidence-based recovery for accepted candidates or aligned groups
  after Discovery and Alignment have established the relevant context. It is not
  blind imputation, not a way to create first Discovery hits, and not counted
  detection.
- Any broader Backfill publication requires an explicit public authority
  contract, expected-diff evidence, and focused output tests.
- Broad auto-write from the full candidate universe has no registered writer
  authority. The candidate/audit universe is not the writer pool; consult the
  status index for current lane readiness.
- Current expansion priorities: mechanical adjudication, structured review,
  truth acquisition, trace-evidence recovery, and evidence provenance -- before
  any new writer scope.
- Lifecycle vocabulary (missing call, candidate, evidence-chain packet, review
  item, approval decision, export policy, quant-matrix version) describes
  separate states and is not writer authority by itself.
- `shadow_gate_ready` classifies Backfill readiness; it is not matrix authority.
- Positive support must be provenance-valid. Display tokens in
  `alignment_review.tsv` do not promote a row without the approved source
  sidecar or authority chain.
- Family-level context can guide review, but product support must be tied to a
  same-peak, group-hypothesis, or PeakHypothesis-level authority record before
  it can promote a rescued cell.
- Reusable standard-peak trace evidence caches are opt-in replay helpers, not
  Backfill authority. A warm hit must match per-sample `path_stat_v1` RAW
  identity: resolved path, size, mtime, device, and inode. Missing or mismatched
  identity fails closed to normal RAW extraction; a same path string alone is
  never sufficient cache identity. The reusable failure lesson and focused
  evidence are retained in
  [`raw-backed-evidence-cache-identity.md`](../solutions/validation/raw-backed-evidence-cache-identity.md).

## Conservative Eligibility Kernel

Issue #162 adds a bounded `shadow_ready / no-write` policy owner over M1's
canonical `CellObservationRef`. Its design and activation base is
`e30a04f8ed30a02c7424b49b24a42c844937a201`. It does not replace legacy
Backfill, activate a runtime path, or grant Matrix, CSV, workbook, config,
selected-value, counted-detection, or writer authority.

The frozen rules are:

- `N` counts non-excluded `study_sample`, `qc`, and `pooled_qc` samples. Blank,
  MixSTD/calibrator, system-suitability, solvent, and unknown roles are outside
  the activation population. Study and QC samples each contribute one equal
  vote.
- `K = max(2, floor(0.05 × N))`.
- `S` counts distinct activation-eligible direct SUPPORT samples in the exact
  existing group context, at most once per sample × group. Repeated scans,
  Backfill candidates, shared-set, inferred, deconvolved, shadow, and manual
  evidence cannot add votes.
- A group and its cells remain representable when `S < K`; the group simply
  cannot start a conservative recovery attempt.
- The declared run population and the canonical cell set must close exactly
  before the group is treated as frozen. Backfill cannot create a first hit,
  group, target universe, identity, landmark, or recursive support vote.
- Every group context binds the processing attempt that formed its direct
  SUPPORT closure. A later cell may reuse that closure only in the same attempt
  or through the current attempt's owner-validated retry-predecessor chain;
  same-request sibling attempts fail closed.
- The complete direct SUPPORT `PeakObservationRef` trace set is retained as
  exact references. It is not collapsed into a median template.
- Recovery candidates enter only through an immutable owner receipt that binds
  the exact request/attempt, sample, method subject and optional Targeted
  compatibility identity derived from the frozen group
  facts, source-cell artifacts, and one owner-minted CID-NL method assessment.
  That assessment—not a naked `PeakObservationRef` argument—owns the complete
  method-subject-bound candidate universe and its peak artifacts. Mapping consumes the
  receipt itself, so an empty completed universe remains distinguishable from
  a missing receipt, and callers cannot independently pair a method subject
  with a physical peak, combine receipts for one sample × method subject, or
  omit individual members.
- Receipt-backed recovery evidence is mapped jointly within the same exact
  method subject by equal cardinality, finite group RT, unique elution order, and the
  existing candidate query RT/ppm envelope. `1↔N`, `N↔1`, tied order, or
  out-of-envelope evidence stays HOLD and nonnumeric. Drift, split,
  deconvolution, and multi-component resolution remain later work.
- A method-level `REJECT` is terminal for that attempt and is not Backfill
  eligible. Technical/no-evidence and HOLD cells may remain nonnumeric pending
  an owner-bound recovery universe; neither state creates a vote by itself.
- Targeted and Untargeted adapters unwrap their M1 reference projections and
  call the same policy owner over the same exact `CellObservationRef` objects;
  neither adapter owns a second workflow-neutral cell model.

This slice, including the request/candidate, attempt-lineage, and recovery
receipt closure added while resolving #156 gaps 5/8/9, leaves every current
productization maturity tier and active lane unchanged. It changes admission
and replay identity only: no selected area, counted detection, output schema,
Matrix value, or writer authority changes. All pre-existing registered
Backfill writer authorities remain owned by the status index and authority
manifest; those machine owners and the historical productization control plane
therefore require no update for Issue #162.

## Current Evidence Compression

The broad Backfill auto-write decision is retained as provenance, not as a
current status owner. It must not be shortened or moved until the status index
and authority manifest point at a replacement compact artifact with updated
hashes.

Stable claims already absorbed here:

- The candidate/audit universe is not a writer pool.
- Current Backfill product authority is only the registered `write_ready` scope
  named by the status index and authority manifest.
- All remaining candidates stay outside writer authority, including
  trace-matched candidates with no approved evidence class or passing oracle
  and candidates missing trace/overlay evidence.
- Profile-only separation, quality blockers, round-trip reintegration, and
  ISTD behavior cannot become broad ProductWriter predicates.
- Broad Backfill can reopen only through a new independent truth source for
  peak choice, family identity, or area truth, plus an expected-diff contract.

The seed-aware review packet remains a shadow-gate support surface. Its current
signal is useful for review triage, but it does not change matrix authority or
write scope.

## Early-loser settlement shadow

`run_alignment --emit-early-loser-shadow` is an opt-in `diagnostic_only`
comparison surface for Issue #195 / #208. It runs the existing typed
pre-Backfill identity consolidation as a proposal, but the original owner
features still enter the existing Backfill request plan and execution path.
The flag requires `--backfill-scope full-audit` and fails closed when combined
with the activated `--preconsolidate-owner-families` path.
After the claim registry and primary consolidation finish, the shadow compares
every proposal row with the typed final Matrix authority and writes
`alignment_early_loser_shadow.json`.

The packet preserves the proposal source stage, direct and terminal winners,
complete transitive lineage, unresolved states, and final-authority comparison.
Legal two-stage `primary_loser → primary_loser → primary_winner` chains are
retained; missing references, cycles, non-winner terminals, and winner
mismatches fail closed. The report also includes owner request-plan feature,
sample-cell, and logical XIC-request counts for the baseline and proposal. Those
are workload counts, not elapsed-time, RAW-call, chromatogram-call, point-count,
or cost savings.

The durable packet binds the timing `run_id`, a canonical typed source-owner
snapshot digest, the `AlignmentConfig` digest, and the complete final
consolidation-comparison projection digest. A separate request-universe digest
binds sample order and available RAW sample stems. The packet deliberately does
not bind or certify Matrix cells/values; those facts remain unavailable gaps,
and the persisted JSON remains an untrusted diagnostic projection after
deserialization.

The sidecar reports hard-zero observed Matrix/output diff because it never
constructs or routes an alternate Matrix. Counterfactual selected observations,
missing/extra cells, status/reason, areas, Matrix identity/value, and review
population remain explicit activation gaps until an owner-valid survivor-only
execution can supply them under a separate authorization. `GO` means only that
the shadow instrumentation and typed lineage close and all three projected
owner workload reductions are positive; it never grants early skip,
Backfill eligibility, Matrix, ProductWriter, or activation authority.

## Surfaces

| Surface | Role |
| --- | --- |
| `ProductWriter` authority path | Only approved writer path for product matrix changes |
| Productization status index | Machine-checkable Backfill maturity and lane state |
| Authority manifest | Fail-closed writer scope and accepted authority records |
| Mechanical adjudication schema/index | Structured review evidence for candidate promotion |
| Backfill validation packets | Evidence availability, replay, and expected-diff artifacts |
| `alignment_backfill_cell_evidence.tsv` | Compact alignment-side evidence sidecar |
| `alignment_early_loser_shadow.json` | Opt-in owner-valid proposal/final comparison; no-write and non-activating |

## Boundaries

- **Owns**: authority-gated writer path, accepted quantification values,
  promotion gates, evidence-chain lifecycle, expansion packet evaluation.
- **Does not own**: current maturity tier or active lane (see status index),
  candidate-level truth for unresolved rows (see validation
  packets and adjudication), sample-level private investigation details.
- The candidate/audit universe feeds review and classification, not automatic
  matrix writing.

### Stable Categories

| Category | Meaning | Repo owner |
| --- | --- | --- |
| Current scoped writes | Already accepted cells that ProductWriter may publish | Authority manifest, status index |
| Candidate or audit rows | Rows available for review, classification, or evidence recovery | Validation packets, adjudication index |
| Trace-matched unresolved rows | Rows with some trace-level evidence but insufficient authority | Evidence lifecycle, adjudication surfaces |
| Missing-overlay rows | Rows blocked by absent or insufficient overlay evidence | Evidence availability packets, blocker reports |
| Expansion packets | Proposed future writer changes | Status index, authority manifest, promotion contract, expected-diff packet, output tests |
| Evidence-chain packet | Structured source evidence for review or approval | Evidence lifecycle, provenance owners |
| Approval decision | Human or machine-readable approval state | Review roundtrip, authority manifest gates |

## Verification

Before changing Backfill product behavior, require the relevant subset of:

- Productization state and authority checker pass.
- Expected-diff packet for any new matrix-writing scope.
- Focused output tests covering changed matrix values and identity sidecars.
- Evidence-rule review for any new interpretation of trace, overlay, or
  candidate evidence.
- Privacy scan if historical notes or sample-level review details are touched.

## Pitfalls

- Treating the candidate/audit universe as the writer pool.
- Treating overlays, galleries, blocker tokens, or sidecars as matrix authority.
- Treating `shadow_gate_ready` as a write predicate.
- Treating `alignment_review.tsv` tokens as positive support without
  provenance-valid sidecars.
- Treating a family/window match as same-peak evidence without typed anchor,
  selected mode, or PeakHypothesis-level support.
- Updating a topic summary while leaving the status index, authority manifest,
  or promotion contract stale after a real authority change.

## See Also

- [Historical compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index_v1.tsv)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Adjudication schema](../superpowers/schemas/mechanical_adjudication_schema.v1.json)
- [Adjudication index](../superpowers/validation/mechanical_adjudication_index_v1.tsv)
- [Evidence rules](../lc-msms-evidence-rules.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
