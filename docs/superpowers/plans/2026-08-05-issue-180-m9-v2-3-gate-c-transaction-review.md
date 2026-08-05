# Delta review: Issue #180 refreeze v2.3 two-stage Gate C

## 決策

**Approved.** Implementation may proceed only on the task-scoped Gate C
validation harness. V2.3 closes the manual-review/process-lifetime gap by
placing the durable boundary after live M8/M2 facts have already become
hash-pinned M6 proposal artifacts. It does not authorize production-code growth,
a persisted M8 representation, a second writer, or the 85RAW attempt before the
no-RAW rehearsal and RAW preflight pass.

## Pinned input 與 coverage

- Reviewed: all 478 lines of `output/issue180_m9_prd_refrozen_v2.md`, refreeze
  v2.3 dated 2026-08-05, SHA-256
  `F91959EA37E6137A467C0599FCC669BB43749B77586D65D7A53B2F3F45C6EEDA`.
- Delta baseline: approved v2.2 plus completed Gate A and Gate B; production
  implementation is 501 LOC in Slice A+B and 275 net added LOC in Slice C.
- Existing authority inspected:
  `load_checked_production_acceptance_manifest`,
  `build_quant_matrix_version_from_checked_manifest`, and the in-process M9
  pending-row equality and terminal numeric-key checks in
  `untargeted_cid_nl_golden_path.py`.
- Before-review gap: v2.2 required manual review and checked QMV in one Gate C
  run but did not define a legal process boundary between proposal generation
  and human review.
- Not reviewed: a C1/C2 implementation diff, new 85RAW output, the human review
  verdict, or a checked Gate C QMV; none exists yet.
- Missing evidence: Gate C outcome evidence remains intentionally pending. It
  blocks product acceptance, not implementation of the bounded harness.
- Project type: engineering validation bridge plus opt-in production-rollout
  evidence. Primary oracle is exact M6 artifact/manifest/QMV parity with zero
  unrelated diff and baseline-exact rollback.

## Implementation-readiness gates

| Gate | Before review | Evidence anchor | Verdict |
| --- | --- | --- | --- |
| Observable loss | explicit | PRD lines 34–61; a live proposal cannot cross bounded human review and checked finalization safely | pass |
| Semantic owner | explicit | lines 121–135 and 329–345; M6 manifest/QMV owners retain all write authority | pass |
| Identity/cardinality/state invariants | explicit | lines 184–209 and 303–345; request/attempt, artifact hashes, candidate/product keys, one review row, exact manifest equality | pass |
| Forbidden strategies | explicit | lines 211–227; no M8 reload/remint, second writer, product persistence API, or RAW in C2 | pass |
| Counterexample/oracle | explicit | lines 229–255 and 415–420; tamper, missing/rejected review, field drift, unauthorized value, replay, rollback, RAW/M8 call | pass |

## Findings

### Problem / boundary

- **[resolved before verdict] The original Gate C process lifetime was
  unbounded.** A human cannot prepare a checked manifest until C1 enumerates the
  pending rows, while the current M9 projection exists only in memory. V2.3
  replaces that assumption with C1 proposal freeze, bound manual review, and
  no-RAW C2 (lines 296–345).

### Semantic model / design

- **[resolved before verdict] Persisting the M9 projection would retain or
  reconstruct M8 authority.** V2.3 persists only already-rendered M6 rows and
  forbids minting M3/M8/M2/M9 objects in C2 (lines 303–345).
- **[resolved before verdict] Calling generic M6 owners alone would omit M9's
  wrapper-specific equality and terminal assertions.** V2.3 requires the
  validation harness to assert the frozen pending-row and numeric-key oracles
  around the existing owner calls. These assertions are acceptance evidence,
  not an authority-bearing compatibility path (lines 333–350).

### Transaction / evidence / operation

- **[resolved before verdict] Multi-file proposal persistence could expose a
  partial universe.** C1 stages under the task output root and publishes the
  named proposal only after every file hash and receipt validates (lines
  303–317).
- **[resolved before verdict] Manual review was not bound to an immutable
  candidate universe.** Each row is now tied to proposal/source hashes,
  request/attempt, candidate/product key, reviewer/time, evidence locator, and
  verdict; missing, duplicate, rejected, or cross-proposal records fail closed
  (lines 319–327).
- **[resolved before verdict] The harness could have become an unbounded fourth
  implementation surface.** V2.3 adds no production allowance, retains one
  task fixture, and caps it at 600 LOC; needing another fixture, product API,
  type, writer, or production LOC triggers a design split (lines 380–387).

## Design disposition

| Element | Disposition | Why | Oracle |
| --- | --- | --- | --- |
| Long-lived RAW process awaiting human input | Delete | unbounded lifetime and fragile in-memory authority | no process survives C1 |
| Persisted/reloaded M8 or minted projection | Delete | would create cross-process authority and bypass owner identity | C2 has zero M8/M2/M9 construction/import path |
| Manifest validation and QMV rendering | Delegate | existing M6 owners already define authority and expected-diff behavior | checked manifest plus QMV receipt |
| M9 pending equality and terminal-state checks | Keep as task oracle | required to prove generic M6 output matches the in-process wrapper | synthetic differential rehearsal |
| Gate C | Split into C1/manual/C2 | one RAW attempt and human review have distinct lifetimes and rollback | proposal, review, final receipts form one hash chain |

## Transaction map

| Slice | Observable loss | Semantic owner | Public surface | Oracle | Rollback |
| --- | --- | --- | --- | --- | --- |
| C1 proposal freeze | proposal cannot survive review safely | live M7/M8/M2 plus M6 projection; harness stores evidence | none | atomic complete hash-pinned artifact set, zero QMV | discard staged/incomplete proposal |
| Manual review | physical placement is not human-confirmed | operator review record over frozen proposal | none | exactly one passing row per pending cell | reject proposal; no manifest/QMV |
| C2 finalization | reviewed cells remain nonnumeric | existing checked manifest and QMV owners | opt-in QMV artifact only | exact pending equality, expected diff, terminal numeric set, zero unrelated diff | withdraw M9 manifest/QMV; baseline hash exact |

## Implementation handoff

- Semantic owner: existing M6 checked-manifest/QMV functions; harness is an
  orchestrator and oracle only.
- Invariants: C1 is the only RAW attempt; proposal universe and hashes are
  immutable; every pending row has exactly one passing review; C2 performs no
  RAW or M8 reconstruction; result keys equal baseline non-empty plus accepted.
- Forbidden strategies: no production edits, new public/persisted product
  schema, caller-minted owner object, second validator/writer, silent row
  omission, or another 85RAW attempt.
- Acceptance oracle / counterexamples: implement the no-RAW rehearsal in PRD
  lines 415–420 before C1; all tamper and replay cases must fail/pass exactly as
  specified.
- Historical characterization: retain completed Gate A, Gate B 1RAW receipt,
  current M6 acceptance/QMV suites, M1 parity, and M8.1/M8.2 closure tests.
- Scope / budget checkpoint: one existing task fixture, maximum 600 LOC total;
  focused tests remain under 800 LOC; production remains A+B 501 and C 275 net
  added LOC. Crossing any bound stops for a design split.

## Decision provenance

| Decision / invariant | Before review | Review disposition | Source anchor | Invalidates original plan? |
| --- | --- | --- | --- | --- |
| One-process Gate C around manual review | assumed in v2.2 | superseded | v2.3 lines 296–345 | yes, Gate C execution protocol only |
| Two-stage C1/manual/C2 | missing in v2.2; operator-confirmed | review-added and approved | v2.3 lines 296–345 | no change to product rule/owner |
| Persist only M6 proposal artifacts | missing in v2.2 | review-added and approved | lines 303–317 | no |
| C2 delegates to existing M6 owners | explicit owner, new stage binding | retained and narrowed | lines 329–345 | no |
| No production LOC increase | explicit v2.2 hard stop | retained | lines 380–387 | no |
| One 85RAW attempt | explicit v2.2 | retained | lines 278–281, 405–420 | no |

## Conditions / human decisions

| Item | Owner | Due / gate | Evidence to close |
| --- | --- | --- | --- |
| Physical peak/area lineage and destination review | operator | after C1, before C2 | hash-bound review record for every pending row |

The manual review is an acceptance activity, not an unresolved design decision.
No further human direction is required before implementing and testing the
task-scoped C1/C2 harness. The 85RAW attempt remains blocked until the synthetic
rehearsal and `xic-raw-validation` preflight both pass.
