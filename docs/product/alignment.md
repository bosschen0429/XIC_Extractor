# Alignment

Alignment uses sample-local peak observations and cross-sample evidence to
decide which observations align into the same Feature, which must stay
separate, and which stay unresolved. This is a cross-sample correspondence
problem, not compound identification: the same method signal, nearby m/z or
RT, and similar local shapes can support comparison, but none alone proves
identity. The code and its tests own the link radius, costs, merge rules and
thresholds; this page keeps the principles, reasons, evidence and limits.

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Band linking across injections | `alignment.peak_band_linking` | [`test_peak_band_linking.py`](../../tests/test_peak_band_linking.py) |
| Grouping runtime, drift seeds and relinking | `alignment.cid_nl_grouping` | [`test_cid_nl_initial_grouping.py`](../../tests/test_cid_nl_initial_grouping.py) |
| Band consolidation | `alignment.band_identity` | [`test_band_identity.py`](../../tests/test_band_identity.py) |
| Shape consistency | `alignment.shape_consistency` | [`test_shape_consistency.py`](../../tests/test_shape_consistency.py) |
| Group identity, disposition and lineage | CrossSample | [`test_cross_sample_identity_lineage.py`](../../tests/test_cross_sample_identity_lineage.py), [`test_cross_sample_association_lineage.py`](../../tests/test_cross_sample_association_lineage.py) |
| RT coordinates | [RT calibration](rt-calibration.md) | |
| Missing-sample revisit | [Backfill](backfill.md#where-each-decision-lives) | |

## Terms

| Term | Meaning in this document |
| --- | --- |
| owner | An established sample-local peak observation carrying a physical peak and local method evidence. It is the object being aligned, not a guarantee that cross-sample correspondence is correct. |
| Feature | A set of cross-sample observations accepted on the basis of evidence. A group in the code carries its members and decision results; it does not denote a separate scientific entity. |
| band | Peak hypotheses linked along injection order, with at most one per injection. Missing RTs can be estimated from its members; an estimate is not another observation. |

A method subject is an acquisition or evaluation question, not a Feature
boundary, and a fixed Target output row is not a cross-sample Feature.

## Boundaries

- A group is identified by its members, at most one per canonical sample;
  distinguishable peaks in one sample cannot share a band.
- Disposition and output inclusion are separate decisions. Observation-only
  members can bridge a band but cannot establish it, and low support alone is
  not rejection.
- `feature_id` is not identity. Neither label, row order, sample stem, display
  coordinate nor area is an identity basis, and none carries identity across
  reports or processing requests.
- Groups never collapse into one product row except through
  [band consolidation](#join-groups-and-consolidate-split-bands).
- Initial members stay fixed during Backfill; only CrossSample can admit a
  missing-sample member and record its lineage.
- Cross-sample membership is not same-peak proof, and a legacy `family` is not
  a same-peak or chemical-identity claim.

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
a cross-sample link is inferred. When evidence rules out a correspondence,
withdraw it and the conclusions that depend on it; otherwise keep the
uncertainty in the decision facts without rejecting the whole class of
evidence. A selected configuration does not erase its reasonable alternatives.
Local maxima, partitioned segments and independently acceptable physical peaks
are not interchangeable as evidence of distinctness.

### Backfill versus correspondence

Predicting a position, finding a peak, admitting it to the Feature and writing
a value are separate questions: a predicted position grants no membership or
method support, and a found peak no writable value
([Backfill](backfill.md)).

## Why It Works This Way

- **Linking in calibrated RT, nearest first.** Injections are counted by
  their position in the batch, so an injection outside the batch does not
  widen a gap, and missing injection order invents no links. Competitions are
  member facts that keep the measured alternate; no displayed ID decides a
  tie.
- **Drift seeds merge first** when one joint fit explains them; relinking
  uses the seed's frozen drift, never a fit refitted to the peaks it would
  add.

### Join groups and consolidate split bands

A separable valley can cut one band into two segments in some injections only,
so one compound becomes two rows; two co-eluting compounds give the same
single-injection shape, and only cross-injection behaviour separates them.
Consolidation therefore merges same-m/z groups only when their members share
one [envelope](signal-processing.md#separable-valleys) in every common sample
and the halves keep a bounded area ratio, and reports the whole envelope's
integration. The factor of two is product policy chosen on the 8RAW split
bands and true pairs of #1283: splits cut in up to seven of eight injections
had ratio ranges of at most 1.9-fold, and every true pair was resolved in some
injection. A pair resolved only by zero-scan dropouts in a weak channel stays
separate, the conservative direction.

### Reject rows without one elution shape

A compound elutes with the same shape in every injection, give or take
intensity; background plateaus, noise texture and tail extensions show a
different slice of signal in each, with signal-to-noise that can equal a true
peak's, so no single-peak measure separates them. Comparing members' scaled
shapes rejects such rows, and also rows whose members are different peaks in
different injections; regrouping them is a separate problem. On the 8RAW
review of #1283 the rule removed 7 of 16 reviewed background rows and 14 other
rows, mostly rows mixing different peaks; background rows whose slices happen
to share one shape stay. The window and floor are product policy, not a
calibrated error rate.

## Evidence

This page describes the source, which is not scientific validation; local
diagnostics support only the data they compared, and tests confirm declared
behavior. Retained fixtures and their interpretation are owned by the
[Diagnostic Ledger](../agent/diagnostic-ledger.md); evidence labels follow the
[Product Validation Contract](../agent/product-validation-contract.md).
