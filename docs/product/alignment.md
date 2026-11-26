# Alignment

Alignment uses sample-local peak observations and cross-sample evidence to decide
which observations can be aligned into the same Feature, which must remain
separate, and which remain unresolved.

This is a cross-sample correspondence problem, not compound identification. The
same method signal, nearby m/z or RT, and similar local peak shapes may support
comparison, but none alone proves identity.

## Terms

| Term | Meaning in this document |
| --- | --- |
| owner | An established sample-local peak observation carrying a physical peak and local method evidence. It is the object being aligned, not a guarantee that cross-sample correspondence is correct. |
| Feature | A set of cross-sample observations accepted on the basis of evidence. A group in the code carries its members and decision results; it does not denote a separate scientific entity. |
| band | Peak hypotheses linked along injection order, with at most one per injection. Missing RTs can be estimated from its members; an estimate is not another observation. |

A method subject denotes an acquisition or evaluation question, not a Feature
boundary. A fixed Target output row is not a cross-sample Feature either. Shared
terminology remains owned by the [Domain Glossary](domain-glossary.md).

## Group identity and disposition

- A group is identified by its members. Each member is one canonical sample
  plus one owner-backed peak hypothesis and the physical observation it binds.
- A group holds at most one member per canonical sample. Distinguishable peaks
  in the same sample cannot share a band. The fixed link order selects among
  admissible alternatives and records the measured competitor.
- Each finalized group has exactly one disposition: `established`,
  `provisional`, `unresolved` or `rejected`. Established requires at least
  two direct CID-NL activation-supporting members from distinct samples.
  Observation-only members can bridge a band but cannot establish it. Low support alone is not rejection.
  Disposition and output inclusion are separate decisions.
- `feature_id` is not identity. Discovery's `feature_id` labels candidates that
  share one MS1 peak within one sample. Alignment's `feature_id` (`F000001`)
  numbers the published groups of one report in raw measured member order, never
  from m/z/RT centers, scores or display order. Neither label, row order, sample
  stem, display coordinate nor area is an identity basis, and neither carries
  identity across reports.
- Several Discovery peak anchors or groups never collapse into one product row
  except through [band consolidation](#join-groups-and-consolidate-split-bands).
- Initial members stay fixed during bounded Backfill. Only CrossSample can admit
  a unique missing-sample SUPPORT observation and record its successor group and
  lineage; a Backfill observation never implies membership. A new processing
  request creates new groups; cross-request continuity is not inferred.
- Cross-sample membership is not same-peak proof, and a legacy `family` is not a
  same-peak or chemical-identity claim. Two resolved peaks in one sample can
  belong to different groups of one family.

## Method principles

### Unconnected and distinct

Distinguish the following evidential meanings when interpreting results; this
does not require a new program state for each:

1. There is sufficient support to accept a correspondence.
2. There is explicit evidence of incompatibility, requiring separation.
3. The observations have not been compared, or the data are insufficient to
   establish a connection.
4. Competing or conflicting correspondences remain unresolved.

Failure to find a difference does not establish sameness; failure to find
sufficient support does not establish distinctness. RT distance alone proves
neither identity nor incompatibility, and being outside the search scope does
not mean a candidate has been ruled out.

### Jointly consistent correspondences

The current observational unit for a Feature is at most one accepted physical
peak observation per sample. Repackaging the same observation adds no support,
and arbitrarily selecting a candidate does not resolve competition.

If A ↔ B, A ↔ C's early peak, and B ↔ C's late peak are accepted, but C's early
and late peaks cannot be treated as the same observation, these correspondences
cannot all hold.

The conflict alone does not identify which cross-sample correspondence is wrong.
A local peak position is directly observed; linking it to a peak in another
sample remains an inference. When evidence is sufficient to rule out a
correspondence, withdraw that correspondence and the conclusions that depend on
it. Otherwise, preserve the uncertainty in the decision facts without rejecting
the entire class of evidence. A selected configuration does not erase its
reasonable alternatives.

Local maxima, partitioned segments, and independently acceptable physical peaks
are not interchangeable. Using them as evidence of distinctness must respect
the observational unit above.

### Backfill versus correspondence

Backfill performs a bounded search in an existing Feature context for samples
without a usable observation.

Predicting a search position, obtaining a physical peak, deciding whether it
belongs to the Feature, and deciding whether to output a value are separate
questions. A predicted position cannot directly grant membership or method
support; finding a peak cannot directly grant a writable quantitative value.

The search scope is separate from the signal context needed for integration.
Acquiring wider context does not allow acceptance of candidates outside the
original search scope. See [Backfill](backfill.md) for search and acceptance
conditions.

## Current implementation

The following describes the current ordinary Untargeted scalar CID-NL path. It
does not declare these algorithms, stages, or thresholds irreplaceable.
Known-identity constraints and composite subjects retain their own processing
paths.

### Inputs and comparison scope

Alignment retains measured peak hypotheses from the CID-NL assessment of each
sample. A member holds the hypothesis itself; its ID and physical observation
are read from it. Compatible, overlapping precursor search bounds in the same
CID-NL method form a workset, not a feature or chemical identity. Independently
known ISTD bindings are grouped separately, subject to sample uniqueness
and the mass span.

### Prepare RT coordinates

Raw-RT bands supply ordinary anchors to the existing batch calibration fitter.
The bootstrap step bound is 0.6 min so it tolerates some whole-injection
shifts. Both batch-state (B) and baseline sample shift (M) read the ordinary
band anchors; direct bands spanning at least 20 injections can then vote on M.
See [Retention Time Calibration](rt-calibration.md) for the fit and voting.

### Decide membership

[Band linking](../../xic_extractor/alignment/peak_band_linking.py) tries links
within eight injections and 0.4 min in calibrated RT, or in measured RT where
the calibration supplies no bounded answer. Shorter RT steps are tried first,
then shorter injection gaps, then measured peak order. A union may not
duplicate an injection or exceed the configured full-band precursor span
(capped at 10 ppm). Missing injection order does not invent links.

A band with at least six members and two direct peaks may seed its own
[drift fit](rt-calibration.md#drift-beyond-the-map). Peaks in the workset
are linked again after removing that seed's frozen drift; only the band
overlapping the seed may be taken on this pass. Peaks with an unknown used
regressor do not participate in that pass. Unclaimed peaks are linked in the
general coordinate; groups with fewer than two direct peaks stay out of the
Matrix. Competitions are member facts and preserve the measured alternate
peak or band; no displayed ID decides a tie.

### Join groups and consolidate split bands

Band consolidation is the one consolidation contract. A separable valley can
cut one chromatographic band into two segments in some injections only, and
each half then groups with its counterparts, so one compound becomes two rows.
The same single-injection shape also arises from two co-eluting compounds;
only cross-injection behaviour separates them. After final grouping and before
Backfill, `band_identity` compares established same-m/z groups in the samples
where both hold a member:

- both members lie in one [envelope](signal-processing.md#separable-valleys) —
  segments joined by shared cut scans — or the groups are not one band;
- members in separate envelopes, in any sample, mean two compounds;
- the area ratio of the two halves, over at least two samples, stays within a
  factor of two.

Groups that pass merge into one `one_band` group with one member per sample (the
earliest apex). Each sample's value is the physical owner's integration of the
whole envelope holding that member, so a half, or a half whose sibling failed
qualification, reports the band. Pairs that fail any condition stay separate rows.
The factor of two is product policy chosen on the 8RAW split bands and true
pairs of #1283: splits cut in up to seven of eight injections had ratio ranges
of at most 1.9-fold, and every true pair was resolved in some injection. A pair
resolved only by zero-scan dropouts in a weak channel stays separate, the
conservative direction.

### Reject rows without one elution shape

A compound elutes with the same shape in every injection, give or take
intensity. Background plateaus, noise texture and tail extensions do not: each
injection shows a different slice of differently shaped signal, and their
signal-to-noise can equal a true peak's, so no single-peak measure separates
them. After band consolidation, `shape_consistency` aligns each member's
canonical signal at its apex (for a one-band group, the apex of its envelope),
over ±0.6 min, scales it to its height, and takes the median pairwise
correlation. An established group below 0.6 is rejected and does not plan
Backfill. The rule also rejects rows whose members are different peaks in
different injections; their values mixed compounds, and regrouping them is a
separate problem. On the 8RAW review of #1283 it removed 7 of 16 reviewed
background rows and 14 other rows, mostly rows mixing different peaks;
background rows whose slices happen to share one shape stay. The window and
floor are product policy, not a calibrated error rate.

### Localization and Backfill

Backfill localizes from the accepted band's measured member apices, including
MS1-only positions without turning them into CID-NL support. It fits up to
twelve same-cohort neighbors in the common coordinate when possible, removing
and restoring the band's frozen drift without refitting it. The bounded
fallbacks and empirical tolerance are described in
[Backfill](backfill.md#query-rt-versus-group-rt).

Backfill never changes an original member or creates a row. It accepts a
missing-sample observation only inside the fixed band's physical and method
limits, resolving competing claims against the same physical peak once.
Output reads the resulting member and cell decisions without regrouping.

## Evidence and related documents

Descriptions of current implementation come from the corresponding source and
are not independent validation of scientific correctness. Local diagnostics
support only the candidates and data scope actually compared; they cannot be
extrapolated into validation of the entire method. Engineering tests confirm
declared behavior and regressions, not scientific correctness directly.
Existing retained fixtures and their interpretation are owned by the
[Diagnostic Ledger](../agent/diagnostic-ledger.md); evidence labels follow the
[Product Validation Contract](../agent/product-validation-contract.md).

This document focuses on cross-sample correspondence; existing documents own
the other topics:

- Retention time calibration: [RT Calibration](rt-calibration.md).
- Bounded searches and observation acceptance: [Backfill](backfill.md).
- Values, rows, and output contracts: [Quant Matrix](quant-matrix.md) and
  [Decision Policy](decision-policy.md).
- Sample/cohort inputs: [Sample Metadata and QC](sample-metadata-qc.md).
- Operation, diagnostics, and review:
  [Untargeted analysis](../user/untargeted.md),
  [Diagnostics Index](../../tools/diagnostics/INDEX.md),
  [Workbench and review](workbench.md).
