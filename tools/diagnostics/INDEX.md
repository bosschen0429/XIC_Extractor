# tools/diagnostics/ — Diagnostic Tool Index

**Last refreshed:** 2026-07-14
**Total entry-points:** 121
**Total files (incl. helpers):** 205 Python files under `tools/diagnostics/`
**Governing specs:** `docs/architecture-contract.md` and
`docs/agent/script-tool-lifecycle.md`; the retired dated diagnostic lifecycle
spec remains only as a same-path public stub.
**Provenance note:** `Originating spec/plan`, `Recent doc`, and similar fields
name historical implementation identifiers. After the docs-cleanup migration,
they are not current source-of-truth links and should not by themselves keep a
private-history same-path stub in version control. Current governing owners live
in the entry status text, `docs/product/`, `docs/architecture-contract.md`,
`docs/lc-msms-evidence-rules.md`, and validation/control-plane artifacts.
Opaque `retired-provenance:*` values mark private-history development inputs
that were intentionally removed from the public repo surface.
**Count method:** top-level `### *.py` entry headings for entry-points;
top-level `tools/diagnostics/*.py` files for total files.

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

## Table of Contents

1. [Phase Gates (P1/P7)](#phase-gates-p1p7) — 7 tools
2. [Evidence Consistency](#evidence-consistency) — 8 tools
3. [Alignment Diagnostics](#alignment-diagnostics) — 8 tools
4. [Backfill Reviews](#backfill-reviews) — 42 tools
5. [Peak / Candidate Audits](#peak--candidate-audits) — 7 tools
6. [Targeted Benchmarks & Reviews](#targeted-benchmarks--reviews) — 11 tools
7. [Instrument QC](#instrument-qc) — 6 tools
8. [Peak-Group / Overlay Visualization](#peak-group--overlay-visualization) — 7 tools
9. [Area / Region Audits](#area--region-audits) — 4 tools
10. [One-off Fixtures](#one-off-fixtures) — 1 tool
11. [Delta Mass / Pt Pattern Discovery](#delta-mass--pt-pattern-discovery) — 14 tools
12. [Agent / Docs Workflow Guards](#agent--docs-workflow-guards) — 6 tools

---

## Phase Gates (P1/P7)

Pipeline-tier gates that compare candidate configurations against current
production, or summarize cost/parity for production decisions.

Maintenance note: P2/P2b/P2c AsLS-vs-linear-edge gates were retired after the
baseline decision closed. AsLS is the current production baseline integration
method, and `linear_edge` is retired as a selectable baseline method. Do not add
new diagnostics that compare against or fall back to linear-edge; use current
AsLS/boundary/product contracts instead.

### `p1_resolver_default_gate.py`

**Purpose**: Compare local_minimum and region_first_safe_merge targeted 8RAW ISTD area RSD and RT shifts for the P1 default-switch gate.
**Topic group**: `p1_resolver_default_gate.py` (single-file)
**Originating spec**: `retired-provenance:4d545a180b14`
**Recent doc**: `retired-provenance:c1b9909ce7c0`
**Stop-maintenance note**: The resolver-default decision is closed. Current
public/config settings still accept the `region_first_safe_merge` token, while
alignment production maps that token to `local_minimum`; this gate is retained
only as decision history.

---

### `p7_alignment_parity.py`

**Purpose**: Run P7 alignment parity checks.
**Topic group**: `p7_alignment_parity.py`
**Originating spec/plan**: `retired-provenance:ad226e2eb39a`, `retired-provenance:10eb03817ff8`
**Note**: No standalone spec; lives under the P7 evidence-chain cost-control plan family.

---

### `p7_evidence_cost_summary.py`

**Purpose**: Summarize P7 evidence cost savings.
**Topic group**: `p7_evidence_cost_summary.py`
**Originating spec/plan**: `retired-provenance:ad226e2eb39a`, `retired-provenance:10eb03817ff8`

---

### `cid_nl_feature_inclusion_gate.py`

**Purpose**: Build the diagnostic CID-NL feature-inclusion / identity-authority
gate from existing differential review, paired overlay summary, AI
identity-triage artifacts, and optional versioned manual review verdicts.
**Topic group**: CID-NL default activation Gallery and differential overlay
review; consumes
`cid_nl_discovery_identity_differential_review.tsv`,
`cid_nl_differential_overlay_review_summary.tsv`, and
`cid_nl_differential_ai_adjudication.tsv`; optional
`cid_nl_manual_feature_inclusion_review.tsv`.
**Originating spec/goal/plan**:
`docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`;
stable external-method takeaways now live in `docs/product/untargeted-method.md`.
**Status note**: Diagnostic-only gate adapter. It does not read RAW, render
PNGs, create a second Discovery system, mutate ProductWriter/default
matrix/workbook/GUI outputs, or treat candidates as matrix rows. It writes
`cid_nl_feature_inclusion_gate_summary.tsv/json`,
`cid_nl_feature_inclusion_gate_transitions.tsv`,
`cid_nl_identity_expected_diff_queue.tsv`,
`cid_nl_supported_candidate_expected_diff_contract.tsv`,
`cid_nl_feature_inclusion_review_queue.tsv`, and
`cid_nl_manual_resolved_expected_diff_contract.tsv`,
`cid_nl_manual_resolved_hold_queue.tsv`,
`cid_nl_user_review_queue.tsv`, and
`cid_nl_feature_inclusion_blocked_queue.tsv` under ignored
`output/validation/cid_nl_default_activation_gallery_review_v1/feature_inclusion_gate/`.
The gate separates supported feature-inclusion candidates from review-required
and current-bundle-blocked candidates. Supported candidates only advance to
expected-diff design; identity authority and ProductWriter activation remain a
separate public-surface contract.

---

### `cid_nl_activation_copy_candidate.py`

**Purpose**: Build a CID-NL activated-copy matrix candidate and value-delta
checker from approved expected-diff design rows.
**Topic group**: CID-NL feature-inclusion gate and expected-diff contract.
Consumes `cid_nl_supported_candidate_expected_diff_contract.tsv` plus
`cid_nl_agent_resolved_expected_diff_contract.tsv` and
`cid_nl_manual_resolved_expected_diff_contract.tsv` by default, and fails
closed if any contract transition overlaps the user-review, agent-hold,
manual-hold, or blocked queues.
**Originating spec/goal/plan**:
`docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Diagnostic-only activated-copy surface. It reads the current
public `alignment_matrix.tsv` and `alignment_matrix_identity.tsv`, writes
`alignment_matrix_activated_copy.tsv`,
`alignment_matrix_identity_activated_copy.tsv`,
`cid_nl_activation_copy_value_delta.tsv`, and
`cid_nl_activation_copy_candidate_summary.tsv/json` under ignored
`output/validation/cid_nl_default_activation_gallery_review_v1/activation_copy_candidate/`.
It refuses to overwrite existing matrix values, requires every written cell to
come from the expected-diff contract, and reports explicit false flags for
ProductWriter, default matrix, workbook/GUI, and candidate-row authority. This
is not a default activation path.

---

### `cid_nl_activation_copy_acceptance.py`

**Purpose**: Accept or reject the CID-NL activated-copy matrix candidate
against its expected-diff contract, forbidden transition queues, and exact
matrix diff.
**Topic group**: CID-NL activated-copy validation. Consumes the expected-diff
contracts, user-review/agent-hold/manual-hold/blocked queues, input public
matrix, activated copy matrix, matrix identity sidecar, and activated-copy
value delta.
**Originating spec/goal/plan**:
`docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Diagnostic-only acceptance gate. It writes
`cid_nl_activation_copy_acceptance_summary.tsv/json` and
`cid_nl_activation_copy_matrix_diff.tsv` under ignored
`output/validation/cid_nl_default_activation_gallery_review_v1/activation_copy_candidate/acceptance/`.
It passes only when value-delta keys match the contract, no forbidden
review/hold/blocked transitions overlap the contract, and the input/output
matrix diff is exactly the contracted cell set. It reports
`production_ready=FALSE`; an explicit adopt gate is still required before any
default matrix or ProductWriter change.

---

### `cid_nl_activation_adopt_gate.py`

**Purpose**: Close the CID-NL activated-copy adopt/hold decision from accepted
copy evidence, without mutating ProductWriter or the default matrix.
**Topic group**: CID-NL activated-copy validation adoption. Consumes the
expected-diff contracts, forbidden user-review/hold/blocked queues,
feature-inclusion summary, activated-copy summary, activated-copy acceptance
summary, and value-delta TSV.
**Originating spec/goal/plan**:
`docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Production-candidate adopt gate for the narrowed 95-cell
validation-copy bundle. It writes
`cid_nl_activation_adopt_gate_summary.tsv/json` and the compact 20-transition
`cid_nl_activation_adopt_manifest.tsv` under ignored
`output/validation/cid_nl_default_activation_gallery_review_v1/activation_adopt_gate/`.
It returns `adopt_ready` only when the feature gate, copy gate, and acceptance
gate all pass; user review is zero; no forbidden transitions overlap; the
value-delta keys exactly match the expected-diff contract; ProductWriter,
default matrix, workbook/GUI, and candidate-row authority flags remain false;
and acceptance still reports `production_ready=FALSE`. `adopt_ready` means the
95-cell bundle can feed a later explicit public activation change; it is not
itself default output or ProductWriter authority.

---

## Evidence Consistency

Compare evidence rows produced by parallel pipelines (targeted vs untargeted,
spine vs cell) and shared-identity sidecars. The tools below share low-level
diagnostic IO helpers where their schemas permit it, but keep separate row
models because their output schemas are intentionally different; see
`retired-provenance:9ac1510742f0` Cluster 1.

### `evidence_spine_consistency.py`

**Purpose**: Compare targeted candidate and untargeted alignment evidence.
**Topic group**: `evidence_spine_consistency.py` + `_io`, `_models`, `_analysis`, `_writers` (5 files)
**Originating decision**: `retired-provenance:2a2ae49b79cb`
**Recent commit**: `a3e4ea0 2026-05-25` (feat: add phase 1 peak pipeline gates)

---

### `cross_report_evidence_consistency.py`

**Purpose**: Compare targeted reliability and peak candidate evidence.
**Topic group**: `cross_report_evidence_consistency.py` + `_io`, `_models`, `_analysis`, `_writers` (5 files)
**Originating spec**: (none found — likely landed alongside post-PR60 cleanup; see `retired-provenance:100307efc8fa` Workstream G context)
**Duplication note**: Low-level parsing/writing helpers are consolidated through `xic_extractor/tabular_io.py`; `xic_extractor/diagnostics/diagnostic_io.py` and `tools/diagnostics/diagnostic_io.py` remain compatibility shims. Row dataclasses stay separate because the schemas differ.

---

### `shared_peak_identity_explanation.py`

**Purpose**: Write the default Slice 0 `diagnostic_only` shared peak identity explanation outputs from a durable manual oracle plus existing alignment/candidate-gate artifacts; with `--enable-blast-radius`, add Slice 1 blast-radius manifest, summary, run facts, and report sections over existing 8RAW / 85RAW alignment artifacts; with `--enable-shadow-label-alignment`, add V2 shadow-label alignment, machine-evidence provenance, readiness, and report artifacts. Optional `--cwt-shape-evidence-tsv`, `--tier2-trace-evidence-tsv`, `--candidate-ms2-pattern-evidence-tsv`, `--ms1-pattern-coherence-evidence-tsv`, `--qc-ms1-pattern-reference-evidence-tsv`, `--sample-negative-evidence-tsv`, `--matrix-rt-drift-policy-tsv`, `--rt-mode-evidence-tsv`, and `--peak-hypothesis-selection-tsv` inputs can mark CWT shape, Tier2 raw-trace scan/intensity, sample/candidate-aligned MS2 pattern, MS1 constellation pattern coherence, local/consensus injection-QC MS1 pattern reference, sample-level negative evidence class/detail, independent matrix RT drift facts, iRT/raw selected-apex mode membership, and mode-level PeakHypothesis selection as machine-observed evidence; `--candidate-ms2-pattern-batch-index` can generate that candidate-MS2 sidecar from the same discovery batch index used by the alignment run, `--candidate-ms2-pattern-raw-dll-dir` can additionally probe rows without `source_candidate_id` against the sample RAW file recorded in that batch index, `--generate-ms1-pattern-coherence-evidence` can generate a conservative alignment-cell boundary-constellation sidecar, `--ms1-pattern-coherence-overlay-trace-data-json` can enrich that generated MS1 sidecar with RAW-backed `family_ms1_overlay_plot` trace shape/local-window metrics and optional peak-quality feature-vector fields when overlay `rt` / `intensity` arrays are present, `--generate-matrix-rt-drift-policy` can generate the matrix RT drift policy sidecar from alignment cells plus optional owner-edge, RT-normalization-family, targeted-ISTD anchor-local trend artifacts, and optional ISTD injection-order / phase-summary provenance, `--generate-rt-mode-evidence` can generate `shared_peak_identity_rt_mode_evidence.tsv` from an existing selected-apex mode assignment TSV plus optional mode summary and candidate-MS2 tag sidecar, and `--generate-peak-hypothesis-selection` can generate `shared_peak_identity_peak_hypothesis_selection.tsv` from RT-mode evidence.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/*`
**Originating spec/plan**: `retired-provenance:4451f010e6c4`; `retired-provenance:36493f007c1f`; `retired-provenance:fddde24e3c1d`; `retired-provenance:4d97a2722c41`
**Status note**: Default mode emits only the manual-oracle copy, evidence vectors, explanations, run facts, and Markdown report under `output/shared_peak_identity_evidence_explanation/`; it does not emit Slice 1 files unless `--enable-blast-radius` is passed. Slice 1 writes `shared_peak_identity_blast_radius_manifest.tsv` and `shared_peak_identity_blast_radius_summary.tsv` from existing artifacts only. V2 shadow mode writes `shared_peak_identity_shadow_labels.tsv`, `shared_peak_identity_shadow_alignment_summary.tsv`, `shared_peak_identity_v2_readiness.tsv`, `shared_peak_identity_machine_evidence_support.tsv`, and `shared_peak_identity_v2_report.md`; when the batch-index producer is enabled it also writes `shared_peak_identity_candidate_ms2_pattern_evidence.tsv`, when MS1 pattern generation is enabled it writes `shared_peak_identity_ms1_pattern_coherence_evidence.tsv`, when matrix RT drift generation is enabled it writes `shared_peak_identity_matrix_rt_drift_policy.tsv`, when RT mode generation is enabled it writes `shared_peak_identity_rt_mode_evidence.tsv`, and when PeakHypothesis generation is enabled it writes `shared_peak_identity_peak_hypothesis_selection.tsv`. `--generate-hypothesis-consistency` writes `shared_peak_identity_hypothesis_consistency.tsv` and `shared_peak_identity_hypothesis_consistency_summary.tsv`; this is a full-matrix diagnostic gate that cross-checks each PeakHypothesis against MS1 pattern, QC MS1 reference context, matrix RT drift, and candidate MS2/DDA opportunity evidence, but it does not select peaks, retarget rows, activate labels, or rewrite matrices. It can report `exploratory_only` when blast-radius evidence is not current or when decisive shape / pattern / opportunity evidence remains partial, conflicting, proxy-only, or manual-oracle-derived. Candidate MS2 pattern evidence is fail-closed and must be explicitly keyed by `feature_family_id + sample_stem`; target-label-only or RT/mz heuristic joins do not count. MS1 pattern coherence sidecars can close `formal_pattern_metric` when they emit supportive/partial `sample_constellation`, `sample_boundary_constellation`, or `trace_constellation` evidence; RAW-backed `trace_constellation` overlay rows can close `formal_shape_metric` only when they have `shape_metric_source=family_ms1_overlay_raw_trace`, `family_ms1_overlay_trace_data_json`, non-empty `shape_correlation_score`, and a machine-observed `peak_quality_vector_basis=family_ms1_overlay_raw_trace_vector` with `peak_quality_vector_status` of `supportive` or `partial_support`. QC MS1 reference sidecars can close `formal_pattern_metric` only when consensus-backed evidence levels such as `qc_consensus_with_local_qc_overlay` or `qc_consensus_qc_overlay` are supportive/partial; nearest-valid-local-only QC rows remain context-only. Consensus-backed QC conflicts can contribute to `pattern_metric_not_supportive` only when not overridden by sample-level RAW MS1 pattern support; they do not close `formal_shape_metric`. RT mode sidecars consume selected-apex mode assignments from overlay/iRT diagnostics and classify `rt_mode_pure`, `tag_backed_core_with_outlier_modes`, `irt_refined_mode_split`, `tailing_confounded`, or `consolidation_no_go`; tag-backed non-core mode membership fails closed as `rt_mode_not_supportive` and feeds the product activation `wrong_peak_conflict` rule, while tailing-confounded mode evidence remains diagnostic and must not force a split. PeakHypothesis selection sidecars convert RT-mode evidence into explicit product units: `product_candidate_core` is a mode-level candidate, `cross_mode_rescue_blocked` feeds `wrong_peak_conflict`, and `mode_split_required` / `consolidation_no_go` block family promotion until mode-aware consolidation exists. Sample negative sidecars can close `sample_level_negative_evidence` only with machine-observed `negative_evidence_class` in `no_candidate_ms1_evidence`, `pattern_mismatch`, `rt_not_explained`, or `local_peak_not_decisive`; `negative_evidence_detail` preserves narrower review reasons such as ugly shape, bad boundary, or QC reference conflict. Older overlay JSON without `rt` / `intensity` arrays remains readable but no longer counts as the full V2 formal-shape evidence chain. RAW overlay enrichment also records cell/local/global intensity metrics, including `cell_to_local_window_max_ratio`, and optional `peak_quality_*` vector fields for trace point count, boundary point count, S/N proxy, FWHM, sharpness, zigzag/noise, tailing, boundary margin, feature count, status, basis, and reason. Low shape correlation or low height can therefore be reviewed against selected-cell height dominance and trace-vector quality instead of being treated as automatic negative identity evidence. The generated MS1 sidecar remains conservative without overlay inputs: it uses existing alignment-cell apex/boundary/family-reference facts plus optional matrix RT drift policy, leaves raw shape correlation empty, and reports weak boundary-only or unmodeled-shift cases as `inconclusive` rather than false conflicts. Generated/read matrix RT drift policy evidence can close `matrix_rt_drift_policy` when supportive, and it fails closed as a machine-observed conflict when contradictory. The matrix RT drift producer reuses existing `alignment_cells.tsv`, optional `owner_edge_evidence.tsv`, optional `rt_normalization_families.tsv`, and optional paired `targeted_istd_benchmark_summary.tsv` + `rt_normalization_leave_one_anchor_out.tsv`; optional ISTD RT trend and phase-summary TSVs are provenance-only trend evidence, not a new fitted RT model. The targeted-ISTD anchor-local trend path is coverage-gated for 85RAW-like injection-order evidence and must not be used to tune RT policy from 8RAW method-smoke subsets. RAW-backed fallback is opt-in only through `--candidate-ms2-pattern-raw-dll-dir`; it uses the batch-index `raw_file`, existing `neutral_loss.collect_candidate_ms2_evidence`, and reports `sample_boundary_aligned` evidence only when a boundary/apex-aligned precursor MS2 scan is observed. Missing MS2 stays `not_observed`, not negative identity evidence; when MS1 is strongly supportive, MS1 intensity is at least `2.5e4`, RAW-boundary MS2 triggers are at least three, and the MS2 trace-strength proxy is moderate/strong, the missing expected NL is recorded as `dda_missing_nl_policy_status=not_dispositive` instead of a hard fail. This diagnostic does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbooks, selected peaks, backfill, Tier 2 support, or downstream contracts, and it must not claim production readiness.
**QC reference policy note**: QC MS1 reference evidence is no longer nearest-QC-wins. `nearest_valid_qc_local_condition_only` is context only; only consensus-backed levels such as `qc_consensus_with_local_qc_overlay` or `qc_consensus_qc_overlay` can close `formal_pattern_metric`, and local-vs-consensus disagreement stays review-only. A QC consensus conflict is not a standalone veto when the target cell already has sample-level RAW MS1 pattern support; it supports product blocking only together with sample-level pattern/RT/PeakHypothesis wrong-peak evidence.

**Activation note**: With `--enable-product-activation-contract`, V2 shadow mode also writes `shared_peak_identity_activation_decisions.tsv` and `shared_peak_identity_activation_acceptance.tsv`. These files translate sidecar statuses into `auto_activate`, `auto_block`, `confidence_only`, or `review_required` product-effect candidates under the current quant-matrix/productization owners (`docs/product/quant-matrix.md`, `docs/product/productization.md`; retired sidecar-to-product activation spec provenance retained in file-management manifests); they remain diagnostic until a separate product wiring flag consumes them. Activation decisions keep `feature_family_id`/`candidate_container_id` as provenance and carry `peak_hypothesis_id` plus `activation_unit_scope` when a mode-level PeakHypothesis is the product candidate unit. When Slice 1 blast-radius is enabled, activation acceptance uses the current `all_available_85raw` blast-radius `assessed_row_count` as the row denominator and emits explicit basis fields so seed-scope decisions are not confused with a full production matrix mutation. `--activation-must-not-regress-tsv` evaluates a machine-readable expectation manifest such as `docs/superpowers/fixtures/shared_peak_identity_activation_must_not_regress_v1.tsv`; the manual `--activation-must-not-regress-status` flag is only a diagnostic fallback.

**Mode generalization note**: `--generate-rt-mode-evidence` can also consume repeated `--rt-mode-evidence-overlay-trace-data-json` inputs from RAW-backed `family_ms1_overlay_*` trace JSONs. iRT/assignment rows take precedence when the same family/sample is present. RAW-overlay-only split evidence is emitted as `raw_mode_review_only`; it can feed review queues and PeakHypothesis coverage, but it must not directly auto-block promotion or override the activation must-not-regress set without independent iRT/tag support. Use `--generate-mode-hypothesis-assignment` when the V2 flow should produce product-facing sample-level PeakHypothesis selection from typed iRT/mode evidence plus MS1, QC, RT-drift, and MS2 opportunity sidecars; this supersedes raw-overlay-derived mode selection for activation-facing evidence. Legacy `--generate-peak-hypothesis-selection` output is retained as diagnostic/review evidence; activation-facing auto-activation requires `peak_hypothesis_authority_source=typed_mode_hypothesis_assignment` or a locked oracle manifest. Overlay JSON `mode_windows` product-status fields are ignored by matrix construction and demoted to review-only candidates.

**Hypothesis consistency note**: `--generate-hypothesis-consistency` summarizes `scope=sidecar_key_union` by default. Interpret it as full-matrix only when the input PeakHypothesis, MS1 pattern, matrix RT drift, QC reference, and candidate MS2 sidecars are themselves full-matrix; representative or manual-oracle sidecars remain representative diagnostics.

**Opportunity-policy note**: RAW-backed MS1 overlay height/local-window evidence can satisfy `intensity_opportunity_metric` without a separate Tier 2 trace sidecar when it comes from `family_ms1_overlay_raw_trace` or the RAW trace-vector basis and intensity is at least `2.5e4`. Missing NL/product evidence with `raw_ms2_diagnostic_product_absence_reason=product_outside_diagnostic_window` is recorded as `dda_missing_nl_policy_status=not_dispositive` when the family has required-tag context, the sample has enough boundary MS2 trigger opportunity, and MS1 evidence is supportive; it remains non-dispositive evidence, not an auto-activation rule.

---

### `apply_shared_peak_identity_activation.py`

**Purpose**: Apply accepted `shared_peak_identity_activation_*` sidecars to alignment product outputs.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/product_activation.py`
**Originating owner/plan**: `docs/product/quant-matrix.md`; retired sidecar-to-product activation spec provenance.
**Status note**: This is the explicit sidecar-to-product bridge. It requires
`activation_acceptance_status=pass` by default. `--output-mode activated-copy`
writes `alignment_matrix_activated.tsv`, `alignment_review_activated.tsv`, and
`alignment_cells_activated.tsv`; `--output-mode formal` writes the formal
downstream contract files `alignment_matrix.tsv`, `alignment_review.tsv`, and
`alignment_cells.tsv` in the requested output directory. Formal
`alignment_matrix.tsv` remains the downstream `Mz` / `RT` / sample-column
matrix; `peak_hypothesis_id` is written to
`activation_hypothesis_identity.tsv` as an identity/provenance sidecar, not as a
replacement matrix row key. Formal mode also writes
`alignment_matrix_identity.tsv` with `matrix_row_index`, `Mz`, `RT`,
`peak_hypothesis_id`, and `source_feature_family_ids` so downstream tools such
as `targeted_istd_benchmark.py` can read the public matrix directly. When the
source matrix is already the public
`Mz` / `RT` / sample-column matrix, `--alignment-matrix-identity-tsv` supplies
the bridge back to internal provenance and `feature_family_id` remains
debug/provenance only. Optional `--rt-mode-evidence-tsv` lets formal output use
matching typed mode `raw_selected_rt` values as area-weighted
PeakHypothesis-level RT centers for split rows; rows without matching typed mode
evidence fall back to their source family RT and disclose that fallback through
`alignment_matrix_identity.tsv:center_rt_basis`. Optional
`--candidate-ms2-pattern-evidence-tsv`,
`--ms1-pattern-coherence-evidence-tsv`,
`--qc-ms1-pattern-reference-evidence-tsv`, and
`--matrix-rt-drift-policy-tsv` project typed shared-peak evidence into rescued
cell `backfill_*` fields so the downstream backfill promotion gate can consume
RT, MS1 pattern/QC, and optional MS2/NL opportunity facts from the same
activation output. MS1 product support requires product-authorized sidecars
from the backfill authorizers; Candidate MS2 rows are optional context and must
also carry product-authorized provenance before they are displayed as reviewed
context. Raw diagnostic sidecars remain review-only and fail closed. Missing sidecars do not invent support; stale cells without projection
columns remain fail-closed for gate/report consumers. Formal activation keeps
`feature_family_id` as provenance/debug only and treats `peak_hypothesis_id` as
the internal product identity sidecar. By default, unresolved rows without
split/wrong-peak/mode evidence are excluded from emitted `alignment_matrix.tsv`
and reported in `family_projection_rows_excluded` /
`family_projection_cells_excluded`. These exclusions keep
`canonical_row_identity_ready=FALSE` with
`canonical_row_identity_blockers=family_projection_excluded_incomplete_scope`;
projection-free emitted rows are not proof that the full legacy family matrix has
been completely split. `--exclude-family-projections` is the formal-mode default
and remains as a compatibility flag. `--include-family-projections` is
diagnostic-only opt-in: it writes `<feature_family_id>::family_projection` rows
with `row_identity_basis=family_projection_no_split_evidence`, reports
`canonical_row_identity_blockers=family_projection_present`, and must not be
handed to downstream as a product matrix. `--require-complete-peak-hypothesis-identity`
must fail while included or excluded projection rows remain. Optional
`--legacy-rt-row-oracle-xlsx` adds
context-only `legacy_rt_row_context_id` hints from a legacy MZmine RT-row
workbook but does not mint product identities; matching outputs report
`legacy_rt_row_context_authority=context_only_not_identity_authority`. Both
modes also write `activation_application_summary.tsv` and
`activation_value_delta.tsv`. Existing matrix values are preserved; only missing
`auto_activate` cells are written, while `auto_block` cells can be blanked or
family promotion can be blocked. `activation_value_delta.tsv` records original
value, activated value, source cell area, effect, `value_changed`, and v3
matrix-value provenance fields such as `matrix_value_kind`,
`matrix_value_source`, `matrix_value_source_field`,
`matrix_value_source_artifact_sha256`, and `matrix_value_source_row_sha256` for
review. Matrix-only `activation_values.tsv` input must carry
`projected_matrix_value_source`, source artifact schema/hash, source row hash,
and source provenance detail; the source artifact hash must be a real lowercase
SHA256 provenance digest, not an id-derived placeholder. Naked projected values
fail closed. If multiple
provenance rows share one formal hypothesis/sample value,
the temporary pre-AsLS policy records the conflict and keeps the larger numeric
value. Formal mode refuses to overwrite source alignment TSVs unless
`--allow-overwrite-source` is passed. `--allow-non-passing-acceptance` is a
diagnostic override only and must not be used for product claims.

`--matrix-only` is the reviewed normal-peak backfill cost-control path. It
requires `--activation-values-tsv`, `--alignment-matrix-tsv`,
`--alignment-matrix-identity-tsv`, and `--alignment-review-tsv`, but does not
require or read `--alignment-cells-tsv`. It writes only `alignment_matrix.tsv`,
`alignment_matrix_identity.tsv`, `activation_hypothesis_identity.tsv`,
`activation_value_delta.tsv`, and `activation_application_summary.tsv`.
`alignment_cells.tsv` remains the audit/debug ledger for full activation copies
and evidence projection; it is not a product dependency for matrix-only
activation when product-authorized activation values are available. Matrix-only
written values are tagged in `activation_value_delta.tsv` as
`matrix_value_kind=backfill_activation` with
`matrix_value_source=activation_values_tsv`, source artifact hash, and source
row hash, so downstream reviewers can distinguish reviewed backfill values from
primary detected values.

---

### `build_mode_hypothesis_assignment.py`

**Purpose**: Build typed sample-level PeakHypothesis assignment rows from `shared_peak_identity_rt_mode_evidence.tsv` plus candidate MS2 tag/opportunity, RAW-backed MS1 pattern, QC MS1 reference, and matrix RT drift policy sidecars.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/mode_hypothesis_assignment.py`
**Originating owner/plan**: `docs/product/quant-matrix.md`; retired sidecar-to-product activation spec provenance.
**Status note**: This is the product-facing producer for mode-aware sample assignment. It writes the existing `shared_peak_identity_peak_hypothesis_selection.tsv` schema so downstream matrix construction and activation can keep using `peak_hypothesis_id`, but the selection is no longer inferred from family overlay raw modes. A sample can become `product_candidate_core` only when typed iRT/mode evidence assigns it to a mode that has required-tag support and the sample-level MS1 shape, QC context, RT-drift policy, and boundary/MS2 opportunity evidence do not conflict. QC is a context/conflict gate, not a hard required gate: missing QC does not block when sample-level MS1, RT drift, and MS2/tag evidence support the assignment. RAW-selected or raw-overlay-only modes stay `raw_mode_review_only`; a family with no required tag becomes `consolidation_no_go`; a sample assigned to a non-tag mode while another mode carries the tag becomes `cross_mode_rescue_blocked`.

---

### `evaluate_mode_window_assignment_contract.py`

**Purpose**: Evaluate the Mode-Window Assignment Contract Gate v0 sentinel fixture against 85RAW sidecars.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/mode_window_assignment_gate.py`
**Originating owner/plan**: `docs/product/quant-matrix.md`; retired sidecar-to-product activation spec provenance.
**Status note**: This acceptance diagnostic consumes `docs/superpowers/fixtures/shared_peak_identity_mode_window_assignment_contract_v0.tsv`, a PeakHypothesis selection sidecar, and optional activation, matrix-summary, QC-reference, RT-drift, MS1-pattern, and candidate-MS2 sidecars. It writes `shared_peak_identity_mode_window_assignment_gate.tsv` plus `shared_peak_identity_mode_window_assignment_summary.tsv`. The gate checks explicit selection `peak_hypothesis_id`, activation `peak_hypothesis_id`, `product_unit_scope`, `selected_mode_id`, and `activation_unit_scope`, so legacy status/action rows cannot fake a typed mode-window acceptance. It also checks that fixture `required_evidence_oracle` requirements are backed by the supplied sidecars when the oracle names MS1, QC, RT, MS2, or tag evidence. The gate verifies that typed mode assignment can pass product-candidate and wrong-peak sentinel rows, that raw/overlay-only modes remain activation-ineligible, that QC and ISTD trend artifacts are treated as context rather than standalone identity authorities, and that any matrix with family-projection rows does not overclaim complete canonical row identity. A passing summary with `canonical_row_identity_ready=FALSE` is scoped contract acceptance only; it intentionally keeps product activation and complete canonical row identity blocked until matrix construction is ready. Use `--fail-on-gate-failure` when the command is serving as an actual acceptance gate instead of a diagnostic artifact writer.

---

### `build_peak_hypothesis_matrix.py`

**Purpose**: Build a diagnostic PeakHypothesis-assigned construction artifact before product activation by consuming source alignment TSVs plus `shared_peak_identity_peak_hypothesis_selection.tsv`, optional `shared_peak_identity_hypothesis_consistency.tsv`, and optional RAW-backed `family_ms1_overlay_*` trace-data JSONs with mode windows.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/peak_hypothesis_matrix.py`
**Originating owner/plan**: `docs/product/quant-matrix.md`; retired sidecar-to-product activation spec provenance.
**Status note**: This is the first construction boundary for mode-aware identity, not the final downstream matrix contract. It writes `alignment_matrix.tsv`, `peak_hypothesis_inventory.tsv`, `peak_hypothesis_cell_assignments.tsv`, and `peak_hypothesis_matrix_summary.tsv` in a separate output directory as diagnostic construction artifacts. In this artifact, row identity is `peak_hypothesis_id`; explicit product-candidate modes use `row_identity_basis=matrix_construction_peak_hypothesis`, unresolved legacy cells use `<feature_family_id>::family_projection` with `row_identity_basis=family_projection_no_split_evidence`, and hard split/wrong-peak consistency blockers are recorded in assignments but not written to the artifact matrix. This `alignment_matrix.tsv` must not be handed to downstream as the final matrix until it is converted back into the public `Mz` / `RT` / sample-column contract with a separate hypothesis identity sidecar. When `--overlay-trace-data-json` is supplied, each declared `mode_windows` peak with RAW trace signal is enumerated as an expanded candidate row before matrix construction, so two peaks from the same `feature_family_id` can both enter the construction artifact as independent `peak_hypothesis_id` rows instead of competing through retarget. Any product-status fields embedded in overlay JSON `mode_windows` are ignored and demoted to `raw_mode_review_only`; only typed PeakHypothesis selection rows can supply product-facing authority. Untyped mode windows and raw-apex inferred windows are review-only expanded candidates: they use `construction_assignment_status=expanded_candidate`, default to `candidate_value_basis=raw_overlay_window_trapezoid_area`, set `canonical_row_identity_ready=FALSE` with `canonical_row_identity_blockers=raw_mode_review_only`, and must not be treated as product-ready row identity until a typed mode-hypothesis contract supplies explicit iRT/manual/tag-confirmed status. Family projection rows set `canonical_row_identity_blockers=family_projection_present`; they are acceptable bridge rows but not proof of complete canonical identity. Cells with source `alignment_cells.tsv` area but no source matrix value are recorded as candidate context only (`matrix_value_effect=source_matrix_value_missing`) until quantification/baseline policy is wired. `--require-complete-peak-hypothesis-identity` now fails unless the summary reports `canonical_row_identity_ready=TRUE`, so projection-heavy diagnostic outputs cannot be mistaken for promotion-ready matrices. This tool is `diagnostic_only`: it proves that split/wrong-peak separation can happen before matrix output, but it does not mutate source alignment artifacts, does not define the downstream `Mz` / `RT` / sample-column matrix, and does not claim all-family split-science readiness.

---

### `diagnose_shared_peak_wrong_peak.py`

**Purpose**: Diagnose `wrong_peak_conflict` activation blocks and propose RAW-overlay alternate peak candidates without retargeting product outputs.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/wrong_peak_root_cause.py`
**Originating owner/plan**: `docs/product/quant-matrix.md`; retired sidecar-to-product activation spec provenance.
**Status note**: This diagnostic consumes `shared_peak_identity_activation_decisions.tsv`, `shared_peak_identity_machine_evidence_support.tsv`, `alignment_cells.tsv`, and optional `family_ms1_overlay_*` trace-data JSON files. It writes `shared_peak_identity_wrong_peak_root_cause.tsv` with the selected-cell evidence, root-cause class, selection failure mode, and strongest alternate local maximum outside the selected boundary. Alternate peaks are review candidates only; this tool does not mutate `alignment_matrix.tsv`, product labels, selected peaks, backfill, or activation decisions.

---

## Alignment Diagnostics

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

### `alignment_decision_report.py`

**Purpose**: Render an HTML decision report from alignment diagnostics.
**Topic group**: `alignment_decision_report.py` + `_io`, `_styles`, `_components`, `_model`, `_rendering` (6 files)
**Originating spec**: `retired-provenance:038bfb65fe90` (decomposition target PR1) + `retired-provenance:100307efc8fa` Workstream G
**Note**: Rendering / components / styles split landed in commit `4549cdd 2026-05-24`.

---

### `untargeted_alignment_guardrails.py`

**Purpose**: Compute and compare untargeted alignment guardrails.
**Topic group**: `untargeted_alignment_guardrails.py` + `_io`, `_models`, `_metrics`, `_outputs`, `_targets` (6 files)
**Originating plan**: `retired-provenance:6a962d15a062`
**Spec status**: ACTIVE facade per `retired-provenance:100307efc8fa:638` ("now stays as the CLI/orchestration compatibility facade").

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
outputs so repeated 8RAW/85RAW validation can quickly surface owner ambiguity,
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

### `backfill_scope_probe.py`

**Purpose**: Probe alignment backfill scope size without running owner backfill.
**Topic group**: `backfill_scope_probe.py` (single-file)
**Originating doc**: `retired-provenance:8742b42cba88` (recent investigation context)

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

### `seed_aware_backfill_review.py`

**Purpose**: Build a seed-aware shadow review for rescued-heavy backfill families.
**Topic group**: `seed_aware_backfill_review.py` + `_constants`, `_io`, `_model`, `_writers` (5 files)
**Originating spec**: (none found; pre-dates the lifecycle spec)

---

### `family_ms1_backfill_review_report.py`

**Purpose**: Build a review queue for low-seed/high-backfill MS1-supported families.
**Topic group**: `family_ms1_backfill_review_report.py` + `_io`, `_model`, `_writers` (4 files)
**Originating spec**: (none found explicitly; appears in post-PR60 cleanup spec line ~183 as Workstream G product)

---

### `low_ms1_assessable_coverage_audit.py`

**Purpose**: Classify low_ms1_assessable_coverage_review families as RT/window, single-center XIC, or primary-backfill support issues.
**Topic group**: `low_ms1_assessable_coverage_audit.py` + `low_ms1_coverage_review_{classifier, loaders, models, writers}` (5 files; 683-line classifier is the largest)
**Originating spec**: `retired-provenance:f59e13685ecc`

---

### `ms1_index_backfill_audit.py`

**Purpose**: Audit owner-backfill vendor XIC versus MS1-index XIC.
**Topic group**: `ms1_index_backfill_audit.py` (single-file)
**Originating note**: `retired-provenance:10d87af35fec` (recent investigation context)

---

### `owner_backfill_request_economics.py`

**Purpose**: Summarize owner-backfill request cost by final row identity.
**Topic group**: `owner_backfill_request_economics.py` (single-file)
**Originating spec**: `retired-provenance:fa041eff0067`
**Cleanup note**: Cell-row preparation now builds per-family cell groups and
first-seen sample order in one input index while preserving feature row order,
sample order, and output TSV/JSON/Markdown schemas.

---

### `provisional_backfill_candidate_gate.py`

**Purpose**: Emit a `diagnostic_only` machine sidecar for retained provisional backfill rows, including Tier 2 support components, challenge blockers, and source artifact hashes.
**Topic group**: `provisional_backfill_candidate_gate.py` + `xic_extractor/alignment/production_candidate_gate.py`
**Originating spec/plan**: `retired-provenance:481a2032bd31`; `retired-provenance:902c312d3c37`
**Status note**: Writes `alignment_production_candidate_gate.tsv`; optional Tier 2 support must come from `--tier2-trace-evidence-tsv` plus `--tier2-raw-manifest-tsv`, not direct `alignment_review.tsv` tokens. Does not mutate `alignment_review.tsv`, `alignment_matrix.tsv`, workbook schemas, or downstream correction/statistics contracts.

---

### `retained_backfill_evidence_gate.py`

**Purpose**: Emit a `diagnostic_only` machine sidecar for product-retained backfill family/seed groups, linking actual primary-matrix backfill behavior to seed provenance, MS1 overlay support/blockers, missing evidence, and source artifact hashes.
**Topic group**: `retained_backfill_evidence_gate.py` + `xic_extractor/diagnostics/retained_backfill_evidence_gate.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `alignment_retained_backfill_evidence_gate.tsv`, `alignment_retained_backfill_evidence_gate.json`, `alignment_retained_backfill_missing_overlay_queue.tsv`, and `alignment_retained_backfill_overlay_review_queue.tsv`. It consumes existing alignment review/cell/matrix, optional owner backfill seed audit, and optional overlay summary TSVs only. `detected=0` families are excluded from main rows and counted separately. Exact `seed_group_id` overlay rows are required for seed-specific MS1 support/blocker decisions; legacy overlay rows without `seed_group_id` are retained only as family context and are re-queued as `missing_seed_specific_overlay`. Missing-overlay rows with seed provenance are emitted as queues consumable by `family_ms1_overlay_batch.py`; the full missing-overlay queue and family-deduplicated review queue share one ordered candidate list during output writing. It does not accept RAW/DLL paths, does not generate overlays, does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbook schemas, or product decisions, and does not declare production readiness.

---

### `backfill_shadow_policy_report.py`

**Purpose**: Emit a `diagnostic_only` cell-level MS1+RT shadow-policy report for retained backfill seed groups, showing which rescued cells already fill now, which would fill under an MS1 own-max + RT policy, which still need MS1 same-peak evidence, and which are blocked by missing seed/overlay evidence or visual-review blockers.
**Topic group**: `backfill_shadow_policy_report.py` + `xic_extractor/diagnostics/backfill_shadow_policy.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `backfill_shadow_policy_cells.tsv`, `backfill_shadow_policy_summary.json`, and `backfill_shadow_policy_report.html`. It consumes existing `alignment_cells.tsv`, `alignment_retained_backfill_evidence_gate.tsv`, optional `alignment_matrix.tsv` for source hashing, and optional overlay batch summaries for own-max metric display. Candidate MS2 context is displayed as auxiliary provenance only; missing candidate-MS2 product authority is not a production gap when MS1 same-peak evidence is otherwise supportive. Decision/reason/gap serialization uses the shared diagnostics-only `BackfillDecisionExplanation` payload, but the shadow-policy decision taxonomy remains separate from `shadow_production_projection.py`. The report does not accept RAW/DLL paths, generate overlays, compute a composite score, mutate alignment artifacts, workbook schemas, or product decisions, and remains a calibration surface for a future reviewed production-policy contract.

---

### `shadow_production_projection.py`

**Purpose**: Emit a `shadow_projection_only` cell-level current-production-decision vs projected-decision sidecar for retained backfill seed groups, using formal `build_production_decisions()` as the current product snapshot and applying the reviewed shadow criteria only as a projection.
**Topic group**: `shadow_production_projection.py` + `xic_extractor/diagnostics/shadow_production_projection.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `shadow_production_projection_cells.tsv` and `shadow_production_projection_summary.json`. The tool entry point is a thin CLI facade; reusable TSV loading, production-decision reconstruction, matrix cross-check loading, and output writing live in `xic_extractor/diagnostics/shadow_production_projection.py`. Rows expose `current_matrix_written`, `current_matrix_source`, `current_production_status`, `shadow_decision` (`accept` / `block` / `context`), `projected_matrix_written`, reasons, warnings, `product_authority_chain`, detected-anchor count, request-window overlap status, and overlay provenance. Decision reason/warning serialization uses the shared diagnostics-only `BackfillDecisionExplanation` payload; the projection decision taxonomy remains separate from `backfill_shadow_policy_report.py`. `product_authority_chain` is the compact MS1 product-rule / optional candidate-MS2 / same-peak trace consumed by gallery review; it is not a matrix schema change. When both `--alignment-matrix-tsv` and `--alignment-matrix-identity-tsv` are supplied, `current_matrix_written` and `current_matrix_value` are grounded in the actual public matrix cell and `current_matrix_source=alignment_matrix_tsv`; otherwise the tool falls back to the formal production-decision snapshot. Matrix cross-check loading limits materialized public matrix cells to retained-gate requested `(family, sample)` keys while preserving source-family aliases and the production snapshot fallback. Projection no longer lets `visual_support` alone create a projected write: `accept` requires the same product-authorized evidence chain used by promotion (`trace_constellation` RAW-overlay MS1 same-peak own-max support or standard-peak gate MS1 support, same-peak reason, and a positive projected matrix value). Seed provenance plus MS1 same-peak visual support without formal product authority is emitted as `shadow_decision=context` with `shadow_reasons=identity_supported_review`; it keeps the positive projected value for reviewed allowlist calibration but leaves `projected_matrix_written=FALSE`. Candidate MS2 is auxiliary context for backfill cells; missing candidate-MS2 product authority does not block projection because those cells would already be detected if they had the required NL tag. Product-authorized same-peak rows may pass old retained-gate `evidence_missing` / missing-overlay states, while missing detected anchors, missing selected peak segments, outside-window cells, explicit wrong-peak/hypothesis blockers, hard MS1 blockers, and retained-gate `review_required_*` challenge blockers remain closed or context. `same_peak_multi_claim` / DUP is a warning instead of a hard blocker when that product-authorized same-Gaussian evidence chain is present; without that chain it remains `context`. `local/global` dominance is annotation only and does not hide traces. The summary separately reports `gate_row_count`, `projectable_gate_row_count`, `unprojectable_gate_row_count`, and `unprojectable_gate_reasons`; `row_count=0` can therefore mean that retained gate rows lacked seed/cell provenance such as `missing_seed_audit`, not that risk was absent. This tool does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbooks, or product decisions.

---

### `shift_aware_backfill_calibration_pack.py`

**Purpose**: Build a standard-peak calibration/review pack from shift-aware family overlay summaries, reconciliation groups, and overlay batch summaries.
**Topic group**: `shift_aware_backfill_calibration_pack.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: Writes `shift_aware_backfill_calibration_pack.tsv/html` and, when `--shift-aware-summary-dir` is supplied, `shift_aware_family_best_shift_summary.tsv`. Manual labels in this pack are calibration oracle fields only; they are not a production whitelist. The default standard-peak machine threshold is `--min-shape-r 0.95`, matching the accepted broad-but-standard Gaussian-smoothed peak contract; stricter thresholds may be used for review experiments but should not silently replace the product gate. The pack can aggregate multiple `*_source_family_best_shift_summary.tsv` files and records non-reference source-family count, best-shift shape similarity, max absolute shift, overlay verdict, and provenance paths for downstream machine-gate review. Source-family summary aggregation uses a per-family accumulator while reading summary rows, so reference-only families and non-reference rows without usable shape similarity are skipped without materializing all rows for a second pass.

---

### `shift_aware_standard_peak_gate_calibration.py`

**Purpose**: Convert shift-aware calibration rows into a machine standard-peak gate that separates supported standard-peak same-pattern cases from blocked/review-only cases.
**Topic group**: `shift_aware_standard_peak_gate_calibration.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: Writes `shift_aware_standard_peak_gate_calibration.tsv` and summary JSON. `standard_peak_gate_supported` requires the configured shift-aware shape threshold and a Gaussian-smoothed family overlay verdict that supports standard-peak backfill. Rows without manual labels are reported as `unlabeled_machine_supported` or `unlabeled_machine_blocked`; they are valid machine-gate candidates but are not counted as manual false positives/negatives. Non-standard, missing-overlay, stale-provenance, and conflict rows remain fail-closed or review-only.

---

### `standard_peak_ms1_authority_bundle.py`

**Purpose**: Convert standard-peak gate rows plus overlay trace provenance into a product-authorized MS1 same-peak sidecar consumable by shadow projection.
**Topic group**: `standard_peak_ms1_authority_bundle.py` + `xic_extractor/diagnostics/standard_peak_ms1_authority_bundle.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: Writes `standard_peak_ms1_pattern_coherence_evidence.tsv`, `standard_peak_ms1_product_authority_allowlist.tsv`, `shared_peak_identity_ms1_pattern_coherence_product_authorized.tsv`, an audit TSV, and summary JSON. `--authority-mode manual-oracle` keeps the historical manual calibration requirement; `--authority-mode machine-gate` authorizes rows from `standard_peak_gate_supported` without requiring a manual status label and records `machine_standard_peak_gate_authorized` / `machine_shift_aware_standard_peak_gate` provenance. Trace JSON read/decode/SHA work is cached per path within one run, but family/sample/vector validation and audit reasons remain per row. Trace JSON path/SHA validation, Gaussian-smoothed standard-peak family verdicts, and `same_peak_reason:shift_aware_standard_peak_gate_supported` are preserved before any projection can write matrix values.

---

### `standard_peak_heldout_oracle_results.py`

**Purpose**: Evaluate deterministic standard-peak held-out oracle manifest rows against observed boundary/area results and write `heldout_oracle_results.tsv`.
**Topic group**: `standard_peak_shadow_activation_inputs.py` + `xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Thin CLI wrapper around the package-level held-out oracle evaluator. It reads `heldout_oracle_manifest.tsv`, observed result rows with `oracle_case_id`, observed boundary/area fields, and observed-result provenance fields, records the source artifact SHA, and writes the existing `standard_peak_seed_guard_heldout_oracle_results_v1` schema. Manifest rows must declare `heldout_original_cell_status` proving the masked source cell was originally detected; `rescued`, blank, or unknown statuses fail before result evaluation. Observed rows must identify an independent product-writer, masked-rerun, or boundary-reintegration source; oracle/manual-review/review-queue source copies fail closed after punctuation/whitespace canonicalization, and neutral observed source labels also fail when they canonicalize to the same value as the matching manifest `oracle_source`. The result source artifact must exist so its SHA can be recorded. It does not run RAW, generate oracle rows, mutate matrices, authorize non-standard peaks, or upgrade the standard-path seed guard beyond `production_candidate`; real/reviewed held-out oracle rows plus 85RAW expected-diff evidence are still required for `production_ready`.

---

### `standard_peak_heldout_trace_oracle.py`

**Purpose**: Build deterministic held-out trace reintegration oracle packets from existing standard-peak trace JSON and detected cell evidence.
**Topic group**: `standard_peak_heldout_trace_oracle.py` + `xic_extractor/diagnostics/standard_peak_heldout_trace_oracle.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Reads `alignment_backfill_cell_evidence.tsv` plus existing `*_trace_data.json` artifacts and writes a full eligible pool, selected candidate TSV, `heldout_oracle_manifest.tsv`, `heldout_observed_results.tsv`, `heldout_oracle_results.tsv`, and summary JSON. It supports the existing `standard_high_signal_clean_trace` scope and the additive `standard_low_scan_clean_trace` scope, where scan count is 7-9 while trace status, shape, local/global ratio, height, width, and apex-delta remain clean. It also supports additional probe scopes: `standard_low_height_clean_trace`, where the clean envelope is retained but height is below 2e6; `standard_low_height_low_scan_clean_trace`, where height is below 2e6 and scan count is 7-9 while shape/local-global/width/apex remain clean; `standard_apex_delta_clean_trace`, where the clean envelope is retained but apex delta from family center exceeds 0.15 min; `standard_width_clean_trace`, where the clean envelope is retained but boundary width is outside 0.30-0.65 min; `standard_shape_margin_clean_trace`, where the clean envelope is retained but shape similarity is only the near-threshold band `0.93 <= shape < 0.95`; `standard_low_height_reintegration_stable_candidate_family_trace`, where candidate families are derived from a reintegration-stability audit plus activation scope audit and then filtered to low-height written rows; `standard_shape_clean_reintegration_stable_candidate_family_trace`, where the same family-level oracle is filtered to activation-scope shape similarity `>=0.95`; and `standard_reintegration_stable_candidate_family_trace`, where the family-level oracle is run over all reintegration-stability eligible written rows without the low-height or shape-clean filter. The first 85RAW no-RAW low-height probe found 230 eligible candidates across 54 families, selected 20 family cases, and failed closed at 19/20 selected cases because `FAM008651/TumorBC2312_DNA` exceeded the accepted boundary tolerance. The later bounded-window low-height diagnostic replay kept the default full-trace mode unchanged, added explicit `expected_window_bounded` observed reintegration with `expected_window_padding_min`, and passed selected 20/20 at `padding=0.5 min` with max boundary error 0.0857986 min and max area relative error 0.0564106; the matching explicit 57-row low-height scoped writer now has writer expected-diff approval and `readiness_tier=production_ready`. The formal low-height-low-scan bounded replay found 210 eligible rows across 51 families, selected 20 family cases, and passed 20/20 with max boundary error 4.80376e-05 min and max area relative error 0.00881912; the matching explicit 69-row low-height-low-scan scoped writer now has writer expected-diff approval and `readiness_tier=production_ready`. The formal low-height reintegration-stable replay is a family-level oracle, not a 220-row identity oracle: it records 220 audit-intersection rows across 66 families, `candidate_family_scope_match_level=family_id`, 1520 available detected trace candidates from those families, selected 20 family cases, and passed 20/20 with max boundary error 0.0830019 min and max area relative error 0.0725986; the matching explicit 220-row writer now has writer expected-diff approval and `readiness_tier=production_ready`. Shape-clean reintegration-stable is candidate/explanation evidence only: `heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/` records 104 audit-intersection rows across 33 families, 334 available detected trace candidates across 31 families, selected 20/20 pass, max boundary error 0.0830019 min, and max area relative error 0.0725986, but a temporary writer probe found 0 new matrix writes and 104 unchanged/pre-existing matrix values, so no public writer flag was retained. Apex-delta, width-only, shape-margin, and all-stability remain blocked/candidate-only probe scopes: the first 85RAW no-RAW apex-delta probe found 78 eligible candidates across 27 families, selected 20 family cases, and failed closed at 17/20 selected cases with max boundary error 2.19621 min and max area relative error 0.424518; the first 85RAW no-RAW width-only probe found 4 eligible candidates across 3 families, selected 3 family cases, and failed closed at 1/3 selected cases with max boundary error 1.86561 min and max area relative error 0.599229; the first 85RAW no-RAW shape-margin probe found 18 eligible candidates across 8 families, selected 8 family cases, and failed closed at 6/8 selected cases with max boundary error 0.0625542 min and summary max area relative error 0.198393; the formal all-stability family replay under `heldout_trace_reintegration_oracle_all_stability_family/` records 299 audit-intersection rows across 77 families, expands to 1694 available detected trace candidates from those families, selects 20 family cases, and fails closed at 19/20 because `FAM000949/NormalBC2261_DNA` has area relative error 0.19621, above the accepted 10% ceiling. Observed rows are produced by local-minimum reintegration over stored trace arrays plus `integration_from_peak_trace` with gaussian15 positive AsLS morphology area, then evaluated by the shared held-out oracle evaluator under the existing `0.1 min / 10% area` ceiling. It does not open RAW, mutate matrices, authorize non-standard peaks, or broaden the Backfill writer by itself.

---

### `standard_peak_activation_scope_audit.py`

**Purpose**: Audit whether actual standard-peak `activation_value_delta.tsv` writes fall inside named trace evidence envelopes used by held-out oracles.
**Topic group**: `standard_peak_activation_scope_audit.py` + `xic_extractor/diagnostics/standard_peak_activation_scope_audit.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Reads an existing matrix-only `activation_value_delta.tsv`, the matching `shadow_production_projection_cells.tsv`, and sibling `*_trace_data.json` artifacts derived from `overlay_png_path`. It writes `activation_high_signal_clean_scope_audit.tsv`, `activation_high_signal_clean_scope_summary.tsv/json`, `eligible_activation_value_delta.tsv`, `narrow_activation_expected_diff_acceptance.tsv/json`, plus additive `low_scan_clean_activation_value_delta.tsv`, `low_scan_clean_activation_expected_diff_acceptance.tsv/json`, `low_height_clean_activation_value_delta.tsv`, `low_height_clean_activation_expected_diff_acceptance.tsv/json`, `low_height_low_scan_clean_activation_value_delta.tsv`, and `low_height_low_scan_clean_activation_expected_diff_acceptance.tsv/json`. The audit joins actual writes to projection rows by `matrix_value_source_row_sha256`, classifies missing projection/trace evidence separately from trace-matched ineligible rows, and applies four named envelopes: high-signal clean requires supported trace status, shape >=0.95, local/global >=0.95, height >=2e6, width 0.30-0.65 min, apex within 0.15 min of family center, and at least 10 boundary scans; low-scan clean uses the same clean envelope but requires height >=2e6 and 7-9 boundary scans; low-height clean keeps the clean trace/width/apex/scan envelope but requires height <2e6 and at least 10 boundary scans; low-height-low-scan clean requires height <2e6 and 7-9 boundary scans while shape/local-global/width/apex remain clean. The current 85RAW no-RAW combined audit covers 4613 broad candidate writes and classifies 72 high-signal clean, 42 low-scan clean, 57 low-height clean, and 69 low-height-low-scan clean rows; all four classes now have explicit scoped product writers with writer expected-diff approval. Each expected-diff acceptance verifies that the filtered eligible delta rows are exactly the eligible audit rows and contain no duplicate, missing, unexpected, non-eligible, non-written, unchanged, or blank-value rows. It does not open RAW, generate evidence, mutate matrices, or authorize product activation. Filtered delta TSVs are convenience artifacts only; product output must come from a separate productization run such as `standard_peak_backfill_productization.py --high-signal-clean-activation-scope-audit-tsv`, `--low-scan-clean-activation-scope-audit-tsv`, `--low-height-clean-activation-scope-audit-tsv`, or `--low-height-low-scan-clean-activation-scope-audit-tsv`, whose writer acceptance must pass before claiming a scoped product behavior.

---

### `standard_peak_reintegration_stability_audit.py`

**Purpose**: Audit whether standard-peak activation writes have boundary-stable stored traces under two no-RAW reintegration views.
**Topic group**: `standard_peak_reintegration_stability_audit.py` + `xic_extractor/diagnostics/standard_peak_reintegration_stability_audit.py`
**Originating spec/goal/plan**: `docs/superpowers/plans/2026-06-15-productization-control-plane.md`
**Status note**: Reads `activation_high_signal_clean_scope_audit.tsv` from `standard_peak_activation_scope_audit.py`, reloads the referenced `*_trace_data.json` rows, and reintegrates each written trace twice: full stored trace and an expected-window-bounded trace around the stored reference boundary. It writes `reintegration_stability_audit.tsv` plus `reintegration_stability_summary.tsv/json`. The input scope audit must carry `standard_peak_activation_scope_audit_v1` schema rows with nonblank source run ids; the summary records the upstream TSV SHA/source provenance. `--expected-window-padding-min` must be finite and <=0.5 min so the bounded reintegration remains an independent local check instead of collapsing into full-trace reintegration. A row is `eligible` only when full-trace and bounded-window boundary error are each <=0.1 min, full-trace and bounded-window area relative error are each <=10%, and the two reintegration methods agree within the same boundary/area tolerances. The first real no-RAW 85RAW run over the consolidated 4613-row activation scope found 299 eligible written rows, 3227 ineligible rows, and 1087 missing-evidence rows; 271 eligible rows were outside the four then-current production-ready scoped writer envelopes. This audit remains a `production_candidate` evidence class by itself, and the summary intentionally reports `status=candidate_pool_blocked`, `writer_authority_status=blocked`, and `production_ready=FALSE`: it proves a broader boundary-stability candidate pool exists, but it does not mutate matrices, create broad writer authority, replace masked/product-writer oracle evidence, or authorize broad 4613-row activation. A separate follow-up promoted only the low-height subset of this candidate pool: 220 low-height reintegration-stable rows have per-row stability evidence, a passing family-level heldout oracle over detected traces from the same families, plus explicit writer expected-diff and are now the fifth scoped `production_ready` Backfill writer.

---

### `standard_peak_shadow_activation_inputs.py`

**Purpose**: Convert product-authorized standard-peak `shadow_production_projection_cells.tsv` accepts into `product_activation --matrix-only` input TSVs.
**Topic group**: `standard_peak_shadow_activation_inputs.py` + `xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: This is part of the built-in `dna_dr` standard-peak
publication path. Treat it as product-adjacent activation infrastructure, not a
disposable research diagnostic.
**Status note**: Writes `standard_peak_activation_decisions.tsv`, `standard_peak_activation_values.tsv`, `standard_peak_activation_acceptance.tsv`, `standard_peak_activation_inputs.tsv`, `standard_peak_activation_inputs_summary.json`, and additive `seed_guard_decisions.tsv`. It selects only rows with `shadow_decision=accept`, `current_matrix_written=FALSE`, `projected_matrix_written=TRUE`, a nonblank projected value, and `same_peak_reason:shift_aware_standard_peak_gate_supported` in the product-authority chain. Current-matrix rows are counted as already written and are not reactivated; nonstandard, context, blocked, or unprovenanced rows fail closed. When a seed-guard context is supplied by the productization bridge, standard-path candidates are evaluated against the N-banded cohort seed-support rule before activation decisions are written; low-seed and inconclusive rows do not write activation decisions. Before writing acceptance, the converter runs a standard-peak row-level gate that checks PeakHypothesis scope, auto-activate decision shape, matching activation values, source schema, source row SHA, and the standard-peak same-peak reason. Passing rows generate an existing-contract `activation_acceptance.tsv` with `must_not_regress_status=pass`, a max product-affecting row allowance equal to the gated selected row count, and an activation scope derived from authority provenance: manual-oracle rows stay `manual_oracle_seed_rows`, while machine-gate rows are labeled `machine_gate_standard_peak_rows` with `must_not_regress_basis=machine_shift_aware_standard_peak_gate`. With `--apply-matrix-only`, the CLI immediately calls the existing matrix-only product activation writer and emits an activated matrix under `<output-dir>/activated_matrix` unless `--activated-output-dir` is supplied. If the standard-peak gate fails, acceptance stays fail and product application must stop for review. This converter does not generate upstream evidence, change source alignment artifacts, or bypass product activation; `apply_shared_peak_identity_activation.py --matrix-only` remains the matrix writer.

---

### `standard_peak_policy_observed_oracle.py`

**Purpose**: Build row-specific observed-oracle packets for generated Backfill
policy rows that are still `detected_flagged`.
**Topic group**: `standard_peak_backfill_productization.py` +
`xic_extractor/diagnostics/standard_peak_policy_observed_oracle.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: This is a machine evidence producer for the generated
Backfill policy engine, not a manual approval TSV and not a standalone writer
scope.
**Status note**: Consumes generated `standard_peak_backfill_policy.tsv` plus
the matching activation-scope audit. It selects policy rows with
`backfill_policy_decision=detected_flagged`, joins source rows by
`matrix_value_source_row_sha256`, reloads the stored trace JSON, recomputes
boundaries/area from the full stored trace, and writes
`standard_peak_policy_observed_oracle.tsv` plus `summary.tsv/json`. Results use
schema `standard_peak_policy_observed_oracle_v1`; only rows with
`oracle_case_status=pass`, `included_in_product_acceptance=TRUE`, and
`observed_result_source=policy_observed_full_trace_reintegration_v1` may be
consumed by `standard_peak_backfill_productization.py
--policy-observed-oracle-tsv --policy-observed-oracle-summary-json`. The current no-RAW 85RAW packet under
`output/productization_realdata_seed_guard_85raw_20260617/policy_observed_oracle_detected_flagged_full_trace/`
evaluated the prior 72 `detected_flagged` rows, passed 72/72, and recorded max
boundary error `8.91875e-05 min` plus max area relative error `0.098218`.
This producer does not open RAW, mutate matrices, loosen nonstandard-peak
policy, or authorize broad Backfill by itself; product behavior changes only
after generated policy replay and writer expected-diff pass.

---

### `standard_peak_backfill_productization.py`

**Purpose**: Run the reviewed standard-peak backfill path end to end: convert shadow projection accepts into activation inputs, apply matrix-only product activation, and optionally render an activation-synced reconciliation gallery.
**Topic group**: `standard_peak_backfill_productization.py` + `xic_extractor/diagnostics/standard_peak_backfill_productization.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: The `dna_dr` preset can reach this path after
`scripts/run_alignment.py` finishes base alignment. It can publish accepted
standard-peak values into the default alignment matrix outputs.
**Status note**: Requires `shadow_production_projection_cells.tsv`, `alignment_matrix.tsv`, `alignment_matrix_identity.tsv`, and `alignment_review.tsv`. It writes `standard_peak_backfill_productization_summary.tsv/json`, nests the converter output under `standard_peak_activation_inputs/`, and writes matrix-only activation outputs under `activated_matrix/`. It uses the same fail-closed standard-peak row gate as `standard_peak_shadow_activation_inputs.py`; rows without `same_peak_reason:shift_aware_standard_peak_gate_supported`, rows already written in the current matrix, context/block rows, rows without positive projected values, and standard-path rows failing the N-banded seed guard are not activated. The seed guard derives `total_N` from the pre-backfill matrix sample columns and `detected_count` from `alignment_review.tsv`, writes `seed_guard_decisions.tsv` for every standard-path candidate, and after matrix activation joins actual writes back from `activation_value_delta.tsv` by `peak_hypothesis_id/sample_stem`. Before activation, the orchestrator checks `current_matrix_written=TRUE` claims against the actual public matrix via `alignment_matrix.tsv` + `alignment_matrix_identity.tsv`; stale projection rows that claim an already-written current cell while the public matrix is blank fail fast and must be regenerated. With `--high-signal-clean-activation-scope-audit-tsv`, it fail-closed filters the writer to audit rows with `high_signal_clean_status=eligible`; the current real no-RAW 85RAW scoped writer has 72/72 expected-diff pass and `readiness_tier=production_ready`. With `--low-scan-clean-activation-scope-audit-tsv`, it filters to `low_scan_clean_status=eligible`; the current real no-RAW 85RAW scoped writer has 42/42 expected-diff pass and `readiness_tier=production_ready`. With `--low-height-clean-activation-scope-audit-tsv`, it filters to `low_height_clean_status=eligible`; the current real no-RAW 85RAW scoped writer has 57/57 expected-diff pass and `readiness_tier=production_ready`. With `--low-height-low-scan-clean-activation-scope-audit-tsv`, it filters to `low_height_low_scan_clean_status=eligible`; the current real no-RAW 85RAW scoped writer has 69/69 expected-diff pass and `readiness_tier=production_ready`. With `--low-height-reintegration-stable-activation-scope-audit-tsv` plus `--reintegration-stability-audit-tsv`, it derives `low_height_reintegration_stable_status=eligible` only for rows that are reintegration-stability eligible, still written in the activation scope audit, and below 2e6 height; the current real no-RAW 85RAW scoped writer has 220/220 expected-diff pass, records the stability audit path/SHA in both summary and expected-diff artifacts, and has `readiness_tier=production_ready`. With `--backfill-policy-source-audit-tsv`, the orchestrator generates `standard_peak_backfill_policy.tsv` and `standard_peak_backfill_policy_summary.json` over the supplied candidate universe, optionally using `--reintegration-stability-audit-tsv` and the paired `--policy-observed-oracle-tsv` / `--policy-observed-oracle-summary-json` evidence packet, then applies only generated `backfill_policy_decision=write_ready` rows with `expected_scope=backfill_policy_write_ready_rows`; generated `detected_flagged` and `blocked` rows remain audit-only. The generated policy source audit must carry `apex_aligned_shape_similarity`, so shape-clean stability can be recorded as candidate evidence without becoming writer authority; this tag also requires `matrix_value_effect=written`, trace match, family match, and shape similarity `>=0.95`. The observed-oracle TSV must be generated `standard_peak_policy_observed_oracle_v1` evidence with companion summary JSON; accepted rows must pass full-trace reintegration, have `included_in_product_acceptance=TRUE`, keep matching row SHA/family/sample, still be written/trace matched, and the summary must bind the oracle TSV to the source audit SHA and base generated-policy SHA. The generated policy TSV is a replay/audit artifact, not a manual allowlist, and malformed `write_ready` rows fail closed. Current generated policy schema is `standard_peak_backfill_policy_v2`; every row must carry nonblank `backfill_policy_reason`, `backfill_policy_decision_basis`, and `backfill_policy_next_evidence`, with `backfill_policy_candidate_evidence_class` showing candidate evidence even when writer authority is blocked. The latest real no-RAW 85RAW generated-policy replay over the 4613-row consolidated source audit produced 511 `write_ready`, 0 `detected_flagged`, and 4102 `blocked` rows, wrote exactly 511 matrix cells, passed writer expected-diff with zero blockers, and records 72 accepted `policy_observed_full_trace_reintegration` rows. The prior shape-clean explanation replay tags 104 rows with `shape_clean_reintegration_stable` candidate evidence: by authority/decision, 30 remain `review_only` / `detected_flagged` before observed-oracle promotion and 74 are already `writer_approved` / `write_ready`; by candidate-evidence string, 76 are `shape_clean_reintegration_stable,reintegration_stable`, 21 overlap `low_height_clean`, and 7 overlap `high_signal_clean`. This makes policy replay production-ready for current approved evidence classes plus accepted observed-oracle rows, not for broad 4613-row activation. Only one scoped audit or generated policy path may be supplied at a time. In scoped/policy mode it emits `narrow_product_writer_expected_diff_acceptance.tsv/json` and may claim `production_ready` only for that explicit writer scope when acceptance passes; it does not authorize the broad 4613-row activation set or the all-stability 299-row pool. There is no apex-delta, width-only, shape-margin, or shape-clean reintegration-stable writer flag; those diagnostic/evidence probes must remain candidate-only until they have reviewed oracle evidence plus explicit writer expected-diff approval on rows that actually change product output. With `--write-gallery`, it also consumes optional gallery artifacts and passes the generated `activation_application_summary.tsv` / `activation_value_delta.tsv` into `backfill_evidence_reconciliation_gallery.py`, so the HTML distinguishes existing accepted-rescue writes from newly activated matrix writes. This orchestrator does not generate upstream evidence, rerun RAW, loosen nonstandard-peak policy, or mutate source alignment artifacts.

---

### `standard_peak_backfill_machine_pipeline.py`

**Purpose**: Run the standard-peak machine-gate chain from either an existing overlay summary or a retained-backfill review queue to activated matrix and optional gallery.
**Topic group**: `family_ms1_overlay_batch.py`, `family_ms1_alignment_experiment_batch.py`, `backfill_evidence_reconciliation_gallery.py`, `shift_aware_backfill_calibration_pack.py`, `shift_aware_standard_peak_gate_calibration.py`, `standard_peak_ms1_authority_bundle.py`, `shadow_production_projection.py`, `standard_peak_backfill_productization.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: This orchestrator can start from an existing `family_ms1_overlay_batch_summary.tsv` or from `alignment_retained_backfill_overlay_review_queue.tsv` plus RAW/DLL paths. The two source modes are mutually exclusive so stale overlay evidence is not silently reused when RAW queue inputs are supplied. In queue mode it runs `family_ms1_overlay_batch.py` with resumable PNG-only defaults unless `--evidence-only` is supplied; evidence-only writes compact trace TSV/JSON plus summary rows with blank PNG/PDF paths and is the default path used by the `dna_dr` matrix-only publication mode. Omitting `--limit` processes all remaining queue rows from `--start-rank` instead of inheriting the overlay-batch default top-N chunk size. It builds a fresh reconciliation group index when `--reconciliation-groups-tsv` is omitted, runs shift-aware source-family best-shift evidence, builds the calibration pack with the product default `min_shape_r=0.95`, evaluates the standard-peak machine gate, creates the machine-gate MS1 authority bundle, regenerates shadow projection grounded in `alignment_matrix.tsv` + `alignment_matrix_identity.tsv`, applies matrix-only product activation, and optionally writes the activation-synced gallery. In `publication_mode=matrix-only`, shift-aware evidence writes the machine summary TSVs needed by downstream gates without rendering PNG review images; review image rendering remains enabled for `review-gallery` and `deep-audit`. It writes `standard_peak_backfill_machine_pipeline_summary.json` plus subdirectories for each stage. The final summary records `publication_mode`, evidence source mode, rendered image count, queue row count, start rank, requested/effective limit, whether shift-aware images were rendered, overlay/shift status counts, activation counts, and `status_reasons`; `status=pass` is reserved for the requested scope with successful RAW-backed evidence, shift-aware evidence, and productization. Full 85RAW use remains intentionally chunkable with `--start-rank`, `--limit`, and `--reuse-existing` so expensive RAW-backed evidence generation can resume without hand-splicing downstream inputs.
It can pass an opt-in `--evidence-cache-dir` to the overlay batch producer for
`evidence-only` runs; this reuses exact cached trace TSV/JSON evidence and does
not authorize stale overlay summaries or bypass matrix/publication checks.

---

### `standard_peak_backfill_chunk_consolidation.py`

**Purpose**: Consolidate chunked standard-peak machine-pipeline outputs into one formal matrix-only activation pass.
**Topic group**: `standard_peak_backfill_machine_pipeline.py` + `standard_peak_backfill_productization.py` + `xic_extractor/diagnostics/standard_peak_backfill_chunk_consolidation.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: This consolidation path is part of standard-peak
publication when chunked RAW-backed evidence is used. It is not Bucket B and
must not be removed during spent-gate cleanup.
**Status note**: Consumes repeated chunk `standard_peak_backfill_machine_pipeline_summary.json` files or chunk directories, validates that all chunks passed, optionally verifies complete non-overlapping rank coverage against the full retained-backfill review queue by reading actual overlay summary `rank` values, deduplicates full-matrix chunk `shadow_projection_cells.tsv` rows into `consolidated_shadow_projection_cells.tsv`, preferring product-affecting accepted rows over context rows and failing closed on conflicting accepted values, then calls `standard_peak_backfill_productization.py` once to write the final matrix-only activated matrix. This is the product bridge for expensive 85RAW chunked overlay runs: individual chunk matrices are review artifacts only; the consolidated run is the formal matrix candidate. With `--emit-formal-product-output`, only a passing consolidated run with a supplied full review queue and complete non-duplicated rank coverage publishes a downstream-ready product output directory containing `alignment_matrix.tsv`, `alignment_matrix_identity.tsv`, `activation_hypothesis_identity.tsv`, `activation_value_delta.tsv`, `activation_application_summary.tsv`, and `standard_peak_formal_product_manifest.json`; the manifest records the activation output mode, decision scope, standard-peak gate basis, source shadow projection hash, artifact hashes, and queue coverage. By default that passing formal product is also promoted back to the source alignment output so the source `alignment_matrix.tsv` and `alignment_matrix_identity.tsv` become the standard-peak-backfilled final matrix; the original source files are preserved as `*.pre_standard_peak_backfill.tsv`, and `standard_peak_default_matrix_manifest.json` plus `standard_peak_*` audit sidecars are written beside the final matrix. Use `--no-publish-to-source-alignment-output` only for sidecar-only validation. After publication, reruns should use the `*.pre_standard_peak_backfill.tsv` files as activation input and supply `--publish-alignment-matrix-tsv` / `--publish-alignment-matrix-identity-tsv` pointing back to the default final matrix paths, so written-count semantics stay grounded in the pre-standard matrix. The CLI refuses to use the source alignment directory as the formal sidecar directory, requires publish target matrix/identity paths as a pair, and incomplete or duplicated coverage leaves only a failing consolidation summary while clearing known stale formal-product files from the target formal output directory. With `--write-gallery`, it also passes all chunk overlay and shift-aware gate TSVs to the activation-synced reconciliation gallery so evidence display and matrix writes use the same product-authorized rows.

---

#### `standard_peak_backfill_preset.py` (preset bridge, non-CLI entry)

**Purpose**: Run the standard-peak publication preset after a base alignment
finishes, using retained-gate review rows, chunked machine evidence, and formal
chunk consolidation to publish accepted standard-peak values.
**Topic group**: `standard_peak_backfill_machine_pipeline.py` +
`standard_peak_backfill_chunk_consolidation.py` +
`standard_peak_backfill_productization.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: `scripts/run_alignment.py` calls this entry point when
the active preset runtime options enable `standard_peak_backfill`; the built-in
`dna_dr` preset defaults to `standard_peak_backfill_publication_mode =
"matrix-only"`. This is the default preset publication bridge, not an optional
research gallery.
**Status note**: Runs the retained backfill evidence gate, reads the generated
standard-peak review queue, dispatches chunked machine-pipeline runs, and then
calls chunk consolidation to publish the final matrix-only standard-peak output.
It writes `standard_peak_backfill_preset_summary.json` and records whether the
run skipped because no review rows were present or published a consolidated
matrix. The preset can also pass an explicit overlay `evidence_cache_dir` to
the global matrix-only overlay evidence step; cached evidence remains a
performance reuse layer and does not change the retained-gate queue, chunk
rank/order, matrix authority, or publication checker requirement. It does not
replace base alignment; it executes only after the base alignment artifacts
exist.

---

### `dna_dr_product_ready_stage_replay.py`

**Purpose**: Replay a single expensive `dna_dr_product_ready` post-alignment
stage from an existing alignment output directory for timing/parity validation.
**Topic group**: `standard_peak_backfill_preset.py` +
`scripts.check_product_ready_preset_publication`
**Status note**: Currently supports `--stage standard_peak`. It copies the
required source alignment artifacts into a fresh output directory, records input
artifact SHA/size/row-counts in
`dna_dr_product_ready_stage_replay_manifest.json`, reruns the standard-peak
publication preset, and then runs the product-ready publication checker. The
runner is diagnostic/incremental validation infrastructure: it is not a second
preset runner, does not alter default CLI behavior, and any public artifact hash
diff must be treated as a failed exact-safe replay unless an expected-diff plan
approved it.

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

### `backfill_peakhypothesis_promotion.py`

**Purpose**: Convert reviewed PeakHypothesis/sample-cell backfill projection rows
into an allowlisted product-candidate promotion sidecar while keeping
nonstandard but assessable peaks review-only.
**Topic group**: `backfill_peakhypothesis_promotion.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_promotion.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_promotion_cells.tsv`,
`backfill_peakhypothesis_area_uncertainty.tsv`, and
`backfill_peakhypothesis_promotion_summary.json`. It consumes
`shadow_production_projection_cells.tsv` and a reviewed allowlist only. It can
promote either formal product-authorized `accept` rows or
`identity_supported_review` context rows when an exact PeakHypothesis/sample
allowlist marks the area as `standard_assessable_area`. It does not read RAW,
generate overlays, mutate alignment artifacts, change workbook schemas, or write
final matrices. `nonstandard_assessable_area` rows can be review evidence only
and remain blocked until a separate integration policy exists.
8RAW activation and 85RAW validation are still required before production-ready
claims.

---

### `backfill_peakhypothesis_activation_bridge.py`

**Purpose**: Convert reviewed `backfill_peakhypothesis_promotion_cells.tsv`
rows into the existing shared-peak activation decision/acceptance TSV contract
without creating a parallel matrix writer.
**Topic group**: `backfill_peakhypothesis_activation_bridge.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_activation_bridge.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `activation_decisions.tsv`,
`activation_acceptance.tsv`, `activation_matrix_preflight.tsv`, and
`backfill_peakhypothesis_activation_bridge_summary.json`. By default acceptance
is `fail` with `next_action=run_activation_matrix_diff_smoke`, so the sidecar
cannot be mistaken for production approval. Optional
`--normal-peak-decisions-tsv` makes explicit `require_backfill` normal-peak
decisions a fail-closed activation prerequisite. Optional
`--alignment-matrix-tsv` plus `--alignment-matrix-identity-tsv` performs a
public-matrix preflight: promoted PeakHypothesis/sample cells already present in
the public matrix are suppressed from activation decisions. If the promotion
snapshot says `current_matrix_written=FALSE` but the public matrix has a value,
the preflight reports
`public_matrix_conflicts_with_projection_current_snapshot` and points to
rebuilding the matrix with the current writer before activation. This tool does
not read RAW, mutate alignment artifacts, change workbook schemas, or write
final matrices; matrix application remains owned by
`apply_shared_peak_identity_activation.py`.

---

### `backfill_peakhypothesis_activation_acceptance.py`

**Purpose**: Validate the post-activation matrix diff for a reviewed
PeakHypothesis backfill promotion slice, proving that activation changed exactly
the promoted PeakHypothesis/sample cells and no unrelated public matrix cells.
**Topic group**: `backfill_peakhypothesis_activation_acceptance.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_activation_acceptance.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_activation_acceptance.tsv`,
`backfill_peakhypothesis_activation_matrix_diff.tsv`, and
`backfill_peakhypothesis_activation_acceptance_summary.json`. The gate consumes
promotion cells, activation decisions, bridge matrix preflight, activation
application summary, activation value delta, and before/after
`alignment_matrix.tsv` plus `alignment_matrix_identity.tsv`. A pass requires
all promoted rows to have matching `auto_activate` decisions, `needs_activation`
preflight, `written` value deltas, coherent application summary, and a full
matrix diff containing only the promoted cells with values matching the
promotion sidecar. This is an 8RAW/current-writer diagnostic acceptance surface;
it does not read RAW, mutate artifacts, change workbook schemas, or replace
85RAW production validation.

---

### `backfill_peakhypothesis_raw85_slice_gate.py`

**Purpose**: Gate reviewed PeakHypothesis backfill promotion cells against an
existing 85RAW alignment artifact refresh before any direct production-transfer
trial.
**Topic group**: `backfill_peakhypothesis_raw85_slice_gate.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_raw85_slice_gate.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_raw85_slice_gate.tsv` and
`backfill_peakhypothesis_raw85_slice_gate_summary.json`. It consumes only
`backfill_peakhypothesis_promotion_cells.tsv`, 85RAW `alignment_review.tsv`,
and 85RAW `alignment_cells.tsv`. Cross-run `feature_family_id` values are not
treated as stable identity; when the promotion row carries a seed m/z/RT anchor,
the 85RAW candidate is selected by hypothesis-anchor m/z/RT plus sample. A
direct-transfer candidate requires that anchored PeakHypothesis/sample cell to
exist, remain a primary matrix row, avoid unresolved family consolidation, have
`detected` or `rescued` status, and carry a positive
`gaussian15_positive_asls_residual` primary matrix area. Anchored detected or
rescued cells blocked only by family consolidation/non-primary-row ownership are
reported as `hypothesis_candidate_review`, not as absent. Hard missing cells,
absent cells, duplicate-assigned cells, and missing Gaussian15 area still fail
closed. This diagnostic does not read RAW, remap winners, apply activation,
mutate alignment artifacts, change workbook schemas, or claim production
readiness. This diagnostic's `rescued` eligibility is not a heldout-oracle
contract: standard-path heldout oracle manifests separately require
`heldout_original_cell_status` proving the masked source cell was originally
detected, and `rescued` rows fail closed for product-readiness oracle evidence.

---

### `backfill_peakhypothesis_raw85_winner_remap.py`

**Purpose**: Build diagnostic 85RAW primary-winner remap proposals for reviewed
PeakHypothesis backfill cells that became consolidation losers in the direct
85RAW slice gate.
**Topic group**: `backfill_peakhypothesis_raw85_winner_remap.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_raw85_winner_remap.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Legacy family-consolidation context only. It writes
`backfill_peakhypothesis_raw85_winner_remap.tsv` and
`backfill_peakhypothesis_raw85_winner_remap_summary.json` from a slice-gate TSV
plus 85RAW `alignment_review.tsv` and `alignment_cells.tsv`. It must not be used
as hypothesis identity authority, because family winners can collapse multiple
peaks inside a broad family/window. The 2026-06-09 top14 winner-remap artifact
generated before the hypothesis-anchor correction is obsolete. Use the
hypothesis-anchor slice gate as the current review surface; winner-remap output,
if regenerated later, is only family-consolidation context and not product
activation. It does not read RAW, change row identity, apply activation, mutate
matrices, remap public outputs, or claim production readiness.

---

### `backfill_peakhypothesis_raw85_hypothesis_review.py`

**Purpose**: Package corrected 85RAW hypothesis-anchor slice-gate candidates
into a compact manual review queue before any product-transfer decision.
**Topic group**: `backfill_peakhypothesis_raw85_hypothesis_review.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_raw85_hypothesis_review.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes
`backfill_peakhypothesis_raw85_hypothesis_review_queue.tsv` and
`backfill_peakhypothesis_raw85_hypothesis_review_summary.json` from the corrected
85RAW slice-gate TSV only. It keeps source PeakHypothesis identity, m/z/RT
anchor, matched 85RAW PeakHypothesis, same-sample area/status, and family
consolidation context in one review row. It intentionally leaves
`reviewer_verdict` and `reviewer_note` blank and records
`proposed_product_transfer_status=review_only_pending_same_peak_and_consolidation_policy`.
It does not read RAW, judge peak shape, choose S/N, apply activation, mutate
matrices, remap family winners, or claim production readiness.

---

### `backfill_peakhypothesis_raw85_overlay.py`

**Purpose**: Render RAW XIC overlay plots for corrected 85RAW hypothesis review
candidates so reviewers do not need to inspect each row manually in Xcalibur.
**Topic group**: `backfill_peakhypothesis_raw85_overlay.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_raw85_overlay.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_raw85_overlay_index.tsv`,
`backfill_peakhypothesis_raw85_overlay_summary.json`, an HTML gallery, and
per-candidate PNG/PDF plots. Each plot overlays raw XIC plus
`gaussian15_asls_residual` smoothed XIC for the candidate m/z and current
consolidation winner m/z in the same RAW sample, with candidate anchor RT,
candidate peak window, and winner RT marked. The overlay index also records
machine Gaussian15 normal-shape evidence from the selected bounds' local shape
context, using `ChromPeakSegment` baseline-return boundaries plus
`gaussian15_positive_asls_residual` lobe area as audit evidence. It can support
standard vs non-standard peak-shape gating, show the Gaussian area that would
be integrated, and emit a machine same-peak verdict when the slice-gate
PeakHypothesis/sample match, detected/rescued cell state, standard shape, and
positive lobe area all agree. It does not apply activation, mutate matrices,
remap family winners, or claim production readiness.

---

### `backfill_peakhypothesis_normal_peak_decision.py`

**Purpose**: Convert PeakHypothesis promotion rows, corrected 85RAW
hypothesis-anchor slice-gate rows, machine Gaussian15 shape evidence, and
same-peak verdicts into an explicit normal-peak backfill decision surface.
**Topic group**: `backfill_peakhypothesis_normal_peak_decision.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_normal_peak_decision.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_normal_peak_decisions.tsv` and
`backfill_peakhypothesis_normal_peak_decision_summary.json`. The normal-peak
shape definition is
`gaussian15_asls_residual_selected_shape_context_single_complete_unimodal_peak;raw_spikes_neighbor_contact_family_multiplet_not_blockers`:
the selected PeakHypothesis/sample must show a complete Gaussian15 positive
AsLS residual single peak in the selected bounds' local shape context, from
broad/flat through sharp/peaked. This avoids treating a too-narrow integration
bound as the whole peak-shape review window. Raw XIC spikes, neighboring peak
contact, family/window-level multiplets, and
family consolidation/non-primary ownership are not peak-shape hard blockers by
themselves. When `--machine-shape-evidence-tsv` is supplied, the standard vs
non-standard peak-shape decision comes from `machine_shape_decision`; a
`standard_peak_shape_supported` row can resolve a missing/legacy area label to
`standard_assessable_area`, while `nonstandard_peak_shape` stays review-only and
does not activate. Same-peak support is still a separate evidence requirement,
but it can now come from the overlay index's `machine_same_peak_verdict` when
manual review is absent; manual same-peak conflicts still override and block.
For standard machine-shape rows, the decision TSV selects
`normal_peak_quantitation_area` from the overlay's Gaussian15
baseline-return lobe area (`gaussian15_lobe_area`), with boundary start/end and
source fields preserved for audit. Without machine-shape evidence, the legacy
fallback remains the reviewed `raw85_primary_matrix_area`. For product
activation, use the end-to-end normal-peak activation CLI so matrix writing is
gated by post-activation matrix diff acceptance.

---

### `backfill_peakhypothesis_85raw_activation_trial.py`

**Purpose**: Run a no-RAW, artifact-only 85RAW normal-peak activation trial for
reviewed PeakHypothesis/sample cells before launching a new full 85RAW rerun.
**Topic group**: `backfill_peakhypothesis_85raw_activation_trial.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_85raw_activation_trial.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes `backfill_peakhypothesis_85raw_activation_trial.tsv`
and `backfill_peakhypothesis_85raw_activation_trial_summary.json`. It consumes
only current 85RAW `alignment_matrix.tsv`, `alignment_matrix_identity.tsv`,
`alignment_run_metadata.json`, `timing.json`, and normal-peak decision rows.
Manual same-peak verdict rows are optional override/review evidence; when they
are absent, the trial consumes `same_peak_verdict` from the normal-peak
decision TSV. It does not read RAW, load `alignment_cells.tsv`,
apply activation, mutate alignment artifacts, or claim production readiness. It
uses `raw85_matched_peak_hypothesis_id + sample` as the transfer trial key
rather than cross-run source FAM IDs. The 2026-06-09 artifact-only trial found
`normal_peak_required_count=11`, `primary_loser_count=9`,
`primary_winner_count=2`, `expected_matrix_diff_count=11`,
`unexpected_diff_count=0`, and a pass status, so the next action is implementing
the normal-peak override through the activation owner rather than rerunning
85RAW unchanged.

---

### `backfill_peakhypothesis_85raw_activation_transfer.py`

**Purpose**: Transfer reviewed normal-peak 85RAW backfill decisions into
raw85-keyed promotion rows that the existing activation bridge can consume.
**Topic group**: `backfill_peakhypothesis_85raw_activation_transfer.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_85raw_activation_transfer.py`
**Originating spec/goal/plan**:
`retired-provenance:3aee8e2b6031`
**Status note**: Writes
`backfill_peakhypothesis_85raw_transfer_promotion_cells.tsv`,
`backfill_peakhypothesis_85raw_activation_transfer.tsv`, and
`backfill_peakhypothesis_85raw_activation_transfer_summary.json`. It uses
`raw85_matched_peak_hypothesis_id + sample` as the activation key, keeps source
PeakHypothesis/FAM ids as audit-only provenance, and writes
`source_artifact_sha256` as the content bundle hash of
`normal_peak_decisions_tsv + activation_trial_tsv`, with row-level
`source_row_sha256` for each transfer/promotion row. It fails closed on
normal-peak decision blockers, same-peak conflicts, non-positive normal-peak
quantitation area, non-Gaussian15 quantitation area source, or activation-trial
blockers. The projected value is
`normal_peak_quantitation_area` when present, so machine-shape standard peaks
write the Gaussian15 lobe area selected by the decision TSV. It does not apply
activation or write matrices; downstream matrix mutation remains owned by
`apply_shared_peak_identity_activation.py` / `product_activation`.

---

### `backfill_peakhypothesis_normal_peak_activation.py`

**Purpose**: Run the normal-peak backfill product path end to end: evidence
rows become explicit `require_backfill` normal-peak decisions, 85RAW activation
keys, activation sidecars, matrix-only `alignment_matrix.tsv` output, and a
post-activation matrix-diff acceptance audit.
**Topic group**: `backfill_peakhypothesis_normal_peak_activation.py` +
`xic_extractor/diagnostics/backfill_peakhypothesis_normal_peak_activation.py`
**Originating spec/goal/plan**:
`retired-provenance:9851d7cfb739`
**Status note**: This is the normal-peak end-to-end CLI for product behavior.
It accepts either a provided `backfill_peakhypothesis_normal_peak_decisions.tsv`
or the evidence inputs needed to generate it
(`backfill_peakhypothesis_promotion_cells.tsv`,
`backfill_peakhypothesis_raw85_slice_gate.tsv`, and machine Gaussian15
shape/same-peak evidence from the overlay index; manual same-peak verdicts are
optional overrides). It then consumes an existing
`backfill_peakhypothesis_85raw_activation_trial.tsv` or can build one from a
current 85RAW artifact directory, runs the 85RAW activation transfer,
bridges transfer promotions into activation decisions, applies matrix-only
activation through `product_activation`, and runs exact post-activation
matrix-diff acceptance. The bridge's pre-application `activation_acceptance.tsv`
is intentionally allowed to be fail-closed inside this orchestrator because the
real product gate is the final `backfill_peakhypothesis_activation_acceptance.tsv`.
Standard normal-peak rows with blockers fail closed before matrix writing.
For machine-shape standard peaks, the matrix-only activation value comes from
the decision TSV's `normal_peak_quantitation_area`, which is the Gaussian15
baseline-return lobe area selected by the overlay/`ChromPeakSegment` evidence.
`review_only_nonstandard_peak` rows are counted and excluded from activation
rather than blocking this normal-peak goal. Passing output writes `alignment_matrix.tsv`,
`alignment_matrix_identity.tsv`, `activation_value_delta.tsv`,
`activation_hypothesis_identity.tsv`, and an end-to-end summary with source
paths, source hashes, changed-cell count, and acceptance status.

---

### `backfill_peakhypothesis_transfer_readiness.py`

**Purpose**: Summarize transfer readiness for a reviewed PeakHypothesis backfill
promotion slice by joining the promotion summary, 8RAW/current-writer activation
acceptance, 85RAW current-writer artifact contract, optional 85RAW slice gate
summary, optional 85RAW hypothesis manual-review summary, and optional 85RAW
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
diff acceptance passes and the 85RAW artifact refresh uses the canonical
current-writer contract. With `--raw85-slice-gate-summary-json`, a failing
slice gate becomes a hard blocker such as
`85raw_slice_specific_no_regression_failed`; a missing slice gate remains
`raw85_slice_gate_status=not_assessed`. A hypothesis-anchor partial gate is
surfaced as `raw85_slice_gate_hypothesis_candidate_review_count` with
`next_action=review_85raw_hypothesis_candidates_before_product_transfer`. When
`--raw85-hypothesis-review-summary-json` shows every review candidate was
manually accepted as same-peak evidence, the partial gate no longer counts as a
hard peak-shape failure; readiness records
`raw85_peak_shape_review_status=manual_same_peak_supported_all_review_candidates`
and moves the remaining blocker to `raw85_consolidation_policy_not_productized`.
Winner-remap summaries are optional legacy context and no longer override the
hypothesis-anchor next action. It deliberately keeps `production_ready=FALSE`
until an explicit product-transfer decision and consolidation policy exist.

---

### `authorize_backfill_ms1_pattern_evidence.py`

**Purpose**: Convert reviewed, allowlisted RAW-overlay MS1 pattern sidecar rows into a product-authorized MS1 sidecar candidate for `apply_shared_peak_identity_activation.py`.
**Topic group**: `authorize_backfill_ms1_pattern_evidence.py` + `xic_extractor/alignment/backfill_ms1_product_authority.py`
**Originating spec/goal/plan**: `retired-provenance:69bd6d102e9c`
**Status note**: Writes `shared_peak_identity_ms1_pattern_coherence_product_authorized.tsv`, `backfill_ms1_pattern_product_authority_audit.tsv`, and `backfill_ms1_pattern_product_authority_summary.json`. Authorization is fail-closed: the allowlist row must be `backfill_ms1_pattern_product_authority_v1` with `authority_status=product_authorized`, reviewed `expected_overlay_trace_data_json`, and reviewed `expected_overlay_trace_data_sha256`; the source MS1 row must be supportive/partial `trace_constellation` RAW-overlay evidence with the same-peak own-max anchor reason, `shape_metric_source=family_ms1_overlay_anchor_peak_own_max`, and an own-max similarity above the allowlist threshold. Empty allowlist thresholds use the default `0.5` floor; explicit thresholds may tighten but cannot lower that floor. Duplicate source/product sidecar keys are ambiguous and fail closed instead of using row order. The recorded `family_ms1_overlay_trace_data_json` must resolve relative to the source TSV without absolute paths or bundle escape, declare the same top-level `family_id`, contain one matching sample trace with usable RAW RT/intensity vectors and own-max similarity, match the reviewed allowlist path/hash, and the authorized row records `product_authority_overlay_trace_data_sha256`. Output rows set `diagnostic_only=FALSE` plus explicit `product_authority_*` fields. Projection copies authority provenance into rescued-cell `backfill_ms1_product_authority_*` columns, and promotion policy does not treat naked `backfill_ms1_*` support fields as product support. This tool does not generate overlays, does not mutate source alignment artifacts, and by itself is only a product-authority sidecar candidate; 8RAW/85RAW activation validation is still required before product readiness.

---

### `authorize_backfill_candidate_ms2_pattern_evidence.py`

**Purpose**: Convert reviewed, allowlisted Candidate MS2/NL pattern sidecar rows into a product-authorized Candidate MS2 sidecar candidate for `apply_shared_peak_identity_activation.py`.
**Topic group**: `authorize_backfill_candidate_ms2_pattern_evidence.py` + `xic_extractor/alignment/backfill_candidate_ms2_product_authority.py`
**Originating spec/goal/plan**: `retired-provenance:69bd6d102e9c`
**Status note**: Writes `shared_peak_identity_candidate_ms2_pattern_product_authorized.tsv`, `backfill_candidate_ms2_product_authority_audit.tsv`, and `backfill_candidate_ms2_product_authority_summary.json`. Authorization is fail-closed: the allowlist row must be `backfill_candidate_ms2_pattern_product_authority_v1` with `authority_status=product_authorized`, non-empty authority source, expected status/level/alignment-source fields, and a reviewed canonical SHA256 of the source Candidate MS2 row. The source row must carry the canonical `shared_peak_identity_candidate_ms2_pattern_v2` producer schema and full producer columns. Only supportive or partial Candidate MS2 rows at `sample_candidate_aligned` or `sample_boundary_aligned` can become product-authorized; direct candidate rows require matched-tag/NL provenance and RAW-boundary rows require positive trigger/strict-NL/product-trace evidence. `not_observed`, conflicts, missing rows, stale source-row hashes, status/level/alignment drift, malformed provenance, and similarity below the default `0.5` floor remain audit rejects. Output rows set `diagnostic_only=FALSE` plus explicit `product_authority_*` fields so backfill projection can consume them. Projection copies authority provenance into rescued-cell `backfill_candidate_ms2_product_authority_*` columns, and promotion policy does not treat naked `backfill_candidate_ms2_*` support fields as product support. This tool does not generate Candidate MS2 evidence, does not read RAW, does not mutate source alignment artifacts, and by itself is only a product-authority sidecar candidate; 8RAW/85RAW activation validation is still required before product readiness.

---

### `backfill_evidence_reconciliation_gallery.py`

**Purpose**: Build a `diagnostic_only` / `shadow_review` backfill family/seed-group reconciliation index and HTML gallery from existing alignment, seed-audit, seed-aware, overlay, and candidate-gate artifacts.
**Topic group**: `backfill_evidence_reconciliation_gallery.py` + `xic_extractor/diagnostics/backfill_reconciliation_gallery.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `backfill_evidence_reconciliation_groups.tsv`, `backfill_evidence_reconciliation_representative_cells.tsv`, `backfill_evidence_reconciliation_summary.json`, and `backfill_evidence_reconciliation_gallery.html`. The gallery is a hypothesis-first sticky table: thin family header rows provide MS1 pattern/drift/multimodal context only, while hypothesis rows are directly visible decision-review rows with representative cells collapsed in row details. Seed requests are provenance, not the primary visual decision unit. When alignment review marks a row as `primary_family_consolidated`, close seed requests are rendered as one MS1 product hypothesis with seed aliases in the evidence drawer instead of separate main-table decisions. Build-time preparation pre-indexes seed-group cells and exact vs legacy overlay rows once per run, and HTML subset/lookup preparation is isolated in `_GalleryRenderContext` before rendering. The default Focus is `Product rows`; `Projection accepts` isolates projected new writes from optional shadow production projection input; `family_consolidation_loser`, `duplicate_only`, and duplicate-loser audit rows are routed to `Duplicate / audit debug` instead of competing with product candidates in the first view. Family headers summarize detected required-tag anchors and their nearest seed group (`anchors D=... · seed N D=...`). Hypothesis rows show `impact`: without projection input, `NL` is the family detected anchor count, `Fill` is hypothesis rescued/backfilled cells, and `Dup` / `Review` appear only when duplicate-assigned or provisional context is non-zero; this is alignment cell provenance, not target benchmark coverage. With optional `shadow_production_projection_cells.tsv`, the impact column switches to current production-decision writes / review target / projected accept / projected block counts, the detail drawer shows a cell-level current-decision vs projected-decision table, and consolidated drawers include a `Projection accept cells` mini-index with sample, exact seed request, reason/warning, MS1 product rule / optional context chain, and overlay link. Overlay links distinguish `family context` from `hypothesis PNG`; if multiple seed aliases share one family-level PNG, the gallery labels it as shared context instead of presenting fake per-seed PNGs. Each generated gallery also copies and links the maintained visual reader guide `docs/superpowers/validation/evidence_overlay_interpretation_guide.html`, so Backfill and Discovery overlay semantics are visible in the served artifact directory without creating a separate generated HTML system. For very large reports, the HTML DOM caps low-information `evidence_inconclusive` rows while preserving all action/overlay rows and writes a visible scope notice; the groups/representatives TSV plus summary JSON remain exhaustive. Optional `backfill_shadow_policy_cells.tsv` input is rendered as HTML-only MS1+RT shadow provenance (`fill_now` / `would_fill_under_ms1_rt_policy` / `blocked` / policy gap counts) without changing the reconciliation group TSV schema. Optional `targeted_istd_benchmark_summary.tsv` input is rendered as validation-only target match context in HTML and summary counts; it does not become product identity authority and does not change the group TSV schema. The TSV remains a deterministic family/seed-group machine index. `review_required_*` overlay verdicts are displayed as human visual judgment needs, not hard evidence blockers. It consumes existing artifacts only, does not accept RAW/DLL paths, does not generate overlays, does not invent `backfill_score`, and does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbook schemas, or product decisions. Product promotion remains outside this renderer and requires a separate reviewed allowlist contract plus 8RAW/85RAW validation.
**Activation sync note**: Optional `--activation-application-summary-tsv` and `--activation-value-delta-tsv` let the gallery display an already-applied activated matrix view. Current `accepted_rescue` projection cells and row-level activation delta `written` cells can update only the gallery's product-state display/provenance; the renderer still does not write or recompute matrix values.

---

### `cid_nl_default_activation_gallery_review.py`

**Purpose**: Build a Gallery-compatible CID-NL feature-inclusion / identity-authority review packet from the existing successor decision packet and optional family MS1 overlay batch summary.
**Topic group**: `cid_nl_default_activation_gallery_review.py` plus the existing `backfill_evidence_reconciliation_gallery.py` renderer and `family_ms1_overlay_batch.py` overlay producer.
**Originating spec/goal/plan**: `docs/superpowers/validation/cid_nl_default_activation_successor_authority_contract_v1/README.md`; `docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`.
**Status note**: Consumes the ignored successor decision packet under `output/validation/cid_nl_default_activation_successor_authority_contract_v1/`, the versioned preflight summary, and existing CID-NL alignment cell evidence. It writes the standard reconciliation gallery outputs under `output/validation/cid_nl_default_activation_gallery_review_v1/`, plus `cid_nl_default_activation_overlay_review_queue.tsv` for the existing RAW-backed overlay batch producer and `cid_nl_discovery_identity_differential_review.tsv` as a no-RAW source/successor transition index. With `--overlay-batch-summary-tsv`, it links successful overlay PNG/trace rows back into the existing gallery lightbox instead of creating a CID-NL-specific standalone HTML viewer, but only when the overlay summary row matches the current review queue by family id, seed group, m/z, RT window, family-center RT, and output prefix. Without linked overlay provenance, the packet can still build the no-RAW queue but reports `overall_status=needs_overlay_batch`, so `--require-pass` fails. The adapter records successor decision counts, overlay-linked groups, cellless overlay skips, differential transition counts, explicit false flags for `product_writer_changed`, `default_quant_matrix_changed`, and `candidate_rows_are_matrix_rows`, plus additive gate fields: `feature_inclusion_gate`, `identity_authority_gate`, and `source_successor_relationship`. Its payload and gallery summary link the maintained HTML reader guide `docs/superpowers/validation/evidence_overlay_interpretation_guide.html`; the guide is copied beside generated HTML outputs for localhost/in-app Browser review. It presents CID-NL as an Evidence Review Gallery mode: the interaction shell is reused, but Backfill domain semantics and the shared representative-cells TSV schema are not changed. It does not accept RAW/DLL paths directly, does not generate PNGs itself, does not mutate ProductWriter/default matrix/workbook/GUI outputs, and does not make CID-NL/MS2 evidence direct writer authority. Product adoption remains a separate expected-diff/public-surface gate.

---

### `cid_nl_differential_overlay_review.py`

**Purpose**: Generate paired source vs successor RAW-backed MS1 overlay review artifacts for CID-NL feature-inclusion and identity-relationship review.
**Topic group**: `cid_nl_default_activation_gallery_review.py` plus the existing `backfill_evidence_reconciliation_gallery.py` renderer and RAW `open_raw(...).extract_xic_many(...)` batch extraction path.
**Originating spec/goal/plan**: `docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`.
**Status note**: Consumes `cid_nl_discovery_identity_differential_review.tsv` and `successor_authority_decisions.tsv`, filters the 78 `ready_for_paired_overlay` source/successor transitions, and extracts source plus successor XIC traces batched by RAW sample. It writes a paired review queue, 78 Gaussian15-smoothed PNG overlays, 78 raw-trace JSON files with Gaussian15 max-intensity fields, `cid_nl_differential_overlay_review_summary.tsv/json`, and a reused Evidence Review Gallery HTML under ignored `output/validation/cid_nl_default_activation_gallery_review_v1/differential_overlays/`. The collapsed Gallery row includes the source-to-successor transition, lightbox links are labeled as `HYPOTHESIS DIFFERENTIAL OVERLAY`, and detail cards expose explicit `source -> successor` PeakHypothesis identity. Plot wording frames the lower-left panel as same-sample feature/identity relationship context: successor MS1 support can justify feature inclusion, while replacement/merge/dedupe is a separate identity-authority gate. A source peak does not invalidate successor feature inclusion, and source/successor m/z values may differ. The generated output also links a local copy of `evidence_overlay_interpretation_guide.html`. This tool is `diagnostic_only`: it does not mutate ProductWriter/default matrix/workbook/GUI outputs, does not create a second Discovery system, does not treat candidates as matrix rows, and does not make CID-NL/MS2 evidence direct writer authority. Product adoption remains a separate expected-diff/public-surface gate.

---

### `gallery_browser_smoke.py`

**Purpose**: Run a deterministic headless Chromium smoke test against an already-rendered gallery HTML, independent of Codex MCP tabs or the Chrome extension.
**Topic group**: `gallery_browser_smoke.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Opens a local HTML file through Playwright/Chromium and checks desktop, mobile, and 200 percent zoom viewports; sticky review table chrome; Focus/search behavior including `Projection accepts`; detail drawer open/close; PNG anchor fallback plus lightbox focus/Esc close; and coarse table-cell overlap. It writes screenshots plus `gallery_browser_smoke_summary.json` under the requested output directory. It does not parse TSVs, generate reports, mutate artifacts, depend on MCP/Chrome-extension state, or modify product behavior. Playwright is a repo dev dependency; use `uv sync --extra dev --group dev` before running it. The runner defaults to bundled Playwright Chromium only and fails fast when that browser is missing; pass `--browser-channel auto`, `chrome`, or `msedge` only when system-browser fallback is explicitly intended.

---

### `tier2_raw_trace_reread_producer.py`

**Purpose**: Produce paired `diagnostic_only` Tier 2 RAW trace evidence and RAW manifest sidecars for retained provisional backfill candidates.
**Topic group**: `tier2_raw_trace_reread_producer.py` + `xic_extractor/alignment/tier2_trace_producer.py` + `xic_extractor/alignment/production_candidate_gate.py`
**Originating spec/plan**: `retired-provenance:443a0af82ac4`; follows the sidecar provenance gate checkpoint in `retired-provenance:d3d707f162f6`; v0.1 diagnostic criteria review in `retired-provenance:38373345f4da` and `retired-provenance:e509e2788bf2`.
**Status note**: Writes diagnostic-only v0.1 Tier 2 RAW trace evidence and RAW manifest sidecars. The v0.1 criteria expose scan availability, signal/noise, shape, boundary-reference, apex-span, and neighbor-interference context, but do not provide positive Tier 2 support or change `alignment_matrix.tsv`. 85RAW is out of scope until a reviewed follow-up plan is approved after a successful v0.1 8RAW diagnostic rerun.

---

## Peak / Candidate Audits

### `cwt_peak_candidate_audit.py`

**Purpose**: Audit CWT peak candidate agreement from peak_candidates.tsv.
**Topic group**: `cwt_peak_candidate_audit.py` + `_io`, `_models`, `_analysis`, `_writers` (5 files)
**Originating spec**: `retired-provenance:22f973e5ed27` + `retired-provenance:0091435e5f7e`

---

### `peak_candidate_score_calibration_report.py`

**Purpose**: Audit peak candidate scoring against newer evidence labels without changing production selection.
**Topic group**: `peak_candidate_score_calibration_report.py` + `_io`, `_models`, `_analysis`, `_writers` (5 files)
**Originating spec**: `retired-provenance:22f973e5ed27`

---

### `selected_envelope_review_queue.py`

**Purpose**: Build `diagnostic_only` selected-envelope changed-row and boundary-oracle review queue artifacts from `selected_envelope_diagnostics.tsv`.
**Topic group**: `selected_envelope_review_queue.py` + `xic_extractor/peak_detection/selected_envelope_*`
**Originating owner/plan**: `docs/product/quantitation-context.md`; retired selected full-envelope boundary spec provenance `retired-provenance:13e9499ab37d`
**Status note**: Writes `selected_envelope_changed_rows.tsv`, `selected_envelope_oracle_review_queue.tsv`, `selected_envelope_diagnostic_manifest.tsv`, and `selected_envelope_review_queue.json` under an explicit output directory. This tool only packages the audit sidecar for manual/expert boundary review; it does not run RAW files, mutate selected `IntegrationResult`, change targeted workbook/CSV `Area`, or authorize FE4/8RAW by itself.

---

### `selected_envelope_plot_review.py`

**Purpose**: Render `diagnostic_only` selected-envelope boundary review plots from selected-envelope diagnostic rows, showing RAW XIC, AsLS baseline, Gaussian15 morphology overlay, resolver interval, selected envelope, optional manual/expert oracle overlay, and quantitation context in one figure.
**Topic group**: `selected_envelope_plot_review.py` + `xic_extractor/diagnostics/selected_envelope_gallery.py` + `xic_extractor/peak_detection/selected_envelope_*`
**Originating owner/plan**: `docs/product/quantitation-context.md`; retired selected full-envelope boundary spec provenance `retired-provenance:13e9499ab37d`
**Status note**: Re-reads RAW files for bounded manual/expert review only. It can consume an optional `selected_envelope_boundary_oracle.tsv` / boundary-oracle TSV to draw expert-reviewed RT windows and record selected candidate id plus oracle id/source/status in `selected_envelope_plot_index.tsv`. It can also consume `chrom_peak_segment_review_rows.tsv` from `chrom_peak_segment_candidate_gate.py` to force explicit review-only segment rows into the plot index. Diagnostic rows may carry Gaussian15-smoothed positive AsLS residual, selected segment, and selected Gaussian peak-group evidence from upstream package logic; this plotter only renders those recorded evidence fields and is not an exact clone of Xcalibur's proprietary smoothing. Boundary promotion, final area ownership, and production selection remain outside this renderer. Plot overlays fail closed unless oracle rows are `expert_reviewed` with manual/expert sources (`manual_overlay`, `expert_overlay`, or `manual_2raw`); targeted workbook control rows remain benchmark-only and are not drawn as boundary truth. It writes PNG/PDF overlays and `selected_envelope_plot_index.tsv`; `review_gallery.html` is a sticky-table, details, and PNG-lightbox human review surface that remains `diagnostic_only`. It does not mutate selected `IntegrationResult`, change targeted workbook/CSV `Area`, or promote selected-envelope behavior by itself.

---

### `chrom_peak_segment_candidate_gate.py`

**Purpose**: Summarize scored `chrom_peak_segment` candidate adoption from
`peak_candidates.tsv`, compare selected-row area deltas against a baseline
candidate TSV, and emit a segment-native gate manifest plus changed-row TSV.
**Topic group**: `chrom_peak_segment_candidate_gate.py` (single-file)
**Originating spec/plan**:
`docs/product/quantitation-context.md`;
retired selected full-envelope boundary spec provenance `retired-provenance:129cc4823496`
**Status note**: Writes
`chrom_peak_segment_gate_manifest.json` and
`chrom_peak_segment_changed_rows.tsv`, plus
`chrom_peak_segment_review_rows.tsv`. This is a product-candidate diagnostic
gate for segment enumeration, not a selected-envelope promotion gate. It reports
boundary and presence sub-gates separately so area/boundary regressions do not
get mixed with analyte false-pick or review-only detection policy risk. Manual
presence review verdicts such as `expected_peak_change`, `blocked`,
`false_pick`, `inconclusive`, and `needs_followup` keep the presence gate in
`defer` until the product-selection or review policy is resolved. Optional
`--manual-selected-envelope-review-tsv` rows are matched by
`selected_candidate_id`; `extend_right_boundary_before_promotion` keeps the
boundary gate in `defer`, while
`select_alternate_or_keep_not_counted_until_rerun` keeps the presence gate in
`defer`. Typed `decision: not_counted` review-only rows with explicit
not-counted policy evidence are diagnostic non-presence rows and do not require a
manual presence verdict. These rows are review comments / promotion blockers,
not formal boundary+area oracle rows.

---

### `subthreshold_sensitivity_report.py`

**Purpose**: Aggregate Gaussian15 sub-threshold (missed-peak) candidates across a batch of overlay trace-data JSONs as evidence for whether/how far to relax `ms1_peak_modes.gaussian15_peak_observations` multi-peak thresholds.
**Topic group**: reuses `subthreshold_candidate_report` (mirrors the detector's local-maximum scan).
**Status note**: Diagnostic-only; changes NO detection logic. Reports accepted-vs-rejected local maxima, which gate (height/prominence/edge/overlapping) blocked rejects (appears-in-reasons vs SOLE blocker), and a height-recovery upper bound. Use on a real (ideally spike-in) batch, then validate any threshold change with spike-in recovery.

---

### `ms1_peak_group_nl_scope_gate.py`

**Purpose**: Gate selected `chrom_peak_segment` candidate MS2/NL evidence so
strict neutral-loss support must belong to the same Gaussian15 MS1 peak group,
not merely the same target or candidate window.
**Topic group**: `ms1_peak_group_nl_scope_gate.py` (single-file)
**Originating note**: 2026-06-05 Gaussian15 MS1 peak-group MS2/NL ownership
production-readiness follow-up.
**Status note**: Consumes `peak_candidates.tsv` rows with
`ms1_peak_group_*` and `outside_ms1_peak_group_*` diagnostics, writes
`ms1_peak_group_nl_scope_gate_manifest.json` plus
`ms1_peak_group_nl_scope_review_rows.tsv` and the non-blocking
`ms1_peak_group_nl_scope_context_rows.tsv`. It fails closed when a selected
chrom peak segment lacks Gaussian15 MS1 peak-group scope, uses an unexpected
group source, has a selected apex outside the group bounds, or appears to borrow
active strict NL support from outside that selected group. Repeated DDA/NL scans
inside the same selected group are counted as multiple scans but one
chromatographic support event; outside strict-NL observations with valid
in-group support are listed for review context only. This gate does not mutate
workbook, matrix, or candidate-selection outputs.

---

## Targeted Benchmarks & Reviews

### `targeted_istd_benchmark.py`

**Purpose**: Run strict targeted ISTD benchmark for untargeted alignment.
**Topic group**: `targeted_istd_benchmark.py` + `_loaders`, `_matching`, `_models`, `_stats`, `_summary`, `_writers` (7 files; largest group)
**Originating spec**: `retired-provenance:53a763b9ec7a`
**Recent doc**: `retired-provenance:5162156a9766`
**Status note**: Reads targeted workbook inputs plus existing alignment review/cell/public-matrix artifacts and writes benchmark summary, match, JSON, and Markdown outputs. Public `alignment_matrix.tsv` loading supports both legacy `feature_family_id` matrices and clean `Mz` / `RT` / sample-column matrices with `alignment_matrix_identity.tsv` provenance; matrix sample-column normalization is computed once per matrix read and reused across row expansion. This benchmark remains validation-only context and does not mutate alignment artifacts, workbook schemas, matrix identity, or production decisions.

---

### `targeted_evidence_review_report.py`

**Purpose**: Render a human-first HTML report from targeted diagnostics.
**Topic group**: `targeted_evidence_review_report.py` + `_components`, `_model`, `_rendering`, `_styles` (5 files)
**Originating spec**: `retired-provenance:100307efc8fa` Workstream G (rendering split twin with `alignment_decision_report`)

---

### `targeted_peak_reliability_audit.py`

**Purpose**: Audit targeted peak reliability for benchmark eligibility.
**Topic group**: `targeted_peak_reliability_audit.py` + `_classifier`, `_loaders`, `_models`, `_writers` (5 files)
**Originating spec**: `retired-provenance:53a763b9ec7a`

---

### `targeted_nl_dropout_root_cause_audit.py`

**Purpose**: Classify targeted review-positive NL dropout root causes.
**Topic group**: `targeted_nl_dropout_root_cause_audit.py` + `_io`, `_models`, `_logic`, `_writers` (5 files)
**Originating spec**: `retired-provenance:8adf83f1a353`

---

### `single_dr_production_gate_decision_report.py`

**Purpose**: Build a single-dR production gate decision report.
**Topic group**: `single_dr_production_gate_decision_report.py` + `single_dr_gate_decision_{loaders, writers}` (3 files)
**Originating spec**: `retired-provenance:038bfb65fe90:29` (designated PR2 split target)
**Status note**: The report now writes both review summaries and product-facing
gate artifacts. `single_dr_gate_activation_decisions.tsv` translates implemented
row-level gate candidates into activation rows, and
`single_dr_gate_changed_row_bundle.tsv` records the required changed-row review
fields for every product-affecting row removal. These files are intended to feed
`apply_shared_peak_identity_activation.py`; after activation is applied, rerun
the gate and require zero pending activation-decision rows before claiming the
8RAW slice has no remaining product mutation. Gate-candidate report assembly
indexes family rows by risk classification once and reuses those buckets for the
individual gate summaries while preserving blocked-row order and existing output
schemas.

---

### `multi_tag_adduct_audit.py`

**Purpose**: Audit multi-tag and adduct evidence.
**Topic group**: `multi_tag_adduct_audit.py` (single-file)
**Originating spec**: `retired-provenance:b11335f3179c`
**Recent doc**: `retired-provenance:000d7d4caa29`

---

### `target_pair_rt_candidate_plot_review.py`

**Purpose**: Render target-pair RT candidate review plots from RAW XIC traces for human adjudication of paired analyte/ISTD RT candidate selection.
**Topic group**: target-pair RT review; reuses `gaussian15_morphology_trace`.
**Status note**: Diagnostic-only RAW-backed review plots; does not change selection or matrix areas.

---

### `targeted_ms1_shape_identity_from_grid.py`

**Purpose**: Convert an existing targeted own-max trace grid plus per-sample
summary into `targeted_ms1_shape_identity_v0` evidence rows with the formal
`own_max_same_peak_support` token.
**Topic group**: targeted NL dropout / MS1 shape identity; reuses
`xic_extractor.diagnostics.targeted_ms1_shape_identity`.
**Status note**: Reads existing TSV artifacts only and writes a diagnostic TSV.
It does not open RAW files, recompute extraction, inject support into normal
runs, change selected RT/area, or mutate workbooks/matrices.

---

### `build_targeted_ms1_shape_identity_supports.py`

**Purpose**: Build generic `targeted_ms1_shape_identity_v0` evidence rows from
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
effective config policy is `limited_5hmdc_5medc_v1` and no support TSV is
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
`targeted_ms1_shape_identity_v0` `--support-tsv`. It fails closed unless every
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

The Instrument QC suite is a coherent product surface; six entry-points
share the `instrument_qc_*` prefix. Per the lifecycle spec, several of
these are candidates for "seasonal cadence" declarations (calibration runs
are not daily).

### `instrument_qc_sequence_manifest.py`

**Purpose**: Build docs-derived instrument QC sequence manifest.
**Originating spec**: `retired-provenance:9b1b633f6f09`
**Status note**: The `run_instrument_qc.py --method-doc` flow writes the sequence manifest, legacy `instrument_qc_injection_order.csv`, and additive `instrument_qc_sample_metadata.tsv` using `sample_metadata_v1`. The sample-metadata sidecar projects matched RAW rows and raw-dir-only instrument-QC rows as metadata only; roles do not alter instrument-QC trend values or matrix outputs.

---

### `instrument_qc_sdolek_calibration.py`

**Purpose**: Calibrate Phase 1 SDO/LEK trends with method-doc order.
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

Matplotlib-rendered MS1 overlay visuals for human review of backfill candidates.
Legacy `family_ms1_*` filenames and `feature_family_id` fields mean public
row-label compatibility, not product identity authority. Peak-group overlays are
context; mode-aware outputs should use
`peak_hypothesis_id` / selected-apex mode evidence when a row may contain
multiple MS1 peak modes.

### `family_ms1_overlay_plot.py`

**Purpose**: Render MS1 overlay evidence for one legacy family-id peak group.
**Topic group**: `family_ms1_overlay_plot.py` + `_evidence`, `_models`, `_rendering`, `_rendering_styles`, `_trace`, `_writers` (7 files)
**Originating spec**: (none found; landed alongside backfill review work)
**Status note**: Peak-group overlay PNG/PDF is context-only and intentionally keeps two panels: absolute RT own-max pattern and raw-intensity signal height. The writer also emits sibling `_hypothesis.png` / `_hypothesis.pdf` files that keep apex-aligned MS1 shape on the hypothesis-evidence surface, using detected NL seed traces as the anchor reference instead of letting rescued traces define the reference shape. Detected/rescued selected-peak traces are drawn even when selected/cell local-window peak dominance is below `0.5`; low dominance is an annotation/warning for review, not a visibility gate. The shaded band marks the selected/cell peak segment used for the visual comparison. `--targeted-workbook` / `--sample-info` remain accepted for CLI compatibility, but the current two-panel peak-group context overlay does not render a drift-corrected iRT panel. Area, apex-delta, shape-similarity scatter, and iRT/mode evidence remain hypothesis/mode questions and should not be put back into the peak-group context overlay. This renderer remains diagnostic-only and does not change backfill decisions or matrix areas.

---

### `family_ms1_overlay_batch.py`

**Purpose**: Render queued peak-group MS1 overlays from a backfill review report.
**Topic group**: shares helpers with `family_ms1_overlay_plot`
**Pairs with**: `family_ms1_backfill_review_report.py` (produces the queue, this consumes it)
**Status note**: Preserves optional queue `seed_group_id` in `family_ms1_overlay_batch_summary.tsv` so downstream retained-backfill evidence gates can join overlay evidence at seed-group precision. Summary rows carry separate apex-aligned shape metrics and own-max absolute-RT shape / absolute apex cluster metrics; these are evidence notes, not a composite `backfill_score`. Queues without `seed_group_id` remain supported for legacy family context, but seed-specific retained-backfill gates treat them as insufficient and request an exact `seed_group_id` overlay before using visual support/blockers. The CLI writes summary/Markdown incrementally after each row and supports `--reuse-existing`, which rebuilds summary rows from completed trace artifacts and, in rendered mode, completed PNG/PDF bundles without re-reading RAW; this makes large 85RAW queues resumable after timeout. In-process preset callers use the same renderer without incremental summary rewrites, then write final batch outputs once. RAW trace extraction is sample-batched and uses bounded scan-window super-window grouping before cropping traces back to their original request windows; `family_ms1_overlay_batch_summary.json` records selected rows, RAW opens, XIC requests, exact scan windows, super-window groups, chromatogram calls, and trace point counts. `--evidence-only` keeps the same trace TSV/JSON and summary schema while leaving `png_path` / `pdf_path` blank for image-free publication runs.
`--evidence-cache-dir` is an opt-in evidence-only accelerator for repeated
modeling validation. Cache keys bind the alignment-cell hash, RAW/DLL paths,
overlay source, rank/output prefix, legacy family/sample/seed request, mz/ppm/RT
window, and peak-group center RT. Cache v4 also records per-sample RAW identity
with `path_stat_v1` (`resolved_path`, size, mtime, device, inode). Warm hits
reuse cached trace TSV/JSON evidence without opening RAW files only after the
cached trace payload, stable provenance, and current RAW identities still match
the request; missing or mismatched RAW identity fails closed to normal RAW
extraction and can store exact evidence for future runs. Rendered PNG/PDF runs
ignore the evidence cache. Cache rows remain evidence-provider inputs only and
must still flow through the retained gate, shift-aware gate, consolidation, and
publication checker before any matrix output is accepted.

---

### `family_ms1_overlay_evidence_cache_seed.py`

**Purpose**: Seed a `family_ms1_overlay_batch.py` evidence cache from an
already-reviewed overlay summary and its trace TSV/JSON artifacts.
**Topic group**: `family_ms1_overlay_batch.py`
**Status note**: This is a no-RAW cache warm-up helper for repeated
8RAW/85RAW validation loops. It verifies the same request/key inputs as the
overlay batch cache, copies trace summaries, writes normalized trace JSON
provenance with the same per-sample RAW identity binding into the cache, and
writes the shared cache index. If RAW identity cannot be verified, the row is
not seeded as a reusable cache hit. It does not render overlays, re-extract RAW,
modify source summaries, or prove product readiness.

---

### `family_ms1_alignment_experiment.py`

**Purpose**: Render honest RT-interpretation comparison panels for one legacy family-id peak group.
**Topic group**: underlying single-peak-group experiment for `family_ms1_alignment_experiment_batch.py`; reuses `family_ms1_overlay_*` helpers.
**Status note**: Diagnostic-only RT-interpretation comparison; does not change backfill decisions or matrix areas. The batch wrapper reads existing overlay trace JSONs and runs this per successful overlay row.

---

### `family_ms1_alignment_experiment_batch.py`

**Purpose**: Convert `family_ms1_overlay_batch_summary.tsv` trace JSON outputs into shift-aware source-family alignment experiment outputs in batch.
**Topic group**: `family_ms1_alignment_experiment.py`
**Status note**: This no-RAW batch wrapper reads existing overlay trace JSONs, runs the single-peak-group shift-aware alignment experiment for successful overlay rows, and writes `family_ms1_alignment_experiment_batch_summary.tsv/json`. It preloads source-family provenance for selected peak groups once per batch and the underlying best-shift search reuses normalized trace curves across candidate shifts, so matrix-only summary generation does not rescan the full cell-evidence TSV or resmooth traces per shift. It uses deterministic `<rank>_<family>_shift_aware` output prefixes so downstream `shift_aware_backfill_calibration_pack.py --shift-aware-summary-dir` can collect `*_source_family_best_shift_summary.tsv` files without hand-built command lists. `--no-images` keeps the summary TSV outputs, including `*_source_family_best_shift_summary.tsv`, while leaving `source_best_shift_png` blank and skipping PNG review rendering; this is the matrix-only publication path. `--start-rank`, `--limit`, and `--reuse-existing` make full 85RAW review queues resumable after overlay rendering; in no-images mode, reuse requires completed summary TSVs rather than completed PNGs. Failed or missing overlay rows are reported as skipped/failed and do not imply standard-peak support.

---

### `changed_row_mode_overlay_review.py`

**Purpose**: Convert changed-row peak-group MS1 overlay trace JSONs into a
mode-aware review surface: RAW selected-apex RT-mode evidence TSV,
PeakHypothesis review projection TSV, per-sample mode review TSV,
legacy family-id summary TSV, review-only similarity TSV/summary, Gaussian15-smoothed
mode-colored PNGs, and an HTML gallery.
**Topic group**: consumes `family_ms1_overlay_batch.py` trace JSONs plus
`alignment_matrix_identity.tsv`; reuses
`xic_extractor/alignment/shared_peak_identity_explanation/rt_mode_evidence.py`
and `peak_hypothesis_selection.py`.
**Status note**: This tool keeps `feature_family_id` as provenance only. RAW
overlay-derived modes are emitted as review-only evidence, not typed iRT or
product authority. Optional matrix RT drift and MS1 pattern sidecars are
reported in the review-only similarity panel, which computes
Gaussian15-smoothed apex-aligned shape similarity within the selected mode and
badges wrong-apex, partial-shape, multimodal-family, and coherent-review cases.
Subthreshold Gaussian15 candidate markers are computed once per trace and reused
across sample rows, family summaries, and mode plots. The similarity badge is a
human triage aid, not a product gate. It is intended for changed-row
adjudication where a plain family overlay can hide selected-apex multimodality
or global trace apex conflicts.

---

### `qc_ms1_pattern_reference.py`

**Purpose**: Build a `diagnostic_only` nearest-injection-QC MS1 pattern reference sidecar from `family_ms1_overlay_plot` RAW trace JSON plus SampleInfo injection order; reports whether the closest QC supports, conflicts with, or cannot adjudicate a reviewed sample's selected MS1 peak.
**Topic group**: `qc_ms1_pattern_reference.py` + `xic_extractor/alignment/shared_peak_identity_explanation/qc_ms1_pattern_reference.py`
**Originating context**: V2 shared peak identity evidence-chain follow-up for using nearby QC MS1 pattern as an RT-drift / wrong-peak adjudication surface.

---

## Area / Region Audits

### `alignment_primary_area_authority_audit.py`

**Purpose**: Audit `alignment_cells.tsv` primary matrix area authority against
the Gaussian15 morphology area contract; reports fail-closed rows with missing
MS1 morphology area and hard-fails any non-Gaussian source that still writes
primary matrix area.
**Topic group**: `alignment_primary_area_authority_audit.py` (single-file)
**Originating context**: 2026-06-05 Product Authority Reconciliation v1
follow-up for owner scalar fallback / retired area-source leakage.
**Status note**: Reads existing `alignment_cells.tsv`, writes summary and
flagged-row TSV/JSON only, and does not mutate matrices, workbook outputs,
owner backfill, or RAW-derived evidence. The CLI loads `alignment_cells.tsv`
once and shares the row analysis between summary counts and flagged-row output;
the path-based summary/flagged helper functions remain available for focused
tests and ad hoc checks. A non-Gaussian primary area source is a
product-authority failure; missing Gaussian15 morphology area remains fail-closed
and requires trigger-rate review before promotion.

### `gaussian15_area_pressure_audit.py`

**Purpose**: Build a diagnostic-only pressure surface for Gaussian15 morphology
area versus raw area and configured/default-15 smoothing duration from
`peak_candidates.tsv`.
**Topic group**: `gaussian15_area_pressure_audit.py` (single-file)
**Originating context**: 2026-06-05 Product Authority Reconciliation v1
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

### `area_integration_uncertainty_audit.py`

**Purpose**: Classify targeted/untargeted area mismatch by integration audit.
**Topic group**: `area_integration_uncertainty_audit.py` + `_io`, `_models`, `_analysis`, `_writers` (5 files)
**Originating spec**: `retired-provenance:298c51c7ae51`
**Recent doc**: `retired-provenance:62bdbc64baf0`, `retired-provenance:f5c2e710208f`
**Schema note**: The current accepted `alignment_cell_integration_audit.tsv`
schema no longer emits or consumes linear-edge rollback columns. Area
uncertainty uses the reported `area_baseline_corrected` value from the current
audit schema only.

---

### `region_first_safe_merge_comparison.py`

**Purpose**: Compare default XIC output with opt-in region-first safe merge.
**Topic group**: `region_first_safe_merge_comparison.py` (single-file)
**Originating spec**: `retired-provenance:4545acb11ee0` + `retired-provenance:801934525810`

---

## One-off Fixtures

Tools that build deterministic test fixtures from production data; expected
to be re-run only when the underlying data shape changes.

### `build_istd_false_missing_fixture.py`

**Purpose**: Build ISTD false-missing validation fixture.
**Topic group**: `build_istd_false_missing_fixture.py` (single-file)
**Originating plan**: `retired-provenance:6d2a06f8be03`
**Cadence note**: "Build-once" — should declare a per-campaign or per-data-shape cadence per the lifecycle spec's seasonal-cadence exception.

---

## Delta Mass / Pt Pattern Discovery

Diagnostics-only bridge tooling for the `delta_mass(pattern_id=pt)` Discovery
lane (`docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`).
This group must stay diagnostics-only: no Alignment, Backfill, ProductWriter,
or workbook Matrix imports (enforced by `tests/test_pt_pattern_boundaries.py`
for the core `xic_extractor.discovery` / `scripts.run_pt_pattern_discovery`
modules; this tool follows the same boundary by convention).

### `pt_pattern_scan_proxy.py`

**Purpose**: Scan-level Pt pattern proxy. Reads MS1 scans directly from a
RAW file (reusing `xic_extractor.raw_reader.open_raw`/`RawFileHandle`) and
emits a schema-valid Detected Features CSV
(`xic_extractor.discovery.delta_mass_csv.DETECTED_FEATURES_COLUMNS`) with
`source = "raw_scan_proxy_v1"` on every row, plus a `proxy_provenance.json`
provenance file, so the Pt pattern CLI
(`scripts/run_pt_pattern_discovery.py`) has something to run against for a
first diagnostic look at a real Fullscan sample.
**Topic group**: `pt_pattern_scan_proxy.py` (single-file)
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md` (Task 7)
**Status note**: This is explicitly **not** a product feature detector — no
chromatographic peak-shape modeling, no smoothing, no deconvolution. Its own
scan-level m/z lookup window (`--lookup-ppm`, default 20 ppm) matches the Pt
pattern matcher's real m/z gate by default, while wider exploratory windows
remain opt-in. Repeated full-pattern scan groups are written with
`trace_quality = "OK"` and blank `shape_score` so the bridge can produce review
candidates, but the provenance labels the output `diagnostic_only`; see
`docs/superpowers/validation/pt_pattern_discovery_v1/README.md`.

---

### `pt_pattern_trace_diagnosis.py`

**Purpose**: Chromatography-aware Pt pattern trace diagnostic. Reads an existing
`pt_pattern_candidates.csv`, extracts vendor XIC traces for base / +1 / +2 /
+4 around each candidate's base RT using
`xic_extractor.raw_reader.open_raw` / `RawFileHandle.extract_xic_many`, and
writes candidate-level trace support summaries, position-level co-elution
metrics, and optional trace points. Current full-context schema v4 keeps the
full extraction-window semantics, including
`pt_pattern_trace_points.csv`. Anchor-local segmentation is emitted separately
as `pt_pattern_anchor_local_trace_*.csv` under
`pt_pattern_anchor_local_trace_diagnosis.v3`. For the anchor-local output, each
Pt position must yield exactly one chromatographic segment that contains the
candidate anchor RT; its co-elution metrics use the common intersection of the
four selected segments. Missing or ambiguous segments fail closed to review
instead of choosing the nearest or strongest peak.
**Topic group**: `pt_pattern_trace_diagnosis.py` +
`xic_extractor.discovery.pt_pattern_trace_evidence` +
`xic_extractor.discovery.pt_pattern_trace_batch`
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
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
third diagnosis. Schema v2 records
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only review-surface builder. It does not open RAW,
create candidates, mutate Discovery outputs, read Alignment/Backfill state, or
write Matrix/workbook artifacts. The sidecar records
`readiness_tier = diagnostic_only`, `oracle_status = pending_human_review`, and
`evidence_owner = xic_extractor.discovery.pt_pattern_trace_evidence`. The
historical `oracle_status` field names workflow state only: filled and promoted
human labels remain bounded adjudication evidence, not independent ground truth.
An `uncertain` label stays unresolved and must not be counted as a true reject.
Current full-context inputs use packet schema v4; anchor-local inputs use packet
schema v5 with explicit `anchor_segmentation_*` evidence fields. Both require a
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
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
overlay/machine schema v5. Rendering, sealed-call projection, and artifact
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only despite the historical `oracle` names in its
schema and paths. Promotion schema v2 requires each adjudicated row to carry a
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
with current manual-review packets and sealed machine-call CSVs, then write a v2 refresh
draft, a pending human-review worklist, stale old-oracle rows, and a summary
JSON with input SHA-256 provenance.
**Topic group**: `pt_pattern_oracle_refresh.py` +
`xic_extractor.discovery.pt_pattern_oracle_refresh`
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only adjudication-maintenance surface. It does not
open RAW, create candidates, recompute trace evidence, mutate Discovery outputs,
read Alignment/Backfill state, or write Matrix/workbook artifacts. It retains
legacy columns for audit, but matching packet-row and candidate IDs prove only
logical continuity: without exact reviewed-artifact identity, old labels remain
`pending_human_review` instead of being carried forward. Candidate conflicts
remain conflicts, and old rows absent from the current packet are reported as
stale. A refresh draft can be packaged only after every pending/conflicting row
is freshly resolved under the promotion v2 provenance contract. The
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only threshold calibration surface. It does not
open RAW, create candidates, recompute trace evidence, mutate Discovery
outputs, read Alignment state, or write Matrix/workbook artifacts. The
calibration consumer validates current manifest-sealed packet schema v4/v5 and requires each promotion
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only synthesis surface. It consumes already-produced
CSV/JSON artifacts, writes summary JSON, Markdown, assembly-layer CSV, and known
miss examples, and does not open RAW, create candidates, mutate Discovery
outputs, read Alignment/Backfill state, decide ratios, change detector rules,
or write Matrix/workbook artifacts.

---

### `pt_pattern_candidate_assembly_replay.py`

**Purpose**: Replay inclusion-list rows whose four list-relative m/z pools exist
against the canonical local MS1 feature table, then identify the first failed
mass, quality, RT, or matcher-emission condition.
**Topic group**: `pt_pattern_candidate_assembly_replay.py` +
`xic_extractor.discovery.pt_pattern_candidate_assembly_replay`
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only no-RAW replay. It reuses the current Pt
candidate matcher and shared mass-accuracy helpers, writes row-level CSV, summary JSON,
and chart-first HTML, and does not change detector rules, counted detection,
export, Alignment/Backfill, or Matrix authority.

---

### `pt_pattern_same_scan_replay.py`

**Purpose**: Evaluate fixed theoretical base, +1, +2, and +4 m/z windows in the
same Fullscan MS1 scans for current positive diagnostic hypotheses and known
inclusion-list controls, then expose strict four-position and
core-with-conditional-+4 window-occupancy evidence.
**Topic group**: `pt_pattern_same_scan_replay.py` +
`xic_extractor.discovery.pt_pattern_same_scan_evidence` +
`xic_extractor.discovery.pt_pattern_same_scan_replay`
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only RAW-backed replay. It loads or builds one MS1
index per run, never reopens RAW per candidate, preserves below-noise +4 as
censored evidence, and reports shared-feature conflict components. Replay v3
keeps the v2 strict component rule and treats `(source_set, seed_id)` as the
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

### `pt_pattern_conflict_graph_replay.py`

**Purpose**: Build a provenance-bound pairwise shared-claim graph over frozen Pt
candidate rows, then compare connected-component-wide `at_most_one` semantics
with the pairwise incompatibility structure represented by those rows.
**Topic group**: `pt_pattern_conflict_graph_replay.py` +
`xic_extractor.discovery.pt_pattern_conflict_graph` +
`xic_extractor.discovery.pt_pattern_conflict_graph_replay`
**Originating plan**: `docs/superpowers/plans/2026-07-15-pt-pattern-event-component-contract.md`
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

### `pt_pattern_ddms2_support_replay.py`

**Purpose**: Replay Fullscan-primary Pt pattern seeds against a DDMS2 local MS1
feature table to inspect whether the DDMS2 run contains matching base/+1/+2/+4
support near the Fullscan seed.
**Topic group**: `pt_pattern_ddms2_support_replay.py` +
`xic_extractor.discovery.pt_pattern_ddms2_support_replay`
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
**Status note**: Diagnostic-only support replay. It does not open RAW, create
Fullscan seeds, recompute trace evidence, decide DDMS2 targeting opportunity,
mutate Discovery outputs, read Alignment/Backfill state, or write
Matrix/workbook artifacts. Its m/z tolerance and RT window must be positive
finite values. Without the historical target/inclusion list,
missing DDMS2 support is context only, not negative evidence against a Fullscan
Discovery seed.

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
**Originating plan**: `docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
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
written. Diagnostic-gate schema v2 preserves the input schema, assignment SHA,
extraction SHA, and artifact role in every output row.
`--require-zero-fpfn` makes the CLI fail if bounded-oracle replay has any false
positive or false negative. Reviewed-label replay first requires an exact
promotion-v2 row and actual review-packet SHA/candidate/sample match; synthetic
trace-summary rows cannot stand in for that packet. The pass/fail result is
still only a diagnostic gate against those reviewed labels. A zero-error result is resubstitution, not
ground-truth accuracy or held-out validation, and must not be treated as final
detector performance, detector activation, counted-detection authority, or
export authority without a later public-surface contract.

---

## Agent / Docs Workflow Guards

Tools that guard repo-local agent and documentation workflow. These are not
LC-MS evidence diagnostics and must not claim product behavior readiness.

### `docs_placement_guard.py`

**Purpose**: Check staged A/M/R/C Markdown files for repo/Obsidian placement
markers before commit; block lifecycle-managed Markdown deletions without
matching retirement evidence; and fail closed on a same-path
`body_to_compatibility_index` transaction unless its preimage, private
source-copy readback, claim census, typed owner locators, replacement hash, and
normalized dependency census all agree. Non-lifecycle deletions are ignored.
**Topic group**: `docs_placement_guard.py` + `.codex/hooks/xic_hook_policy.py`
**Governing doc**: `docs/agent/obsidian-handoff-contract.md`
**Status note**: Reads staged git metadata, staged Markdown/JSON text, deleted
or replaced source blobs, source/claim hashes, exact headings, relative links,
literal referrers, and named constant consumers. It does not read RAW,
mutate files, stage changes, alter product output, copy to Obsidian, or decide
semantic product absorption. Hook integration runs the same checker after shell
`git add` and before shell `git commit`; failures report paths, reasons, and
required markers without auto-fixing. Destructive retirement still belongs to
`retire_docs.py --evidence`.

---

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
Use `--repo-only --fail-on-completed-transient` in CI/PR closeout to fail when
completed transient originals remain outside allowed stub states without reading
the private Obsidian vault.
**Topic group**: `docs_management_audit.py` + `docs_placement_guard.py` +
`handoff_retention_audit.py`
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

### `handoff_retention_audit.py`

**Purpose**: Audit repo-tracked handoff retention state. It checks that every
git-tracked file under `docs/superpowers/handoffs/current/` and
`docs/superpowers/handoffs/archive/` has a `RETENTION.tsv` row, that retention
decisions are valid, that tracked current handoffs stay compact, and that
tracked archive cleanup remains manifest-driven. Ignored local handoffs are
private workspace state and are not audited here.
**Topic group**: `docs_management_audit.py` + `handoff_retention_audit.py`
**Governing doc**: `docs/superpowers/handoffs/README.md`
**Status note**: Read-only docs governance audit. It reports Obsidian-transfer
and cleanup candidates but never moves files, writes to Obsidian, stages
changes, or authorizes tracked deletion.

---

### `generate_stubs.py`

**Purpose**: Generate compact repo stub files from a Link Stub Blockers TSV so
historical links can point to Obsidian originals without keeping long private
history bodies in repo.
**Topic group**: `docs_management_audit.py` + `generate_stubs.py`
**Governing doc**: `docs/agent/obsidian-handoff-contract.md`
**Status note**: Dry-run by default; `--execute` writes stub files but does not
move vault pages, update manifests, stage changes, or authorize deletion.

---

### `retire_docs.py`

**Purpose**: Retire completed transient docs/superpowers plans, specs, and
notes after lifecycle metadata, retirement evidence, source-copy readback flag,
source hash, and exact repo referrers are checked. Unreferenced completed docs
leave the repo only with matching `pass_can_retire` evidence; referrer-bound
docs are kept unless `--stub-bound` is explicitly supplied to create a short
compatibility stub with matching referrer evidence. For a protected same-path
replacement, `--source-copy-only` copies and reads back the exact preimage
without deleting, stubbing, or editing the repo source.
**Topic group**: `docs_management_audit.py` + `retire_docs.py`
**Governing doc**: `docs/project-layout.md`;
`docs/agent/obsidian-handoff-contract.md`
**Status note**: Dry-run unless `--execute` is supplied. It skips README,
schema, JSON, TSV, active/draft lifecycle docs, missing-metadata docs, and
control-plane anchors; it does not decide product authority or validation
readiness. Product absorption review must happen before retirement and be
recorded in the evidence JSON supplied through `--evidence`.
`--source-copy-only --execute` also requires that evidence, verifies both
top-level and source-entry hashes, and reports only the stable repo source key
and hash; the repository evidence stores no private absolute vault path.

---

### `validate_vault_page.py`

**Purpose**: Validate required Obsidian vault page metadata before promotion:
frontmatter, summary, lifecycle, tier, and visibility tag.
**Topic group**: `docs_management_audit.py` + `validate_vault_page.py`
**Governing doc**: `docs/agent/obsidian-handoff-contract.md`
**Status note**: Read-only single-page metadata check. It does not edit vault
pages, promote staged notes, update manifests, or change repo files.

---

## Shared Infrastructure

Not entry-points, but referenced by multiple topic groups:

- `xic_extractor/tabular_io.py` — package-neutral shared delimited/TSV read-write, file SHA256 hashing, scalar parsing, numeric equality, header validation, label splitting, text grouping, and value formatting. `xic_extractor/diagnostics/diagnostic_io.py` and `tools/diagnostics/diagnostic_io.py` re-export it as compatibility shims for existing diagnostic imports. Cluster 1, the listed Cluster 3 loaders, alignment backfill authority/projection modules, and backfill/standard-peak diagnostics now reuse this helper; use it before adding local `_read_required_tsv`, `_bool_value`, `_optional_float`, `_text`, `_required_indexes`, `_write_tsv`, `_sha256_file`, `_numeric_equal`, or `_group_by_family` copies.
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
- **Adding a new evidence-provider diagnostic**: First use
  `.codex/skills/xic-architecture-preflight/SKILL.md` to name the package owner,
  reusable helper, evidence role, RAW/TSV call-cost model, public contract risk,
  validation tier, and stop rule. HCD-PI, Delta Mass, CID-NL, RT/iRT, MS1
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
