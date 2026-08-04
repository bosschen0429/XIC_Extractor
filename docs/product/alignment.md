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
- Runner scripts orchestrate inputs, profiles, and output locations. Reusable
  grouping, ownership, identity, writer, and sidecar behavior belongs in
  package modules or explicit specs.

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

## Shadow ModeTrack Assignment

Issue #179 adds a second internal, no-write fact at the existing M3
identity/trajectory boundary. `FamilyContextRef` describes one wide m/z/RT
observation universe and references zero or more existing
`group_hypothesis_id` values. Each existing group is one ModeTrack; neither the
Family nor the ModeTrack is a chemical-identity claim. Public or legacy family
labels, `method_subject_id`, display labels, and ordinals do not mint or replace
the internal Family context.

The initial model set is fit once per Family from canonical direct-SUPPORT
`PeakHypothesis` anchors. A later uniquely assigned direct-SUPPORT observation
may mint an immutable successor model set; consumers still do not refit per
cell. Each track follows its own injection-order-local, piecewise-linear
trajectory. At fit time, the M3 owner projects each existing group's canonical
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
valley, width, shoulder, or intensity threshold. Missing or poor landmarks,
sparse anchors, unsupported interpolation/extrapolation, and predicted interval
crossing fail closed.

When every track is outside the policy's bounded interpolation/extrapolation
coverage, the sample is `out_of_model`. When at least one track is predictable
but another competitor is unavailable, that competitor cannot be excluded and
the sample is instead `technical_unassessable`.

For one sample, resolved hypotheses may occupy the first track, the second
track, both through different hypotheses, or neither. A candidate is
`uniquely_assigned` only when exactly one prediction interval contains it and
all competitors are excluded. The other terminal states are
`ambiguous_multiple_tracks`, `out_of_model`, `not_observed`, and
`technical_unassessable`. A track accepts at most one hypothesis per sample,
and a hypothesis accepts at most one track. Only a uniquely assigned direct
SUPPORT observation may reinforce that one track; MS1-only, Backfill, shadow,
inferred, deconvolved, manual, and shared-set observations remain non-training.
Model sets are bound to their exact processing request; anchors, candidates, and
landmarks must retain the resolved acquisition source and injection order.
Every observed assignment retains its exact candidate identity, and a direct
SUPPORT assignment also retains the exact source cell artifact identity.

The retained fixture
`docs/superpowers/fixtures/mode_track_assignment_85raw_inconclusive_v1.json`
records the source-qualified M8 stop condition. Replay normalizes checkout CRLF
to LF before hashing and parsing this text fixture, so its identity does not
depend on the runner platform. Its required-oracle fields name
the expected 85-open/85-call target; they are not observed counters for the
retained historical packet. The required owner-bound v2 Discovery index and
original seed packet bytes were unavailable, while the available historical
packet had different hashes and no observed RAW-open/chromatogram-call
completion counters. The authorized Windows-native reconstruction therefore
did not launch and remains available for a future source-restored attempt.
No-RAW replay reports one Family, 85 samples, zero RAW opens, zero chromatogram
calls, and zero typed hypotheses, tracks, anchors, landmarks, assignments, or
ambiguous assignments. Its evidence verdict is `inconclusive / no-write`; the
historical morphology, intensity, and mode labels cannot seed tracks or grant a
current-checkout compatibility claim.

`FamilyContextRef`, the model set, and `ModeTrackAssignment` remain internal
`shadow_ready / product_authority=none` structure. The inconclusive retained
evidence does not alter Targeted behavior, Backfill, acceptance manifests,
QuantMatrixVersion, Matrix values, public schema, workbook output, GUI, CLI,
configuration, or defaults.

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
| Alignment | Cross-sample grouping, owner construction, primary consolidation, sidecar emission | Discovery seed creation, workbook rendering, product writer authority |
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
- For shadow ModeTrack assignment, optional occupancy, per-track local drift,
  unique/both/neither interval cases, sparse anchors, missing/poor landmarks,
  bounded interpolation/extrapolation, crossing intervals, non-training
  rejection, deterministic identity/serialization, and a zero-RAW retained
  replay verdict.
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
