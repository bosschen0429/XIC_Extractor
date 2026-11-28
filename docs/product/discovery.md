# Discovery

Discovery answers which sample-local signals warrant further evaluation. It
turns method-specific seed evidence and MS1 traces into reviewable candidates;
a candidate does not establish cross-sample correspondence or a final value,
and Discovery publishes no product value
([Writer Authority](decision-policy.md#writer-authority)). Providers generate
candidates their own way but publish through one evidence contract, so
Alignment, Backfill and the cell decision do not fork per provider. The code
and its tests own the tolerances, statuses, tables and thresholds; this page
keeps the boundaries, reasons, evidence and limits. The
[Untargeted method](untargeted-method.md) owns the literature basis; the
[Untargeted guide](../user/untargeted.md) owns commands and outputs.

## CID-NL Peaks And Events

### Ions, peaks and events

One raw valley-separated MS1 peak is one candidate and one measurement; NL
events only locate it and add method evidence, so several events on one peak
never compete as areas. Events group by their MS1-measured ion, never by tag,
product or RT. Adjacent XIC windows overlap, so the same ion is recognized by
identical intensity readings rather than mass proximity: different ions in
overlapping windows, such as ¹³C and ¹⁵N isotopes about 25 ppm apart, never
read equal intensity. A peak row is not cross-sample identity, selected-peak
truth or Backfill authority; sharing an MS2 scan does not make two ions'
peaks duplicates.

Only admitted peaks can enter Alignment, choose query centers, mint method
subjects or authorize Backfill; Backfill can still find a new MS1-only
observation of a legitimate group in another sample, but never resurrects a
rejected peak. A shoulder alone does not hold a peak, and plateaus or tails are
rejected per row by
[shape consistency](alignment.md#reject-rows-without-one-elution-shape).

### Accurate-mass reading

Product + loss only points at an ion: ddMS2 runs at a quarter of MS1's
resolution, so an inferred precursor is re-read in MS1 at the apex, and the
peak's mass is the center of the window that measured it. Selected precursors
take no reading: in the 85-sample tissue cohort, about half of the selected
precursors a reading would have moved landed on a neighbouring ion present at
the selected m/z when the scan was triggered. Product + loss assumes a singly
charged precursor, so a peak reading as a doubly charged isotope cannot reach
Alignment.

### Precursor and loss evidence

- The loss tolerance combines both ions' independent mass errors. On 85RAW,
  events whose loss fragment ranks in the top three of the spectrum have a
  median error of 2.5 ppm, flat from m/z 150 to 700, and 93% lie within
  10 ppm.
- An inferred precursor alone grants no strict relation or detection count;
  its loss error is null, not zero, because derived equality is not an
  independent measurement. Verification in the parent MS1 is method support,
  not proof of a unique precursor–product pairing.
- A vendor monoisotopic reference is recorded evidence independent of
  product + loss arithmetic, but no precursor basis proves unique chemical
  parentage.
- Physical trace context and precursor-identity confidence are separate:
  reacquiring a short trace answers the physical question, not identity.

## CID-NL support relations

The method binds MS2 evidence to physical MS1 peaks under one attachment rule
for every precursor basis. The RT margin covers DDA sampling a foot or tail
just past the peak's return; an RT inside another measured peak never attaches
through it. Evidence on a shared endpoint or overlap supports only the
connected set, not an individual peak. Repeated strict scans inside one peak
are one chromatographic event, not independent votes. Shared class fragments
or common losses support class confidence, not analyte-specific proof. AND
composites need every loss on the same physical peak in the same sample.

- Technical failure never becomes `REJECT`, non-qualifying scans cannot
  manufacture it, and absent positive evidence does not prove a search ran;
  an unsearched slot supplies no negative evidence.
- The lab acquisition is DDA/ddMS2; DIA is outside this contract because the
  lab has no mature DIA method.

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
enable it, and opting in requires method-appropriate validation. It changes
eligibility only, never boundaries, apex or area.

- The library keeps no RT and qualifies no shape.
- A short solvent blank on a different LC gradient is diagnostic background,
  not a hard-exclusion reference for a longer method; RT must not be scaled
  linearly between different 15-minute and 60-minute gradients.
- A method-matched blank is a prerequisite for evaluating removal, not proof
  that an m/z-only rule is valid; clean pre-sample solvent injections differ
  from carryover monitors and waste-directed runs.
- Instrument acquisition exclusion and software blank qualification are
  separate: disabling software filtering does not recreate an unacquired
  spectrum.

## Delta Mass Discovery

Delta Mass finds sample-local MS1 peak combinations at preset-defined mass
separations, with co-elution and, at discriminating positions, shape
correlation. It has no built-in chemistry, and its output is
`diagnostic_only`: no Alignment handoff and no Matrix, Backfill,
counted-detection or export authority.

- Ratios are evidence only: expected abundance never predicts missing signal,
  and RAW never completes a solitary seed.
- Detector heights are smoothed screening heights, not areas; the background
  statistic is a screen, not a calibrated detection limit, and its floor exists
  because sparse flanks can have zero MAD.
- Shape similarity never establishes membership; real-sample counts are
  method-development evidence, not an accuracy threshold.

### Pt presets

The built-in Pt presets are provisional applications of this method. They
restore Easy-IC mass coordinates before building features, and matching, RAW
confirmation and review use that same per-sample result. Restoration changes
the measured input, not the configured window or thresholds. The abundance
reference is
[NIST platinum isotope composition](https://physics.nist.gov/cgi-bin/Compositions/stand_alone.pl?ele=Pt);
the base-support tolerance is provisional, not implied by natural abundance,
and ratio agreement is not a calibrated Pt probability.

### Read-only candidate review

Review never changes Discovery, admission or Matrix eligibility. Its RAW reads
are new diagnostic views matched by sample name, not a replay of the detector
or verified original bytes; Inspector itself never reads RAW. NL support there
is `diagnostic_only`, not identity, and its absence is not chemical absence.
The Pt envelope spread compares synchronous intensities with Pt+C, Pt+C+Cl,
Pt+C+S and Pt+C+2S models: closer to 1 is a closer fit, not identity, and no
threshold or status change follows from it.

## Method-specific validation

Discovery validation is method-specific. CID-NL 8RAW/85RAW fixtures are not
universal Discovery gates; the 8RAW fixture is a subset of the 85RAW cohort with
the same processing recipe. Pt pattern work uses Pt-method real samples, trace
and co-elution evidence, no-write authority checks and independent human
adjudication. Reviewed Pt labels are method-development evidence, not ground
truth. Vendor RAW-to-mzML conversion is not an implicit product dependency.
