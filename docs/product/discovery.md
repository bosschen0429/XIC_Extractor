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
admitted CID-NL peaks and the sample-local predicate on the peak's
`peak_mz` and apex RT; it does not alter CID-NL decisions.

## CID-NL peaks and events

### Neutral-loss tolerance

`nl_tolerance_ppm` (default 20) is a per-ion m/z tolerance. A product matches
the configured loss when its absolute loss error is at most
`sqrt((precursor_mz × tol)^2 + (observed_product_mz × tol)^2)` Da, where
`tol = nl_tolerance_ppm × 1e-6`. Both ions carry independent mass error, so the
loss tolerance combines them. The reported loss-error ppm is relative to the
configured loss mass; it is not the acceptance gate.

### Ions, peaks and events

CID-NL Discovery publishes two tables per sample. `discovery_peaks.csv` has one
row per physical MS1 peak. `discovery_events.csv` has one row per strict NL
event, keyed by MS2 scan, tag and product; a scan matching two tags is two
events. Each event names the peak it attaches to (`peak_id`) or why it attaches
to none (`unattached_reason`), never both.

- **Ion.** Events are grouped only by their MS1-measured ion: the recorded
  scan precursor, or the single parent-MS1 centroid of an inferred precursor.
  Product + loss is the fallback only when the parent scan reads no single ion.
  A group opens at the lowest unassigned ion and takes every ion within the
  precursor ppm tolerance of that anchor, so no ion bridges two groups. Tag,
  product and RT never split an ion. Its XIC center, `xic_mz`, is the
  representative event's MS1 reading before acquisition: an MS1-measured ion
  first, then the configured tag order, then product intensity.
- **Peak.** Each raw valley-separated peak on an ion's XIC is one row and one
  measurement. `peak_id` is `{sample}@mz{xic_mz}_rt{raw apex RT}`. Adjacent
  XICs overlap, so one ion can be acquired twice: measurements whose
  XIC masses lie within the precursor tolerance of the lowest one and that share
  its raw apex scan are one peak. The kept measurement locates the most events,
  then has the larger area, then the lower mass; the grouping is anchored and
  does not chain.
- **Valley.** An event on a valley shared by two peaks belongs to the earlier
  peak.
- **Evidence.** `event_scan_ids`, tag evidence, the event RT range and
  `ms2_product_max_intensity` cover every event located on the peak, attached or
  not, because Alignment re-reads and re-judges them. `attached_event_count` and
  `status` count attached events only. The `best_event_*` columns are the
  representative event's values, not the peak's mass.
- **Mass.** `peak_mz` is the peak's ion mass: the [accurate-mass
  reading](#accurate-mass-reading) for an inferred precursor, else `xic_mz`.
  Discovery writes it once; Alignment and Delta Mass read it.

| `unattached_reason` | Meaning |
| --- | --- |
| a precursor status (`parent_ms1_ion_missing`, `neutral_loss_mismatch`, …) | The precursor does not verify the loss (see [precursor basis](#precursor-basis)) |
| `mass_outside_peak` | The verified precursor lies outside the peak's precursor ppm window |
| `rt_outside_peak` | The MS2 scan, or the parent MS1 scan of an inferred precursor, lies outside the peak bounds ± 0.1 min |
| `no_peak_at_rt`, or a physical evaluation reason | The ion's XIC has no measurable physical peak at the event RT |

### Peak status

`status` decides what may enter Alignment. When several apply, the first row of
this table wins.

| `status` | Meaning | Enters Alignment |
| --- | --- | --- |
| `blank` | Demoted by [blank qualification](#blank-qualification) | No |
| `doubly_charged` | The MS1 ion reads as a doubly charged isotope ([accurate-mass reading](#accurate-mass-reading)) | No |
| `overlap` | A shoulder or overlapping envelope whose MS2 cannot be attributed to one peak | No |
| `no_nl_event` | A measurable MS1 peak without an [attached](#cid-nl-support-relations), precursor-verified NL event | No; review only |
| `admitted` | At least one attached event and none of the above | Yes |

Peaks that are not `admitted` cannot
choose query centers, merge into admitted peaks, mint method subjects, supply
support scans or authorize Backfill. A legitimate group can still find a new
MS1-only observation in another sample through [Backfill](backfill.md); that
observation cannot resurrect a rejected peak or create a new group. A batch with
no admitted peak completes with no subjects and an empty Matrix.

### Physical qualification

Within a sample, overlapping or adjacent RT requests share their RT planning.
Every distinct precursor center and ppm window is actually acquired; mass
compatibility alone does not make extraction windows interchangeable. Compatible
mass queries may share preparation only when their signals are exactly equal
over the final complete acquired range. Different signals, ppm windows and
isotope-separated channels remain separate. A bounded raw-only read probe can
extend the shared context before its single canonical preparation and segment
measurement; it does not quantify each NL request independently.

Each raw valley-separated peak owns one measurement. NL events only locate
that owner and contribute their independent method evidence. Multiple events
on that peak produce one retained physical record and peak row with all scan
receipts, not competing areas. Completed readers decode the stored result
without preparing or evaluating the signal again. The canonical measurement
query is the raw apex, while `best_event_rt` remains the representative NL
event's RT.

Discovery uses the shared physical peak owner. An ordinary candidate needs two
adjacent acquired points supporting its half-prominence top, and relief above
the accepted component boundaries of at least four times the local raw-noise MAD. A valley at
or below half the smaller apex separates peaks; a shallow shoulder stays one
envelope; uncut top ripples do not create contrast boundaries. This gate can reject
short real signals. It is not
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

`mixed` summarizes a peak whose events have different bases. The method
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
- both the parent MS1 scan and the MS2 scan must attach to the physical peak
  (see [peak attachment](#cid-nl-support-relations)).

This is method support, not proof of a unique precursor–product pairing. The
original inferred m/z and null loss error stay unchanged. Alignment reacquires
these facts from RAW; a saved CSV verification grants no authority.

### Accurate-mass reading

For a pure `product_plus_neutral_loss` group with a resolved peak, Discovery
reads the ion at the apex scan and one MS1 scan on each side, and reports the
intensity-weighted reading as `ms1_apex_mz`. MS1 is the method's accurate-mass
scan, while ddMS2 runs at a quarter of its resolution, so product + loss only
points at the ion. The reading becomes `peak_mz`, the mass Alignment and Delta
Mass read. The
original precursor and product remain the keys for MS2 scan replay.

- A reading spread wider than 5 ppm comes from two ions and reports none.
- Selected precursors (`scan_precursor`, `scan_monoisotopic`, `mixed`) report
  none. In the 85-sample tissue cohort, about half of the selected precursors a
  reading would have moved landed on a neighbouring ion present at the selected
  m/z when the scan was triggered.

Product + loss assumes a singly charged precursor. When the MS1 ion is read
with a centroid one doubly charged ¹³C spacing (0.50168 m/z) above it in most
scans, the peak's `status` is `doubly_charged`. Its bounds,
area and reason remain visible; it cannot reach Alignment or a Matrix value. A
separate singly charged ion keeps its own admission.

### Trace context

Physical trace context and precursor-identity confidence are separate. A
`product_mz + neutral_loss` group starts with the configured RT context. When
that trace is too short for the signal profile, Discovery reacquires context
only when at least two MS2 seeds agree and their maximum product intensity
reaches the profile's high-intensity threshold. Reacquisition keeps the
original query m/z and tolerance. It answers the physical peak question; it
does not upgrade precursor identity or grant Matrix authority.

### Shared measurements

- Distinct ions can share one MS2 scan. Sharing a scan does not make their
  peaks duplicates; evidence scoring ranks them without deleting either.
- One ion's XIC can contain several physical peaks. Events are partitioned by the
  physical owner's peak selection. Events outside or between peaks stay in the
  event table with their reason and do not support another peak.
- The physical owner prepares each retained trace population once and measures
  each raw peak once; NL events share that measurement, not competing areas.
  Different m/z windows share a population only after acquisition: they use the
  same ppm tolerance, their m/z values are compatible with every member, and
  their complete acquired RT and raw-intensity arrays are exactly equal with
  positive signal. Observed-signal sharing does not establish chemical
  identity; a peak row is not cross-sample identity, selected-peak truth or
  Backfill authority.

## CID-NL support relations

The CID-NL method assessment binds MS2 evidence to physical MS1 peaks. The
current lab acquisition is DDA/ddMS2; DIA is outside this contract because the
lab has no mature DIA method.

- **Peak attachment (Discovery admission).** One rule applies to every
  precursor basis. An NL event supports a Discovery peak only when its precursor
  is verified (recorded, or measured in the parent MS1 for an inferred
  precursor), that mass lies within the precursor ppm window of the peak, and
  the MS2 scan RT, plus the parent MS1 scan RT for an inferred precursor, lie
  within the peak bounds ± 0.1 min. The margin covers DDA sampling a peak's foot
  or tail just past its 10%-height return. An RT outside the bounds that lies
  inside another measured peak of the same trace never attaches through the
  margin. A peak with no attached event is `no_nl_event`.

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
`composite_requirement_not_supported`). Each sample's Discovery peak needs its
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
- When enabled, a peak matched within 10 ppm keeps its admission only when its
  MS1 height is at least 3× the blank's maximum intensity. Below that its
  `status` is `blank`. Qualification changes eligibility
  only; it does not change the peak's boundaries, apex or area.

## Delta Mass Discovery

Delta Mass finds sample-local MS1 peak combinations at preset-defined mass
separations. A candidate must satisfy the mass differences and co-elution;
designated discriminating positions also need apex-region shape correlation.
The method supports two or more positions and has no built-in chemistry.

The built-in Pt presets restore Easy-IC mass coordinates before constructing
features. Matching, RAW confirmation and review use that same per-sample mass
result. The sidecar saves it for review replay; older sidecars without a mass
policy retain vendor coordinates. Restoration changes the measured input, not
the configured 20 ppm window, apex tolerance or R threshold. Pt envelope models
remain review-only.

The Delta Mass ppm setting also controls feature mass linking, so a tolerance
comparison starts before hypotheses are selected. Detector mass-dispersion
quality limits are separate and are not changed by this window setting.

1. Only detector-qualified features anchor a hypothesis, in the roles the
   preset names. The matcher trusts that qualification.
2. Partners are searched in both mass directions from the lowest-mass position,
   within the ppm window and the scan-aware apex tolerance
   `max(apex_floor_sec, scan_interval_multiplier × MS1 interval)`.
3. Measured height ratios are compared with the preset's reference ranges and
   recorded as `ratio_status`. Ratios are evidence only: out-of-range ratios do
   not reject candidates, and expected abundance never predicts missing signal.
4. Missing positions are confirmed in RAW by apex alignment and apex-region
   Pearson correlation. RAW apex tolerance uses the median interval of the
   acquired XIC times, including zero-intensity scans, with the same floor and
   multiplier. It does not reuse the whole-file interval used for initial
   table matching. A two-position method needs its partner in the feature
   table; RAW never completes a solitary seed.
5. Discriminating positions are shape-checked. An optional `lowest_position`
   must have the lowest background-subtracted RAW height and clear background
   support.

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

Review answers: candidate → position XICs → NL assessment → supporting MS2.
Export selected IDs, or all saved candidates, with
`python -m tools.diagnostics.delta_mass_review --help`; open the JSON with
`xic-inspector <review.json>` (or `python -m xic_inspector`).
Choose `--nl-preset` explicitly to assess NL inside saved feature bounds.
Without it, only XICs are acquired; there is no manual MS2 browsing mode.
The Inspector shows unsmoothed RAW XICs on a shared intensity axis, position
labels, configured ΔDa, m/z, saved statuses and feature bounds.
Review XICs use the same expected m/z centers as Discovery, retained at
round-trip precision in the position CSV. Observed partner masses remain
available for feature/precursor evidence; they do not silently recenter the XICs.
Clicking NL support opens its exact spectrum, measured product and XIC bounds;
multiple supporting scans remain navigable without a scan search.
The existing NL matcher, parent-MS1 evidence and peak attribution own the answer.
Missing evidence, ambiguous attribution and read errors remain distinguishable.
Review does not consume shape correlations or independently measured height ratios.
For `pattern_id=pt`, review also compares the synchronous base/+1/+2/+4 net
intensities with Pt+C, Pt+C+Cl, Pt+C+S and Pt+C+2S models. The base defines one
half-height core around the local apex; all four positions use those same scans.
The reported spread is the largest intensity/model amount divided by the smallest
at the best carbon count: closer to 1 means a closer fit, not confirmed identity.
Inspector orders the loaded candidates by this score and shows the model,
carbon count, core scan count and observed/expected ratios. Missing or incompatible
traces show an unavailable reason rather than a fabricated score. This comparison
uses fixed singly charged positions, not charge/origin search, fine-structure
attribution or RT fitting. No spread threshold is applied and no candidate status
or Discovery CSV is changed. Older reviews without this evidence remain readable.
The comparison table expresses both observations and model expectations with
BASE set to 100. Display names use BASE/PLUS N rather than Light/Heavy or
internal `base`/`plus_*` identifiers; custom Heavy positions use their configured
mass offset. Exact Δm/z remains visible separately, and saved position IDs do not
change. Four Pt curves have distinct colors as well as distinct line patterns.
RAW files are matched by saved sample name, not verified as the original bytes.
These are new diagnostic reads, not a replay of the original detector.
Inspector reads the saved report only and never acquires RAW.
NL support is `diagnostic_only`, not chemical identity or full CID-NL qualification.
Neither review nor a support click changes Discovery, candidate admission or Matrix
eligibility; absence of support is not proof of chemical absence.

## Method-specific validation

Discovery validation is method-specific. CID-NL 8RAW/85RAW fixtures are not
universal Discovery gates; the 8RAW fixture is a subset of the 85RAW cohort with
the same processing recipe. Pt pattern work uses Pt-method real samples,
trace and co-elution evidence, no-write authority checks and independent human
adjudication. Reviewed Pt labels are method-development evidence, not ground
truth. Vendor RAW-to-mzML conversion is not an implicit product dependency.

## Verification

- `tests/test_discovery_ion_peaks.py`, `tests/test_discovery_csv.py`,
  `tests/test_discovery_csv_reader.py`

## See Also

- [Alignment](alignment.md) | [Backfill](backfill.md) | [Untargeted method](untargeted-method.md)
- [Presets](presets.md)
