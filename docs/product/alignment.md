# Alignment

Alignment uses sample-local peak observations and cross-sample evidence to
decide which observations align into the same Feature, which must stay
separate, and which stay unresolved. This is a cross-sample correspondence
problem, not compound identification: the same method signal, nearby m/z or
RT, and similar local shapes can support comparison, but none alone proves
identity.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| What is a group, and when is it established? | Its members, one peak per sample; established with two direct CID-NL members. | [Group identity](#group-identity-and-disposition) |
| Is "no difference found" sameness? | No; unsupported is not distinct either. | [Method principles](#method-principles) |
| How are peaks linked? | Within eight injections and 0.4 min calibrated RT, nearest first. | [Decide membership](#decide-membership) |
| When do two groups become one row? | When they share one envelope and their area ratio stays within twofold. | [Consolidate split bands](#join-groups-and-consolidate-split-bands) |
| When is a row rejected for shape? | Median member shape correlation below 0.6. | [One elution shape](#reject-rows-without-one-elution-shape) |
| Can Backfill add a member? | Only through CrossSample, inside the fixed band. | [Localization and Backfill](#localization-and-backfill) |

## Terms

| Term | Meaning in this document |
| --- | --- |
| owner | An established sample-local peak observation carrying a physical peak and local method evidence. It is the object being aligned, not a guarantee that cross-sample correspondence is correct. |
| Feature | A set of cross-sample observations accepted on the basis of evidence. A group in the code carries its members and decision results; it does not denote a separate scientific entity. |
| band | Peak hypotheses linked along injection order, with at most one per injection. Missing RTs can be estimated from its members; an estimate is not another observation. |

A method subject is an acquisition or evaluation question, not a Feature
boundary, and a fixed Target output row is not a cross-sample Feature.

## Group identity and disposition

- A group is identified by its members. Each member is one canonical sample
  plus one owner-backed peak hypothesis and the physical observation it binds.
- A group holds at most one member per canonical sample. Distinguishable peaks
  in the same sample cannot share a band. The fixed link order selects among
  admissible alternatives and records the measured competitor.
- Each finalized group has exactly one disposition: `established`,
  `provisional`, `unresolved` or `rejected`. Established requires at least two
  direct CID-NL activation-supporting members from distinct samples.
  Observation-only members can bridge a band but cannot establish it. Low
  support alone is not rejection. Disposition and output inclusion are
  separate decisions.
- `feature_id` is not identity. Discovery's `feature_id` labels candidates that
  share one MS1 peak within one sample. Alignment's `feature_id` (`F000001`)
  numbers the published groups of one report in raw measured member order,
  never from m/z/RT centers, scores or display order. Neither label, row order,
  sample stem, display coordinate nor area is an identity basis, and neither
  carries identity across reports.
- Several Discovery peak anchors or groups never collapse into one product row
  except through [band consolidation](#join-groups-and-consolidate-split-bands).
- Initial members stay fixed during bounded Backfill. Only CrossSample can admit
  a unique missing-sample SUPPORT observation and record its successor group and
  lineage; a Backfill observation never implies membership. A new processing
  request creates new groups; cross-request continuity is not inferred.
- Cross-sample membership is not same-peak proof, and a legacy `family` is not
  a same-peak or chemical-identity claim. Two resolved peaks in one sample can
  belong to different groups of one family.

## Method principles

### Unconnected and distinct

Interpretation distinguishes four evidential meanings, without a program state
for each: (1) enough support to accept a correspondence; (2) explicit evidence
of incompatibility, requiring separation; (3) not compared, or too little data
to connect; (4) competing or conflicting correspondences, unresolved.

Failure to find a difference does not establish sameness; failure to find
support does not establish distinctness. RT distance alone proves neither
identity nor incompatibility, and lying outside the search scope does not rule
a candidate out.

### Jointly consistent correspondences

A Feature's observational unit is at most one accepted physical peak per
sample. Repackaging the same observation adds no support, and arbitrarily
picking a candidate does not resolve competition. If A ↔ B, A ↔ C's early
peak, and B ↔ C's late peak are accepted, but C's early and late peaks cannot
be one observation, these correspondences cannot all hold.

The conflict does not say which link is wrong: a local peak is observed,
a cross-sample link is inferred. When
evidence rules out a correspondence, withdraw it and the conclusions that
depend on it; otherwise keep the uncertainty in the decision facts without
rejecting the whole class of evidence. A selected configuration does not erase
its reasonable alternatives. Local maxima, partitioned segments and
independently acceptable physical peaks are not interchangeable as evidence of
distinctness.

### Backfill versus correspondence

Backfill searches, within a Feature, samples without a usable observation.
Predicting a position, finding a peak, admitting it to the Feature and
writing a value are separate questions: a predicted position grants no
membership or method support, and a found peak no writable value. Wider signal
context for integration does not admit candidates outside the search scope
([Backfill](backfill.md)).

## Current implementation

This is the ordinary Untargeted scalar CID-NL path; its algorithms, stages and
thresholds are replaceable. Known-identity constraints and composite subjects
keep their own paths.

### Inputs and comparison scope

Alignment keeps the measured peak hypotheses from each sample's CID-NL
assessment; a member holds the hypothesis itself, and its ID and physical
observation are read from it. Compatible, overlapping precursor search bounds
in the same CID-NL method form a workset, not a feature or chemical identity.
Independently known ISTD bindings are grouped separately, subject to sample
uniqueness and the mass span.

### Prepare RT coordinates

Raw-RT bands supply ordinary anchors to the batch calibration fitter. The
bootstrap step bound is 0.6 min so it tolerates some whole-injection shifts.
Batch state (B) and the baseline sample shift (M) read each method channel's
unique direct peaks, banded or not; direct bands spanning at least 20
injections can then vote on M. See
[Retention Time Calibration](rt-calibration.md) for the fit and voting.

### Decide membership

Band linking tries links within eight injections and 0.4 min in calibrated RT,
or in measured RT where the calibration supplies no bounded answer.

- Injections are counted by their position in the batch, so an injection
  outside the batch does not widen a gap. Missing injection order invents no
  links.
- A link's distance is its RT step plus 0.005 min per injection apart; shorter
  distances are tried first, then shorter injection gaps, then measured peak
  order. A union cannot duplicate an injection or exceed the configured
  full-band precursor span (capped at 10 ppm).
- An under-equilibrated injection's peaks before 8.5 min first move to where
  they would elute without its
  [early-segment shift](rt-calibration.md#early-segment-of-an-under-equilibrated-injection).
- A band with at least six members and two direct peaks can seed its own
  [drift fit](rt-calibration.md#drift-beyond-the-map). In final grouping, seeds
  that one joint fit explains merge first: two seeds join when, after removing
  the drift fitted to their union, the union alone links into one band.
- Workset peaks are linked again after removing that seed's frozen drift; only
  the band overlapping the seed can be taken on this pass, and peaks with an
  unknown used regressor do not take part. Unclaimed peaks are linked in the
  general coordinate.
- Groups with fewer than two direct peaks stay out of the Matrix. Competitions
  are member facts that keep the measured alternate peak or band; no displayed
  ID decides a tie.

### Join groups and consolidate split bands

Band consolidation is the one consolidation contract. A separable valley can
cut one band into two segments in some injections only, so one compound
becomes two rows; two co-eluting compounds give the same single-injection
shape, and only cross-injection behaviour separates them. After final grouping and before
Backfill, established same-m/z groups are compared in the samples where both
hold a member:

- both members lie in one [envelope](signal-processing.md#separable-valleys) —
  segments joined by shared cut scans — or the groups are not one band;
- members in separate envelopes, in any sample, mean two compounds;
- the area ratio of the two halves, over at least two samples, stays within a
  factor of two.

Groups that pass merge into one `one_band` group with one member per sample
(the earliest apex). Each sample's value is the physical owner's integration of
the whole envelope holding that member, so a half, or a half whose sibling
failed qualification, reports the band. Pairs failing any condition stay
separate rows. The factor of two is product policy chosen on the 8RAW split
bands and true pairs of #1283: splits cut in up to seven of eight injections
had ratio ranges of at most 1.9-fold, and every true pair was resolved in some
injection. A pair resolved only by zero-scan dropouts in a weak channel stays
separate, the conservative direction.

### Reject rows without one elution shape

A compound elutes with the same shape in every injection, give or take
intensity; background plateaus, noise texture and tail extensions show a
different slice of signal in each, with signal-to-noise that can equal a true
peak's. After band consolidation, each member's canonical signal is aligned at
its apex (for a one-band group, the apex of its envelope), over ±0.6 min,
scaled to its height, and the median pairwise correlation is taken. An
established group below 0.6 is rejected and plans no Backfill. The rule also
rejects rows whose members are different peaks in different injections; their
values mixed compounds, and regrouping them is a separate problem. On the 8RAW
review of #1283 it removed 7 of 16 reviewed background rows and 14 other rows,
mostly rows mixing different peaks; background rows whose slices happen to
share one shape stay. The window and floor are product policy, not a
calibrated error rate.

### Localization and Backfill

Backfill localizes from the accepted band's measured member apices, including
MS1-only positions without turning them into CID-NL support. It fits up to
twelve same-cohort neighbors in the common coordinate when possible, removing
and restoring the band's frozen drift without refitting it
([Backfill](backfill.md#where-each-decision-lives) names the owners).
It creates no row and accepts a missing-sample observation only inside the
fixed band's physical and method limits, resolving competing claims on one
physical peak once; output reads the resulting decisions without regrouping.

## Evidence and related documents

This page describes the source, which is not scientific validation; local
diagnostics support only the data they compared, and tests confirm declared
behavior. Retained fixtures and their interpretation are owned by the
[Diagnostic Ledger](../agent/diagnostic-ledger.md); evidence labels follow the
[Product Validation Contract](../agent/product-validation-contract.md).

Other topics: [RT Calibration](rt-calibration.md) ·
[Backfill](backfill.md) · [Quant Matrix](quant-matrix.md) ·
[Decision Policy](decision-policy.md) ·
[Sample Metadata and QC](sample-metadata-qc.md) ·
[Untargeted analysis](../user/untargeted.md) ·
[Diagnostics Index](../../tools/diagnostics/INDEX.md) ·
[Workbench and review](workbench.md)

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Band linking | [`peak_band_linking`](../../xic_extractor/alignment/peak_band_linking.py) |
| Band consolidation | `band_identity` |
| Shape consistency | `shape_consistency` |
