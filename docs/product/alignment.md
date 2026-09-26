# Alignment

The target Alignment contract is the cross-sample scientific workflow. It
consumes explicit sample-local hypotheses/observations, closes canonical group
identity and trajectory/Backfill evidence, then returns
`FinalizedScientificAlignment` to the workflow projection's binding/subject
operation. The immutable workflow result exists only after cell decision and
projection completion. Alignment does not use a mutable Matrix as its internal
transport contract or let writers recompute science.

## Execution Boundary

The bounded production entry creates initial jobs only for sample/subject pairs
with Discovery candidates. One shared template per sample retains the complete
sample population and acquisition settings. Backfill creates a missing pair's
job only after a direct-SUPPORT group meets the population support threshold.
Unsearched pairs receive their method assessment during final cell composition,
without a worker dispatch or a runtime sample-result artifact. The direct batch
interface retains its declared, complete job manifest.

Acquisition backfill planning uses direct-SUPPORT group membership and the
eligible sample population to apply the conservative support threshold. It
does not construct a dense group-by-sample CellObservation population merely
to decide which missing samples to search. Cell evidence and quantitation
remain responsibilities of the later composition stage.
Process backfill requests contain only subjects with scheduled searches;
responses use work-unit keys to update those subjects while retaining the
other subjects' initial results.

The bounded Untargeted runtime keeps exact source-bound XIC and MS2 acquisition
facts in the output directory's private `.cid_nl_acquisition_cache`. A retry
uses a new processing attempt and recomputes scientific decisions; matching
queries reuse acquisition facts without repeating the vendor query. Source or
query changes require acquisition for the affected entries. Sample batches with
an input-owner-verified source check its current path identity before replay;
they open a native RAW handle only when a missing query or metadata needs it.
Runtime resource receipts report whether that native open actually occurred.
Without a verified source expectation, the existing native-open source check
remains in force. This cache does
not preserve completed grouping graphs or turn an old failed attempt into a
successful result. The Alignment entry point archives a finalized failed or
cancelled runtime marker before launching a new attempt in the same directory.

Targeted and Untargeted CID-NL close through the shared scientific workflow.
`alignment.pipeline.run_alignment` adapts pure CID-NL inputs to that workflow
and renders its finalized result. It requires explicit pure CID-NL discovery
settings; the Alignment CLI supplies them through a bound preset. Unsupported
discovery settings are rejected before acquisition or output creation.

The Alignment CLI resolves ISTD input before RAW acquisition. An explicit
targets CSV or reference workbook takes precedence; otherwise it uses the same
`config/targets.example.csv` template as the GUI's fresh default and prints the
resolved source and ISTD count. Missing or invalid input fails before RAW work.
An intentional uncorrected CLI run requires `--allow-uncorrected`, which cannot
be combined with either ISTD input. The Python API retains its explicit optional
input contract.

With ISTD input, the existing target extractor obtains label-bound measurements
and the formal drift owner prepares its batch map before initial edge/group
decisions. The finalized explanation saves the same prepared sample knots,
domains, measurements and source provenance. Downstream review reuses these
results; it does not refit the model. Insufficient anchors and positions outside
the bounded map remain unassessable. A saved group built without the intended
ISTD input must be rebuilt by the alignment owner, rather than being validated
by a later display transformation.

Initial owner edges use a selective drift trigger before querying that map.
`alignment.edge_scoring` first evaluates raw RT with the existing identity,
owner-quality and seed-support rules. A strong raw edge skips correction unless
the reference owner has a direct, unique same-target binding for both endpoints;
that measured pairing remains usable against a closer unbound competing peak.
Weak seed support or weak/ambiguous owner quality cannot be repaired by RT, so
those pairs also skip correction. Other non-blocked pairs query the prepared
map once. No new RAW acquisition, model fit or grouping pass is introduced.

After a query, correction that worsens the pair by more than the existing
10-second contradiction threshold, or moves raw RT from inside to outside the
preferred window, falls back to the raw-only score and decision. Fallback does
not keep a prior bonus, promote a weak edge to strong, or bypass the applicable
hard gates. The existing targeted-ISTD product/loss hard-gate exceptions
retain their RT query. Group ranking reuses the cached edge's chosen coordinate.

Edge evidence and its audit projections record `rt_basis` as `raw` or
`drift_corrected`. `drift_prior_source` identifies the supplied lookup, not proof
that correction was applied. Unqueried or unavailable corrected RT is absent;
the reason distinguishes raw sufficiency, insufficient non-RT evidence,
unavailability and post-query fallback. Historical explanation records remain
readable as recorded; they are not reclassified by this field addition.

The grouping workflow consumes candidate and exact owner evidence, applies the
constrained group rules, and returns one immutable
`CrossSampleGroupingResult(processing_owners, partition_transition,
drift_lookup)`. Confirmed known-ISTD identity constraints remain indivisible.
The Target workflow may apply its existing initial ModeTrack refinement before
this result closes. The retained Untargeted CID-NL route carries its initial
groups directly into the same result without model fitting or assignment.
Initial candidates retain their request, sample, acquisition, physical-
hypothesis, integration, and evidence bindings.

Initial members stay in their original groups through bounded backfill.
A group-bound backfill with unique SUPPORT may add a missing sample member
when it passes the existing feature evidence rule. The grouping owner records
the admitted member and exact predecessor/successor lineage; it does not refit,
move original members, split known ISTD bindings, or create new groups.
Competing claims on the same physical content remain unresolved and supply no
numeric value. Final cells retain the original cell for provenance and the
completed backfill assessment/observation for the final decision and integral.
Cell evidence uses `cell_observation` and final-cell canonical bases use
`final_group_cell_observation` to bind unresolved SUPPORT and backfill
provenance explicitly. Old diagnostic evidence remains historical; acquisition
cache facts do not depend on these cell schemas.

The production CID-NL runtime prepares that result after initial assessments,
once per compatible subject workset, using the complete sample population and
the supplied known-ISTD context. Backfill eligibility counts distinct supporting
samples across the frozen group, including compatible subjects. It does not
regroup each method subject separately. Missing sample jobs are created only
after this eligibility decision; dense cells are assembled during final
composition. Backfill attempts bind the group ID to their sample and method
subject in the runtime result. RT bounds describe the acquisition query rather
than identify the destination group. The direct production API therefore takes
its alignment configuration and known-ISTD reference at execution time;
composition cannot replace that grouping context.
The acquisition boundary canonicalizes repeated or unordered subject/RT
queries. Different groups may reuse the same physical trace while retaining
separate group-bound backfill attempts and decisions.

Initial grouping uses the sparse feature index described below. It scores only
compatible feature candidates and retains the evaluated evidence. Missing pair
records mean `not_evaluated`; they do not prove incompatibility. Backfill checks
its observation against the fixed destination group before admission.
After local ordinary bands settle, a prepared batch warp and complete injection
order allow bands separated by unobserved injections to join when their
corrected RT trends and mass centres support one band, each is the other's
unique best bridge, and their samples do not overlap. An injection gap alone is
not evidence of different group identity.

Before scoring, a feature candidate must have a seed or most-recent witness
that can support the preferred RT window in raw or corrected coordinates.
Unavailable correction remains eligible; a raw-compatible pair also remains
eligible when correction would be contradictory. Index axes are updated only
when their stored extrema or composition key change. Runtime progress records
the inference operation counts, including RT pruning, actual edge evaluations,
index updates, and assignment solver calls.
Single-owner and single-feature competitions use their analytical optimum and
next-best cost, including the private unassigned option. Equal or near-equal
optima retain ambiguity. A stable ID selects the diagnostic representative of
an exact tie; it does not authorize membership. General multi-owner,
multi-feature components retain the joint assignment solver.

Writer projection checks sample completeness per finalized row without building
a second global row-by-sample key set. Cell and integration-audit TSV writers
consume formatted rows incrementally instead of retaining a second complete
population of cell dictionaries.

The initial ModeTrack model identity binds its assignment-scope bounds, exact
group identities, eligible anchors, explicit noneligible evidence, and policy.
Physical, candidate, integration, and group identity schemas remain unchanged.
The stable grouping result and revisit receipts bind the final cell observation
and its production decision without a second trajectory or group identity.
Explanation summaries publish `initial_edge_coverage`
with this versioned policy, so an empty retained edge set is not missing audit
evidence. Inspector forwards that coverage with item details. Older explanation
files remain readable with `legacy-unspecified` coverage and are not reclassified.

Matrix and provisional review artifacts are projections of the finalized
owner result. They do not run a separate Matrix-based scientific lifecycle.
Delta Mass / Pt pattern retains its Discovery candidate, evidence, and review
outputs under the Discovery contract; preserving that capability does not
authorize a separate downstream decision pipeline.

## Frozen Owner Contract

| Owner | Sole responsibility | Output to the next seam |
| --- | --- | --- |
| CrossSample grouping workflow | Constrained initial grouping, membership, group identity, disposition, lineage, and application of an accepted complete-partition transition | One immutable `CrossSampleGroupingResult` |
| ModeTrack | Optional request-bound initial trajectory modeling over immutable evidence where the workflow uses it; proposes only | Typed assessable initial refinement proposal |
| Backfill | One bounded local revisit under an established group | Observation or typed no-result decision |
| Scientific alignment closure | Validate request/group/disposition/membership/refinement/revisit closure | `FinalizedScientificAlignment` |
| Workflow projection: binding/subject operation | Target fixed-row binding or Untarget group-row identity, followed by explicit Target/Group cell-subject construction | `TargetRowBinding` + `TargetCellSubject`, or `UntargetRowKey` + `GroupCellSubject` |
| ProductionCellDecision | Selected value or typed no-write result for one explicit Target/Group subject | Immutable cell decision with evidence refs |
| Workflow projection: completion operation | Consume cell decisions to close row inclusion, counted-detection/count/reason fields, Review Queue refs, and archive routing | Closed typed projection decisions |
| Result finalizer | Closure validation over the core, completed projection, and cell decisions | Discriminated immutable Targeted or Untargeted result |

The grouping workflow alone may mint or supersede a group and apply the
accepted `CrossSamplePartitionTransition`. ModeTrack cannot move a member or
write a value, and Backfill cannot turn a revisited observation into implicit
membership. The finalizer cannot rerun RAW access, grouping, ModeTrack,
Backfill, integration, or selection.

The two workflow-projection rows above are ordered operations of the same
Target or Untarget projection owner, not two owners. Binding and cell-subject
construction must close before `ProductionCellDecision`; inclusion, counts,
reasons, Review Queue/archive routing, and result finalization close afterward.

### Group identity and disposition

- `group_hypothesis_id` is a typed digest over the group identity schema, exact
  `processing_request_id`, and canonical-sorted identity-bearing members. Each
  member combines canonical sample identity with a discriminated member-subject
  union: an owner-backed `PeakHypothesis` identity or an owner-backed physical-
  observation identity. The canonical variant tag is part of identity. The same
  physical observation reached through different wrappers must canonicalize to
  the same member subject, or remain deliberately distinct under different
  explicit variant tags.
- Ordinal `FAM...`, row order, sample stem, m/z/RT display coordinates, area,
  and ModeTrack anchor IDs are compatibility/display facts, not identity bases.
- A group contains at most one identity-bearing member per canonical sample.
  Same-sample distinguishable peaks remain separate; unresolved ambiguity does
  not choose an arbitrary winner.
- Any accepted initial membership change or split/merge appears in the one
  complete before/after `CrossSamplePartitionTransition`, mints the resulting
  group identity and immutable lineage, and closes before bounded Backfill. A
  new processing request creates new group identity; this contract does not
  infer cross-request continuity.
- `CrossSampleGroupDisposition` is exactly one of `established`, `provisional`,
  `unresolved`, or `rejected`. Established requires accepted identity-bearing
  members from at least two distinct canonical samples plus method hard gates;
  a singleton is always provisional. Low support alone is not rejection.
  Disposition and output inclusion are separate.
- A split-off shoulder is `rejected`. Discovery can cut one chromatographic
  peak at a valley into a small shoulder and the peak; the shoulders then form
  their own group, whose Backfill finds the whole peak where no shoulder was
  cut. An established Untargeted CID-NL group is a shoulder of another
  established group when more than half of its members share a sample with that
  group and, in every shared sample, its member is smaller in area, within the
  preferred m/z tolerance, and shares a peak boundary with that group's member.
  The majority is the one shoulder threshold in Alignment; it is owned here and
  was set from the d3-5-hmdC split (#469). A rejected group becomes an archive
  row and plans no Backfill; its members are not moved into the other group.
- Rows of one feature that no sample tells apart merge. Two peaks are only
  evidenced by one sample holding both, not by an RT or m/z distance. After the
  final regroup, established Untargeted CID-NL groups within the preferred m/z
  tolerance whose centers lie within one Backfill half window
  (`nl_rt_anchor_half_window_min`) and that share no sample merge, closest
  first; a merge never joins groups that share a sample. Such rows would
  otherwise Backfill each other's peak (#469). Known-identity groups keep
  their own membership.

### ModeTrack and Backfill relationship

- ModeTrack consumes one immutable `ModeTrackAssignmentScope` containing only
  the actual m/z/RT admissibility bounds and exact existing groups, plus the
  request-bound anchors and policy used by its model. The scope has no separate
  domain identity or persistence contract.
- Model selection uses the simplest validated candidate. RT correction is an
  optional prior; direct local evidence wins and a prior alone cannot reassign
  a member.
- Validation must not assume that every ISTD or unknown feature follows one
  batch-wide monotonic RT drift. Sensitivity work therefore permits direction
  changes and adjacent-injection jumps, uses method-appropriate ISTDs plus
  several high-detection unknown features, and reports where the model is
  unassessable.
- A bounded revisit does not change group or trajectory identity and
  does not request another ModeTrack pass. No revisit loop, refit loop,
  arbitrary neighborhood chunking, or singleton-based cross-sample fill is
  allowed.

### Final result and projections

`FinalizedScientificAlignment` closes the request, canonical groups,
dispositions, membership/lineage, and refinement/revisit receipts. Before cell
decision, Targeted adds fixed `TargetKey` rows, typed `TargetRowBinding`, and
`TargetCellSubject`; Untargeted adds projection-owned
`UntargetRowKey` and `GroupCellSubject`. After cell decision, the same projection
owner closes inclusion, counts/reasons, Target Review Queue references, or
Untarget archive routing. Only then may the result finalizer return the matching
immutable workflow result.

Targeted retains a typed empty fixed row when no group qualifies. Qualified
groups with disjoint sample membership bind the same fixed `TargetKey` row;
only groups competing for the same sample keep that row unresolved. Untargeted
standard Matrix includes established groups only.
Provisional groups belong in a compact machine archive/review surface;
unresolved and rejected groups remain AuditTrail facts.

## Contract

- Alignment inputs must be explicit: accepted target input, discovery batch
  index, or documented local validation input. Do not rely on old worktree
  output paths as durable inputs.
- Alignment consumes discovered or accepted feature candidates and resolves
  cross-sample owner/group hypotheses based on evidence. It does not create the
  first sample-local Discovery hit and does not treat a single method match as
  cross-sample identity proof by itself.
- The RAW owner-build route consumes only Discovery's MS1-feature-backed
  candidate states. Non-feature rows, including `review_only_orphan_nl`, persist
  in Discovery candidate/review artifacts only with a `scan_precursor` or `mixed`
  basis and do not receive a second RAW XIC
  search merely by entering Alignment. This keeps candidate recall available
  for review without turning an unavailable physical feature into owner-build,
  group, Matrix, or Backfill work.
- Persisted-input routing selects MS1-feature-backed candidates before minting
  method subjects. Review-only observations cannot choose feature query centers.
  Direct/mixed precursor NL observations may supply local scan evidence for an
  existing feature through `load_review_support`; their physical facts are not
  persisted. Physical-fact replay uses only feature-backed records. Non-feature
  `product_plus_neutral_loss` rows are absent from Discovery artifacts; inferred
  feature-backed rows and their physical evidence remain available.
- Alignment retains compact cohort identities globally and restores candidate
  rows and physical records per closed working set. Scheduling packs worksets
  by 100 Da ownership and a 1,024-candidate budget; an indivisible scientific
  workset may exceed that budget or cross a mass boundary. Mass partitions are
  scheduling units, not scientific exclusion boundaries. Final children retain
  canonical subject order independently of execution order.
- Retained CID-NL capture preserves each candidate's extraction m/z and ppm
  when combining overlapping RT contexts. Preparation sharing may extend the
  RT interval; it does not replace the candidate center with a method-subject
  center. Only identical resulting requests share an acquired trace.
- A CID-NL method subject identifies an acquisition/assessment question, not a
  feature boundary. Non-composite Untargeted subjects with the same scalar
  neutral loss form alignment worksets using resolver intervals together with
  the known-reference comparison envelopes described below. Execution keeps
  each connected workset atomic, and its formal workflow child
  carries all original assessment bindings through grouping and final cells.
  Sharing a workset permits comparison; the existing CrossSample gates still
  decide membership. Different losses, scopes that remain disconnected after
  those envelopes are applied, and composite subjects remain separate worksets.
  SearchScope preserves request-bound provider and candidate membership; it
  does not re-partition an upstream workset using resolver intervals alone.
- A cross-subject group retains each SUPPORT cell's exact assessment and
  hypothesis binding. Its canonical backfill subject is the supporting subject
  nearest the formal group center, with method-subject ID breaking ties.
  Missing/non-support cells and backfill jobs use that exact backfill subject;
  support from another subject is admissible only through the group's exact
  owner-backed support binding. Matrix rows consume these completed groups and
  cells; writers do not merge subjects or invent cross-subject values.
- Two resolved peaks in one sample count as an unresolved doublet only when
  their apex separation is chromatographically meaningful against the narrower
  of them, not on an absolute bound alone. One MS1 peak spans many scans and its
  neutral loss can be seeded from several, and each seed opens its own
  resolution window, so the same peak returns with apexes a few seconds apart
  while the seeds sit tens of seconds apart. Below roughly half a peak width the
  trace does not show two apexes, so the pair is not a doublet and its component
  is not discarded. `owner_apex_close_sec` still applies as the lower bound.
- Initial cross-sample membership is owned by
  `feature_membership_inference.infer_feature_memberships`. Confirmed identity
  is the scientific constraint; complete-link, pair-graph representation, and
  historical membership are replaceable implementation choices.
- Exact `KnownIstdOwnerLookup` bindings are partitioned by identity label before
  ordinary inference. Every bound owner for one label stays in one group,
  regardless of RT spread or pairwise product/loss disagreement. Unbound owners
  cannot enter that known group. Conflicting bindings (multiple owners in one
  sample or one owner bound to multiple labels) are reported as unresolved
  input conflicts rather than split or silently selected. Reference mass
  envelopes connect scalar comparison worksets before dispatch. Proximity determines
  comparison scope only, never the exact known-identity binding. Each subject
  retains its own assessment; sharing a workset does not combine MS2 evidence.
- Untarget ordinary grouping has two finite stages. The retained directional
  inference supplies only agreed groups with at least 20 members, together
  with exact known-identity groups, as RT warp anchors. Their ordinary
  membership is discarded. Across the batch, each injection's anchor offsets
  from the median of six nearby injections are summarized at RT knots. This
  warp covers elution times outside the ISTD span; ISTD groups are local anchors
  rather than a flat extrapolation rule.
- Every ordinary owner is then reconsidered in its connected mass
  neighbourhood. Candidate edges still require pairwise precursor tolerance;
  an unrelated lower-mass owner cannot place a boundary through a valid band.
  Local RT and measured mass edges are ordered by cost across the whole
  window, and a band never contains two owners from one sample. Dense bands
  compare against their own six-neighbour injection trend after the batch warp;
  sparse bands use a wider warp-only RT comparison. Precursor and measured
  product deviations contribute
  to cost against band medians during owner-to-band assignment. Observed loss
  contributes neither a veto nor an independent score. Mass neighbourhoods bound
  candidate lookup; accumulated min-max envelopes do not reject membership.
  Remaining competitors use a joint per-sample assignment with a private
  unmatched choice. Near-equal alternatives leave the affected owner undecided
  without archiving an otherwise supported band.
- The final ordinary decision is independent of input sample order. A singleton
  or unresolved competition remains review-only; an assigned band is not a
  chemical proof against an unresolved isomer. Repeated wrappers do not add
  support. Directional grouping supplies bootstrap anchors and handles
  composition cases under their separate cross-tag contract.
- Initial membership is carried as compressed group member sets. Missing pair
  records mean `not_evaluated`, never `excluded`; an initial group does not
  manufacture positive pair evidence. Backfill consumes the closed
  final result and preserves indivisible confirmed identities. Explicit revisit
  pair evidence remains bounded to the actual candidate and current group.
  Physical integrations, observed coordinates, sample attribution, and the
  quantification owner are unchanged by membership inference.
- MS1-only Target owners use the precursor index and the existing physical
  signal-quality and RT evidence. Absence of an MS2 event does not itself
  exclude them. Missing or weak physical quality remains undecided; MS1-only
  owners do not enter a CID evidence group.
- The final `CrossSampleGroupingResult` is constructed after the bootstrap
  anchors have served the warp estimate, from the processing owners, complete
  partition transition, and prepared drift lookup. Internal consumers use that
  same immutable object before bounded Backfill and through
  finalization. Consumers still check the actual revisit/source binding, and a
  caller cannot inject a replacement partition or trajectory after Backfill.
- Batch finalization consumes completed immutable children and checks their
  parent request, attempt, sample coverage, and cross-child uniqueness. It does
  not rerun each child's scientific and post-cell completion checks. Explicit
  finalization and private-spool publication retain their own boundary checks.
- Cross-sample edge seed support is rated on the peak Alignment resolved, not
  on Discovery's `evidence_score`. Every owner exists because ownership
  re-extracted the XIC and found a peak, so the Discovery score -- which is
  dominated by whether Discovery's own MS1 search succeeded -- judges an owner
  by a search its construction already superseded. The rating uses
  `height_raw / baseline_residual_mad` from the owner's `selected_integration`,
  at the detection-limit and quantitation-limit conventions: both peaks at or
  above the quantitation limit with the anchor MS2 seed count is `strong`, both
  at or above the detection limit is `moderate`, anything below is `weak`. The
  MS2 seed count is kept because Alignment does not re-derive it. An owner
  whose integration recorded no baseline residual keeps the Discovery-score
  rating, so paths that do not measure a residual are unchanged. This changes
  the `seed_support_level` column in the edge evidence artifact.
- Owner quantification is eligible only when the selected integration apex is
  strictly inside its selected left/right bounds. An endpoint apex remains an
  owner and remains visible in diagnostics, but its Matrix cell is
  `unchecked` with no area or selected integration, and it does not count as a
  detected owner for the Backfill sample-quorum gate. A Backfill observation may
  replace that censored owner only when its scalar and selected-integration
  boundaries are both interior and its primary Matrix area is valid; an
  endpoint or incomplete observation is censored by the same rule. This is a
  quantification-authority guard, not a new peak-selection or Backfill route.
- Retained CID-NL Alignment can optionally use
  `--known-istd-reference-workbook` for batch-local RT alignment. The report's
  `Targets` sheet declares the ISTDs and its `XIC Results` sheet supplies their
  measured per-sample RTs. Each target uses its median within the canonical
  batch as the reference coordinate. The existing reference owner prepares one
  sample-specific piecewise linear map from observed RT to reference RT before
  the method loops; unchanged input reuses that map. At least two strictly
  increasing observed/reference landmarks are required. Missing landmarks may
  be interpolated between measured canonical injection orders; neither order
  nor RT is extrapolated. Duplicate order disables order interpolation.
  Ordinary analyte pairs compare their mapped coordinates when both have
  bounded support. This accommodates different drift at different RTs and does
  not impose one monotonic trend along injection order. Without a usable map,
  the existing unique same-target supported-owner correction remains available;
  otherwise the ordinary uncorrected comparison applies. A map is matching
  evidence, not a chemical-identity override. The complete binding context is
  retained through cell validation. The public explanation SQLite summary
  records the input fingerprint, target snapshot, and policy, including at the
  production output level. Mass and method evidence gates, current RAW
  integration values, and cell decisions remain authoritative; the map changes
  no observed RT or integrated value, and report areas are not inputs.
  Omitting the reference keeps the ordinary route. Exact owner bindings also
  supply the indivisible identity constraints above.
- `validation-minimal` is the standard fast public validation profile unless a
  stronger gate is explicitly requested.
- Alignment may write matrix, review, identity, evidence, and conditional
  provenance sidecars. Sidecars support review but do not become product
  authority without the authority manifest and expected-diff contract.
- Legacy `feature_family_id` / `public_family_id` preserve public row labels
  and compatibility traceability. `group_hypothesis_id` carries successor
  cross-sample identity when available; product projection decides whether that
  identity may write or count.
- Current public matrix, production-decision, and matrix-identity TSV paths may
  still key records by the stable row/display label for compatibility. Treat
  that as an adapter state, not canonical identity proof; promoting
  `group_hypothesis_id` to the public projection key requires an explicit
  expected-diff contract and schema tests.
- Successor/group identity, gap-fill semantics, and workbook metadata
  versioning are public behavior when they affect alignment outputs and must
  stay repo-readable.
- `alignment_review.tsv` tokens are review context only. Positive support must
  come from provenance-valid sidecars or explicit authority records, not from
  display tokens alone.
- Row-level diagnostic risk tokens such as `row_flags=high_backfill_dependency`
  are guardrail pressure. Diagnostics may count them before legacy `warning`
  compatibility fields, but they do not promote rows into production authority.
- Output filenames, row ordering, column schema, schema-version signals, and
  identity/provenance sidecars are public contracts.
- Retained Backfill composes completed per-sample evidence before final Matrix
  and sidecar publication. Completion order cannot change Matrix or sidecar
  ordering; a failed run does not publish a partial result.
  Process workers project live peak-fact observations through the existing
  provenance owner before IPC and send only typed physical rows, their bounded
  selected-integration projection, and feature/sample order binding. The parent
  revalidates those bindings; raw trace and prepared-signal graphs do not cross
  this internal delivery boundary.
  This is an internal retention mechanism, not a public configuration value,
  persisted shard, alternate Backfill route, or second Matrix authority.
- Runner scripts orchestrate inputs, profiles, and output locations. Reusable
  grouping, ownership, identity, writer, and sidecar behavior belongs in
  package modules or explicit specs.

## Current Compatibility Decision Set Across Activated Outputs

For one current Alignment run, `run_alignment` builds one owner-minted
`ProductionDecisionSet` after final matrix authority is available and supplies
that exact typed result to `write_outputs_atomic`. Activated TSV, XLSX, and HTML
renderers require the supplied set and consume its values, statuses, counts,
and inclusion decisions. They may format and serialize those decisions; they
must not rebuild them from raw `AlignedCell` values or create a renderer-local
fallback owner.

The existing multi-file temp-write, replace, fingerprint, and rollback
transaction remains the publication seam. This live in-process handoff does not
invent run/attempt identity, a durable decision carrier, persisted owner
rehydration, cross-version replay, or a second Output owner. Those capabilities
remain deferred until a stable consumer can state an exact schema and
compatibility need. Diagnostic evidence sidecars may preserve their own raw
evidence population, but they do not become a competing production decision
set.

This is current compatibility behavior. The successor
`ProductionCellDecision` deepens the decision owner around explicit Target and
Group subjects, and finalized result variants replace loose Matrix-plus-metadata
transport. The migration must first preserve this decision set's selected
values, statuses, counts, inclusion, and renderer bytes; behavior changes use a
separate expected-diff promotion.

## Shadow Identity Settlement

Issue #163 adds a no-write comparison layer after the existing cross-sample
group owner. It consumes immutable CellObservationRef reference-spine cells,
Backfill decision facts,
physical peak observations, and preregistered local-trajectory evidence. It
does not replace group construction, Backfill, Matrix identity, or any writer.

The minimum model set is:

- `H1a`: one identity represented by the single-component/RT-trajectory model;
- `H1b`: one identity with distinct chromatographic components;
- `H2`: two identities supported by direct identity evidence;
- `H3`: artifact or no reliable feature;
- `scientific_uncertainty`: a valid run-terminal result when the evidence does
  not resolve one of the four models.

Local feature landmarks retain sample, resolved acquisition source, injection
order, observed RT, local trajectory RT, quality, and evidence reference.
Each physical evidence reference may identify exactly one retained landmark in
the lookup; duplicate references fail closed before trajectory assessment so
one observation cannot become multiple interpolation points.
Missing or poor landmarks are
unassessable, not conflict. Conflict requires an explicit reference to a
retained accepted landmark; an absent landmark cannot be relabelled as
conflict. Landmarks for one sample must agree on its injection order. The
comparison retains the complete landmark point set and does not collapse it
into a median template.
The existing targeted/batch ISTD trend remains a separate diagnostic prior and
cannot be passed to the identity-trajectory assessor as universal identity
truth for unknown features. No universal drift, width, valley, or identity
threshold is introduced.

Direct identity relations are owner-bound to one exact group, and the local
trajectory sample set must equal that group's owner population. A derived
identity or split decision cannot be caller-constructed. Each component must
carry explicit owner-bound membership evidence for its component ID and exact
observations. The identity-settlement owner seals those components into one immutable partition
receipt. H1b settlement, split lineage, and same-identity aggregation must all
reuse that exact receipt; a settlement cannot be replayed against a stale,
partial, expanded, or repartitioned component set.

The current Backfill backfill receipt does not prove that caller-supplied group facts
are the complete same-target parent universe (tracked as #156 gap 10). The
identity-settlement owner therefore accepts only exact direct SUPPORT observations from its parent group;
any non-empty backfill candidate universe fails closed before component
membership. A receipt's declared parent count, explicit identity-settlement
membership label, or Backfill mapping result cannot substitute for the missing
complete-group owner. This
is a deliberate shadow-capability narrowing: backfill candidate membership
remains scientifically uncertain until that upstream contract exists.

Repeated components disfavor the one-component model but do not prove `H2`.
Components retain their physical observations and direct SUPPORT cells without
cross-component transfer. CID-NL OR and area
summation are available only after an `H1b` same-identity resolution; the result
remains shadow-only and preserves the component observations used in the sum.

Split lifecycle semantics are explicit:

- `split_watch` retains component evidence without blocking the parent;
- `split_candidate` preserves every parent direct SUPPORT cell and blocks
  parent Backfill/numeric projection;
- `split_supported` requires resolved `H1b` or `H2`, supersedes the parent,
  creates explicit child lineage, and recomputes each child's `S` from its own
  direct SUPPORT cells. Child censuses are owner-minted and internally
  consistent; parent threshold status is never inherited.

The implementation lives in `alignment/identity_settlement.py` and the local
landmark owner in `alignment/drift_evidence.py`. Both advertise
`shadow_ready / product_authority=none`; neither imports Matrix decisions,
public writers, output packages, or activation code. Therefore this work does
not change a productization maturity tier, active lane, public output schema,
selected area/counting behavior, or Matrix authority, and the productization
control plane remains unchanged.

## ModeTrack Initial Refinement And Diagnostic Review

ModeTrack is an optional initial refinement and review surface over groups that
already exist. The Target workflow uses its established initial refinement;
the retained Untargeted CID-NL route carries initial groups directly. ModeTrack
is not a fallback grouping route, a post-repair refit, or a product control
plane. `ModeTrackAssignmentScope` carries only the actual m/z/RT admissibility
bounds and exact groups from producer to fit. It has no standalone hash,
serialization, shadow authority, or public Family identity. Each existing group
is one ModeTrack; neither the assignment scope nor a ModeTrack is a chemical-
identity claim.

This contract is not a reason to build another trajectory module. The
proposal-only `alignment/mode_track/` interface binds each proposal to the
processing request/attempt, model set, exact groups, anchors, and admissibility
bounds, then returns a closed accepted/rejected/insufficient/unassessable
result. The grouping workflow alone may apply an accepted proposal as a
`CrossSamplePartitionTransition` between complete partitions before
`CrossSampleGroupingResult` closes. ModeTrack does not retain population state.
Existing diagnostic writers and validation packages remain outside that
scientific interface; no product writer activates it.

The initial model set is fit once per assignment scope from canonical direct-
SUPPORT assessment × `PeakHypothesis` anchors. Scope groups must have disjoint
owner IDs, and one `PeakHypothesis` or physical peak observation may seed at
most one track. Fit accepts initial anchors only. Within the initial refinement,
a uniquely assigned direct-SUPPORT observation may mint an immutable successor
model set; ObservationRevisit and downstream consumers do not refit. Each track follows its own injection-order-local, piecewise-linear
trajectory. At fit time, the ModeTrack model/assignment owner projects each existing group's canonical
center RT into the model as `ordering_reference_rt_min`. This immutable behavior
snapshot is bound into `model_set_id`, and crossing checks read the snapshot
rather than a retained rich group object. Equal model identities therefore
cannot hide different elution-order behavior, while `group_hypothesis_id`
remains the existing ModeTrack identity.

Accepted feature landmarks remain a complete local point set: a
track uses its exact local landmark or interpolation between bracketing local
landmarks, never one Family-wide median, and landmark extrapolation outside the
retained local RT range is technically unassessable. The versioned lane policy
supplies the prediction half-width and bounded
interpolation/extrapolation limits; the owner introduces no universal drift,
valley, width, shoulder, or intensity threshold. An exact injection anchor is a
complete local prediction even when no landmark is available. Other sparse or
landmark-unassessable prediction branches fail closed.

When every track is outside the policy's bounded interpolation/extrapolation
coverage, the sample is `out_of_model`. When at least one track is predictable
but another competitor is unavailable, that competitor cannot be excluded and
the sample is instead `technical_unassessable`.

For one sample, resolved hypotheses may occupy the first track, the second
track, both through different hypotheses, or neither. A candidate that is
already an exact owner of one scoped ModeTrack is observed occupancy of that
existing identity-trajectory identity; ModeTrack preserves that one-to-one owner fact instead of
reclassifying it from the track's aggregate center or predicted interval. This
does not make an MS1-only owner a trajectory anchor or a SUPPORT vote. Other
candidates are `uniquely_assigned` only when exactly one prediction interval
contains them and all competitors are excluded. The other terminal states are
`ambiguous_multiple_tracks`, `out_of_model`, `not_observed`, and
`technical_unassessable`. Prediction-interval overlap is candidate-local: it
does not block a candidate that lies in a single-track-only region. A track
crossing is also candidate-local: only candidates whose sole match is one of
the crossed tracks become `technical_unassessable`; an unaffected third track
remains assignable. A candidate matching multiple intervals remains
`ambiguous_multiple_tracks` before crossing adjudication. A track
accepts at most one hypothesis per sample, and occupancy includes its exact identity-trajectory
owner, its incumbent anchor for that injection, and provisional candidates in
the current batch. Replaying an incumbent hypothesis is the same occupant; a
different hypothesis cannot become a second occupant. Only a uniquely assigned
direct SUPPORT observation may reinforce that one track; MS1-only, Backfill,
shadow, inferred, deconvolved, manual, and shared-set observations remain
non-training.
Model sets, anchors, candidates, assignments, and retained landmarks are bound
to their exact processing request and attempt, resolved acquisition source, and
injection order. Anchors and candidates also retain the exact physical peak
observation identity. Every ingress revalidates the live PeakHypothesis and,
when present, direct-SUPPORT assessment against those current owners. ModeTrack does
not retain or depend on `CellObservationRef`. Downstream ModeTrack consumers must call
`validate_mode_track_assignment_batch_for_consumption` before using a retained
assignment batch; the operation is pure and grants no reinforcement or product
authority. Reinforcement enters through the same validation closure.

The retained fixture
`docs/superpowers/fixtures/mode_track_assignment_85raw_inconclusive.json`
records the source-qualified ModeTrack stop condition; it is not a replay fixture or a
classification oracle. It preserves the producing code/base SHAs, required
Discovery/sample/trace hashes, 85-sample census, and historical 85-open/85-call
timing claim. The required owner-bound Discovery index and original seed bytes
were unavailable, while the retained historical packet had different hashes
and no observed completion counters. Reconstruction therefore did not launch,
made zero RAW opens and zero chromatogram calls, and did not consume the
authorized attempt. Its evidence verdict remains `inconclusive / no-write`;
historical morphology, intensity, or mode labels cannot seed tracks or grant a
current-checkout compatibility claim.

The later fresh current-request saved85/canary receipt
`docs/superpowers/fixtures/mode_track_assignment_85raw_gate_b.json` closes
that source gap only for the CID-NL 116.0474, precursor 349.215 diagnostic
slice. It is partial engineering evidence, not a scientific full85 verdict or
performance baseline. The recorded run retained 86 physical hypotheses in 11 ModeTracks, with
9 multi-owner tracks, exact owner-to-assignment closure, no same-sample double
occupancy, and no producer-added RAW calls. In `TumorBC2297_DNA`, the 12.412
minute direct-SUPPORT peak and 13.035 minute non-training physical observation
remain exact owners of two different cross-sample tracks.

The lane must use the existing `CID_NL_ALIGNMENT_CONFIG` value
`anchor_min_seed_events=1`: an MS1-only physical observation contributes one
observation event. A rejected control using the generic default value `2`
degenerated to one singleton track per physical hypothesis, so exact-owner
assignment alone is not a sufficient single-sample canary oracle. Acceptance also requires
observed cross-sample multi-owner aggregation. This receipt remains partial
`shadow_ready / product_authority=none`; four tracks have no direct-SUPPORT
anchor, and the evidence grants no Matrix, Backfill, chemical-identity,
ProductWriter, full85 readiness, or performance claim. Scientific full85
evidence remains `inconclusive`.

The bounded closed-writer validation packet at
`docs/superpowers/validation/issue180_m9_gate_c_closed_writer/README.md`
retains the current closed-writer evidence. Its C2 finalizer accepts only the
fresh typed ModeTrack projection plus one in-memory owner-validated review set,
derives its checked manifest and expected diff internally, and publishes the
QMV and terminal receipts atomically. The earlier
`docs/superpowers/validation/issue180_m9_gate_c_fresh_owner/README.md` packet
retains the historical RAW-backed acquisition, fresh-owner comparison,
manual-review, and 26-cell QMV evidence, but its broader persisted input surface
is `diagnostic_only`. Neither packet is a replayable writer input or activates
defaults, changes the CellObservationRef reference spine, establishes chemical identity, or grants a new writer
authority.

`ModeTrackAssignmentScope`, the model set, and `ModeTrackAssignment` remain
internal structures and grant no product authority. The scope is a plain
immutable handoff value rather than an artifact or authority record. Opt-in
runner output is `diagnostic_only` and does not register a productization lane. The runner may
write task-local review tables, but those tables do not replace canonical
Alignment, Backfill,
QuantMatrixVersion, Matrix, workbook, GUI, configuration, or default outputs.
Retain this diagnostic only while an operator or validation gate consumes its
crossing, ambiguity, or typed-lineage review. If those consumers disappear,
remove the entry point and workflow, retaining only primitives reused by the
Alignment owner.

## Retained Validation Anchors

- The 2026-05-28 targeted GT alignment audit fixtures for `5-medC` are
  diagnostic anchors for the 8RAW validation-minimal and primary-delivery-fix
  alignment slices. Both recorded `PASS 8/8`, zero `SPLIT`, zero `DRIFT`, zero
  `DUPLICATE`, and zero `MISS` against the targeted GT RT rows.
- Those fixture reports prove the audited alignment slice did not lose the
  `5-medC` targeted rows, but they do not grant ProductWriter authority,
  broaden matrix publication, or replace stronger 85RAW/product gates.
- Historical `FAM*` labels in those reports are compatibility traceability for
  the audited run. They are not canonical cross-sample identity proof when a
  successor group or PeakHypothesis surface exists.

## Surfaces

The default `production` Matrix artifact is `alignment_matrix.tsv`. Retained
CID-NL runs also emit `cid_nl_explanation.sqlite` after successful Matrix
publication, alongside their runtime marker.
Identity, review, provenance, backfill evidence, HTML, and workbook artifacts
remain explicit machine, validation, diagnostic, or compatibility outputs;
their schemas and value policies are unchanged. TSV-only `production` and
`validation-minimal` publication consumes finalized rows in one pass, retaining
the identity census rather than the complete cell and decision populations.
Public row numbering and identity hashes span that complete ordered stream.
Workbook, HTML and additional whole-matrix consumers retain the materialized
projection API. Both routes use the same atomic publication and rollback owner.

| Output | Role |
| --- | --- |
| `alignment_matrix.tsv` | Cross-sample aligned values |
| `cid_nl_explanation.sqlite` | Read-only CID-NL candidate explanations from retained owner decisions |
| `alignment_review.tsv` | Human/machine review summary |
| `alignment_matrix_identity.tsv` | Identity and owner traceability |
| `alignment_backfill_cell_evidence.tsv` | Backfill-related cell evidence sidecar |
| `alignment_run_metadata.json` | Conditional run settings and provenance sidecar |
| `alignment-results/v3/v4` metadata | Schema-version signals for public behavior changes; unanchored accepts the legacy or canonical Matrix value policy, historical paired targeted-ISTD requires the canonical policy, and requires that same paired binding and canonical policy |

### CID-NL execution explanations

Normal retained CID-NL execution preserves effective method assessments and the
initial CrossSample groups and classified edges already used by composition.
The completed batch carries these existing owner values for diagnostics; they
grant no additional completion, identity, eligibility or Matrix authority.

The diagnostic projection joins exact candidate, canonical sample, hypothesis,
physical observation and member identities. It records configured subjects,
assessment states/reasons, exclusions, initial edge scores/reasons, group
dispositions and final row/cell decisions. Multiple group relationships remain
explicit. It does not infer membership from m/z or RT or recalculate a score.

The SQLite artifact stores compact, indexed candidate records and deduplicated
details. A summary does not load every candidate's details; a candidate lookup
does not load unrelated records. RAW traces, spectra and the live runtime object
graph are not copied. The read-only interface is:

```text
python -m tools.diagnostics.explain_cid_nl_run --report <output>/cid_nl_explanation.sqlite
python -m tools.diagnostics.explain_cid_nl_run --report <output>/cid_nl_explanation.sqlite --candidate-id <id> --json
```

The query needs only the retained diagnostic artifact, without RAW or Thermo DLL
access. Missing reports mean explanation unavailable, including runs interrupted
before publication. Missing decision evidence is not converted into rejection or
zero; an unknown candidate and an unsupported/corrupt report are explicit errors.
The artifact records request/attempt provenance, configuration and the published
Matrix hash/count. Its `diagnostic_only` content cannot authorize a scientific
writer or prove readiness.

The owned output collision guard reserves this artifact and its temporary/backup
names before acquisition. Matrix publication still uses its existing atomic
writer. Explanation publication follows successful Matrix writing; if explanation
IO fails, the command fails and the Matrix may remain, without a complete report.
This diagnostic is outside the Matrix writer's artifact-fingerprint receipt.

## Boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| CrossSample | Grouping, membership, identity, disposition, proposal application, and split/merge lineage | ModeTrack fitting, local ObservationRevisit, value selection, row inclusion, or rendering |
| ModeTrack | Immutable trajectory snapshots and refinement proposals | Group minting/mutation, Backfill, selected values, or Matrix authority |
| Backfill | Bounded local ObservationRevisit and provenance | First hits, membership, trajectory model, selected value, or writer authority |
| Cell decision/result | Typed write/no-write decisions and immutable result closure | RAW access, regrouping, refitting, repairing, or renderer policy |
| Alignment workflow | Typed composition, runtime/transaction receipts, finalization, and delivery handoff | Mutable Matrix transport, scientific fallbacks, or emit-flag policy |
| Discovery | Candidate generation and per-sample review surfaces | Cross-sample matrix authority |
| Output | Rendering TSV/XLSX/HTML artifacts from domain decisions | Recomputing evidence or selecting authority |
| Diagnostics | Audits and observability | Product behavior unless promoted through public contract |

## Verification

Before changing alignment behavior, require the relevant subset of:

- Focused tests for changed TSV schemas, row ordering, identity sidecars, or
  owner construction.
- Runner/profile smoke check for documented command shapes.
- Expected-diff and authority review if matrix values become product-facing.
- For shadow identity settlement, H1a/H1b/H2/H3 truth tables, missing/poor
  landmark cases, leave-one-landmark-out behavior, adversarial component
  transfer, split lifecycle/lineage, and child-local `S` recomputation.
- For diagnostic ModeTrack review, optional occupancy, per-track local drift,
  unique/both/neither interval cases, sparse anchors, missing/poor landmarks,
  bounded interpolation/extrapolation, crossing intervals, non-training
  rejection, request/attempt/physical-observation substitution, deterministic
  identity/serialization, and the retained zero-RAW stop receipt.
- Public hard zero: the shadow owner must not import or call Matrix identity,
  production decisions, TSV/XLSX/CSV writers, output packages, or activation.
- Downstream consumer check when output filenames or columns change.
- RAW-tier evidence label matching the claim (synthetic, 8RAW, or 85RAW)
  without promoting a lower tier as production readiness.

## Pitfalls

- Treating alignment sidecars as ProductWriter authority.
- Treating legacy family labels as canonical identity proof after a successor
  group or PeakHypothesis surface exists.
- Using old worktree output paths as durable inputs.
- Changing output columns without updating specs, tests, and downstream docs.
- Claiming product readiness from `validation-minimal` alone.
- Treating gap-filled or successor-group sidecar rows as product authority
  without a matching output contract.
- Letting `alignment_review.tsv` support tokens promote rows without
  provenance-valid source sidecars.
- Treating ModeTrack as a second grouping owner or Backfill as a membership or
  selected-value owner.
- Using a singleton or RT prior to fill another sample without direct local
  evidence or an accepted independent ObservationRevisit.
- Calling a pre-final owner matrix `Matrix` and allowing it to cross the final
  result or delivery seam.

## See Also

- [Architecture contract](../architecture-contract.md)
- [Parameter settings](../agent/parameter-settings.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Diagnostic ledger](../diagnostic-ledger.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.json)
- [Discovery](discovery.md)
- [Evidence spine](evidence-spine.md)
