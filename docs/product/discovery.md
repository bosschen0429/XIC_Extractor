# Discovery

Discovery answers which sample-local signals warrant further evaluation. It
turns method-specific seed evidence and MS1 traces into reviewable candidates;
a candidate does not establish cross-sample correspondence or a final value,
and Discovery publishes no product value
([Writer Authority](decision-policy.md#writer-authority)). Each provider
(CID-NL, HCD-PI, Delta Mass and future ones) generates candidates its own way
but publishes through one shared evidence contract, so Alignment, Backfill and
the cell decision do not fork per provider. Untargeted keeps every candidate
qualifying under its configured evidence space; Targeted keeps only configured
Targets. The [interpretation guide](../user/concepts.md#從-raw-到矩陣)
explains the distinction; the [Untargeted method](untargeted-method.md) owns
the literature basis; the [Untargeted guide](../user/untargeted.md)
owns commands and outputs.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| What is one CID-NL candidate? | One raw valley-separated MS1 peak on one ion's XIC; several windows reading the same centroids are one peak. | [Ions, peaks and events](#ions-peaks-and-events) |
| When does an NL event support a peak? | Its precursor is verified, within the peak's ppm window, and its scan RT lies within the peak bounds ± 0.1 min. | [CID-NL support relations](#cid-nl-support-relations) |
| Which peaks enter Alignment? | Only `admitted` peaks: at least one attached event, not doubly charged, not blank. | [Peak status](#peak-status) |
| Which mass does a peak carry? | The recorded precursor, else the MS1 accurate-mass reading at the apex. | [Accurate-mass reading](#accurate-mass-reading) |
| What does a RAW claim need? | Its full query: scan filter, trace type, mass and RT window, backend and processing basis. | [RAW evidence is a query](#raw-evidence-is-a-query) |
| Is blank qualification on? | No, it is opt-in after method-appropriate validation. | [Blank qualification](#blank-qualification) |
| What does Delta Mass find? | Co-eluting MS1 peaks at preset mass separations; output is diagnostic only. | [Delta Mass Discovery](#delta-mass-discovery) |

## Evidence providers and composites

Strategy families name provider classes: `neutral_loss` for CID-NL and
`delta_mass` for MS1 mass-difference patterns. `DNA_dR` is a neutral-loss tag
and `pt` a delta-mass pattern; `pt_pattern` is the method label for
`delta_mass(pattern_id=pt)`, not a separate strategy. Composite methods combine
providers by explicit set operations on the same base peak:
`pt_pattern + cid_nl(dR)` intersects Delta Mass hypotheses with
`neutral_loss(tag=DNA_dR)` evidence. Delta Mass evaluates its own hypotheses
first, then annotates whether the base matches CID-NL(dR)
([Output](#output)).

## CID-NL peaks and events

### Neutral-loss tolerance

`nl_tolerance_ppm` (default 10) is a per-ion m/z tolerance. A product matches
the configured loss when its absolute loss error is at most
`sqrt((precursor_mz × tol)^2 + (observed_product_mz × tol)^2)` Da, where
`tol = nl_tolerance_ppm × 1e-6`. Both ions carry independent mass error, so the
loss tolerance combines them. The reported loss-error ppm is relative to the
configured loss mass; it is not the acceptance gate. On 85RAW, events whose
loss fragment ranks in the top three of the spectrum have a median error of
2.5 ppm in this unit, flat from m/z 150 to 700, and 93% lie within 10.

### Ions, peaks and events

CID-NL Discovery publishes one `discovery.sqlite` per batch for Alignment,
Delta Mass and diagnostics to query; read its `discovery_peaks` and
`discovery_events` views. `peaks` has one row per physical MS1 peak; `events`
has one row per strict NL event, keyed by MS2 scan, tag and product, so a scan
matching two tags is two events. Each event names the `peak` it locates and,
separately, the `unattached_reason` it does not attach there; `peak` is empty
only when the ion has no measured peak at the event RT. Events carry their
precursor metadata and parent-MS1 observation, so Alignment assesses them
without reading RAW. A stopped run leaves the previous file unchanged; layout,
part-file merging, derived counts and schema refusal are pinned by
[`test_discovery_store.py`](../../tests/test_discovery_store.py).

- **Ion.** Events are grouped only by their MS1-measured ion: the recorded
  scan precursor, or the single parent-MS1 centroid of an inferred precursor.
  Product + loss is the fallback only when the parent scan reads no single ion.
  A group opens at the lowest unassigned ion and takes every ion within the
  precursor ppm tolerance of that anchor, so no ion bridges two groups. Tag,
  product and RT never split an ion. Its XIC center, `xic_mz`, is the
  representative event's MS1 reading before acquisition: an MS1-measured ion
  first, then the configured tag order, then product intensity.
- **Peak.** Each raw valley-separated peak on an ion's XIC is one row and one
  measurement. `peak_id` is `{sample}@mz{xic_mz}_rt{raw apex RT}`, named by the
  XIC of the ion whose measurement is kept; measuring the peak again in another
  window (see [accurate-mass reading](#accurate-mass-reading)) does not rename
  it.
- **Valley.** An event on a valley shared by two peaks belongs to the earlier
  peak.
- **Evidence.** The event table lists every event located on the peak,
  attached or not, because Alignment re-judges them from it; the peak row keeps
  only `best_ms2_scan_id`, which names the representative event there, not the
  peak's mass. `attached_event_count` and `status` count attached events only.
  The peak's mass is `peak_mz` ([accurate-mass reading](#accurate-mass-reading)).

| `unattached_reason` | Meaning |
| --- | --- |
| a precursor status (`parent_ms1_ion_missing`, `neutral_loss_mismatch`, …) | The precursor does not verify the loss (see [precursor basis](#precursor-basis)) |
| `mass_outside_peak` | The verified precursor lies outside the peak's precursor ppm window |
| `rt_outside_peak` | The MS2 scan, or the parent MS1 scan of an inferred precursor, lies outside the peak bounds ± 0.1 min |
| `no_peak_at_rt`, or a physical evaluation reason | The ion's XIC has no measurable physical peak at the event RT |

#### Same ion in several windows

Adjacent XIC windows overlap, so one ion can be measured in
several. A trace point sums the centroids inside its window, so windows
holding the same centroids read exactly the same intensity; a centroid near a
window edge can fall inside one window only. Two measurements are one peak
when their mass windows overlap, each apex lies inside the other peak's
bounds, and on the scans where both read non-zero inside the shared part of
the two peaks at least 3 read exactly equal and those are the majority.
Different ions in overlapping windows, such as ¹³C and ¹⁵N isotopes about
25 ppm apart, never read an equal intensity. The relation is closed
transitively, so the result does not depend on input order, channel order or
peak IDs. The kept measurement is one a recorded precursor located when the
group has one, then the window observing the most non-zero scans of the
group's peaks, then the wider RT context, then the lower `xic_mz` and earlier
context. Its RT context represents the peak, and every event of the group is
attached against it.

### Peak status

`status` decides what may enter Alignment. When several apply, the first row of
this table wins.

| `status` | Meaning | Enters Alignment |
| --- | --- | --- |
| `doubly_charged` | The MS1 ion reads as a doubly charged isotope ([accurate-mass reading](#accurate-mass-reading)) | No |
| `blank` | Demoted by [blank qualification](#blank-qualification) | No |
| `no_nl_event` | A measurable MS1 peak without an [attached](#cid-nl-support-relations), precursor-verified NL event | No; review only |
| `admitted` | At least one attached event and none of the above | Yes |

Peaks that are not `admitted` cannot
choose query centers, merge into admitted peaks, mint method subjects, supply
support scans or authorize Backfill. A legitimate group can still find a new
MS1-only observation in another sample through [Backfill](backfill.md); that
observation cannot resurrect a rejected peak or create a new group. A batch with
no admitted peak completes with no subjects and an empty Matrix.

A shoulder or overlapping envelope alone does not hold a peak. Background
plateaus, noise texture and tail extensions are rejected per row by
[shape consistency](alignment.md#reject-rows-without-one-elution-shape), not per
peak. A qualified peak's area is measured on its segment; a split band is
measured on its whole envelope (see
[Alignment](alignment.md#join-groups-and-consolidate-split-bands)).

### Physical qualification

Each raw valley-separated peak owns one measurement; NL events only locate it
and add method evidence. Several events on one peak give one measurement and
one peak row, not competing areas, and the event table keeps each event. The
measurement query is the raw apex; the representative event keeps its own MS2
RT.

- **Acquisition.** Within a sample, overlapping or adjacent RT requests share
  RT planning, but every distinct precursor center and ppm window is acquired;
  mass compatibility alone does not make windows interchangeable. A bounded
  raw-only read probe can extend the shared context before one canonical
  preparation and segment measurement; it does not quantify each NL request
  separately.
- **Shared population.** Different m/z windows share a prepared population
  only after acquisition, when they use the same ppm tolerance, their m/z values
  are compatible with every member, and their complete acquired RT and
  raw-intensity arrays are exactly equal with positive signal. Different
  signals, ppm windows and isotope-separated channels stay separate. Sharing
  observed signal does not establish chemical identity.
- **Several peaks, several ions.** One ion's XIC can hold several peaks; events
  are partitioned by the physical owner's peak selection, and events outside or
  between peaks stay in the event table with their reason without supporting
  another peak. Distinct ions can share one MS2 scan; that does not make their
  peaks duplicates, and both are kept.
- A peak row is not cross-sample identity, selected-peak truth or Backfill
  authority.

The shared physical peak owner qualifies peaks. An ordinary candidate needs two
adjacent acquired points supporting its half-prominence top and relief above
its accepted boundaries of at least four times the local raw-noise MAD. A
valley at or below half the smaller apex separates peaks; a shallow shoulder
stays one envelope; uncut top ripples create no contrast boundary. This gate
can reject short real signals; it is not chemical identity and does not
replace cross-sample support. [Signal processing](signal-processing.md) owns
the method and its evidence.

### Precursor basis

A CID-NL mass reference has one basis; `mixed` summarizes a peak whose events
differ. The method assumes singly charged ions and rejects known incompatible
charge. A missing or zero vendor value is unavailable, not a measured zero.

| Basis | Reference | Condition for a strict relation |
| --- | --- | --- |
| `scan_precursor` | The MS2 scan's selected isolation m/z | — |
| `scan_monoisotopic` | A distinguishable vendor-recorded monoisotopic m/z, independent of product + loss arithmetic | Only inside the recorded nominal isolation window; outside or unknown stays a mass-compatible candidate |
| `product_plus_neutral_loss` | Inferred: product m/z plus the configured loss | Only after [verification](#inferred-precursor-verification) |

No basis proves unique chemical parentage. Inference stays inside the recorded
isolation window: center = selected m/z + isolation offset, radius = the
smaller of half the recorded width and `ms2_precursor_tol_da` (default 0.8 Da,
the half-width of the 1.6 Da method). It establishes no precursor identity and
does not replace the NL tolerance. Its loss error is null, not zero: derived
equality is not an independent measurement and earns no ranking advantage.

### Inferred-precursor verification

An inferred precursor alone grants no strict NL relation or detection count.
After physical qualification, Discovery reads the recorded parent MS1 scan
within the precursor ppm window. Exactly one positive centroid supplies an
independent reference; a missing scan or ion, or several centroids, leave the
inference unresolved. The measured mass must lie inside the nominal isolation
window and satisfy the per-ion NL tolerance, and both the parent MS1 and the
MS2 scan must [attach](#cid-nl-support-relations) to the peak. This is method
support, not a proven precursor–product pairing; the inferred m/z and null
loss error stay unchanged, and Alignment reassesses the parent-MS1 observation
from the event table.

### Accurate-mass reading

`peak_mz` is the one mass Alignment and Delta Mass read, written once by
Discovery and always the center of the window that measured the peak: the
reading below for a peak whose events are all inferred, else `xic_mz`. A group
with a recorded precursor takes no reading; that precursor fixes the ion.

For a measurement located only by `product_plus_neutral_loss` events,
Discovery reads the ion at the apex scan and one MS1 scan on each side: MS1 is
the accurate-mass scan, while ddMS2 runs at a quarter of its resolution, so
product + loss only points at the ion. When the intensity-weighted reading
differs from the XIC center, the peak is measured once more in a window
centred on the reading, with the same RT context and tolerance, selecting the
peak at its raw apex; that measurement judges the peak, and the reading is
reported as `ms1_apex_mz`. A window that selects nothing there, or a peak with
another raw apex because its extra signal merges or splits peaks, keeps the
first measurement and takes no reading. `peak_id` keeps the XIC center, the
original precursor and product stay the MS2 replay keys, and Alignment
measures the peak again from RAW.

- A reading spread wider than 5 ppm comes from two ions and reports none.
- Selected precursors (`scan_precursor`, `scan_monoisotopic`, `mixed`) report
  none. In the 85-sample tissue cohort, about half of the selected precursors a
  reading would have moved landed on a neighbouring ion present at the selected
  m/z when the scan was triggered.

Product + loss assumes a singly charged precursor. When the MS1 ion read at
the apex has a centroid one doubly charged ¹³C spacing (0.50168 m/z) above it
in most scans, the peak's `status` is `doubly_charged`, judged once per peak
after same-ion grouping. Its bounds, area and reason stay visible, but it
cannot reach Alignment or a Matrix value; a separate singly charged ion keeps
its own admission.

### Trace context

Physical trace context and precursor-identity confidence are separate. A
`product_mz + neutral_loss` group starts with the configured RT context. When
that trace is too short for the signal profile, Discovery reacquires context
only when at least two MS2 seeds agree and their maximum product intensity
reaches 100,000. Reacquisition keeps the
original query m/z and tolerance. It answers the physical peak question; it
does not upgrade precursor identity or grant Matrix authority.

## CID-NL support relations

An NL event attaches to a Discovery peak only when its precursor is verified
(recorded, or measured in the parent MS1 for an inferred precursor), that mass
lies within the peak's precursor ppm window, and the MS2 scan RT, plus the
parent MS1 scan RT for an inferred precursor, lie within the peak bounds ±
0.1 min. One rule covers every precursor basis, in Discovery admission and in
method assessment. The margin covers DDA sampling a foot or tail just past the
10%-height return; an RT inside another measured peak of the same trace never
attaches through it. The lab acquisition is DDA/ddMS2; DIA is outside this
contract because the lab has no mature DIA method.

- **Strict support.** An event attached to exactly one m/z-compatible
  candidate, with no positive-width overlap with another compatible peak,
  supports it individually, including a scan at an isolated peak's boundary or
  within its 0.1 min margin.
- **Set relation.** A scan on an endpoint shared by adjacent peaks, or a peak
  with positive-width overlap, relates to the connected candidate set, with no
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

### Assessment results

| Result | Meaning |
| --- | --- |
| `SUPPORT` | At least one strict relation |
| `HOLD` | Set-only evidence, or a completed Backfill search without qualifying support |
| `REJECT` | Qualifying CID-NL evidence with no usable physical MS1 peak |

- Non-qualifying scans cannot manufacture `REJECT`. The technical state
  (`evaluated`, `unsupported`, `failed`, `not_evaluated`) is a separate axis:
  technical failure never becomes `REJECT`, and absent positive evidence does
  not prove a search ran.
- A sample with no eligible Discovery input is `not_evaluated`. A later
  group-guided Backfill that finds nothing qualifying makes it `HOLD`, distinct
  from the unsearched slot and not a rejection.
- A subject with at least one evaluated result, all of them `HOLD` or
  `REJECT`, and every other slot unsearched is excluded under `cid_nl_requirement_not_supported` (or
  `composite_requirement_not_supported`). Each sample's Discovery peak needs its
  own evaluated answer; an unsearched slot supplies no negative evidence.

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

Blank qualification is off by default; producing a blank library does not
enable it, and opting in requires method-appropriate validation. When enabled,
a peak matched within 10 ppm keeps its admission only when its MS1 height is at
least 3× the blank's maximum intensity; below that its `status` is `blank`,
unless it reads as doubly charged. It changes eligibility only, not the peak's
boundaries, apex or area.

- The library aggregates centroid m/z and maximum intensity; it keeps no RT
  and qualifies no shape.
- A short solvent blank on a different LC gradient is diagnostic background,
  not a hard-exclusion reference for a longer method. RT must not be scaled
  linearly between different 15-minute and 60-minute gradients.
- Background references distinguish clean pre-sample solvent injections from
  post-standard or post-sample carryover monitors and waste-directed runs. A
  method-matched blank is a prerequisite for evaluating removal, not proof
  that an m/z-only rule is valid.
- Instrument acquisition exclusion and software blank qualification are
  separate: an exclusion can prevent an MS/MS trigger, and disabling software
  filtering does not recreate an unacquired spectrum.

## Delta Mass Discovery

Delta Mass finds sample-local MS1 peak combinations at preset-defined mass
separations. A candidate must satisfy the mass differences and co-elution;
designated discriminating positions also need apex-region shape correlation.
The method supports two or more positions and has no built-in chemistry.
Output is `diagnostic_only`, with no Alignment handoff and no Matrix,
Backfill, counted-detection or export authority.

### Matching

1. Only detector-qualified features anchor a hypothesis, in the roles the
   preset names; the matcher trusts that qualification. The RAW adapter gives
   the detector vendor-centroided MS1 ions, so profile samples on a mass peak's
   sides are not separate features. Detector heights are smoothed screening
   heights, not areas.
2. Partners are searched in both mass directions from the lowest-mass position,
   within the ppm window and the scan-aware apex tolerance
   `max(apex_floor_sec, scan_interval_multiplier × MS1 interval)`. The ppm
   setting also controls feature mass linking, so tolerance comparison starts
   before hypotheses are selected; detector mass-dispersion limits are
   separate.
3. Measured height ratios are compared with the preset's reference ranges and
   recorded as `ratio_status`. Ratios are evidence only: out-of-range ratios do
   not reject candidates, and expected abundance never predicts missing signal.
4. Missing positions are confirmed in RAW by apex alignment and apex-region
   Pearson correlation. RAW apex tolerance uses the median interval of the
   acquired XIC times, zero-intensity scans included, with the same floor and
   multiplier, not the whole-file interval of table matching. A two-position
   method needs its partner in the feature table; RAW never completes a
   solitary seed.
5. Discriminating positions are shape-checked. An optional `lowest_position`
   must have the lowest background-subtracted RAW height and clear background
   support.

Shape similarity never establishes membership by itself.

### Background and base support

- **Background.** The median of the lower-median flank of two configured
  intervals around the base apex; the lower flank limits contamination from a
  neighbour on one side. Variation is `1.4826 × MAD` on that flank. The lowest
  position needs net height of at least the larger of an absolute floor and a
  multiple of that variation. The floor is needed because sparse flanks can
  have zero MAD, and zero variation is not infinite signal-to-noise. This is a
  screening statistic, not a calibrated detection limit.
- **Base support.** The optional check sums the named non-base positions' net
  heights and divides by the sum of their reference ratios (geometric centers
  of each range); the base must reach `min_base_support` of that expectation.
  This is one pooled lower bound, not separate ratio gates or an upper bound.
  Each height is its own central-window maximum, not a simultaneous spectrum or
  area, and isotope overlap or interference can affect the distribution.

### Pt presets

The built-in Pt presets are provisional applications of this method. They
restore Easy-IC mass coordinates before building features, and matching, RAW
confirmation and review use that same per-sample result, saved per sample in
the Delta Mass file. Restoration changes the measured input, not the
configured 20 ppm window, apex tolerance or R threshold. The preset TOML files
own every numeric choice: positions, anchors, ratio bounds, background floor
and base-support tolerance. The abundance reference is
[NIST platinum isotope composition](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Pt);
the base-support tolerance is provisional, not implied by natural abundance.
Ratio agreement is not a calibrated Pt probability, and Pt envelope models are
review-only.

### Output

A run publishes only established candidates. `discovery.sqlite` holds the
method, each sample's mass-restoration result, the candidates, their positions
and the feature bounds review reads; `delta_mass_candidates.csv` writes its
`delta_mass_candidate_table` view as the readable table, one row per candidate
with sample, m/z, RT, height, positions found and joined dR peak.

A composite method reads dR peaks from the CID-NL `discovery.sqlite` of the
same RAW (`--cid-nl-discovery`). The annotation uses admitted CID-NL peaks and
the sample-local predicate on the peak's `peak_mz` and apex RT; it does not
alter CID-NL decisions. An unmatched base records why: a non-admitted dR peak
inside the window (`not_admitted`, with its status), an admitted dR peak of the
same ion outside the RT window (`outside_rt_tolerance`), or neither
(`no_cid_nl_peak`). Base m/z and apex RT are written at full precision so the
table reproduces the join. Real-sample counts are method-development
evidence, not an accuracy threshold.

### Read-only candidate review

Review follows candidate → position XICs → NL assessment → supporting MS2 and
never changes Discovery, admission or Matrix eligibility.

- **Reads.** Review XICs use Discovery's expected m/z centers at full
  precision; observed partner masses stay evidence and do not recenter the
  XICs. RAW files are matched by saved sample name, not verified as the
  original bytes. These are new diagnostic reads, not a replay of the
  detector. Inspector reads the saved report only and never acquires RAW.
- **NL.** NL is assessed inside saved feature bounds only when `--nl-preset`
  is chosen; otherwise only XICs are acquired, and there is no manual MS2
  browsing mode. The shared NL matcher, parent-MS1 evidence and peak
  attribution own the answer. NL support is `diagnostic_only`, not identity or
  full CID-NL qualification; its absence is not proof of chemical absence.
  Missing evidence, ambiguous attribution and read errors stay distinct.
- Review does not consume shape correlations or independently measured height
  ratios.
- **Pt envelope.** For `pattern_id=pt`, review compares the synchronous
  base/+1/+2/+4 net intensities with Pt+C, Pt+C+Cl, Pt+C+S and Pt+C+2S models
  over one half-height core around the base apex, shared by all four
  positions. The spread is the largest intensity/model amount divided by the
  smallest at the best carbon count: closer to 1 is a closer fit, not
  identity. It uses fixed singly charged positions, with no charge/origin
  search, fine-structure attribution or RT fitting. No spread threshold
  applies and no saved status changes; Inspector orders candidates by it and
  shows model, carbon count, core scan count and observed/expected ratios, and
  missing or incompatible traces show an unavailable reason instead of a
  score.

Export with `python -m tools.diagnostics.delta_mass_review --help` and open
the JSON with `xic-inspector <review.json>` (or `python -m xic_inspector`).
[`test_delta_mass_review.py`](../../tests/test_delta_mass_review.py) and
[`test_delta_mass_review_browser.py`](../../tests/test_delta_mass_review_browser.py)
pin the export and the display (shared intensity axis, BASE/PLUS N position
names and colors, exact support spectra without scan search, unavailable
states).

## Method-specific validation

Discovery validation is method-specific. CID-NL 8RAW/85RAW fixtures are not
universal Discovery gates; the 8RAW fixture is a subset of the 85RAW cohort with
the same processing recipe. Pt pattern work uses Pt-method real samples,
trace and co-elution evidence, no-write authority checks and independent human
adjudication. Reviewed Pt labels are method-development evidence, not ground
truth. Vendor RAW-to-mzML conversion is not an implicit product dependency.

## Verification

- [`test_discovery_ion_peaks.py`](../../tests/test_discovery_ion_peaks.py)
- [`test_discovery_store.py`](../../tests/test_discovery_store.py)
- [`test_discovery_output_publication.py`](../../tests/test_discovery_output_publication.py)

## See Also

[Alignment](alignment.md) · [Backfill](backfill.md) ·
[Untargeted method](untargeted-method.md) · [Presets](presets.md)

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Discovery batch file | `xic_extractor.discovery.store` |
| Delta Mass review export | `tools.diagnostics.delta_mass_review` |
| Delta Mass review display | `xic_inspector` |
