# Discovery

Discovery answers which sample-local signals warrant further evaluation. It
turns method-specific seed evidence and MS1 traces into reviewable candidates;
a candidate does not establish cross-sample correspondence or a final value.
The [processing story](../user/processing-guide.html#discovery) explains that
distinction with an example. This page owns candidate generation and the method
evidence behind it. The [Untargeted method](untargeted-method.md) page owns the
literature basis and the Untargeted scope; the
[Untargeted guide](../user/untargeted-discovery.md) owns commands and outputs.

Discovery is provider-specific at its input. CID-NL, HCD-PI, Delta Mass and
future providers consume different evidence and generate candidates
differently. Each publishes observations and hypotheses through one shared
evidence contract, so Alignment, Backfill and the cell decision do not fork per
provider. Untargeted retains every candidate qualifying under its configured
evidence space; Targeted retains only configured targets. Discovery does not
publish product values; see [Writer Authority](decision-policy.md#writer-authority).

## Evidence providers and composites

Discovery strategy families name reusable evidence-provider classes:
`neutral_loss` for CID-NL methods and `delta_mass` for MS1 mass-difference
patterns. Concrete patterns live under a family: `DNA_dR` is a neutral-loss
tag, and `pt` is a delta-mass pattern. `pt_pattern` is the method label for
`delta_mass(pattern_id=pt)`, not a separate primitive strategy.

Composite methods combine providers by explicit set operations on the same base
peak. `pt_pattern + cid_nl(dR)` intersects Delta Mass hypotheses with existing
`neutral_loss(tag=DNA_dR)` evidence. Delta Mass evaluates its own hypotheses
first, then annotates whether the base matches CID-NL(dR). That annotation uses
the existing eligible CID-NL states and the sample-local m/z/RT predicate; it
does not alter CID-NL decisions.

## CID-NL candidates

### Neutral-loss tolerance

`nl_tolerance_ppm` (default 20) is a per-ion m/z tolerance. A product matches
the configured loss when its absolute loss error is at most
`sqrt((precursor_mz × tol)^2 + (observed_product_mz × tol)^2)` Da, where
`tol = nl_tolerance_ppm × 1e-6`. Both ions carry independent mass error, so the
loss tolerance combines them. The reported loss-error ppm is relative to the
configured loss mass; it is not the acceptance gate.

### Candidate states

Candidate state decides what may enter Alignment, not only how a row is
labelled.

| State | Meaning | Enters Alignment |
| --- | --- | --- |
| `ms1_feature_nl_supported` | A resolved MS1 peak with an independent precursor and product inside it | Yes |
| `ms1_feature_nl_rescued` | A measurable MS1 peak without independent same-peak NL support | No; review only |
| `review_only_unresolved_peak_attribution` | A shoulder or overlapping envelope whose MS2 cannot be attributed to one peak | No |
| `review_only_ambiguous_coisolation` | An inferred precursor whose ion is a doubly charged isotope (below) | No |
| `review_only_orphan_nl` | NL evidence without an accepted MS1 peak, or demoted by blank qualification | No |

Rejected and review-only rows cannot choose query centers, merge into admitted
candidates, mint method subjects, supply support scans or authorize Backfill.
A legitimate group can still find a new MS1-only observation in another sample
through [Backfill](backfill.md); that observation cannot resurrect a rejected
candidate or create a new group. An all-rejected batch completes with no
subjects and an empty Matrix.

### Physical qualification

Discovery uses the shared physical peak owner. An ordinary candidate needs two
adjacent acquired points supporting its half-prominence top, and relief above
the nearest valleys of at least four times the local raw-noise MAD. A valley at
or below half the smaller apex separates peaks; a shallow shoulder stays one
envelope. This gate can reject short or rippled real signals. It is not
chemical identity and does not replace cross-sample support. The
[signal processing rationale](signal-processing.md) owns the
method and its evidence.

### Precursor basis

A CID-NL mass reference has one of three bases:

- `scan_precursor`: the selected isolation m/z of the MS2 scan;
- `scan_monoisotopic`: a distinguishable vendor-recorded monoisotopic m/z,
  which is recorded evidence independent of product-plus-loss arithmetic;
- `product_plus_neutral_loss`: an inferred precursor, product m/z plus the
  configured loss.

`mixed` summarizes candidates that merge rows of different bases. The method
assumes singly charged ions and rejects known incompatible charge. A missing or
zero vendor value is unavailable, not a measured zero.

Source and support are separate questions. A monoisotopic reference supports a
strict NL relation only when it lies inside the recorded nominal isolation
window. An outside or unknown reference stays a recorded mass-compatible
candidate. Neither case proves unique chemical parentage.

Inference stays inside the recorded isolation window: center = selected m/z +
isolation offset, radius = the smaller of half the recorded width and
`ms2_precursor_tol_da` (default 0.8 Da, the half-width of the 1.6 Da method).
Inference does not establish precursor identity or replace the NL tolerance.
Its loss error is null, not zero: derived equality is not an independent
measurement and earns no ranking advantage.

### Inferred-precursor verification

An inferred precursor alone cannot grant a strict NL relation or a detection
count. After physical qualification, Discovery reads the recorded parent MS1
scan within the precursor ppm window:

- exactly one positive centroid supplies an independent precursor reference;
- a missing scan, a missing ion or several centroids leave the inference
  unresolved;
- the measured mass must lie inside the nominal isolation window and satisfy
  the per-ion NL tolerance;
- both the parent MS1 scan and the MS2 scan must lie inside the physical peak.

This is method support, not proof of a unique precursor–product pairing. The
original inferred m/z and null loss error stay unchanged. Alignment reacquires
these facts from RAW; a saved CSV verification grants no authority.

### Accurate-mass reading

For a pure `product_plus_neutral_loss` group with a resolved peak, Discovery
reads the ion at the apex scan and one MS1 scan on each side, and reports the
intensity-weighted reading as `ms1_apex_mz`. MS1 is the method's accurate-mass
scan, while ddMS2 runs at a quarter of its resolution, so product + loss only
points at the ion. The reading supplies Alignment's XIC and grouping mass. The
original precursor and product remain the keys for MS2 scan replay.

- A reading spread wider than 5 ppm comes from two ions and reports none.
- Selected precursors (`scan_precursor`, `scan_monoisotopic`, `mixed`) report
  none. In the 85-sample tissue cohort, about half of the selected precursors a
  reading would have moved landed on a neighbouring ion present at the selected
  m/z when the scan was triggered.

Product + loss assumes a singly charged precursor. When the MS1 ion is read
with a centroid one doubly charged ¹³C spacing (0.50168 m/z) above it in most
scans, the candidate becomes `review_only_ambiguous_coisolation`. Its peak,
area and reason remain visible; it cannot reach Alignment or a Matrix value. A
separate singly charged seed keeps its own admission.

### Trace context

Physical trace context and precursor-identity confidence are separate. A
`product_mz + neutral_loss` group starts with the configured RT context. When
that trace is too short for the signal profile, Discovery reacquires context
only when at least two MS2 seeds agree and their maximum product intensity
reaches the profile's high-intensity threshold. Reacquisition keeps the
original query m/z and tolerance. It answers the physical peak question; it
does not upgrade precursor identity or grant Matrix authority.

### Same-peak candidates

- Distinct MS1 features can share one MS2 scan. Sharing a scan does not make
  them duplicates; evidence scoring ranks them without deleting either.
- One RT seed group can contain several physical peaks. Seeds are partitioned
  by the physical owner's peak selection. Scans outside or between peaks keep
  separate diagnostic lineage and do not support another peak.
- Independently admitted candidates of one sample are one Discovery peak when
  their MS1 traces share the apex scan and their precursor m/z agree within
  `precursor_mz_tolerance_ppm`. An inferred precursor carries the MS2 product
  error, and a window that clips an ion reports only part of its peak. For
  candidates up to twice the tolerance apart, they are one peak when the other
  apex lies inside the higher candidate's bounds and the most intense MS1
  centroids at the two apexes agree within the tolerance. The merged candidate
  keeps the higher candidate's m/z, peak and area.

Discovery `feature_id` labels candidates sharing one MS1 peak within one sample,
RAW source and tag. Members have matching apexes, overlapping bounds and
precursor m/z within tolerance of every other member, so intermediate masses
cannot chain separate peaks. The label is not cross-sample identity, selected-
peak truth or Backfill authority. Candidate IDs still identify separate seed
hypotheses.

## CID-NL support relations

The CID-NL method assessment binds MS2 evidence to physical MS1 peaks. The
current lab acquisition is DDA/ddMS2; DIA is outside this contract because the
lab has no mature DIA method.

- **Strict support.** A scan strictly inside exactly one candidate peak, with no
  positive-width overlap with another m/z-compatible peak, supports that peak
  individually. Adjacent peaks can share a valley endpoint without blocking it.
- **Set relation.** A scan on a peak boundary, or a peak with positive-width
  overlap, relates to the connected candidate set. A set relation grants no
  individual vote and no hypothesis.
- **One event per peak.** Repeated strict scans inside one MS1 peak are one
  chromatographic support event with several scans, not independent votes. The
  exact per-scan evidence stays upstream. For a single-loss method, the event
  reports the mean measured product m/z and observed loss.
- **Alternatives.** When several product centroids could explain one loss, the
  event prefers independently compatible precursor/loss evidence, then the
  smaller independent loss error, then product intensity. An inferred equality
  never supplies a zero-error advantage.
- **Assignment.** Product ions, neutral losses, adducts and in-source fragments
  are evidence only when co-eluting, boundary-aligned and assigned to the same
  precursor or candidate. Shared class fragments or common neutral losses
  support class confidence, not analyte-specific proof.
- **Composite methods.** OR requires any configured loss; AND requires every
  configured loss on the same physical peak in the same sample. Different peaks
  or samples cannot pool evidence into AND support. One scan can support two
  different required losses without counting as two scans.

| Result | Meaning |
| --- | --- |
| `SUPPORT` | At least one strict relation |
| `HOLD` | Set-only evidence, or a completed Backfill search without qualifying support |
| `REJECT` | Qualifying CID-NL evidence with no usable physical MS1 peak |

Non-qualifying scans cannot manufacture `REJECT`. The technical state
(`evaluated`, `unsupported`, `failed`, `not_evaluated`) is a separate axis:
technical failure never becomes `REJECT`, and the absence of positive evidence
is not proof that a search ran. A sample with no eligible Discovery input is
`not_evaluated`. If a group-guided Backfill later searches it and finds nothing
qualifying, the completed result is `HOLD`, distinct from the unsearched slot
and not proof of rejection.

A subject with at least one evaluated result, every evaluated result `HOLD` or
`REJECT`, and every other slot unsearched is excluded under `cid_nl_requirement_not_supported` (or
`composite_requirement_not_supported`). Each feature-backed candidate needs its
own evaluated `HOLD` or `REJECT`; an unsearched slot cannot supply negative
evidence.

## RAW evidence is a query

A RAW-backed claim answers one reproducible query, so it keeps its scan filter,
trace type, mass window, RT window, extraction backend and processing basis.

- The scan filter selects which scans contribute; it is not display metadata.
- Mass-range XIC, TIC, base-peak, neutral-loss and product-ion views are
  different evidence; compare traces only when both queries are known.
- Name which view drove a decision: raw, smoothed, detection, boundary or
  quantitation basis.
- Peak detection is a strategy over the query result. Vendor integration (ICIS,
  Avalon, Genesis) parity is not claimed without deliberately mapped settings.
- Manual review and overrides are review-layer facts; they never rewrite
  automatic rules.
- Compare acquisition methods by their role: a high-resolution full-scan MS1 run
  can be Discovery evidence while a lower-resolution DDA run supports found
  bases. Absent DDA MS2 is not negative evidence unless the inclusion list shows
  the candidate should have been queried.

A zero in an extracted trace does not prove ion absence. Inspect the acquired
centroid stream, adjacent scans and scan mode before changing the mass window or
peak algorithm. Sources:
[Xcalibur 4.6 Qual Browser User Guide](https://docs.thermofisher.com/v/u/Xcalibur-4.6-Qual-Browser-User-Guide)
and [Thermo RawFileReader](https://github.com/thermofisherlsms/RawFileReader).

## Blank qualification

Blank qualification is disabled by default. Producing a blank library alone
does not enable it; an explicit opt-in requires method-appropriate validation.

- The library aggregates centroid m/z and maximum intensity. It does not
  preserve RT or qualify peak shape.
- A short solvent blank with a different LC gradient is diagnostic background,
  not a validated hard-exclusion reference for a longer method. RT must not be
  scaled linearly between different 15-minute and 60-minute gradients.
- Background references distinguish clean pre-sample solvent injections from
  post-standard or post-sample carryover monitors and waste-directed runs. A
  method-matched blank is a prerequisite for evaluating removal, not proof that
  an m/z-only intensity rule is valid.
- Instrument acquisition exclusion lists and software blank qualification are
  separate. An acquisition exclusion can prevent an MS/MS trigger; disabling
  software filtering does not recreate an unacquired spectrum.
- When enabled, a candidate matched within 10 ppm stays feature-backed only when
  its MS1 height is at least 3× the blank's maximum intensity. Below that it
  becomes `review_only_orphan_nl`. Qualification changes eligibility only; it
  does not change the peak's boundaries, apex or area.

## Delta Mass Discovery

Delta Mass finds sample-local MS1 peak combinations at preset-defined mass
separations. A candidate must satisfy the mass differences and co-elution;
designated discriminating positions also need local peak-shape correlation.
The method supports two or more positions and has no built-in chemistry.

1. Only detector-qualified features anchor a hypothesis, in the roles the
   preset names. The matcher trusts that qualification.
2. Partners are searched in both mass directions from the lowest-mass position,
   within the ppm window and the scan-aware apex tolerance
   `max(apex_floor_sec, scan_interval_multiplier × MS1 interval)`.
3. Measured height ratios are compared with the preset's reference ranges and
   recorded as `ratio_status`. Ratios are evidence only: out-of-range ratios do
   not reject candidates, and expected abundance never predicts missing signal.
4. Missing positions are confirmed in RAW by positive measured height and
   Pearson correlation at or above `min_shape_r`. Their measured apex offsets
   remain evidence, not a second admission gate. Filling a missing base does
   not move the hypothesis RT or recheck table partners against its RAW apex.
   A two-position method needs its partner in the feature table; RAW never
   completes a solitary seed. The table-partner apex gate in step 2 remains.
5. Missing and discriminating positions use `correlation_window_sec` (default
   ±20 s) around the original hypothesis RT. When background traces are already
   acquired, correlation uses that wider evidence without additional RAW
   requests. Otherwise the existing confirmation requests cover the correlation
   window. An optional `lowest_position`
   must have the lowest background-subtracted RAW height and clear background
   support.

`shape_window_sec` retains the original RAW-apex/height and signal peak region
(±10 s in Pt presets); it is independent of `correlation_window_sec`. Background
flanks remain 20–30 s. The signal gate's heights, background, noise and base
support calculations do not widen with the correlation window. The candidate
reference RT stays at its original base/table-anchor location; a RAW-completed
position's measured apex is retained in the positions table.

Background is the median of the lower-median flank of two configured intervals
around the base apex; the lower flank limits contamination from a neighbouring
peak on one side. Background variation is `1.4826 × MAD` on that flank. The
lowest position needs net height of at least the larger of an absolute floor
and a multiple of that variation. The floor remains necessary because sparse
flanks can have zero MAD; zero variation is not infinite signal-to-noise. This
screening statistic is not a calibrated detection limit.

The optional base-support check sums the named non-base positions' net heights
and divides by the sum of their reference ratios (geometric centers of each
range). The base must reach `min_base_support` of that expectation. This is one
pooled lower bound, not separate ratio gates or an upper bound. Each height is
its own central-window maximum, not a simultaneous spectrum or area, and
molecular isotope overlap or interference can affect the distribution.

The RAW adapter supplies vendor-centroided MS1 ions to the local detector, so
profile samples on a mass peak's sides are not separate features. Detector
heights are smoothed screening heights, not quantitative areas.

The Pt presets are provisional applications of this generic method. Their TOML
files own every numeric choice: positions, anchors, ratio bounds, background
floor and base-support tolerance. The abundance reference is
[NIST platinum isotope composition](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Pt).
The base-support tolerance is provisional, not implied by natural abundance.
Ratio agreement is not a calibrated Pt probability.

Delta Mass output is `diagnostic_only`. It has no Alignment handoff and no
Matrix, Backfill, counted-detection or export authority. Shape similarity never
establishes membership by itself. Real-sample counts are method-development
evidence, not an accuracy threshold.

### Read-only candidate review

Delta Mass candidates can be reviewed directly, without an Alignment handoff.
`python -m tools.diagnostics.delta_mass_review --help` describes the bounded
export: select candidate IDs from current Discovery output, choose the RAW/DLL
directories, and write a review JSON. Open that JSON with
`xic-inspector <review.json>` (or `python -m xic_inspector` in a checkout).

The Inspector keeps one candidate's positions together and plots their unsmoothed
RAW XICs on a shared intensity axis. Saved heights, ratios, shape evidence and
statuses remain the Discovery answers; zooming does not recompute them. Ratio
and shape reference positions are retained from the method settings.

The exporter reads the selected RT windows anew, matching RAW names to saved
samples. It does not verify the original analysis bytes or reproduce the original
detector. Without NL assessment, every readable MS2 in those windows remains selectable, even when its
recorded precursor is unrelated to a pattern position. A time marker is temporal
context, not precursor attribution, sugar-loss detection or candidate admission.
Missing XIC/spectrum data and MS2 read errors stay visible. The Inspector reads
only the saved review; it never acquires RAW or changes candidate or Matrix values.

With or without NL assessment, the view uses the same candidate header, XIC,
selected spectrum and details layout. Position tables, display-range controls
and manual MS2 lookup live in details; NL support adds a direct evidence action,
not a separate layout. Read-error summaries remain visible outside details.

The review does not import historical task-specific CSV/NPZ formats, infer charge,
impose a height-ratio gate, match fragments or group candidates across samples.

#### Candidate-level NL intersections

The same review entry accepts `--all-candidates --nl-preset dna_dr` to answer
which saved Delta Mass candidates also have peak-scoped NL evidence, without
manual scan selection. A preset is explicit, never guessed from a sample name.
The JSON retains the evidence; its sibling `.nl.csv` contains one candidate/tag
row with each position's result and supporting scan references. This summary,
not scan browsing, is the entry for cross-method assessment. Inspector also
shows the saved position results in its candidate list and detail table. Clicking
NL support opens the corresponding spectrum, highlights its measured product,
and focuses the XIC on the saved peak with the scan time and bounds marked.
Several support records use previous/next evidence controls; manual MS2 lookup
is collapsed as secondary inspection and never inherits an NL-support label.
Supported candidates appear first. Routine no-match and no-MS2 messages are
omitted from the view rather than repeated or moved into a disclosure; the
candidate and its XIC remain accessible. Actual ambiguity, unresolved evidence
and incomplete reads remain visible. The JSON and CSV keep every position's
answer unchanged.

NL-enabled exports retain only spectra referenced by the completed position
answers (related opportunities and matched evidence), rather than duplicating
unrelated RT-window spectra per candidate. `ms2_scope` distinguishes this from
the unfiltered review. The XIC context, position answers and support references
are unchanged. Inspector renders saved NL values and never re-matches fragments
or recomputes the verdict when zooming.

The adapter reuses Discovery's scan matcher, parent-MS1 acquisition and
precursor-loss assessment, then the shared CID-NL interval-relation owner.
It uses each position's saved detected-feature bounds and the complete nearby
feature population, including unselected competitors. Display padding does not
define a peak and selecting fewer candidates cannot remove overlap ambiguity.
Missing feature bounds remain unavailable; RAW-completed positions without a
saved feature are not assigned a fabricated peak interval.

An intersection means the saved Delta Mass status is `candidate` and **at
least one named position** has NL support for that tag. It does not mean both
positions support NL or that all configured tags are jointly satisfied. Each
position remains separate: `supported`, `ambiguous`, `unresolved`, `no_match`,
`no_ms2`, `no_spectrum`, `incomplete`, or `no_peak_bounds`. `no_match` means the
configured method did not establish support, not chemical absence. Inferred
product-plus-loss evidence cannot confirm itself: it needs the existing
parent-MS1 check. Overlap/boundary evidence stays ambiguous under the shared
relation rule.

These are fresh `diagnostic_only` assessments on saved detector intervals, not
a rerun of formal CID-NL physical qualification. Delta Mass statuses, ratios,
shape evidence, Discovery files and Matrix eligibility are unchanged. A saved
Delta Mass rejection cannot become an intersection merely because NL is seen.

## Method-specific validation

Discovery validation is method-specific. CID-NL 8RAW/85RAW fixtures are not
universal Discovery gates; the 8RAW fixture is a subset of the 85RAW cohort with
the same processing recipe. Pt pattern work uses Pt-method real samples,
trace and co-elution evidence, no-write authority checks and independent human
adjudication. Reviewed Pt labels are method-development evidence, not ground
truth. Vendor RAW-to-mzML conversion is not an implicit product dependency.

## Verification

- `tests/test_discovery_csv.py`, `tests/test_discovery_review_csv.py`,
  `tests/test_discovery_csv_reader.py`, `tests/test_discovery_csv_physical_state_contract.py`

## See Also

- [Alignment](alignment.md) | [Backfill](backfill.md) | [Untargeted method](untargeted-method.md)
- [Presets](presets.md)
