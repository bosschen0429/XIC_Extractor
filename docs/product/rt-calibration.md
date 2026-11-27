# Retention Time Calibration

Retention Time (RT) calibration places every injection on a common RT coordinate
frame so cross-sample correspondence, Backfill forecasting and review can compare
observations across runs. It models run-to-run retention drift, not compound
identification.

[`rt_anchors`](../../xic_extractor/alignment/rt_anchors.py) owns the anchor
observations both models read,
[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
the knots and the fit, and [`rt_drift`](../../xic_extractor/alignment/rt_drift.py)
the drift model on top of it;
[`drift_evidence`](../../xic_extractor/alignment/drift_evidence.py) owns production
ISTD RT correspondence; downstream consumers access the frozen map
through `SampleRtFrame`. Shared terminology remains owned by the
[Domain Glossary](domain-glossary.md). See [Alignment](alignment.md) for how
grouping consumes this frame.

## Calibration Contract

### Anchor qualification

Bootstrap links peaks in measured RT. Established bands supply measured anchors
from direct, quantifiable members; bootstrap membership is not final membership.

A Known-ISTD reference from a target list takes each sample's ISTD apex from
its Discovery peaks without reading RAW: a peak of any status at the target's
m/z, carrying an event of the target's neutral loss, with its apex within 2 min
of the expected RT. The spiked standard triggers the most events; ties go to the
apex closest to the expected RT. A sample whose standard DDA never sampled has
no coordinate for that label. An imported reference workbook retains its
declared measurement contract; the reader does not add MS2 qualification. See the
[Untargeted guide](../development/command-line.md#alignment-cli-inputs) for CLI
input selection.

| Mechanism | Current source and use |
| --- | --- |
| Known-ISTD reference | Exact identity bindings and measured coordinates. Bootstrap comparison uses the shared fitter with ISTDs only; final calibration combines those measurements with ordinary anchors. |
| Sample RT calibration | One frozen map per batch, fitted from its measured ISTDs and the ordinary bootstrap correspondences of the reference population without shoulder overlap. Final Grouping, Backfill localization/admission, and replay consume this map through `SampleRtFrame`. |
| Batch drift model | Frozen batch state (B), sample shift (M) and drift anchors, measured on the same reference population; see [Drift beyond the map](#drift-beyond-the-map). |
| Band | Links measured member peaks in injection order; the final band supplies sample-local Backfill coordinates. |
| Calibration diagnostic | Uses the same fit owner with detected-cell inputs and an interleaved held-out comparison. Its files do not alter production results. |

[`rt_anchors`](../../xic_extractor/alignment/rt_anchors.py) produces the
anchor observations once per initial grouping: an owner with direct method
support, a primary identity event and quantification-eligible integration,
with its method channel, apex RT and, when it belongs to an established
bootstrap band holding at least two such owners, that band. Each band is one
calibrating correspondence; the general map also uses the measured ISTD
coordinates. The [drift model](#drift-beyond-the-map) reads every observation
by channel, banded or not. M is [voted](#drift-beyond-the-map) from the same
population's direct bands. Removing a peak only after grouping is not
equivalent: that peak may have set a channel center or changed which bootstrap
bands formed. Final rows and their coefficients are inferred on the full
population.

The calibration population is read from the first pass, not acquired again.
Bootstrap bands exist only to calibrate, so they are linked only where they
can: not for a workset whose anchors fall short of the correspondence quorum
below in every cohort. Such owners stay unlinked until final grouping, and
their anchors still reach the drift model.

[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
turns each agreed bootstrap group into one ordinary correspondence and fits the
map. A correspondence's reference is its same-cohort median observed RT; it
needs observations in at least half the declared cohort (rounded up), with at
least three measured members. These are anchor qualifications, not
feature-retention rules: other observations remain available to grouping. Exact
duplicate sample/RT patterns contribute only once, keeping every channel the
duplicates were measured in. Measured ISTDs require at least two same-cohort
measurements, but not ordinary-feature prevalence. Missing ISTDs, predicted
coordinates and Backfill observations are not imputed as anchors. Declared
batch IDs define cohorts; empty IDs share an unspecified cohort and sample role
does not define the cohort.

Each knot is one correspondence in one cohort. It records that cohort, the ISTD
label for a measured ISTD, and the method channels (precursor m/z, neutral
loss) of its observations; ISTD knots and the diagnostic fits, which have no
method channel, record none.

### Robust LOESS fit

Each sample uses robust local-linear regression of reference-minus-observed RT,
with a 0.3 neighborhood fraction and three residual reweightings. Repeated
observed RT positions receive one median reference vote. A new fit requires at
least eight distinct measured RT positions, and at least eight positions must
retain nonzero robust weight in the final fit. Local neighborhoods include at
least eight positions; they expand when rejected points would otherwise fill
the final neighborhood. Distance weights remain unequal: eight positions are
not a guarantee of precision or dense coverage. A deficient strong fit is
unavailable, not replaced by a two-point line or interpolated pseudo-anchors.
A fit whose reference order folds between adjacent positions merges only those
positions into one knot at their mean observed and fitted RT
(pool-adjacent-violators); ordered stretches keep their fitted values. A fold
broad enough to leave fewer than eight knots keeps the strong fit unavailable.
In 85RAW two Tumor injections folded at one pair 0.04 min apart, where the
offset drops from about +40 s to about 0 s. With only ISTDs, contradictory
landmark order cannot establish strong calibration from the same
measurements. Ordinary corroboration can support a fit despite a conflicting
standard; fitted standard residuals remain visible and do not reselect that
standard's peak.

Eight is a conservative application support floor, not a universal LOESS
theorem. Local support, polynomial degree, bandwidth and sampling density still
matter; see [Cleveland (1979)](https://doi.org/10.1080/01621459.1979.10481038)
and the [NIST LOESS guidance](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm).
It counts RT positions per injection, not the number of samples in the batch.
The fit consumes fixed bootstrap anchors once; final regrouping and Backfill do
not feed their own results back into that fit. Grouping replay refits from the
same retained anchors; a saved map without them keeps its coordinates and is
not refitted under this floor.

### ISTD landmark bounded influence

Within a usable fit, a measured ISTD also supplies a bounded local landmark.
Its fitted coordinate can move toward its measured cohort reference by at most
six times the median absolute residual of the final nonzero-weight positions,
using the same residual multiplier as the robust fit. Rejected correspondences
do not enlarge this influence limit. The adjustment must preserve order against
both the original neighboring fitted coordinates and neighboring landmark
proposals; conflicting proposals keep their original fitted values. It changes
only an existing measured knot, not neighboring knots, the observed support
range, or the count of supported positions. The frozen interpolation therefore
carries this local correction without a second ISTD override. Large or
order-conflicting standard residuals remain visible rather than forcing the
curve through every standard. This bound limits influence; it is not a
statistical confidence interval or independent identity verification.

### Graded query support

The strong-only map and inverse remain bounded by the supported anchor range.
Localization explicitly uses a graded query instead:

- Inside a usable curve, preserve its LOESS coordinate exactly.
- Outside it, use the nearest endpoint's constant shift (`boundary_shift`).
  This weak continuation joins continuously and preserves order and a unique
  inverse; it does not extend strong calibration support.
- With at least two qualified distinct positions but no usable strong fit,
  use their median reference-minus-observed shift (`median_shift`). Zero or
  one position does not establish a batch shift. Failed strong-fit status is
  retained, not relabelled as strong support.

The frozen measured support is prepared once. Each answer carries its method,
qualified position count, measured range, model residual spread, nearest-anchor
distance and distance outside model support. These are descriptive facts, not
probabilities or bounds on true error. The bounded map supplies band membership
coordinates, apart from an under-equilibrated injection's
[early segment](#early-segment-of-an-under-equilibrated-injection); the graded
estimates remain available for Backfill forecasts and their review facts. Accepted membership risks retain the applied query facts in the
review outputs. Weak localization uses an `estimated_` basis and remains a
forecast, not a physical observation. Neither query changes observed RT, peak
boundaries, area, identity, or Backfill admission rules.

The persisted calibration retains the measured support for these queries. Legacy
v1/v2 coordinates remain exact and bounded, with quality unrecorded; old knots
do not create weak estimates. Bootstrap's strong-only APIs and ISTD-only unique
same-target fallback retain their existing meaning.

### Early segment of an under-equilibrated injection

After a short method (a 15 min Blank or an 8 min SDOLEK), the next injection
starts on a column that has not re-equilibrated, and its early-eluting compounds
come early (#786): shift(RT, injection) = A × g(RT). The map absorbs the shift
from about 9 min, where the injection has anchors; before that it usually has
none, so its early peaks kept the whole shift, up to two minutes, and fell out
of their bands.

[`early_segment_shift`](../../xic_extractor/alignment/early_segment_shift.py)
measures A from the frozen calibration's own standards. A standard's deviation is
its observed RT minus the median of the same standard in up to three injections
on each side. An injection is affected when its earliest standard is earlier
than both 10 s and five times the batch's median absolute deviation, and its next
standard moved the same way by a fraction closer to g's decay than to a uniform
shift; with fewer than two standards no injection is affected. g is
exp(−(RT − 8.5)/5.26) from 8.5 min and, before that, the medians #786 measured:
0 at 2.6 min, rising to 2.6 at 5.0 min, then 1.4 at 6.5 min and 1.0 from 7.5 min.
Both were measured on the 60 min dna_dr gradient; another gradient needs g
measured again. Single compounds at 4–7 min spread from 1.2 to 3.1 times A.

Only band linking reads it. An affected injection's peak before 8.5 min moves to
the elution time solving observed = elution + A × g(elution), then is read
through the maps of the unaffected injections around it. Such a predicted
coordinate does not vote on M. The published calibration, Backfill localization
and every output keep their values.

### Persisted RT replay

Explanation summaries retain the final frozen batch `sample_rt_calibration`,
including its knots with their cohort, ISTD label and channels, fitted sample
knots and distinct qualified observation positions with their
`estimate_support`. The bootstrap ISTD reference's measured identity provenance
and target snapshot are recorded once, in the run metadata
`known_istd_reference_*` keys, not as a second display calibration. Inspector and
Workbench replay the same estimate owner without refitting: `loess` within the
fit, continuous endpoint `boundary_shift` outside it, and qualified
`median_shift` for samples without a curve. Only samples with no estimate fall
back to raw RT, with an explicit label. This changes display coordinates, not
measured signal, area, grouping or quantification. Injection order is preserved
separately. Obsolete explanation layouts and calibrations whose knot fields
differ from the current ones require rerunning Alignment.

Optional grouping snapshots (`--stop-after grouping --save-grouping-inputs`) keep the
frozen drift model, including the owners each drift anchor observed, so replay
reuses its B shapes and M. Explanation summaries keep only the calibration.

### RT anchor table

Every Alignment run writes `alignment_rt_anchors.tsv`, and
`--stop-after grouping` writes `rt_anchors.tsv`: one row per frozen knot, with
`knot_id`, `cohort`, `kind` (`ordinary` or `istd`), `istd_label`,
`precursor_mz`, `neutral_loss_da`, `reference_rt_min` and `sample_count`. A knot
measured in several channels lists them in matching order, separated by `;`;
ISTD knots leave the channel columns empty, as does a composition channel's
neutral loss. The table is a projection of the persisted knots, not another
calculation; without a calibration it has only its header. Per-sample anchor
RTs are not written.

## Declared batches

A run whose sample metadata declares more than one batch ID aligns each batch
alone, as if it were the only input: subject minting, worksets, bootstrap,
calibration, drift, final grouping and Backfill see only that batch's samples.
[`batch_correspondence`](../../xic_extractor/alignment/batch_correspondence.py)
is the one step that relates the batches; it pairs finished Matrix groups and
never changes a batch's membership. One declared batch, or no metadata, takes
the single-batch path unchanged. There is no cross-batch Backfill.

- **Reference batch**: the batch with the most samples; on equal sizes, the
  batch whose latest RAW acquisition is later. Batch IDs and input order do not
  decide it.
- **Feature**: a batch's Matrix-destination group, placed at the median of its
  members' calibrated RT (raw apex for a sample without an estimate). Two
  features share a channel when their neutral loss tags match and their
  precursor m/z agree within the Discovery precursor tolerance both ways.
- **Coarse prediction**: the batch's ISTD knots against the reference batch's,
  with offsets interpolated linearly and the endpoint offset beyond them. Its
  search half-width is `max(0.2 min, 2 × largest leave-one-out ISTD error)`.
- **Anchors**: a feature pair unique both ways inside that window, with both
  features meeting the ordinary anchor threshold of their own batch. Pairs
  within 0.05 min of each other in both batches form one elution family and
  vote once; a family containing an ISTD takes the ISTD's coordinates. Every
  excluded candidate keeps its reason: `no_candidate`, `outside_window`,
  `ambiguous`, `counterpart_below_threshold` or `competitor`.
- **Batch map**: the non-reference batch is fitted as one sample by the sample
  calibration fitter, with the same LOESS, pool-adjacent-violators, eight-position
  floor and graded query. With fewer than two ISTDs in common the batch has no
  map and its features stay unpaired (`unpaired_no_map`).
- **Pairing**: a Matrix feature bound to a known ISTD pairs with the other
  batch's feature bound to the same ISTD, by identity and without an RT
  search; neither is a candidate for any other feature. Every other
  feature is searched through the map. The search window follows RT: at each
  ISTD its half-width is `max(0.2 min, 2 × that ISTD's leave-one-out error of
  the map)`, interpolated linearly between neighboring ISTDs in the reference
  batch's coordinate and held at the nearest ISTD's value before the first and
  after the last. Each feature's window is taken at its own reference-batch RT,
  so a region the ISTDs measure well keeps a narrow window and a poorly
  measured region a wide one. A feature pairs only when the match is unique
  both ways. More than one candidate in the
  window is `unpaired_ambiguous` and lists the candidates; a reverse match that
  points elsewhere lists that competitor. An unpaired feature keeps its own row.

The multiplier 2 and the 0.2 min floor are fixed before results are read; the
multiplier changes only on leave-one-out ISTD evidence.

Limit: outside the range of its anchors the map extrapolates from the
endpoint, and the search window does not widen with the extrapolation
distance. Leave-one-out errors measure only between ISTDs, so drift that bends
beyond the outermost ISTD is invisible to both the coarse window and the map.
The method therefore needs ISTDs covering the early and late ends of the
gradient; without them, nonlinear drift there can pair a feature with a
neighboring same-channel feature instead of its counterpart.

Outputs:

- The Matrix writes a paired row once, with the reference batch's Mz and RT and
  both batches' cells. An unpaired row of another batch is written with its RT
  converted through the batch map. A batch's absent samples are empty in the
  Matrix and `not_searched` in status and cells; no cell is recomputed.
- `alignment_batch_correspondence.tsv` (`batch_correspondence.tsv` with
  `--stop-after grouping`) has one row per feature: `batch_id`, `group_id`,
  `neutral_loss_tag`, `precursor_mz`, `batch_rt_min`, `reference_rt_min`,
  `sample_count`, `known_identity_label`, `anchor` (ISTD label or `pair`),
  `state`,
  `partner_group_id` and `competitor_group_ids`.
- Run metadata `batch_correspondence` records each map's status, knot and
  anchor counts, coarse ISTDs, leave-one-out errors, the coarse half-width,
  the search window (half-width at each ISTD's reference RT), and anchors
  and exclusion reasons per 4 min reference RT segment, with feature states and
  paired and single-batch row counts. `rt_drift_summary` is keyed by batch.
- The review TSV adds `batch_ids` and `batch_pairing`. Counts add up; the other
  judgement columns list each batch's distinct values.
- The explanation report records a paired row as the reference batch's group,
  joined by its partner's members and cells, with the less certain
  disposition. Initial groups, drift coefficients and empty-cell positions stay
  each batch's own; an empty cell of the other batch is inspected at the
  reference group's centre m/z.
- The RT anchor table lists every batch's knots in that batch's own coordinate,
  distinguished by `cohort`; knot IDs restart per batch. The persisted
  calibration follows each sample's map with its batch map, so replay reads the
  reference batch's coordinate.

## Drift beyond the map

The user guide's [RT 漂移模型](../user/rt-drift.md) page explains this section
with figures; keep it consistent when the model changes.

The general map moves every compound at one RT together. A few compounds move
more, and differently: after general calibration,

> RT(c, s) = center(c) + a(c) × B(s) + b(c) × M(s) + error

- **Batch state B** is the batch's slow chromatographic change along injection
  order. Pooled QC, a fixed matrix, carries it alone.
- **Sample shift M** is each sample's own retention offset, felt only by some
  compounds (a matrix effect is the likely cause; tissue type explains little of
  it, so it is measured per sample).
- **a, b** are one group's two coefficients; ordinary compounds have both near
  zero.

[`rt_drift`](../../xic_extractor/alignment/rt_drift.py) freezes B and M once,
after the general fit and before final grouping:

- **Channels**: a channel is one CID-NL method subject (precursor m/z and
  neutral loss). When a sample has exactly one
  [anchor observation](#anchor-qualification) in that channel, its apex RT is
  that channel's observation in that sample; several leave the sample unknown.
  Whether the peak joined a bootstrap band is not consulted, so compounds that
  drift too far to link before calibration still contribute. Grouping by
  channel measures drift only; it does not anchor the general map.
- **Drift anchors** are channels whose general-calibrated RT keeps a robust
  spread of at least 12 seconds and whose residuals correlate (Spearman ≥ 0.75
  over at least six common samples) with a channel of another elution family.
  Channels whose apices coincide within 0.05 min (median over the samples both
  observe) form one family (isotopes, adducts, co-eluting ions) and vote once.
- **M**: each drift anchor's residual is detrended by its pooled-QC trend (the
  samples' own running median along injection order when fewer than two QCs
  carry it). Anchors whose scatter around their own running trend has a robust
  spread of at least 8 seconds, and that correlate with another family's
  scatter, define M. M(s) is the median over families of each family's median
  offset. A sample without such an offset has unknown M, not zero. The zero of
  M is arbitrary: a constant is absorbed by the group center.
- **Rows vote M**: channels need one peak per sample, so some samples are voted
  by one or two families. In a batch of at least 20 samples, the peaks with
  their own method evidence are therefore linked once with the initial drift
  model, over the full population. A row seen in at least 20 samples is compared with
  its eight nearest injection-order neighbours, which cancels its slow trend.
  It votes when that local offset follows the anchors' M (Theil–Sen response
  of at least 0.3, Spearman ≥ 0.75), and its vote is the offset divided by its
  response. A sample with at least three votes takes their median; the others
  keep the anchors' M. The drift anchors are then fitted against the voted M.
- **B shapes**: each drift anchor freezes its own shape, the running median
  along injection order of its residual after removing its own M response
  (a Theil–Sen slope against the voted M, or against M without its own family
  when no row voted), scaled to a unit
  5–95% range and oriented to rise with injection order. Shapes are not one
  batch-wide curve: early and late RT drift differently.
- Without pooled QC, M is measured against the samples' running trend; when
  samples are injected in tissue blocks, time and tissue then mix.

Final grouping uses them as follows:

- A group overlapping a drift anchor's observations uses that anchor's shape;
  other groups borrow shapes from anchors weighted by a Gaussian of RT distance
  (σ = 1 min). No group fits a free curve during grouping.
- Each group may fit time only, sample shift only, or both, by intercept-bearing
  Theil–Sen (alternating for both). A coefficient is estimated only with at
  least six members having that regressor and a regressor range of at least a
  quarter of the shape range (B) or 10 seconds (M); otherwise it stays zero.
  A seed keeps the fit with the least robust residual spread, preferring
  zero or fewer coefficients on equal spread. Relinking uses that fixed seed
  fit, never one refitted to peaks the relink would add. In final grouping,
  seeds that one joint fit explains merge first: two seeds join when their
  union, with the drift fitted to it removed, links into one band on its own;
  the merged seed is fitted on seed members only. Shift-vote rows and Targeted
  keep one fit per seed, so M does not depend on the merge. A peak with an unknown used
  regressor stays out of that relink; remaining peaks can still link without
  the drift model. Backfill reads the accepted band's coefficients and seed
  shape without refitting them.
- Observed RT, peak boundaries and area are unchanged. Review rows record
  `rt_time_coefficient` and `rt_sample_shift_coefficient`; run metadata records
  `rt_drift_summary` (drift anchors, unknown-M samples, knots per RT segment,
  failed calibrations, groups with nonzero coefficients).

Known limits: a non-anchor drifting compound near the RT boundary between two
shape types can borrow the wrong shape; isomers that occupy different injection
segments can be explained by a segment-aligned M only when the shift within
each segment does not contradict it. A seed's coefficients come only from seed
members, and seeds are taken by direct-peak count after merging: a seed that
holds two lines in some injections cannot merge with the rest of either, and an
earlier seed can still take peaks that a later seed's fit would have joined.
Pieces of one line more than eight injections apart, with no detected peak
between them, stay separate rows.
