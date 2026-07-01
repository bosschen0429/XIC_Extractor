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
- Broad auto-write from the full candidate universe is parked. The
  candidate/audit universe is not the writer pool.
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

## Current Evidence Compression

The broad Backfill auto-write decision is parked, not unresolved. The retained
decision packet is still an authority/status anchor, so it must not be shortened
or moved until the status index and authority manifest point at a replacement
compact artifact with updated hashes.

Stable claims already absorbed here:

- The 4613-row candidate/audit universe is not a writer pool.
- Current Backfill product authority is the scoped 511 `write_ready` cells.
- The remaining 4102 rows are outside writer authority; this includes 3015
  trace-matched rows with no approved evidence class or passing oracle, and
  1087 rows missing trace/overlay evidence.
- Profile-only separation, quality blockers, round-trip reintegration, and
  ISTD behavior cannot become broad ProductWriter predicates.
- Broad Backfill can reopen only through a new independent truth source for
  peak choice, family identity, or area truth, plus an expected-diff contract.

The seed-aware review packet remains a shadow-gate support surface. Its current
signal is useful for review triage, but it does not change matrix authority or
write scope.

## Surfaces

| Surface | Role |
| --- | --- |
| `ProductWriter` authority path | Only approved writer path for product matrix changes |
| Productization status index | Machine-checkable Backfill maturity and lane state |
| Authority manifest | Fail-closed writer scope and accepted authority records |
| Mechanical adjudication schema/index | Structured review evidence for candidate promotion |
| Backfill validation packets | Evidence availability, replay, and expected-diff artifacts |
| `alignment_backfill_cell_evidence.tsv` | Compact alignment-side evidence sidecar |

## Boundaries

- **Owns**: authority-gated writer path, accepted quantification values,
  promotion gates, evidence-chain lifecycle, expansion packet evaluation.
- **Does not own**: current maturity tier or active lane (see control plane and
  status index), candidate-level truth for unresolved rows (see validation
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
| Expansion packets | Proposed future writer changes | Control plane, authority manifest, expected-diff packet, output tests |
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
- Updating a topic summary while leaving the control plane, status index, or
  authority manifest stale after a real authority change.

## See Also

- [Productization control plane](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Status index](../superpowers/validation/productization_status_index_v1.tsv)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Adjudication schema](../superpowers/schemas/mechanical_adjudication_schema.v1.json)
- [Adjudication index](../superpowers/validation/mechanical_adjudication_index_v1.tsv)
- [Evidence rules](../lc-msms-evidence-rules.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
