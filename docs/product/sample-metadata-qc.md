# Sample Metadata and QC

Sample metadata and QC describe sample identity, injection order, roles,
batches, exclusions, and instrument/QC context. Sample metadata and QC are
public behavior surfaces because they affect ordering, grouping, review
interpretation, and future activation gates.

## Contract

- `sample_metadata` owns sample identity, injection-order projection, role,
  batch and exclusion inputs. The status index owns lane readiness.
- Within `sample_metadata`, `sample_name` is the owner-stable,
  case-sensitive primary sample key.
- `raw_stem` is a lookup alias only.
- Injection order, role, batch, `matrix_type`, group, and exclusion fields do not
  participate in canonical sample identity.
- Only `xic_extractor.sample_metadata` mints `CanonicalSampleIdentity`. The
  identity is `xic.sample:<sha256>` over an explicit stable owner namespace,
  the identity schema/version, and the primary sample key.
- The namespace must identify the owning study/sample registry. The namespace
  must not be a path, timestamp, or output location.
- Renaming `sample_name` creates a new identity. Changing only a projection
  field does not create a new identity.
- Resolution requires an immutable, content-digested
  `SampleMetadataSnapshot`.
- Snapshot identity includes the namespace and all normalized metadata rows.
  Snapshot identity is row-order neutral.
- Snapshot identity changes on an alias or projection mutation, even when a
  canonical sample identity remains stable.
- Resolution accepts only an exact primary key or an unambiguous current
  `raw_stem` alias.
- A missing, ambiguous, or stale alias-to-key binding fails closed.
- Resolution never normalizes or infers padded tokens, filename suffixes,
  paths, or row order.
- The returned resolution retains the exact lookup token and the owner metadata
  row. A lineage consumer can therefore bind the resolution to a request input
  without reconstructing sample identity.
- Sample metadata can act as an injection-order and sample-identity projection
  surface when sample metadata preserves existing output values.
- `AnalysisSet` uses role and explicit exclusion to choose analysis members;
  other injections remain context rather than analysis inputs.
- Conservative Backfill uses study/QC roles and exclusion to derive its
  eligible support population. The RT frame uses batch membership as cohort
  context.
- Feature Quality uses explicit study-sample and exclusion metadata for the
  availability denominator and a separate analysis-set export. It does not
  mutate the source Matrix.
- Role-based blank subtraction, QC weighting, response correction and
  normalization are not implemented. Metadata alone cannot authorize these
  value transformations; they require an explicit product contract and
  expected-diff review.
- Instrument-QC sidecars are evidence and observability surfaces, not
  authorization to change selected values.
- Sample metadata is part of run provenance. Sample metadata does not replace
  method manifests or output schemas.

### Alignment input

Sample metadata is an explicit Alignment input; it is not inherited from a
previous Discovery invocation. `--sample-metadata` loads the existing metadata
format and passes its batch IDs, injection order, sample roles and exclusions
to the workflow. The CLI reports the source and sample/batch counts before
execution. The request owner retains responsibility for sample coverage.

Without this input, the existing RAW-header/default derivation treats the RAW
directory as one cohort. A flattened directory containing several acquisition
batches therefore needs explicit metadata; it does not acquire separate batch
identities merely because its files were measured on different dates.

## Surfaces

| Surface | Role |
| --- | --- |
| `sample_metadata` CSV/TSV | Shared sample identity, order, role, batch, `matrix_type`, group, exclusion schema |
| `SampleMetadataSnapshot` | Owner-minted content identity for one complete normalized metadata snapshot and stable identity namespace |
| `mint_canonical_sample_identity` / `resolve_canonical_sample_identity` | Owner-only namespaced identity minting and self-validating fail-closed lookup resolution |
| `injection_order_source` | Optional sequence annotations; RAW chronology owns resolved injection order |
| Instrument-QC sidecars | QC context and trend evidence |
| Run metadata / method manifest | Provenance links to sample metadata inputs |

## Boundaries

- Owns: sample identity schema, injection-order projection, role/batch/
  exclusion field definitions, and QC sidecar structure.
- Does not own: a specific sample's role or exclusion decision,
  normalization/calibration activation policy, or matrix activation scope.
- New role-driven value transformations require a separate product contract
  and expected-diff review; existing analysis-membership and support rules
  remain with their current owners.

## Injection timeline diagnostic

`python -m tools.diagnostics.build_injection_timeline` produces an opt-in,
read-only injection timeline JSON sidecar and a readable TSV. CLI help owns
the arguments. `raw_reader` acquires header facts; `injection_timeline` owns
deduplication, chronological previous-injection links, and the pre-flag policy.
This producer does not change sample metadata identity, analysis-set membership,
RT, selected peaks, or values. Evidence is `diagnostic_only`; the
[sample RT calibration diagnostic](../../tools/diagnostics/INDEX.md#build_sample_rt_calibrationpy)
confirms a perturbation from the measured data.

The producer recursively reads all RAW files under the supplied batch root,
including blanks and subfolders. Creation date plus the original vendor
acquisition path identifies copies; all local copy paths remain in JSON.
Conflicting duplicate headers fail rather than silently select a copy. A file's
filesystem timestamps and local basename do not determine injection identity.
`raw_stem` is the local filename stem, the identity used by the rest of the
product; the original acquisition stem is kept as `acquisition_stem`, and
`renamed_after_acquisition` marks files renamed after acquisition.
Creation date is the clock origin; acquisition start/end add the respective
`RunHeaderEx` minute offsets, and run length is end minus start. Idle is current
acquisition start minus previous acquisition end. Genuine negative idle is
preserved with an overlap warning and does not trigger the pre-flag.

The default policy flags an injection when the previous run is at most
20 minutes, at most half the current run length, and idle is between zero and
5 minutes inclusive. All three thresholds are configurable and persisted in
the JSON sidecar. These thresholds describe a possible perturbation, not a
validated method-independent classifier; 25-minute blanks are excluded by
default. Headers with missing acquisition identity/date or invalid run times
fail instead of generating a plausible but incomplete timeline.

Optional repeated `--sld` inputs use Thermo's `SequenceFileReaderFactory`.
Exact original acquisition filenames (Windows case-insensitive) match observed
injections. Segment provenance, missing RAWs, and ambiguous repeat acquisitions
are retained separately; empty filename rows remain `unconfigured`, not missing
injections. Sequence rows never invent timestamps or order the
actual timeline. A missing RAW means absent from the supplied root, not proof
that an injection never occurred. JSON and TSV are diagnostic/offline consumer
surfaces; this command does not add an Inspector UI panel.

## RAW analysis-set preparation

`raw_reader.read_analysis_set` owns the RAW-derived analysis set shared by
Targeted, Untargeted, and their command-line entry points. Injection order comes
from the copy-aware acquisition timeline (`CreationDate` plus `RunHeaderEx`),
not filesystem modification time, directory enumeration, or sequence row numbers.
Sample names come from RAW headers, with local RAW stems as the fallback for
missing or duplicate names or names that collide with another RAW lookup alias.
All RAW aliases are reserved before names are chosen; repeated context-file
stems receive a unique display-name suffix rather than taking another RAW alias.

Word, Excel, CSV, and TSV sequence inputs supply role, group, batch, and exclusion
annotations; they do not rename samples or replace acquisition order. Partial
coverage is allowed. Matching reports unmatched entries in both directions,
duplicate printed sequence numbers, and contradictory sample codes. Unreadable
RAWs that would be analyzed fail with a file list; unreadable excluded or blank
files are omitted with a warning. The workbench displays these conflicts and
omissions. Targeted reports the frozen input's sample checks before extraction
and retains them with the result. Reloading the workbench restores these checks
and the last loaded Untargeted sample summary. Queued Targeted work retains its
frozen sequence and membership.

### Sample groups and exclusions

`sample_groups.resolve_sample_group` is the group decision owner: an explicit
metadata `group` wins; otherwise `qc` and `pooled_qc` roles display `QC`, and
all other roles display `Other`. Sample-name keywords do not assign groups.
Targeted's `Group` column consumes the resolved sample metadata, not an
independent filename classifier.

Group names have surrounding whitespace removed and internal whitespace
collapsed. `sample_group_key` supplies the case-insensitive identity for all
group comparisons; `canonicalize_sample_groups` retains the first encountered
display spelling. Blank and case-insensitive `Other` mean unassigned; `QC` is
also a valid explicit group. `suggest_sample_groups` is an opt-in pure naming
suggestion: it removes the extension, sample codes (one to three capital letters
followed by at least three digits), and a trailing `_N` or `-N` repeat number,
then collapses underscores, hyphens, and whitespace. Suggestions do not change
roles or apply themselves to metadata.

Word, Excel, and ordinary delimited sequence tables share
`sample_metadata.match_sample_metadata`. It uses `normalize_for_matching` and
pairs repeated names by injection order with numerically ordered RAW suffixes
only when that pairing is unambiguous. Reports distinguish matched, unmatched,
and ambiguous names. Word table parsing belongs to `sequence_document`; the
instrument-QC trend tool retains its own matching and classification rules.

Files with a `schema_version` column are persisted sample metadata, not fuzzy
sequence tables. They bind only by case-insensitive **exact `raw_stem`**, never
by normalized sample names or repeat order. Unbound rows are reported unmatched;
their group and exclusion flags must not migrate to a similar RAW name.

Explicit exclusion has the same meaning in complete Targeted and Untargeted
runs: the sample is not analyzed. The shared `AnalysisSet` retains excluded
injections as context, but they are neither extracted nor emitted as analyzed
result rows/columns. Targeted accepts the TSV through `--injection-order-source`;
Untargeted applies it when selecting Discovery inputs and passes the same TSV
through Alignment's `--sample-metadata`. Alignment's CLI passes only analyzed
metadata to its Discovery-batch input. Role inference and the RAW-owned
injection timeline are unchanged.

This input-preparation contract can change resolved order relative to legacy
mtime or document ordering. It is distinct from the no-value-change canonical
identity projection above and requires a real-data expected-diff comparison.
Header/order parity alone is engineering evidence, not quantitative or
scientific readiness.

## Verification

- Schema validation tests for metadata fields and role values.
- Identity tests for deterministic owner minting, alias parity, rename
  invalidation, projection-field neutrality, and missing/ambiguous/stale
  resolution.
- Parity tests proving no value change when metadata is only an
  order/lookup source.
- Expected-diff packet for any role-aware value behavior.
- Downstream matrix/workbook tests if outputs can change.

## Pitfalls

- Treating sample roles as value-changing policy before activation.
- Using exclusions to silently remove product rows or cells.
- Treating QC sidecars or calibration previews as matrix authority.
- Hiding sample identity or batch assumptions only in private notes.

## See Also

- [Run provenance](run-provenance.md)
- [Productization](productization.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index.tsv)
