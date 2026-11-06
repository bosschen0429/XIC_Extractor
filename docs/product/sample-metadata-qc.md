# Sample Metadata and QC

Sample metadata and QC describe sample identity, injection order, roles,
batches, exclusions, and instrument/QC context. Sample metadata and QC are
public behavior surfaces because they affect ordering, grouping, review
interpretation, and future activation gates.

## Contract

- `sample_metadata` owns sample identity, injection-order projection, role,
  batch and exclusion inputs.
- Within `sample_metadata`, `sample_name` is the owner-stable,
  case-sensitive primary sample key.
- `raw_stem` is a lookup alias only.
- Injection order, role, batch, `matrix_type`, group, and exclusion fields do not
  participate in canonical sample identity.
- Renaming `sample_name` creates a new sample identity. Changing only a
  projection field (order, role, batch, group, exclusion) does not.
- Sample lookup accepts only an exact `sample_name` or an unambiguous current
  `raw_stem` alias. A missing, ambiguous or stale alias fails; lookup never
  infers identity from padded tokens, filename suffixes, paths or row order.
- One analysis folder holds one independent canonical sample per RAW source.
  QC replicates and meaningful reinjections keep their full distinct names;
  suffixes are never stripped to collapse them into one specimen.
- Sample metadata can act as an injection-order and sample-identity projection
  surface when sample metadata preserves existing output values.
- `AnalysisSet` uses role and explicit exclusion to choose analysis members;
  other injections remain context rather than analysis inputs.
- Conservative Backfill uses study/QC roles and exclusion to derive its
  eligible support population. The RT frame uses batch membership as cohort
  context.
- Matrix curation's finite-value fraction uses explicit study-sample and
  exclusion metadata for its denominator and writes a new folder. It does not
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

`tools.diagnostics.build_injection_timeline` builds a read-only batch
chronology; its arguments, inputs and outputs are listed in the
[Diagnostics Index](../../tools/diagnostics/INDEX.md#build_injection_timelinepy).
The timeline does not change sample identity, analysis-set membership, RT,
selected peaks or values.

Injection identity comes from the RAW creation date plus the original vendor
acquisition path, not filesystem timestamps or the local basename. Idle time is
the current acquisition start minus the previous acquisition end; genuine
negative idle is kept with an overlap warning.

The default policy pre-flags an injection when the previous run is at most 20
minutes, at most half the current run length, and idle is between zero and 5
minutes inclusive. A short previous method followed closely by a long one can
leave the LC gradient or column state unsettled. These thresholds describe a
possible perturbation, not a validated method-independent classifier; the
[sample RT calibration diagnostic](../../tools/diagnostics/INDEX.md#build_sample_rt_calibrationpy)
confirms it from measured data. 25-minute blanks are excluded by default. A
sequence row never invents a timestamp, and a RAW missing from the supplied root
is not proof that an injection never occurred.

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

Targeted resolves explicit roles and exclusions before opening RAW files. Known
non-analysis files remain listed as unopened context, without pretending to have
acquired their sample names or injection order. Its analyzed population is ordered
by the acquired timestamps of the included injections. Consumers that need the
full injection timeline continue to request context headers. A selected file
without an MS device blocks analysis with its filename and vendor error; an
explicitly excluded file does not need to open successfully.

### Sample groups and exclusions

- An explicit metadata `group` wins. Otherwise the role decides: `study_sample`
  displays `Sample`, `qc` and `pooled_qc` display `QC`, `blank` displays
  `Blank`, other known roles display their role name, and `unknown` displays
  `Other`. Sample names never assign groups.
- Group comparison is case-insensitive after whitespace normalization. Blank
  and `Other` mean unassigned.
- Sequence documents and ordinary delimited tables are matched by normalized
  sample name, pairing repeated names by injection order only when that pairing
  is unambiguous. Matched, unmatched and ambiguous names are reported.
- Files with a `schema_version` column are persisted sample metadata, not fuzzy
  sequence tables. They bind only by case-insensitive exact `raw_stem`.
  Unbound rows stay unmatched; their group and exclusion never migrate to a
  similar RAW name.
- Explicit exclusion means the sample is not analyzed, in both Targeted and
  Untargeted runs. The shared `AnalysisSet` keeps excluded injections as
  context, but they are neither extracted nor emitted as result rows or
  columns. An exclusion needs a reason and applies from the next run; it never
  changes a published result.

### Workbench sample step

Targeted and Untargeted share one confirmed `sample_metadata` TSV per RAW
folder under the workbench config directory. Both flows read and write it, and
once it exists it is that folder's metadata source. Edits are validated by
`parse_sample_metadata` before writing. Previewing samples or importing a
sequence reads no RAW and leaves unmatched roles unknown rather than overriding
a later RAW-header role; real identities and chronology are resolved before
analysis. A failed or cancelled import keeps the previous confirmed file.

This input preparation can change resolved order relative to file modification
time or document ordering. Such a change needs a real-data expected-diff
comparison; header and order parity alone is engineering evidence, not
scientific readiness.

## Verification

- `tests/test_sample_metadata.py`, `tests/test_sample_metadata_auto_derivation.py`,
  `tests/test_sample_metadata_sequence_parsing.py`
- `tests/test_analysis_set.py`, `tests/test_sample_groups.py`,
  `tests/test_targeted_sample_metadata.py`
- `tests/test_injection_timeline.py`

## Pitfalls

- Treating sample roles as value-changing policy before activation.
- Using exclusions to silently remove product rows or cells.
- Treating QC sidecars or calibration previews as matrix authority.
- Hiding sample identity or batch assumptions only in private notes.

## See Also

- [Targeted workbook](targeted-workbook.md)
- [Product validation contract](../agent/product-validation-contract.md)
