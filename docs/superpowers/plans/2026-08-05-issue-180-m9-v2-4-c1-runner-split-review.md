# Delta review: Issue #180 refreeze v2.4 C1 runner split

## 決策

**Approved.** Implementation may resume for exactly one validation-only C1
runner split. The reviewed design delegates sample parsing to the existing
`sample_metadata_v1` owner and keeps Discovery/M7/M8/M2/M6 orchestration in one
shared core fixture. It adds no production code, public surface, writer, RAW
attempt, or product authority.

## Pinned input 與 coverage

- Reviewed: all 527 lines of `output/issue180_m9_prd_refrozen_v2.md`, refreeze
  v2.4 dated 2026-08-05, SHA-256
  `A929BAE5025F3D3F8167C37265A0F96176F68AFA33012B6209B40FCED46329B2`.
- Delta baseline: approved v2.3, core closure fixture at 600/600 LOC, focused
  test file at 700/800 LOC, and commits `962dcfd4` plus `c92661a3`.
- Current executable gap inspected: the only M9 fixture CLI calls the one-RAW
  `run_gate_b`; it cannot enumerate 85 samples or produce a Gate C proposal.
- Reuse census: the M8.2 durable receipt retains hashes and result census but no
  executable 85RAW runner. `load_sample_metadata` already owns CSV/TSV parsing,
  role validation, injection-order fields, and `sample_metadata_v1` rows.
- Not reviewed: an implementation diff, transcribed 85-row metadata file,
  no-RAW launch manifest, independent launch review, or 85RAW output; none
  exists yet.
- Missing evidence: runner behavior and the exact metadata/RAW universe remain
  pre-launch evidence gates. They block RAW launch, not this bounded
  implementation slice.
- Project type: validation engineering. Primary oracle is no-RAW proof that the
  thin adapter binds the exact 85-sample launch contract and calls the single
  shared owner path once.

## Implementation-readiness gates

| Gate | Before review | Evidence anchor | Verdict |
| --- | --- | --- | --- |
| Observable loss | explicit | PRD lines 353–360; checked closure exists but no executable 85RAW C1 path exists | pass |
| Semantic owner | explicit | lines 362–382; existing metadata loader plus shared core owner path | pass |
| Identity/cardinality/state invariants | explicit | lines 369–386; exactly 85 non-excluded unique stems/orders and exact RAW closure | pass |
| Forbidden strategies | explicit | lines 374–392 and 426–430; no direct owner-stage imports, custom parser, duplicated orchestration, third file, or production diff | pass |
| Counterexample/oracle | explicit | lines 383–386 and 465–468; count/stem/order/hash/output/command failures stop before core/RAW | pass |

## Findings

### Problem / boundary

- **[resolved before verdict] V2.3 mistook closure helpers for a complete C1
  launch path.** The synthetic fixture can freeze an already-created projection
  but its CLI still constructs only `TumorBC2297_DNA`. V2.4 names the missing
  adapter explicitly and blocks RAW until it exists (lines 353–360).

### Semantic model / design

- **[resolved before verdict] A second standalone 85RAW fixture would duplicate
  the owner path.** V2.4 requires Gate B and Gate C to call one internal batch
  operation in the core fixture; the new file may only validate launch inputs
  and invoke that operation once (lines 362–377).
- **[resolved before verdict] A task-specific metadata parser would create an
  avoidable second interpreter.** The runner must call `load_sample_metadata`
  and add only closed-universe count/stem/order checks (lines 369–382).
- **[resolved before verdict] Filename-prefix grouping would silently encode
  the study design.** Group, role, and injection order come only from the
  operator-provided Word table transcribed into the existing metadata schema
  (lines 378–382).

### Transaction / evidence / operation

- **[resolved before verdict] The missing runner cannot be hidden in a large
  inline shell command.** That path would be unreviewed orchestration. V2.4
  gives the adapter one file, a 200-LOC cap, a no-RAW preflight, and an explicit
  import denylist (lines 369–392, 422–430).
- **[resolved before verdict] V2.3's one-file budget was exhausted.** The design
  split permits core refactoring up to 650 LOC and a thin runner up to 200 LOC,
  with an 850 combined cap and the existing 800 focused-test cap. Production
  remains unchanged (lines 422–430).

## Design disposition

| Element | Keep / Delete / Delegate / Split | Why | Oracle |
| --- | --- | --- | --- |
| Inline PowerShell/Python 85RAW orchestration | Delete | unreviewed duplicate implementation | no launch command contains owner logic |
| Custom metadata JSON/parser | Delete | existing metadata owner already closes the schema | `load_sample_metadata` is the only parser |
| Sample metadata validation | Delegate | existing owner validates schema/role/row shape | exact 85-row metadata load |
| Discovery through M6 proposal orchestration | Keep once in core fixture | Gate B and C must share one semantic path | call-count test and identical 1RAW characterization |
| Launch input binding and CLI | Split into thin runner | distinct execution concern with no semantic authority | no-RAW preflight manifest |

## Transaction map

| Slice | Observable loss | Semantic owner | Public surface | Oracle | Rollback |
| --- | --- | --- | --- | --- | --- |
| Core batch refactor | one-sample-only implementation cannot serve C1 | existing Discovery/M7/M8/M2/M6 owners; fixture orchestrates | none | Gate B receipt unchanged; synthetic C1/C2 suite stays green | revert refactor; no RAW consumed |
| Thin 85RAW runner | no bounded way to bind and launch exact 85 inputs | existing metadata owner plus core operation | task CLI only | no-RAW launch manifest, exact universe/hash/call closure | delete runner and launch manifest |

## Implementation handoff

- Semantic owner: `load_sample_metadata` for metadata and the single core
  fixture batch operation for owner-path orchestration.
- Invariants: exactly 85 non-excluded metadata rows, unique complete injection
  orders, exact metadata/raw stem equality, one core call, one C1 RAW attempt,
  and receipt-marker publication only after complete output validation.
- Forbidden strategies: no new parser, filename-derived group/role/order,
  direct stage imports in the thin runner, copied orchestration, public/product
  API, third validation file, or production diff.
- Acceptance oracle: focused no-RAW cases for count, duplicate/missing order,
  stem drift, metadata hash drift, existing output, and preflight/core call
  separation; retain the existing 53-pass related suite and Gate B evidence.
- Scope / budget checkpoint: core fixture <=650, thin runner <=200, combined
  <=850, focused test file <=800, production unchanged. Any breach is Blocked,
  not another estimate increase.

## Decision provenance

| Decision / invariant | Before review | Review disposition | Source anchor | Invalidates original plan? |
| --- | --- | --- | --- | --- |
| One 600-LOC validation fixture | explicit v2.3 | superseded after executable-gap census | v2.4 lines 353–392 | yes, validation packaging only |
| One semantic owner path | explicit v2.3 | retained and strengthened | lines 364–377 | no |
| Existing metadata owner | missing in v2.3 | review-added delegation | lines 369–382 | no |
| Two bounded validation files / 850 combined | missing in v2.3 | review-added design split | lines 422–430 | no product-design change |
| One foreground 85RAW attempt | explicit | retained | lines 283–286, 450–468 | no |
| Production/M1/public/default zero diff | explicit | retained | lines 19–28, 405–430 | no |

## Conditions / human decisions

No direction-changing human decision remains before implementation. The
operator must still perform the already-defined manual EIC/MS2 review after C1.
The 85RAW launch remains prohibited until the runner's no-RAW preflight,
`xic-raw-validation` packet, and independent `validation / science evidence`
preflight review all pass.
