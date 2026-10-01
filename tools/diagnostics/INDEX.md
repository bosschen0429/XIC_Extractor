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
| Select Matrix features for downstream analysis | GUI **篩選匯出…** or `xic-feature-quality`; [Feature Quality](../../docs/product/feature-quality.md) owns the rule and output |
| Review instrument/ISTD drift | `scripts.run_instrument_qc` and `scripts.add_istd_rt_trend`; [Instrument QC](../../docs/product/instrument-qc-calibration.md) owns their distinct inputs and claims |
| Build a solvent-blank background library | `scripts.extract_blank_library`; `xic_extractor.discovery.blank_qualification` owns extraction and serialization |
| Prepare targeted review actions | [Review Roundtrip commands](../../docs/product/review-roundtrip.md#operator-commands) distinguish candidate verification, copy-state edits, and peak-editing authority |
| Run tissue validation | `scripts.run_tissue_validation`; [execution settings](../../docs/agent/parameter-settings.md#validation-scale) own prerequisites |
| Maintain CI or command safety | `tools.testing.test_shards` and `tools.command_policy` serve CI/hooks; they have no GUI workflow |

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
8. [Area / Region Audits](#area--region-audits)
9. [Delta Mass / Pt Pattern Discovery](#delta-mass--pt-pattern-discovery)
10. [Agent / Docs Workflow Guards](#agent--docs-workflow-guards)
11. [Surface Population And Reachability](#surface-population-and-reachability)

---

## Validation Gates

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
**Owner**: [Alignment execution explanations](../../docs/product/alignment.md#cid-nl-execution-explanations).
**Input**: `cid_nl_explanation.sqlite`, optionally an exact candidate ID.
**Output**: Readable text or JSON on stdout; no file or product mutation.
**Status**: Maintained diagnostic reader. Retains owner assessment, initial
CrossSample and final disposition facts; does not recalculate decisions or grant
Matrix authority. Existing `alignment_health_packet.py` summarizes machine-level
health; it does not contain excluded candidate and initial weak-edge facts from
default runs.

---

CLI reports and audits over alignment outputs (cells, families, owner
decisions, RT normalization, matrix identity).

### `row_completion_confidence.py`

**Purpose**: Build the daily artifact-lane row-completion confidence packet from
existing alignment health and matrix artifacts.
**Topic group**: `row_completion_confidence.py` + package-owned
`xic_extractor.diagnostics.row_completion_confidence`
**Originating spec/plan**:
`docs/product/quant-matrix.md`; retired row-completion confidence benchmark
design provenance `retired-provenance:6f1e25e036cf`
**Status**: diagnostic_only. Builds manifest-bound row-completion confidence
outputs from existing alignment health and matrix artifacts. Does not open RAW,
recompute evidence, run mature tools, alter matrix authority, or change
selected values/counting.

---

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
It reads `alignment_review.tsv`, `alignment_matrix.tsv`,
`alignment_matrix_identity.tsv`, optional `alignment_backfill_cell_evidence.tsv`
or `alignment_cells.tsv`, and optional `alignment_owner_backfill_seed_audit.tsv`.
It is `diagnostic_only`: it does not read RAW, recompute evidence, change
selected peaks/areas/counting, alter matrix identity, or grant product
authority.

---

### `analyze_rt_normalization_anchors.py`

**Purpose**: Analyze anchor-based RT normalization for alignment outputs.
**Topic group**: `analyze_rt_normalization_anchors.py` + `rt_normalization_anchor_{loaders, models, analysis, writers}` (5 files)
**Originating spec**: (none found; rt_normalization pipeline behavior lives in `xic_extractor/alignment/rt_normalization.py`)
**Naming note**: The entry-point uses `analyze_*` prefix while helpers use `rt_normalization_anchor_*` prefix — pending rename per audit-note Cluster discussion.

---

### `build_sample_rt_calibration.py`

- **Role:** evidence producer; maintained operator interface.
- **Owner:** `xic_extractor.alignment.sample_rt_calibration`; contract in
  [`alignment.md`](../../docs/product/alignment.md#sample-rt-calibration-diagnostic).
- **Use:** `python -m tools.diagnostics.build_sample_rt_calibration --help`.
- **Outputs:** calibration JSON, sample TSV, and held-out TSV from
  `alignment_cells.tsv` plus an optional injection timeline JSON. It does not
  read RAW.
- **Authority:** `diagnostic_only`; no grouping, selection, RT, or value
  authority. Keep until a correspondence owner adopts the same model; the older
  `analyze_rt_normalization_anchors.py` uses Targeted-workbook anchors instead.

---

### `run_alignment` owner-minted exact Backfill request reuse

**Purpose**: Partition the live hypothesis-bound Backfill request population by
complete typed extraction semantics, execute one extraction per canonical
class, and fan the typed trace back to every logical request.
**Topic group**: `xic_extractor/alignment/backfill_request_plan.py` +
`backfill_peak_fact.py` (in-process request-plan and extraction owners; no
standalone persisted-projection reader).
**Originating issues**: [#195](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/195)
and [#210](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/210).
**Status note**: `diagnostic_only`; current evidence is synthetic/no-RAW.
Exact identity requires owner-closed acquisition content identity plus exact
m/z, RT bounds, ppm, backend, window, validation, and result semantics. Missing
or mismatched owner context emits no exact claim. Timing records expose only
observed logical-request and canonical-class counts. Display-coordinate and
persisted-projection proxies remain non-proof; no elapsed, RAW-call,
chromatogram-call, point, cost, or current real-population savings are claimed.
The path does not consume the early-loser shadow or change Backfill eligibility,
claim/Matrix/writer authority, PR E artifact streaming, or PR F recovery.

---

## Backfill Reviews

Three overlapping review axes (seed-level / family-level /
row-classifier-level), 2 owner-backfill economics tools, 1 reconciliation
gallery/index surface, 1 MS1+RT shadow-policy report, 1 Tier 2 RAW trace
producer, 1 high-signal clean activation scope audit, 1 `diagnostic_only`
provisional candidate-gate sidecar, and 1
`diagnostic_only` product-retained backfill evidence-gate sidecar. The sidecars
are not economics axes; they emit source-hashed support/blocker/missing-evidence
rows while the existing review/economics axes remain pending cleanup per
audit-note Cluster 2.

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

### `dna_dr_product_ready_fast_mode_expected_diff.py`

**Purpose**: Compare explicit fast-mode candidate outputs against an exact
`dna_dr_product_ready` output and write an expected-diff packet.
**Topic group**: `scripts.run_alignment` fast-mode backend candidates,
`validate_ms1_scan_index_xic.py`
**Status note**: Writes
`dna_dr_product_ready_fast_mode_expected_diff.json/tsv` with public artifact
hash parity, matrix/key coverage, selected cell/status/numeric drift,
matrix-identity drift, timing deltas, and optional MS1-index audit summaries.
It can classify a candidate as `fast_mode_candidate` only when the candidate is
explicitly supplied and the packet supports that decision; otherwise results
remain `diagnostic_only_do_not_wire_to_default`. This tool never mutates exact
outputs and cannot promote an approximate backend into the default preset by
itself.

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

### CID-NL differential evidence producer helpers

### `tier2_raw_trace_reread_producer.py`

**Purpose**: Produce paired `diagnostic_only` Tier 2 RAW trace evidence and RAW manifest sidecars for retained provisional backfill candidates.
**Topic group**: `tier2_raw_trace_reread_producer.py` + `xic_extractor/alignment/tier2_trace_producer.py` + `xic_extractor/alignment/production_candidate_gate.py`
**Originating spec/plan**: `retired-provenance:443a0af82ac4`; follows the sidecar provenance gate checkpoint in `retired-provenance:d3d707f162f6`; diagnostic criteria review in `retired-provenance:38373345f4da` and `retired-provenance:e509e2788bf2`.
**Status note**: Writes diagnostic-only Tier 2 RAW trace evidence and RAW manifest sidecars. The criteria expose scan availability, signal/noise, shape, boundary-reference, apex-span, and neighbor-interference context, but do not provide positive Tier 2 support or change `alignment_matrix.tsv`. FULL COHORT is out of scope until a reviewed follow-up plan is approved after a successful 8RAW diagnostic rerun.

---

## Peak / Candidate Audits

---

### `selected_envelope_review_queue.py`

**Purpose**: Build `diagnostic_only` selected-envelope changed-row and boundary-oracle review queue artifacts from `selected_envelope_diagnostics.tsv`.
**Topic group**: `selected_envelope_review_queue.py` + `xic_extractor/peak_detection/selected_envelope_*`
**Originating owner/plan**: `docs/product/quantitation-context.md`; retired selected full-envelope boundary spec provenance `retired-provenance:13e9499ab37d`
**Status note**: Writes `selected_envelope_changed_rows.tsv`, `selected_envelope_oracle_review_queue.tsv`, `selected_envelope_diagnostic_manifest.tsv`, and `selected_envelope_review_queue.json` under an explicit output directory. This tool only packages the audit sidecar for manual/expert boundary review; it does not run RAW files, mutate selected `IntegrationResult`, change targeted workbook/CSV `Area`, or authorize FE4/8RAW by itself.

---

## Targeted Benchmarks & Reviews

### `targeted_istd_benchmark.py`

**Purpose**: Run strict targeted ISTD benchmark for untargeted alignment.
**Topic group**: `targeted_istd_benchmark.py` + `_loaders`, `_matching`, `_models`, `_stats`, `_summary`, `_writers` (7 files; largest group)
**Originating spec**: `retired-provenance:53a763b9ec7a`
**Recent doc**: `retired-provenance:5162156a9766`
**Status note**: Reads targeted workbook inputs plus existing alignment review/cell/public-matrix artifacts and writes benchmark summary, match, JSON, and Markdown outputs. Public `alignment_matrix.tsv` loading supports both legacy `feature_family_id` matrices and clean `Mz` / `RT` / sample-column matrices with `alignment_matrix_identity.tsv` provenance; matrix sample-column normalization is computed once per matrix read and reused across row expansion. This benchmark remains validation-only context and does not mutate alignment artifacts, workbook schemas, matrix identity, or production decisions.

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

### `build_targeted_ms1_shape_identity_supports.py`

**Purpose**: Build generic `targeted_ms1_shape_identity` evidence rows from
a baseline `xic_results_long.csv` plus RAW MS1 traces. It selects all eligible
analyte NL-fail policy-blocked rows with paired RT/area-ratio support, finds a
Gaussian-smoothed local apex near the paired-ISTD reference mode, and compares
that trace against a counted reference trace using own-max same-peak identity.
**Topic group**: targeted NL dropout / MS1 shape identity; reuses
`xic_extractor.diagnostics.targeted_ms1_shape_identity` and
`xic_extractor.diagnostics.targeted_ms1_shape_identity_support_builder`; the
reusable runner lives in
`xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer` so the
formal extraction CLI can reuse the same support-producing path.
**Status note**: Diagnostic-only support producer when run directly. It opens
RAW files to build the support TSV, but does not directly write product output.
Normal extraction can consume a reviewed TSV through
`targeted_ms1_shape_identity_support_tsv`. The headless CLI also wraps the same
producer in a bounded auto workflow, either with
`--targeted-ms1-shape-identity-auto-limited-default` or by default when the
effective config policy is `limited_5hmdc_5medc` and no support TSV is
configured. In both auto paths, product output is published only after the
support-TSV expected-diff gate passes for the limited `5-hmdC + 5-medC` /
`detected_flagged` behavior.

---

### `targeted_ms1_shape_identity_expected_diff_gate.py`

**Purpose**: Gate targeted MS1 shape identity expected-diff artifacts for the
limited `5-hmdC` / `5-medC` activation policy.
**Topic group**: targeted NL dropout / MS1 shape identity; reuses
`xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff`.
**Status note**: Reads existing `expected_diff_summary.tsv`,
`matrix_diff_summary.tsv`, and the required actual
`targeted_ms1_shape_identity` `--support-tsv`. It fails closed unless every
long-row mutation is an analyte `NL_FAIL` row moving from `not_counted/FALSE` to
`detected_flagged/TRUE` with `own_max_same_peak_support`, every wide-matrix cell
mutation is an allowed `5-hmdC` or `5-medC` measurement moving from `ND` to a
populated value, and accepted support TSV sample/target keys exactly match the
long-row diff keys. It does not open RAW, build
support evidence, run extraction, or mutate product outputs.

---

### `build_target_pair_expected_diff_approval_registry.py`

**Purpose**: Convert explicitly user-approved `target_pair_rt_auto_reselection.tsv`
rows into a durable `model_selection_expected_diff_approval_registry` TSV for
guarded targeted product mutation.
**Topic group**: `build_target_pair_expected_diff_approval_registry.py`
(single-file)
**Originating spec**:
`docs/product/targeted-selection.md`; retired target-pair RT auto-reselection
spec provenance
**Status note**: Requires repeated `--approved-row SAMPLE::TARGET` inputs and
fails closed unless each row is a `row_approval_candidate` shadow switch with
matching runtime `expected_diff_stable_row_id`, selected-candidate RT, and
paired area ratio `within_robust_range`. The active paired-area ratio basis is
the counted leave-one-out median +/- 3 scaled-MAD target/ISTD reference owned by
`xic_extractor.extraction.paired_area_ratio_projection`; the diagnostic writer
renders that product evidence and must not reintroduce min/max or
all-reported-area authority. It does not auto-approve all watch rows and does
not recompute candidate evidence.

---

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

The Instrument QC suite is a coherent product surface; six entry-points
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

### `instrument_qc_biological_istd_transfer_audit.py`

**Purpose**: Build audit-only evidence for clean-standard RT trend transfer to biological QC ISTDs.
**Originating spec**: `retired-provenance:9b1b633f6f09`

---

### `instrument_qc_calibration_maturity_gate.py`

**Purpose**: Build audit-only go/no-go decisions for instrument-QC calibration maturity levels.
**Originating spec**: `retired-provenance:9b1b633f6f09`
**Recent doc**: `retired-provenance:458efd729908`

---

### `instrument_qc_matrix_calibration_preview.py`

**Purpose**: Build manifest-backed instrument QC calibration evidence bundle and optional matrix preview sidecar.
**Originating spec**: `retired-provenance:5cd57a52f007`
**Recent doc**: `retired-provenance:7626f95b0a28`

---

### `instrument_qc_decision_report.py`

**Purpose**: Render a compact instrument QC decision report.
**Originating spec**: `retired-provenance:9b1b633f6f09`

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

## Area / Region Audits

### `gaussian15_area_pressure_audit.py`

**Purpose**: Build a diagnostic-only pressure surface for Gaussian15 morphology
area versus raw area and configured/default-15 smoothing duration from
`peak_candidates.tsv`.
**Topic group**: `gaussian15_area_pressure_audit.py` (single-file)
**Originating context**: 2026-06-05 Product Authority Reconciliation
follow-up for raw-vs-Gaussian area comparability and scan-rate sensitivity.
**Status note**: Reads candidate-table provenance fields
(`area_raw_counts_seconds`, `area_ms1_morphology`,
`ms1_morphology_trace_*`, `region_scan_count`, and `region_duration_min`),
writes `gaussian15_area_pressure_summary.tsv/json` plus
`gaussian15_area_pressure_rows.tsv`, and reports
`readiness_label=diagnostic_pressure_test_surface`. The CLI derives summary
counts from the already computed row audit so it reads `peak_candidates.tsv`
once while preserving the path-based summary/detail helper functions. It does
not mutate selected boundaries, selected area, confidence, presence, workbook
output, matrix values, or candidate selection.

---

## Delta Mass / Pt Pattern Discovery

Diagnostics-only bridge tooling for the `delta_mass(pattern_id=pt)` Discovery
lane owned by `docs/product/discovery.md`.
This group must stay diagnostics-only: no Alignment, Backfill, ProductWriter,
or workbook Matrix imports (enforced by `tests/test_pt_pattern_boundaries.py`
for the core `xic_extractor.discovery` / `scripts.run_pt_pattern_discovery`
modules; this tool follows the same boundary by convention).

### `pt_pattern_scan_proxy.py`

**Purpose**: Scan-level Pt pattern proxy. Reads MS1 scans directly from a
RAW file (reusing `xic_extractor.raw_reader.open_raw`/`RawFileHandle`) and
emits a schema-valid Detected Features CSV
(`xic_extractor.discovery.delta_mass_csv.DETECTED_FEATURES_COLUMNS`) with
`source = "raw_scan_proxy"` on every row, plus a `proxy_provenance.json`
provenance file, so the Pt pattern CLI
(`scripts/run_pt_pattern_discovery.py`) has something to run against for a
first diagnostic look at a real Fullscan sample.
**Topic group**: `pt_pattern_scan_proxy.py` (single-file)
**Product owner**: `docs/product/discovery.md` (Task 7)
**Status note**: This is explicitly **not** a product feature detector — no
chromatographic peak-shape modeling, no smoothing, no deconvolution. Its own
scan-level m/z lookup window (`--lookup-ppm`, default 20 ppm) matches the Pt
pattern matcher's real m/z gate by default, while wider exploratory windows
remain opt-in. Repeated full-pattern scan groups are written with
`trace_quality = "OK"` and blank `shape_score` so the bridge can produce review
candidates, but the provenance labels the output `diagnostic_only`; see
`docs/superpowers/validation/pt_pattern_discovery/README.md`.

---

### `pt_pattern_trace_diagnosis.py`

**Purpose**: Chromatography-aware Pt pattern trace diagnostic. Reads an existing
`pt_pattern_candidates.csv`, extracts vendor XIC traces for base / +1 / +2 /
+4 around each candidate's base RT using
`xic_extractor.raw_reader.open_raw` / `RawFileHandle.extract_xic_many`, and
writes candidate-level trace support summaries, position-level co-elution
metrics, and optional trace points. Current full-context schema keeps the
full extraction-window semantics, including
`pt_pattern_trace_points.csv`. Anchor-local segmentation is emitted separately
as `pt_pattern_anchor_local_trace_*.csv` under
`pt_pattern_anchor_local_trace_diagnosis`. For the anchor-local output, each
Pt position must yield exactly one chromatographic segment that contains the
candidate anchor RT; its co-elution metrics use the common intersection of the
four selected segments. Missing or ambiguous segments fail closed to review
instead of choosing the nearest or strongest peak.
**Topic group**: `pt_pattern_trace_diagnosis.py` +
`xic_extractor.discovery.pt_pattern_trace_evidence` +
`xic_extractor.discovery.pt_pattern_trace_batch`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only evidence probe. It does not create Pt
candidates, mutate Discovery outputs, read Alignment/Backfill state, or write
Matrix/workbook artifacts. The summary separates `scan_pattern_seed_status`
from `trace_support_status` and reports a diagnostic-only
`combined_pt_pattern_status`; `pt_pattern_detected_diagnostic` means scan seed
plus co-elution support in this ignored output only. Use it to evaluate whether
scan-proxy or feature-level Pt candidates have RT-close, shape-supported
chromatographic families before promoting any rule into the product lane. The
diagnostic wrapper owns RAW opening and file writes only; batch diagnosis,
segmentation, boundary facts, and support semantics live in Discovery package
modules. One RAW extraction feeds one full-window diagnosis and one
anchor-local diagnosis; context trace points are not produced by recomputing a
third diagnosis. Schema records
`trace_decision_basis = gaussian15_smoothed_trace`: apex, shape similarity,
active overlap, and support status use Gaussian15-smoothed XIC intensity, while
raw vendor trace points and raw audit metrics are retained as reference
evidence. Every summary, metric, and point row carries candidate-assignment,
trace-extraction, and artifact-role lineage. The JSON manifest binds those rows
and output hashes to the candidate CSV, RAW hash, candidate slice, extraction
settings, and decision/smoothing contracts. The JSON sidecar records
`readiness_tier = diagnostic_only` and
`evidence_owner = xic_extractor.discovery.pt_pattern_trace_evidence`.

---

### `pt_pattern_review_packet.py`

**Purpose**: Build a bounded manual-review packet for Pt pattern candidates by
joining `pt_pattern_candidates.csv` with either the legacy full-window trace
summary/metrics or the separate anchor-local summary/metrics. The output is a
small CSV with blank
`human_review_decision` / `human_review_note` columns for accepted/rejected/
uncertain adjudication work plus a JSON sidecar.
**Topic group**: `pt_pattern_review_packet.py` +
`xic_extractor.discovery.pt_pattern_review_packet`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only review-surface builder. It does not open RAW,
create candidates, mutate Discovery outputs, read Alignment/Backfill state, or
write Matrix/workbook artifacts. The sidecar records
`readiness_tier = diagnostic_only`, `oracle_status = pending_human_review`, and
`evidence_owner = xic_extractor.discovery.pt_pattern_trace_evidence`. The
historical `oracle_status` field names workflow state only: filled and promoted
human labels remain bounded adjudication evidence, not independent ground truth.
An `uncertain` label stays unresolved and must not be counted as a true reject.
Current full-context inputs use packet schema v4; anchor-local inputs use packet
schema with explicit `anchor_segmentation_*` evidence fields. Both require a
packet manifest and the verified trace manifest/fingerprint that produced the
reviewed rows; historical unsealed packet schemas fail closed.

---

### `pt_pattern_blind_overlay.py`

**Purpose**: Build a blind MS1/XIC overlay gallery from a Pt pattern manual
review packet and a trace-point CSV. For anchor-local review, pass
`pt_pattern_anchor_local_trace_points.csv` as the primary trace input and the
legacy full-window `pt_pattern_trace_points.csv` through optional
`--context-trace-points-csv`; the gallery then separates full-window background,
anchor-local own-max shape, and anchor-local shared `+1 apex = 1` intensity
views. The HTML intentionally hides
machine calls and diagnostic status buckets so the user can make an independent
visual call before opening the sealed machine-call CSV.
**Topic group**: `pt_pattern_blind_overlay.py` +
`xic_extractor.discovery.pt_pattern_blind_overlay` +
`xic_extractor.discovery.pt_pattern_blind_overlay_call` +
`xic_extractor.discovery.pt_pattern_blind_overlay_writer`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only human-review surface. It reads existing
review-packet and trace-points CSVs, writes an HTML gallery, a sealed machine
call CSV, and a summary JSON with no call distribution. It does not open RAW,
create candidates, mutate Discovery outputs, read Alignment/Backfill state, or
write Matrix/workbook artifacts. The sealed machine-call CSV is not an oracle
or final detector output; it is revealed only after the user records independent
review decisions. The HTML plots Gaussian15-smoothed traces for review and
sealed machine calls use the same Gaussian15 trace-decision metrics. Those calls
are the current `trace_rule_call` proxy only. Raw trace-point CSVs remain raw
vendor evidence and are not Matrix, counting, or quantitation authority. Current
full-context output uses overlay/machine schema v4; anchor-local inputs use
overlay/machine schema. Rendering, sealed-call projection, and artifact
writing are maintained by separate package modules. Trace-point CSVs are sparse
observation tables: a reviewed candidate or position may have zero rows when
the XIC is empty or anchor segmentation fails. The writer renders those gaps as
missing evidence only when a verified point manifest authenticates the
header-only artifact, while still rejecting candidates outside the review packet,
unknown position labels, non-finite points, and sample mismatches. Its summary
binds the review-packet manifest, primary/context trace manifests and points,
gallery HTML, and sealed machine-call CSV by SHA-256. Schema, role, extraction
fingerprint, gallery mode, and context presence must form one valid mode matrix.
For anchor schema v5, trace ambiguity, anchor segmentation,
and boundary review must all be explicitly `clear`; any other state routes the
sealed review projection to `uncertain`. This is not a threshold reject or
product non-detection.

---

### `pt_pattern_oracle_promotion.py`

**Purpose**: Validate adjudicated Pt pattern blind-review rows against the exact
reviewed packet and blind-overlay artifact bundle, then write a historically
named bounded adjudication packet.
**Topic group**: `pt_pattern_oracle_promotion.py` +
`xic_extractor.discovery.pt_pattern_oracle_promotion`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only despite the historical `oracle` names in its
schema and paths. Promotion schema requires each adjudicated row to carry a
fresh human `accept` or `reject`, the SHA-256 of its review-packet CSV, and the
SHA-256 of a supplied blind-overlay summary. The tool re-hashes every packet,
trace CSV, gallery HTML, sealed machine-call CSV, and summary named by that
bundle; legacy logical IDs without this exact artifact binding fail closed.
Adjudicated base m/z and RT values, when present, must numerically match the
review packet; the promotion output always uses the packet's canonical
coordinates rather than treating the editable adjudication CSV as identity
authority.
Blank, duplicate, unmatched, or `uncertain` calls are excluded rather than
coerced, and the output records `oracle_detection_status = not_asserted`. It
does not open RAW, create
candidates, recompute trace evidence, mutate Discovery outputs, read
Alignment/Backfill state, or write Matrix/workbook artifacts. Excluding an
`uncertain` row from a promotion packet does not turn it into a reject, and the
packet does not establish detector accuracy or physical Pt identity.

---

### `pt_pattern_oracle_refresh.py`

**Purpose**: Compare an older, historically named Pt pattern adjudication CSV
with current manual-review packets and sealed machine-call CSVs, then write a refresh
draft, a pending human-review worklist, stale old-oracle rows, and a summary
JSON with input SHA-256 provenance.
**Topic group**: `pt_pattern_oracle_refresh.py` +
`xic_extractor.discovery.pt_pattern_oracle_refresh`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only adjudication-maintenance surface. It does not
open RAW, create candidates, recompute trace evidence, mutate Discovery outputs,
read Alignment/Backfill state, or write Matrix/workbook artifacts. It retains
legacy columns for audit, but matching packet-row and candidate IDs prove only
logical continuity: without exact reviewed-artifact identity, old labels remain
`pending_human_review` instead of being carried forward. Candidate conflicts
remain conflicts, and old rows absent from the current packet are reported as
stale. A refresh draft can be packaged only after every pending/conflicting row
is freshly resolved under the promotion provenance contract. The
machine-call CSV is accepted only through its sibling
`pt_pattern_blind_overlay_summary.json`; that summary must verify the complete
packet/trace/gallery/machine bundle and bind the exact supplied machine path.
historical repaired 32-row packet is now stale calibration context: its labels
were reviewed against an older bounded slice, are not independent ground truth,
and cannot be carried into a changed packet merely because a legacy row ID
matches.

---

### `pt_pattern_oracle_thresholds.py`

**Purpose**: Calibrate simple Pt pattern trace thresholds against a bounded
reviewed-label packet. It joins `pt_pattern_oracle_promotion`
rows with current manual-review packet metrics, sweeps full-pattern
apex/Pearson/overlap threshold candidates, and writes a threshold-candidate CSV,
a case-level audit CSV, and a summary JSON.
**Topic group**: `pt_pattern_oracle_thresholds.py` +
`xic_extractor.discovery.pt_pattern_oracle_thresholds` +
`xic_extractor.discovery.pt_pattern_oracle_replay_contract`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only threshold calibration surface. It does not
open RAW, create candidates, recompute trace evidence, mutate Discovery
outputs, read Alignment state, or write Matrix/workbook artifacts. The
calibration consumer validates current manifest-sealed packet schema v4/and requires each promotion
row's schema/status, candidate/sample identity, and review-packet SHA-256 to
match the actual packet supplying the metrics. A stale label cannot be paired
with another packet merely by reusing `packet_row_id`. The recommended rule is
review evidence only; it must not be treated as detector
activation, counted-detection authority, or export authority without a later
explicit public-surface contract. The historical zero-error result is
resubstitution against the same 32 reviewed rows used for calibration; it is not
ground-truth accuracy, held-out validation, or evidence of generalization.

---

### `pt_pattern_ratio_profile.py`

**Purpose**: Profile Pt pattern candidate intensity/area ratios from existing
`pt_pattern_candidates.csv` and `pt_pattern_evidence.csv` artifacts. It writes
primary-population ratio rows, primary/background distribution summaries, a
static HTML review page, and a summary JSON without opening RAW or recalculating
trace evidence.
**Topic group**: `pt_pattern_ratio_profile.py` +
`xic_extractor.discovery.pt_pattern_ratio_profile`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only ratio observation surface. It separates
FeatureHunter-compatible `base/+N` ratios from derived `+1/base`, `+1/+2`, and
`+1/+4` review ratios, and uses only data-driven distribution-tail flags. These
flags are review aids, not detector activation, counted-detection authority,
export authority, selected-area authority, or Matrix-writing authority.

---

### `pt_pattern_hypothesis_synthesis.py`

**Purpose**: Synthesize existing Pt pattern ratio, overlap/interference, +1 seed,
and inclusion-list assembly diagnostics into a compact no-RAW hypothesis report.
**Topic group**: `pt_pattern_hypothesis_synthesis.py` +
`xic_extractor.discovery.pt_pattern_hypothesis_synthesis`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only synthesis surface. It consumes already-produced
CSV/JSON artifacts, writes summary JSON, Markdown, assembly-layer CSV, and known
miss examples, and does not open RAW, create candidates, mutate Discovery
outputs, read Alignment/Backfill state, decide ratios, change detector rules,
or write Matrix/workbook artifacts.

---

### `pt_pattern_replay.py`

Run `python -m tools.diagnostics.pt_pattern_replay --help` to choose a
saved-CSV question. All three subcommands write diagnostics without RAW
acquisition or product activation. Scientific rules stay in the package owners.

| Question | Subcommand |
| --- | --- |
| Why did an inclusion-list row fail candidate assembly? | `assembly` |
| Which saved candidates make incompatible shared-feature claims? | `conflicts` |
| Does DDMS2 contain MS1 feature support near a Fullscan seed? | `ddms2-support` |

#### `assembly`

**Purpose**: Replay inclusion-list rows whose four list-relative m/z pools exist
against the canonical local MS1 feature table, then identify the first failed
mass, quality, RT, or matcher-emission condition.
**Entry**: `pt_pattern_replay assembly`; package owner:
`xic_extractor.discovery.pt_pattern_candidate_assembly_replay`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only no-RAW replay. It reuses the current Pt
candidate matcher and shared mass-accuracy helpers, writes row-level CSV, summary JSON,
and chart-first HTML, and does not change detector rules, counted detection,
export, Alignment/Backfill, or Matrix authority.

---

#### `conflicts`

**Purpose**: Build a provenance-bound pairwise shared-claim graph over frozen Pt
candidate rows, then compare connected-component-wide `at_most_one` semantics
with the pairwise incompatibility structure represented by those rows.
**Entry**: `pt_pattern_replay conflicts`; package owners:
`xic_extractor.discovery.pt_pattern_conflict_graph` +
`xic_extractor.discovery.pt_pattern_conflict_graph_replay`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only no-RAW replay. It verifies candidate and
detected-feature provenance, rejects unknown source schema versions, validates
legacy resolution/preferred-candidate consistency, and writes versioned vertex,
edge, component, legacy comparison, and summary sidecars. The semantic summary
contains hashes and graph identity but no path or report label; those
presentation details live in a separate run-metadata JSON. It marks where
component-wide `at_most_one` would overconstrain pairwise-compatible
hypotheses. Current vertices are serialized greedy matcher representatives, not
complete enumeration of all compatible base/+1/+2/+4 assignments; the replay
cannot recover alternatives pruned before candidate emission. It does not select a
hypothesis, instantiate or count an event, change current machine calls, read
Alignment/Backfill state, or write Matrix/workbook/export artifacts.

---

#### `ddms2-support`

**Purpose**: Replay Fullscan-primary Pt pattern seeds against a DDMS2 local MS1
feature table to inspect whether the DDMS2 run contains matching base/+1/+2/+4
support near the Fullscan seed.
**Entry**: `pt_pattern_replay ddms2-support`; package owner:
`xic_extractor.discovery.pt_pattern_ddms2_support_replay`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only support replay. It does not open RAW, create
Fullscan seeds, recompute trace evidence, decide DDMS2 targeting opportunity,
mutate Discovery outputs, read Alignment/Backfill state, or write
Matrix/workbook artifacts. Its m/z tolerance and RT window must be positive
finite values. Without the historical target/inclusion list,
missing DDMS2 support is context only, not negative evidence against a Fullscan
Discovery seed.

---

### `pt_pattern_same_scan_replay.py`

**Purpose**: Evaluate fixed theoretical base, +1, +2, and +4 m/z windows in the
same Fullscan MS1 scans for current positive diagnostic hypotheses and known
inclusion-list controls, then expose strict four-position and
core-with-conditional-+4 window-occupancy evidence.
**Topic group**: `pt_pattern_same_scan_replay.py` +
`xic_extractor.discovery.pt_pattern_same_scan_evidence` +
`xic_extractor.discovery.pt_pattern_same_scan_replay`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only RAW-backed replay. It loads or builds one MS1
index per run, never reopens RAW per candidate, preserves below-noise +4 as
censored evidence, and reports shared-feature conflict components. Replay
keeps the strict component rule and treats `(source_set, seed_id)` as the
structured seed identity. Known-list seed IDs derive from normalized base-m/z
assignments; compound names remain review metadata and do not define identity.
A component counts as resolved only when exactly one candidate has full
base/+1/+2/+4 strict support; core-only support remains diagnostic and cannot
nominate a preferred Discovery hypothesis. It does not
change feature detection, candidate assembly, ratios, counted detections,
Alignment/Backfill, export, or Matrix authority. A populated theoretical m/z
window does not identify which chemical species contributed its intensity and
does not prove an exact centroid-to-envelope assignment.

---

### `pt_pattern_diagnostic_gate.py`

**Purpose**: Apply the current Pt full-pattern diagnostic trace rule to one or
more manual-review packet CSVs or full `pt_pattern_trace_summary.csv` inputs,
optionally replaying against a bounded reviewed-label packet, and write
row-level gate calls plus a summary JSON.
**Topic group**: `pt_pattern_diagnostic_gate.py` +
`xic_extractor.discovery.pt_pattern_diagnostic_gate` +
`xic_extractor.discovery.pt_pattern_evidence_rule` +
`xic_extractor.discovery.pt_pattern_oracle_replay_contract`
**Product owner**: `docs/product/discovery.md`
**Status note**: Diagnostic-only replay surface. It does not open RAW, create
candidates, recompute trace evidence, mutate Discovery outputs, read Alignment
state, or write Matrix/workbook artifacts. The reusable Pt full-pattern
accept/reject rule lives in
`xic_extractor.discovery.pt_pattern_evidence_rule`; this CLI only adapts review
packet or trace-summary rows into replay artifacts. `--trace-summary-input
SOURCE_LABEL=PATH` lets the same calibrated rule run on current full-context
trace-diagnosed candidate slices instead of only bounded manual-review packets;
the canonical sibling metrics CSV and trace manifest are required and the
manifest fingerprint/output hashes are revalidated before any gate row is
written. Diagnostic-gate schema preserves the input schema, assignment SHA,
extraction SHA, and artifact role in every output row.
`--require-zero-fpfn` makes the CLI fail if bounded-oracle replay has any false
positive or false negative. Reviewed-label replay first requires an exact
promotion row and actual review-packet SHA/candidate/sample match; synthetic
trace-summary rows cannot stand in for that packet. The pass/fail result is
still only a diagnostic gate against those reviewed labels. A zero-error result is resubstitution, not
ground-truth accuracy or held-out validation, and must not be treated as final
detector performance, detector activation, counted-detection authority, or
export authority without a later public-surface contract.

---

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
