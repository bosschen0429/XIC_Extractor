# Six-Week Shared Processing Kernel Implementation Wave

Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/architecture-contract.md
Doc exit rule: After Issue #127 records GO, NARROW, or REDRAW, keep this plan
active only for an approved implementation wave. When that wave closes, absorb
durable module, promotion, rollback, and stop rules into their canonical owners,
then retire this transient plan through the normal docs lifecycle.

Date: 2026-07-25
Issue: [Compose the six-week implementation wave and promotion plan](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/126)
Parent map: [Find the six-week route to an evidence-first shared processing kernel](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/118)
Pre-implementation challenge: [Challenge the implementation package and decide GO, NARROW, or REDRAW](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/127)
Validation status: `diagnostic_only`

This transient file owns package order, package-specific gates, WIP, and stop
conditions only. Durable runtime, lineage, review, promotion, and rollback
semantics remain with the canonical owners in the frozen-input table below; if
this map conflicts with an owner, the owner controls and this map must be
corrected.

## Current Execution Progress

Last updated: 2026-07-27

Current validation status: `diagnostic_only`

Current issue gate: Package 0 and Package 1 are complete at their executable
no-RAW exits. The operator explicitly activated Package 2 on 2026-07-27; this
tracked checkpoint is the canonical local tracker scope for branch
`codex/package-2-targeted-method`. The intended write scope is one bounded
`diagnostic_only` Targeted CID-NL method: constrained Discovery -> explicit
diagnostic `PeakHypothesis` selection/externalization -> cross-sample Alignment
with preserved hypothesis identity -> evidence-based Backfill -> Targeted
selection/projection -> the existing hard-exact `xic_results.csv` writer.
`TargetedApplicationService` remains the one application owner for CLI and GUI,
while the existing Targeted process scheduler and Alignment process runner are
the runtime consumers. Package 0/1 identity, lineage, runtime, observation,
settlement, and frozen-comparator contracts are reused, not rebuilt. No remote
GitHub issue was changed. RAW/DLL, 8RAW/85RAW, Pt, Untargeted widening,
Matrix/XLSX, Package 3, product activation/publication, new storage/runtime/core,
and public CLI/config/schema/writer authority remain outside this write scope.
The prior selection-externalization blocker is resolved: on 2026-07-27 the
operator accepted a diagnostic-only immutable selection reference owned by
`peak_detection.selection_decision`. The review repair now adds the missing
Discovery-owned `TargetedCandidateSetRef`: it proves the complete per-cell
candidate universe, exact Targeted CID-NL projections, canonical sample,
request/attempt, target snapshot/key/definition, and full immutable artifact
closure before selection. Selection, Alignment, and Backfill consume that same
receipt; only a unique candidate may be selected, while ambiguity,
no-candidate, and failure remain explicit. The legacy writer target must equal
the complete owner-resolved `Target`, not merely its label. The existing
product-selection guard is unchanged.

The expanded no-RAW engineering gate is now complete. All seven repository
shards pass at the integrated Package 2 head: docs-config 464 passed/1 skipped,
GUI 110 passed, targeted-core 1,090 passed/1 skipped, alignment-core 2,487
passed, product-gates-activation 872 passed/1 skipped,
product-gates-evidence 633 passed, and diagnostics-tools 356 passed/1 skipped
(6,012 passed/4 skipped total). Ruff, full-package mypy over 476 source files,
Import Linter (4 kept/0 broken), architecture census, diagnostics index, docs
audit with zero blockers, shard census, validation retention, hook fixtures,
hook assertions, diff hygiene, and secret/private-path/local-config scans pass.
Independent QA separately replayed the complete method, failure semantics, CSV
digest, 13-row exact-zero manifest, unchanged writer blob, and zero Matrix/XLSX
claim without a correctness finding. Fresh independent code review returned
`REQUEST_CHANGES`: the current path is not yet a complete method because CLI
and GUI do not reach it, Discovery and the two runtime consumers are not in the
scientific execution path, delivery settlement does not close the canonical
result artifact through the existing lineage owner, candidate-universe/Target
binding is caller-asserted, and Backfill collapses unassessable states into
`true_missing`. The Backfill finding is closed in the first repair slice:
missing source remains `unchecked`, non-unique correspondence remains
`ambiguous`, unassessable owner evidence remains `unassessable`, and only a
completed no-peak query becomes `true_missing`; 12 focused method/Backfill tests
pass with hard-exact projection unchanged. The second repair slice closes the
candidate-universe/exact-Target finding and introduces owner-specific
Alignment-group and Targeted-method runtime carriers. Carriers are exposed only
after successful integrity/finalization settlement and bind canonical domain
bytes plus explicit upstream IDs. The last consumer boundary actually run is
still focused runtime settlement, not yet the spawned scientific workers;
47 integrated candidate/selection/Alignment/Backfill/runtime/method tests and
Ruff/diff checks pass. Three review findings remain: real spawned scientific
execution, canonical delivery-lineage closure, and CLI/GUI reachability through
one service path.

The next repair slice addresses adapter reachability without a public switch.
`TargetedApplicationService.execute_application` owns one legacy-authoritative
execution plus an optional, internally injected diagnostic request factory and
runner; both must be configured together. The existing `execute` API delegates
to this path. The actual CLI `main` and GUI `PipelineWorker.run` now call that
same method, differing only in progress/cancellation/display mechanics. An
executable test drives both adapters with the same configured service and the
real bounded diagnostic method: each invokes legacy, request construction, and
scientific method exactly once, while legacy remains the active output. The
adapter/service regression gate is 46 passed. No public CLI/config/replay/output
schema was added, so diagnostic activation remains internal-only.

The spawned scientific and delivery-lineage repair is now integrated. Real
process workers run candidate-set-backed `align_selected_targeted_hypotheses`;
only an integrity-clean, successfully finalized Alignment group carrier may
construct Targeted completion work. The Targeted worker consumes those exact
groups and calls `complete_targeted_diagnostic_method` without realignment.
The resulting owner artifact, Alignment artifact, candidate-set ancestors, and
selection artifacts close through the existing candidate-only delivery owner,
which mints per-cell revisions, a stage candidate, and a seal candidate only
after both runtime work refs were `OFFERED_TO_OWNER` and CSV bytes are
hard-exact. Failed/cancelled/worker-loss/partial/conflicting attempts quarantine;
rollback preserves prior current plus diagnostic evidence. The integrated gate
is 110 passed with true spawn, serial/reordered parity, CLI/GUI reachability,
hard-exact delivery, rollback, and failure semantics; Ruff, focused production
mypy, and diff check pass. All five review findings are addressed in code and
focused evidence; expanded repository validation and fresh independent review
remain required before completion.

Expanded static validation then found one dependency-direction defect:
inner-layer `peak_detection.selection_decision` imported the outer Discovery
receipt class. The repair keeps ownership unchanged and inverts only the read
boundary: Selection defines the minimal immutable receipt protocol it consumes,
and `TargetedCandidateSetRef` re-runs its Discovery-owned full integrity check
when crossing that boundary. No candidate model or decision logic is copied.
Import Linter is now 4 kept/0 broken, full mypy passes over 479 source files,
17 affected receipt/selection/Alignment/Backfill tests pass, and the complete
110-test integration remains green. The final expanded rerun at code head
`20bf89b6` passes all seven shards: docs-config 464 passed/1 skipped, GUI 110,
targeted-core 1,090 passed/1 skipped, alignment-core 2,492,
product-gates-activation 872 passed/1 skipped, product-gates-evidence 660, and
diagnostics-tools 356 passed/1 skipped (6,044 passed/4 skipped total). Ruff,
full mypy over 479 source files, Import Linter 4 kept/0 broken, architecture
census, diagnostics index, docs audit with zero blockers, bootstrap, shard
coverage, retention, and diff checks pass. The validation packet and artifact
inventory now include the receipt, spawned-runtime, shared-adapter, and
candidate-only delivery evidence while retaining the 13-row exact-zero
DifferenceManifest SHA-256
`377932de8bac5a929467a108658a3f9c5899a8b0e78fb7c05e277cacec0f2755`.

Fresh independent QA at frozen `eaf29598` passed, but fresh independent review
returned `REQUEST_CHANGES`. Production call-graph review proved that the actual
CLI/GUI composition still constructs a legacy-only service; candidate-set
completeness is still asserted by a caller tuple; runtime scientific artifacts
are not content-bound to the successful result/groups; the unchanged writer
and candidate delivery owners are not composed into one path; and
confidence/reason parity is copied from legacy rather than compared against a
successor-owned semantic projection. Backfill terminal-state preservation and
exact Target equality passed review. These findings require an internal
composition/content-binding/comparator repair, not a public CLI/config/schema
or writer-authority change. Expanded evidence is therefore not closure evidence
until the repaired production call graph passes fresh QA and review.

The bounded repair is now integrated locally. Production CLI and GUI construct
the same internal service composition; a scoped owner-backed request provider
activates the true spawned Alignment and Targeted consumers without adding a
public switch. Discovery now seals an immutable completion artifact from the
entire explicitly bounded no-RAW hypothesis/projection input before minting the
candidate-set receipt, so a caller cannot omit or inject members at the receipt
boundary. Delivery accepts only the typed finalized Alignment and Targeted
application carriers, revalidates their scientific content and exact upstream
bindings, invokes the unchanged wide CSV writer inside the delivery path, and
stages only after hard-exact bytes. Confidence/reason is first projected from
successor terminal state and owner reason, then compared with legacy. The
30-test focused repair gate, Ruff, and mypy over the seven affected production
modules pass. Expanded evidence and a fresh independent review remain required;
the prior `REQUEST_CHANGES` verdict is not yet superseded.

Expanded evidence at code head `64b716c8` is green: the 77-test changed Package
2 set passes, and all seven repository shards report 6,048 passed/4 skipped
(464/1, 110, 1,090/1, 2,492, 872/1, 664, 356/1). Full mypy passes over 480
source files, Import Linter reports 4 kept/0 broken, and CI-scoped Ruff,
architecture census, diagnostics index, docs audit with zero blockers,
bootstrap, shard coverage, retention, hook assertions, and diff checks pass.
The exact-zero manifest remains 13/13 with the same 403-byte CSV and unchanged
writer blob. Validation packet/inventory digests are being refreshed before
fresh independent QA and review.

Fresh independent QA at frozen `ce2dd277` passed, but fresh review returned
`REQUEST_CHANGES` after exercising the real GUI thread boundary and production
call graph. Four P1 shapes remain: the scoped diagnostic provider is not copied
into the actual `QThread`; production composition stops after Targeted runtime
instead of settling delivery; the delivery writer still receives legacy DTOs
rather than a successor-owned render projection; and adapter cancellation is
not accepted by the successor runtime. Candidate completeness, scientific
carrier binding, unchanged writer placement inside delivery, successor
confidence/reason comparison, Backfill states, and exact Target equality passed
review. The next repair is limited to internal application/thread mechanics,
successor render DTOs, and existing runtime cancellation; no public or product
authority change is authorized.

The bounded thread/delivery repair is now integrated locally. `PipelineWorker`
captures the scoped diagnostic provider before crossing the real `QThread`
boundary and reinstalls it only for the canonical service call. The production
composition now accepts cancellation before each spawned consumer and after
Targeted settlement, quarantines any non-successful or late-cancelled attempt,
and settles delivery only from integrity-clean typed runtime carriers. The
unchanged writer receives render-only DTOs constructed solely from successor
projections; an executable tampered-legacy regression proves the candidate CSV
does not consult legacy result DTOs. Nine affected/adjacent files report 65
tests passed; Ruff, focused production mypy, `py_compile`, Import Linter (4
kept/0 broken), architecture census, and diff checks pass. Expanded evidence,
packet digest refresh, fresh QA, and fresh reviewer `APPROVE` remain required.

Expanded verification at frozen code head `9f79ad4c` is green. The complete
changed Package 2 set reports 80 passed; all seven repository shards report
6,051 passed/4 skipped (464/1, 110, 1,090/1, 2,492, 872/1, 667, 356/1).
Ruff, full mypy over 480 source files, Import Linter 4 kept/0 broken,
architecture census, diagnostics index, docs audit with zero blockers,
bootstrap, shard coverage, validation retention, hook fixtures/assertions,
secret/private-path scan, and diff checks pass. The hard-exact CSV remains 403
bytes at SHA-256 `f0a471bd81046cefaa924d619b934957505ee22591b17820a73143f7b4126937`;
the 13-row DifferenceManifest value pairs remain exact-zero; its description is
being corrected from the obsolete legacy-DTO bypass to the canonical
successor-projection render path, so the prior digest is intentionally stale. The
writer blob remains `6792d0fcd4d15b3cf23f2ce9e174fdbf96ed8399`, and no Matrix/XLSX artifact
exists. Packet and inventory bindings are refreshed; fresh independent QA and
review are the remaining closure gates.

Fresh independent QA at evidence head `d7dd28e2` passed the complete 65-test
fixture and a 41-test real-thread/runtime/delivery replay with a clean worktree.
Fresh review nevertheless returned `REQUEST_CHANGES`: an older test-only
`settle_targeted_diagnostic_delivery` API still formed a second downstream path,
accepted untyped finalization summaries, and passed legacy result DTOs directly
to the writer. The tracker and DifferenceManifest also described that obsolete
path while the packet described the canonical successor-projection path. This
is a correctness and evidence-coherence blocker. The repair is to remove the
duplicate API/tests completely and retain only the typed production delivery
owner; no public or product-authority change is authorized.

The duplicate path is now removed rather than deprecated: there is no
`TargetedDiagnosticDelivery` model or `settle_targeted_diagnostic_delivery`
symbol outside the canonical candidate-delivery module, and the method module
no longer imports writer/finalization dependencies. The three bypass-only test
cases are deleted; canonical delivery already covers exact bytes, runtime
failure/cancellation, quarantine, and rollback. The repaired changed set is 77
passed and all seven shards are 6,048 passed/4 skipped (464/1, 110, 1,090/1,
2,492, 872/1, 664, 356/1). Ruff, full mypy over 480 sources, Import Linter 4
kept/0 broken, architecture census, and retention pass. The 13 exact-zero value
pairs, 403-byte CSV digest, writer blob, and zero Matrix/XLSX remain unchanged;
the DifferenceManifest digest is now
`213bf92f5f237cdedf61abc9435d675c63831774004bb2153952c2322a3dabcd` solely
because its render-path reason now truthfully names successor projection DTOs.
Fresh independent review remains required.

The final owner-projection/carrier repair is integrated locally. All numeric
fields and counted state now originate from the successor-selected integration
or successor terminal state; legacy DTOs are equality comparators only. The
canonical Targeted method payload now binds canonical sample/target identity,
the complete selected `IntegrationResult`, and the complete owner Backfill
`AlignedCell`. Regressions prove equal-but-distinct legacy numeric objects do not
propagate, selected raw-area or Backfill trace-quality mutation changes canonical
bytes, and the post-spawn Targeted carrier rejects the tampered result. The
changed set is 79 passed and all seven shards are 6,050 passed/4 skipped (464/1,
110, 1,090/1, 2,492, 872/1, 666, 356/1); Ruff, mypy 480, Import Linter 4/0,
architecture census, and exact product/CSV/Matrix/writer evidence remain green.

Current engineering-migration gate: exact-zero product, scientific, Matrix, and
writer parity. Any selected-peak, boundary, area, confidence/reason,
counted-state, Matrix, or CSV value/byte difference stops this migration and
requires a separately authorized behavior-change contract.

No productization control-plane update is required for this slice: maturity
tier, active lane, review/replay behavior, selected values and counting,
Matrix authority, output schemas, and writer authority are unchanged.

Current stack layer: PR #152 (`codex/package-0-identity-core`) squash-merged to
`master` as `2a7c582d`. PR #153 (`codex/package-0-attempt-lifecycle`)
squash-merged to `master` as `2c6bce93`; it carries the bounded Package 0
attempt-event prefix plus two review-driven fail-closed corrections to the
merged identity core: a request-pinned target list now requires its owner
snapshot at lineage composition, and targeted MS1-shape artifact minting
requires exact row columns. PR #154 (`codex/cid-nl-hypothesis-substrate`)
squash-merged to `master` as `6c5d77c4`; its merge tree is identical to the
independently reviewed head `76c831e5` and carries the bounded CID-NL
observation-to-diagnostic-`PeakHypothesis` substrate. Its review closeout binds
Targeted/Discovery evidence and MS1 traces to the exact request acquisition
source, binds Targeted projection to the request-pinned target snapshot and
exact scan, requires a finite direct Discovery scan precursor, normalizes policy
numbers before identity serialization, requires the Targeted scan precursor to
fall within the exact target's owner-defined ppm tolerance, canonicalizes
semantically equal numeric inputs, including signed zero, across the new
integration/evidence/observation/support identity owners without changing
existing null semantics. It closes the review-system root cause where hash
integrity and geometry-only tests were mistaken for proof that a freely
constructed `IntegrationResult` came from the supplied trace. Observation
minting now validates raw apex/height/area against the exact raw trace, requires
the declared selection RT/height to be a true maximum of the selected selection
trace (ties remain valid), and checks any declared AsLS/Gaussian15 facts against
the existing deterministic owner helpers; stale or forged integration values
fail before identity minting. It also rejects out-of-trace integration bounds,
uses physical integration bounds rather than padded trace points for DDA
support, and closes direct diagnostic-hypothesis construction at the
model-selection value boundary. The merged baseline retains the reviewed
closeout evidence: all seven CI shards (5,937 passed, 4 skipped) plus Ruff,
Import Linter, architecture
census, diagnostics index, docs audit, full-package mypy, and 84 focused no-RAW
tracer tests. Deferred Pt work remains excluded.

This section is the canonical progress tracker for this implementation wave.
It tracks execution and consumer wiring; it does not replace the productization
control plane, grant writer authority, or reinterpret a passing unit test as an
end-to-end method result.

Update this section after each merged logical slice, package-gate decision,
scope change, or new evidence tier, and before a branch handoff or PR closeout.
Do not update it for every mechanical commit. Every update must name:

1. the last consumer boundary that actually ran;
2. the strongest evidence reviewed;
3. the first missing handoff;
4. the next falsifiable loop;
5. any Product, Design, Evidence, or Execution unknown that blocks mutation.

Progress vocabulary:

- `not_started` — no executable owner-backed path exists;
- `contract_only` — typed owner contracts and focused tests exist, but the real
  application consumer does not use them;
- `wired_no_raw` — the actual application path consumes the contracts in a
  no-RAW fixture;
- `evidence_passed` — the package's named parity/oracle gate passed;
- `blocked` — the next mutation requires an unresolved owner/product decision;
- `deferred` — intentionally outside the current tracer priority.

### Package checkpoint

| Package | Status | What is actually complete | First missing gate |
| --- | --- | --- | --- |
| Package 0 — executable no-RAW contracts | `evidence_passed` / complete | PR #151 merged the legacy comparator and shared `TargetedApplicationService`; PR #152 merged acquisition-source/request/attempt identity, canonical sample/target/review references, diagnostic evidence-artifact identity, retry-ancestor lineage validation, and fail-closed canonical request content. PR #153 merged owner-backed append-only lifecycle ordering. This branch closes manifest/work/resource/artifact/finalization and recovery-marker behavior for Targeted plus Alignment, then adds executable work-event/offset/metric availability, capability-aware trace coordinates, Targeted delivery-cell/revision/stage/seal candidates, CLI/GUI equal-adapter receipts, and the frozen successor receipt comparator. All remain no-RAW and `diagnostic_only`. | None inside Package 0. Remote Issue #149 state is unchanged because no GitHub mutation was authorized. |
| Package 1 — pure runtime and observation substrates | `evidence_passed` / complete | Merged PR #154 reaches exact request-source-bound DDA evidence → physical MS1 observation → chromatographic support event → diagnostic owner-backed `PeakHypothesis`. The completed runtime slices add a Targeted-owned canonical result identity, pickleable process-worker envelope, same-request/attempt `EvidenceArtifactRef` settlement, manifest-ordered application consumption, resource lifecycle, supervisor finalization, and the narrow recovery marker consumed by both Targeted and Alignment without serializing legacy DTOs or adding storage. Slice 2 now composes both real process consumers with the existing capability-aware batch acquisition and physical-observation owners: true spawned serial/reordered runs preserve immutable signal snapshots, source/kind coordinate truth, request/call/point counts, exact owner/observation lineage, replay idempotence, conflict quarantine, terminal dispositions, and byte-identical canonical receipts without invoking a writer. Its executable `DifferenceManifest` binds the explained internal carrier change to the immutable frozen comparator and one comparator-owned experiment entry that overwrites output paths, pins root identity, accepts only exact primitive no-RAW jobs, runs fixed Targeted/Alignment workers to synchronous drain, inventories final-state paths/bytes, and privately mints the receipt. | None inside Package 1. Package 2 selection/externalization and downstream method wiring remain inactive. |
| Package 2 — complete Targeted CID-NL method | `active` / `diagnostic_only` / no-RAW / code evidence passed, closure review pending | At frozen code head `c25da8bb`, the duplicate delivery is gone; successor cells solely supply writer values/counting; full selected integration and Backfill cells are runtime-bound; substitution regressions pass. The changed set is 79 passed and seven shards are 6,050 passed/4 skipped. Fresh code review found no product correctness blocker; its sole P1 is this tracker/handoff state drift. | Commit the docs-only coherence repair, then obtain a fresh independent reviewer `APPROVE` at the new frozen head. Stop on any unexpected selected peak/boundary/area/confidence/reason/counting/CSV-byte/Matrix/writer difference or any required public CLI/config/schema/writer-contract change. |
| Package 3A — bounded real-data lifecycle | `blocked` | No new RAW run was launched. | Package 2 no-RAW coherence, RAW preflight, sealed oracle inputs, runtime budget, and explicit operator authorization. |
| Package 3B — widened Untargeted Discovery | `deferred` | No widening is credited by this wave. | Full Targeted parity first; widened Discovery must reuse the same downstream core and keep production Matrix authority unchanged. Pt Pattern is not automatically selected as the widening provider. |
| Package 4 — compare and decide | `not_started` | None. | Full tracer evidence, rollback rehearsal, and successor-versus-strangler comparison. |

### CID-NL end-to-end checkpoint

| Method handoff | Contract / local object | Actual consumer wiring | Strongest evidence | Status |
| --- | --- | --- | --- | --- |
| Canonical acquisition input → processing request/attempt → coordinator lifecycle prefix | Implemented across the first two isolated stack layers; events require the owner-issued attempt and cover queued cancellation plus owner-content-driven manifest-preflight failure to a `failed` proposal without `attempt_started` | `TargetedApplicationService` and Alignment's existing process runner now consume one accepted attempt/runtime through the shared work/resource/artifact/finalization lifecycle. Their owner-path marker distinguishes launch-only coordinator loss from an acknowledged durable terminal record. Legacy `extractor.run` and product outputs remain unchanged. | Focused marker/Targeted/Alignment closure tests cover true spawn, reorder equality, failure, tamper, and coordinator loss; the current integrated gate is recorded once below. | `wired_no_raw` |
| Canonical sample/target/review references | Implemented locally through existing owners | Not yet proven through both complete tracer projections | Focused missing/ambiguous/stale/superseded tests | `contract_only` |
| Capability-bound XIC batch → owner-backed physical MS1 observations | Implemented by composing the existing acquisition and `PeakObservation` owners; runtime supplies neither peak selection nor integration truth | Targeted and Alignment real process consumers accept the same immutable batch receipt and exact observation-artifact closure through true spawn | Focused acquisition/observation/runtime/comparator tests cover serial/reordered Targeted/Alignment canonical equality, source/kind and signal immutability, request/call/point counts, owner/upstream rejection, replay/conflict quarantine, terminal dispositions, successful empty-projection rejection, the single comparator-owned final-state parity experiment, fixed result-provider ingress, and immutable exact-zero DifferenceManifest validation; the current integrated gate is recorded once below. | `evidence_passed` |
| Exact DDA/ddMS2 CID-NL evidence | Implemented in the third stack layer with exact request acquisition-source and scan identity; Targeted projection also binds the measured scan precursor to the exact target's ppm tolerance, while direct Discovery projection requires its finite measured scan precursor | Targeted/Discovery adapters can validate bounded refs; main application path does not carry them end to end | Focused synthetic no-RAW source/target/scan/direct-precursor substitution tests | `contract_only` |
| CID-NL evidence → physical MS1 peak | Implemented in the third stack layer as owner-backed applicability refs with request-source-bound raw/selection-trace provenance, in-trace observation bounds, signal-validated true apex/heights/raw area/AsLS/Gaussian15 facts, signed-zero-stable identity, and DDA support matched to physical integration bounds rather than padded trace points | `run_discovery` does not yet emit the complete owner-backed chain | Focused stale-integration, false-apex, signed-zero, field-mutation, substitution, ambiguity, and boundary tests | `contract_only` |
| Repeated strict scans → one chromatographic support event | Implemented in the third stack layer | Not consumed by the real Discovery runtime | Focused no-RAW aggregation tests | `contract_only` |
| Support event → CID-NL `PeakHypothesis` | Implemented in the third stack layer as a diagnostic branch; semantically equal signal-consistent integration numbers preserve one physical content/artifact identity | Selection/model-selection/product boundaries, including direct `PeakModelSelectionResult` construction, intentionally reject it | 84 focused no-RAW identity, signal/integration consistency, aggregation, downgrade, source/target/scan/direct-precursor/integration-scope, numeric-canonicalization, and authority-boundary tests | `contract_only` |
| `PeakHypothesis` → explicit selection/externalization | Discovery-owned `TargetedCandidateSetRef` binds the complete candidate universe, exact Targeted CID-NL projection per hypothesis, canonical sample, exact target snapshot/key/definition, request/attempt, and full artifact closure. `DiagnosticPeakHypothesisSelectionRef` consumes that immutable receipt; only a unique candidate is selected. | Selection, Alignment, and Backfill consume the same candidate-set receipt; foreign receipt/attempt/target/content and direct-state tampering fail closed | 12 candidate-set/selection/Alignment owner tests plus the 47-test integrated repair gate pass; legacy writer target validation now requires exact `Target` equality | `wired_no_raw_review_fixed` |
| Selected sample-local hypothesis → cross-sample Alignment | `TargetedHypothesisAlignmentInput` resolves the exact immutable candidate universe, then projects the selected owner-backed hypothesis into the existing `SampleLocalMS1Owner`/cross-sample group constructor with `owner_id == hypothesis_id` | A bounded three-sample no-RAW fixture groups two selected hypotheses while excluding no-candidate, ambiguous, and failed cells; reversed input order is canonical and foreign universes/duplicate cells fail closed | 26 focused no-RAW selection, Targeted Alignment, and existing owner-family contract tests pass; original hypothesis IDs are exact group owner IDs | `wired_no_raw` |
| Alignment-supported missing cell → evidence-based Backfill | `TargetedHypothesisCell` carries selection artifact, canonical sample/target, exact group/owner IDs, source hypothesis, selected integration, and terminal reason while delegating local evidence detection/integration to existing `build_owner_backfill_result` | Six-sample frozen no-RAW fixture contains two observed hypotheses, one eligible rescued Backfill, one completed-negative true missing, ambiguity, and Discovery failure. Missing source remains `unchecked`, unassessable trace remains `unassessable`, and non-unique correspondence remains `ambiguous`. | 12 focused method/Backfill tests pass after the repair; selected values and legacy projection remain unchanged | `wired_no_raw_review_fixed` |
| Backfill/observed states → existing table writer | `TargetedDiagnosticProjection` validates RT/area/intensity/boundaries/width/confidence/reason/NL token/counted state against the authoritative legacy `RunOutput`; no domain decision occurs in the writer | The canonical `TargetedApplicationService.execute_application` production composition admits only typed, integrity-clean Alignment and Targeted runtime carriers; `settle_targeted_diagnostic_delivery_candidate` builds render-only DTOs solely from successor projections before the unchanged `write_wide_csv` writes under `successor_attempts/<attempt>/xic_results.csv` | Frozen 403-byte `xic_results.csv`, SHA-256 `f0a471bd81046cefaa924d619b934957505ee22591b17820a73143f7b4126937`; equal-but-distinct legacy numeric objects do not propagate, a tampered legacy DTO still produces successor candidate bytes, exact-byte mismatch quarantines, and semantic mismatch stops before delivery | `wired_no_raw` |
| Full multi-sample no-RAW CID-NL lifecycle | One bounded six-sample fixture: two observed Discovery hypotheses, eligible Backfill, true missing, ambiguity, and failure | Same service runs the full diagnostic method; true spawned Targeted scheduler and Alignment runner settle the same receipt; failed/cancelled attempts quarantine, rollback retains diagnostic evidence and restores authoritative legacy output/current | Frozen code head `c25da8bb`: changed set 79 passed; seven shards 6,050 passed/4 skipped; frozen scientific/CSV/writer/Matrix evidence remains exact. Fresh review found no product correctness blocker; docs-only closure coherence and a new-head `APPROVE` remain. | `evidence_passed_review_pending` |
| RAW-backed CID-NL lifecycle | No | No | No new RAW evidence | `blocked` |

**Last application consumer boundary actually run:** the existing
`TargetedApplicationService.execute_diagnostic_method` ran the complete bounded
six-sample method through immutable diagnostic selection, existing cross-sample
Alignment, existing owner Backfill, legacy-validated Targeted projection, the
Targeted scheduler and Alignment process runner under true spawn, and the
unchanged `write_wide_csv` renderer in an attempt namespace. Both finalizations
were required before delivery. Independent QA replayed the same boundary and
verified the 403-byte CSV digest, exact-zero manifest, failure quarantine,
conflicting replay, rollback to legacy with prior current preserved, unchanged
writer blob, and zero Matrix/XLSX output.

**Package 1 closure evidence:** the focused Package 1 comparator and runtime
observability suites pass (49 tests). All seven CI-equivalent shards pass at the
integrated head: docs-config 493 passed/1 skipped, GUI 110 passed,
targeted-core 1,160 passed/1 skipped, alignment-core 2,488 passed,
product-gates-activation 872 passed/1 skipped, product-gates-evidence 623
passed, and diagnostics-tools 351 passed/1 skipped (6,097 passed/4 skipped
total). Ruff, full-package mypy over 474 source files, Import Linter (4 kept/0
broken), architecture census (1,314 modules/2,670 internal imports),
diagnostics index, validation retention, shard census, bootstrap check, and
docs audit with zero blockers also pass.

**Last Package 0 runtime consumer boundary actually run:** Targeted and
Alignment both supplied owner-minted, same-request/attempt
`EvidenceArtifactRef` results through the shared attempt/finalization lifecycle
and durable recovery marker. The closure fixture now additionally validates one
complete attempt/work observability ledger with monotonic offsets and explicit
availability for every required metric. A capability-aware no-RAW adapter
executes two `XICRequest` values through exactly one batch call and returns
source-bound native-scan coordinates. Targeted delivery-cell/revision/stage/seal
candidates compose existing owner refs only, and CLI/GUI receipts compare
canonical request, attempt semantics, lineage, scientific result, delivery
projection, and both frozen CSV digests exactly. The focused Package 0 closure
fixtures remain green; the single integrated verification record above covers
this boundary without duplicating per-shard counts.

**Current CID-NL semantic boundary:** at frozen code head `c25da8bb`, the
complete bounded application/runtime path is connected and the duplicate
delivery bypass is removed. Successor owner state solely supplies writer
numeric/counting projections; legacy values are comparison-only. Canonical
Targeted runtime bytes bind the complete selected integration and Backfill cell,
and post-spawn carrier validation detects Backfill scientific-field
substitution. Fresh code review found no product correctness blocker. Its sole
P1 was that this tracker and the active handoff still described those completed
repairs as open work; Package 2 remains active until that docs-only coherence
repair is committed and a reviewer approves the new frozen head.

**Current Product decision:** on 2026-07-26 the operator confirmed the
Targeted-first Package 2 endpoint is hard-exact `xic_results.csv`. Untargeted
Alignment Matrix/XLSX remains Package 3B and keeps separate authority; this
decision does not skip Package 0/1 gates or authorize Package 2 mutation early.

**First missing handoff and next falsifiable loop:** Package 0 and Package 1
have no remaining canonical exit. Package 2 is active at `diagnostic_only`;
CLI/GUI, Discovery, Alignment, Targeted runtime, typed delivery, quarantine,
rollback, successor-only projection, and complete runtime scientific binding
have passed code review. The next loop is docs-only: commit the canonical
tracker/active-handoff correction, verify retained packet/inventory/difference
evidence is unchanged, and obtain a fresh independent reviewer `APPROVE` at the
new frozen head. Any unexpected scientific, counting, CSV-byte, Matrix, or
writer difference is a stop condition and must be preserved as difference
evidence before further mutation.

**Remaining unknowns:** no Product, Design, or scientific-correctness unknown
remains inside Package 2's bounded no-RAW scope. The only open Execution gate is
a fresh independent approval of the docs-coherent frozen head. No retention,
product seal, current pointer, CAS, publication, generic storage, selected-value,
Matrix, or writer authority was selected. The validation tier remains
`diagnostic_only`; any need to change a public contract triggers the Package 2
hard stop.

**Deferred method note:** Pt Pattern work is related and retained, but its
Discovery method is not stable enough to drive this vertical tracer. It is not
the next `PeakHypothesis`, Alignment, Backfill, or writer task.

## Decision

The smallest credible wave is one evidence-complete CID-NL / NL116 vertical
tracer, not a repository rewrite:

1. implement the no-RAW identity, lineage, runtime, acquisition-observation,
   and shared-kernel seams already specified by the completed decision tickets;
2. run the complete Targeted method through constrained Discovery, cross-sample
   Alignment, evidence-based Backfill, selection, and its existing hard-exact
   delivery projection;
3. widen only Discovery coverage to exercise the same Alignment, Backfill,
   evidence, lineage, and runtime core under Untargeted policy, without granting
   Matrix or writer authority;
4. compare the successor tracer with both the frozen legacy oracle and the
   smallest viable strangler;
5. rehearse whole-attempt rollback and record GO, NARROW, or KILL at a fixed
   evidence gate.

The wave may reach `shadow_ready`. It does not promise default promotion or
legacy retirement. Six weeks is the maximum planning envelope inherited from
the map, not the expected duration or a reason to idle between passed gates.

## Authority Boundary

This plan grants no scientific truth, selected-value, selected-area,
counted-detection, Matrix, workbook, activation, export, or `ProductWriter`
authority. Until a later explicit promotion decision:

- legacy Targeted and Untargeted paths remain the product authorities;
- successor output is isolated shadow evidence;
- a `ProcessingAttempt` uses one authority path end to end and may not splice
  legacy and successor decisions;
- Targeted `xic_results.csv` bytes and semantics remain hard-exact unless a
  separately approved expected diff exists;
- Untargeted Matrix values and identity remain unchanged;
- no source format, persistence engine, or cache format becomes a domain owner.

## Frozen Inputs — Consume, Do Not Redo

The wave starts from clean `master` at `23c97ec1`. These completed artifacts are
inputs, not work-package research tasks:

| Decision input | Canonical artifact |
| --- | --- |
| Executable dependency boundary | `docs/architecture-contract.md` |
| Public-output and claim disposition | `docs/agent/architecture-public-contracts.md`; `docs/superpowers/validation/cid_nl_public_output_claim_catalog_v1.tsv` |
| Script/tool lifecycle and consumer inventory | `docs/agent/script-tool-lifecycle.md`; `docs/agent/script-lifecycle-family-snapshot-2026-07-23.tsv` |
| Shared kernel and policy seam | `docs/product/shared-processing-kernel.md` |
| Processing identity, lineage, and delivery sealing | `docs/product/processing-lineage.md` |
| Canonical sample identity and retained review records | `docs/product/sample-metadata-qc.md`; `docs/product/review-roundtrip.md` |
| Acquisition and reusable-artifact capabilities | `docs/superpowers/validation/acquisition_source_capability_probe_v1/README.md` |
| Independent oracle and no-RAW packet contract | `docs/superpowers/plans/2026-07-24-cid-nl-nl116-oracle-acceptance-contract.md`; `docs/superpowers/validation/cid_nl_oracle_packet_preflight_v1/README.md` |
| Runtime lifecycle, cleanup, retry, and metrics | `docs/product/execution-runtime.md` |
| Frozen legacy and Targeted-output behavior | `docs/superpowers/validation/cid_nl_legacy_baseline_v1/`; `docs/superpowers/validation/cid_nl_targeted_csv_contract_v1/` |
| Product maturity and promotion authority | `docs/superpowers/plans/2026-06-15-productization-control-plane.md`; `docs/superpowers/validation/productization_status_index_v1.tsv`; `docs/superpowers/schemas/productization_authority_manifest.v1.json` |

An input is reopened only when its checksum, public contract, owner, or
implementation assumption has materially changed. A new timestamp alone is not
an invalidation.

## Human Ownership And WIP

The operating model assumes one human scientific/product decision owner and one
self-hosted CI execution slot.

| Responsibility | Owner and limit |
| --- | --- |
| Scope, scientific labels, RAW authorization, expected differences, promotion, and risk acceptance | Human operator only |
| Bounded implementation and mechanical tests | Agent or developer inside one assigned owner surface |
| Formal blinded adjudication | Human reviewer using the sealed oracle packet; machine output cannot answer for the reviewer |
| Integration and release narrative | One integration owner; one PR awaiting human review at a time |
| Writing WIP | At most two disjoint writing worktrees during active implementation |
| CI WIP | One self-hosted test run at a time; do not fan shards into a matrix while runner concurrency is one |

The two writing lanes are fixed:

- **Lane A — processing/runtime substrate:** processing identity, attempts,
  events, runtime reducer, cleanup, metrics, artifact registry, and recovery
  finalization.
- **Lane B — observation/tracer substrate:** acquisition capabilities,
  `TraceQuery` / trace observation, scan coordinates, existing
  `PeakHypothesis` deepening, lineage binding, and Targeted/Untargeted
  projections.

They may share only frozen contracts and opaque identifiers. Full Targeted
Discovery/Alignment/Backfill integration, Untargeted Discovery widening,
public-output comparison, global ledgers, and promotion records are serial
integration work. If both lanes need to edit the same owner, the second lane
stops rather than creating a parallel model.

Agent throughput does not expand human review capacity. A second completed PR
must wait if the first is still awaiting scientific or public-contract review.

## Schedule Interpretation

The issue's six-week framing is a maximum operating envelope, not an estimate
that the work needs six calendar weeks. Work advances as soon as its evidence
gate closes, and adjacent packages may complete on the same day.

Any elapsed range discussed during execution is an agent-assisted wall-clock
estimate, not a human labour commitment. CI queue time, RAW runtime, and formal
human review are reported separately. The operator may move faster, pause,
narrow, or stop as actual evidence changes the plan.

## Evidence-Driven Execution Packages

### Pre-Implementation Gate — Issue #127

Issue #127 reviews this plan before any Package 0 implementation write begins.
Only a recorded `GO` opens Package 0. `NARROW` or `REDRAW` revises the package
first; neither verdict permits interfaces or executable gates to be frozen in
code while the challenge remains open.

### Package 0 — Turn Decisions Into Executable No-RAW Gates

**Write scope**

- freeze the smallest concrete interfaces needed by the tracer;
- define one canonical headless Targeted application-service contract; CLI and
  GUI are input, lifecycle-control, and presentation adapters over that service,
  not separate scientific or orchestration owners;
- add pure tests for processing identity, attempt/event transitions, retry,
  cancellation, exactly-once cleanup, resource loss, artifact disposition,
  two-phase delivery sealing, trace capability, and dependency direction;
- add an equal-adapter invariant: the same normalized source set and `cid-dR`
  preset must produce equivalent canonical requests, attempt semantics,
  lineage, scientific results, and delivery projection through CLI and GUI;
- bind sample identity and retained human-review conclusions through explicit
  references to their existing canonical owners; filename, display name, row
  order, trace-array offset, lineage-local IDs, and mutable review rows are
  forbidden substitutes;
- classify the durable supervisor finalization marker as runtime recovery
  metadata, not product persistence or delivery evidence;
- create the frozen smallest-strangler comparison checklist before successor
  implementation starts, including exact legacy-comparator cell membership,
  public-output surfaces, and the separate oracle route for new shadow
  selections.

**Evidence**

- pure state and schema tests;
- headless CLI/GUI adapter-contract fixtures; GUI interaction receives a
  focused smoke, while scientific parity remains owned by the headless service;
- Windows spawn/no-RAW envelope smoke;
- static dependency and architecture census;
- owner-binding fixtures proving stable sample identity and immutable typed
  review-record references, including missing, ambiguous, stale, and superseded
  fail-closed cases;
- hard-exact Targeted CSV fixture remains unchanged;
- no new RAW.

**Exit gate**

- all no-RAW contracts are executable;
- neither CLI nor GUI can bypass the canonical Targeted application service or
  retain a second Discovery/Alignment/Backfill orchestration path;
- canonical sample-identity and immutable review-record references are
  executable from both tracer projections; an owner that cannot provide an
  unambiguous reference stops Package 0 rather than creating a substitute;
- no workflow-specific scientific flag leaks into runtime or acquisition;
- the proposed interfaces require no generic `core/`, storage engine, or second
  decision model;

### Package 1 — Build Two Disjoint Pure Substrates

**Lane A — processing/runtime**

- implement deterministic run/request/attempt identity and append-only events;
- implement the pure runtime reducer, manifest settlement, cancellation,
  retry/resume tokens, resource lifecycle, cleanup, failure aggregation,
  observability, and coordinator/supervisor finalization;
- keep all scientific and writer policy outside the runtime.

**Lane B — observation/tracer**

- deepen existing `peak_detection` and acquisition seams rather than creating a
  universal framework;
- implement capability-aware batch trace observations with truthful
  `native_scan`, `conversion_scan`, or `rt_only` coordinates;
- implement typed lineage bindings and no-write dual-policy projection fixtures;
- keep the current Thermo adapter and existing writer behind compatibility
  boundaries.

**Exit gate**

- both lanes pass their no-RAW tests from clean worktrees;
- neither lane imports GUI, CLI, diagnostics renderer, workbook, writer, or
  vendor implementation into domain types;
- no shared file or global ledger was independently rewritten by both lanes;
- the integration branch can consume both through the frozen Package 0 seams.

### Package 2 — Run The Complete Targeted Method

Targeted completion means the full batch lifecycle:

```text
constrained Discovery
  -> cross-sample Alignment
  -> evidence-based Backfill
  -> Targeted selection/projection
  -> xic_results.csv
```

**Write scope**

- adapt the Targeted process scheduler and Alignment process runner as the first
  two runtime consumers inside the complete Targeted method;
- route both CLI and GUI `cid-dR` execution through the same canonical Targeted
  application service; adapters may differ in start, cancellation, progress,
  and display mechanics, but not in normalized parameters, scientific stages,
  attempt semantics, or output projection;
- route constrained Discovery candidates into shared `PeakHypothesis`,
  evidence, and lineage carriers;
- preserve hypothesis identity across samples through Alignment;
- allow Backfill only after Discovery found the hypothesis, Alignment supports
  its cross-sample correspondence, and the missing sample cell has sufficient
  local evidence;
- project through the existing Targeted writer without recomputing domain
  decisions;
- build the smallest viable strangler comparator: existing Targeted behavior
  plus only the minimum identity/lineage wrapper needed for the same claims;
- keep legacy output authoritative and successor artifacts in a separate
  attempt namespace.

**Evidence**

- a bounded synthetic/frozen batch exercises Discovery, Alignment, at least one
  eligible Backfill, true missing, ambiguity, and failure;
- trace, hypothesis, alignment, Backfill, and selected-result parity;
- CLI/GUI parity for normalized request, lineage, scientific selections, and
  output when given the same source set and `cid-dR` preset;
- hard-exact CSV bytes and semantic assertions;
- cancellation, worker-loss, partial-artifact, and rollback tests;
- exact zero product-writer and Matrix differences;
- no RAW until this no-RAW full-lifecycle path is coherent.

**Exit gate**

- the complete Targeted method runs end to end;
- CLI and GUI reach that method through one application service, with no
  adapter-specific scientific branch or duplicate downstream orchestration;
- Targeted integration does not change selected peak, boundary, area,
  confidence/reason, counted state, output bytes, or public CLI/config;
- rollback routes the entire attempt to legacy and leaves the previous sealed
  delivery current;
- a failed successor attempt remains quarantined and cannot become a product
  result.

A trace-only, Discovery-only, or single-sample extraction smoke does not satisfy
this milestone.

### Package 3A — Run The Smallest Bounded Real-Data Targeted Lifecycle

No RAW run is automatic. Before each run:

1. run the XIC architecture preflight and RAW-validation gate;
2. name the exact RAW/source inventory, target, proposal, comparator, expected
   artifacts, runtime budget, and stop condition;
3. seal proposal, reference-universe, sampling, review, lineage, and parameter
   manifests required by the oracle;
4. repair the existing blind-plot portability gap without rerunning RAW when
   the retained bytes are sufficient;
5. obtain explicit operator authorization.

A one-RAW smoke may test only the acquisition adapter. Full Targeted acceptance
needs the smallest multi-sample batch that actually exercises Discovery,
Alignment, and an eligible Backfill opportunity. The frozen 8RAW workload is
preferred when it is the smallest existing comparable oracle. No 85RAW run is
implied by this architecture wave.

**Evidence**

- trace, scan-coordinate, hypothesis, alignment, Backfill, selected-result, and
  CSV parity;
- RAW opens, logical requests, batches, vendor calls, points, elapsed, memory,
  artifact I/O, and rebuild cost;
- cache-disabled baseline/candidate comparison in the same environment;
- focused manual EIC/MS2 review from a blinded, lineage-bound packet.

**Exit gate**

- no fabricated scan identity or silent RT-only promotion;
- vendor calls and hard-locality metrics meet the frozen comparator contract;
- elapsed/RSS budgets are preregistered before promotion judgment;
- any scientific or public-output delta is either an already approved expected
  diff or an immediate stop.

### Package 3B — Widen Discovery Without Forking The Core

**Write scope**

- widen Discovery coverage under Untargeted policy after full Targeted parity;
- feed the broader candidate universe into the same Alignment, Backfill,
  evidence, lineage, and runtime owners;
- verify that Targeted and Untargeted differ in Discovery coverage and
  application/output policy, not in downstream core ownership;
- exercise observed, evidence-based Backfill, ambiguous, unassessable, true
  missing/null, failure, and Targeted-only paired-evidence cases;
- keep Untargeted results out of the production Matrix.

**Evidence**

- constrained and broadened Discovery both preserve Alignment/Backfill
  invariants and exact locality;
- hard-zero selected-result difference within the frozen legacy-comparator
  cells, Targeted public writer-byte difference, and Untargeted production
  Matrix identity/value difference;
- newly recovered shadow selections outside the frozen legacy-comparator cells
  are adjudicated through independent-reference recovery, proposal precision,
  automated coverage, and bounded Untargeted non-inferiority rather than raw
  agreement with legacy;
- sealed proposal, independent reference universe, sampling manifest, review
  packet, adjudication, and lineage binding;
- the frozen Untargeted non-inferiority and automated-coverage gates;
- reviewer burden derived from the sealed sampling design, not from the earlier
  12-case calibration.

**Exit gate**

- required oracle gates return pass, preregistered NARROW, or `inconclusive`;
- missing inputs, invalid packets, authority leakage, state collapse,
  non-observed selection, or lineage defects fail closed;
- runtime remains workflow-neutral under broadened Discovery;
- if Untargeted coverage requires a second Alignment/Backfill implementation or
  Targeted-specific runtime flags, stop and narrow or kill the shared-core
  hypothesis.

### Package 4 — Compare, Rehearse Rollback, And Decide

No new feature is added during this package.

Compare the successor tracer and smallest strangler on:

- dependency crossings and forbidden imports;
- duplicate behavior, projection, and writer authorities;
- RAW opens, vendor calls, points, elapsed, memory, artifact I/O, and rebuild
  cost;
- replay and lineage completeness;
- public-surface blast radius;
- implementation, review, migration, and future change cost;
- scientific oracle results and unresolved selective risk.

Rehearse rollback by routing a complete attempt to legacy, preserving the prior
sealed delivery, quarantining successor artifacts, and proving no mixed
authority or stale current pointer remains.

Record exactly one operational verdict:

| Verdict | Required action |
| --- | --- |
| **GO** | The complete Targeted Discovery/Alignment/Backfill method passes hard-exact output, scientific oracle, locality, performance, replay, and rollback gates; widening Discovery reuses the same downstream core; the successor is materially cheaper to evolve than the strangler. |
| **NARROW** | Full Targeted works but only a proven subset such as identity, lineage, runtime, or constrained Discovery is cheaper or sufficiently evidenced. Retain that substrate behind legacy authority and open a smaller follow-up. |
| **KILL** | Two changed-hypothesis loops still fail full-lifecycle parity; truthful lineage is impossible; a Targeted-only or duplicate Alignment/Backfill core is required; public safety, cost, or rollback fails; or change cost is not better than the strangler. Remove or quarantine the unshipped successor slice and preserve reusable evidence/tests. |

Past implementation effort cannot change the verdict.

## Pre-Implementation Versus End-Of-Wave Decisions

The two decision vocabularies serve different times:

| Gate | Timing | Meaning |
| --- | --- | --- |
| Issue #127 `GO / NARROW / REDRAW` | Before any Package 0 implementation write | Is this package itself the best plan? `NARROW` or `REDRAW` revises the package before code. |
| Package 4 `GO / NARROW / KILL` | After full Targeted, Discovery widening, and comparator evidence | Did the implementation hypothesis survive? `KILL` means stop the tested successor direction rather than redraw history. |

## Promotion Ladder

Promotion is evidence-driven, not calendar-driven. This table records only the
states reachable or explicitly excluded by this wave; the durable authority
rules remain in the productization and processing-lineage owners.

| State | Entry criteria | Product authority |
| --- | --- | --- |
| `diagnostic_only` | Default through Packages 0–3; contracts, synthetic tests, and bounded tracer evidence may exist | None |
| `shadow_ready` | Complete Targeted Discovery/Alignment/Backfill and broadened-Discovery no-write paths pass; frozen comparator cells and public outputs preserve their hard-exact contracts; new shadow selections pass the independent oracle; lineage, exact locality, and rollback evidence are complete | Legacy remains sole product authority |

Opt-in, default promotion, and legacy retirement remain later product
decisions. This plan grants none of them.

## Rollback Contract

Package 4 rehearses the canonical whole-attempt rollback from
[`processing-lineage.md`](../../product/processing-lineage.md) and
[`execution-runtime.md`](../../product/execution-runtime.md): legacy remains
default, successor artifacts stay attempt-scoped and quarantinable, and the
last valid sealed delivery remains current. Cleanup and retention remain owner
actions outside runtime rollback.

Failure to demonstrate this rollback is a Package 4 KILL condition.

## Continuing Product Work

The main product is not frozen. Every new feature merged during the wave must
declare exactly one migration disposition:

| Disposition | Use |
| --- | --- |
| `legacy-temporary` | Urgent current-product behavior implemented in legacy with a named successor migration/retirement edge |
| `shared-contract` | Workflow-neutral domain or public invariant implemented once behind the accepted shared seam |
| `successor-first` | New capability whose owner exists only in the successor and can remain non-default until promotion |
| `retain-legacy` | Deliberate decision that the capability is outside the successor destination |

No feature may silently create a second `PeakHypothesis`, decision, batching,
cache, writer, or lineage owner. Any feature that changes selected values,
counted state, Matrix identity, CSV/workbook schema, CLI/config behavior, or
cross-repo handoff requires its own expected-diff and focused public-contract
gate.

## Documentation Budget

Documentation and governance are capped at 10–15% of the wave:

- update canonical owners only when a durable contract changes;
- prefer executable schemas, tests, claim catalogs, and short PR bodies over a
  second architecture narrative;
- keep this plan as the single transient execution map;
- retain compact evidence summaries and hashes, not command diaries or
  duplicate RAW bundles;
- defer broad docs cleanup, handbook expansion, and unrelated repo-structure
  work.

If documentation work exceeds the cap, stop and identify whether an owner is
missing or the implementation seam is still unclear.

## Open Inputs That Must Be Closed In-Sequence

These are not reasons to redo the completed decision tickets:

- exact elapsed/RSS budget for the named baseline/candidate environment;
- populated and sealed tracer proposal/reference/sampling/review/lineage
  manifests;
- formal reviewer-load estimate derived from the sealed sampling design;
- durable locator for the existing 12 blind calibration plots;
- concrete owner-backed sample-identity and immutable review-record APIs used by
  the tracer;
- post-wave default-promotion and legacy-retirement thresholds.

The owner-backed identity APIs block Package 0 closure and every later tracer
package. The elapsed/RSS budget, populated manifests, reviewer-load estimate,
and durable plot locator block only the affected RAW, formal-oracle, or Package
4 claim. Post-wave thresholds block default promotion and retirement, not a
`diagnostic_only` or `shadow_ready` tracer.

## Verification Of This Plan

Before resolving Issue #126:

1. verify every scheduled gate traces to the frozen inputs above;
2. verify the two writing lanes have disjoint owner surfaces;
3. verify no package grants scientific or product authority implicitly;
4. verify the RAW step is bounded, separately authorized, and has a no-RAW
   fail-fast path;
5. verify public-output and rollback rules are testable;
6. obtain human reaction to this prototype;
7. hand the accepted plan to Issue #127 for independent GO, NARROW, or REDRAW
   challenge before implementation begins.
