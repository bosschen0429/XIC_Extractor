# Alignment

The target Alignment contract is the cross-sample scientific workflow. It
consumes explicit sample-local hypotheses/observations, closes canonical group
identity and trajectory/repair evidence, then returns
`FinalizedScientificAlignment` to the workflow projection's binding/subject
operation. The immutable workflow result exists only after cell decision and
projection completion. Alignment does not use a mutable Matrix as its internal
transport contract or let writers recompute science.

## Current Problem

The current `alignment/` tree is functional but shallow at its public seam and
overloaded internally. `pipeline.py` builds an owner Matrix early, then passes
that mutable shape through claim registration, consolidation, Backfill,
production decisions, and output assembly. Group identity, provisional/primary
inclusion, cell state, Backfill policy, diagnostics, and writer concerns are
spread across many peer files. Targeted meanwhile owns a partially separate
cross-sample/rescue path. The result is high coupling, duplicated scientific
meaning, and a large change surface even when only one decision owner should
move.

This is a responsibility/locality problem, not evidence that Targeted and
Untargeted need different capture algorithms. The frozen direction is to deepen
the current owners and replace the mixed Matrix pipeline in dependency order.
Current public output and science remain parity constraints until a separate
promotion explicitly changes them.

## Frozen Owner Contract

| Owner | Sole responsibility | Output to the next seam |
| --- | --- | --- |
| CrossSample | Initial grouping, membership, group identity, disposition, split/merge lineage, and application of accepted proposals | Canonical immutable groups and lineage |
| ModeTrack | Request-bound trajectory modeling over immutable evidence; proposes only | Typed assessable refinement proposals |
| Backfill | One bounded local `ObservationRepair` epoch | Owner-backed repaired observation or typed no-repair result |
| Scientific alignment closure | Validate request/group/disposition/membership/refinement/repair closure | `FinalizedScientificAlignment` |
| Workflow projection: binding/subject operation | Target fixed-row binding or Untarget group-row identity, followed by explicit Target/Group cell-subject construction | `TargetRowBinding` + `TargetCellSubject`, or `UntargetRowKey` + `GroupCellSubject` |
| ProductionCellDecision | Selected value or typed no-write result for one explicit Target/Group subject | Immutable cell decision with evidence refs |
| Workflow projection: completion operation | Consume cell decisions to close row inclusion, counted-detection/count/reason fields, Review Queue refs, and archive routing | Closed typed projection decisions |
| Result finalizer | Closure validation over the core, completed projection, and cell decisions | Discriminated immutable Targeted or Untargeted result |

CrossSample alone may mint or supersede a group. ModeTrack cannot move a member
or write a value, and Backfill cannot turn a repaired observation into implicit
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
- Any accepted membership change or split/merge mints successor identity and
  immutable lineage. A new processing request creates new group identity; this
  refactor does not infer cross-request continuity.
- `GroupDispositionDecision` is exactly one of `established`, `provisional`,
  `unresolved`, or `rejected`. Established requires accepted identity-bearing
  members from at least two distinct canonical samples plus method hard gates;
  a singleton is always provisional. Low support alone is not rejection.
  Disposition and output inclusion are separate.

### ModeTrack and repair relationship

- ModeTrack consumes one immutable `trajectory_input_id` containing only the
  anchors, members, apexes, injection order, evidence profile, and exclusions
  relevant to its model. Area-only or boundary-only changes do not rerun it.
- Model selection uses the simplest validated candidate. RT correction is an
  optional prior; direct local evidence wins and a prior alone cannot reassign
  a member.
- Validation must not assume that every ISTD or unknown feature follows one
  batch-wide monotonic RT drift. Sensitivity work therefore permits direction
  changes and adjacent-injection jumps, uses method-appropriate ISTDs plus
  several high-detection unknown features, and reports where the model is
  unassessable.
- A bounded ObservationRepair may change trajectory identity once. CrossSample
  may then rebuild the snapshot and request one final ModeTrack pass. No repair
  loop, refit loop, arbitrary neighborhood chunking, or singleton-based
  cross-sample fill is allowed.

### Final result and projections

`FinalizedScientificAlignment` closes the request, canonical groups,
dispositions, membership/lineage, and refinement/repair receipts. Before cell
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
  candidate states. `review_only_orphan_nl` and other review-only states remain
  in Discovery review/audit artifacts but do not receive a second RAW XIC
  search merely by entering Alignment. This keeps candidate recall available
  for review without turning an unavailable physical feature into owner-build,
  group, Matrix, or Backfill work.
- Two resolved peaks in one sample count as an unresolved doublet only when
  their apex separation is chromatographically meaningful against the narrower
  of them, not on an absolute bound alone. One MS1 peak spans many scans and its
  neutral loss can be seeded from several, and each seed opens its own
  resolution window, so the same peak returns with apexes a few seconds apart
  while the seeds sit tens of seconds apart. Below roughly half a peak width the
  trace does not show two apexes, so the pair is not a doublet and its component
  is not discarded. `owner_apex_close_sec` still applies as the lower bound.
- Cross-sample grouping uses all-pairs strong-edge complete-link by default. A
  connected RT/mass continuum may replace the split only when it contains one
  clean detected owner from every sample in the explicit Alignment batch,
  every accepted local edge passes the pair hard gates, and complete-link would
  otherwise split it. Incomplete groups, same-sample recurrences, weak-quality
  owners, and RT discontinuities remain on complete-link. The recovered family
  is `owner_full_batch_continuity`; Matrix identity evaluates it through the
  existing `multi_sample_detected` policy rather than treating it as all-pairs
  `owner_complete_link` evidence. A later owner may reconcile multiple
  compatible groups in this connected-link pass only when the combined group
  passes the hard envelope and every pair is free of a blocked edge.
  Complete-link remains the downstream hard partition; single-link chaining is
  not family identity authority.
- A partial connected component may replace its complete-link split only with
  complete unique explicit injection order for every owner, the symmetric
  precursor/product/loss hard envelope, and bounded RT continuity on every
  adjacent injection-order edge. The recovered family is
  `owner_injection_trajectory`; the full-batch rescue keeps priority. Missing or
  duplicate order, a hard-envelope conflict, or an RT discontinuity remains on
  complete-link; `drift_lookup` is not an alternate order source. Parallel
  connected components remain separate, and this rule does not bridge groups or
  add a control plane.
- A connected component that neither full-batch nor injection-trajectory rescue
  admits may still replace its complete-link split when the whole component
  stays inside the symmetric precursor/product/loss hard envelope and its apex
  RT span stays within the group span bound. The recovered family is
  `owner_bounded_continuity`; full-batch and injection-trajectory rescues keep
  priority and their own policy labels.
- After the partition settles, the grouping owner may resolve a duplicate-owner
  group only through a typed identity anchor. Current product behavior enables
  this for validated targeted ISTD trajectories: every subordinate sample is
  already owned by the larger same-target trajectory, both groups bind to the
  same owner-minted target snapshot, and the combined precursor envelope stays
  valid. Aggregate m/z/RT/product/loss geometry alone is not same-peak evidence;
  an untyped parallel trajectory remains an independent group. A resolved group
  stays a review row carrying `owner_duplicate_group_resolved_loser;winner=<id>`,
  `consolidation_state=primary_loser`, and its winner identity; the winner
  carries `owner_duplicate_group_resolved_winner`. Resolution is one level
  deep and never bridges groups that would add sample coverage, so it cannot
  chain. Because a resolved group is `review_only`, Backfill issues no request
  for it: the duplicate-owner decision belongs to this owner, not to Backfill
  and not to a second grouping stage.
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
  detected owner for the Backfill sample-quorum gate. A Backfill rescue may
  replace that censored owner only when its scalar and selected-integration
  boundaries are both interior and its primary Matrix area is valid; an
  endpoint or incomplete rescue is censored by the same rule. This is a
  quantification-authority guard, not a new peak-selection or Backfill route.
- For the observed, configured DNA dR ISTD lane only, an owner-minted targeted
  ISTD anchor from the current run's target-list snapshot may reconcile
  sample-disjoint groups that independently match the same configured m/z/RT
  target and form one locally continuous injection-order trajectory. Within
  that bounded anchor, product-m/z or observed-loss disagreement is retained as
  measurement evidence but is not a chemical-identity hard split; the known
  standard identity is stronger evidence. Same-sample occupancy, precursor-m/z
  conflict, missing or duplicate injection order, RT discontinuity, weak owner
  quality, and untyped/unobserved targets still block reconciliation.
  `tail_supported` is not weak and remains admissible. Parallel same-sample
  peaks remain separate. Reconciliation is independent of group processing
  order: a compatibility component merges only when its complete owner union
  passes the same hard and trajectory validation. Otherwise every member stays
  separate instead of choosing a track greedily. A smaller group whose samples
  are a strict subset of another same-target group cannot bridge into that
  containing trajectory; it may reconcile only with other subordinate groups
  and may be demoted only through the existing duplicate-owner review path.
  Without the mapping or typed snapshot, this rule is absent. This remains
  inside the Alignment grouping owner: it does not promote a rescued cell,
  write a Matrix value directly, infer unknown-family chemical identity, add a
  public knob, or create a second control plane.
- An `owner_bounded_continuity` family reaches the primary matrix only when at
  least two of its owners carry a resolved peak at or above the quantitation
  limit. Bounded continuity admits a group on detection-grade evidence, which is
  the right bar for deciding two peaks are the same entity and the wrong one for
  publishing a quantity; a family that misses it stays `provisional_discovery`
  with `bounded_continuity_below_quantitation_limit` and keeps its grouping, so
  Backfill scope and family identity are unaffected. The gate reads the family
  evidence, not the primary evidence token, because `promotion_policy` scopes
  itself on that token; it does not apply to any other grouping policy.
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
- Multi-process Owner Backfill delivers each complete sample result into one
  private Alignment transaction. The transaction replaces rescued cells in the
  feature-major/sample-major Matrix builder, projects live peak facts through
  the existing provenance owner, and retains audit/runtime attachments in
  canonical owner order before releasing the worker result graph. Completion
  order cannot change Matrix or sidecar ordering. Any worker or delivery
  failure discards the unpublished transaction; only an all-sample success may
  freeze the Matrix and reach the existing atomic output publication seam.
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
group owner. It consumes immutable M1 cell references, M2 Backfill facts,
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
cannot be passed to the M3 local-trajectory assessor as universal identity
truth for unknown features. No universal drift, width, valley, or identity
threshold is introduced.

Direct identity relations are owner-bound to one exact group, and the local
trajectory sample set must equal that group's owner population. A derived
identity or split decision cannot be caller-constructed. Each component must
carry explicit owner-bound membership evidence for its component ID and exact
observations. The M3 owner seals those components into one immutable partition
receipt. H1b settlement, split lineage, and same-identity aggregation must all
reuse that exact receipt; a settlement cannot be replayed against a stale,
partial, expanded, or repartitioned component set.

The current M2 recovery receipt does not prove that caller-supplied group facts
are the complete same-target parent universe (tracked as #156 gap 10). M3
therefore accepts only exact direct SUPPORT observations from its parent group;
any non-empty recovery candidate universe fails closed before component
membership. A receipt's declared parent count, explicit M3 membership label, or
M2 mapping result cannot substitute for the missing complete-group owner. This
is a deliberate shadow-capability narrowing: recovery candidate membership
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

## Diagnostic ModeTrack Review

ModeTrack is an opt-in debug and batch-review diagnostic downstream of the
existing Alignment group owner. It is not an automatic Alignment stage, a
fallback grouping route, or a candidate product control plane. Running normal
Alignment does not invoke it. `FamilyContextRef` describes one wide m/z/RT
observation universe and references zero or more existing
`group_hypothesis_id` values for diagnostic comparison. Each existing group is
one ModeTrack; neither the Family nor the ModeTrack is a chemical-identity
claim. Public or legacy family labels, `method_subject_id`, display labels, and
ordinals do not mint or replace the internal Family context.

This describes current activation state, not a reason to build another
trajectory module. The opt-in CID-NL diagnostic workflow now routes this
capability through the proposal-only `alignment/mode_track/` interface. That
owner binds each proposal to an immutable trajectory input, returns a closed
accepted/rejected/insufficient/unassessable result. The workflow passes an
accepted proposal to the existing CrossSample atomic successor application;
ModeTrack does not apply or retain successor population state. Rich predecessor,
owner/edge, and budget objects remain in a separate CrossSample-owned
application context and are absent from the immutable trajectory snapshot.
Existing diagnostic writers and validation packages remain outside that
scientific interface; no normal Alignment route or product writer activates it.

The initial model set is fit once per Family from canonical direct-SUPPORT
assessment × `PeakHypothesis` anchors. Family ModeTracks must have disjoint
owner IDs, and one `PeakHypothesis` or physical peak observation may seed at
most one track. Fit accepts initial anchors only. A later uniquely assigned
direct-SUPPORT observation may mint an immutable successor model set; consumers
still do not refit. Each track follows its own injection-order-local, piecewise-linear
trajectory. At fit time, the M8 fit owner projects each existing group's canonical
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
already an exact owner of one Family ModeTrack is observed occupancy of that
existing M3 identity; M8 preserves that one-to-one owner fact instead of
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
accepts at most one hypothesis per sample, and occupancy includes its exact M3
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
when present, direct-SUPPORT assessment against those current owners. M8 does
not retain or depend on `CellObservationRef`. Downstream M8 consumers must call
`validate_mode_track_assignment_batch_for_consumption` before using a retained
assignment batch; the operation is pure and grants no reinforcement or product
authority. Reinforcement enters through the same validation closure.

The retained fixture
`docs/superpowers/fixtures/mode_track_assignment_85raw_inconclusive_v1.json`
records the source-qualified M8 stop condition; it is not a replay fixture or a
classification oracle. It preserves the producing code/base SHAs, required
Discovery/sample/trace hashes, 85-sample census, and historical 85-open/85-call
timing claim. The required owner-bound Discovery index and original seed bytes
were unavailable, while the retained historical packet had different hashes
and no observed completion counters. Reconstruction therefore did not launch,
made zero RAW opens and zero chromatogram calls, and did not consume the
authorized attempt. Its evidence verdict remains `inconclusive / no-write`;
historical morphology, intensity, or mode labels cannot seed tracks or grant a
current-checkout compatibility claim.

The later fresh current-request Gate B receipt
`docs/superpowers/fixtures/mode_track_assignment_85raw_gate_b_v1.json` closes
that source gap for the CID-NL 116.0474, precursor 349.215 validation lane. The
accepted 85-sample run retained 86 physical hypotheses in 11 ModeTracks, with
9 multi-owner tracks, exact owner-to-assignment closure, no same-sample double
occupancy, and no producer-added RAW calls. In `TumorBC2297_DNA`, the 12.412
minute direct-SUPPORT peak and 13.035 minute non-training physical observation
remain exact owners of two different cross-sample tracks.

The lane must use the existing `CID_NL_ALIGNMENT_CONFIG` value
`anchor_min_seed_events=1`: an MS1-only physical observation contributes one
observation event. A rejected control using the generic default value `2`
degenerated to one singleton track per physical hypothesis, so exact-owner
assignment alone is not a sufficient Gate B oracle. Acceptance also requires
observed cross-sample multi-owner aggregation. This receipt remains
`shadow_ready / product_authority=none`; four tracks have no direct-SUPPORT
anchor, and the evidence grants no Matrix, Backfill, chemical-identity, or
ProductWriter authority.

The bounded Issue #180 M9 Gate C packet at
`docs/superpowers/validation/issue180_m9_gate_c_closed_writer_v3/README.md`
retains the current closed-writer evidence. Its C2 finalizer accepts only the
fresh typed ModeTrack projection plus one in-memory owner-validated review set,
derives its checked manifest and expected diff internally, and publishes the
QMV and terminal receipts atomically. The earlier
`docs/superpowers/validation/issue180_m9_gate_c_85raw_v2/README.md` packet
retains the historical RAW-backed acquisition, fresh-owner comparison,
manual-review, and 26-cell QMV evidence, but its broader persisted input surface
is `diagnostic_only`. Neither packet is a replayable writer input or activates
defaults, changes M1, establishes chemical identity, or grants a new writer
authority.

`FamilyContextRef`, the model set, and `ModeTrackAssignment` remain internal
diagnostic structures with `product_authority=none`. Their owner-specific
`shadow_ready` token describes retained artifact validity; opt-in runner output
is `diagnostic_only` and does not register a productization lane. The runner may
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

The default `production` Alignment output is only `alignment_matrix.tsv`.
Identity, review, provenance, backfill evidence, HTML, and workbook artifacts
remain explicit machine, validation, diagnostic, or compatibility outputs;
their schemas and writers are unchanged.

| Output | Role |
| --- | --- |
| `alignment_matrix.tsv` | Cross-sample aligned values |
| `alignment_review.tsv` | Human/machine review summary |
| `alignment_matrix_identity.tsv` | Identity and owner traceability |
| `alignment_backfill_cell_evidence.tsv` | Backfill-related cell evidence sidecar |
| `alignment_run_metadata.json` | Conditional run settings and provenance sidecar |
| `alignment-results-v2/v3/v4` metadata | Schema-version signals for public behavior changes; unanchored v3 accepts the legacy or canonical Matrix value policy, historical paired targeted-ISTD v3 requires the canonical policy, and v4 requires that same paired binding and canonical policy |

## Boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| CrossSample | Grouping, membership, identity, disposition, proposal application, and split/merge lineage | ModeTrack fitting, local ObservationRepair, value selection, row inclusion, or rendering |
| ModeTrack | Immutable trajectory snapshots and refinement proposals | Group minting/mutation, Backfill, selected values, or Matrix authority |
| Backfill | Bounded local ObservationRepair and provenance | First hits, membership, trajectory model, selected value, or writer authority |
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
  evidence or an accepted independent ObservationRepair.
- Calling a pre-final owner matrix `Matrix` and allowing it to cross the final
  result or delivery seam.

## See Also

- [Architecture contract](../architecture-contract.md)
- [Parameter settings](../agent/parameter-settings.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Diagnostic ledger](../diagnostic-ledger.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Discovery](discovery.md)
- [Evidence spine](evidence-spine.md)
