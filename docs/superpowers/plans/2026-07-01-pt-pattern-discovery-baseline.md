Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: draft
Repo owner: docs/product/discovery.md
Doc exit rule: Refine into an implementation-ready Pt pattern plan before code
  changes; after implementation, absorb stable claims into product docs and
  retire this dated baseline.

Status: Pt pattern discovery draft, not implementation-ready
Source branch: codex/delta_mass_strategy
Source commit: e8545f72 docs: tighten delta mass shadow plan contract
Restored source path: docs/superpowers/plans/2026-05-28-delta-mass-shadow-introduction-plan.md

> Provenance note: this file started as the restored Delta Mass shadow baseline,
> but the active plan is now Pt pattern discovery. Delta Mass remains the
> extensible internal pattern family, not an alignment-only shadow audit. Do not
> implement the old `delta_mass_strategy = shadow` or 13-file validation
> contract from historical copies.

# Pt Pattern Discovery Baseline

## Plain Contract

Pt pattern is a discovery-time MS1 evidence provider. It is closer to CID-NL
than to a post-run audit: it finds feature candidates from a specific MS1
pattern, then the normal discovery, alignment, review, and Backfill rules decide
what can be counted or quantified.

First implementation target:

- built-in user-facing method name: `Pt pattern`
- internal discovery strategy family: `delta_mass`
- built-in `pattern_id`: `pt`
- polarity: positive only
- charge: z=1 only
- observed m/z deltas: `0`, `1.0021`, `2.0023`, `4.0052`
- output position labels: `base`, `pt_plus_1`, `pt_plus_2`, `pt_plus_4`

This document is still a draft because exact code hook points, public config
keys, schema version/column order, sample manifest, CID-NL base join predicate,
and the intensity-vs-area ratio choice remain open.

## Architecture Preflight Contract

Goal:

- Establish a current Pt pattern discovery baseline from the restored
  historical Delta Mass notes. The plan should later support Pt-only and
  Pt-plus-CID-NL method modes without reviving the old shadow-only audit
  contract.

Existing owner/helper to reuse:

- Put reusable Pt pattern discovery logic under `xic_extractor/discovery/`.
- Reuse existing CID-NL evidence; Pt pattern code must not recompute CID-NL or
  scan MS2 data.
- Reuse existing alignment and Backfill semantics for detected seeds,
  backfilled quant values, row identity, and ProductWriter authority.
- Use preset/config surfaces for strategy selection and pattern parameters.
- Reuse the existing preset strategy-family slot `delta_mass`; `pt` is the first
  built-in pattern under that family, analogous to a named neutral-loss preset
  value under `neutral_loss`.

New code location:

- Domain models and matching helpers: `xic_extractor/discovery/`, with names
  like `delta_mass_patterns.py`, `pt_pattern.py`, or similar.
- Review/sidecar rendering can use existing output helpers, but writers must
  render precomputed records only.
- Diagnostics or one-off audit CLIs may live under `tools/diagnostics/`, but
  reusable matching and models belong in package code.

Evidence provider role:

- Pt pattern produces candidate families and typed evidence.
- `pt_pattern` is a user-facing method/preset label for
  `strategy = delta_mass` with `pattern_id = pt`.
- `pt_pattern + cid_nl(dR)` is an intersection of the `delta_mass` Pt pattern
  evidence provider with existing CID-NL(dR) support on the base peak.
- Future HCD-PI combinations are allowed by the method model, but they are not
  first-sample acceptance scope.

Simplest product rule:

- Discovery seed requires a sample-local full Pt pattern.
- For the Pt-plus-CID-NL method mode, the same base peak must also have
  CID-NL(dR).
- Quantification uses the base peak.
- Backfill can fill accepted quant values for already established families, but
  Backfill is not counted detection.

Call-cost model:

- Match inside each sample's detected feature list, not by scanning full RAW
  scans.
- Use indexed `base + expected_delta` lookup rather than unbounded all-pairs
  matching.
- Backfill searches only around an already accepted Pt family and expected
  missing position.
- No full-run RAW scan, no global rediscovery during Backfill.

Public contracts at risk:

- Discovery strategy-family config/preset keys.
- Discovery candidate/review schema.
- Review workbook summary fields and the proposed `Pt Pattern Candidates`
  sheet.
- Machine-readable sidecar schema.
- Matrix values only when an active strategy and existing authority path allow
  them.
- Counted detection must not be redefined by this plan.

Validation gate:

- no-RAW tests for matching, ppm tolerance, full-pattern rules, method-mode
  composition, schema, and sidecar output.
- Pt-method real samples supplied by the user for candidate generation and
  sampled manual review.
- Manual confirmation from those candidates becomes the later regression oracle.
- 8RAW and 85RAW are not Pt pattern science gates because their analysis
  strategy and dosing design are not comparable.

Stop rule:

- Stop and redesign before implementation if a proposed change treats Backfill
  as counted detection, lets a sidecar directly write Matrix values, changes
  ProductWriter authority without expected-diff, uses Pt evidence as neutral
  mass or adduct identity proof, or requires OR/union strategy behavior in the
  first implementation.
- Do not start implementation until the Pt sample manifest, per-sample detected
  feature-list hook, config/preset key contract, review/sidecar schema contract,
  and CID-NL base join predicate are pinned in this plan or a successor plan.

## Pattern Definition

The first supported catalog contains one built-in pattern:

```text
pattern_id = pt
display_name = Pt pattern
charge = 1
polarity = positive
positions = [
  {label = base, delta = 0},
  {label = pt_plus_1, delta = 1.0021},
  {label = pt_plus_2, delta = 2.0023},
  {label = pt_plus_4, delta = 4.0052},
]
tolerance_ppm = 20
```

Rules:

- `base` is the lowest observed m/z anchor.
- Only `base + delta` is valid. `base - delta` is not valid.
- Every nonzero position must match its expected m/z independently:

```text
expected_mz = base_mz + expected_delta
mz_error_ppm = (observed_mz - expected_mz) / expected_mz * 1e6
```

- The first hard gate is `abs(mz_error_ppm) <= 20` for `pt_plus_1`,
  `pt_plus_2`, and `pt_plus_4`.
- `base` has `delta = 0`, so it is the anchor and is not evaluated with this
  position formula.
- Delta spacing error can still be recorded as evidence:

```text
observed_delta = observed_mz - base_mz
delta_error_da = observed_delta - expected_delta
```

- Delta error, intensity ratio, and area ratio are evidence fields, not
  separate pass rules in the first version.
- Rationale: Pt pattern discovery is matching observed MS1 peak positions. The
  hard mass window should therefore follow the usual absolute m/z ppm basis.
  Using expected delta as the hard-gate denominator would make the `+1` window
  about `0.000020 Da`, which is likely too narrow for discovery and inconsistent
  with the repo's normal m/z matching behavior.
- z=2 and higher are out of scope for the first implementation.
- Negative mode is out of scope for the first implementation.
- Pt pattern does not infer neutral mass or adduct identity.

## Discovery Rules

Discovery works from already detected features or peak candidates. It does not
begin by scanning RAW full scans.

Formal Pt discovery seed:

- single-sample full pattern group is present;
- `base` is present;
- `pt_plus_1`, `pt_plus_2`, and `pt_plus_4` are present;
- all nonzero positions pass `+/-20 ppm` against their expected m/z;
- RT is very close, with a first hard upper bound of `<= 30 sec`;
- sample-local peak shape or trace evidence supports the pattern.

Cross-sample behavior cannot create the first detected seed. It can support
review confidence only after at least one sample has already established the
family through the sample-local seed gate.

The 30 sec RT bound is a wide fail-closed ceiling, not the expected ideal
behavior. Real strong Pt candidates are expected to be much tighter. A later
implementation should score tighter RT spans more highly and may introduce a
`strong_rt_delta_sec` threshold after real sample review.

Intensity/envelope evidence:

- Pt intensity ratio is expected to be useful and probably stable.
- It is evidence, not a hard gate in the first implementation.
- The plan must not hard-code whether ratio uses apex intensity or integrated
  area until real samples are reviewed.
- If both are available, record both or make the basis explicit.

Sample role:

- sample role such as treated, control, blank, or QC is review evidence.
- sample role is not a hard gate in the first implementation.

## Strategy Semantics

First implementation method modes:

```text
pt_pattern
pt_pattern + cid_nl(dR)
```

Implementation meaning:

```text
pt_pattern = delta_mass(pattern_id=pt)
pt_pattern + cid_nl(dR) =
  delta_mass(pattern_id=pt) INTERSECT neutral_loss(tag=DNA_dR) on base
```

`pt_pattern` is the user-facing label. The primitive discovery strategy family is
`delta_mass`, and `pt` is the first built-in pattern. Do not add `pt_pattern` as
a new primitive strategy enum unless a successor plan explicitly replaces the
`delta_mass` family model.

Future method model may allow:

```text
pt_pattern + hcd_pi
pt_pattern + cid_nl(dR) + hcd_pi
```

Out of scope now:

```text
pt_pattern OR cid_nl
pt_pattern OR hcd_pi
any evidence union strategy
```

Detected seed semantics:

- Pt-only method mode: a sample-local full Pt pattern from
  `delta_mass(pattern_id=pt)` is a detected seed.
- Pt-plus-CID-NL method mode: a sample-local full Pt pattern plus existing
  CID-NL(dR) support on `base` is a detected seed.
- `pt_plus_1`, `pt_plus_2`, and `pt_plus_4` may have NL behavior, but that
  behavior is audit-only until real samples clarify whether they show `nl-dR`,
  `dR+1`, `dR+2`, or `dR+4`.

CID-NL integration rule:

- Pt pattern code consumes existing CID-NL evidence.
- It does not recompute neutral loss.
- For Pt-plus-CID-NL method mode, CID-NL(dR) must support the base peak.
- The base join predicate must be explicit before this strategy is implemented.
  Prefer a stable same-feature or same-peak identifier for the same sample. If
  existing CID-NL evidence only exposes m/z/RT/boundary fields, the implementation
  plan must define the exact tolerance and fail-closed behavior before code.
- Until that predicate is pinned, Pt-plus-CID-NL is a planned method mode, not an
  implementation-ready runtime value.

## Alignment And Backfill

Alignment anchor:

- cross-sample family/row alignment uses the base peak as anchor;
- supporting positions follow the base as evidence;
- the anchor fields are base m/z, base RT, base boundary, and base area or
  intensity.

Cross-sample behavior:

- At least one sample must establish the family through a detected seed.
- Other samples do not need full Pt pattern to align into the row.
- Cross-sample behavior is family/review confidence evidence after seed
  establishment, not a substitute for the first sample-local detected seed.
- Per-sample Pt completeness is review evidence:

```text
full
missing_one
base_only
absent
```

Counted detection and Backfill:

- `detected` and `backfill` keep the current repo meanings.
- Backfill values are accepted quantification values, not counted detections.
- `base_only` after family establishment is Backfill-style quant support, not a
  Pt detected seed.
- Backfill cannot create a discovery hit.
- Backfill can only fill positions or base quant values for an already
  established Pt candidate family.

Targeted Backfill search:

```text
expected_mz = base_mz + missing_delta
mz_window = expected_mz +/- 20 ppm
rt_window = near base RT, never wider than the discovery hard RT ceiling
```

Prefer existing weak peak candidates first. If needed, a later implementation may
use a targeted XIC window for area/intensity evidence through existing Backfill
mechanics, not through a new global RAW scanner.

## Outputs And Review

Matrix:

- Matrix stays clean.
- Matrix values, when an active strategy is implemented and authorized, use base
  quant values.
- Pt completeness, m/z errors, delta errors, RT span, and ratio evidence do not
  become Matrix columns.

Review surfaces:

- main review should show a compact Pt summary and candidate id;
- add a human-readable `Pt Pattern Candidates` sheet when workbook review is in
  scope;
- write a machine-readable sidecar for replay, tests, and debugging.

The fields below are a schema sketch, not a public contract yet. Before code,
replace this sketch with an exact contract table that names schema version,
owner constants, column order, empty values, sidecar filename, and workbook sheet
insertion point.

Main review summary sketch:

```text
discovery_source
pt_pattern_id
pt_pattern_status
pt_candidate_id
pt_rt_span_sec
pt_support
pt_strategy
pt_sample_pattern_status
```

`Pt Pattern Candidates` / sidecar detail sketch:

```text
candidate_id
sample_id
strategy
review_bucket
base_mz
base_rt
base_area
base_intensity
pt_plus_1_mz
pt_plus_1_rt
pt_plus_1_area
pt_plus_1_intensity
pt_plus_1_mz_error_ppm
pt_plus_1_delta_error_da
pt_plus_2_mz
pt_plus_2_rt
pt_plus_2_area
pt_plus_2_intensity
pt_plus_2_mz_error_ppm
pt_plus_2_delta_error_da
pt_plus_4_mz
pt_plus_4_rt
pt_plus_4_area
pt_plus_4_intensity
pt_plus_4_mz_error_ppm
pt_plus_4_delta_error_da
rt_span_sec
shape_support
sample_behavior_support
intensity_ratio_basis
area_ratio_basis
cid_nl_dr_on_base
notes
```

Review buckets:

```text
high_confidence
borderline
failed_near_miss
```

`failed_near_miss` is review/audit only. It must not create a formal candidate
family, enter alignment, enter Backfill, or write Matrix values.

## Config And Presets

Pattern configuration belongs in the existing preset/config system. Do not add a
standalone pattern file as the first public surface.

The block below is non-authoritative pseudocode. It must not be implemented
verbatim until mapped onto the current preset/config loader. Before code, replace
it with an exact public key contract: key names, legal enum values, default,
unknown-key behavior, built-in preset name, and custom-preset behavior.

Possible first implementation shape:

```toml
[discovery.delta_mass]
enabled = true
pattern_id = "pt"
deltas = [0, 1.0021, 2.0023, 4.0052]
tolerance_ppm = 20
rt_hard_max_sec = 30
require_base = true
require_full_pattern_for_seed = true
charge = 1
polarity = "positive"
```

Method labels are provisional until the implementation owner maps them onto the
current preset/config schema. The implementation plan must keep the primitive
strategy family as `delta_mass` with `pattern_id = pt`; `pt_pattern` may be a
built-in preset name, GUI label, or shorthand method label, but it must not
silently become a parallel primitive strategy enum.

```toml
[[tag]]
strategy = "delta_mass"
name = "pt"
# exact pattern serialization is not decided; do not reuse neutral-loss
# tag.value semantics unless the preset loader explicitly supports it.

method_label = "pt_pattern"
# optional composite method label, if exposed:
# composite_method_label = "pt_pattern+cid_nl_dr"
```

Extensibility:

- Internal pattern catalog should support future patterns.
- First implementation ships only the built-in Pt pattern.
- Config/preset may describe a pattern.
- GUI pattern editor is out of scope.

## Validation Plan

No-RAW tests:

- exact `base + delta` matching;
- no `base - delta` matching;
- z=1 only;
- positive only;
- per-position `+/-20 ppm` pass/fail for nonzero positions with expected m/z
  denominator;
- ppm boundary tests prove `base` is not evaluated as a matched position, and
  prove the `+1` tolerance is `(base_mz + 1.0021) * 20e-6`, not
  `1.0021 * 20e-6`;
- `base` required;
- discovery requires full pattern;
- Backfill cannot create a discovery hit;
- `pt_pattern + cid_nl(dR)` requires CID-NL(dR) support on base;
- CID-NL behavior on supporting positions is audit-only;
- `failed_near_miss` never enters formal candidate family, alignment, Backfill,
  or Matrix output;
- writer/render tests prove domain matching is precomputed outside writers.

Real-sample validation:

- Use the user's Pt method samples, not 8RAW or 85RAW.
- Fullscan gate: use the Fullscan Pt sample to produce `pt_pattern` candidates
  only.
- DDMS2 composite gate: use the DDMS2 Pt sample to produce
  `pt_pattern + cid_nl(dR)` candidates only after the CID-NL base join predicate
  is pinned.
- Sort and bucket candidates for sampled manual review.
- The first pass is candidate-generation, not oracle validation.
- Manually confirmed candidates become the later regression oracle.

First Pt sample pass outcome contract:

- `pass`: no-RAW contract tests pass; both planned method modes emit
  schema-valid review rows and sidecars; candidates can be sorted into
  `high_confidence`, `borderline`, and `failed_near_miss`; and the
  `pt_pattern + cid_nl(dR)` output contains only candidates with an explicit
  base-peak CID-NL support join.
- `fail`: output schema is invalid; `base - delta`, partial patterns, or
  `failed_near_miss` rows become formal candidates; failed-near-miss rows enter
  alignment, Backfill, or Matrix output; CID-NL support is attached to a
  non-base peak for the active method mode; or Backfill creates a detected seed.
- `inconclusive`: input manifest or sample role information is missing or
  wrong; the data are not Pt-method-relevant; CID-NL evidence is unavailable or
  cannot be mapped to the base peak; or manual review cannot inspect the required
  base/position/RT/ppm/ratio fields.

Explicit non-gates:

- 8RAW and 85RAW are not science gates for Pt pattern because their data
  strategy and dosing setup are not comparable.
- The old 13-file shadow validation set is not the current acceptance plan.
- Broad production readiness is not claimed by this document.

## Open Questions Before Code

- Exact sample manifest and file locations for the Pt method data.
- Exact current code entry point for per-sample detected feature lists.
- Exact config key names and preset placement.
- Exact review/workbook column insertion point and sidecar schema version.
- Whether ratio evidence should use apex intensity, area, or both.
- The first `strong_rt_delta_sec` scoring threshold after real sample review.
- Exact CID-NL base join predicate, including identifier-first matching or
  m/z/RT/boundary fallback tolerances.
- Whether HCD-PI should be modeled now as future metadata only or left out of
  the first method-mode enum/preset label set.

## Implementation-Readiness Test Gate

Before code starts, the implementation plan must name concrete tests for:

- preset/config loading, applying, defaulting, and unknown-key rejection;
- preset strategy namespace behavior, proving `delta_mass` is the primitive
  strategy family and `pt` is the built-in pattern, while `pt_pattern` remains a
  user-facing preset/method label;
- exact discovery review schema snapshots;
- exact sidecar filename, schema version, and column order;
- workbook sheet order and insertion point if workbook output is in scope;
- writer render-only behavior from a precomputed `PtPatternCandidateRecord` or
  equivalent typed record;
- `pt_pattern + cid_nl(dR)` failing closed when CID-NL evidence cannot be joined
  to base;
- Backfill not creating detected seeds or counted detections;
- Matrix remaining unchanged unless a separate ProductWriter authority and
  expected-diff contract explicitly allow an active write.

## Out Of Scope

- OR/union strategies.
- z=2 or higher.
- negative mode.
- neutral mass inference.
- adduct identity inference.
- GUI pattern editor.
- direct ProductWriter authority from Pt sidecars.
- redefining counted detection.
- treating Backfill as detection.
- reusing legacy Artificial Adduct modules as Pt evidence.

## Productization Note

This draft changes no current maturity tier, no active writer scope, no selected
area, no counted detection, and no Matrix authority by itself. A future
implementation that changes output schemas or active Matrix behavior must update
the status index, authority manifest, promotion contract, product docs,
expected-diff contracts, and focused tests in the same PR.
