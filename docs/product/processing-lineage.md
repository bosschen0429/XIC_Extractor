# Result Traceability

Result traceability distinguishes which request, attempt, source observations,
and immutable evidence produced a result. Identity is not scientific acceptance
or permission to publish.

## Current Status

This page separates current identity and artifact contracts from the
[reserved delivery design](#reserved-delivery-contract). The latter is an
accepted design boundary, not a claim that product sealing exists.

| Part | Plain meaning | Status |
| --- | --- | --- |
| Processing identity (`xic_extractor/processing_identity.py`) | Which request and attempt ran, over which RAW sources | Implemented; production Alignment, Backfill, and Discovery modules use it |
| Target identity (`TargetKey`, `TargetListSnapshot` in `xic_extractor/configuration/target_identity.py`) | Which exact target list a run used | Implemented; production Targeted modules use it |
| Targeted delivery lineage | Per-cell Targeted revisions, stages, and seals | Planned; no current implementation or product publication authority |
| Untargeted row identity (`UntargetRowKey`) | A stable identity for one Untargeted Matrix row | Implemented in `alignment/projection/untarget/identity.py` |
| Untargeted per-cell delivery lineage | A retained revision/evidence bundle for each Matrix cell | Not planned; see [Untargeted delivery decision](#workflow-specific-delivery) |
| Untargeted output-level seal | One sealed manifest for one complete Untargeted Matrix delivery | Planned; not implemented |

Current entry points are
[processing identity](../../xic_extractor/processing_identity.py),
[target identity](../../xic_extractor/configuration/target_identity.py), and
[Untarget row identity](../../xic_extractor/alignment/projection/untarget/identity.py).
Executable identity does not establish a production artifact registry or
delivery seal.

## Ownership Boundary

This page owns:

- workflow-neutral processing-request and attempt identity;
- immutable evidence-artifact identity and lineage edges;
- the relationship between kernel decisions and projection-owned delivery
  cells;
- the reserved two-phase staging and sealing contract;
- logical retention classes and plot-ready evidence closure;
- retry, stale, supersession, failure, and null lineage semantics.

Other owners remain:

- [`run-provenance.md`](run-provenance.md): Targeted workbook run metadata;
- [`sample-metadata-qc.md`](sample-metadata-qc.md): canonical sample identity
  schema and lookup projection;
- [`review-roundtrip.md`](review-roundtrip.md): typed review actions, review
  queues, expected-diff review, and reviewer-rationale routing;
- [`shared-processing-kernel.md`](shared-processing-kernel.md): scientific
  hypotheses, observation states, group membership, selection, and Backfill
  facts;
- [`vendor-raw-evidence-model.md`](vendor-raw-evidence-model.md): source-query
  and scan-selection meaning;
- [Execution Runtime](execution-runtime.md): scheduling, cancellation, retry
  execution, cleanup, cache lifecycle, and runtime observability;
- Targeted and Untargeted projection owners: counted detection, Matrix
  presence/value, output schemas, and final writer authority.

## Minimal Vocabulary

These are internal lineage concepts, not additional user-visible review tags.

| Term | Meaning | Not the same as |
| --- | --- | --- |
| `AcquisitionSource` | One immutable source-data identity plus its acquisition format and adapter-readable observation namespace | A local file path or sample display name |
| `TargetKey` | One target-list-owner namespace plus its stable normalized target label | An exact m/z/RT/transition definition or row number |
| `TargetDefinition` | One exact normalized Target definition under a `TargetKey` | A mutable CSV row or label alone |
| `TargetListSnapshot` | One immutable ordered set of owner-minted target definitions used by a processing request | `target_config_hash`, a path, or a caller-owned mutable list |
| `ProcessingRequest` | One normalized scientific computation request over identified sources, effective method/evidence settings, workflow constraints, and contract versions | An execution, output folder, timestamp, or UI session |
| `ProcessingAttempt` | One execution of a `ProcessingRequest`; every retry receives a new identity | The logical request or the product delivery |
| `EvidenceArtifact` | One immutable artifact instance identified by producer attempt, content digest, schema, semantic role, and lineage references | A mutable path, cache entry, or authority decision |
| `UntargetRowKey` | Untargeted projection-owned row identity derived from one finalized canonical group | `group_hypothesis_id`, `FAM...`, m/z/RT display text, or row order |

Do not introduce a generic `run_id` as a substitute for these identities.
Existing diagnostic `run_id` / `source_run_id` fields remain local compatibility
labels until an adapter binds them to the contract above.

## Identity Contract

| Identity | Required basis | Forbidden basis |
| --- | --- | --- |
| `acquisition_source_id` | Capability-validated content identity or stable source fingerprint, acquisition format, and observation namespace | Path, mtime, file order, or display name alone |
| `target_key_id` | Target-list-owner namespace + owner-normalized target label | m/z, RT window, current row order, or label without an owner namespace |
| `target_definition_id` | Owner-minted target key + every normalized effective `Target` field | Target label or raw CSV bytes alone |
| `target_list_snapshot_id` | Owner namespace + exact ordered target definitions + target-list schema/version | Mutable `list[Target]`, file path, truncated config hash, or unordered membership |
| `processing_request_id` | Identified acquisition sources; normalized evidence queries and method constraints; algorithm/schema/contract versions | Output path, wall-clock time, worker count, cache hit/miss, or product publication result |
| `processing_attempt_id` | A unique execution identity referencing exactly one request; optional retry/predecessor reference | Request digest alone or a reused timestamp |
| `canonical_sample_identity` | Owner-minted stable namespace + `sample_metadata` identity schema/version + owner-stable primary sample key | Acquisition source, filename/path, row order, display alias, injection order, role, batch, or exclusion state |
| `immutable_review_record_ref` | Review-owner subject from canonical sample + stable target key; exact target definition + processing request; schema/version; canonical content digest; exact artifact bindings; optional same-subject predecessor and supersession reason | Bare sample/target labels, caller-provided ID/digest, mutable latest row, private rationale, or review truth inferred by lineage |
| `evidence_artifact_id` | Producer attempt + semantic role + schema version + content digest | Filename, directory, row count, or generated timestamp alone |
| owner-backed CID-NL `peak_hypothesis_id` | Hypothesis method + canonical sample identity + physical peak content ID + chromatographic-support artifact content ID | Target label, ISTD role/pairing, trace-group ID, selection status, attempt token, or output position |
| kernel object reference | Request identity plus the normalized sample-local or cross-sample scientific identity | A bare ID whose meaning can change across requests |
| `group_hypothesis_id` | Group identity schema + exact processing request + canonical-sorted identity-bearing members, each binding canonical sample to an owner-backed hypothesis identity | `FAM...`, group ordinal, sample stem, area, display RT/m/z, or an untagged wrapper identity |
| `untarget_row_key` | Untarget projection schema + finalized canonical group identity | Group/display label alone, center coordinates, current Matrix row number, or cross-request inference |

The `processing_identity` owner defines typed IDs
as SHA-256 digests over compact canonical JSON (`sort_keys`, UTF-8, finite JSON
numbers), with a type/version prefix. Its current content boundary is:

- an acquisition source binds the full-file SHA-256, byte size, acquisition
  format, and adapter-readable observation namespace; path and file stat values
  are used only to locate the file and detect mutation during hashing;
- target keys, exact definitions, and ordered snapshots come from the
  [target-list owner](../../xic_extractor/configuration/target_identity.py);
  changing a definition or order creates a new snapshot, not a mutation of the
  request-pinned list;
- the Targeted processing request binds the canonical source set, the exact
  source-to-request-local-adapter-key and injection-order binding, all effective
  Targeted extraction settings, the exact owner-minted target-list snapshot,
  effective RT
  priors, approval records, optional MS1-shape support content and activation
  policy, the complete order-insensitive Discovery candidate universe for every
  acquisition sample, and the owning input-schema, algorithm, and contract
  versions. The candidate universe digest covers the full scientific candidate
  payload; missing, additional, or changed candidates cannot run under the same
  Targeted request. The adapter key is part of current execution semantics but
  is not a canonical sample identity and cannot be reused as a delivery-cell
  key;
- a processing attempt binds one request, a fresh opaque 128-bit token, and an
  optional predecessor attempt. Retry always issues a new attempt. Terminal
  outcome and cancellation/failure events remain append-only facts owned by the
  execution-runtime contract and never rewrite identity.
- artifact content binds exact payload bytes through SHA-256, byte size, media
  type, and an owner-fixed serialization format. An artifact instance separately
  binds one producer request/attempt, the producer-owned semantic role/schema
  contract, that content identity, and a canonical set of upstream artifact
  references. Equal bytes across attempts preserve content equality but never
  collapse artifact-instance identity;
- the Pt Discovery evidence CSV artifact-envelope adapter is diagnostic-only.
  Its owner fixes the `pt_pattern_discovery_evidence` UTF-8 CSV bytes and role.
  Callers cannot supply role/schema strings; minting requires an owner-issued
  processing attempt;
- canonical samples and immutable review records come from
  [Sample Metadata](sample-metadata-qc.md) and
  [Review Roundtrip](review-roundtrip.md). Lineage composes their owner-resolved
  references and snapshots rather than deriving IDs from display labels.

The Targeted adapter currently names the existing Thermo MS-controller-1
namespace explicitly. A different controller, acquisition adapter, or
observation namespace therefore creates a different source identity rather
than silently reusing a bare scan number. The resolver performs one sequential
full-file read per source, but opens no vendor RAW handle and makes no XIC call.
Production Targeted execution already uses this resolver:
`TargetedApplicationService.execute_application` snapshots its inputs through
`snapshot_targeted_production_inputs` and
`build_targeted_processing_request_identity` before acquisition. This source
fingerprinting does not imply that product delivery sealing is implemented.

This freezes the acquisition-source -> processing-request ->
processing-attempt boundary plus the sample, target-list, and review owner APIs.
The lineage layer's `ProcessingOwnerReferences` only composes and validates a
complete request and attempt, owner-resolved sample bindings whose exact lookup tokens
cover every request acquisition input and share one exact metadata snapshot,
the request-pinned target-list snapshot, a same-request/same-attempt artifact
closure with no missing upstream dependency, and an optional owner-resolved current
review record bound to the exact sample, target key/definition, request, and
review registry snapshot. A review record must retain typed artifact references
that exist in that validated closure. Lineage does not mint
sample/target/artifact/review IDs and it
rejects mixed sample snapshots, target/request substitution, unresolved review
records, superseded review records, artifact content/role/schema substitution,
missing dependencies, cross-attempt mixing, and review-snapshot substitution.

Production workflows and diagnostic tracers consume this owner-reference
closure; it is not a production artifact registry. The processing-identity owner
derives the complete same-request predecessor chain from `ProcessingAttempt.retry_of`.
Reusable artifact closure accepts an owner-issued producer attempt, not a
caller-authored allowlist; siblings are not implicit dependencies. Cross-attempt
runtime dependency reuse remains unimplemented, as described in
[Execution Runtime](execution-runtime.md#retry-and-resume).

The [Evidence Spine](evidence-spine.md#cell-observation-reference) owns the
application of these references to provisional membership and bounded recovery.
It does not permit a naked target plus an independently valid peak to substitute
for recovery evidence.


An acquisition source and a sample remain different typed identities, but the
current lab contract is one independent canonical analysis sample per source
included in an XIC analysis folder. QC replicates and meaningful reinjections
therefore keep their distinct full names, result columns, and review scopes;
lineage must not strip suffixes or collapse them into a biological-specimen
identity. Invalid pre-crash acquisitions are excluded before this inventory.
Two paths containing the same exact source bytes remain one acquisition-source
identity and are rejected as duplicate request inputs; a filename copy cannot
mint another sample or source identity.
If the bounded tracer cannot obtain an unambiguous stable sample key from the
sample-metadata owner, it must stop rather than derive one independently from a
filename, display label, or row order.

[Group Boundary](family-hypothesis-boundary.md) owns canonical group membership,
successor identity, and the distinction between owner-backed observations and
legacy display labels. The Untarget projection derives `UntargetRowKey` from the
finalized canonical group; it is not an ordinal label or a chemical identity.
Automatic cross-request same/split/merge continuity is not implemented.


## Current bindings and storage limits

- [Targeted application](../../xic_extractor/applications/targeted/application.py)
  snapshots source content and the exact request before acquisition. Its normal
  output is the workbook described by [Run Provenance](run-provenance.md), not
  a persisted delivery revision or seal.
- Pt and Targeted MS1-shape adapters bind diagnostic artifacts to exact bytes,
  roles, schemas, requests, and attempts. This does not grant publication.
- Current Untargeted output retains `alignment_cells.tsv` for per-cell tracing.
  Optional Backfill Parquet is a diagnostic shadow, not a shared production
  archive or a required future cutover.
- Same-run handoff uses immutable typed batches. Arrow IPC is considered only
  after a measured transport bottleneck. No generic archive, cross-run catalog,
  new delivery-plan type, or archive-to-delivery handoff is required without a
  current consumer.
- Heavy candidate, trace, and debug populations remain opt-in, validation-,
  review-, or failure-scoped. This contract does not add an ordinary-run archive.

Retention is by dependency rather than file extension. The lifecycle classes
below distinguish what may be retained; they do not assert that a shared
retention engine or product sealing implementation exists.

Every retained record also belongs to one lifecycle class:

| Class | Meaning |
| --- | --- |
| `essential` | Final result, delivery identity/seal, authority closure, and minimum evidence refs needed to explain a current value/null |
| `reviewable_compact` | Queryable machine archive needed for bounded review, including provisional groups and final cell-decision evidence |
| `diagnostic_heavy` | Candidate universes, traces, galleries, and debug payloads retained only for opt-in validation, active review, or failure forensics |
| `reconstructable` | Derived cache/artifact whose exact source identity, algorithm contract, and rebuild capability remain available |
| `compatibility` | Legacy schema/projection retained only for a named current consumer and removed when its consumer census reaches zero |

## Failure, Null, Stale, And Supersession

Keep orthogonal facts on separate axes. References to sealing and delivery
supersession below belong to the reserved delivery contract, not current
publication capabilities:

| Axis | Owner | Required distinction |
| --- | --- | --- |
| Scientific observation | Shared kernel | `observed`, `missing`, `absent`, `unassessable`, `ambiguous`, `failed` |
| Execution outcome | Execution runtime | Whether the attempt completed, failed, was cancelled, or produced partial diagnostic state |
| Artifact integrity | Lineage validation | Available and matching, missing, corrupt/hash-mismatched, or incompatible with the requested contract |
| Planned delivery lifecycle | Projection/writer | Staged, sealed/current, or explicitly superseded |
| Human review queue | Workflow review | Waiting for review versus a completed review conclusion |

Rules:

- A failed attempt does not mean a sample is `absent`.
- A missing/corrupt evidence artifact blocks sealing; it does not change the
  scientific observation.
- `missing` means no completed scientific evaluation result; `absent` means a
  completed evaluation found no supported hypothesis.
- `unassessable` remains a reviewed evidence conclusion, not a pending-review
  status.
- Staleness is a relationship between an immutable artifact and the request,
  contract, or input identity now being evaluated. Do not mutate the artifact
  to call it fresh again.
- Targeted supersession changes which sealed revision is current; Untargeted
  supersession changes which complete output-level seal is current. Neither
  erases or rewrites the prior delivery.

## Reserved delivery contract

The following preserves the accepted delivery and retention design from
[Issue #123](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/123).
Targeted delivery revisions/stages/seals and the Untargeted output-level seal
are not implemented. These requirements neither add a current writer gate nor
authorize a new output surface. Existing output transactions remain authoritative.

### Workflow-specific delivery

Targeted uses immutable per-cell revisions: each delivered value or explicit
null traces to its attempt, selected hypothesis/integration or non-observation
decision, evidence artifacts, and source observations.

Untargeted uses one output-level seal binding the complete Matrix, retained cell
ledger, processing attempt, input sources, and output hashes. It does not use
per-cell delivery revisions or require per-cell retained plots. This keeps
retention proportional to review needs; per-cell tracing uses the ledger or
replay. An explicitly promised offline review must still retain its own evidence.

Retries create new attempts. Supersession never overwrites old identities or
evidence and requires the owning projection and writer authority.

### Delivery identities

| Term | Meaning |
| --- | --- |
| `DeliveryCellKey` | Targeted canonical sample plus target identity; not an acquisition source or kernel cell. |
| `DeliveryRevision` | Immutable Targeted value/null for a delivery cell, linked to its producing attempt and decision; not an Untargeted cell or publication permission. |
| `DeliverySeal` | Immutable authority-approved manifest for Targeted revisions or one complete Untargeted Matrix and retained ledger. |

| Identity | Required basis | Forbidden basis |
| --- | --- | --- |
| `delivery_cell_key` (Targeted) | `sample_metadata`-owned stable sample identity + target-list-owner identity | Acquisition-source identity, display name alone, column position, or output path |
| `delivery_revision_id` (Targeted) | Cell key + producer attempt + selected decision/null payload + projection policy/contract version | Mutable “latest” state or Untargeted cell identity |
| `delivery_seal_id` (planned) | Canonical sealed manifest content: Targeted revision and output-artifact identities, or Untargeted attempt, source, retained-ledger, and complete Matrix artifact identities | Directory existence, writer exit code, or a diagnostic candidate |

### Lineage Graph And Invariants

```mermaid
flowchart LR
    S["AcquisitionSource"] --> R["ProcessingRequest"]
    R --> A1["ProcessingAttempt A1"]
    R --> A2["ProcessingAttempt A2 (retry)"]
    A1 --> F1["failed/unsealed artifacts"]
    A2 --> E["immutable EvidenceArtifacts"]
    E --> K["kernel decisions:<br/>PeakHypothesis / CellObservation"]
    K --> T["Targeted staged DeliveryRevision"]
    T --> TL["planned Targeted DeliverySeal"]
    TL --> TO["Targeted output"]
    K --> C["Untargeted retained cell ledger"]
    C --> UL["planned Untargeted output-level seal"]
    UL --> UO["complete Untargeted Matrix"]
```

Required invariants:

1. A `ProcessingAttempt` references exactly one `ProcessingRequest`.
2. Every evidence artifact references one producer attempt, declares its
   semantic role and schema, and names any upstream evidence-artifact
   dependencies.
3. Artifacts are immutable. Rebuilding under a new attempt creates a new
   artifact identity; matching content digests may prove byte equivalence but
   never collapse the producer-attempt lineage.
4. An attempt may consume an artifact from an earlier attempt only through an
   explicit immutable dependency reference. It must not copy or relabel that
   artifact as though the later attempt produced it.
5. Every selected hypothesis, integration, group observation, review action,
   Targeted delivery revision, and retained Untargeted cell-ledger decision can
   identify the attempt and artifacts it used. A
   retained human-review conclusion additionally references one immutable,
   typed review record owned by the review-roundtrip contract. That reference
   is lineage-valid only when the record has a stable identity, schema/version,
   content digest, exact artifact bindings, and any predecessor/supersession
   reference plus reason. This does not transfer review authority or private
   rationale ownership into the lineage layer.
6. A Targeted delivery revision references exactly one Targeted delivery cell
   and producing attempt. Its upstream evidence may come from other producer
   attempts through declared artifact-dependency edges.
7. An observed numeric Targeted revision references its selected hypothesis or
   `CellObservation`, `IntegrationResult`, selection/audit record, and the
   evidence needed to explain and plot it. An Untargeted Matrix cell is traced
   through the sealed attempt's retained cell ledger or replay, without a
   retained per-cell revision or plot bundle.
8. A Targeted null revision records the underlying non-observation state and
   reason. Untargeted cell-ledger decisions preserve the corresponding state.
   Missing evidence, a failed attempt, or a missing artifact cannot silently
   become scientific `absent` or numeric zero.
9. A planned seal is valid only when referenced artifacts exist and match
   their content identities and the owning projection's authority gate passes.
   Targeted validates its revision set; Untargeted validates the complete
   Matrix, retained cell ledger, attempt, and source bindings.
10. Sealing a successor does not mutate the prior seal. It records an explicit
   supersession edge and changes the current delivery only through the
   authorized writer.
11. Targeted uses per-cell `DeliveryCellKey` and `DeliveryRevision` values.
    Untargeted uses `UntargetRowKey` and a planned output-level seal (see
    [workflow-specific delivery](#workflow-specific-delivery)). Both can
    still reference the same kernel decision and evidence artifacts.

### Source-Observation And Plot-Ready Evidence

An evidence artifact that depends on source data records:

- `acquisition_source_id`;
- acquisition adapter/backend and source format;
- native scan/spectrum identity or a capability-validated equivalent;
- scan-selection predicate and trace type;
- m/z or mass range, tolerance, RT window, and relevant MS/precursor context;
- raw extracted trace points used as input;
- the exact decision-trace points or a deterministic transform contract and
  all inputs needed to reproduce them;
- detection, boundary, and quantitation basis;
- related sample, trace-group, hypothesis, integration, group, and cell
  references as applicable.

For Targeted selected, delivered, reintegrated, or manual-review candidates,
the retained bundle must be sufficient to render the evidence view without
reopening the acquisition source. Source references remain necessary for
audit, but they are not a substitute for retained plot-ready evidence.
Untargeted delivery retains the cell ledger for per-cell tracing or replays the
sealed attempt; it does not retain per-cell plot evidence or require zero-RAW
rendering for each Matrix cell. An explicit Untargeted review that promises an
offline plot must separately retain the exact evidence needed for that review.

This requirement does not select Thermo RAW, mzML, JSON, NPZ, Arrow, a
database, or another physical representation. [Acquisition capability evidence](../diagnostic-ledger.md#2026-07-24-acquisition-source-and-reusable-artifact-capability) must establish
source and reusable-artifact capabilities, fidelity, size, and rebuild cost.

### Delivery retention

| Class | Logical rule |
| --- | --- |
| Sealed Targeted delivery lineage | The seal, per-cell revision records, output hashes, decision/audit references, and source identities remain immutable for as long as that delivery can be cited |
| Sealed Untargeted delivery lineage (planned) | The output-level seal, complete Matrix artifact hashes, attempt and source bindings, and retained `alignment_cells.tsv` remain addressable for as long as that delivery can be cited; individual cell traces use that ledger or replay |
| Current delivered Targeted plot-ready evidence | Pinned; it cannot depend on a future source reopen |
| Superseded delivered Targeted evidence | The immutable seal/revision/decision lineage remains audit-addressable; plot-ready evidence stays pinned while the old delivery remains a supported claim or is referenced by an active review, lockbox, expected diff, or retained audit claim |
| Active manual-review evidence | Pinned while waiting for a conclusion; the review binds to exact artifact identities and then moves to the completed-review class |
| Completed human-review evidence | The typed review record and exact evidence promised to that review stay pinned until the conclusion is explicitly superseded or retired and no active dependency still references it |
| Targeted selected or reintegrated but not delivered evidence | The selection/reintegration audit and exact plot-ready evidence stay pinned until the decision is explicitly superseded or retired and no active dependency still references it |
| Unselected and unreviewed evidence | May be evictable if it is not referenced by a seal, active review, lockbox, expected diff, or retained audit claim |
| Failed/cancelled-attempt artifacts | Never become delivery evidence by process success alone; cleanup and optional forensic retention belong to [Execution Runtime](execution-runtime.md) |
| Rebuildable cache | May be evicted only when the source identity, deterministic request/transform contract, and rebuild capability remain valid |

Previously delivered or human-reviewed decisions are comparators, not
immutable scientific truth. New evidence may explicitly supersede them, but a
refactor or rerun must not silently overwrite them. Targeted and any explicitly
retained offline-review evidence must remain reproducible with zero
acquisition-source reopens until the decision is explicitly superseded or
retired; Untargeted Matrix cell tracing uses the retained ledger or replay,
without per-cell evidence retention. Completion of a refactor alone is not
retirement.

Here, `audit-addressable` includes the immutable seal, source references, and
artifact identities; Targeted also includes revisions and decision/review
records, while Untargeted includes the retained cell ledger. Targeted plot-ready
inputs remain available whenever the historical decision is a supported claim
or has another active dependency. Retirement requires a durable record
of the subject, owning policy or actor, reason, and time; it is invalid while a
seal, active review, lockbox, expected diff, or supported audit claim still
depends on that evidence.

No numeric retention period or eviction technology is selected here. A
retention policy must never advertise “rebuildable” if the required source,
adapter, method contract, or fidelity is unavailable.

### Two-Phase Delivery Seal

The reserved delivery contract uses two phases:

1. **Stage** — write proposed output artifacts and a lineage manifest outside
   the final delivery location. Validate schemas, hashes, artifact closure,
   and the owning projection's authority. Targeted also validates delivery-cell
   uniqueness and revision-to-kernel-decision references. Untargeted validates
   the complete Matrix, retained ledger, attempt, and source bindings.
2. **Seal and publish** — create the immutable `DeliverySeal`, then atomically
   expose that sealed bundle or current-delivery pointer. Only sealed content
   is deliverable.

If staging, validation, sealing, or publication fails:

- the prior sealed delivery remains current;
- partial files remain non-deliverable;
- no scientific observation is rewritten;
- no null becomes zero;
- the failed attempt and stage can be diagnosed and cleaned through the execution runtime.

For Targeted, an exact duplicate retry does not require a new revision when
the existing sealed revision remains authoritative. A new sealed revision
references its producing attempt and any superseded revision. For Untargeted,
an authorized successor seals a complete output; it does not create per-cell
delivery revisions.

### Validation boundary

A delivery implementation must demonstrate, with synthetic/no-RAW artifacts:

- distinct attempts for retry and changed requests, immutable content identity,
  and explicit predecessor closure;
- no seal for partial/failed work, corrupt or missing artifacts, incompatible
  schemas, stale identity, or unauthorized publication;
- Targeted revision uniqueness and selected/null decision closure, versus
  complete-output and retained-ledger closure for Untargeted;
- zero acquisition-source opens for Targeted or explicitly promised offline
  review plots, while ordinary Untargeted cell tracing remains ledger/replay;
- immutable supersession and retention pins across active review and audit;
- unchanged scientific decisions, selected values, counts, and writer authority
  unless the separate workflow contract explicitly changes them.

No storage engine, numeric retention period, public registry, or publication
mechanism is selected by these requirements.

## Verification of current identities

[Processing identity tests](../../tests/test_processing_identity.py),
[owner-reference tests](../../tests/test_processing_owner_references.py), and
[target-list identity tests](../../tests/test_target_list_identity.py) exercise
the current identity and binding owners. They do not prove that the reserved
delivery system exists or that a product run has a delivery seal.
