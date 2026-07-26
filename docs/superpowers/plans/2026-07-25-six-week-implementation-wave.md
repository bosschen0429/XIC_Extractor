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

Last updated: 2026-07-26

Current validation status: `diagnostic_only`

Current issue gate: Package 0 / Issue #149 remains open

Current stack layer: `codex/package-0-attempt-lifecycle` stacks the bounded
Package 0 attempt-event prefix on `codex/package-0-identity-core`. Package 1
CID-NL substrate and deferred Pt work are excluded and remain later, separate
stack layers.

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
| Package 0 — executable no-RAW contracts | `contract_only` / in progress | PR #151 merged the legacy comparator and shared `TargetedApplicationService`. The first two isolated stack layers implement acquisition-source/request/attempt identity, canonical sample/target/review references, diagnostic evidence-artifact identity, retry-ancestor lineage validation, fail-closed canonical request content, and owner-backed append-only lifecycle ordering through queued cancellation plus a queued manifest-preflight failure to a non-acknowledged `failed` terminal proposal. | Real `RequiredWorkManifest` validation, offsets/metrics, durable terminal acknowledgement, resource lifecycle/cleanup, acquisition capability/trace coordinates, artifact disposition plus delivery-cell/revision/seal, equal-adapter `cid-dR` parity, and the executable successor comparator remain open. Issue #149 remains open. |
| Package 1 — pure runtime and observation substrates | `contract_only` / partial | Separate retained CID-NL commits reach exact DDA evidence → physical MS1 observation → chromatographic support event → diagnostic owner-backed `PeakHypothesis`; they are intentionally not in the Package 0 layers. | Runtime reducer/resource lifecycle and actual acquisition batch observations remain missing; no application consumer traverses the complete substrate. |
| Package 2 — complete Targeted CID-NL method | `not_started` | The operator confirmed hard-exact Targeted `xic_results.csv` as the first endpoint. Legacy stages exist, but the successor owner-backed chain is not wired through them. | After Package 0/1 gates close, wire selection/externalization → cross-sample Alignment → Backfill → existing hard-exact writer in one no-RAW multi-sample fixture. |
| Package 3A — bounded real-data lifecycle | `blocked` | No new RAW run was launched. | Package 2 no-RAW coherence, RAW preflight, sealed oracle inputs, runtime budget, and explicit operator authorization. |
| Package 3B — widened Untargeted Discovery | `deferred` | No widening is credited by this wave. | Full Targeted parity first; widened Discovery must reuse the same downstream core and keep production Matrix authority unchanged. Pt Pattern is not automatically selected as the widening provider. |
| Package 4 — compare and decide | `not_started` | None. | Full tracer evidence, rollback rehearsal, and successor-versus-strangler comparison. |

### CID-NL end-to-end checkpoint

| Method handoff | Contract / local object | Actual consumer wiring | Strongest evidence | Status |
| --- | --- | --- | --- | --- |
| Canonical acquisition input → processing request/attempt → coordinator lifecycle prefix | Implemented across the first two isolated stack layers; events require the owner-issued attempt and cover queued cancellation plus manifest-preflight failure to a `failed` proposal without `attempt_started` | Legacy `extractor.run` still consumes resolved inputs directly; no Targeted or Alignment runtime consumes the owner-backed attempt history, and no real manifest validator or durable finalizer is wired | Full CI-equivalent no-RAW gates plus focused identity, adversarial, and event-ordering tests | `contract_only` |
| Canonical sample/target/review references | Implemented locally through existing owners | Not yet proven through both complete tracer projections | Focused missing/ambiguous/stale/superseded tests | `contract_only` |
| Exact DDA/ddMS2 CID-NL evidence | Implemented locally | Targeted/Discovery adapters can validate bounded refs; main application path does not carry them end to end | Focused synthetic no-RAW tests | `contract_only` |
| CID-NL evidence → physical MS1 peak | Implemented locally as owner-backed applicability refs | `run_discovery` does not yet emit the complete owner-backed chain | Focused substitution/mutation/ambiguity tests | `contract_only` |
| Repeated strict scans → one chromatographic support event | Implemented locally | Not consumed by the real Discovery runtime | Focused no-RAW aggregation tests | `contract_only` |
| Support event → CID-NL `PeakHypothesis` | Implemented locally as a diagnostic branch | Selection/model-selection/product boundaries intentionally reject it | Focused no-RAW identity and authority-boundary tests | `contract_only` |
| `PeakHypothesis` → explicit selection/externalization | No authorized successor transition | None | None | `not_started` |
| Selected sample-local hypothesis → cross-sample Alignment | Existing legacy Alignment does not consume the owner-backed hypothesis | None | None | `not_started` |
| Alignment-supported missing cell → evidence-based Backfill | Existing legacy Backfill exists, but has no owner-backed CID-NL hypothesis/group/cell chain | None | None | `not_started` |
| Backfill/observed states → existing table writer | Legacy writers remain authoritative; successor projection is not wired | None for the successor path | Legacy output fixtures only | `not_started` |
| Full multi-sample no-RAW CID-NL lifecycle | No | No | No end-to-end artifact | `not_started` |
| RAW-backed CID-NL lifecycle | No | No | No new RAW evidence | `blocked` |

**Last application consumer boundary actually run:** legacy `extractor.run`
resolves sample metadata aliases and passes them to the Targeted backend. The
canonical request projects the same input to exact RAW-stem acquisition
bindings in focused tests, but the runtime does not yet consume that request or
attempt end to end. This is why the identity layer remains `contract_only`.

**Current CID-NL semantic boundary:** the last completed semantic seam is exact CID-NL evidence
→ physical MS1 peak → diagnostic `PeakHypothesis`. The first missing product
seam is `PeakHypothesis` → explicit selection/externalization → cross-sample
Alignment. Therefore Discovery, Alignment, Backfill, and table writing are not
yet complete as one CID-NL method, even though the local domain objects pass
focused tests.

**Current Product decision:** on 2026-07-26 the operator confirmed the
Targeted-first Package 2 endpoint is hard-exact `xic_results.csv`. Untargeted
Alignment Matrix/XLSX remains Package 3B and keeps separate authority; this
decision does not skip Package 0/1 gates or authorize Package 2 mutation early.

**First missing handoff and next falsifiable loop:** gap-audit and freeze only
the owner-supplied `RequiredWorkManifest` content needed for a missing
completeness attestation to drive the existing queued-to-failed proposal path
rather than a caller manually declaring failure. It must bind to the existing
attempt, use closed unique opaque work-unit keys, and must not mint a
runtime-local owner or identity. Stop if the attestation requires an immutable
owner reference that current code/docs/issues do not define. Keep this pure and
no-RAW; scheduler/resource integration remains Package 1 and the full
multi-sample writer fixture remains Package 2.

**Remaining unknowns:** no Product unknown blocks stack publication. The first
Design unknown is the explicit diagnostic-hypothesis selection/externalization
transition; the Evidence unknown is the absent complete application-consumer
fixture. No Execution unknown blocks this Package 0 lifecycle draft layer.

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
