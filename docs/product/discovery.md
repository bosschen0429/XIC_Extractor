# Discovery

Discovery is the untargeted feature-discovery lane. It turns seed evidence and
MS1 traces into reviewable candidates. It does not directly publish product
matrices; publication requires the separate Backfill/ProductWriter authority
path.

## Contract

- Discovery produces candidate and review surfaces, not final product authority.
- Seed evidence can include MS2 neutral loss, precursor inference, MS1 trace
  support, and future evidence providers.
- CID-NL, HCD-PI, Delta Mass, and future configured methods are Discovery
  methods when they are used to find features matching method-specific evidence
  settings. They produce feature candidates plus typed evidence, not
  cross-sample identity or matrix writes by themselves.
- Standard per-sample outputs: `discovery_candidates.csv` (archival/alignment
  detail) and `discovery_review.csv` (compact human review).
- Discovery `feature_family_id` remains a public output header and dataclass
  field, but its Discovery meaning is a per-sample peak anchor label for
  candidates sharing the same discovered MS1 peak. It must not be treated as
  cross-sample identity, selected-peak truth, or Backfill promotion authority by
  itself.
- The peak-anchor schema migration intentionally removed legacy
  `family_context`, `feature_superfamily_id`, `feature_superfamily_size`,
  `feature_superfamily_role`, `feature_superfamily_confidence`, and
  `feature_superfamily_evidence` columns from `discovery_candidates.csv` and
  `discovery_review.csv`. There is no compatibility adapter for those columns;
  downstream consumers must read the current column constants or tolerate the
  missing legacy fields.
- Batch discovery handoff uses explicit index files. Do not depend on old
  worktree-local output directories as durable inputs.
- Minimal output modes are for fast inspection; standard outputs are the public
  machine handoff surfaces.
- Discovery UX and review surfaces are public handoffs when they define
  filenames, columns, row identity, output level, or downstream consumption.
- CID-NL Discovery is a bounded accepted slice, separate from Backfill. Future
  expansion needs successor-level tag context, MS1/quant support, provenance,
  value-delta framing, expected matrix effect, and explicit expected-diff review.
- Do not introduce vendor RAW-to-mzML conversion as an implicit product
  dependency without a separate public contract.
- The untargeted lane is an alignment, recovery, and primary-matrix hygiene
  layer. It addresses false missingness, RT-driven splitting, owner-centered
  recovery, duplicate-claim control, and Review/Audit separation. It is not a
  full LC-MS QA/QC normalization, artifact deconvolution, blank/background
  contaminant, isotope/adduct annotation, or post-acquisition normalization
  system; see [Untargeted Method](untargeted-method.md) for the absorbed
  method/literature boundary.
- Current untargeted product direction preserves a clean primary `Matrix` while
  keeping rescue-only, ambiguous, and diagnostic candidates in Review/Audit
  surfaces. Do not judge the lane by exact equality to old upstream feature
  tables when the accepted contract is matrix hygiene plus targeted benchmark
  recovery.

## Untargeted Product Flow

The intended product flow is:

1. Discovery finds sample-local feature candidates that satisfy configured
   method evidence such as CID-NL, HCD-PI, Delta Mass, or explicit composites.
2. Alignment resolves the cross-sample owner or group problem for those
   candidates using evidence. A Discovery hit is a candidate anchor, not
   cross-sample truth by itself.
3. Backfill revisits accepted candidates or aligned groups to recover cells
   that were not detected for normal signal, threshold, or workflow reasons.
   Backfill still requires provenance-valid evidence; it is not blind
   imputation and it does not create counted Discovery detections.
4. Product matrices are downstream-analysis surfaces only after the relevant
   projection, Backfill/ProductWriter authority, expected-diff, and schema
   contracts are satisfied.

## Evidence Provider Naming

Discovery strategy families name reusable evidence-provider classes. Current
and planned families include `neutral_loss` for CID-NL-style methods and
`delta_mass` for MS1 delta-pattern methods.

Concrete patterns or tags live under a family. `DNA_dR` is a neutral-loss tag;
`pt` is the first built-in delta-mass pattern. `Pt pattern` is the human-facing
method name, and `pt_pattern` may be used as a method or preset label for
`delta_mass(pattern_id=pt)`. Do not add `pt_pattern` as a separate primitive
Discovery strategy unless a successor design intentionally replaces the
strategy-family model.

Composite methods combine providers by explicit set operations, not by creating
new primitive strategies. The planned first composite is
`pt_pattern + cid_nl(dR)`, defined as `delta_mass(pattern_id=pt)` intersected
with existing `neutral_loss(tag=DNA_dR)` evidence on the same base peak. Union
or OR modes are out of scope until separately designed.

When an intersection method has a natural base seed, implement it seed-first
instead of running a broad provider over every possible anchor and filtering
afterward. For `pt_pattern + cid_nl(dR)`, CID-NL(dR) base candidates seed the
base feature IDs, then Pt pattern evidence checks only those bases for
`+1.0021`, `+2.0023`, and `+4.0052`.

Delta Mass / Pt pattern evidence has two layers. The first layer is a
scan-level delta-pattern seed: base / `+1.0021` / `+2.0023` / `+4.0052` are
seen together in MS1 scans often enough to propose a sample-local candidate.
The second layer is chromatographic co-elution confirmation: those four m/z
positions, viewed as XIC traces or trace-like featurelets, have close RT/apex
behavior and compatible peak shape. A scan-level hit by itself is a seed, not a
complete Pt pattern detection. A Pt pattern candidate is detection-grade only
when the delta-pattern seed and the co-elution evidence both support the same
candidate. The scan-level RAW proxy remains a diagnostic bridge for the seed
layer; it must not become Matrix authority or the whole product detector by
itself.

These providers create candidates and typed review evidence. They must not
directly grant ProductWriter authority, Backfill promotion, counted detection,
or Matrix changes.

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor/discovery/` | Discovery domain package |
| `scripts/run_discovery.py` | Discovery CLI entry point |
| `discovery_candidates.csv` | Detailed candidate table for archival or downstream handoff |
| `discovery_review.csv` | Compact review table |
| `discovery_batch_index.csv` | Batch handoff index for downstream alignment |

## Boundaries

- **Owns**: candidate generation, per-sample review surfaces, seed evidence
  handoff, batch index for downstream alignment.
- **Does not own**: ProductWriter authority or Backfill promotion (see
  [backfill.md](backfill.md)), final evidence truth for a candidate feature
  (see [evidence rules](../lc-msms-evidence-rules.md)), or release-slice
  readiness (see [productization.md](productization.md)).
- A Discovery slice must use Discovery vocabulary and authority; it must not
  reopen broad Backfill or expand the Backfill authority manifest by accident.

## Verification

Before changing discovery behavior, require the relevant subset of:

- Schema or snapshot tests for candidate, review, and batch-index outputs.
- Focused tests for seed evidence and MS1 trace handoff behavior.
- Performance/output-level review if a minimal or standard mode changes.
- Evidence-rule review if a new evidence provider changes candidate meaning.
- Downstream alignment handoff check when filenames or columns change.
  `tests/test_discovery_csv.py` and `tests/test_discovery_review_csv.py` own the
  current peak-anchor output schema.

## Pitfalls

- Treating `discovery_review.csv` as a product matrix.
- Treating discovery family membership, evidence tier, or review priority as a
  ProductWriter decision.
- Depending on old worktree output directories instead of explicit batch index.
- Hiding discovery output-contract changes in dated implementation notes.
- Treating a new evidence provider as ProductWriter authority without Backfill
  activation.
- Dragging CID-NL Discovery decisions into the Backfill lane.
- Starting untargeted performance work before timing/locality evidence
  identifies the bottleneck and correctness gate.

## See Also

- [Architecture contract](../architecture-contract.md) -- package ownership and
  dependency direction
- [Alignment](alignment.md) -- cross-sample handoff boundary
- [Peak anchor and group boundary](family-hypothesis-boundary.md) -- Discovery
  peak anchors, cross-sample groups, and PeakHypothesis authority
- [Untargeted Method](untargeted-method.md) -- durable method boundary
- [Evidence rules](../lc-msms-evidence-rules.md) -- evidence semantics
- [Productization](productization.md) -- product lane boundary and CID-NL scope
