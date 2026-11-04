# Discovery

Discovery answers which sample-local signals warrant further evaluation. It
turns method-specific seed evidence and MS1 traces into reviewable candidates;
a candidate does not establish cross-sample correspondence or a final value.
The [processing story](../user/processing-guide.html#discovery) explains that
distinction with an example. This page owns the exact candidate and provider
contracts.

Untargeted retains every candidate qualifying under its configured evidence
space; Targeted supplies a `TargetSearchScope` that retains only configured
targets. Both may inspect a broad evidence policy such as dR, R, or MeR through
the same scientific task. Discovery does not directly publish product outputs.
Publication requires closed cell decisions, the relevant workflow
projection, and [Writer Authority](productization.md#writer-authority).

Discovery is provider-specific at its input and initial-interpretation seam.
CID-NL, HCD-PI, Delta Mass, and future providers may consume different evidence
and use different candidate-generation strategies. They publish owner-backed
observations and hypotheses through the shared evidence contract so downstream
Alignment, ObservationRevisit, and Backfill owners do not require a parallel
lifecycle for each provider.

This page owns candidate-generation and method evidence. For execution and
review commands use the [Untargeted guide](../user/untargeted-discovery.md) and
[Diagnostics Index](../../tools/diagnostics/INDEX.md). Dataset-specific Pt
results and acquisition limitations are retained with the
not as universal Discovery requirements.


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
4. Product outputs are usable only after the relevant projection and schema
   contracts and the [Writer Authority](productization.md#writer-authority)
   activation chain are satisfied.

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
Discovery evidence providers must not directly grant Backfill promotion,
counted detection, or Matrix changes.

### Composite methods

Composite methods combine providers by explicit set operations, not by creating
new primitive strategies. The planned first composite is
`pt_pattern + cid_nl(dR)`, defined as `delta_mass(pattern_id=pt)` intersected
with existing `neutral_loss(tag=DNA_dR)` evidence on the same base peak. Union
or OR modes are out of scope until separately designed.

Delta Mass evaluates its own hypotheses first, then annotates the base's
CID-NL(dR) match. Every hypothesis reaching layer 3 remains in the diagnostic
CSV, including `unmatched` bases. Matching uses the existing eligible CID-NL
states and sample-local m/z/RT predicate; it does not alter CID-NL decisions.

## CID-NL candidate contract

For CID-NL, `nl_tolerance_ppm` (default 20) is a per-ion m/z tolerance. A
direct product matches the configured loss when its absolute loss error is at
most `sqrt((precursor_mz × tol)^2 + (observed_product_mz × tol)^2)` Da, where
`tol = nl_tolerance_ppm × 1e-6`. The reported loss-error ppm still describes
the error relative to the configured loss mass; it is not the acceptance gate.

### Scope and candidate state

- Discovery produces candidate and review surfaces, not final product authority.
- `broad candidate discovery` describes evidence-provider/search coverage, not
  an Untargeted-only implementation. Targeted narrows the accepted target/row
  universe without forking capture mechanics.
- Discovery candidate state is an execution boundary as well as a review
  label. Only `ms1_feature_nl_supported` with an available, resolved physical
  evaluation enters Alignment. `ms1_feature_nl_rescued` retains a measurable
  MS1 peak without independent same-peak NL support; it is diagnostic only.
  `review_only_unresolved_peak_attribution` retains an unresolved shoulder or
  overlapping envelope without attributing its MS2 to one constituent peak.
  Candidate and review CSVs retain measured MS1 peaks, including held rows.
  Rows without an MS1 peak are retained only with a `scan_precursor`,
  `scan_monoisotopic`, or `mixed` precursor basis.
  Rejected rows cannot choose query centers, merge into admitted candidates,
  mint method subjects, supply review-support scans, or authorize Backfill.
  The complete Discovery receipt remains intact; eager and indexed Alignment
  select admitted IDs before physical grouping and subject membership.
  An all-rejected batch completes with no subjects, RAW jobs, or Matrix rows.
  Each admitted subject still assesses the complete sample universe, including
  samples without a local candidate, for group-authorized Backfill.
- Persistence filtering does not change classification or product-plus-neutral-loss
  precursor inference. `discovery_physical_facts.json` retains physical records
  for candidates with a measured MS1 peak, including held candidates and their
  merge-member evidence; rows without a peak have no persisted physical facts.
  Retaining a physical answer does not grant NL qualification. Receipts, batch-index
  counts and hashes describe the persisted candidate rows.
- Every Discovery run is bound to a `ProcessingRunSpec`; `run_discovery` and
  `run_discovery_batch` require it and always write `discovery_physical_facts.json`
  beside the candidate and review CSVs. There is no run-spec-free Discovery mode.
- Seed evidence can include MS2 neutral loss, precursor inference, MS1 trace
  support, and future evidence providers.

### Physical qualification

- Discovery uses the shared physical point qualification for every supported
  Gaussian window (default 9). An ordinary candidate needs two adjacent acquired
  observations supporting its half-prominence top and canonical relief above
  the nearest valleys of at least four times the local raw noise MAD. Clear
  valleys at or below half the smaller apex can separate peaks; shallow
  shoulders remain one envelope. Boundaries use a 10% apex-residual return.
  Qualification consumes the prepared arrays without another baseline, area
  calculation, or caller-specific judge. It can reject short or rippled real
  signals; it is not chemical identity or a replacement for cross-sample support
  and Backfill eligibility. Unsupported saved profiles are rejected rather than
  converted or replayed through a second algorithm; see
  [the method owner](signal-processing-rationale.md).
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
  enabled/disabled value and bytes; changing the default does not
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
  with a `scan_precursor`, `scan_monoisotopic`, or `mixed` basis. Qualification classifies downstream
  eligibility; it does not undo the physical trace-shape result or change the
  selected peak's boundaries, apex, or integrated area.

Alignment checks the persisted physical evaluation as well as the candidate
state, so an older CSV's supported label cannot admit an unresolved envelope.
Review-only observations remain available to diagnostic readers, not local
scan-support or runtime candidate loaders. A legitimate group may recover a
new MS1-only observation in another sample through the existing Backfill owner;
that observation cannot resurrect a rejected candidate or create a new root.

### Precursor inference

- CID-NL Discovery acquires MS2 search seeds and retains the existing groups'
  XIC centers and ppm. For each identical mass query in one RAW, overlapping
  local RT windows form one shared context. Signal preparation and the physical
  peak population are reused by all seed questions; the signal is not cropped
  back to each seed's original window. Nearby but different XIC centers remain
  separate measurement channels, not assumed to be the same ion.
  Seeds are attached to owner-selected peaks, then Discovery verifies each
  candidate's independent precursor/NL relation and isotope hold before merging.
  Only independently
  admitted candidates may merge; the resulting representative is rechecked
  against the retained member evidence. A feature with a compatible independent
  precursor and product inside its resolved peak is `ms1_feature_nl_supported`.
  A feature lacking that relation remains `ms1_feature_nl_rescued` for review,
  not downstream assessment. Admission itself grants neither detection nor
  Matrix publication.
- CID-NL mass references use either the selected scan precursor (`scan_precursor`)
  or a distinguishable vendor-recorded monoisotopic precursor
  (`scan_monoisotopic`). The latter is recorded evidence independent of the
  product-plus-loss calculation. Selected m/z remains unchanged; charge, master
  MS1 scan number, isolation width and offset remain attached to the evidence.
  A missing/zero vendor value is unavailable, not a measured zero. This method
  assumes singly charged ions and rejects known incompatible charge.
  Source and support are separate: `precursor_evidence` assesses nominal
  isolation membership. A monoisotopic reference needs known in-window geometry
  before it can support strict NL; outside/unknown references remain recorded
  mass-compatible candidates. The selected precursor retains the existing method
  when geometry is unavailable. Neither case proves unique chemical parentage.
- Product-plus-loss inference stays inside the recorded isolation window:
  center = selected m/z + isolation offset, radius = the smaller of half the
  recorded width and `DiscoverySettings.ms2_precursor_tol_da`. When width is
  unavailable, the configured radius supplies the bound. The installed CLI
  exposes the same setting as `--ms2-precursor-tol-da`; the default is 0.8 Da,
  the nominal half-width of the 1.6 Da method. Saved explicit settings remain
  effective as the configured cap. Inference does not establish precursor
  identity or replace the direct NL mass tolerance.
- MS2 cache and canonical CID-NL evidence retain the typed precursor metadata.
  Old MS2 cache payloads lacking this contract are reacquired from RAW. Replay
  retains recorded references and applies the same isolation assessment for
  strict support and Backfill; isotope-related ions remain distinct candidates.
- Physical trace context and precursor-identity confidence are separate. A
  `product_mz + neutral_loss` group starts with the bounded configured RT
  context; when that trace is too short for the active signal-processing
  profile, Discovery may reacquire enough context only when at least two MS2
  seeds agree and their maximum product intensity reaches the profile's existing
  high-intensity threshold. Reacquisition keeps the original MS2 query RT and
  m/z tolerance. It supplies enough points to answer the physical peak question;
  it does not upgrade inferred precursor identity or grant Matrix authority.
  An incomplete edge exposed by any seed extends the whole shared context in
  one bounded reacquisition round. Contexts connected by that extension are
  reunited before the final peak population is built. Windows are the union of
  local requests, not a fixed full-run extraction; the physical owner's shape,
  boundary, qualification and overlap rules are unchanged.
- When the final physical evaluation of a pure `product_mz + neutral_loss`
  group resolves a peak, including after point reacquisition, Discovery reads
  the ion at the apex scan and one MS1 scan on each side, inside the final
  request, and reports the intensity-weighted reading as `ms1_apex_mz`. Groups
  sharing the same retained physical peak question reuse its final ion/isotope
  reading. MS1 is the method's accurate-mass scan, while ddMS2 runs at a
  quarter of its resolution, so product + loss only points at the ion. The
  reading supplies Alignment's physical XIC and grouping mass. The candidate's
  original precursor and product remain the keys for exact MS2 scan replay;
  retained Discovery physical facts keep their original acquisition query.
  Capture authorizes the current physical query separately from the mass used
  by its retained evaluation. Alignment evaluates a changed mass afresh even
  when the two XICs contain identical signal arrays. Readings spread wider than 5 ppm
  come from two ions and report none. A selected (`scan_precursor`, `scan_monoisotopic`, or `mixed`)
  precursor reports none: in full85, about half of the selected precursors a
  reading would have moved landed on a neighbouring ion present at the selected
  m/z when the scan was triggered.
- Product + loss assumes a singly charged precursor. A pure inferred precursor
  whose MS1 ion is read with a centroid one doubly charged 13C spacing
  (0.50168 m/z, within the 5 ppm reading spread) above it, in most of the
  scans where the ion is read, becomes `review_only_ambiguous_coisolation` with
  a reason naming the doubly charged isotope. A separate singly charged seed
  keeps its own admission; it cannot carry the held candidate through merging.
  The held peak, area and reason remain in the
  candidate/review outputs and physical sidecar without authorizing Alignment
  search or a Matrix value. Selected precursors are not tested.
- Direct and inferred rows from one MS2 scan may coexist. `candidate_id` must
  retain the sample/scan anchor plus precursor/product row identity so those
  rows cannot collide, and `tag_evidence_json` must retain precursor-basis,
  measured scan-precursor, and delta provenance. This is a public identity and
  replay contract even when the CSV column set is unchanged.
  `tag_evidence_json.scan_receipts` retains each scan's precursor/product, source,
  selected m/z, RT, independent loss error, and isolation/support assessment.
  A strongest representative or `mixed` summary does not replace these receipts.
  Product-plus-loss errors are null in JSON and blank in CSV; derived equality
  supplies no independent error measurement and gets no zero-error ranking bonus.
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
  One RT seed group can contain multiple physical peaks. Its seeds are
  partitioned by the physical owner's point-selection result, retaining a
  candidate for each seeded peak while sharing the acquired MS1 trace.
  Outside or ambiguous scans retain separate diagnostic lineage rather than
  joining the original representative's peak. They do not supply support to
  another peak. Each retained candidate keeps its own
  physical question and persisted answer binding.
- Untargeted CID-NL carries eligible inferred evidence into the shared method
  assessment alongside direct evidence. Canonical evidence retains the precursor
  basis, actual scan precursor, and configured inference bound; an inferred
  precursor and its derived loss are not reported as directly measured facts.
  An inferred precursor alone cannot grant a strict NL relation or detection
  count. After physical qualification, Discovery reads the exact recorded parent
  MS1 scan in the existing precursor ppm window. One positive centroid supplies
  an independent reference; missing scans, missing ions, or multiple centroids
  leave the inference unresolved. The measured mass must satisfy the nominal
  isolation window and the existing per-ion NL tolerance, and both parent MS1
  and MS2 times must lie inside the physical peak. This is method support, not
  proof of a unique chemical precursor–product pairing.
  `tag_evidence_json.scan_receipts` adds the parent observation and a separate
  `precursor_verification` result; original inferred m/z, source and null loss
  error stay unchanged. Alignment reacquires these facts from RAW before minting
  canonical evidence and relations; a saved CSV verification result supplies no
  authority. A merged peak keeps its representative when that complete physical
  answer retains qualified member evidence. Otherwise Discovery tries original
  same-apex members in the existing representative order and rechecks support
  on the rebuilt merged candidate. Candidate identity, physical work binding,
  boundaries and integral move together; interval overlap or a measured-ion match
  at a different apex does not authorize this fallback. The formal method
  assessment still owns unique support and eligibility. Parent scans are read
  once per acquisition batch, with all requested
  mass windows evaluated against the same scan. MS1 quantitation remains subject to the existing method
  assessment and cell-decision policy. CrossSample and cell-decision owners decide
  identity, Backfill, and Matrix publication. Candidate eligibility alone
  grants none of these authorities.
### Outputs, peak anchors, and batch handoff

- Standard per-sample outputs: `discovery_candidates.csv` (archival/alignment
  detail) and `discovery_review.csv` (compact human review). Both write
  floats at full precision (the shortest text that reads back to the same
  value), because Alignment reads the candidate m/z and RT back; display
  rounding belongs to the final Matrix.
- Discovery `feature_id` is a per-sample peak anchor label for candidates
  sharing the same discovered MS1 peak. Members must share the
  sample, RAW source and tag, have matching apexes and overlapping peak bounds,
  and stay within the configured precursor m/z tolerance of every other member.
  Intermediate masses cannot bridge separate peaks through chained matches;
  multiple hypotheses for the same peak remain allowed. The label must not be
  treated as cross-sample identity, selected-peak truth, or Backfill promotion
  authority by itself. A review-only row may retain the same physical feature
  row ID as an admitted row; only admitted rows require uniqueness in that
  sample/tag scope. Candidate IDs still identify separate seed hypotheses.
- Independently admitted candidates of one sample whose MS1 traces share the
  apex scan are one Discovery peak when their precursor m/z agree within
  `precursor_mz_tolerance_ppm`. A precursor inferred as product + loss carries
  the MS2 product error, so two candidates of one ion can differ by more. A
  window that clips the ion shows only part of its peak, such as the tail, and
  reports that part with its own apex. When the two differ by at most twice the
  tolerance, they are one Discovery peak if the other apex lies inside the
  higher candidate's peak bounds (one peak on that trace) and the most intense
  MS1 centroids read at the two apexes, each within its own window, agree within
  the tolerance (one ion). The merged candidate keeps the higher candidate's
  m/z, peak and area.
- Batch discovery handoff uses `discovery_batch_index`. For every sample,
  the Discovery owner binds the exact candidate CSV SHA-256/byte size, complete
  path-independent candidate payload/count, the physical-facts file
  SHA-256/byte size computed by its writer, and content-bound acquisition
  source into one `DiscoveryCandidateUniverseReceipt`. Alignment checks the
  physical-facts fingerprint once before reading that file. A downstream real-data replay
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

## Delta Mass Discovery

Delta Mass finds sample-local MS1 peak combinations at preset-defined mass
separations. A candidate must satisfy mass differences and co-elution;
designated discriminating positions additionally satisfy apex-region shape
correlation. Presets can additionally require a designated lowest position with
clear local-background support. Height ratios are additional evidence, never
admission gates. The method supports two or more positions and has no built-in
chemical pattern.

### One-pass method

1. Only detector-qualified features can anchor a hypothesis, in the roles named
   by the preset. The matcher trusts detector qualification; it does not repeat
   physical peak evaluation.
2. Search the same feature table in both mass directions, using the lowest-mass
   position as base. Partners must be within the ppm window and scan-aware apex
   tolerance: `max(apex_floor_sec, scan_interval_multiplier × MS1 interval)`.
   Equivalent position assignments are emitted once.
3. Require a compatible table partner and record measured height ratios against
   the preset's reference ranges. Out-of-range ratios do not reject candidates
   or suppress RAW acquisition. A missing base has unavailable ratios until its
   height is measured; expected abundance is not used to predict missing signal.
4. Acquire one XIC batch per RAW for missing positions and shape-reference traces.
   Confirm missing positions by apex alignment and apex-region Pearson correlation,
   without the shared physical evaluator. A two-position method requires its
   partner in the table; RAW never fills a solitary seed.
5. Evaluate shape for the preset's discriminating positions, in addition to
   missing-position confirmation. An empty discriminating list is supported.
   If `lowest_position` is configured, also require that position to have the
   lowest locally background-subtracted RAW peak height and clear background
   support. These checks share the same final confirmation stage, not a new
   chemical-identity claim.

The optional lowest-position and base-support checks acquire all positions in the same RAW batch
with wider background context. Separate short requests in that same batch
preserve the vendor scan-rounded `shape_window_sec` apex/shape windows; wider
traces are not recropped to silently change existing shape decisions. Every position's comparison height is its raw maximum
in that central window minus its local background; no table/smoothed height is
mixed into this ordering decision. Background is the median of the lower-median
flank, using the two configured `background_inner_sec`–`background_outer_sec`
intervals around the base apex. A usable flank needs at least three samples;
without a usable flank the hypothesis cannot pass. Selecting the lower flank
reduces contamination from a neighboring peak or tail on one side.

Background variation is `1.4826 × MAD` on that same flank. The designated lowest
position must have net height at least
`max(min_height_above_background, min_background_sigma × background variation)`.
The absolute floor remains necessary when sparse RAW flanks have zero MAD;
zero variation is not reported as infinite signal-to-noise. Net heights,
background, variation, required height, and independent signal/lowest statuses
are published in position evidence. This local screening statistic is not a
calibrated detection limit.

An optional `base_support_positions` list enables a one-sided base-strength
check on these same RAW net heights. For the named non-base positions, sum
their nonnegative net peak heights and divide by the sum of their reference
ratios (the geometric centers of `ratio_min` and `ratio_max`). This gives
`base_expected_net_height`. The nonnegative base net height divided by that
expectation is `base_support_score`; it must be at least `min_base_support`.
The candidate CSV records both values and `base_support_status`; insufficient
support rejects with `base_support_below_threshold`. Missing background or a
nonpositive reference sum cannot supply support. An empty reference-position
list disables this check independently of `lowest_position`.

This is one pooled lower-bound check, not separate ratio gates or an upper
bound on base. Each height is its own central-window maximum, not a
simultaneous spectrum or integrated area. Reference agreement does not establish
chemical identity, and molecular isotope overlap or interference can affect
the observed distribution.

The pure method lives in `delta_mass_patterns`; the pipeline owns RAW access,
local feature detection, and serialization. Table heights are detector-produced
smoothed screening heights; missing-position heights are measured from the
acquired XIC. These are diagnostic pattern evidence, not quantitative areas.
Each position reports `ratio_status` (`within_range`, `below_range`,
`above_range`, or `unavailable`) separately from `position_status`. The latter
records required matching/confirmation checks, not abundance agreement.
Ratio specificity is not validated; agreement is not a calibrated Pt probability.
The RAW adapter supplies vendor-centroided MS1 ions to the local detector:
profile samples on the sides of a mass peak are not separate features. The
sidecar records this spectrum mode. Other scan-index consumers retain their
existing stored-sample behavior.

### Presets and detector settings

The preset owns ordered positions, anchor/discriminating roles, height-ratio
ranges, ppm tolerance, apex floor, scan-interval multiplier, shape window, and
minimum correlation. Local MS1 smoothing is configured in seconds and converted
per RAW to the nearest positive odd number of scan points. A 6.6 s setting with
1.31 s scans gives five points. This screening setting is separate from the
shared canonical Gaussian profile's 9-point default.

`pt_pattern` and `pt_pattern_cid_nl_dr` are **provisional** applications: positions
0/+1.002111/+2.002271/+4.005213, base/+1 anchors, and +4 discrimination. Their
TOMLs own all numerical choices, including natural-abundance ratio bounds with
factor-two tolerance. The abundance reference is
[NIST platinum isotope composition](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Pt).
The Pt presets require +4 to be the lowest net peak and to exceed the larger
of 25,000 intensity units or five robust background deviations, using 20–30 s
flanks. The absolute floor matches the detector noise setting; it does not
establish instrument-wide sensitivity. +1 being highest is not required.
Both Pt presets use +1 and +2 as the base-support reference, with
`min_base_support = 0.25`: base must reach a quarter of the expected height
`(H_+1 + H_+2) / 1.7951917224589167`. The tolerance is provisional, not a bound
implied by natural abundance. +4 is excluded from this pooled test, so a weak
+4 does not fail it; the existing +4 presence, shape, background, and
lowest-position requirements still apply. Per-position ratio ranges remain
evidence only. There is no all-position relative-height floor.
All these choices are configurable; an empty `lowest_position` disables the
optional signal/order gate. Changing these provisional parameters does not
change the generic method.

### Inputs and results

Use `xic-delta-mass-discovery-cli` (or `python -m scripts.run_delta_mass_discovery`)
with `--raw-dir`, `--dll-dir`, `--preset`, and `--output-dir`. Each RAW is detected
and matched in the same run. The composite preset also requires
`--cid-nl-candidates-csv`. Its base join retains sample-local CID-NL(dR) eligibility
and the bounded m/z/RT join; detector-local IDs are not CID-NL identities.

- Each sample directory contains the detector's `detected_features.csv`.
- `delta_mass_candidates.csv` contains every hypothesis reaching layer 3, with
  candidate/rejected status, first rejecting layer, and one reason code.
- `delta_mass_positions.csv` contains one row per hypothesis and position, with
  source, mass/apex/height measurements, ratio evidence and reference bounds,
  and applicable shape evidence.
- `delta_mass_sidecar.json` records effective method settings, inputs, per-sample
  layer counts, scan interval, XIC cost, `height_ratio_policy: evidence_only`,
  and `status: diagnostic_only`. Layer 3 records evidence and cannot reject.

Layer 1–2 failures are counted rather than emitted as candidate rows. The
composite join retains all rows and records `matched` or `unmatched` independently
of method status. It does not change CID-NL's own candidate decisions.

There is no alignment handoff or Matrix, Backfill, counted-detection, or export
activation authority. Alignment integration requires a separate stable physical
base-record contract. The base remains the hypothesis identity; a future Pt
quantitative channel would use +1 rather than summing the pattern. That direction
does not grant selected-area authority. Shape similarity never establishes
assignment membership by itself. Real-sample counts are **方法開發** evidence, not a claimed
accuracy or production-readiness threshold.

## Method-specific validation

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

## Surfaces

| Surface | Role |
| --- | --- |
| `xic_extractor/discovery/` | Discovery domain and orchestration owners |
| `xic_extractor/discovery/detected_features.py` | Sample-local detected-feature model and CSV |
| `xic_extractor/discovery/local_ms1_features.py` | Seeded local MS1 detector; screening evidence |
| `xic_extractor/discovery/delta_mass_patterns.py` | Pure delta-mass preparation and trace completion |
| `xic_extractor/discovery/delta_mass_strategy.py` | Generic method settings and strategy |
| `xic_extractor/discovery/delta_mass_pipeline.py` | RAW-to-diagnostic delta-mass workflow |
| `scripts/run_delta_mass_discovery.py` | Delta Mass CLI |
| `scripts/run_discovery.py` | CID-NL Discovery CLI |
| `discovery_candidates.csv` | CID-NL candidate handoff |
| `discovery_review.csv` | CID-NL review table |
| `discovery_batch_index.csv` | CID-NL downstream alignment index |

## Boundaries

- **Owns**: candidate generation, per-sample review surfaces, seed evidence
  handoff, batch index for downstream alignment.
- **Does not own**: Backfill promotion (see
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
- Treating CID-NL 8RAW/85RAW fixtures as required gates for Pt pattern or other
  Discovery methods whose evidence and sample design are not comparable.
- Depending on old worktree output directories instead of explicit batch index.
- Hiding discovery output-contract changes in dated implementation notes.
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
