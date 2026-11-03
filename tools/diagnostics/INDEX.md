# tools/diagnostics/ — Diagnostic Tool Index

**Governing contracts:** `docs/architecture-contract.md` and
`docs/agent/script-tool-lifecycle.md`.
**Provenance note:** `Originating spec/plan`, `Recent doc`, and similar fields
are navigation labels, not current source-of-truth links or retention reasons.
Current governing owners live
in the entry status text, `docs/product/`, `docs/architecture-contract.md`,
`docs/lc-msms-evidence-rules.md`, and validation/control-plane artifacts.

---

> **For agents and developers opening a new session:** Before creating a new
> diagnostic tool in `tools/diagnostics/`, scan this index first. Most needs
> are already covered by an existing tool or its topic group. If your need
> is genuinely new, list in your PR description which existing entries you
> considered and why they are inadequate. This avoids the "agent A and
> agent B independently solve the same problem with twin tools" failure
> mode that the 2026-05-26 audit documented.
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
| Inspect chromatograms | GUI **開啟 Inspector** or `xic-inspector`; [Inspector](../../docs/product/xic-inspector.md) owns offline review |
| Select Matrix features for downstream analysis | GUI **矩陣整理** or `xic-matrix-curation`; [Matrix Curation](../../docs/product/matrix-curation.md) owns the steps and output |
| Review instrument/ISTD drift | `scripts.run_instrument_qc` and `scripts.add_istd_rt_trend`; [Instrument QC](../../docs/product/instrument-qc-calibration.md) owns their distinct inputs and claims |
| Build a solvent-blank background library | `scripts.extract_blank_library`; `xic_extractor.discovery.blank_qualification` owns extraction and serialization |
| Run tissue validation | `scripts.run_tissue_validation`; [execution settings](../../docs/agent/parameter-settings.md#validation-scale) own prerequisites |
| Query Discovery or experiment with grouping / RT calibration | `scripts.run_alignment --stop-after grouping`, then `tools.diagnostics.grouping_experiment replay`; [Grouping experiments](#grouping_experimentpy) describes the input and output boundary |
| Score one peak quantified in several Matrix rows | `tools.diagnostics.duplicate_quantification --alignment-dir <validation output>`; [Duplicate quantification](#duplicate_quantificationpy) |
| Maintain CI or command safety | `tools.testing.test_selection` and `tools.command_policy` serve CI/hooks; they have no GUI workflow |

The sections below index maintained diagnostics. Retired tools are recovered
from Git history; retained historical evidence does not make them current
commands.

## Table of Contents

1. [Validation Gates](#validation-gates)
2. [Evidence Consistency](#evidence-consistency)
3. [Backfill Reviews](#backfill-reviews)
4. [Peak / Candidate Audits](#peak--candidate-audits)
5. [Targeted Benchmarks & Reviews](#targeted-benchmarks--reviews)
6. [Instrument QC](#instrument-qc)
7. [Peak-Group / Overlay Visualization](#peak-group--overlay-visualization)
9. [Agent / Docs Workflow Guards](#agent--docs-workflow-guards)
10. [Surface Population And Reachability](#surface-population-and-reachability)

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
for `validation`/`debug`) and `cid_nl_explanation.sqlite` from a
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
See [Local Execution](../../docs/agent/parameter-settings.md#validation-scale)
and [Presets](../../docs/product/presets.md#tissue-validation-defaults).

Maintenance note: P2/P2b/P2c AsLS-vs-linear-edge gates were retired after the
baseline decision closed. AsLS is the current production baseline integration
method, and `linear_edge` is retired as a selectable baseline method. Do not add
new diagnostics that compare against or fall back to linear-edge; use current
AsLS/boundary/product contracts instead.

---

### `backfill_evidence_cost_summary.py`

**Purpose**: Summarize backfill evidence cost and correctness outcomes.
**Topic group**: `backfill_evidence_cost_summary.py`
**Originating spec/plan**: `retired-provenance:ad226e2eb39a`, `retired-provenance:10eb03817ff8`

---

## Evidence Consistency

Compare evidence rows produced by parallel pipelines (targeted vs untargeted,
spine vs cell) and shared-identity sidecars. The tools below share low-level
diagnostic IO helpers where their schemas permit it, but keep separate row
models because their output schemas are intentionally different; see
`retired-provenance:9ac1510742f0` Cluster 1.

---

### `explain_cid_nl_run.py`

**Purpose**: Read the normal CID-NL execution explanation artifact and return a
compact summary or exact candidate details without reading RAW.
**Owner**: [CID-NL execution explanations](../../docs/product/xic-inspector.md#cid-nl-execution-explanations).
**Input**: `cid_nl_explanation.sqlite`, optionally an exact candidate ID.
**Output**: Readable text or JSON; `--compact-to` writes compact review from an
existing full report; `--curve-bundle` acquires only explicitly selected windows
for Inspector. [Retention and curve export](../../docs/product/xic-inspector.md#lightweight-alignment-review).
**Status**: Maintained diagnostic workflow. Retains owner assessment, initial
CrossSample and final disposition facts; does not recalculate decisions or grant
Matrix authority. Existing `alignment_health_packet.py` summarizes machine-level
health; it does not contain excluded candidate and initial weak-edge facts from
default runs.

---

CLI reports and audits over alignment outputs (cells, families, owner
decisions, RT calibration, matrix identity).


### `targeted_gt_alignment_audit.py`

**Purpose**: Audit untargeted alignment against targeted workbook GT.
**Topic group**: `targeted_gt_alignment_audit.py` + `_io`, `_models`, `_analysis`, `_writers`, `_utils` (6 files)
**Originating spec**: `retired-provenance:038bfb65fe90` + Workstream G in `retired-provenance:100307efc8fa`

---

### `analyze_matrix_identity_blast_radius.py`

**Purpose**: Analyze matrix identity blast radius for alignment outputs, including projected machine-decision role/action columns from existing review and cell artifacts.
**Topic group**: `analyze_matrix_identity_blast_radius.py` (single-file)
**Originating spec**: `retired-provenance:d35acbb67c99`; machine-decision projection columns from `retired-provenance:358af6f258e2`
**Recent doc**: `retired-provenance:02679bb817de`; `retired-provenance:bcaa7dd85d0f`; `retired-provenance:cff688a35421`
**Status note**: Machine-decision columns are `diagnostic_only` projection output, not a downstream `alignment_matrix.tsv` contract.
**Cleanup note**: Blast-radius input assembly now builds the alignment matrix,
current-review lookup, and per-family cell-row groups together, preserving
cluster order, sorted sample order, row grouping order, TSV/JSON schemas, and
benchmark join behavior.

---

### `alignment_health_packet.py`

**Purpose**: Build a no-RAW alignment health packet from existing alignment
outputs so repeated 8RAW/FULL COHORT validation can quickly surface owner ambiguity,
backfill dependency, unchecked pressure, and primary-consolidation sentinel
families.
**Topic group**: `alignment_health_packet.py` (single-file)
**Status note**: Writes `alignment_health_summary.json`,
`alignment_health_summary.tsv`, and `alignment_health_family_sentinels.tsv`.
It reads `alignment_review.tsv` (including its Matrix projection),
`alignment_matrix.tsv`, optional `alignment_backfill_cell_evidence.tsv`
or `alignment_cells.tsv`, and optional `alignment_owner_backfill_seed_audit.tsv`.
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
  [graded RT contract](../../docs/product/alignment.md#current-implementation)
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

## Backfill Reviews

Four Backfill review tools: a Parquet shadow bundle of the Backfill evidence
TSV projections, a `diagnostic_only` evidence-gate sidecar for product-retained
Backfill, a fast-mode expected-diff packet against exact `dna_dr_product_ready`
output, and a PeakHypothesis Backfill transfer-readiness summary.

### `backfill_evidence_parquet_shadow.py`

**Purpose**: Build a post-run, opt-in Parquet shadow bundle for the existing
owner-readable Backfill and skipped-evidence TSV projections.
**Topic group**: `backfill_evidence_parquet_shadow.py` +
`xic_extractor.alignment.backfill_evidence_{parquet,parquet_bundle}`.
**Originating issue**: [#200](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/200).
**Status note**: `diagnostic_only`; this command does not run Alignment or RAW,
does not register a console script, and does not change TSV schemas, paths,
defaults, Matrix values, or artifact authority. The manifest binds source and
Parquet bytes, row counts, and relative locators. Owner claims still come from
the shared Backfill evidence parser; canonical group assignments and public
compatibility-label bindings are reported separately, formula-protected machine
IDs must resolve unambiguously against owner bindings, and a missing lookup
remains unavailable.

---

### `retained_backfill_evidence_gate.py`

**Purpose**: Emit a `diagnostic_only` machine sidecar for product-retained backfill family/seed groups, linking actual primary-matrix backfill behavior to seed provenance, MS1 overlay support/blockers, missing evidence, and source artifact hashes.
**Topic group**: `retained_backfill_evidence_gate.py` + `xic_extractor/diagnostics/retained_backfill_evidence_gate.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `alignment_retained_backfill_evidence_gate.tsv`, `alignment_retained_backfill_evidence_gate.json`, `alignment_retained_backfill_missing_overlay_queue.tsv`, and `alignment_retained_backfill_overlay_review_queue.tsv`. It consumes existing alignment review/cell/matrix, optional owner backfill seed audit, and optional overlay summary TSVs only. `detected=0` families are excluded from main rows and counted separately. Exact `seed_group_id` overlay rows are required for seed-specific MS1 support/blocker decisions; legacy overlay rows without `seed_group_id` are retained only as family context and are re-queued as `missing_seed_specific_overlay`. Missing-overlay rows with seed provenance are emitted as review queues; the full missing-overlay queue and family-deduplicated review queue share one ordered candidate list during output writing. It does not accept RAW/DLL paths, does not generate overlays, does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbook schemas, or product decisions, and does not declare production readiness.

---


### `backfill_peakhypothesis_transfer_readiness.py`

**Purpose**: Summarize transfer readiness for a reviewed PeakHypothesis backfill
promotion slice by joining the promotion summary, 8RAW/current-writer activation
acceptance, FULL COHORT current-writer artifact contract, optional FULL COHORT slice gate
summary, optional FULL COHORT hypothesis manual-review summary, and optional FULL COHORT
winner-remap proposal summary.
**Topic group**: `backfill_peakhypothesis_transfer_readiness.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_transfer_readiness.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_transfer_readiness.tsv` and
`backfill_peakhypothesis_transfer_readiness_summary.json`. The gate is a
decision surface, not a matrix writer: it does not read RAW, apply activation,
mutate alignment artifacts, or change workbook schemas. A pass through its hard
checks can report `readiness_label=production_candidate` when the 8RAW matrix
diff acceptance passes and the FULL COHORT artifact refresh uses the canonical
current-writer contract. With `--full-cohort-acceptance-gate-summary-json`, a failing
slice gate becomes a hard blocker such as
`full_cohort_slice_specific_no_regression_failed`; a missing slice gate remains
`full_cohort_acceptance_gate_status=not_assessed`. A hypothesis-anchor partial gate is
surfaced as `full_cohort_acceptance_gate_hypothesis_candidate_review_count` with
`next_action=review_full_cohort_hypothesis_candidates_before_product_transfer`. When
`--full_cohort-hypothesis-review-summary-json` shows every review candidate was
manually accepted as same-peak evidence, the partial gate no longer counts as a
hard peak-shape failure; readiness records
`full_cohort_peak_shape_review_status=manual_same_peak_supported_all_review_candidates`
and moves the remaining blocker to `full_cohort_consolidation_policy_not_productized`.
Winner-remap summaries are optional legacy context and no longer override the
hypothesis-anchor next action. It deliberately keeps `production_ready=FALSE`
until an explicit product-transfer decision and consolidation policy exist.

---

## Peak / Candidate Audits

---

## Targeted Benchmarks & Reviews

### `targeted_istd_benchmark.py`

**Purpose**: Run strict targeted ISTD benchmark for untargeted alignment.
**Topic group**: `targeted_istd_benchmark.py` + `_loaders`, `_matching`, `_models`, `_stats`, `_summary`, `_writers` (7 files; largest group)
**Originating spec**: `retired-provenance:53a763b9ec7a`
**Recent doc**: `retired-provenance:5162156a9766`
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
- **Authority:** `diagnostic_only`; no selection, RT, value, or analysis-set
  authority. Keep as the batch chronology producer for offline diagnostics;
  retire if run metadata adopts the same producer and sidecar contract.

The Instrument QC suite is a coherent product surface; its entry-points
share the `instrument_qc_*` prefix. Per the lifecycle spec, several of
these are candidates for "seasonal cadence" declarations (calibration runs
are not daily).

### `instrument_qc_sequence_manifest.py`

**Purpose**: Build docs-derived instrument QC sequence manifest.
**Originating spec**: `retired-provenance:9b1b633f6f09`
**Status note**: The `run_instrument_qc.py --method-doc` flow writes the sequence manifest, legacy `instrument_qc_injection_order.csv`, and additive `instrument_qc_sample_metadata.tsv` using `sample_metadata`. The sample-metadata sidecar projects matched RAW rows and raw-dir-only instrument-QC rows as metadata only; roles do not alter instrument-QC trend values or matrix outputs.

---

### `instrument_qc_sdolek_calibration.py`

**Purpose**: Calibrate SDO/LEK instrument trends with method-doc order.
**Originating spec**: `retired-provenance:77f7869271f4`
**Recent doc**: `retired-provenance:b15c365ea97c`

---

## Peak-Group / Overlay Visualization

New human-facing chromatogram review goes through XIC Inspector (see
[script lifecycle](../../docs/agent/script-tool-lifecycle.md#diagnostic-boundaries)); the Matplotlib tools in this section are retirement candidates under #441.

Matplotlib-rendered MS1 overlay visuals for human review of backfill candidates.
Legacy `family_ms1_*` filenames and `feature_family_id` fields mean public
row-label compatibility, not product identity authority. Peak-group overlays are
context; mode-aware outputs should use
`peak_hypothesis_id` / selected-apex mode evidence when a row may contain
multiple MS1 peak modes.

## Agent / Docs Workflow Guards

Tools that guard repo-local agent and documentation workflow. These are not
LC-MS evidence diagnostics and must not claim product behavior readiness.

### `docs_management_audit.py`

**Purpose**: Audit the repo/Obsidian documentation management system after
cleanup. It orchestrates the read-only repo/vault audit and delegates
`docs/superpowers` routing, topic clustering, text-scan mechanics, and generated
topic README rendering to focused helper modules. Its report covers routing
candidates with three-route doc-route, key-concept, repo-owner, document
kind/lifecycle/exit-rule status, topic-owner collision clusters, Obsidian-lane,
digestion status/action, and `source_repo_path` original-lookup hints plus wiki
skill routing for query, ingest/update, lint, and staged promotion, stale
post-commit handoff wording, manifest stats drift, pending vault raw/staged
files, missing vault lifecycle metadata, approximate broken wikilinks, handoff
retention drift, and local/private path exposure that needs focused
retention/privacy review.

Use `--routing-manifest-tsv` for file-level cleanup rows and
`--topic-clusters-tsv` for big-direction consolidation. Use
`--topic-index-dir output/docs-topic-indexes` to generate temporary,
ignored index-only topic README files from the current cluster summary.
Use `--repo-only` in CI/PR closeout without reading the private Obsidian vault.
Completed-document inventory is advisory: lifecycle status alone does not
determine whether a document still has a useful consumer.
**Topic group**: `docs_management_audit.py` + `docs_scan.py`
**Governing doc**: `docs/agent/obsidian-handoff-contract.md`
**Status note**: Read-only docs governance audit. It does not move files, write
to Obsidian, stage changes, mutate product output, or authorize tracked
deletion. Repo-only mode skips private vault checks and is safe for CI. It can
write a full candidate review TSV with
`--routing-manifest-tsv`; that TSV is a file-management control table, not
deletion approval. `repo_product_doc`, `kept_files`, and route-retained counts
mean "not automatically movable", not "fully digested"; use
`digestion_status`, `digestion_next_action`, and topic-cluster digestion review
counts before treating retained docs as cleaned-up knowledge. Use it after major
docs cleanup and before PR closeout when the repo/vault split changed.

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
that no code in `xic_extractor/`, `gui/`, `scripts/` or `xic_inspector/` loads
outside their own body, and that no `[project.scripts]` entry point names.
Tests, `tools/`, imports and `__all__` strings do not count as referrers.
**Status note**: Static and name-based. A same-named symbol elsewhere hides a
dead one, and names reached only through `getattr` or string tables are
reported. It reads source only and always exits 0.

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
- `xic_extractor/diagnostics/backfill_overlay.py` — package-owned shared selector for seed-specific backfill overlay rows. It keeps retained backfill gates, shadow policy, and shadow projection on one fail-closed selection rule: exact seed rows beat legacy family rows when allowed, and conflict/review verdicts outrank support verdicts when duplicate rows exist for the same seed. Use it before adding local `_selected_overlay_row` or `_overlay_sort_key` copies.
- `tools/diagnostics/docs_scan.py` — shared text-file discovery/read helpers for docs diagnostics. Reuse it before adding another `git ls-files` fallback, repo-relative path helper, or text-file scan predicate.
- `tools/diagnostics/docs_routing_review.py` — docs/superpowers routing domain logic: route classification, lifecycle metadata, topic hints, topic-owner clustering, exact-referrer status, and TSV field contracts. Keep docs-route/product-authority rules here instead of rebuilding them inside CLI wrappers.
- `tools/diagnostics/docs_topic_indexes.py` — generated topic-index README renderer. It writes navigation-only topic README files to a caller-provided ignored output directory, normally `output/docs-topic-indexes/<topic>/README.md`, from the topic-cluster summary and must not define product behavior, validation policy, matrix authority, or selected values.

Large diagnostics refactor reviews should use the `code-review` workflow with a
fixed base and head. Start from high blast-radius helpers and public contracts:
shared tabular/overlay helpers, matrix identity, activation decisions, value
deltas, output schemas, RAW access locality/reuse, and diagnostic-vs-production
claims. Treat representative writer parity and focused tests as stronger
evidence than an unactionable line-by-line pass over mechanical writer churn.

## Maintenance Notes

- **Adding a new entry-point**: Append it to the matching group section, or
  create a new group if none fit. Always populate Purpose / Topic group /
  Originating spec — these are the minimum for an entry to be useful at
  session start.
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
