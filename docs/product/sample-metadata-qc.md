# Sample Metadata and QC

Sample metadata and QC describe sample identity, injection order, roles,
batches, exclusions, and instrument/QC context. They are public behavior
surfaces because they affect ordering, grouping, review interpretation and
future activation gates.

## At A Glance

| Question | Rule | Detail |
| --- | --- | --- |
| What identifies a sample? | Exact, case-sensitive `sample_name`. | [Identity](#identity) |
| Which injections are analyzed? | Targeted: non-excluded RAW; Untargeted: non-excluded `sample`, `qc`. Roles change no value. | [Roles and exclusion](#roles-and-exclusion) |
| Where does injection order come from? | The RAW acquisition timeline. | [Analysis set](#raw-analysis-set-preparation) |
| Does Alignment inherit metadata? | No; without it the RAW directory is one cohort. | [Alignment input](#alignment-input) |

## Contract

### Identity

- Sample metadata owns sample identity, injection-order projection, role,
  batch and exclusion inputs. `sample_name` is the owner-stable,
  case-sensitive primary key; `raw_stem` is a lookup alias only.
- Injection order, role, batch, `matrix_type`, group and exclusion do not
  participate in identity. Renaming `sample_name` creates a new sample;
  changing only a projection field does not.
- Lookup accepts only an exact `sample_name` or an unambiguous current
  `raw_stem` alias. A missing, ambiguous or stale alias fails; identity is never
  inferred from padded tokens, filename suffixes, paths or row order.
- One analysis folder holds one independent canonical sample per RAW source.
  QC replicates and meaningful reinjections keep their full distinct names;
  suffixes are never stripped to collapse them into one specimen.
- Sample metadata can act as an injection-order and identity projection
  surface when it preserves output values. It is part of run provenance and
  does not replace method manifests or output schemas.

### Roles and exclusion

- `sample_role` is one of `sample`, `qc`, `std`, `blank` or `other`; a role is
  a label. Exclusion is the only "do not analyze" control and keeps the role.
  Targeted analyzes every non-excluded RAW; Untargeted analyzes non-excluded
  `sample` and `qc`. Other injections stay context.
- A blank or `unknown` role in a file means "not specified": the RAW header and
  file name decide it, so an ordinary sample becomes `sample` and only a RAW
  nothing identifies becomes `other`. Earlier role names are read as current
  roles
  ([`test_sample_metadata.py`](../../tests/test_sample_metadata.py)); writers
  emit only the five roles.
- Explicit exclusion means not analyzed in either workflow: excluded injections
  stay context but are neither extracted nor emitted as result rows or
  columns. An exclusion needs a reason and applies from the next run; it never
  changes a published result.
- Conservative Backfill derives its eligible support population from
  `sample`/`qc` roles and exclusion. The RT frame uses batch membership as
  cohort context. Matrix curation's finite-value fraction uses explicit
  `sample` role and exclusion for its denominator and writes a new folder
  without mutating the source Matrix.
- Role-based blank subtraction, QC weighting, response correction and
  normalization are not implemented. Metadata alone cannot authorize these
  value transformations; they need an explicit product contract and
  expected-diff review.
- An explicit metadata `group` wins. Otherwise the role decides: `sample`,
  `qc`, `std`, `blank` and `other` display `Sample`, `QC`, `STD`, `Blank` and
  `Other`; no role displays `Other`. Sample names never assign groups.
- Group comparison is case-insensitive after whitespace normalization; blank
  and `Other` mean unassigned.

### Alignment input

Sample metadata is an explicit Alignment input, not inherited from a previous
Discovery invocation. `--sample-metadata` passes its batch IDs, injection
order, roles and exclusions to the workflow, and the CLI reports the source
and sample/batch counts before execution. The request owner keeps
responsibility for sample coverage. Without it, the RAW-header/default
derivation treats the RAW directory as one cohort, so a flattened directory
holding several acquisition batches needs explicit metadata; measuring files
on different dates does not give them separate batches.

## RAW Analysis-Set Preparation

One RAW-derived analysis set is shared by Targeted, Untargeted and their
command-line entry points.

- **Order.** Injection order comes from the copy-aware acquisition timeline
  (`CreationDate` plus `RunHeaderEx`), not filesystem modification time,
  directory enumeration or sequence row numbers. Re-resolving order this way
  can change it relative to file times or documents; such a change needs a
  real-data expected-diff comparison, since header and order parity alone is
  engineering evidence.
- **Names.** Sample names come from RAW headers, with local RAW stems as the
  fallback for missing or duplicate names or names colliding with another RAW
  alias. All aliases are reserved first; repeated context-file stems get a
  unique display suffix rather than another RAW's alias.
- **Sequence inputs.** Word, Excel, CSV and TSV sequence inputs supply role,
  group, batch and exclusion annotations; they do not rename samples or
  replace acquisition order. Partial coverage is allowed. Matching is by
  normalized sample name, pairing repeated names by injection order only when
  unambiguous, and reports matched, unmatched-both-ways and ambiguous names,
  duplicate printed sequence numbers and contradictory sample codes.
- **Persisted metadata.** Files with a `schema_version` column are persisted
  sample metadata, not fuzzy sequence tables; they bind only by
  case-insensitive exact `raw_stem`. Unbound rows stay unmatched, and their
  group and exclusion never migrate to a similar RAW name.
- **Unreadable RAW.** An unreadable RAW that would be analyzed fails with a
  file list; other unreadable RAWs stay listed, without injection order, with a
  warning. A selected file without an MS device blocks analysis with its
  filename and vendor error.
- **Targeted.** Exclusions are resolved before opening RAW; excluded files stay
  listed as unopened context without claimed names or order, and need not
  open. The analyzed population is ordered by the included injections'
  acquired timestamps; consumers needing the full timeline request context
  headers. Targeted reports the frozen input's sample checks before extraction
  and keeps them with the result; queued work keeps its frozen sequence and
  membership.

### Workbench sample step

Targeted and Untargeted share one confirmed sample metadata TSV per RAW folder
under the Workbench config directory; once it exists it is that folder's
metadata source, and edits are validated before writing. Choosing a RAW folder
or another 上機資訊 lists every RAW below it in the background and reads each
header for name, injection order and role; confirmed roles and exclusions win.
Listing decides no membership, so an unreadable RAW is listed with a warning.
The sample editor rereads headers only when RAW were added or removed; without
a RawFileReader folder, roles come from file names. A failed or cancelled
import keeps the previous file. The Workbench shows conflicts and omissions,
and reloading restores them and the last Untargeted sample summary.

## Injection Timeline Diagnostic

A read-only diagnostic builds a batch chronology; its inputs and outputs are in
[its module docstring](../../tools/diagnostics/build_injection_timeline.py). It
does not change sample identity, analysis-set membership, RT, selected peaks or
values.

- Injection identity is the RAW creation date plus the original vendor
  acquisition path, not filesystem timestamps or the local basename. Idle time
  is the current acquisition start minus the previous acquisition end; genuine
  negative idle is kept with an overlap warning.
- The default policy pre-flags an injection when the previous run is at most 20
  minutes, at most half the current run length, and idle is between zero and 5
  minutes inclusive: a short method followed closely by a long one can leave
  the gradient or column unsettled. These thresholds describe a possible
  perturbation, not a validated classifier; the
  [sample RT calibration diagnostic](../../tools/diagnostics/build_sample_rt_calibration.py)
  confirms it from measured data. 25-minute blanks are excluded by default.
- A sequence row never invents a timestamp, and a RAW missing from the supplied
  root is not proof that an injection never occurred.

## Boundaries

- Owns: the sample identity schema, injection-order projection, role, batch
  and exclusion field definitions, and QC sidecar structure.
- Does not own: a specific sample's role or exclusion decision,
  normalization/calibration activation, or Matrix activation scope.

| Surface | Role |
| --- | --- |
| `sample_metadata` CSV/TSV | Shared sample identity, order, role, batch, `matrix_type`, group, exclusion schema |
| `injection_order_source` | Optional sequence annotations; RAW chronology owns resolved injection order |
| Run metadata / method manifest | Provenance links to sample metadata inputs |

## Pitfalls

- Using exclusions to silently remove product rows or cells.
- Treating QC sidecars or calibration previews as Matrix authority.
- Hiding sample identity or batch assumptions only in private notes.

## Verification

[`test_sample_metadata.py`](../../tests/test_sample_metadata.py) ·
[`test_sample_metadata_auto_derivation.py`](../../tests/test_sample_metadata_auto_derivation.py) ·
[`test_sample_metadata_sequence_parsing.py`](../../tests/test_sample_metadata_sequence_parsing.py) ·
[`test_analysis_set.py`](../../tests/test_analysis_set.py) ·
[`test_sample_groups.py`](../../tests/test_sample_groups.py) ·
[`test_targeted_sample_metadata.py`](../../tests/test_targeted_sample_metadata.py) ·
[`test_injection_timeline.py`](../../tests/test_injection_timeline.py).
See also [Targeted workbook](targeted-workbook.md) and the
[product validation contract](../agent/product-validation-contract.md).

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Identity, roles, parsing | `xic_extractor.sample_metadata` (`infer_sample_role`, `parse_sample_metadata`) |
| RAW analysis set (`AnalysisSet`) | `raw_reader.read_analysis_set` |
| Injection timeline | `tools.diagnostics.build_injection_timeline` |
