# Fresh review: Issue #180 bounded M8 -> M2 refreeze v2

## 決策

**Approved.** The refrozen PRD is implementation-ready for Gate A only. It
keeps M1 byte- and behavior-exact, bounds M9 to an opt-in exact-owner M8 -> M2
projection, and leaves numeric write authority with the existing checked
manifest/QMV path. Gate B and Gate C remain separately gated; this approval is
not product acceptance.

## Pinned input and coverage

- Reviewed input: `output/issue180_m9_prd_refrozen_v2.md`, all 381 lines,
  SHA-256 `DF2DC7E7A08BC6240CDAF51A2894AFE6D1D3B8A14BB88A9B11871C8827D57C24`,
  read on 2026-08-05 before the GitHub issue update.
- Baseline: PR #189 squash
  `e0cd46d4ffa40b1df14c11fa3031102d7a66be53`.
- Real-evidence anchor:
  `docs/superpowers/fixtures/mode_track_assignment_85raw_gate_b_v1.json`,
  fixture `issue-185-m3-m8-cid-nl-85raw-v16`.
- Code owners checked: M7 assessment/hypothesis closure validator, M8.1 batch
  validator, M2 conservative Backfill, existing M6 row projection, manifest,
  and QMV boundaries.
- Not reviewed: an implementation diff or new M9 RAW result; neither exists.
- Project type: bounded engineering bridge plus opt-in product acceptance.

## Before-review plan state

| Field | Before review | Anchor |
| --- | --- | --- |
| Problem / observable loss | explicit | `Problem and observable loss` |
| Success / failure | explicit | at least one checked QMV cell; zero is `inconclusive` |
| Non-goals / smaller alternative | explicit | M1 redesign and non-owner adoption rejected |
| Semantic owners | explicit | M3 identity, M8 assignment, M2 activation, M6/QMV writing |
| Identity/cardinality/state | explicit | exact owner, destination, physical, vote, and product keys |
| Forbidden strategies | explicit | no fake M1 cells, reminting, synthetic votes, or second writer |
| Scope / budget | explicit | four production modules, 350 production LOC, two internal types |
| Counterexamples / oracles | explicit | complete adversarial table and legacy parity |
| History / rollback | explicit | prior PR anchors, squash revert, manifest/QMV withdrawal |
| Open product decisions | closed | exact-owner-only opt-in boundary fixed by operator |

## Implementation-readiness gates

| Gate | Evidence anchor | Verdict |
| --- | --- | --- |
| Observable loss | exact-owner physical observation remains nonnumeric without per-track M2 facts | pass |
| Semantic owner | M2 owns N/S/K and eligibility; M8 supplies validated assignment only | pass |
| Identity/cardinality/state | one exact group/sample cell; unresolved and non-owner states remain nonnumeric | pass |
| Forbidden strategies | M1 denylist, no remint/reseal, no assignment vote, no new writer | pass |
| Counterexample/oracle | mixed early SUPPORT / late non-training sample plus crossed, partial, duplicate, and replay cases | pass |

## Findings

No blocker or major finding remains.

- **[resolved before verdict] S-voter provenance was ambiguous.** The reviewed
  PRD now requires M2 to receive complete current `sample_assessments`, call the
  existing M7 assessment/hypothesis closure validator, and derive votes from
  source objects. Assignment retention is only a binding/census cross-check.
- **[resolved before verdict] Product row identity needed an exact mapping.**
  The PRD now freezes the current M6 contract:
  `group_hypothesis_id` is the QMV row `peak_hypothesis_id`; no ModeTrack or
  assignment identifier is substituted.
- **[resolved before verdict] The 1RAW oracle was underspecified.** Gate B now
  pins `TumorBC2297_DNA` and its two-track direct-SUPPORT/non-training contrast.
- **[resolved before verdict] Manual review could be mistaken for new chemical
  proof.** It is limited to physical peak/area lineage and destination
  placement for the operator-accepted same-batch cohort.

## Delete / Delegate / Split disposition

| Element | Disposition | Reason / oracle |
| --- | --- | --- |
| M1 per-track rewrite | Delete | aggregate cell cannot express mixed sibling state; M1 remains exact |
| Non-owner trajectory adoption | Delete | geometry is not accepted product identity in M9 |
| M8 grouping and assignment | Delegate | existing producer plus M8.1 validator own these facts |
| N/S/K and eligibility | Delegate to M2 | independently recomputed from M7 source closures |
| Numeric publication | Delegate to existing M6 manifest/QMV owners | exact expected diff and zero unrelated diff |
| Implementation transaction | Split into A/B/C checkpoints | facts, mapping, and publication have independent RED/GREEN and rollback |
| Delivery | Keep one final PR only inside frozen budget | complete bridge is one opt-in product transaction |

## State-space closure

- Exact-owner non-training observation: may become a candidate, then must pass
  independent M2 and checked publication gates.
- Direct SUPPORT: supplies an existing detected value and at most one S vote;
  it is never a Backfill candidate.
- Non-owner predicted unique, ambiguous, out-of-model, not-observed, technical,
  missing, REJECT, failed, and cancelled states: remain nonnumeric.
- Duplicate physical or destination proposals: fail closed; none silently wins.
- Partial, extra, crossed-attempt, copied, reloaded, or resealed inputs: reject
  before M2 facts or product output.
- No opt-in bundle: legacy M1/M2/M6, Targeted, public, and default behavior stay
  byte- and behavior-exact.
- Exact replay: idempotent; changed replay: conflict.
- Zero real adopted cells: `inconclusive`, not promotion.

## Decision provenance

| Decision | Source | Review disposition |
| --- | --- | --- |
| M1 remains completely unchanged | operator refreeze instruction | fixed constraint |
| M9 is bounded opt-in M8 -> M2 | operator refreeze instruction | fixed design boundary |
| Only exact existing M3 owners may enter M9 | refrozen product rule | approved |
| Assignment/model/anchor grants no S vote or write authority | refrozen product rule | approved |
| Same supplied sample batch needs no new chemical-identity proof | operator domain decision | retained; manual review narrowed accordingly |
| Default activation | future product decision | explicitly out of scope |

## Implementation handoff

1. Commit the complete Gate A RED census before production implementation.
2. Implement Slice A, then B, then C, closing each focused oracle before the
   next slice.
3. Stop and refreeze on any M1 diff, non-owner adoption, synthetic vote, second
   writer, public/default change, incomplete population, unrelated diff, zero
   adopted Gate C cell, or frozen budget overflow.
4. Gate B requires `xic-raw-validation` preflight and the pinned 1RAW canary.
   Gate C permits at most one new foreground 85RAW M9 acceptance attempt.

No human decision is required before Gate A. Product acceptance still requires
the separately observed Gate C result and checked QMV closure.
