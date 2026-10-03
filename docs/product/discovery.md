# Discovery

Discovery is the candidate-generation stage of the shared XIC lifecycle.
Untargeted retains every candidate qualifying under its configured evidence
space; Targeted supplies a `TargetSearchScope` that retains only configured
targets. Both may inspect a broad evidence policy such as dR, R, or MeR through
the same scientific task. Discovery turns seed evidence and MS1 traces into
reviewable candidates. Discovery does not directly publish product outputs.
Publication requires closed cell decisions and the relevant workflow
projection/writer authority.

Discovery is provider-specific at its input and initial-interpretation seam.
CID-NL, HCD-PI, Delta Mass, and future providers may consume different evidence
and use different candidate-generation strategies. They publish owner-backed
observations and hypotheses through the shared evidence contract so downstream
Alignment, ObservationRevisit, and Backfill owners do not require a parallel
lifecycle for each provider.

For CID-NL, `nl_tolerance_ppm` (default 20) is a per-ion m/z tolerance. A
direct product matches the configured loss when its absolute loss error is at
most `sqrt((precursor_mz × tol)^2 + (observed_product_mz × tol)^2)` Da, where
`tol = nl_tolerance_ppm × 1e-6`. The reported loss-error ppm still describes
the error relative to the configured loss mass; it is not the acceptance gate.

## Contract

### Scope and candidate state

- Discovery produces candidate and review surfaces, not final product authority.
- `broad candidate discovery` describes evidence-provider/search coverage, not
  an Untargeted-only implementation. Targeted narrows the accepted target/row
  universe without forking capture mechanics.
- Discovery candidate state is an execution boundary as well as a review
  label. Only `ms1_feature_nl_supported` and `ms1_feature_nl_rescued` are
  MS1-feature-backed inputs to Alignment RAW owner build. Candidate and review
  CSVs retain all feature-backed rows and only non-feature rows with a
  `scan_precursor` or `mixed` precursor basis. These review-only rows do not
  independently authorize another RAW XIC search, cross-sample grouping,
  Matrix identity, or Backfill. Pure CID-NL production planning preserves the complete canonical
  subject membership and query centers, but excludes subjects with no
  MS1-feature-backed candidate before sample-by-subject scheduling. Their
  candidates close with Discovery eligibility exclusions without method
  assessments. Each eligible subject still assesses the complete sample
  universe, including samples without a local candidate, for Backfill.
- Persistence filtering does not change classification or product-plus-neutral-loss
  precursor inference. `discovery_physical_facts.json` retains physical records
  only for feature-backed candidates, including their merge-member evidence;
  non-feature rows have no persisted physical facts. Receipts, batch-index
  counts and hashes describe the persisted candidate rows.
- Seed evidence can include MS2 neutral loss, precursor inference, MS1 trace
  support, and future evidence providers.

### Physical qualification

- Discovery's Gaussian15 physical point policy rejects an irregular background
  maximum only when both the acquired trace and its existing, unclipped
  baseline residual lack sufficient rise/fall shape or local apex relief.
  The physical owner qualifies the already selected interval; surviving peaks
  keep their original boundaries, apex and integrated area. Saved policies
  retain their original answer semantics, and other callers or smoothing-window
  settings keep their existing selection policies. This local qualification does
  not prove chemical identity or replace cross-sample support and Backfill
  eligibility.
- Discovery retains provisional qualification when it reuses a physical result.
  Candidate CSV carries the final `is_provisional` decision through Alignment
  replay, including merged candidates. Legacy CSVs without provisional evidence
  remain readable; legacy rows marked provisional without the decision field
  are rejected because their final qualification cannot be recovered exactly.

### Blank qualification

Build a reusable background library with
`python -m scripts.extract_blank_library --blank <RAW-or-directory> --output <library.json>`.
The acquisition and serialization owner is
`xic_extractor.discovery.blank_qualification`. This adapter accepts explicit
intensity, scan-recurrence, and sampling parameters. Use Discovery's
`--blank-dir` to rebuild a library during each cohort run with default
extraction parameters, or `--blank-library` to consume a prebuilt library.
Producing a library alone does not enable qualification.

- Blank qualification is disabled by default for newly configured ordinary
  mainline Discovery runs. Library resolution prefers an explicitly selected
  library, then a sample-local `blank_library.json.gz` or `blank_library.json`,
  then the packaged default library. ProcessingRunSpec binds each input to the
  selected library's content digest, and execution consumes the bound immutable
  snapshot. Reading a saved run for execution verifies the selected library
  against that digest. Historical saved settings and run specs keep their stored
  enabled/disabled value, bytes, and identity; changing the default does not
  retroactively change qualification when they are read or replayed. An enabled
  run without a library binding must be recompiled before execution. The CLI's
  explicit `--no-blank-qualification` opts out and takes precedence over
  `--blank-dir`. When qualification is enabled, an explicit blank directory is
  extracted anew; matching filenames alone do not establish reusable blank
  content.
- A short solvent blank with a different LC gradient is diagnostic background
  evidence, not a validated hard-exclusion reference for a longer sample method.
  Background references must distinguish clean pre-sample solvent injections
  from post-standard/post-sample carryover monitors and waste-directed runs.
  A method-matched blank is a prerequisite for evaluating feature removal, not
  proof that an m/z-only intensity rule is valid. The current library aggregates
  centroid m/z and maximum intensity; it does not preserve RT or qualify peak
  shape. Explicit opt-in requires method-appropriate validation.
- Instrument acquisition exclusion lists and software blank qualification are
  separate operations. An acquisition exclusion can prevent an MS/MS trigger;
  disabling software filtering does not recreate an unacquired spectrum. Do not
  linearly scale RT between different 15-minute and 60-minute gradients. A larger
  ion list alone does not establish chemical identity or chromatographic support.
- Blank qualification matches candidate precursor m/z within 10 ppm by default.
  A matched feature remains feature-backed when its MS1 height is at least 3×
  the matched blank's maximum intensity. Below 3×, it becomes
  `review_only_orphan_nl`, loses its feature-row binding, and is excluded from
  MS1-feature-backed owner build. Its candidate and review rows persist only
  with a `scan_precursor` or `mixed` basis. Qualification classifies downstream
  eligibility; it does not undo the physical trace-shape result or change the
  selected peak's boundaries, apex, or integrated area.

### Precursor inference

- CID-NL precursor inference supports both direct scan-precursor seeds and the
  bounded `product_mz + neutral_loss` basis when the inferred precursor remains
  within `DiscoverySettings.ms2_precursor_tol_da` of the measured filter/
  isolation precursor. The installed CLI exposes the same setting as
  `--ms2-precursor-tol-da`.
- Physical trace context and precursor-identity confidence are separate. A
  `product_mz + neutral_loss` group starts with the bounded configured RT
  context; when that trace is too short for the active signal-processing
  profile, Discovery may reacquire enough context only when at least two MS2
  seeds agree and their maximum product intensity reaches the profile's existing
  high-intensity threshold. Reacquisition keeps the original MS2 query RT and
  m/z tolerance. It supplies enough points to answer the physical peak question;
  it does not upgrade inferred precursor identity or grant Matrix authority.
- When the final physical evaluation of a pure `product_mz + neutral_loss`
  group resolves a peak, including after point reacquisition, Discovery reads
  the ion at the apex scan and one MS1 scan on each side, inside the final
  request, and reports the intensity-weighted reading as `ms1_apex_mz`. Groups
  sharing a physical request reuse the same final ion/isotope reading. MS1 is the method's accurate-mass scan, while ddMS2 runs at a
  quarter of its resolution, so product + loss only points at the ion. The
  reading supplies Alignment's physical XIC and grouping mass. The candidate's
  original precursor and product remain the keys for exact MS2 scan replay;
  retained Discovery physical facts keep their original acquisition query.
  Capture authorizes the current physical query separately from the mass used
  by its retained evaluation. Alignment evaluates a changed mass afresh even
  when the two XICs contain identical signal arrays. Readings spread wider than 5 ppm
  come from two ions and report none. A selected (`scan_precursor` or `mixed`)
  precursor reports none: in full85, about half of the selected precursors a
  reading would have moved landed on a neighbouring ion present at the selected
  m/z when the scan was triggered.
- Product + loss assumes a singly charged precursor. A pure inferred precursor
  whose MS1 ion is read with a centroid one doubly charged 13C spacing
  (0.50168 m/z, within the 5 ppm reading spread) above it, in most of the
  scans where the ion is read, becomes `review_only_ambiguous_coisolation` with
  a reason naming the doubly charged isotope, unless a singly charged seed on
  the same peak carries it. Selected precursors are not tested.
- Direct and inferred rows from one MS2 scan may coexist. `candidate_id` must
  retain the sample/scan anchor plus precursor/product row identity so those
  rows cannot collide, and `tag_evidence_json` must retain precursor-basis,
  measured scan-precursor, and delta provenance. This is a public identity and
  replay contract even when the CSV column set is unchanged.
- Candidate CSV preserves the full floating-point representation of
  `configured_neutral_loss_da`. Downstream replay compares this configured
  method value with the bound preset; display rounding must not change it.
- After physical MS1 evaluation, distinct MS1-feature-backed candidates may
  share one sample/tag/best-scan context, including pure
  `product_mz + neutral_loss` candidates. Sharing an MS2 scan does not make
  distinct MS1 features duplicates; evidence scoring ranks them without
  deleting either. Candidates that remain physically ambiguous still follow
  the `review_only_ambiguous_coisolation` gate. Downstream cell-decision owners
  enforce one final Matrix value per feature/sample cell.
- Untargeted CID-NL carries eligible inferred evidence into the shared method
  assessment alongside direct evidence. Canonical evidence retains the precursor
  basis, actual scan precursor, and configured inference bound; an inferred
  precursor and its derived loss are not reported as directly measured facts.
  Inferred evidence requires a qualifying relation to a usable physical MS1
  peak to support the method. CrossSample and cell-decision owners still decide
  identity, Backfill, and Matrix publication. Candidate eligibility alone
  grants none of these authorities.

### Methods and validation

- CID-NL, HCD-PI, Delta Mass, and future configured methods are Discovery
  methods when they are used to find features matching method-specific evidence
  settings. They produce feature candidates plus typed evidence, not
  cross-sample identity or matrix writes by themselves.
- Discovery validation is method-specific. CID-NL 8RAW/85RAW fixtures are not
  universal Discovery gates. The 8RAW fixture is a bounded subset of the 85RAW
  fixture and uses the same processing recipe, not a separate blank-qualification
  profile. Pt pattern work should use Pt-method real samples, trace/co-elution
  evidence, no-write authority checks, and independent human adjudication unless
  a later Pt owner explicitly names a different fixture. Reviewed Pt labels are
  method-development evidence, not ground truth or an accuracy oracle by default.

### CID-NL oracle formats

CID-NL/NL116 oracle inputs carry `diagnostic_only` evidence. The Python
validators own their exact fields, version tokens, and rejection rules.
The formats below separate tracer proposals, independent references, blinded
human judgments, and the gate decision.

| Format | Meaning and required boundary | Executable owner |
| --- | --- | --- |
| Tested-tracer proposal manifest | Freeze every candidate and its Targeted/Untargeted decision from one tracer attempt before holdout review. `select_value` rows define proposal precision; independent references define recovery. Blank candidate metrics do not remove a candidate from the frozen universe. | [`validate_proposal_row`, `validate_proposal_manifest_rows`](../../xic_extractor/discovery/cid_nl_oracle_contract.py) |
| Formal-holdout sampling manifest | Freeze blinded case and presentation assignments before review. `case_id` is the analysis unit, with exactly one primary presentation. Hidden duplicates measure repeatability only; adaptive supplemental cases are descriptive only. Probability samples retain inclusion probabilities, inverse-probability weights, and dependency groups. | [`validate_sampling_row`, `validate_sampling_manifest_rows`, `validate_sampling_statistical_binding`](../../xic_extractor/discovery/cid_nl_oracle_contract.py) |
| Oracle lineage binding | Bind proposals, cases, immutable review events, and gate results to canonical request, attempt, and evidence-artifact identities. Paths, display names, timestamps, row order, and case locators cannot replace lineage identity. Missing source bytes are `not_replayable`; missing required bindings yield `inconclusive`. | [`validate_lineage_binding_row`, `validate_lineage_binding_rows`](../../xic_extractor/discovery/cid_nl_oracle_contract.py); [Result Traceability](processing-lineage.md) |
| Review and adjudication events | Seal blinded sample-local identity, peak, and boundary labels before cross-sample policy review. Policy context cannot rewrite those labels. `unassessable` is a completed scientific judgment. Hidden duplicates use the same reviewer within each phase. Adjudication is append-only; unblinded taxonomy cannot change confirmatory labels. | [`validate_review_event`, `validate_review_event_set`, `validate_adjudication_event`, `validate_adjudication_event_set`](../../xic_extractor/discovery/cid_nl_oracle_review.py) |
| Independent reference universe | Use independent, sample-local, method-matched evidence for expected controls and supported positives. Tracer proposals, legacy calls, and library membership cannot establish sample presence or the recovery denominator. Declare shared enumeration dependencies; shared candidate or decision logic cannot claim independent recovery. ISTD presence does not establish its paired analyte. Out-of-scope and unassessable rows do not enter eligible denominators. Incomplete frames downgrade recovery claims. | [`validate_reference_row`, `validate_reference_manifest_rows`](../../xic_extractor/discovery/cid_nl_oracle_contract.py) |
| Narrow-scope manifest | Freeze scope precedence, exact case membership hashes, required gates, and allowed overall failures before reveal. Membership binds existing primary holdout rows and must agree with the structured scope. Derive `NARROW` from sealed membership and raw gate results; do not select a passing scope after reveal. | [`validate_narrow_scope_row`](../../xic_extractor/discovery/cid_nl_oracle_contract.py), [`reduce_oracle_verdict`](../../xic_extractor/discovery/cid_nl_oracle_verdict.py) |
| Statistical-design manifest | Preregister estimands, thresholds, planning inputs, grouping keys, review budget, and uncertainty routes before reveal. A finite census describes its frame; sampled inference follows the probability and dependency design. Nonprobability challenges remain descriptive. Incomplete frames require partial bounds or a known-reference claim. Machine `hold` remains in unconditional recovery denominators; human unassessable cases are reported separately. | [`validate_statistical_design_row`, `validate_sampling_statistical_binding`](../../xic_extractor/discovery/cid_nl_oracle_contract.py) |

Seals bind the actual artifact bytes, canonical rows, derived counts, and
required identity coverage. Sealed rows remain append-only. Corrections require
a new manifest or packet with a supersession edge. Post-reveal integrity incidents
yield `inconclusive`; they do not authorize case removal, relabeling, or
denominator recomputation. `validate_manifest_seal` and
`validate_review_label_seal` enforce the supplied seal contract and byte bindings.

These inputs grant no compound-identification, selected-area, counted-detection,
Matrix, Backfill-write, workbook, GUI, activation, or export authority.
[Product Validation](../agent/product-validation-contract.md) owns readiness and
promotion semantics; [Result Traceability](processing-lineage.md) owns the
canonical lineage identities.

### Outputs, peak anchors, and batch handoff

- Standard per-sample outputs: `discovery_candidates.csv` (archival/alignment
  detail) and `discovery_review.csv` (compact human review). Both write
  floats at full precision (the shortest text that reads back to the same
  value), because Alignment reads the candidate m/z and RT back; display
  rounding belongs to the final Matrix.
- Discovery `feature_family_id` remains a public output header and dataclass
  field, but its Discovery meaning is a per-sample peak anchor label for
  candidates sharing the same discovered MS1 peak. Members must share the
  sample, RAW source and tag, have matching apexes and overlapping peak bounds,
  and stay within the configured precursor m/z tolerance of every other member.
  Intermediate masses cannot bridge separate peaks through chained matches;
  multiple hypotheses for the same peak remain allowed. The label must not be
  treated as cross-sample identity, selected-peak truth, or Backfill promotion
  authority by itself.
- Candidates of one sample whose MS1 traces share the apex scan are one
  Discovery peak when their precursor m/z agree within
  `precursor_mz_tolerance_ppm`. A precursor inferred as product + loss carries
  the MS2 product error, so two candidates of one ion can differ by more. A
  window that clips the ion shows only part of its peak, such as the tail, and
  reports that part with its own apex. When the two differ by at most twice the
  tolerance, they are one Discovery peak if the other apex lies inside the
  higher candidate's peak bounds (one peak on that trace) and the most intense
  MS1 centroids read at the two apexes, each within its own window, agree within
  the tolerance (one ion). The merged candidate keeps the higher candidate's
  m/z, peak and area.
- The peak-anchor schema migration intentionally removed legacy
  `family_context`, `feature_superfamily_id`, `feature_superfamily_size`,
  `feature_superfamily_role`, `feature_superfamily_confidence`, and
  `feature_superfamily_evidence` columns from `discovery_candidates.csv` and
  `discovery_review.csv`. There is no compatibility adapter for those columns;
  downstream consumers must read the current column constants or tolerate the
  missing legacy fields.
- Batch discovery handoff uses `discovery_batch_index`. For every sample,
  the Discovery owner binds the exact candidate CSV SHA-256/byte size, complete
  path-independent candidate payload/count, and content-bound acquisition
  source into one `DiscoveryCandidateUniverseReceipt`. A downstream real-data replay
  must reject truncated/substituted candidate bytes and a same-stem RAW whose
  content identity differs. Candidate and RAW paths remain locators rather than
  identity. The current producer writes candidate/review locators relative to
  the batch index so a complete Discovery directory remains readable after a
  rename or copy. The external RAW locator is not rewritten. Owner-closed
  execution requires the handoff and rejects legacy batch indexes. Do not
  depend on old worktree-local output directories as durable inputs.
- Minimal output modes are for fast inspection; standard outputs are the public
  machine handoff surfaces.
- Discovery UX and review surfaces are public handoffs when they define
  filenames, columns, row identity, output level, or downstream consumption.
### Scope limits

- CID-NL Discovery is a bounded accepted slice, separate from Backfill. Future
  expansion needs successor-level tag context, MS1/quant support, provenance,
  value-delta framing, expected matrix effect, and explicit expected-diff review.
- Do not introduce vendor RAW-to-mzML conversion as an implicit product
  dependency without a separate public contract.

### Untargeted lane

- The Untargeted lane runs Discovery, Alignment, and Backfill, with
  conservative primary-matrix delivery. The Untargeted lane addresses false
  missingness, RT-driven splitting, owner-centered Backfill, duplicate-claim
  control, and Review/Audit separation. The Untargeted lane is not a full
  LC-MS QA/QC normalization, artifact deconvolution, blank/background
  contaminant, isotope/adduct annotation, or post-acquisition normalization
  system; see [Untargeted Method](untargeted-method.md) for the absorbed
  method/literature boundary.
- Current Untargeted product direction preserves a clean primary `Matrix` while
  keeping Backfill-only (`rescue_only`), ambiguous, and diagnostic candidates in Review/Audit
  surfaces. Do not judge the lane by exact equality to old upstream feature
  tables when the accepted contract is matrix hygiene plus Targeted benchmark
  recall.

## Core Product Flow

The intended product flow is:

1. Discovery finds sample-local feature candidates that satisfy configured
   method evidence such as CID-NL, HCD-PI, Delta Mass, or explicit composites.
2. Alignment establishes cross-sample correspondence using evidence.
   Untargeted constructs owners or groups; Targeted may begin with configured
   target identity but still tests whether each sample-local hypothesis belongs
   to it. A Discovery hit is a candidate anchor, not cross-sample truth by
   itself.
3. Backfill revisits accepted candidates, aligned groups, or configured target
   cells to find results that were not counted for normal signal, threshold,
   or workflow reasons.
   Backfill still requires provenance-valid evidence; it is not blind
   imputation and it does not create counted Discovery detections.
4. Product outputs are usable only after the relevant projection,
   Backfill/ProductWriter authority, expected-diff, and schema contracts are
   satisfied.

Targeted applies this flow to a bounded target universe and ends in its own
delivery projection. Untargeted is the general form and ends in the analytical
feature-by-sample Matrix. Different projections do not create different
Discovery, Alignment, or Backfill cores.

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

Discovery evidence providers create candidates and typed review evidence.
Discovery evidence providers must not directly grant ProductWriter authority,
Backfill promotion, counted detection, or Matrix changes.

### Composite methods

Composite methods combine providers by explicit set operations, not by creating
new primitive strategies. The planned first composite is
`pt_pattern + cid_nl(dR)`, defined as `delta_mass(pattern_id=pt)` intersected
with existing `neutral_loss(tag=DNA_dR)` evidence on the same base peak. Union
or OR modes are out of scope until separately designed.

When an intersection method has a natural base seed, implement it seed-first
instead of running a broad provider over every possible anchor and filtering
afterward. For `pt_pattern + cid_nl(dR)`, CID-NL(dR) base candidates seed the
base feature IDs, then Pt pattern evidence checks only those bases for
`+1.002111`, `+2.002271`, and `+4.005213`.

## Pt Pattern Diagnostic Evidence

### Evidence layers and position roles

Delta Mass / Pt pattern diagnostic evidence has two layers. The first layer is a
scan-level delta-pattern seed: base / `+1.002111` / `+2.002271` / `+4.005213` are
seen together in MS1 scans often enough to propose a sample-local candidate.
The `+1.002111` position, experimentally referred to as the 195 position in the
current Pt workflow, is the preferred high-recall seed because it is expected to
carry the strongest Pt isotope signal. That seed role does not make `+1` the
candidate identity; when a Pt pattern is promoted to a candidate, the canonical
identity anchor remains `base` / the lowest m/z 0 position. The pattern is
defined as `0/+1/+2/+4`, not `-1/0/+1/+3`, because the `-1/0/+1/+3` frame would
depend on two adjacent one-Da gaps (`-1 -> 0` and `0 -> +1`). Those paired
one-Da relationships are more likely to be confounded by natural isotope
structure from other elements such as `13C` and `15N` than the current
`0/+1/+2/+4` evidence shape. The second layer is chromatographic co-elution
confirmation: those four m/z positions, viewed as XIC traces or trace-like
featurelets, have close RT/apex behavior and compatible peak shape. A
scan-level hit by itself is a seed, not a complete Pt pattern detection. When
both layers support the same row, the result is a stronger diagnostic
hypothesis, not a final detector call. The current matcher greedily retains
one compatible non-base feature per position for each base anchor. Its emitted
rows therefore represent one serialized assignment per base, not enumeration
of all compatible assignments; alternatives pruned during matching cannot be
recovered by a later conflict-graph replay. The scan-level RAW proxy remains a
diagnostic bridge for the seed layer; it must not become Matrix authority or
the whole product detector by itself.

Pt identity and quantitation intentionally use different position roles. The
`base` / 0 position remains the canonical hypothesis identity anchor so the
pattern frame does not move to the more isotope-confounded `-1/0/+1/+3`
alternative. If a later authority contract permits Pt quantitation, the `+1`
(experimental 195) position is the quantitative channel. The base position is
not the quantitative channel, and the four positions are not summed by
default. This role assignment does not grant current selected-area or product
authority: Pt pattern remains `diagnostic_only`, and its selected area stays
unavailable until a separate quantitation/activation contract is validated.

The science target for Pt pattern is a single Pt isotope envelope represented by
the base/+1/+2/+4 positions. The current diagnostic proxy cannot prove that
physical identity directly; it uses the observable proxy of a sample-local full
pattern plus close-apex co-elution support while ratio behavior and envelope
overlap remain under review. Therefore, dense RT clusters, suspected isotope
envelope collisions, and distorted position ratios are review evidence and
label-development signals first. In V1, a candidate that passes the full-pattern
seed and close-apex co-elution rule receives diagnostic
`trace_rule_call = accept` even when ratios look unusual. That field must not be
read as counted detection or human truth. Ratio and collision concerns may add
ambiguity/review flags, but they must not become a hard accept/reject gate until
an independent truth set shows that they separate physical Pt envelopes from
false patterns. Current human `uncertain` labels are unresolved evidence, not
true rejects.

### Mass arithmetic and candidate matching

Pt pattern m/z arithmetic uses shared mass-accuracy primitives: expected m/z is
the ppm denominator, exact Pt deltas are part of the built-in pattern contract,
and local `_ppm()` helpers should not be added to the core matcher path.

The Pt diagnostic candidate matcher consumes `DetectedFeature` rows, not centroid
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

`source_feature_row_id` is provenance, not automatic cross-provider identity
authority. Pt + CID-NL composite matching may compare `feature_id` or
`source_feature_row_id` directly with CID-NL `ms1_feature_row_id` only when the
detected feature declares the exact shared-namespace capability
`source = cid_nl_ms1_feature_handoff`. Local-MS1, MZmine, OpenMS, scan-proxy,
and unknown source IDs remain detector-local even when their strings happen to
match; they may participate only through the bounded same-sample m/z/RT
fallback. A declared shared-namespace mismatch must not fall back.

### Local MS1 feature tables

Local MS1 feature table generation is the candidate-source replacement path for
the scan-level Pt proxy. It does not by itself create product detections.
`scripts/build_local_ms1_feature_table.py` builds a
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

The local generator and Discovery's same-scan evidence consumers share
`xic_extractor.ms1_scan_index`. That owner fails closed when an MS1 scan has
malformed mass/intensity arrays or when a RAW source contains no MS1 scans.
Its index and Discovery consumer tests preserve this shared failure contract.

The reusable co-elution support semantics belong in
`xic_extractor.discovery.pt_pattern_trace_evidence`. Diagnostic tools may open
RAW files and write CSV/JSON probes, but they should call this package owner
instead of carrying their own Pt trace-support rules.

### Trace assessment

Pt pattern diagnostic trace assessments use Gaussian15-smoothed XIC intensity as the
decision basis for apex, shape similarity, active overlap, and support status.
Raw vendor trace points remain in the artifacts as audit/reference evidence;
they are not the default machine decision basis and are not quantitation
authority. The current positive `trace_rule_call` proxy is close-apex
co-elution: the full base/+1/+2/+4 pattern must pass the combined diagnostic
status, have all three non-base positions supported, keep the maximum apex
delta at or below the larger of 0.6 seconds and 1.25 acquisition intervals,
keep Pearson-to-base at least 0.90, and keep active overlap at least 0.50.
Shift-aware shape metrics stay in the artifacts as review evidence only.
A translated-shape match with shifted Pearson-to-base
at least 0.85 and absolute shift at most 45 seconds is reported as
`shifted_shape_review_only`, not as a positive trace-rule call; larger
shifted-only matches receive diagnostic reason `shape_shift_above_threshold`.
Neither reason is an adjudicated human reject or a product non-detection.
The blind-overlay sealed `machine_call` is a trace-review projection only. For
the current anchor-local schema, trace ambiguity, anchor segmentation, and boundary
review must all be explicitly `clear`; any missing, unknown, or review-required
state returns `uncertain` rather than becoming a threshold reject or final
detector decision.

### Artifact lineage and joins

Current Pt trace/review artifacts are sealed as a lineage chain. Full-context
trace summary/metrics/points use `pt_pattern_trace_diagnosis`; anchor-local
trace artifacts use `pt_pattern_anchor_local_trace_diagnosis`. Every row
carries a candidate-assignment SHA-256, a shared extraction SHA-256, and an
exact `full_context` or `anchor_local` role. The trace manifest binds those
identities to the candidate CSV, RAW hash, candidate slice, extraction
settings, decision/smoothing contracts, and every emitted trace artifact.
Current review packets use full or anchor-local v5; blind-overlay summaries
and sealed machine rows use full or anchor-local. Current consumers fail
closed on blank/historical schemas, missing manifests, stale fingerprints,
artifact swaps, role/schema conflicts, or full/anchor mixtures across files.

Trace-point validation remains bundle-scoped even when a review packet is a
bounded subset selected from that bundle. Blind-overlay writers and verified
loaders first validate every primary/context trace row against the complete
manifest-verified summary/metric identity set. Only after that validation may
they filter trace rows to packet candidates for rendering and accept-completeness
checks. Non-packet candidates therefore cannot escape integrity validation, and
their presence in the source trace bundle is not itself an error.

Human adjudication is bound to the exact review bundle, not just a logical row
ID. The blind-overlay summary hashes the review packet, primary/context traces,
gallery, and sealed calls. The historically named promotion step re-reads those
artifacts, requires the recorded machine call to equal the sealed row, and
emits `oracle_detection_status = not_asserted`. Threshold calibration and
reviewed-label gate replay must then match the promotion row back to the actual
review-packet SHA and candidate/sample identity. Legacy labels without that
chain stay pending/stale diagnostic context.

Candidate-to-trace-summary joins are one-to-zero-or-one by normalized
`candidate_id`. A matched row must also preserve `sample_stem`, `base_mz`, and
`base_rt_apex_min`; blank, non-finite, or conflicting identity fields fail
closed before Discovery evidence or a manual-review packet is built. Numeric
serialization differences such as `500` versus `500.0` are equivalent. Trace
summaries produced before round-trip identity serialization are stale when
their stored coordinates no longer match the candidate and must be regenerated;
the consumer does not infer precision that the old artifact discarded.

Candidate-to-Discovery-evidence joins use the same fail-closed principle across
ratio profiling, same-scan replay, and DDMS2 support replay. The evidence CSV
therefore carries `sample_stem`, `method_label`, `pattern_id`, `candidate_id`,
`base_mz`, and `base_rt_apex_min`. A reused ID is not sufficient: all six fields
must identify the same candidate payload. Version 2 consumers require an exact
one-to-one candidate/evidence key set and validate every pair before filtering
on `trace_rule_call`; rejected or otherwise non-selected rows cannot hide stale
identity. Evidence produced before those identity coordinates were written is
stale for these consumers and must be regenerated from the matching candidate
CSV.

Diagnostic synthesis treats an omitted optional artifact as unavailable, but it
does not treat malformed values inside a supplied artifact as missing evidence.
Counts must be exact non-negative integers, recall fractions must stay within
`0..1`, booleans must use recognized true/false values, identity m/z values must
be finite and positive, and subset counts cannot exceed their parent
population. An empty ratio population cannot carry non-empty quantiles.
JSON/CSV structures used by the synthesis must satisfy these input contracts;
invalid supplied evidence fails closed instead of being truncated or silently
replaced with zero/false.

### Gaussian15 time width

Because Gaussian15 is point-based, its time width depends on scan density.
Trace diagnosis therefore records `median_rt_step_sec` and
`effective_smoothing_window_sec` per position, plus
`min_median_rt_step_sec`, `min_effective_smoothing_window_sec`, and
`max_trace_point_count` per candidate. Scan-aware apex tolerance uses the direct
observed RT step; it does not reconstruct scan spacing by dividing the effective
window by the configured Gaussian15 point count because short traces use a
smaller effective odd window.
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

### Intensity ratios and RT shifts

Intensity ratios are useful Pt-pattern evidence but are not a machine gate yet.
Treat base/+1/+2/+4 ratios as ambiguity and review-priority signals until an
independent truth set shows they separate physical Pt envelopes from false
patterns beyond the close-apex co-elution metrics. Ratio is a future hard-gate candidate and
should be studied with explicit experiment design, starting from the current
Fullscan close-apex accepted set. Until that evidence exists, ratio fields only
support human review, hypothesis generation, and outlier explanation. The first
ratio profiling pass must not emit machine pass/fail decisions; it should emit
values, distribution summaries, data-driven outlier/review flags, and RT/mz
context. RT context must preserve the existing scan-aware close-apex semantics;
do not use coarse RT bins such as 0.25 min for Pt ratio profiling. If RT
sensitivity views are added, the upper exploratory bin/window should be
0.05 min / 3 sec. RT binning may be explored in a later hypothesis slice, but it
is out of scope for the first ratio profile. First-pass ratio outliers should be
based on quantiles/IQR within the
observed accepted set, not fixed FeatureHunter or theoretical thresholds. The
first profile should focus on the Fullscan close-apex accepted rows and use
rejected rows only as a background distribution summary; rejected rows must not
be treated as ratio-training truth in this slice. First-pass flags should stay
column-level, with only a thin candidate summary such as outlier count, extreme
columns, and review note. Do not emit ratio scores, ratio confidence, ratio pass,
or ratio fail. The first profile may include a static diagnostic HTML surface for
distribution plots, RT/mz scatter views, and outlier tables. That HTML is a
review/visualization artifact only: it must be labelled `diagnostic_only`, avoid
accept/reject/pass/fail language, and must not become an interactive product UI
or detector gate. First-pass ratio rows should carry references back to existing
trace/evidence/overlay artifacts, but the ratio profiler itself should stay
no-RAW: it must not reopen RAW files, re-extract XIC traces, or redraw overlay
evidence. The
preferred next evidence shape separates smoothed apex-height ratio, local
apex-window area ratio, and full-window area ratio: apex height highlights crest
distortion, local apex-window area describes the co-eluting core, and full-window
area is report-only context for tail, shoulder, or background enrichment. A
Pt-only reference envelope may be recorded as a loose reference, not as a hard
threshold; molecular isotope composition, adducts, co-eluting species,
saturation, and feature-picking artifacts can all distort the observed ratios.
FeatureHunter category-3 intensity ratio is a pairwise
`Precursor ion / Precursor ion'` formula. In the manual's positive delta-mass
figure, `Precursor ion` is the lower-m/z member and `Precursor ion'` is the
higher-m/z member, so historical positive-delta thresholds for `+1`, `+2`, and
`+4` correspond to `base/+1`, `base/+2`, and `base/+4`. If `+1` is used as the
Pt isotope-envelope reference because it is expected to be the strongest
position, then `+1/base`, `+1/+2`, and `+1/+4` are derived review ratios rather
than the raw FeatureHunter pairwise formula. The FeatureHunter formula is a
historical comparator and possible failure source, not a Pt-pattern authority:
ratio profiling should test whether FH-compatible `base/+delta` thresholds,
`+1`-normalized ratios, or some other bounded ratio family better explains the
human-adjudicated Pt candidates. Those labels may remain `uncertain` and are not
ground truth unless a later evidence contract establishes that status.
Smoothed-trace ratios must be labelled as decision-trace ratios, not raw
abundance ratios: smoothing stabilizes apex/shape decisions but can attenuate
or redistribute signal, especially when the effective smoothing window is short
or trace density is high. Raw trace ratios and raw bounded-area ratios may be
recorded as audit context, but raw traces do not become the default shape
decision basis unless a later independent truth set shows that they improve
discrimination.

Small RT/apex shifts just beyond the close-apex threshold may be review evidence
when expressed with `median_rt_step_sec`, apex-delta scan steps, and effective
smoothing-window context. They must not become a product detection without a
later independent truth set showing improved discrimination without
nearby-coeluter or tail-following failures.

### Current Pt validation pair

For the current 20251210 Pt validation pair, the Fullscan RAW is the Discovery
authority because it was acquired as high-resolution MS1 specifically to find
Pt-pattern candidates. The DDMS2 RAW has lower MS1 resolution and targeted/DDA
fragmentation coverage; it is useful as support evidence for Fullscan seeds,
but not as a global Pt-only denominator. A Fullscan-seeded DDMS2 support replay
therefore starts from Fullscan `accept` rows and asks whether the DDMS2 local
feature table contains matching base / `+1.002111` / `+2.002271` / `+4.005213`
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

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor/discovery/` | Discovery domain package |
| `xic_extractor/discovery/detected_features.py` | Shared sample-local feature contract, identity rules, validation, and feature-table adapters |
| `xic_extractor/discovery/delta_mass_strategy.py` | Delta Mass / Pt pattern settings and resolved strategy |
| `xic_extractor/discovery/local_ms1_features.py` | Local MS1 feature-source generator; screening evidence only, not trace/co-elution or quantitation authority |
| `xic_extractor/discovery/delta_mass_pipeline.py` | Pt pattern Discovery pipeline; writes feature-level candidate/review output plus Discovery evidence rows |
| `xic_extractor/discovery/pt_pattern_discovery_evidence.py` | Bridges feature-level Pt candidates with optional trace-summary rows; emits `pt_pattern_evidence.csv` rows and never grants Matrix/export authority |
| `xic_extractor/discovery/pt_pattern_evidence_rule.py` | Package-owned reusable diagnostic trace-rule classifier for trace-diagnosed rows; its accept/reject vocabulary is not human truth, counted detection, or detector activation |
| `xic_extractor/discovery/pt_pattern_trace_evidence.py` | Package-owned Pt trace/co-elution support semantics |
| `xic_extractor/discovery/pt_pattern_review_packet.py` | Bounded Pt manual-review packet builder; reviewed labels remain adjudication evidence, not ground-truth oracle authority |
| `xic_extractor/discovery/pt_pattern_blind_overlay.py` | Blind Pt MS1/XIC overlay review surface; visual traces and sealed machine calls share the Gaussian15 trace-decision basis, and sealed calls are not oracle authority |
| `xic_extractor/discovery/pt_pattern_oracle_promotion.py` | Historical-named bounded adjudication-packet builder; exact packet/gallery/sealed-call provenance is required, and the result remains `diagnostic_only` rather than ground truth, detector, or Matrix authority |
| `xic_extractor/discovery/pt_pattern_review_bundle.py` | Canonical sealed loaders for trace manifests, review-packet collections, blind-overlay bundles, and machine-call identity/role checks |
| `xic_extractor/discovery/pt_pattern_oracle_replay_contract.py` | Shared fail-closed replay binding from promotion rows to current manifest-verified review packets before threshold calibration or reviewed-label replay |
| `xic_extractor/discovery/pt_pattern_oracle_thresholds.py` | Diagnostic-only threshold calibration against an exact-SHA-bound reviewed-label packet using the reusable Pt evidence rule; resubstitution fit is not detector accuracy or activation |
| `xic_extractor/discovery/pt_pattern_diagnostic_gate.py` | Diagnostic-only replay wrapper for the reusable Pt evidence rule against manifest-verified review packets or full trace bundles; optional reviewed-label replay requires exact packet provenance and is not detector validation or activation |
| `xic_extractor/discovery/pt_pattern_ddms2_support_replay.py` | Diagnostic-only Fullscan-seeded DDMS2 support replay; not a DDMS2 global discovery denominator and not detector activation |
| `scripts/run_discovery.py` | Discovery CLI entry point |
| `scripts/prepare_delta_mass_detected_features.py` | Converts a normalized external detected-feature TSV or MZmine wide feature table CSV into standard `detected_features.csv`; no RAW access and no Matrix/Backfill authority |
| `scripts/build_local_ms1_feature_table.py` | Builds single-sample local MS1 `detected_features.csv`, summary JSON, and diagnostics CSV from one RAW file; diagnostic-only feature-source evidence |
| `tools/diagnostics/pt_pattern_replay.py` | Saved-CSV diagnostics: `assembly` explains candidate assembly failures, `conflicts` compares shared-feature claims, and `ddms2-support` checks support near Fullscan seeds; no RAW access or activation |
| `discovery_candidates.csv` | Detailed candidate table for archival or downstream handoff |
| `discovery_review.csv` | Compact review table |
| `discovery_batch_index.csv` | Batch handoff index for downstream alignment |

Pt pattern Discovery additionally writes `pt_pattern_evidence.csv`
(`pt_pattern_discovery_evidence`) and `pt_pattern_sidecar.json`
(`pt_pattern_sidecar`). `pt_pattern_evidence.csv` separates feature-level
full-pattern seed status from trace-level evidence. If no trace summary is
provided, trace rule fields are `not_evaluated`; if a trace summary is provided,
the shared Pt rule writes diagnostic `accept`/`reject` and legacy
`detected`/`non_detected` projections without changing Matrix, Backfill, counted
detection, workbook, or export authority. These fields are proxy assessments,
not final detector or human calls. The sidecar summary records trace
status/call/reason counts so
partial trace-summary coverage is visible without opening the full evidence CSV.
Future ratio or small-shift fields must follow the same no-write authority
contract: they can explain ambiguity or route manual review, but they do not
activate Matrix, Backfill, counted detection, workbook, export, or ProductWriter
behavior.

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

`tests/test_cid_nl_oracle_contract_validator.py` exercises invalid rows,
missing fields, sealed-byte mismatches, counts, and reveal timing.
`tests/test_cid_nl_oracle_gate_verdict_contract.py` and
`tests/test_cid_nl_oracle_synthetic_contract_suite.py` exercise gate reduction
and denominator behavior. Those tests prove engineering regression coverage.

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
- Treating CID-NL 8RAW/85RAW fixtures as required gates for Pt pattern or other
  Discovery methods whose evidence and sample design are not comparable.
- Depending on old worktree output directories instead of explicit batch index.
- Hiding discovery output-contract changes in dated implementation notes.
- Treating a new evidence provider as ProductWriter authority without Backfill
  activation.
- Dragging CID-NL Discovery decisions into the Backfill lane.
- Starting Untargeted performance work before timing/locality evidence
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
