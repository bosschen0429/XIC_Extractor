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
| pattern | Measured local maxima within one sample, together with their order, spacing, widths, and relative heights. Maxima may include shoulders and need not each form an owner. |
| trajectory | Observation positions linked by cross-sample correspondences. Missing-sample positions may be estimated from them, but an estimate is neither a new observation nor additional evidence of Feature identity. |

A method subject denotes an acquisition or evaluation question, not a Feature
boundary. A fixed Target output row is not a cross-sample Feature either. Shared
terminology remains owned by the [Domain Glossary](domain-glossary.md).

## Method principles

### Observations and correspondence

Owners provide actual sample-local observations; patterns provide surrounding
structure. Alignment uses these data to:

- propose candidates worth comparing;
- support a cross-sample correspondence;
- rule out correspondences incompatible with observations or accepted constraints.

Proposing a candidate does not accept a correspondence, and support does not mean
that other reasonable explanations have been ruled out. An established group
cannot retroactively create independent evidence that did not exist between its
members.

Patterns may propose candidates outside ordinary RT/mass searches. Their role
therefore need not be limited to adjusting scores for existing edges.

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
it. Otherwise, retain the unresolved state without rejecting the entire class
of evidence.

Local maxima, partitioned segments, and independently acceptable physical peaks
are not interchangeable. Using them as evidence of distinctness must respect
the observational unit above.

### Different uses of the same evidence

The same pattern may participate in candidate generation, membership decisions,
and position estimation, but its use in multiple steps does not provide multiple
independent confirmations.

- RT correction changes comparison coordinates, not measured peak positions or
  integration.
- Method compatibility supports comparison but does not alone establish the same
  Feature.
- Confirmed known-identity bindings are input constraints, not re-derived from
  ordinary pattern similarity.
- An unavailable correction or pattern does not mean zero drift was measured or
  no peak exists.
- Not every ISTD or unknown Feature is assumed to follow the same batch-wide
  monotonic RT drift.

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

Alignment consumes candidates and owner-backed hypotheses from Discovery or
Targeted; it does not create the first sample-local Discovery hit. RAW owner-build
accepts only MS1-feature-backed Discovery candidates; review-only rows do not
thereby receive a second XIC search. See
[Execution Runtime](execution-runtime.md#cid-nl-alignment-execution) for input
and scheduling details.

Non-composite method subjects with the same scalar neutral loss form connected
worksets through resolver intervals and known-reference comparison envelopes.
Different losses, disconnected scopes, and composite subjects retain separate
worksets. A workset defines only the comparison scope: it does not establish
membership or pool the subjects' MS2 evidence.

Confirmed known-ISTD owners are first grouped by exact identity label. Bindings
with the same label remain intact; proximity alone cannot admit unbound owners.
Multiple owners bound within the same sample, or the same owner bound to
different labels, remain unresolved input conflicts rather than being
arbitrarily selected or split.

### Build owners and local patterns

Initial processing retains owners and separately acquires wider MS1 context.
Measured maxima in that context may participate in a pattern even when they
are not independent owners or quantitative results; apices without sufficient
smoothing-kernel acquired support are excluded. The original capture, physical
evaluation, and integration remain unchanged.

Current [pattern matching](../../xic_extractor/alignment/peak_trajectories.py)
preserves peak order, permits missing and extra maxima, and pairs them using a
shared RT shift and peak-width constraints. Relative heights distinguish equally
complete matches; patterns without a unique resolution do not directly provide
correspondences. A single-pair match cannot connect tracks, bind owners, or
supply positions; it lacks a second landmark to establish an ordered pattern.

When both complete compared patterns lie inside their sample calibration spans,
matching uses common-RT apices and transforms peak widths into that same frame. The calibration
already supplies the displacement: matching permits width-bounded residuals, not
a second free shift onto neighboring peaks. Cross-sample context selection and
neighbor pruning/ranking use the same supported common coordinates before the
match; local owner-to-landmark binding remains in measured coordinates.
Initial grouping retains supplied contexts so calibrated regroup can reconsider
candidates excluded by raw-RT pruning.
Diagnostic replay retains full calibration support and arbitrary-landmark RT
mapping, including bounded sample calibration loaded from a file or fitted by
the replay command. An owner-only coordinate callback does not supply those
facts. If any required apex or width endpoint lacks support, the entire
pair retains raw-RT pattern matching; missing support is not measured zero drift.
These comparison coordinates do not replace measured landmarks or owner values.

Different seed windows may produce slightly different apices for the same peak.
Current doublet detection uses the narrower peak's width and the
owner_apex_close_sec lower bound, rather than absolute apex or seed RT distance
alone. Differences smaller than approximately half a peak width are therefore
not treated as two peaks that cause the entire component to be discarded.

### Build trajectories

Accepted pattern matches form tracks and bind eligible owners. Tracks are not
considered distinct merely because they are unconnected. The current
implementation establishes incompatibility between two tracks when distinct
landmarks from both tracks are observed in the same sample, or their measured
elution order connects them through other retained tracks. For example,
`A < B < C` prevents merging A with C even without a sample containing both.
Sharing a reference on the same side (`A < B > C`) does not establish that
A and C differ. Membership and separated-band bridges consume this prepared
compatibility decision; RT proximity or shape agreement cannot override it.

Trajectories are then used as seeds for ordinary membership, for compatibility
decisions, and for Backfill localization. These uses share the same source and
do not independently validate one another.

Membership inference withdraws a track's membership authority when co-observed
direct peaks contradict its common-Feature claim. The witness requires two
nonprovisional scalar CID-NL owners from one sample, with usable method evidence,
matching chemistry, and precursors within the configured comparison tolerance.
Their selected integration segments must be disjoint; owner bounds apply only
when selected integrations are unavailable. Existing method compatibility must
place track members exclusively on opposite sides of that pair.
Product/loss envelope mismatches disambiguate that witness; a mismatch alone
does not revoke a track's membership authority.

Withdrawal removes the track's seeds, same-track RT benefit, and derived
distinct-track exclusions together. Other tracks and the original localization
geometry remain available. Missing, weak, overlapping, or method-ambiguous
witnesses do not authorize withdrawal. RT distance and shape similarity do not
disambiguate the witness. Known-identity constraints retain their separate path.

### Prepare RT coordinates

Bootstrap obtains measured anchors from consistent directional grouping
results; ordinary bootstrap membership is not used directly as the final
answer. Pattern seeds participate in both directions. Extensions that disagree
between directions remain unresolved without downgrading the seed.

Known-ISTD reference acquisition reuses Targeted's authoritative peak selection
and credible-anchor decisions rather than adding a separate rule that makes all
candidates unusable whenever multiple candidates appear. An imported reference
workbook retains its declared measurement contract; the reader does not add MS2
qualification. See the
[Untargeted guide](../user/untargeted-discovery.md#alignment-cli-inputs) for CLI
input selection.

| Mechanism | Current source and use |
| --- | --- |
| Known-ISTD reference | Exact identity bindings and measured coordinates. Bootstrap comparison uses the shared fitter with ISTDs only; final calibration combines those measurements with ordinary anchors. |
| Sample RT calibration | One frozen map fitted from the complete request's measured ISTDs and ordinary bootstrap correspondences. Final Grouping, Backfill localization/admission, and replay consume this map through `SampleRtFrame`. |
| Peak trajectory | Ordered correspondences and direct/MS1-only positions, used for membership constraints and sample-local localization. |
| Calibration diagnostic | Uses the same fit owner with detected-cell inputs and an interleaved held-out comparison. Its files do not alter production results. |

[`sample_rt_calibration`](../../xic_extractor/alignment/sample_rt_calibration.py)
owns anchor preparation and the fit. A correspondence's reference is its
same-cohort median observed RT. Ordinary correspondences require direct identity
support, no identity conflict or provisional status, and quantification-eligible
observations in at least half the declared cohort (rounded up), with at least
three measured members. Bootstrap takes measured support from the retained
assessment binding, not nominal method-query masses projected into an
observation-only owner. These are anchor qualifications, not feature-retention
rules: other observations remain available to grouping. Exact duplicate
sample/RT patterns contribute only once. Measured ISTDs require at least two
same-cohort measurements, but not ordinary-feature prevalence. Missing ISTDs,
predicted coordinates and Backfill observations are not imputed as anchors.
Declared batch IDs define cohorts; empty IDs share an unspecified cohort and
sample role does not define the cohort.

Each sample uses robust local-linear regression of reference-minus-observed RT,
with a 0.3 neighborhood fraction and three residual reweightings. Repeated
observed RT positions receive one median reference vote. A new fit requires at
least eight distinct measured RT positions, and at least eight positions must
retain nonzero robust weight in the final fit. Local neighborhoods include at
least eight positions; they expand when rejected points would otherwise fill
the final neighborhood. Distance weights remain unequal: eight positions are
not a guarantee of precision or dense coverage. A deficient fit is unavailable,
not replaced by a two-point line, constant, or interpolated pseudo-anchors.
Non-monotone results also remain unavailable. With only ISTDs, contradictory
landmark order remains unavailable rather than being resolved by the same
measurements. Ordinary corroboration can support a fit despite a conflicting
standard; fitted standard residuals remain visible and do not reselect that
standard's peak.

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

Eight is a conservative application support floor, not a universal LOESS
theorem. Local support, polynomial degree, bandwidth and sampling density still
matter; see [Cleveland (1979)](https://doi.org/10.1080/01621459.1979.10481038)
and the [NIST LOESS guidance](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm).
It counts RT positions per injection, not the number of samples in the batch.
The fit consumes fixed bootstrap anchors once; final regrouping and Backfill do
not feed their own results back into that fit. Historical frozen maps retain
their coordinates on replay and are not retroactively refitted under this floor.

The frozen map is linearly interpolated only within its supported anchor range,
with a bounded inverse for sample-local Backfill queries. It never changes
observed RT, peak boundaries, area or identity. Final pair scoring uses this
common coordinate when both endpoints are supported, not a second ISTD override
or a choice of whichever raw/corrected score is stronger. Non-RT evidence and
known-identity constraints remain separate. Bootstrap's ISTD-only lookup retains
the unique same-target fallback; it is not the final batch map.

Final grouping rebuilds correspondence in the frozen frame and carries those
trajectories to localization. Unsupported coordinates are not measured zero
drift: a supported apex cannot join an unsupported raw apex through
ordinary correspondence. When both apices are unsupported, existing raw/shape
evidence remains available. A Backfill forecast uses common coordinates only
when its references and target inverse are supported; otherwise it estimates
the entire forecast in raw RT, without mixing coordinate systems. Historical
frozen `BatchRtWarp` payloads remain readable for replay, not as a runtime fitter.

### Decide membership

[Final grouping](../../xic_extractor/alignment/feature_membership_inference.py)
builds candidates within connected mass neighborhoods, compares RT, precursor
mass, and product mass, and handles competition within each sample. Currently:

- Ordinary inference does not force trajectory seeds together once its owners
  supply measured common-coordinate apices. A shared track cannot replace
  either endpoint's measured RT separation with zero. Tracks connected through
  raw-frame pattern matching also lose their derived membership exclusions;
  common-frame pattern correspondences retain physical-order evidence. The
  caller's original trajectories and localization links remain unchanged.
  Without measured apices, ordinary inference retains raw-pattern seeds;
  directional bootstrap keeps its existing seed authority.
- Pairwise precursor tolerance limits candidates; product deviation is a soft
  cost, observed loss is not a separate veto, and a cumulative min-max envelope
  does not independently veto membership.
- Ordinary edge union priority uses a fixed RT scale: a larger injection gap
  does not discount RT separation and move that edge ahead of closer support.
  Per-sample ambiguity retains its gap-dependent uncertainty allowance; that
  allowance does not grant union priority. Both costs retain precursor and
  product evidence. Candidate eligibility and sparse/distant comparisons keep
  their existing allowances.
- Once both sides are established bands with common-coordinate support, union
  priority uses their median measured RT and mass summaries, refreshed after
  each merge, rather than one connecting pair's old cost. They merge only as
  reciprocal best alternatives. A closer alternative remains in that comparison
  when its sample occupancy prevents a union; that conflict does not make a
  worse band the correct match. Singleton growth and bands without measured
  common-coordinate support retain the local pair priority.
- Supported common-coordinate apices and measured peak edges are the primary
  physical correspondence check. Each side of a proposed union contributes a
  consensus region: the median of its members' supported apices and measured
  edges. Only completely disjoint consensus intervals exclude correspondence;
  overlapping or touching intervals do not require either apex to lie inside
  the other interval. When only one side has calibration support, both
  sides are compared the same way in the observed RT frame; missing calibration
  support is not evidence of a different peak.
  A peak edge outside the calibration span remains unknown: it is neither
  extrapolated nor clipped into a new measured boundary. It cannot establish
  disjointness on that side or switch supported apices to a raw-shape veto.
  Every union compares the complete groups' consensus intervals, not just the
  connecting pair. Overlap removes this veto; it does not itself establish
  membership. This is a correspondence constraint, not proof of chemical
  identity; same-sample
  competition and physical order still apply.
- When neither side has calibration support, measured peak
  shape supplies the fallback evidence. The owner adapter
  retains an apex-relative, own-max Gaussian-smoothed shape from the selected
  raw MS1 subtrace, without changing its baseline, bounds, apex or area. Comparison
  translates RT but does not stretch width. Only in this private derivative,
  an observed nonpositive gap separates the selected apex's positive signal
  island from neighboring signal; continuous background is not subtracted.
  This does not reselect the peak or change the measured observation.
  A median local Pearson similarity across the two sides' member pairs below
  `min_peak_shape_similarity` (default 0.80) prevents ordinary membership in
  that fallback; a single dissimilar pair does not. Pairs distinguished by
  physical peak order are not shape evidence. The fallback cannot veto
  supported common-coordinate regions.
  At least five acquired points per trace and half of the shared +/-0.35-minute
  grid are required. Fine-shape comparison also requires five measured points
  at or above half height in each retained primary signal; zeros, smoothing and
  interpolation do not supply that resolution. With fewer points, only the
  coarse contrast between a localized signal (both observed edges below half
  height) and a sustained signal (both edges at or above half height) can supply
  a shape veto, still using the same Pearson threshold. Insufficient context,
  unresolved primary shape, missing legacy resolution metadata and flat traces
  remain unassessed, not positive evidence of identity.
  Shape agreement alone does not establish membership. A measured contradiction
  applies to the entire proposed union, including trajectory seeds, singleton
  admission and separated-band bridges; intermediate members cannot hide it.
  Directional assignment and Backfill admission consume the same region/shape rule.
  Simultaneous Backfill candidates are also compared with each other after
  claim resolution. Both endpoints of a shape conflict remain unassigned;
  independent compatible additions can still join, without an ordering winner.
  Explicit known-identity constraints remain separate from inferred membership.
- Dense bands use their own trend over six neighboring injections in the shared
  RT frame; sparse bands use broader calibrated comparisons. This feature-local
  correspondence estimate is not another batch map or Backfill localization.
- A band cannot contain two owners from the same sample.
- Near-equivalent competitors remain unresolved; other independent support does
  not disappear because of one contested pair.
- A missing pair record means unevaluated, not incompatible, and group membership
  does not manufacture positive pair evidence.
- Final ordinary membership does not depend on input sample order. Singletons
  and unresolved competition remain review-only; an assigned band is not
  chemical identification.

Bootstrap seed support uses height/baseline-residual MAD from the owner's
selected integration and the existing MS2 seed count. Paths without baseline
residuals retain the Discovery-score fallback. The current
anchor_min_seed_events in CID_NL_ALIGNMENT_CONFIG is 1: an MS1-only physical
observation counts as one event rather than using the generic default of 2.
This does not mean all final-window decisions apply the same seed-rating gates.
MS1-only Target owners separately use precursor, physical quality, and RT
evidence; missing MS2 alone does not exclude them, nor does it admit them to a
CID evidence group.

### Join groups and handle shoulders

Ordinary grouping has an internal band bridge. With the prepared warp and full
injection order, bands separated by unobserved injections may connect based on
mass, corrected RT trend, sample disjointness, and mutual unique-best decisions.
An injection gap alone is not evidence of distinct Features.

Initial membership is decided only by `infer_feature_memberships`, including
local grouping, singleton assignment, and this internal bridge. The workflow
consumes that result without a second merge. With owners, pattern evidence and
RT frame fixed, Backfill search windows do not change initial membership;
widening acquisition may change recovery, not the initial Feature boundary.
Known-identity and bootstrap/composite
directional rules remain within inference.

Shoulder rejection is a separate action. Current conditions require both groups
to be established, more than half of the smaller group's members to share
samples with the other group, and the member in each shared sample to have a
smaller area, compatible preferred m/z, and the same peak boundaries. A rejected
group does not plan Backfill, and its members are not moved into the other group.
This is the current split-off shoulder treatment, not a general correspondence
merge rule.

### Localization and Backfill

Trajectories use observed positions for localization. Missing positions are
estimated from up to twelve neighboring observations in the same cohort, using
available batch coordinates and a robust local RT fit. This includes compatible
MS1-only positions without creating CID-NL support.

Insufficient temporal support, bounded extrapolation, and localization
reliability each have explicit handling; detailed conditions are owned by
[Backfill](backfill.md#query-rt-versus-group-rt). Direct owner positions always
remain authoritative. A same-sample borrowed position is contradicted only when
the direct owner is bound to a landmark within its selected peak segment and
the borrowed landmark lies outside that segment. Before fitting, localization
withdraws that position and its dependent branch along accepted pattern links.
Withdrawal stops at direct anchors and preserves positions supported by an
independent direct anchor that does not itself endorse a contradicted position.
Unrelated positions remain available; a conflict does not discard the entire
trajectory. These localization decisions do not change initial membership or
the owners' selected peaks and integrations.

Backfill returns observations or reasons for no result. Existing members are not
reassigned by this search; membership and cell decisions still determine
whether new observations are acceptable. Output consumes the completed results
without repeating grouping, peak selection, or integration.

## Open questions

The following questions still need clarification between the method description
and current implementation. They are not settled new algorithms or additional
execution rules.

| Question | Known behavior | Still to clarify |
| --- | --- | --- |
| Pattern acceptance conditions | Eligible seeds connect directly; sharing a track also affects cost and ambiguity handling. | What evidence is sufficient to accept a correspondence? Do later steps consume an accepted result or repeat the decision? |
| Granularity of landmarks and physical peaks | Distinct landmarks may establish distinct-track constraints; landmarks may also include shoulders. | When is this sufficient to rule out the same Feature, and when is it only structure within one peak or a segmentation difference? |
| Overgeneralized decision reasons | Assigned window groups are uniformly labeled as supported by RT trend; unions with fewer than 10 owners may pass the trend check directly. | How should explanations distinguish actually measured support, a pattern seed, and merely not being ruled out by a check? |

These questions do not predetermine that pattern or RT/mass always takes
priority, nor do they require a single graph, solver, or aggregate score. Their
common concern is whether specific correspondences and their dependencies can
hold together, not the names or number of mechanisms.

## Evidence and related documents

Descriptions of current implementation come from the corresponding source and
are not independent validation of scientific correctness. Local diagnostics
support only the candidates and data scope actually compared; they cannot be
extrapolated into validation of the entire method. Engineering tests confirm
declared behavior and regressions, not scientific correctness directly.
Existing retained fixtures and their interpretation are owned by the
[Diagnostic Ledger](../diagnostic-ledger.md); evidence labels follow the
[Product Validation Contract](../agent/product-validation-contract.md).

This document focuses on cross-sample correspondence; existing documents own
the other topics:

- Group identity, disposition, and lineage:
  [Group Boundary](family-hypothesis-boundary.md).
- Bounded searches and observation acceptance: [Backfill](backfill.md).
- Workflow and result interfaces:
  [Shared Processing Kernel](shared-processing-kernel.md).
- Values, rows, and output contracts: [Quant Matrix](quant-matrix.md).
- Scheduling, caches, and failure handling:
  [Execution Runtime](execution-runtime.md).
- Sample/cohort inputs: [Sample Metadata and QC](sample-metadata-qc.md).
- Operation, diagnostics, and review:
  [Untargeted Discovery](../user/untargeted-discovery.md),
  [Diagnostics Index](../../tools/diagnostics/INDEX.md),
  [XIC Inspector](xic-inspector.md).
