# Result Traceability

Result traceability distinguishes which request, attempt, source observations,
and immutable evidence produced a result. Identity is not scientific acceptance
or permission to publish.

## Current Status

This page records the current identity and artifact contracts.

| Part | Plain meaning | Status |
| --- | --- | --- |
| Processing identity (`xic_extractor/processing_identity.py`) | Which request and attempt ran, over which RAW sources | Implemented; production Alignment, Backfill, and Discovery modules use it |
| Target identity (`TargetKey`, `TargetListSnapshot` in `xic_extractor/configuration/target_identity.py`) | Which exact target list a run used | Implemented; production Targeted modules use it |
| Untargeted group label (`feature_id`) | A report-scoped `F000001` label for one published cross-sample group | Implemented in `alignment/untargeted_finalized_matrix.py` |

Current entry points are
[processing identity](../../xic_extractor/processing_identity.py),
[target identity](../../xic_extractor/configuration/target_identity.py), and
the [feature_id label](../../xic_extractor/alignment/untargeted_finalized_matrix.py).
Executable identity does not establish a production artifact registry.

## Ownership Boundary

This page owns:

- workflow-neutral processing-request and attempt identity;
- file-boundary fingerprints that bind written evidence to its sources;
- the relationship between kernel decisions and projection-owned delivery
  cells;
- logical retention classes;
- retry, stale, failure, and null lineage semantics.

Other owners remain:

- [`run-provenance.md`](run-provenance.md): Targeted workbook run metadata;
- [`sample-metadata-qc.md`](sample-metadata-qc.md): canonical sample identity
  schema and lookup projection;
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
| `EvidenceLocator` | A completed evidence report path plus the Matrix file SHA-256, attempt UUID, and schema version it belongs to | A digest of in-memory facts or an authority decision |
| `feature_id` | Report-scoped `F000001` label of one published cross-sample group, numbered in raw measured member order | A cross-run identity, chemical identity, or Matrix row number |

Do not introduce a generic `run_id` as a substitute for these identities.
Existing diagnostic `run_id` / `source_run_id` fields remain local compatibility
labels until an adapter binds them to the contract above.

## Identity Contract

| Key | Basis | Forbidden basis |
| --- | --- | --- |
| `acquisition_source_id` | `sha256:<full-file content digest>` for content-bound sources, or `local:<device>:<file id>:<size>:<mtime>:<ctime>` for a local file revision; format and observation namespace stay on the source object | Path, file order, or display name alone |
| `TargetKey` | Target-list-owner namespace + owner-normalized target label | m/z, RT window, current row order, or label without an owner namespace |
| `TargetListSnapshot`, `ProcessingRequest` | Compared by their complete content; no scalar ID | A path, config hash, or digest standing in for the content |
| `processing_attempt_id` | A fresh UUID4 per execution; a retry links its predecessor through `retry_of` | Request content alone or a reused timestamp |
| `sample_key` | The sample-metadata `sample_name`, unchanged, under the [Sample Metadata contract](sample-metadata-qc.md#contract) | Reconstructing sample identity from acquisition sources or display/projection fields |
| `method_subject_id` | `target\|<label>` or `precursor\|<m/z>\|<ppm>\|<losses>` | A digest of the subject |
| Peak hypothesis key (in process) | Sample, method, measured m/z, apex RT, apex height, trace RT bounds and point count; DDA support appends its scan/loss facts | Target label, selection status, attempt token, or output position |
| `physical_peak_key` (written reports) | Sample, m/z, trace RT bounds and point count, apex RT, apex height; a key collision inside one report is an error | A content digest or a key reused across reports |
| Cross-sample group (in process) | The group object and its member subjects; members join in sample order | An ID persisted beyond the run |
| `feature_id` | Report-scoped `F000001` ordinal over published groups, in partition order and raw measured member order | Group centers, scores, hashes, worker order, or cross-report continuity |

Identifiers are allocated only where a reference crosses a file, table, or
human-facing boundary and no natural key suffices
([Change Locality](../architecture-contract.md#change-locality)). In-process
objects are compared by content and ordered by raw measured facts.
Its current content boundary is:

- normal Targeted and Discovery-to-Alignment acquisition bind the local filesystem revision (device,
  file identity, byte size, modification and creation/change timestamps),
  acquisition format, and adapter-readable observation namespace. Preparation
  does not scan RAW content. The adapter checks the revision before and after
  opening the vendor handle, and the workflow rejects input changes before
  finalization. This identifies a local file revision, not portable byte
  equality: copies are distinct sources and no full-content digest is claimed;
- persisted Discovery sources retain that local revision. Re-analysis of old
  source schemas requires regenerating Discovery. Candidate and physical-facts
  files retain their own boundary checksums; explicit content fingerprinting
  still binds exact bytes where a file consumer requires them;
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
- a processing attempt binds one request, a fresh UUID4, and an optional
  predecessor attempt. Retry always issues a new attempt. Terminal
  outcome and cancellation/failure events remain append-only facts owned by the
  execution-runtime contract and never rewrite identity.
- a written evidence report is located by its path, the Matrix path, and the
  attempt UUID. The reader admits the report schema and checks the Matrix
  SHA-256 stored in the report; the locator carries no duplicate checksum;
- Delta Mass runs detection, matching, and trace confirmation in one pass.
  Its diagnostic CSV/sidecar outputs do not create processing-attempt
  authority;
- canonical samples come from [Sample Metadata](sample-metadata-qc.md).
  Lineage composes owner-resolved references and snapshots rather than deriving
  IDs from display labels.

The Targeted adapter currently names the existing Thermo MS-controller-1
namespace explicitly. A different controller, acquisition adapter, or
observation namespace therefore creates a different source identity rather
than silently reusing a bare scan number. Local source preparation reads file
metadata, not RAW bytes, and opens no vendor RAW handle or XIC call.

### Owner-reference composition

This freezes the acquisition-source -> processing-request ->
processing-attempt boundary plus the sample and target-list owner APIs.
`ProcessingOwnerReferences` composes a complete request and attempt,
owner-resolved sample bindings whose exact lookup tokens cover every request
acquisition input and share one metadata snapshot, and the request-pinned
target-list snapshot. Lineage does not mint sample or target IDs. It rejects
mixed sample snapshots and target/request substitution.

Production workflows and diagnostic tracers consume this owner-reference
closure; it is not a production artifact registry. The processing-identity owner
derives the complete predecessor chain from `ProcessingAttempt.retry_of`.
Cross-attempt runtime dependency reuse remains unimplemented, as described in
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
legacy display labels. Untargeted reports label each published group with a
report-scoped `feature_id`; it is not a cross-run or chemical identity.
Automatic cross-request same/split/merge continuity is not implemented.


## Current bindings and storage limits

- [Targeted application](../../xic_extractor/applications/targeted/application.py)
  snapshots source content and the exact request before acquisition through
  `TargetedApplicationService.execute_application`,
  `snapshot_targeted_production_inputs`, and
  `build_targeted_processing_request_identity`. Its normal output is the
  workbook described by [Run Provenance](run-provenance.md).
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
retention engine exists.

Every retained record also belongs to one lifecycle class:

| Class | Meaning |
| --- | --- |
| essential | Final result, authority closure, and minimum evidence refs needed to explain a current value/null |
| reviewable compact | Queryable machine archive needed for bounded review, including provisional groups and final cell-decision evidence |
| diagnostic heavy | Candidate universes, traces, galleries, and debug payloads retained only for opt-in validation, active review, or failure forensics |
| reconstructable | Derived cache/artifact whose exact source identity, algorithm contract, and rebuild capability remain available |
| compatibility | Legacy schema/projection retained only for a named current consumer and removed when its consumer census reaches zero |

## Failure, Null, And Stale

Keep orthogonal facts on separate axes:

| Axis | Owner | Required distinction |
| --- | --- | --- |
| Scientific observation | Shared kernel | `observed`, `missing`, `absent`, `unassessable`, `ambiguous`, `failed` |
| Execution outcome | Execution runtime | Whether the attempt completed, failed, was cancelled, or produced partial diagnostic state |
| Artifact integrity | Lineage validation | Available and matching, missing, corrupt/hash-mismatched, or incompatible with the requested contract |
| Human review queue | Workflow review | Waiting for review versus a completed review conclusion |

Rules:

- Missing evidence, a failed attempt, or a missing artifact cannot silently
  become scientific `absent` or numeric zero.
- `missing` means no completed scientific evaluation result; `absent` means a
  completed evaluation found no supported hypothesis.
- `unassessable` remains a reviewed evidence conclusion, not a pending-review
  status.
- Staleness is a relationship between an immutable artifact and the request,
  contract, or input identity now being evaluated. Do not mutate the artifact
  to call it fresh again.

## Verification of current identities

[Processing identity tests](../../tests/test_processing_identity.py),
[owner-reference tests](../../tests/test_processing_owner_references.py), and
[target-list identity tests](../../tests/test_target_list_identity.py) exercise
the current identity and binding owners.
