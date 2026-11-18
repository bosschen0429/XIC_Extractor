# Retention Time Calibration

Retention Time (RT) calibration places every injection on a common RT coordinate
frame so cross-sample correspondence, Backfill forecasting and review can compare
observations across runs. It models run-to-run retention drift, not compound
identification.

[`rt_anchors`](../../xic_extractor/alignment/rt_anchors.py) owns the anchor
observations both models read,
[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
the knots and the fit, and [`rt_drift`](../../xic_extractor/alignment/rt_drift.py)
the drift model on top of it; downstream consumers access the frozen map
through `SampleRtFrame`. Shared terminology remains owned by the
[Domain Glossary](domain-glossary.md). See [Alignment](alignment.md) for how
grouping consumes this frame.

## Calibration Contract

### Anchor qualification

Bootstrap obtains measured anchors from consistent directional grouping
results; ordinary bootstrap membership is not used directly as the final
answer. Pattern seeds participate in both directions. Extensions that disagree
between directions remain unresolved without downgrading the seed.

Known-ISTD reference acquisition reuses Targeted's authoritative peak selection
and credible-anchor decisions rather than adding a separate rule that makes all
candidates unusable whenever multiple candidates appear. An imported reference
workbook retains its declared measurement contract; the reader does not add MS2
qualification. See the
[Untargeted guide](../development/command-line.md#alignment-cli-inputs) for CLI
input selection.

| Mechanism | Current source and use |
| --- | --- |
| Known-ISTD reference | Exact identity bindings and measured coordinates. Bootstrap comparison uses the shared fitter with ISTDs only; final calibration combines those measurements with ordinary anchors. |
| Sample RT calibration | One frozen map per batch, fitted from its measured ISTDs and ordinary bootstrap correspondences. Final Grouping, Backfill localization/admission, and replay consume this map through `SampleRtFrame`. |
| Batch drift model | Frozen batch state (B), sample shift (M) and drift anchors, measured against the frozen map; see [Drift beyond the map](#drift-beyond-the-map). |
| Peak trajectory | Ordered correspondences and direct/MS1-only positions, used for membership constraints and sample-local localization. |
| Calibration diagnostic | Uses the same fit owner with detected-cell inputs and an interleaved held-out comparison. Its files do not alter production results. |

[`rt_anchors`](../../xic_extractor/alignment/rt_anchors.py) produces the
anchor observations once per initial grouping: an owner with direct method
support, a primary identity event and quantification-eligible integration,
with its method channel, apex RT and, when its bootstrap group is agreed, that
group. Bootstrap takes measured support from the retained assessment binding,
not nominal method-query masses projected into an observation-only owner. The
same observations feed calibration and the [drift model](#drift-beyond-the-map);
neither filters them again.

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
not feed their own results back into that fit. Saved frozen maps keep their
coordinates on replay and are not refitted under this floor.

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
Final membership and localization explicitly use a graded query instead:

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
probabilities or bounds on true error. Weak estimates multiply RT costs by 0.5,
in addition to signal-quality weighting; mass and independent shape costs are
unchanged. Accepted membership risks retain the applied query facts in the
review outputs. Weak localization uses an `estimated_` basis and remains a
forecast, not a physical observation. Neither query changes observed RT, peak
boundaries, area, identity, or Backfill admission rules.

The persisted calibration retains the measured support for these queries. Legacy
v1/v2 coordinates remain exact and bounded, with quality unrecorded; old knots
do not create weak estimates. Bootstrap's strong-only APIs and ISTD-only unique
same-target fallback retain their existing meaning.

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
  feature is searched through the map, with half-width
  `max(0.2 min, 2 × largest leave-one-out ISTD error of the map)`. A feature
  pairs only when the match is unique both ways. More than one candidate in the
  window is `unpaired_ambiguous` and lists the candidates; a reverse match that
  points elsewhere lists that competitor. An unpaired feature keeps its own row.

The multiplier 2 and the 0.2 min floor are fixed before results are read; the
multiplier changes only on leave-one-out ISTD evidence.

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
  anchor counts, coarse ISTDs, leave-one-out errors, half-widths, and anchors
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
  Whether the bootstrap group agreed is not consulted, so compounds that drift
  too far to group before calibration still contribute. Grouping by channel
  measures drift only; it does not anchor the general map.
- **Drift anchors** are channels whose general-calibrated RT keeps a robust
  spread of at least 12 seconds and whose residuals correlate (Spearman ≥ 0.75 over at least six common samples) with a channel
  of another elution family. Channels whose apices coincide within 0.05 min
  (median over the samples both observe) form one family (isotopes, adducts,
  co-eluting ions) and vote once.
- **M**: each drift anchor's residual is detrended by its pooled-QC trend (the
  samples' own running median along injection order when fewer than two QCs
  carry it). Anchors whose scatter around their own running trend has a robust
  spread of at least 8 seconds, and that correlate with another family's
  scatter, define M. M(s) is the median over families of each family's median
  offset. A sample without such an offset has unknown M, not zero. The zero of
  M is arbitrary: a constant is absorbed by the group center.
- **B shapes**: each drift anchor freezes its own shape, the running median
  along injection order of its residual after removing its own M response
  (a Theil–Sen slope against M without its own family), scaled to a unit
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
  Each nonzero coefficient costs what a 10-second RT difference costs a pair.
  A group keeps the cheapest feasible choice, zero included. After the initial
  whole-band grouping, each settled group's coefficients are also a candidate
  for every group it overlaps during per-sample and per-owner reassignment. A
  moved owner is judged by the model of the group it joins or leaves, not by
  how its move perturbs a refit; a refit can otherwise shift a worst pair the
  moved owner is not part of.
- A member with unknown B or M does not enter the fit; it keeps its general
  coordinate with half the RT weight and is not placed in the drift-adjusted
  frame for the population-gap constraint. It does not force the group to
  zero coefficients.
- Once chosen, pair costs, population gap, local trend, mean peer fit and
  contradiction flags all use the group's adjusted coordinate. Backfill
  admission keeps the group's coefficients and B, and a Backfill forecast is
  fitted in the adjusted coordinate with the target's drift added back.
- Pattern correspondence between two calibrated samples also tries each nearby
  drift anchor's own predicted displacement; see [Alignment](alignment.md).
- Observed RT, peak boundaries and area are unchanged. Review rows record
  `rt_time_coefficient` and `rt_sample_shift_coefficient`; run metadata records
  `rt_drift_summary` (drift anchors, unknown-M samples, knots per RT segment,
  failed calibrations, groups with nonzero coefficients).

Known limits: a non-anchor drifting compound near the RT boundary between two
shape types can borrow the wrong shape; isomers that occupy different injection
segments can be explained by a segment-aligned M only when the shift within
each segment does not contradict it.
