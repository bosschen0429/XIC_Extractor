# Review: Issue #180 M9 PRD after M8.2 merge

## 決策

**Blocked.** Issue #180 cannot start production implementation until the PRD
defines a ModeTrack-scoped M2 group/cell projection and refreezes the real
acceptance policy and attempt budget. The current proposal can validate and
narrow a candidate, but it cannot create independently owned sibling product
rows without inventing state inside the aggregate M1 cell model.

## Pinned input 與 coverage

- 審了：GitHub Issue #180, `[PRD][M9] Adopt unique ModeTrack assignments into
  bounded Backfill and QMV`, state read on 2026-08-05 after PR #189 merged;
  all sections from Status through Stop rules.
- Code baseline：PR #189 squash `e0cd46d4ffa40b1df14c11fa3031102d7a66be53`.
- M8.2 evidence：
  `docs/superpowers/fixtures/mode_track_assignment_85raw_gate_b_v1.json`,
  fixture `issue-185-m3-m8-cid-nl-85raw-v16`.
- 審了 current owners：M8.2 producer, M8.1 validator, M1 cell/reference
  spine, M2 conservative Backfill, M6 golden path, and lower QMV writer gate.
- 沒審：a proposed code diff (none exists), manual EIC/MS2 images, and a new
  M9 RAW result (not yet authorized or produced).
- Missing evidence：a canonical production trajectory policy and a separately
  budgeted M9 Gate C in-process run. The accepted M8.2 receipt records
  `recovery_attempt_count=0`; it proves shadow grouping/assignment, not M9
  product adoption.
- Project type：bounded engineering bridge plus opt-in production rollout.
  Main oracles are owner characterization, exact expected diff, real candidate
  census, checked manifest/QMV closure, and rollback to zero M9 accepted keys.

## Before-review plan state

| Field | Before review | Anchor |
| --- | --- | --- |
| Problem | explicit | `Problem and observable loss` |
| Observable loss | explicit | zero M9-adopted QMV cells; unique candidates remain nonnumeric |
| Success | explicit | three measurable outcomes under `Success requires` |
| Non-goals | explicit | `Non-goals and smaller alternatives` |
| Smaller alternatives | explicit | historical replay, scaffold, consumer refit, bundled owners rejected |
| Proposed owner/design | assumed | `Chosen design`; assumes existing M1/M2 rows can represent sibling tracks |
| Identity/cardinality/state | explicit | `Identity, cardinality, and projection invariants` |
| Forbidden strategies | explicit | `Forbidden strategies` |
| Scope/budget | explicit | five production modules / 300 LOC / 650 test LOC / 100 docs LOC |
| Counterexamples/oracles | explicit | `Required adversarial oracle` |
| History/rollback | explicit | `History, rollback, and owners` |
| Owners/open decisions | explicit but stale | status still blocked on #185; no row-projection decision owner named |

## Implementation-readiness gates

| Gate | Before review | Evidence anchor | Verdict |
| --- | --- | --- | --- |
| Observable loss | explicit | Issue #180 Problem + Success | pass |
| Semantic owner | assumed | Issue #180 Chosen design; current cell/backfill owners below | **block** |
| Identity/cardinality/state invariants | explicit | Issue #180 invariants and precedence | pass |
| Forbidden strategies | explicit | Issue #180 Forbidden strategies | pass |
| Counterexample/oracle | explicit | Issue #180 adversarial table | pass, but row-creation oracle lacks an implementable owner |

## Findings

### Problem / boundary

- **[minor] Prerequisite state is stale.** `Status and delivery contract` still
  says `blocked_on_m8_2_bootstrap_and_real_producer_gate`. PR #189 is merged at
  `e0cd46d4`; the accepted fixture pins producing state `f8c5e348`, 85 samples,
  86 hypotheses, 11 tracks, and product authority `none`. Refreeze must name
  both the merged prerequisite SHA and the evidence fixture.
- **[major] Gate C has no fresh attempt budget or canonical policy.** The
  retained receipt pins `anchor_min_seed_events=1`, but the repository has no
  production caller or canonical `ModeTrackTrajectoryPolicy`; current concrete
  policy values exist only in tests/task output. Gate C must freeze policy,
  1RAW/85RAW attempt budget, heartbeat, expected diff, and stop rules. The M8.2
  run cannot be replayed as product authority.

### Semantic model / design

- **[blocker] Aggregate M1 cell state cannot represent per-track state.** One
  `CidNlMethodAssessmentRef` may contain a direct-SUPPORT early peak and a
  non-training late observation. `CellObservationContent` preserves the whole
  assessment's method result and only SUPPORT cells may carry a hypothesis
  (`xic_extractor/alignment/cell_observations.py:200`). Therefore the same
  assessment cannot become an early SUPPORT cell and a late HOLD/non-training
  cell without a new per-track semantic projection.
- **[blocker] Current group context rejects the accepted M8.2 group universe.**
  `CidNlCellGroupContext` reconstructs its group exclusively from direct-SUPPORT
  closures (`xic_extractor/alignment/cell_observations.py:96`). M8.2 groups
  intentionally include both direct-SUPPORT and MS1-only owners. The accepted
  receipt contains 22 direct supports and 64 non-training physical hypotheses.
  Passing those tracks into the current context either fails closure or drops
  exact M3 membership.
- **[blocker] The proposed extension starts too late in the data flow.** Adding
  optional M8 references to `ConservativeBackfillRecoveryCandidateUniverse`
  and narrowing `map_conservative_backfill_candidates`
  (`xic_extractor/alignment/conservative_backfill.py:186` and `:736`) can choose
  a candidate only after group facts and target cells already exist. It does not
  create the required sibling `ConservativeBackfillGroupFacts` or baseline QMV
  rows.
- **[major] Existing M6 projection is singular.** `CidNlReferenceSpine` owns one
  group (`xic_extractor/alignment/cid_nl_evidence_spine.py:97`) and
  `UntargetedCidNlGoldenPathProjection` owns one `backfill_facts`
  (`xic_extractor/alignment/untargeted_cid_nl_golden_path.py:163`). The PRD says
  not to use that one-row convenience owner, but does not name its replacement
  or delegate.

### Transaction / evidence / operation

- **[blocker] Cross-owner transaction is not decomposed.** The actual work has
  at least two independently failing axes: (A) ModeTrack -> M2 per-track group
  facts/baseline rows and (B) validated unique assignment -> candidate
  narrowing. M7/M6/QMV handoff is a third projection slice. These need separate
  RED/GREEN and rollback checkpoints even if retained in one final PR.
- **[major] The current scope allowlist omits the owner that must change.** The
  PRD names M2 recovery and M7/M6 handoff but forbids a new cell/group owner.
  Before implementation it must either explicitly authorize an M2-native
  ModeTrack group-facts path or expand the M1 cell owner with a complete
  per-track state model. Implementers cannot decide this inside code review.

## Design disposition

| Element | Disposition | Why | Oracle |
| --- | --- | --- | --- |
| `bootstrap_mode_tracks_from_cid_nl_support` | Keep / delegate | Existing M3/M8 composition owner | exact Family/model/batch closure |
| M8.1 batch validator | Keep / delegate | Existing assignment integrity owner | forged/crossed batch rejection |
| Aggregate M1 cell as sibling-row owner | Delete from M9 strategy | Cannot express mixed per-track state | early SUPPORT + late non-training counterexample |
| M2 ModeTrack-scoped group facts | Refreeze as recommended owner | M2 already owns S/K and Backfill eligibility | one fact set per exact group ID; independent S/K |
| Assignment-aware mapper branch | Keep after row facts exist | Narrows only; must not create rows | unique vs ambiguous/out/technical table |
| M7/M6/QMV projection | Split | Separate lineage/write failure axis | expected diff + checked manifest + rollback |
| New public schema/config/writer | Delete | No requirement and violates scope | hard-zero public/default diff |

## Transaction map required for the refrozen PRD

| Slice | Observable loss | Semantic owner | Public surface | Oracle | Rollback |
| --- | --- | --- | --- | --- | --- |
| A. ModeTrack -> per-track Backfill facts/baseline rows | sibling tracks have no independently addressable product rows | M2, consuming exact M3 group + M8.1-validated batch closure | none | mixed-state sample; independent group N/S/K; single-mode parity | remove opt-in M2 projection; baseline bytes unchanged |
| B. Unique assignment narrowing | recovery candidate uses ordinal/RT mapper instead of exact destination | M8 assignment decision + M2 mapper | none | unique maps one group; all non-unique states nonnumeric | omit M8 inputs and recover byte-exact legacy mapper |
| C. Product projection | eligible cell never reaches checked QMV | existing M7/M6 manifest and QMV owners | opt-in QMV only | exact lineage, expected diff, zero unrelated diff | withdraw M9 manifest/QMV and revert squash |

## Recommended refreeze

1. Make M2 the per-track Backfill-facts owner. It consumes exact
   `CrossSamplePeakGroupHypothesis`, `ModeTrackModelSet`, and the complete set of
   M8.1-validated sample batches directly. It must not reinterpret prediction
   geometry or convert M8 facts into S votes.
2. Permit at most one new retained M2 type if the existing
   `ConservativeBackfillGroupFacts` cannot represent the alternate source
   without optional-field state explosion. Do not force sibling state through
   `CellObservationRef` merely to satisfy the old type shape.
3. Preserve the existing no-M8 ordinal mapper byte- and behavior-exact. The new
   branch may only narrow a candidate to the exact uniquely assigned
   `group_hypothesis_id` after M2 independently proves `S >= K`.
4. Pin prerequisite merge `e0cd46d4`, fixture
   `issue-185-m3-m8-cid-nl-85raw-v16`, `CID_NL_ALIGNMENT_CONFIG`, exact
   trajectory policy, and a new M9 Gate C attempt budget. Gate C must call the
   producer and consumer in-process; retained JSON is census evidence only.
5. Refreeze the three slices above with named symbols, file/LOC budget, RED
   table, rollback, and a positive requirement that at least one real adopted
   cell exists. Zero eligible cells remains `inconclusive`.

## Implementation handoff

- Semantic owners：M3 owns group identity; M8 owns assignment state; M2 owns
  activation/Backfill eligibility; checked manifest/QMV owns numeric writing.
- Invariants：exact request/attempt; one physical hypothesis -> at most one
  destination; one per-track row; no assignment as an S vote; non-unique states
  remain nonnumeric; missing is never zero.
- Forbidden strategies：aggregate assessment relabeling, reminted group IDs,
  ordinal fallback when M8 input is present, trajectory reinterpretation,
  evidence transfer, second writer, persisted M8 replay.
- Acceptance：mixed-state sample, sibling S above/below K, non-unique states,
  duplicate/crossed input, legacy parity, exact expected diff, real adopted
  cell, hard-zero unrelated diff, and rollback.
- Scope checkpoint：owner choice and Slice A must be refrozen before any
  production code. Exceeding one new retained type or touching a fourth
  semantic owner requires another split review.

## Decision provenance

| Decision / invariant | Before review | Review disposition | Source anchor | Invalidates original plan? |
| --- | --- | --- | --- | --- |
| M8 assignment narrows eligibility only | explicit | keep | Product rule / precedence | no |
| Sibling tracks become independent rows | explicit | keep | Identity/cardinality invariants | no |
| Extend recovery universe + mapper is sufficient | author-assumed | superseded | Chosen design vs current M1/M2 state | **yes** |
| M1 aggregate cell can remain the row source | missing | reject | `cell_observations.py:200`, `:344` | **yes** |
| M2-native per-track facts | missing | review-added recommendation | M2 semantic ownership | yes; requires refreeze |
| M8.2 prerequisite closed | stale/missing exact merge | review-added fact | PR #189 / `e0cd46d4` | no |

## Conditions / human decisions

| Item | Owner | Due / gate | Evidence to close |
| --- | --- | --- | --- |
| Choose M2-native per-track facts vs a larger M1 per-track cell redesign | operator / product architecture | before PRD refreeze | explicit owner and transaction map |
| Freeze trajectory policy and M9 Gate C attempt budget | delivery + acceptance owner | before implementation | pinned values, command shape, stop/retry rules |

## 值得保留

- The PRD already states the observable loss, strict state precedence, exact
  identity/cardinality invariants, forbidden shortcuts, expected-diff gate,
  rollback, and a strong adversarial table.
- The proposed M8.1 ingress validation and legacy no-M8 parity branch are the
  correct boundaries; the missing piece is the product-row owner before them.
