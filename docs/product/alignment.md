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
correspondences. See [Open questions](#open-questions) for the actual effects of
single-peak continuation.

Different seed windows may produce slightly different apices for the same peak.
Current doublet detection uses the narrower peak's width and the
owner_apex_close_sec lower bound, rather than absolute apex or seed RT distance
alone. Differences smaller than approximately half a peak width are therefore
not treated as two peaks that cause the entire component to be discarded.

### Build trajectories

Accepted pattern matches form tracks and bind eligible owners. Tracks are not
considered distinct merely because they are unconnected. The current
implementation establishes incompatibility between two tracks when distinct
landmarks from both tracks are observed in the same sample.

Trajectories are then used as seeds for ordinary membership, for compatibility
decisions, and for Backfill localization. These uses share the same source and
do not independently validate one another.

### Prepare RT coordinates

Bootstrap obtains batch-warp anchors from consistent directional grouping
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
| Known-ISTD drift lookup | Exact bindings and supported reference ranges, used for bootstrap comparison without changing observed RT. |
| BatchRtWarp | Bootstrap groups from the complete request; ordinary anchors require at least 20 same-cohort members, and a sample requires at least 12 anchor observations. Used for final grouping and Backfill coordinate conversion. |
| Peak trajectory | Ordered correspondences and direct/MS1-only positions, used for membership constraints and sample-local localization. |
| Sample RT calibration diagnostic | Features present in nearly all samples, used only for opt-in grouping experiments, not as the production warp. |

A known-ISTD map requires at least two landmarks with strictly increasing
observed and reference RT. Missing landmarks may be interpolated within the
canonical injection order, without extrapolating order or RT; duplicate orders
disable order interpolation. When the general map is unavailable, the existing
unique same-target supported-owner correction may still be used. See
[drift evidence](../../xic_extractor/alignment/drift_evidence.py) for the
calculation details.

Directional edge scoring evaluates raw RT first. Strong raw edges normally skip
correction unless both ends have direct, unique same-target bindings; correction
also does not repair weak non-RT evidence. Other eligible pairs use the prepared
map. Scoring falls back to raw-only decisions if correction worsens the
difference beyond the existing 10-second threshold or moves a pair from inside
the raw preferred window to outside it. Existing Targeted-ISTD product/loss
exceptions remain. This is the current
[edge scoring](../../xic_extractor/alignment/edge_scoring.py) policy, not a rule
shared by every grouping stage.

After batch warp is prepared, final grouping reconsiders ordinary owners.
Samples without a usable correction retain observed RT, so comparisons may mix
corrected and observed coordinates; this does not mean zero drift was measured.
Trajectories built from raw context are currently reused by subsequent grouping.
Residual neighbors do not cross declared batches; empty batch IDs share an
unspecified cohort, and sample role does not define the calibration cohort.
Non-monotone sample mappings provide no correction.

### Decide membership

[Final grouping](../../xic_extractor/alignment/feature_membership_inference.py)
builds candidates within connected mass neighborhoods, compares RT, precursor
mass, and product mass, and handles competition within each sample. Currently:

- Eligible trajectory seeds connect directly before ordinary edges are processed.
- Sharing a track affects RT cost and some ambiguity handling.
- Pairwise precursor tolerance limits candidates; product deviation is a soft
  cost, observed loss is not a separate veto, and a cumulative min-max envelope
  does not independently veto membership.
- Dense bands use their own trend over six neighboring injections; sparse bands
  use broader warp-only RT comparisons. This is not another Backfill localization.
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

The subsequent
[sample-disjoint merge](../../xic_extractor/alignment/grouping_workflow.py)
proposes candidates using preferred m/z tolerance, raw group-center RT, and the
Backfill half-window; it combines them by distance while checking sample
overlap, trajectory compatibility, and the union RT trend. Known-identity groups
retain their own membership. Its candidate and competition policies differ
from the internal bridge and cannot be summarized as "merge because no sample
distinguishes them."

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
[Backfill](backfill.md#query-rt-versus-group-rt). A direct owner currently
overrides a borrowed position in the same sample. This does not mean the
conflict has been resolved; see the next section.

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
| Indirect effects of single-peak continuation | It does not directly emit its owner/position, but still participates in root contraction and may change other supported tracks. | What evidence supports this effect? How is it consistent with the boundary that it cannot independently establish a pattern? |
| Two group-merging policies | The internal bridge and later merge use different candidate, ordering, and competition rules. | When does the later merge cover unevaluated candidates, and when does it reconsider existing competition? How can it avoid treating unconnected groups as directly mergeable? |
| Conflict between a borrowed position and a direct owner | Localization lets the direct owner override the borrowed position in the same sample, while other borrowed positions may remain. | If they represent different peaks, which correspondences still hold? Does overwriting only RT hide an unresolved conflict? |
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
