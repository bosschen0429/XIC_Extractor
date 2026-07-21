# Mass Accuracy Standardization PRD

Doc placement: formal_repo_doc
Doc kind: spec
Doc lifecycle: active
Repo owner: docs/architecture-contract.md
Doc exit rule: Absorb durable shared-primitive and migration-order decisions
into docs/architecture-contract.md, absorb Discovery-specific claims into
docs/product/discovery.md, then retire this transient PRD through the docs
cleanup workflow after implementation.

Status: draft
Validation status: design_only
GitHub issue: https://github.com/Chao-hu-Lab/XIC_Extractor/issues/105

## Purpose

Standardize m/z tolerance and ppm arithmetic across XIC Extractor without
changing scientific behavior by accident.

The repo now has `xic_extractor.mass_accuracy`, but only the Pt pattern matcher
uses it. This PRD defines how to turn that primitive into a repo-wide shared
contract while preserving the different semantics currently embedded in XIC
extraction, local feature generation, Discovery, Backfill, Alignment, identity
coherence, and diagnostics.

The first implementation wave must be behavior-preserving unless a later slice
explicitly declares an expected diff and review gate.

## Architecture Preflight

Goal:
Standardize shared m/z accuracy math and migrate local ppm/window helpers in
small behavior-preserving slices.

Existing owner/helper to reuse:
`xic_extractor.mass_accuracy` owns shared m/z accuracy primitives. Package code
should call this module instead of adding new local `_ppm()` helpers when the
semantics match. Diagnostic CLIs may keep orchestration-specific wrappers, but
reusable math belongs in the package.

New code location:
Shared primitives stay in `xic_extractor/mass_accuracy.py`. Domain-specific
call sites remain in their current owning packages: `extraction`, `discovery`,
`alignment`, `instrument_qc`, and `diagnostics`. Do not create a parallel
diagnostic-owned math module.

Evidence provider role:
This is an engineering foundation. It does not create a new evidence provider,
does not change Pt pattern authority, and does not grant Matrix, Backfill,
counted-detection, workbook, selected-area, export, or ProductWriter authority.

Simplest product rule:
Every m/z comparison must state its reference semantics:

- expected/reference denominator for measured-vs-expected comparisons;
- symmetric denominator for unordered pairwise grouping and duplicate folding;
- explicit mDa floor only where instrument resolution or low-mass behavior
  requires it.

Call-cost model:
No migration slice may add RAW opens, vendor XIC calls, repeated TSV scans, or
per-row recomputation. Most changes should be pure arithmetic replacements with
focused tests. RAW-backed or output-diff validation is required only where the
public output values or candidate sets could change.

Public contracts at risk:
CSV/TSV ppm fields, candidate set membership, feature IDs, alignment family
membership, duplicate folding, Backfill support status, selected peak/area,
workbook sheets, and diagnostic gate summaries.

Validation gate:
Focused unit/contract tests for every helper migration; characterization tests
before moving pairwise grouping behavior; no-RAW artifact parity for Discovery
and Alignment surfaces; targeted RAW or expected-diff validation only for
public-output-affecting behavior changes.

Stop rule:
Stop if a migration would change candidate membership, selected peak/area,
alignment family identity, Backfill authority, counted detection, or exported
matrix values without an explicit expected-diff contract and user-reviewed
product decision.

## Current State

Implemented foundation:

- `xic_extractor.mass_accuracy.signed_ppm_error`
- `xic_extractor.mass_accuracy.absolute_ppm_error`
- `xic_extractor.mass_accuracy.mz_tolerance_da`
- `xic_extractor.mass_accuracy.mz_window`
- `xic_extractor.mass_accuracy.is_within_mz_tolerance`

Current semantics:

- ppm error uses `reference_mz` as denominator;
- tolerance windows are inclusive;
- a small boundary epsilon exists for floating-point edge cases;
- optional `minimum_da` is supported but not broadly used yet.

Current adoption:

- `xic_extractor.discovery.delta_mass_patterns` uses the shared primitive for
  Pt pattern feature-level matching.
- Built-in Pt deltas are exact values:
  `1.002111`, `2.002271`, and `4.005213`.
- A no-RAW Fullscan replay preserved all old 386 candidate keys and recovered
  two high-m/z candidates at base m/z `1612.6`.

Known non-adoption:

- XIC extraction and MS1 scan index still build local windows.
- Local MS1 feature generation still computes feature-link windows and ppm
  spread locally.
- Pt DDMS2 support replay and scan proxy still compute Pt lookup windows
  locally.
- Discovery grouping and MS1 Backfill still have local `_within_ppm` helpers.
- Alignment and identity modules still define multiple `_ppm`,
  `_ppm_error`, and `_ppm_delta` helpers.
- Several diagnostic tools still compute m/z windows or ppm deltas locally.

## Semantic Classes

### Class A: measured vs expected

Use `reference_mz` / expected denominator.

Examples:

- Pt pattern expected positions;
- DDMS2 support replay against expected Fullscan seed positions;
- identity coherence candidate-vs-request precursor/product/loss errors;
- neutral-loss and HCD product checks where an expected product or expected loss
  exists;
- targeted benchmark candidate-vs-target errors.

Safe shared primitive direction:

- `signed_ppm_error(observed, expected)` when the sign is reported;
- `absolute_ppm_error(observed, expected)` when only distance matters;
- `mz_window(expected, ppm, minimum_da=...)` for expected-centered lookup;
- `is_within_mz_tolerance(observed, expected, tolerance_ppm=...)`.

### Class B: unordered pairwise grouping

Use symmetric or explicitly documented pairwise denominator. Do not silently
replace this with expected-denominator math.

Examples:

- alignment edge scoring between two observed families;
- duplicate folding;
- cross-sample family compatibility;
- claim registry overlap checks;
- near-duplicate audits.

Shared primitive direction:

- add an explicit `symmetric_ppm_distance(left_mz, right_mz)` primitive if the
  migration preserves the current order-invariant behavior;
- keep local wrappers temporarily when the denominator differs by module and
  needs characterization first.

### Class C: XIC extraction windows

Use target/request m/z as the reference center.

Examples:

- `raw_reader.py` Thermo chromatogram mass range;
- `ms1_scan_index.py` local scan extraction window;
- RAW validation scripts comparing vendor XIC with local MS1 index output.

Shared primitive direction:

- use `mz_window(request.mz, request.ppm_tol)`;
- prove vendor and local scan-index windows remain numerically equivalent before
  changing higher-level extraction behavior.

### Class D: trace linking and detector internals

Use the detector's current anchor semantics until characterization proves a
better rule.

Examples:

- `local_ms1_features.py` feature-link windows around current m/z or last trace
  m/z;
- per-trace m/z ppm spread metrics;
- assignment edge ranking by ppm error.

Shared primitive direction:

- migrate one helper at a time;
- preserve edge ordering and trace assignment behavior;
- record if `last_mz`, current centroid m/z, or trace mean m/z is the intended
  reference.

### Class E: diagnostic-only views

Use shared primitives when convenient, but do not let diagnostic migration
change product behavior.

Examples:

- `tools/diagnostics/pt_pattern_scan_proxy.py`;
- `tools/diagnostics/untargeted_alignment_guardrail_metrics.py`;
- `tools/diagnostics/targeted_gt_alignment_audit_io.py`;
- `scripts/analyze_xic_request_locality.py`.

Shared primitive direction:

- diagnostics should not own new math semantics;
- if a diagnostic mirrors package behavior, call the same package primitive or
  keep a wrapper that names the package contract it mirrors.

## Initial Inventory

The first implementation task must refresh this inventory from the current
branch before editing code. The following list is the current PRD seed.

| Area | Current local math | Risk | Migration stance |
| --- | --- | --- | --- |
| Pt feature matcher | already uses `mass_accuracy` | low | done for core matcher |
| Pt DDMS2 support replay | expected m/z window and signed ppm | medium | Class A; migrate after output schema parity test |
| Pt scan proxy | expected m/z window and signed ppm | low | Class E; migrate as diagnostic-only parity |
| RAW reader XIC window | request m/z times ppm | medium | Class C; migrate with vendor/local window tests |
| MS1 scan index | request m/z times ppm | medium | Class C; migrate with scan-index XIC parity tests |
| local MS1 feature generator | trace-link window and ppm spread | high | Class D; characterization first |
| Discovery grouping | `_within_ppm(a, b)` using `b` denominator | medium | Class A if `b` is expected/reference; document before migrating |
| MS1 Backfill | `_within_ppm(a, b)` using `b` denominator | high | Class A but public Backfill behavior risk; no blind migration |
| Neutral-loss / MS2 seed logic | expected product/loss denominator | medium | Class A; migrate with seed-count parity |
| Instrument-QC HCD evidence | expected product denominator | medium | Class A; migrate with focused evidence tests |
| Alignment compatibility | `ppm_distance(reference, observed)` | high | classify field by field before migrating |
| Alignment edge/family/folding/ownership | symmetric pairwise ppm | high | Class B; likely add `symmetric_ppm_distance` |
| Identity coherence candidate matcher | signed expected ppm | medium | Class A; migrate with TSV field parity |
| Validation/diagnostic reports | mixed helpers | low to medium | Class E; migrate after package behavior is stable |

## Product Requirements

1. The shared primitive must make denominator semantics impossible to miss.
   Function names and docstrings must distinguish expected/reference error from
   symmetric pairwise distance.

2. The first migration wave must be behavior-preserving. Any changed candidate
   set, ppm value, selected peak/area, alignment family, Backfill support, or
   matrix/export row requires an expected-diff contract before implementation.

3. Package code must not add new local `_ppm()` helpers when an existing shared
   primitive matches the semantics.

4. Diagnostics may keep thin wrappers only when they mirror a package contract
   or when they are intentionally preserving an old artifact's historical
   calculation.

5. Public output changes must be explicit. A ppm field value change is a public
   output change even if the schema columns do not change.

6. Pt pattern exact-delta recovery must stay protected. Any migration touching
   Pt pattern matching or support replay must keep the high-m/z `1612.6`
   regression and the exact delta constants.

7. XIC extraction window migration must prove local scan-index and vendor
   extraction windows are still aligned. A helper replacement that shifts the
   requested mass range is not acceptable without a separate product decision.

8. Alignment pairwise grouping must not change denominator semantics without a
   characterization test and a stated expected diff. In most alignment places,
   preserving symmetric distance is safer than converting to expected-denominator
   error.

9. The final implementation plan must split work by semantic class, not by a
   repo-wide search-and-replace.

## Proposed Migration Sequence

### Slice 1: Primitive Contract Hardening

Goal:
Make `xic_extractor.mass_accuracy` expressive enough for both expected-error and
pairwise-distance use.

Scope:

- keep existing expected/reference functions;
- add or explicitly reject `symmetric_ppm_distance`;
- add tests that show expected-error and symmetric-distance differ when order or
  denominator matters;
- document when each primitive is allowed.

Validation:
Focused `tests/test_mass_accuracy.py`.

Stop if:
The new API makes existing Pt matcher semantics ambiguous.

### Slice 2: Low-Risk Expected-Reference Consumers

Goal:
Migrate modules whose current math already uses expected/reference denominator.

Candidates:

- `xic_extractor.discovery.pt_pattern_ddms2_support_replay`;
- `tools/diagnostics/pt_pattern_scan_proxy`;
- `xic_extractor.alignment.identity_coherence.candidate_matcher`;
- `xic_extractor.alignment.identity_coherence.source_mapping`;
- selected neutral-loss / HCD product checks after focused test review.

Validation:
Focused unit tests and no-RAW output parity for affected TSV fields.

Stop if:
Any public ppm field changes without a documented expected diff.

### Slice 3: XIC Window Consumers

Goal:
Use `mz_window(request.mz, ppm)` for local and vendor XIC extraction windows.

Candidates:

- `xic_extractor/raw_reader.py`;
- `xic_extractor/ms1_scan_index.py`;
- RAW/XIC validation scripts.

Validation:
Existing scan-index XIC tests plus a focused parity check that the computed low
and high m/z bounds match previous arithmetic for representative low, medium,
and high m/z values.

Stop if:
The change alters XIC extraction boundaries, RAW call count, or extracted point
sets without explicit approval.

### Slice 4: Discovery And Backfill Helpers

Goal:
Migrate `_within_ppm` helpers only after pinning which argument is reference.

Candidates:

- `xic_extractor/discovery/grouping.py`;
- `xic_extractor/discovery/ms1_backfill.py`;
- `xic_extractor/discovery/ms2_seeds.py`;
- `xic_extractor/neutral_loss.py`.

Validation:
Focused Discovery / Backfill tests plus no-RAW candidate/review parity where
available.

Stop if:
Candidate grouping, backfill support status, or neutral-loss gate counts change
without an expected-diff artifact.

### Slice 5: Local MS1 Feature Generator

Goal:
Preserve detector behavior while replacing local trace-link ppm arithmetic.

Candidates:

- `xic_extractor/discovery/local_ms1_features.py`.

Validation:
Characterization tests for edge construction, conflict component assignment,
trace linking, m/z ppm spread, and feature-quality labels. A single-RAW smoke is
useful only after no-RAW characterization is green.

Stop if:
Trace assignment, feature IDs, or feature counts change before the expected
behavior is understood.

### Slice 6: Alignment Pairwise Distance

Goal:
Reduce duplicated pairwise ppm helpers without changing family identity.

Candidates:

- `xic_extractor/alignment/edge_scoring.py`;
- `xic_extractor/alignment/family_compatibility.py`;
- `xic_extractor/alignment/folding.py`;
- `xic_extractor/alignment/claim_registry.py`;
- `xic_extractor/alignment/cross_sample_peak_groups.py`;
- `xic_extractor/alignment/ownership.py`;
- `xic_extractor/alignment/pre_backfill_consolidation.py`;
- `xic_extractor/alignment/near_duplicate_audit.py`.

Validation:
Characterization tests for pairwise ppm denominator behavior, family grouping,
duplicate folding, and edge scoring. Output parity must be established before
any alignment matrix or review TSV behavior is allowed to change.

Stop if:
Family membership, duplicate folds, matrix row identity, or Backfill ownership
changes without a reviewed activation/expected-diff contract.

## PRD Acceptance Criteria

This PRD is ready for implementation planning when:

- every local ppm/window helper is assigned to a semantic class;
- each class has a migration rule and validation gate;
- the first implementation plan names specific files and tests per slice;
- public-output-affecting changes have expected-diff requirements;
- non-goals remain explicit;
- issue #105 links to the PRD path.

## Non-Goals

- No repo-wide search-and-replace.
- No Matrix, Backfill, counted-detection, selected-area, workbook, export, or
  maturity-tier change.
- No claim that all ppm calculations should use the same denominator.
- No RAW-heavy validation before no-RAW characterization proves the migration
  is behavior-preserving.
- No diagnostic-only helper becoming product authority.

## Open Decisions

These decisions must be closed before implementation planning:

1. Should `symmetric_ppm_distance(left_mz, right_mz)` live in
   `xic_extractor.mass_accuracy`, or should pairwise alignment semantics stay
   behind alignment-owned wrappers?
2. Should `minimum_da` remain opt-in, or should specific low-mass windows adopt
   a default floor after instrument-method review?
3. Which public TSV/CSV ppm fields are allowed to change if the old denominator
   is scientifically wrong?
4. Should diagnostics preserve historical ppm values for archived reports, or
   always reflect current shared primitive semantics?
5. Which migration slice should be implemented first after Pt pattern:
   expected-reference diagnostics/support replay, XIC windows, or identity
   coherence?

## Recommended Next Step

Write an implementation plan from this PRD only after the Open Decisions section
is closed or deliberately scoped. The safest first implementation plan is:

1. harden `mass_accuracy` with pairwise-distance semantics;
2. migrate Pt support replay and scan proxy as no-write diagnostic parity;
3. migrate XIC extraction windows with exact bound parity tests;
4. postpone local MS1 feature generation and alignment pairwise grouping until
   characterization tests are written.
