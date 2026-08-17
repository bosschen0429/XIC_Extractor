# Alignment

Alignment is the cross-sample handoff layer. It consumes explicit discovery or
accepted input, builds owners and aligned matrices, and writes machine-readable
contracts for review and downstream product decisions.

## Contract

- Alignment inputs must be explicit: accepted target input, discovery batch
  index, or documented local validation input. Do not rely on old worktree
  output paths as durable inputs.
- Alignment consumes discovered or accepted feature candidates and resolves
  cross-sample owner/group hypotheses based on evidence. It does not create the
  first sample-local Discovery hit and does not treat a single method match as
  cross-sample identity proof by itself.
- Cross-sample grouping uses all-pairs strong-edge complete-link by default. A
  connected RT/mass continuum may replace the split only when it contains one
  clean detected owner from every sample in the explicit Alignment batch,
  every accepted local edge passes the pair hard gates, and complete-link would
  otherwise split it. Incomplete groups, same-sample recurrences, weak-quality
  owners, and RT discontinuities remain on complete-link. The recovered family
  is `owner_full_batch_continuity`; Matrix identity evaluates it through the
  existing `multi_sample_detected` policy rather than treating it as all-pairs
  `owner_complete_link` evidence. A later owner may extend one bounded
  continuity group, but it does not bridge and merge two existing groups;
  single-link chaining is not family identity authority.
- A near-full connected component with exactly one batch sample absent may also
  replace its complete-link split when Alignment received the same explicit,
  injection-order mapping used to order Matrix columns, with a unique order for
  every owner in that component. The whole component must pass the symmetric
  precursor/product/loss hard envelope,
  and every adjacent injection-order edge must pass bounded RT continuity. The
  recovered family is `owner_injection_trajectory`. Missing or duplicate order,
  a second absent sample, same-sample occupancy, a hard-envelope conflict, or an
  RT discontinuity remains on complete-link; `drift_lookup` is not an alternate
  order source. Parallel connected components remain separate groups, and this
  rule does not bridge existing groups.
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
  This is an internal retention mechanism, not a public configuration value,
  persisted shard, alternate Backfill route, or second Matrix authority.
- Runner scripts orchestrate inputs, profiles, and output locations. Reusable
  grouping, ownership, identity, writer, and sidecar behavior belongs in
  package modules or explicit specs.

## One Decision Set Across Activated Outputs

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

| Output | Role |
| --- | --- |
| `alignment_matrix.tsv` | Cross-sample aligned values |
| `alignment_review.tsv` | Human/machine review summary |
| `alignment_matrix_identity.tsv` | Identity and owner traceability |
| `alignment_backfill_cell_evidence.tsv` | Backfill-related cell evidence sidecar |
| `alignment_run_metadata.json` | Conditional run settings and provenance sidecar |
| `alignment-results-v2/v3` metadata | Schema-version signals for public behavior changes |

## Boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| Alignment | Cross-sample grouping, owner construction, primary consolidation, one production decision set, and atomic Alignment-output composition | Discovery seed creation, renderer-local semantic rebuilding, or a second product writer authority |
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

## See Also

- [Architecture contract](../architecture-contract.md)
- [Parameter settings](../agent/parameter-settings.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Diagnostic ledger](../diagnostic-ledger.md)
- [Peak anchor and group boundary](family-hypothesis-boundary.md)
- [Authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Discovery](discovery.md)
- [Evidence spine](evidence-spine.md)
