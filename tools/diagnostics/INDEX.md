# tools/diagnostics/ — Diagnostic Tool Index

**Governing contracts:** `docs/architecture-contract.md` and
`docs/agent/script-tool-lifecycle.md`.

---

> **For agents and developers opening a new session:** Before creating a new
> diagnostic tool in `tools/diagnostics/`, scan this index first. Most needs
> are already covered by an existing tool or its topic group. If your need
> is genuinely new, list in your PR description which existing entries you
> considered and why they are inadequate. This avoids the "agent A and
> agent B independently solve the same problem with twin tools" failure
> mode.
>
> This index is maintained as part of every PR that adds, removes, or
> renames a diagnostic entry-point. The file is grouped by **purpose**, not
> alphabetically — open the table of contents below to navigate.

## Choose The Workflow

`scripts/` and `tools/` contain different kinds of adapters; their directory
does not determine whether an operation is a user feature. Start with the
workflow owner rather than chaining similarly named commands.

| Task | Entry and owner |
| --- | --- |
| Extract, discover, or align samples | [GUI and CLI user guides](../../docs/user/README.md) |
| Inspect chromatograms | GUI **開啟 Inspector** or `xic-inspector`; [Workbench and review](../../docs/product/workbench.md) owns what review may mean |
| Select Matrix features for downstream analysis | GUI **矩陣整理** or `xic-matrix-curation`; [Matrix Curation](../../docs/product/matrix-curation.md) owns the steps and output |
| Review instrument QC | `scripts.run_instrument_qc`; [Instrument QC](../../docs/product/instrument-qc-calibration.md) owns its inputs and claims |
| Build a solvent-blank background library | `scripts.extract_blank_library`; `xic_extractor.discovery.blank_qualification` owns extraction and serialization |
| Run tissue validation | `scripts.run_tissue_validation`; [execution settings](../../docs/agent/local-execution.md#validation-scale) own prerequisites |
| Query Discovery or experiment with grouping / RT calibration | `scripts.run_alignment --stop-after grouping`, then `tools.diagnostics.grouping_experiment replay`; [Grouping experiments](#grouping_experimentpy) describes the input and output boundary |
| Score one peak quantified in several Matrix rows | `tools.diagnostics.duplicate_quantification --alignment-dir <validation output>`; [Duplicate quantification](#duplicate_quantificationpy) |
| Maintain CI or command safety | `tools.testing.test_selection` and `tools.command_policy` serve CI/hooks; they have no GUI workflow |

The sections below index maintained diagnostics. Retired tools are recovered
from Git history; retained historical evidence does not make them current
commands.

## Table of Contents

1. [Validation Gates](#validation-gates)
2. [Evidence Consistency](#evidence-consistency)
3. [Targeted Benchmarks & Reviews](#targeted-benchmarks--reviews)
4. [Instrument QC](#instrument-qc)
5. [Surface Population And Reachability](#surface-population-and-reachability)

---

## Validation Gates

### `duplicate_quantification.py`

**Purpose**: Count same-sample cells in different Matrix rows whose row
precursor m/z (default ≤10 ppm) and published apex (default ≤3 s) coincide, and
classify each row pair as complementary split, distinct peaks sharing a
Backfill, or duplicate view.
**Topic group**: `duplicate_quantification.py` + package-owned
`xic_extractor.diagnostics.duplicate_quantification`
**Status**: diagnostic_only. Reads the Matrix projection of
`alignment_review.tsv`, the compact `alignment_backfill_cell_evidence.tsv` (or `alignment_cells.tsv`
for `debug`) and `alignment_explanation.sqlite` from a
`validation-minimal` or richer output. Does not open RAW or change Matrix values;
a screening count, not chemical identity.

Pipeline-tier gates that compare candidate configurations against current
production, or summarize cost/parity for production decisions.

### Tissue mainline validation launch

**Entry point:** `python -m scripts.run_tissue_validation --dataset {8,85}`.
**Role:** maintained operator interface / evidence producer.
One launch configuration binds the tissue subset or full cohort to the current
Discovery and Alignment CLIs. `--preflight-only` prints resolved defaults
without RAW acquisition. It does not determine scientific readiness.
See [Local Execution](../../docs/agent/local-execution.md#validation-scale)
and [Presets](../../docs/product/presets.md#tissue-validation-defaults).

AsLS is the production baseline integration method. Diagnostics use the current
AsLS/boundary/product contracts, not linear-edge comparisons or fallbacks.

---

## Evidence Consistency

Compare evidence rows produced by parallel pipelines (targeted vs untargeted,
spine vs cell) and shared-identity sidecars. The tools below share low-level
diagnostic IO helpers where their schemas permit it, but keep separate row
models because their output schemas are intentionally different.

---

### `delta_mass_review.py` — [Delta Mass review](../../docs/product/discovery.md#read-only-candidate-review)

### `explain_cid_nl_run.py`

**Purpose**: Read the normal CID-NL execution explanation artifact and return a
compact summary or exact candidate details without reading RAW.
**Owner**: [Retained evidence](../../docs/product/workbench.md#retained-evidence-and-diagnostics); arguments in `--help`.
**Input**: `alignment_explanation.sqlite`, optionally an exact candidate ID.
**Output**: Readable text or JSON; `--compact-to` writes compact review from an
existing full report; `--curve-bundle` writes one candidate's or group's review
curves for Inspector, saved where the run kept them and read from RAW otherwise.
**Status**: Maintained diagnostic workflow. Retains owner assessment, initial
CrossSample and final disposition facts; does not recalculate decisions or grant
Matrix authority. Existing `alignment_health_packet.py` summarizes machine-level
health; it does not contain excluded candidate and initial weak-edge facts from
default runs.

---

CLI reports and audits over alignment outputs (cells, families, owner
decisions, RT calibration).


### `alignment_health_packet.py`

**Purpose**: Build a no-RAW alignment health packet from existing alignment
outputs so repeated 8RAW/FULL COHORT validation can quickly surface owner ambiguity,
backfill dependency, not-searched pressure, and primary-consolidation sentinel
families.
**Topic group**: `alignment_health_packet.py` (single-file)
**Status note**: Writes `alignment_health_summary.json`,
`alignment_health_summary.tsv`, and `alignment_health_family_sentinels.tsv`.
It reads `alignment_review.tsv` (including its Matrix projection),
`alignment_matrix.tsv`, and optional `alignment_backfill_cell_evidence.tsv`
or `alignment_cells.tsv`.
It is `diagnostic_only`: it does not read RAW, recompute evidence, change
selected peaks/areas/counting, alter matrix identity, or grant product
authority.

---

### `build_sample_rt_calibration.py`

- **Role:** evidence producer; maintained operator interface.
- **Owner:** `xic_extractor.alignment.sample_rt_calibration`; the model and evidence scope are described below.
- **Use:** `python -m tools.diagnostics.build_sample_rt_calibration --help`.
- **Outputs:** calibration JSON, sample TSV, and held-out TSV from
  `alignment_cells.tsv` plus an optional injection timeline JSON. It does not
  read RAW.
- **Authority:** `diagnostic_only`; no grouping, selection, RT, or value
  authority. The diagnostic calls the same owner as formal batch calibration.

`python -m tools.diagnostics.build_sample_rt_calibration` places every sample
on a common RT coordinate. CLI help owns the arguments. Anchors are
`alignment_cells.tsv` features with `detected` status in at least three samples;
backfilled or inferred cells never become anchors. This CLI treats its input as
one cohort. Formal runtime supplies declared batch cohorts and measured ISTDs
to the same `alignment.sample_rt_calibration` owner, which has no RAW, file, or
Matrix access.

- Exact duplicate sample/RT patterns contribute once; repeated observed RT
  positions receive one median reference vote. No fixed RT grid or near-RT
  merge tolerance is fitted in parallel.
- Robust local-linear regression fits each sample against correspondence
  medians, with the CLI's `--loess-fraction`. The frozen result must increase
  strictly on both axes. A calibrated RT exists only inside that sample's
  supported anchor span. Strong fits require at least eight distinct measured
  positions with nonzero final robust weight. The v3 sidecar also freezes
  qualified measured support for explicitly weak median/boundary-shift queries;
  these do not upgrade strong support. See the
  [graded RT contract](../../docs/product/rt-calibration.md#graded-query-support)
  for final membership and localization behavior.
- Measured data decides a perturbation; the
  [injection timeline](../../docs/product/sample-metadata-qc.md#injection-timeline-diagnostic)
  explains it. The front shift is the median shift at the batch's earliest
  knots. It is an outlier when it differs from the batch median, in either
  direction, by more than max(k × batch MAD, a minimum shift). An outlier is
  `confirmed` when the sample is pre-flagged, follows a short method, or is the
  first full-length run after a method change. A short method uses the
  timeline's persisted run-length limits without the idle limit, because a
  short method can leave the gradient unsettled after a long idle. A method
  change compares the instrument method with the latest earlier run longer than
  the persisted length fraction, skipping blanks and SDO/LEK, whose own methods
  always differ. Any other outlier is `unexplained_outlier`, such as a matrix
  effect, and one without a timeline row is `outlier_without_timeline`.
  A pre-flagged sample within tolerance is `flagged_unconfirmed`; a missing
  front knot is `unmeasured`. Sample rows carry the previous injection, its run
  length, the idle time, and the compared full-length run. Confirmation does not remove a sample's own
  calibration; no current consumer reads the status.
- Held-out validation first deduplicates exact acquired patterns, reserves every
  fifth feature in median-RT order, fits the
  remaining features separately, and compares raw/calibrated dispersion on the
  same supported held-out observations. The output map is fitted to all input
  anchors; it is not the model used to score its own held-out rows.

Outputs are a JSON sidecar with the policy, knots, per-band knot counts, and
per-sample knots, plus sample and held-out TSVs. Evidence is `diagnostic_only`.
The diagnostic does not change grouping, peak selection, drift-corrected edge
scoring, or values.

---

## Targeted Benchmarks & Reviews

### `targeted_validation.py`

**Purpose**: Run Targeted real-data validation suites and workbook comparisons.
**Topic group**: `targeted_validation.py` + `_suites`, `_run` (3 files)
**Status note**: Tissue suites and the parallel benchmark share `_run`, which
executes the canonical finalized Targeted application through publication.
Manual-2RAW delegates to the separate manual measurement calibration tool;
its acquisition RT/area comparison is not full application acceptance.

---

### `targeted_parallel_benchmark.py`

**Purpose**: Compare serial/process scheduling for complete Targeted application runs.
**Topic group**: `targeted_parallel_benchmark.py` (single-file)
**Status note**: Uses the shared Targeted validation run owner and compares its
published workbooks. Elapsed time includes finalization and publication, not
only acquisition.

---

### `targeted_manual_validation.py`

**Purpose**: Compare acquired peak RT/area against supplied manual measurements.
**Topic group**: `targeted_manual_validation.py` (single-file)
**Status note**: Peak identity check against an external manual reference:
PASS requires every manual peak and ISTD to be found. Area and RT deviations
are listed for review, not judged. It does not substitute for finalized
Targeted workbook validation.

---

### `compare_workbooks.py`

**Purpose**: Compare all workbook sheets with an explicit numeric tolerance.
**Topic group**: `compare_workbooks.py` (single-file)
**Status note**: Shared workbook regression oracle for Targeted validation and
operator comparisons. Dynamic metadata handling belongs to this comparator.

---

### `targeted_istd_benchmark.py`

**Purpose**: Run strict targeted ISTD benchmark for untargeted alignment.
**Topic group**: `targeted_istd_benchmark.py` + `_loaders`, `_matching`, `_models`, `_stats`, `_summary`, `_writers` (7 files; largest group)
**Status note**: Reads targeted workbook inputs plus existing alignment review/cell/public-matrix artifacts and writes benchmark summary, match, JSON, and Markdown outputs. Public `alignment_matrix.tsv` loading supports both legacy `feature_family_id` matrices and clean `Mz` / `RT` / sample-column matrices bound through the `matrix_row_index` projection of `alignment_review.tsv`; matrix sample-column normalization is computed once per matrix read and reused across row expansion. This benchmark remains validation-only context and does not mutate alignment artifacts, workbook schemas, matrix identity, or production decisions.

---

### `check_reference_consistency.py`

**Purpose**: Review Targeted input reference RT consistency across methods and
within RAW peak-cluster positions, without changing inputs or selection.
**Topic group**: `xic_extractor.diagnostics.reference_consistency` owns pure
mapping and cluster checks; `reference_consistency_io` reuses target config and
RAW acquisition owners.
**Exposure / lifecycle**: maintained operator interface / diagnostic oracle.
**Usage**: `python -m tools.diagnostics.check_reference_consistency --help`.
An explicit JSON manifest contains `groups`, each with `name`, `targets_csv`,
and optional `raw_files`. Relative paths resolve against the manifest directory.
Use `--preflight-only` before RAW acquisition. Requests deduplicate m/z and ppm
within each RAW; each RAW opens once. Outputs are `reference_consistency.json`
(including leave-one-out errors, policy, input hashes and RAW identities) and
`reference_review.tsv`. Mapping-only warnings are informational; RAW multi-cluster
conflicts can be high. Single-group runs skip mapping. Retain while Targeted
reference input review needs this oracle; no matrix or reference-writing authority.

## Instrument QC

### `build_injection_timeline.py`

- **Role:** evidence producer; maintained operator interface.
- **Owner:** `xic_extractor.injection_timeline`, with RAW/SLD acquisition in
  `xic_extractor.raw_reader`; contract in
  [`sample-metadata-qc.md`](../../docs/product/sample-metadata-qc.md#injection-timeline-diagnostic).
- **Use:** `python -m tools.diagnostics.build_injection_timeline --help`.
- **Outputs:** injection timeline JSON and TSV with recursive chronology,
  copy provenance, previous injection/idle, persisted perturbation policy, and
  optional sequence comparisons. No spectra are acquired; each discovered RAW
  is opened once for header facts, without content hashing or acquisition cache.
- **Inputs:** recursively reads every RAW under the batch root, including
  blanks and subfolders. Creation date plus the original vendor acquisition
  path identifies copies, and every local copy path stays in the JSON;
  conflicting duplicate headers fail. `raw_stem` is the local filename stem,
  `acquisition_stem` the original one, and `renamed_after_acquisition` marks a
  rename. Start and end add the `RunHeaderEx` minute offsets to the creation
  date. Headers missing acquisition identity, date or valid run times fail.
  Optional repeated `--sld` inputs use Thermo's `SequenceFileReaderFactory`;
  exact original filenames (case-insensitive) match injections, and missing
  RAWs, ambiguous repeats and empty filename rows (`unconfigured`) stay
  separate. All three pre-flag thresholds are configurable and persisted.
- **Authority:** `diagnostic_only`; no selection, RT, value, or analysis-set
  authority. Keep as the batch chronology producer for offline diagnostics;
  retire if run metadata adopts the same producer and sidecar contract.

The Instrument QC suite is a coherent product surface; its entry-points
share the `instrument_qc_*` prefix. Per the lifecycle spec, several of
these are candidates for "seasonal cadence" declarations (calibration runs
are not daily).

### `instrument_qc_sequence_manifest.py`

**Purpose**: Build docs-derived instrument QC sequence manifest.
**Status note**: The `run_instrument_qc.py --method-doc` flow writes the sequence manifest, legacy `instrument_qc_injection_order.csv`, and additive `instrument_qc_sample_metadata.tsv` using `sample_metadata`. The sample-metadata sidecar projects matched RAW rows and raw-dir-only instrument-QC rows as metadata only; roles do not alter instrument-QC trend values or matrix outputs.

---

### `instrument_qc_sdolek_calibration.py`

**Purpose**: Calibrate SDO/LEK instrument trends with method-doc order.

---

## Surface Population And Reachability

Report-only census of product surfaces that exist but never carry anything: a
subsystem that never runs is otherwise indistinguishable from one that ran and
found nothing. Findings are triage evidence, not defects; classify each as
expected-empty, optional, dead or broken before promoting any of them to a
failing check.

### `production_reachability_census.py`

**Entry point:** `python -m tools.diagnostics.production_reachability_census [--output-tsv PATH]`.
**Role:** maintained operator interface / diagnostic oracle.
**Purpose**: List functions, classes and methods defined under `xic_extractor/`
that no code in `xic_extractor/`, `xic_workbench/`, `scripts/` or `xic_inspector/` loads
outside their own body, and that no `[project.scripts]` entry point names.
Tests, `tools/`, imports and `__all__` strings do not count as referrers.
**Status note**: Static and name-based; uses of an import alias count for the
original name. A same-named symbol elsewhere hides a dead one, and names reached
only through `getattr` or string tables are reported. It reads source only and
always exits 0.

### `output_population_census.py`

**Entry point:** `python -m tools.diagnostics.output_population_census OUTPUT_DIR [--output-tsv PATH] [--require-populated ARTIFACT[:COLUMN]]` (repeatable).
**Role:** maintained operator interface / diagnostic oracle.
**Purpose**: For each `.tsv`/`.csv` file directly inside a run output
directory, report a file with no data rows, a file with exactly one data row,
and each column holding one value on every row of a multi-row file.
**Status note**: Reads existing outputs only; no RAW access, no SQLite
payloads, no writes to the run directory. It cannot see in-memory fields that
are never published. Report-only exits 0 on a readable directory. A declared
requirement exits 1 when its artifact is absent or header-only, its column is
absent, or every row carries a default value. The ordinary test suite runs a
live synthetic Backfill fixture with this check, including a header-only
failure probe; use a fixture known to exercise Backfill when requiring its
evidence sidecar.

---

### `grouping_experiment.py`

**Purpose:** Query arbitrary measured-mass ranges in saved Discovery and replay
a formal grouping snapshot with parameter, RT coordinate or code changes without
RAW or Backfill.

**Owner:** `scripts.run_alignment --stop-after grouping` writes the snapshot
through the formal Alignment application (same config, ISTD reference,
assessment, calibration and grouping as a full run);
`xic_extractor.alignment.grouping_experiment` delegates replay to
the formal grouping owner. Snapshot and table I/O live in
`xic_extractor.diagnostics.grouping_experiment_io`.

**Usage and artifacts:** CLI help owns the arguments. Results stay under ignored task-specific `output/` directories.

`python -m tools.diagnostics.grouping_experiment` provides a developer feedback
loop over saved Discovery candidates for scalar CID-NL presets:

```powershell
.venv/Scripts/python.exe -m tools.diagnostics.grouping_experiment query --discovery-batch-index <index.csv> --mz-range 242.12:242.13 --output-dir output/query
.venv/Scripts/python.exe -m scripts.run_alignment --stop-after grouping <usual Alignment arguments> --output-dir output/grouping
.venv/Scripts/python.exe -m tools.diagnostics.grouping_experiment replay --inputs output/grouping/grouping-inputs.json.gz --set preferred_rt_sec=30 --output-dir output/grouping-tight
.venv/Scripts/python.exe -m tools.diagnostics.grouping_experiment replay --inputs output/grouping/grouping-inputs.json.gz --calibrate --output-dir output/grouping-calibrated
```

Replay reads retained snapshots that still carry the retired owner
`identity_conflict` flag and ignores it; production never set it.

Repeat `--mz-range LOWER:UPPER` for several regions. Query reads Discovery only
and reports measured-mass candidates plus every sample, including zero-match
samples. A query is a lookup in the saved Discovery population, not a fresh
extraction at that mass.

`--stop-after grouping` retains every partition of the batch, runs the formal
initial assessment, RT calibration and grouping, writes
`grouping-inputs.json.gz` into the output directory and stops before Backfill
and publication. It requires the matching RAW files and current Discovery
owner-closure artifacts, and checks that replaying the written snapshot equals
the formal grouping.

The compressed `grouping-inputs.json.gz` retains scalar observations, exact
member identities, frozen batch calibration, compact elution contexts, local peak-shape
descriptors and configuration. It omits full chromatogram arrays. Older snapshots
without shape descriptors cannot test shape-aware membership; a new
`--stop-after grouping` snapshot supplies them through the formal owner adapter.
Backfill acquisition windows are not grouping inputs. The snapshot reader also
accepts retained snapshots carrying that extra metadata, without using it for
membership; new snapshots omit it.
Replay needs only that snapshot, with no RAW, DLL, acquisition or Backfill.
`--compare-to <previous replay dir>` compares group membership with another
replay of the same snapshot, such as one produced by another commit, and writes
`membership-diff.tsv` (merges and splits with their apex RT gap and samples).
`--set FIELD=JSON` changes an `AlignmentConfig` value; only fields consumed by
grouping affect replay. Owner detection and integration changes require a new
snapshot.

`--calibrate` calls the formal sample RT calibration owner on established
baseline groups with at least three measured members. `--loess-fraction` controls
its local regression neighborhood; `--rt-calibration` instead supplies an
existing `sample_rt_calibration` JSON. Replay preserves the map's measured
support and explicit weak estimates without promoting them to strong support.
Calibration does not change measured RT or area. Historical frozen offset maps
remain readable without refitting them.

`groups.tsv` and `members.tsv` report grouping and observed/comparison RT.
When a final decision is recorded, `comparison_rt` uses that decision's RT,
including weak estimates or whole-universe raw fallback. `comparison_rt_basis`
identifies the selected frame. Without a recorded decision, the RT remains the
available coordinate projection and the basis is `not_recorded`; it does not
claim that grouping used that coordinate.

`decisions.tsv` exposes the grouping decisions when present, including optional
`accepted_risk_json` with selected/alternative configurations and sample risk
facts. An empty field means no recorded risk decision. `changes.tsv`
compares each observation's membership and disposition with baseline;
`samples.tsv` retains zero-observation samples. `summary.json` records counts,
parameters and elapsed time. These are initial-grouping experiment results;
the full workflow remains the owner of final Backfill and Matrix output.

**Status:** Maintained scalar CID-NL developer workflow. Existing RT calibration
tools produce coordinates; this tool makes those coordinates consumable by
grouping replay. Initial-grouping output does not include final Backfill results.

## Shared Infrastructure

Not entry-points, but referenced by multiple topic groups:

- `xic_extractor/tabular_io.py` — package-neutral shared delimited/TSV read-write, file SHA256 hashing, scalar parsing, numeric equality, header validation, label splitting, text grouping, and value formatting. Cluster 1, the listed Cluster 3 loaders, alignment backfill authority/projection modules, and backfill/standard-peak diagnostics now reuse this helper; use it before adding local `_read_required_tsv`, `_bool_value`, `_optional_float`, `_text`, `_required_indexes`, `_write_tsv`, `_sha256_file`, `_numeric_equal`, or `_group_by_family` copies.

Large diagnostics refactor reviews should use the `code-review` workflow with a
fixed base and head. Start from high blast-radius helpers and public contracts:
shared tabular/overlay helpers, matrix identity, activation decisions, value
deltas, output schemas, RAW access locality/reuse, and diagnostic-vs-production
claims. Treat representative writer parity and focused tests as stronger
evidence than an unactionable line-by-line pass over mechanical writer churn.

## Maintenance Notes

- **Adding a new entry-point**: Append it to the matching group section, or
  create a new group if none fit. Populate Purpose / Topic group and add a
  Status note for current constraints or governing owners.
- **Adding a new evidence-provider diagnostic**: The owning Issue names the
  package owner, reusable helper, evidence role, RAW/TSV call-cost model, public
  contract risk, validation tier, and stop rule. HCD-PI, Delta Mass, CID-NL, RT/iRT, MS1
  pattern, shape, standards, library, and future model evidence should enter as
  evidence providers rather than direct matrix writers.
- **Removing an entry-point** (RETIRED per spec): Remove the entry from the
  section and add a one-line tombstone in the deletion PR. Do not leave
  stale entries.
- **Group recategorization**: If a tool migrates between groups, update
  both old and new sections' tool counts in the Table of Contents.
- **Regeneration**: This file can be partially regenerated by grepping
  `description=` and `__doc__` over `tools/diagnostics/*.py` entry-points,
  but cross-references to specs/plans require human curation.
