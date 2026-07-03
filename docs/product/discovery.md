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

The product-facing Pt matcher consumes `DetectedFeature` rows, not centroid
scan peaks. A detected-feature source must provide m/z, apex RT, positive
height/intensity or area evidence. Area-only feature-detector tables are allowed
as an intensity proxy for feature-level matching, but candidates from such rows
are labelled `feature_source_detected` until trace/co-elution evidence evaluates
them. `scripts/run_pt_pattern_discovery.py` can read an existing
`detected_features.csv`, a normalized external feature table TSV, or a MZmine
wide feature table CSV; for external source paths it writes a canonical
`detected_features.csv` copy into the output directory before matching.
`scripts/prepare_delta_mass_detected_features.py` keeps the same conversions
available as a standalone preparation step.

Local MS1 feature table generation is the product-facing replacement path for
the scan-level Pt proxy. `scripts/build_local_ms1_feature_table.py` builds a
single-sample `detected_features.csv` from one RAW file using a MZmine-inspired
seeded chromatogram builder: mass-detected MS1 points above
`mass_detection_noise_level` are collected once, high-intensity points above
`min_trace_seed_intensity` define non-overlapping m/z seed ranges, and then all
mass-detected points in a seed range are segmented in scan order. A seed range
may contain multiple RT-local traces; `max_missing_scans` is enforced while
segmenting scan-ordered points, not by intensity order and not only as a later
quality score. The default `mass_detection_noise_level` is 25,000, matching the
lab's routine screening threshold. This keeps the product path at feature-source
level instead of tracing every centroid/noise point.
Gaussian15-smoothed bounded peak regions then produce screening features. Rows with
`source = local_ms1_feature` carry feature-source quality only:
`trace_quality = OK` can seed a base, `trace_quality = WEAK_SUPPORT` can
support non-base Pt positions, and neither value grants trace/co-elution
authority. The local generator also writes `local_ms1_feature_summary.json` and
`local_ms1_feature_diagnostics.csv`; its `height` and `area` values are
screening evidence with `area_contract = screening_only` and
`quant_ready = false`. The summary exposes `trace_builder_mode` at top level:
in `mzmine_seeded` mode, `candidate_edge_count` means mass-detected point count,
`conflict_component_count` means m/z seed range count, and degraded-assignment
counts are not applicable because bipartite assignment is not used.

The reusable co-elution support semantics belong in
`xic_extractor.discovery.pt_pattern_trace_evidence`. Diagnostic tools may open
RAW files and write CSV/JSON probes, but they should call this package owner
instead of carrying their own Pt trace-support rules.

Pt pattern trace decisions use Gaussian15-smoothed XIC intensity as the
decision basis for apex, shape similarity, active overlap, and support status.
Raw vendor trace points remain in the artifacts as audit/reference evidence;
they are not the default machine decision basis and are not quantitation
authority. Shift-aware shape support is bounded: a translated-shape match can
support co-elution only when shifted Pearson-to-base is at least 0.85 and the
absolute shift to the base is at most 45 seconds. Larger shifted-only matches
are review evidence, not machine `detected` Pt pattern calls. The close-apex
shape path stays stricter at Pearson-to-base at least 0.90.

Because Gaussian15 is point-based, its time width depends on scan density.
Trace diagnosis therefore records `median_rt_step_sec` and
`effective_smoothing_window_sec` per position, plus
`min_effective_smoothing_window_sec` and `max_trace_point_count` per candidate.
If the effective window is shorter than 8 seconds, the trace evidence adds
`short_effective_smoothing_window` to the ambiguity flags. This is review
context for dense or jagged traces; it does not by itself reject a Pt pattern
candidate, grant authority, or change Matrix/Backfill/export behavior.
Pt +4 can also carry `plus4_window_tail_dominance` when the own-max normalized
Gaussian15 trace has much larger window area and 25%-height span than the
base/+1/+2 reference traces and its normalized signal centroid is shifted late.
This flag is tail/window-shape review evidence, not an apex-height rule, and it
does not by itself reject a Pt pattern candidate.
The blind overlay HTML is a review surface, not the decision basis: each case
can display raw vendor traces, the current Gaussian15 point-smoothed trace, and
a 20-second time-smoothed trace side by side while sealed machine calls continue
to use the packet's trace-decision metrics.

For the current 20251210 Pt validation pair, the Fullscan RAW is the Discovery
authority because it was acquired as high-resolution MS1 specifically to find
Pt-pattern candidates. The DDMS2 RAW has lower MS1 resolution and targeted/DDA
fragmentation coverage; it is useful as support evidence for Fullscan seeds,
but not as a global Pt-only denominator. A Fullscan-seeded DDMS2 support replay
therefore starts from Fullscan `accept` rows and asks whether the DDMS2 local
feature table contains matching base / `+1.0021` / `+2.0023` / `+4.0052`
feature evidence within explicit m/z and RT windows. Until the historical
DDMS2 target/inclusion list is available, missing DDMS2 or MS2 evidence means
`not_evaluable_without_target_list`, not `reject`.

The DDMS2 support replay is intentionally `diagnostic_only`. RT window choice
can strongly change support counts across different acquisition methods, so
these rows can explain support or coverage, but they must not promote or demote
Fullscan detections, write Matrix/Backfill surfaces, count detections, or
activate export behavior.

`pt_pattern_evidence.csv` can join older trace-summary artifacts that predate
shifted-shape and ambiguity columns. Missing shifted-shape columns are treated
as blank optional evidence: they cannot support the shifted-shape accept path,
but they also do not invalidate close-apex trace evidence. This is backwards
compatibility for existing diagnostic artifacts, not a weaker detection rule.

These providers create candidates and typed review evidence. They must not
directly grant ProductWriter authority, Backfill promotion, counted detection,
or Matrix changes.

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor/discovery/` | Discovery domain package |
| `xic_extractor/discovery/detected_features.py` | DetectedFeature contract plus external feature-table adapter for Delta Mass / Pt matcher inputs |
| `xic_extractor/discovery/local_ms1_features.py` | Local MS1 feature-source generator; screening evidence only, not trace/co-elution or quantitation authority |
| `xic_extractor/discovery/delta_mass_pipeline.py` | Pt pattern Discovery pipeline; writes feature-level candidate/review output plus Discovery evidence rows |
| `xic_extractor/discovery/pt_pattern_discovery_evidence.py` | Bridges feature-level Pt candidates with optional trace-summary rows; emits `pt_pattern_evidence.csv` rows and never grants Matrix/export authority |
| `xic_extractor/discovery/pt_pattern_evidence_rule.py` | Package-owned reusable Pt full-pattern accept/reject rule for trace-diagnosed rows; not detector activation |
| `xic_extractor/discovery/pt_pattern_trace_evidence.py` | Package-owned Pt trace/co-elution support semantics |
| `xic_extractor/discovery/pt_pattern_review_packet.py` | Bounded Pt manual-review packet builder; not oracle authority until reviewed |
| `xic_extractor/discovery/pt_pattern_blind_overlay.py` | Blind Pt MS1/XIC overlay review surface; visual traces and sealed machine calls share the Gaussian15 trace-decision basis, and sealed calls are not oracle authority |
| `xic_extractor/discovery/pt_pattern_oracle_promotion.py` | Bounded Pt manual-oracle promotion packet builder; production-candidate only for reviewed oracle rows, not detector or Matrix authority |
| `xic_extractor/discovery/pt_pattern_oracle_thresholds.py` | Diagnostic-only threshold calibration against a bounded Pt manual oracle using the reusable Pt evidence rule; not detector activation |
| `xic_extractor/discovery/pt_pattern_diagnostic_gate.py` | Diagnostic-only replay wrapper for the reusable Pt evidence rule against review packets or full trace summary rows, with optional bounded-oracle replay; not detector activation |
| `xic_extractor/discovery/pt_pattern_ddms2_support_replay.py` | Diagnostic-only Fullscan-seeded DDMS2 support replay; not a DDMS2 global discovery denominator and not detector activation |
| `scripts/run_discovery.py` | Discovery CLI entry point |
| `scripts/prepare_delta_mass_detected_features.py` | Converts a normalized external detected-feature TSV or MZmine wide feature table CSV into standard `detected_features.csv`; no RAW access and no Matrix/Backfill authority |
| `scripts/build_local_ms1_feature_table.py` | Builds single-sample local MS1 `detected_features.csv`, summary JSON, and diagnostics CSV from one RAW file; diagnostic-only feature-source evidence |
| `tools/diagnostics/pt_pattern_ddms2_support_replay.py` | Writes the Fullscan-seeded DDMS2 support CSV/summary from existing feature/evidence CSVs; no RAW access |
| `discovery_candidates.csv` | Detailed candidate table for archival or downstream handoff |
| `discovery_review.csv` | Compact review table |
| `discovery_batch_index.csv` | Batch handoff index for downstream alignment |

Pt pattern Discovery additionally writes `pt_pattern_evidence.csv`
(`pt_pattern_discovery_evidence.v1`) and `pt_pattern_sidecar.json`
(`pt_pattern_sidecar.v3`). `pt_pattern_evidence.csv` separates feature-level
full-pattern seed status from trace-level evidence. If no trace summary is
provided, trace rule fields are `not_evaluated`; if a trace summary is provided,
the shared Pt rule writes `accept`/`reject` and `detected`/`non_detected`
without changing Matrix, Backfill, counted detection, workbook, or export
authority. The sidecar summary records trace status/call/reason counts so
partial trace-summary coverage is visible without opening the full evidence CSV.

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
