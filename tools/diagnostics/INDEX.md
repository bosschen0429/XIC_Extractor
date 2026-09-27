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

## Table of Contents

1. [Validation Gates](#validation-gates)
2. [Evidence Consistency](#evidence-consistency)
3. [Alignment Diagnostics](#alignment-diagnostics)
4. [Backfill Reviews](#backfill-reviews)
5. [Peak / Candidate Audits](#peak--candidate-audits)
6. [Targeted Benchmarks & Reviews](#targeted-benchmarks--reviews)
7. [Instrument QC](#instrument-qc)
8. [Peak-Group / Overlay Visualization](#peak-group--overlay-visualization)
9. [Area / Region Audits](#area--region-audits)
10. [Delta Mass / Pt Pattern Discovery](#delta-mass--pt-pattern-discovery)
11. [Agent / Docs Workflow Guards](#agent--docs-workflow-guards)
12. [Surface Population And Reachability](#surface-population-and-reachability)

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
`docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`;
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
`output/validation/cid_nl_default_activation_gallery_review/feature_inclusion_gate/`.
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
`docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Diagnostic-only activated-copy surface. It reads the current
public `alignment_matrix.tsv` and `alignment_matrix_identity.tsv`, writes
`alignment_matrix_activated_copy.tsv`,
`alignment_matrix_identity_activated_copy.tsv`,
`cid_nl_activation_copy_value_delta.tsv`, and
`cid_nl_activation_copy_candidate_summary.tsv/json` under ignored
`output/validation/cid_nl_default_activation_gallery_review/activation_copy_candidate/`.
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
`docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Diagnostic-only acceptance gate. It writes
`cid_nl_activation_copy_acceptance_summary.tsv/json` and
`cid_nl_activation_copy_matrix_diff.tsv` under ignored
`output/validation/cid_nl_default_activation_gallery_review/activation_copy_candidate/acceptance/`.
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
`docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`;
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
**Status note**: Production-candidate adopt gate for the narrowed 95-cell
validation-copy bundle. It writes
`cid_nl_activation_adopt_gate_summary.tsv/json` and the compact 20-transition
`cid_nl_activation_adopt_manifest.tsv` under ignored
`output/validation/cid_nl_default_activation_gallery_review/activation_adopt_gate/`.
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

---

---

### `shared_peak_identity_explanation.py`

**Purpose**: Explain shared-peak identity from a manual oracle and existing
alignment/candidate-gate artifacts. Optional blast-radius analysis summarizes
existing cohort artifacts; shadow-label analysis compares human labels with
machine evidence and reports diagnostic readiness.
**Topic group**: `shared_peak_identity_explanation.py` + `xic_extractor/alignment/shared_peak_identity_explanation/*`
**Status note**: The default command writes an oracle copy, evidence vectors,
explanations, run facts, and a Markdown report. Optional modes add blast-radius,
shadow-label, machine-evidence, and readiness artifacts. Evidence inputs cover
CWT shape, trace support, candidate MS2, MS1 coherence, QC reference, sample
negative evidence, RT drift, RT modes, and PeakHypothesis selection. The command
can generate the supported evidence sidecars; `--help` owns the exact options.
Candidate evidence must retain sample/family provenance; missing or proxy-only
evidence does not become a positive identity claim. RAW-backed evidence remains
opt-in. Hypothesis consistency and mode assignments are diagnostic outputs and
cannot authorize values, rewrite matrices, or apply product activation.

---

---

### `diagnose_shared_peak_wrong_peak.py`

**Purpose**: Explain machine-observed wrong-peak conflicts and propose alternate
local peaks for review.
**Topic group**: `diagnose_shared_peak_wrong_peak.py` + `xic_extractor/alignment/shared_peak_identity_explanation/wrong_peak_root_cause.py`
**Status note**: Consumes machine-evidence support, alignment cells, and optional
overlay trace-data JSON. The root-cause sidecar records diagnostic conflict
classes, selected-cell evidence, and alternate local maxima without activation
status or writer decisions. Alternate peaks are review candidates only; the
command does not retarget peaks or rewrite matrices.

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

---

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

### `backfill_decision_ledger.py`

**Purpose**: Build a deterministic, bounded, no-RAW decision ledger and summary
from a retained full-universe artifact. Discovery candidate rows are rehydrated
through their owner receipts; incomplete owner/group/request projections are
reported as unavailable instead of being promoted to semantic proof.
**Topic group**: `backfill_decision_ledger.py` +
`xic_extractor/diagnostics/backfill_decision_ledger.py`
**Originating issue**: [#195](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/195)
**Status note**: The output remains `diagnostic_only`. Displayed seed m/z/RT
duplicates are labeled as a projection-only request proxy, never as exact
request equivalence or optimization savings. Ranking requires a complete
`full-audit` Backfill scope with zero skipped features, closed feature counts,
and exact equality with the review-group universe. Candidate/group projection
links bind both sample and candidate identity; request-proxy rendering is
row-order neutral. Candidate-layer uniqueness is limited to feature-backed
states and reported as acceptance of the Discovery owner's parser invariant,
not as an independently variable post-owner duplicate counter. Populated IDs
outside that invariant domain are counted separately. The tool neither accepts
RAW paths nor changes scientific
selection, Backfill eligibility, Matrix, writer, or activation behavior.

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

---

---

---

### `retained_backfill_evidence_gate.py`

**Purpose**: Emit a `diagnostic_only` machine sidecar for product-retained backfill family/seed groups, linking actual primary-matrix backfill behavior to seed provenance, MS1 overlay support/blockers, missing evidence, and source artifact hashes.
**Topic group**: `retained_backfill_evidence_gate.py` + `xic_extractor/diagnostics/retained_backfill_evidence_gate.py`
**Originating spec/goal/plan**: `retired-provenance:0a1c58083825`; `retired-provenance:69bd6d102e9c`; `retired-provenance:0ceba9b23ba9`
**Status note**: Writes `alignment_retained_backfill_evidence_gate.tsv`, `alignment_retained_backfill_evidence_gate.json`, `alignment_retained_backfill_missing_overlay_queue.tsv`, and `alignment_retained_backfill_overlay_review_queue.tsv`. It consumes existing alignment review/cell/matrix, optional owner backfill seed audit, and optional overlay summary TSVs only. `detected=0` families are excluded from main rows and counted separately. Exact `seed_group_id` overlay rows are required for seed-specific MS1 support/blocker decisions; legacy overlay rows without `seed_group_id` are retained only as family context and are re-queued as `missing_seed_specific_overlay`. Missing-overlay rows with seed provenance are emitted as review queues; the full missing-overlay queue and family-deduplicated review queue share one ordered candidate list during output writing. It does not accept RAW/DLL paths, does not generate overlays, does not mutate `alignment_review.tsv`, `alignment_cells.tsv`, `alignment_matrix.tsv`, workbook schemas, or product decisions, and does not declare production readiness.

---

---

---

## T5D Standard Peak contraction

T5D deleted the live modules and wrappers documented in the historical entries
below: MS1 authority bundle, heldout/productization gates, activation-scope and
reintegration product audits, shadow activation inputs, productization,
machine/preset orchestration, chunk consolidation/formal sidecar, source
republisher, stage replay, and publication checker. The descriptions remain as
historical provenance for retained artifacts; none names a runnable current
entry or current authority.

T5D adds no replacement Standard Peak package entry. Retained low-level overlay,
shift-aware, calibration, and gallery tools remain explicit offline
falsifiers/renderers. The current Backfill owners do not mint a
`diagnostic_eligible` HOLD subject or bind an independently supplied Matrix to a
ProcessingRunSpec attempt, so incomplete evidence remains unavailable and
cannot promote or write.

The clean-target replay is a retained historical diagnostic/oracle packet. Its
checker proves current-file self-consistency only; it does not authenticate the
packet's historical origin or immutability. T5D retired its ownerless writer and
registered authority scope, and the retained counts and hashes cannot mint
current authority. The final SP-001–SP-055 map is
`docs/superpowers/validation/standard_peak_t5d_contraction/capability_disposition.tsv`.

---

### Historical record — `standard_peak_ms1_authority_bundle.py` (retired/deleted)

**Purpose**: Convert standard-peak gate rows plus overlay trace provenance into a product-authorized MS1 same-peak sidecar consumable by shadow projection.
**Topic group**: `standard_peak_ms1_authority_bundle.py` + `xic_extractor/diagnostics/standard_peak_ms1_authority_bundle.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: Writes `standard_peak_ms1_pattern_coherence_evidence.tsv`, `standard_peak_ms1_product_authority_allowlist.tsv`, `shared_peak_identity_ms1_pattern_coherence_product_authorized.tsv`, an audit TSV, and summary JSON. `--authority-mode manual-oracle` keeps the historical manual calibration requirement; `--authority-mode machine-gate` authorizes rows from `standard_peak_gate_supported` without requiring a manual status label and records `machine_standard_peak_gate_authorized` / `machine_shift_aware_standard_peak_gate` provenance. Trace JSON read/decode/SHA work is cached per path within one run, but family/sample/vector validation and audit reasons remain per row. Trace JSON path/SHA validation, Gaussian-smoothed standard-peak family verdicts, and `same_peak_reason:shift_aware_standard_peak_gate_supported` are preserved before any projection can write matrix values.

---

### Historical record — `standard_peak_heldout_oracle_results.py` (retired/deleted)

**Purpose**: Evaluate deterministic standard-peak held-out oracle manifest rows against observed boundary/area results and write `heldout_oracle_results.tsv`.
**Topic group**: `standard_peak_shadow_activation_inputs.py` + `xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Thin CLI wrapper around the package-level held-out oracle evaluator. It reads `heldout_oracle_manifest.tsv`, observed result rows with `oracle_case_id`, observed boundary/area fields, and observed-result provenance fields, records the source artifact SHA, and writes the existing `standard_peak_seed_guard_heldout_oracle_results` schema. Manifest rows must declare `heldout_original_cell_status` proving the masked source cell was originally detected; `backfilled`, blank, or unknown statuses fail before result evaluation. Observed rows must identify an independent product-writer, masked-rerun, or boundary-reintegration source; oracle/manual-review/review-queue source copies fail closed after punctuation/whitespace canonicalization, and neutral observed source labels also fail when they canonicalize to the same value as the matching manifest `oracle_source`. The result source artifact must exist so its SHA can be recorded. It does not run RAW, generate oracle rows, mutate matrices, authorize non-standard peaks, or upgrade the standard-path seed guard beyond `production_candidate`; real/reviewed held-out oracle rows plus FULL COHORT expected-diff evidence are still required for `production_ready`.

---

### Historical record — `standard_peak_heldout_trace_oracle.py` (retired/deleted)

**Purpose**: Build deterministic held-out trace reintegration oracle packets from existing standard-peak trace JSON and detected cell evidence.
**Topic group**: `standard_peak_heldout_trace_oracle.py` + `xic_extractor/diagnostics/standard_peak_heldout_trace_oracle.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Reads `alignment_backfill_cell_evidence.tsv` plus existing `*_trace_data.json` artifacts and writes a full eligible pool, selected candidate TSV, `heldout_oracle_manifest.tsv`, `heldout_observed_results.tsv`, `heldout_oracle_results.tsv`, and summary JSON. It supports the existing `standard_high_signal_clean_trace` scope and the additive `standard_low_scan_clean_trace` scope, where scan count is 7-9 while trace status, shape, local/global ratio, height, width, and apex-delta remain clean. It also supports additional probe scopes: `standard_low_height_clean_trace`, where the clean envelope is retained but height is below 2e6; `standard_low_height_low_scan_clean_trace`, where height is below 2e6 and scan count is 7-9 while shape/local-global/width/apex remain clean; `standard_apex_delta_clean_trace`, where the clean envelope is retained but apex delta from family center exceeds 0.15 min; `standard_width_clean_trace`, where the clean envelope is retained but boundary width is outside 0.30-0.65 min; `standard_shape_margin_clean_trace`, where the clean envelope is retained but shape similarity is only the near-threshold band `0.93 <= shape < 0.95`; `standard_low_height_reintegration_stable_candidate_family_trace`, where candidate families are derived from a reintegration-stability audit plus activation scope audit and then filtered to low-height written rows; `standard_shape_clean_reintegration_stable_candidate_family_trace`, where the same family-level oracle is filtered to activation-scope shape similarity `>=0.95`; and `standard_reintegration_stable_candidate_family_trace`, where the family-level oracle is run over all reintegration-stability eligible written rows without the low-height or shape-clean filter. The first FULL COHORT no-RAW low-height probe found 230 eligible candidates across 54 families, selected 20 family cases, and failed closed at 19/20 selected cases because `FAM008651/TumorBC2312_DNA` exceeded the accepted boundary tolerance. The later bounded-window low-height diagnostic replay kept the default full-trace mode unchanged, added explicit `expected_window_bounded` observed reintegration with `expected_window_padding_min`, and passed selected 20/20 at `padding=0.5 min` with max boundary error 0.0857986 min and max area relative error 0.0564106; the matching explicit 57-row low-height scoped writer now has writer expected-diff approval and `readiness_tier=production_ready`. The formal low-height-low-scan bounded replay found 210 eligible rows across 51 families, selected 20 family cases, and passed 20/20 with max boundary error 4.80376e-05 min and max area relative error 0.00881912; the matching explicit 69-row low-height-low-scan scoped writer now has writer expected-diff approval and `readiness_tier=production_ready`. The formal low-height reintegration-stable replay is a family-level oracle, not a 220-row identity oracle: it records 220 audit-intersection rows across 66 families, `candidate_family_scope_match_level=family_id`, 1520 available detected trace candidates from those families, selected 20 family cases, and passed 20/20 with max boundary error 0.0830019 min and max area relative error 0.0725986; the matching explicit 220-row writer now has writer expected-diff approval and `readiness_tier=production_ready`. Shape-clean reintegration-stable is candidate/explanation evidence only: `heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/` records 104 audit-intersection rows across 33 families, 334 available detected trace candidates across 31 families, selected 20/20 pass, max boundary error 0.0830019 min, and max area relative error 0.0725986, but a temporary writer probe found 0 new matrix writes and 104 unchanged/pre-existing matrix values, so no public writer flag was retained. Apex-delta, width-only, shape-margin, and all-stability remain blocked/candidate-only probe scopes: the first FULL COHORT no-RAW apex-delta probe found 78 eligible candidates across 27 families, selected 20 family cases, and failed closed at 17/20 selected cases with max boundary error 2.19621 min and max area relative error 0.424518; the first FULL COHORT no-RAW width-only probe found 4 eligible candidates across 3 families, selected 3 family cases, and failed closed at 1/3 selected cases with max boundary error 1.86561 min and max area relative error 0.599229; the first FULL COHORT no-RAW shape-margin probe found 18 eligible candidates across 8 families, selected 8 family cases, and failed closed at 6/8 selected cases with max boundary error 0.0625542 min and summary max area relative error 0.198393; the formal all-stability family replay under `heldout_trace_reintegration_oracle_all_stability_family/` records 299 audit-intersection rows across 77 families, expands to 1694 available detected trace candidates from those families, selects 20 family cases, and fails closed at 19/20 because `FAM000949/NormalBC2261_DNA` has area relative error 0.19621, above the accepted 10% ceiling. Observed rows are produced by local-minimum reintegration over stored trace arrays plus `integration_from_peak_trace` with gaussian15 positive AsLS morphology area, then evaluated by the shared held-out oracle evaluator under the existing `0.1 min / 10% area` ceiling. It does not open RAW, mutate matrices, authorize non-standard peaks, or broaden the Backfill writer by itself.

---

### Historical record — `standard_peak_activation_scope_audit.py` (retired/deleted)

**Purpose**: Audit whether actual standard-peak `activation_value_delta.tsv` writes fall inside named trace evidence envelopes used by held-out oracles.
**Topic group**: `standard_peak_activation_scope_audit.py` + `xic_extractor/diagnostics/standard_peak_activation_scope_audit.py`
**Originating owner/goal/plan**: `docs/product/backfill.md`; `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance.
**Status note**: Reads an existing matrix-only `activation_value_delta.tsv`, the matching `shadow_production_projection_cells.tsv`, and sibling `*_trace_data.json` artifacts derived from `overlay_png_path`. It writes `activation_high_signal_clean_scope_audit.tsv`, `activation_high_signal_clean_scope_summary.tsv/json`, `eligible_activation_value_delta.tsv`, `narrow_activation_expected_diff_acceptance.tsv/json`, plus additive `low_scan_clean_activation_value_delta.tsv`, `low_scan_clean_activation_expected_diff_acceptance.tsv/json`, `low_height_clean_activation_value_delta.tsv`, `low_height_clean_activation_expected_diff_acceptance.tsv/json`, `low_height_low_scan_clean_activation_value_delta.tsv`, and `low_height_low_scan_clean_activation_expected_diff_acceptance.tsv/json`. The audit joins actual writes to projection rows by `matrix_value_source_row_sha256`, classifies missing projection/trace evidence separately from trace-matched ineligible rows, and applies four named envelopes: high-signal clean requires supported trace status, shape >=0.95, local/global >=0.95, height >=2e6, width 0.30-0.65 min, apex within 0.15 min of family center, and at least 10 boundary scans; low-scan clean uses the same clean envelope but requires height >=2e6 and 7-9 boundary scans; low-height clean keeps the clean trace/width/apex/scan envelope but requires height <2e6 and at least 10 boundary scans; low-height-low-scan clean requires height <2e6 and 7-9 boundary scans while shape/local-global/width/apex remain clean. The current FULL COHORT no-RAW combined audit covers 4613 broad candidate writes and classifies 72 high-signal clean, 42 low-scan clean, 57 low-height clean, and 69 low-height-low-scan clean rows; all four classes now have explicit scoped product writers with writer expected-diff approval. Each expected-diff acceptance verifies that the filtered eligible delta rows are exactly the eligible audit rows and contain no duplicate, missing, unexpected, non-eligible, non-written, unchanged, or blank-value rows. It does not open RAW, generate evidence, mutate matrices, or authorize product activation. Filtered delta TSVs are convenience artifacts only; product output must come from a separate productization run such as `standard_peak_backfill_productization.py --high-signal-clean-activation-scope-audit-tsv`, `--low-scan-clean-activation-scope-audit-tsv`, `--low-height-clean-activation-scope-audit-tsv`, or `--low-height-low-scan-clean-activation-scope-audit-tsv`, whose writer acceptance must pass before claiming a scoped product behavior.

---

### Historical record — `standard_peak_reintegration_stability_audit.py` (retired/deleted)

**Purpose**: Audit whether standard-peak activation writes have boundary-stable stored traces under two no-RAW reintegration views.
**Topic group**: `standard_peak_reintegration_stability_audit.py` + `xic_extractor/diagnostics/standard_peak_reintegration_stability_audit.py`
**Originating spec/goal/plan**: `docs/superpowers/plans/2026-06-15-productization-control-plane.md`
**Status note**: Reads `activation_high_signal_clean_scope_audit.tsv` from `standard_peak_activation_scope_audit.py`, reloads the referenced `*_trace_data.json` rows, and reintegrates each written trace twice: full stored trace and an expected-window-bounded trace around the stored reference boundary. It writes `reintegration_stability_audit.tsv` plus `reintegration_stability_summary.tsv/json`. The input scope audit must carry `standard_peak_activation_scope_audit` schema rows with nonblank source run ids; the summary records the upstream TSV SHA/source provenance. `--expected-window-padding-min` must be finite and <=0.5 min so the bounded reintegration remains an independent local check instead of collapsing into full-trace reintegration. A row is `eligible` only when full-trace and bounded-window boundary error are each <=0.1 min, full-trace and bounded-window area relative error are each <=10%, and the two reintegration methods agree within the same boundary/area tolerances. The first real no-RAW FULL COHORT run over the consolidated 4613-row activation scope found 299 eligible written rows, 3227 ineligible rows, and 1087 missing-evidence rows; 271 eligible rows were outside the four then-current production-ready scoped writer envelopes. This audit remains a `production_candidate` evidence class by itself, and the summary intentionally reports `status=candidate_pool_blocked`, `writer_authority_status=blocked`, and `production_ready=FALSE`: it proves a broader boundary-stability candidate pool exists, but it does not mutate matrices, create broad writer authority, replace masked/product-writer oracle evidence, or authorize broad 4613-row activation. A separate follow-up promoted only the low-height subset of this candidate pool: 220 low-height reintegration-stable rows have per-row stability evidence, a passing family-level heldout oracle over detected traces from the same families, plus explicit writer expected-diff and are now the fifth scoped `production_ready` Backfill writer.

---

### Historical record — `standard_peak_shadow_activation_inputs.py` (retired/deleted)

**Purpose**: Convert product-authorized standard-peak `shadow_production_projection_cells.tsv` accepts into `product_activation --matrix-only` input TSVs.
**Topic group**: `standard_peak_shadow_activation_inputs.py` + `xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: This is part of the built-in `dna_dr` standard-peak
publication path. Treat it as product-adjacent activation infrastructure, not a
disposable research diagnostic.
**Status note**: Writes `standard_peak_activation_decisions.tsv`, `standard_peak_activation_values.tsv`, `standard_peak_activation_acceptance.tsv`, `standard_peak_activation_inputs.tsv`, `standard_peak_activation_inputs_summary.json`, and additive `seed_guard_decisions.tsv`. It selects only rows with `shadow_decision=accept`, `current_matrix_written=FALSE`, `projected_matrix_written=TRUE`, a nonblank projected value, and `same_peak_reason:shift_aware_standard_peak_gate_supported` in the product-authority chain. Current-matrix rows are counted as already written and are not reactivated; nonstandard, context, blocked, or unprovenanced rows fail closed. When a seed-guard context is supplied by the productization bridge, standard-path candidates are evaluated against the N-banded cohort seed-support rule before activation decisions are written; low-seed and inconclusive rows do not write activation decisions. Before writing acceptance, the converter runs a standard-peak row-level gate that checks PeakHypothesis scope, auto-activate decision shape, matching activation values, source schema, source row SHA, and the standard-peak same-peak reason. Passing rows generate an existing-contract `activation_acceptance.tsv` with `must_not_regress_status=pass`, a max product-affecting row allowance equal to the gated selected row count, and an activation scope derived from authority provenance: manual-oracle rows stay `manual_oracle_seed_rows`, while machine-gate rows are labeled `machine_gate_standard_peak_rows` with `must_not_regress_basis=machine_shift_aware_standard_peak_gate`. With `--apply-matrix-only`, the CLI immediately calls the existing matrix-only product activation writer and emits an activated matrix under `<output-dir>/activated_matrix` unless `--activated-output-dir` is supplied. If the standard-peak gate fails, acceptance stays fail and product application must stop for review. This converter does not generate upstream evidence, change source alignment artifacts, or bypass product activation. Its files are diagnostic proposals, not a matrix-writing interface.

---

### Historical record — `standard_peak_policy_observed_oracle.py` (retired/deleted)

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
schema `standard_peak_policy_observed_oracle`; only rows with
`oracle_case_status=pass`, `included_in_product_acceptance=TRUE`, and
`observed_result_source=policy_observed_full_trace_reintegration` may be
consumed by `standard_peak_backfill_productization.py
--policy-observed-oracle-tsv --policy-observed-oracle-summary-json`. The current no-RAW FULL COHORT packet under
`output/productization_realdata_seed_guard_full_cohort_20260617/policy_observed_oracle_detected_flagged_full_trace/`
evaluated the prior 72 `detected_flagged` rows, passed 72/72, and recorded max
boundary error `8.91875e-05 min` plus max area relative error `0.098218`.
This producer does not open RAW, mutate matrices, loosen nonstandard-peak
policy, or authorize broad Backfill by itself; product behavior changes only
after generated policy replay and writer expected-diff pass.

---

### Historical record — `standard_peak_backfill_productization.py` (retired/deleted)

**Purpose**: Run the reviewed standard-peak backfill path end to end: convert shadow projection accepts into activation inputs, apply matrix-only product activation, and optionally render an activation-synced reconciliation gallery.
**Topic group**: `standard_peak_backfill_productization.py` + `xic_extractor/diagnostics/standard_peak_backfill_productization.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Load-bearing note**: Normal presets and `scripts/run_alignment.py` cannot
reach this path. It is an explicit offline historical/productization referrer
pending the T5D contraction census and cannot publish into source Alignment
Matrix outputs.
**Status note**: This retained offline tool reads historical shadow projection
and Alignment sidecars and writes activation projections only beneath its
explicit output directory; it cannot mutate or republish the source Alignment
Matrix. Retained FULL COHORT packets record scoped `production_ready` labels for the
old high-signal, low-scan, low-height, low-height/low-scan, and reintegration-
stable writer experiments. Those labels are historical artifact evidence, not
current normal-route or ProductWriter authority, and must not be consumed as
preset readiness. T5D owns deletion or diagnostic-only contraction of this
productization surface and its detailed evidence/oracle options.

---

### Historical record — `standard_peak_backfill_machine_pipeline.py` (retired/deleted)

**Purpose**: Run the standard-peak machine-gate chain from either an existing overlay summary or a retained-backfill review queue to activated matrix and optional gallery.
**Topic group**: retired offline orchestration chain.
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: This explicit offline orchestrator can start from an existing
an existing overlay batch summary or from a retained-backfill review queue
plus RAW/DLL paths. Normal presets and `scripts/run_alignment.py` have no
referrer to it. Historical `publication_mode` values control offline evidence
and rendering only; they are not normal-route choices or source-Matrix
authority. Its `status=pass` describes only the requested offline scope.
It can pass an opt-in `--evidence-cache-dir` to the overlay batch producer for
`evidence-only` runs; this reuses exact cached trace TSV/JSON evidence and does
not authorize stale overlay summaries or bypass matrix/publication checks.

---

### Historical record — `standard_peak_backfill_chunk_consolidation.py` (retired/deleted)

**Purpose**: Retained offline consolidation diagnostic for historical standard-peak machine-pipeline outputs.
**Topic group**: `standard_peak_backfill_machine_pipeline.py` + `standard_peak_backfill_productization.py` + `xic_extractor/diagnostics/standard_peak_backfill_chunk_consolidation.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: This module is not reachable from normal GUI, CLI, presets, or
workflows. Source-Matrix/identity targets and the old publication switch fail
closed before any I/O; source backup and republication are retired. The
remaining explicit formal-sidecar mode is a historical diagnostic referrer
awaiting T5D contraction/deletion and is not current product-output or Matrix
authority. Do not feed its `status=pass` or manifest to downstream as readiness
proof.

---

#### `standard_peak_backfill_preset.py` (retired/deleted)

**Purpose**: Retained explicit offline Standard Peak diagnostic wrapper.
**Topic group**: `standard_peak_backfill_machine_pipeline.py` +
`standard_peak_backfill_chunk_consolidation.py` +
`standard_peak_backfill_productization.py`
**Originating spec/goal/plan**: `goals/standard-peak-backfill-productization`
**Status note**: Before T5D, normal `scripts/run_alignment.py`, GUI, workflows,
and built-in presets no longer called this wrapper, while an explicit offline
caller could still run the retained evidence chain and request its historical
formal sidecar. T5D deleted this wrapper; no current entry point can run that
chain or publish its sidecar. The historical sidecar never became current
product authority.

---

### Historical record — `dna_dr_product_ready_stage_replay.py` (retired/deleted)

**Purpose**: Replay a single expensive `dna_dr_product_ready` post-alignment
stage from an existing alignment output directory for timing/parity validation.
**Topic group**: `standard_peak_backfill_preset.py` +
`scripts.check_product_ready_preset_publication`
**Status note**: Before T5D, this supported `--stage standard_peak`. It copied
the required source alignment artifacts into a fresh output directory, recorded
input artifact SHA/size/row-counts in
`dna_dr_product_ready_stage_replay_manifest.json`, reran the standard-peak
publication preset, and then ran the product-ready publication checker. T5D
deleted the replay and publication checker; no current `standard_peak` stage
entry point remains.

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

---

---

---

---

---

---

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

### Historical record — `backfill_evidence_reconciliation_gallery.py` (retired/deleted)

**Purpose**: Formerly assembled a Backfill reconciliation index and standalone HTML gallery from existing diagnostic artifacts.
**Replacement**: Interactive review is owned by the installed `xic-inspector` command and the method-neutral `xic_inspector` API, selection, and renderer. Scientific artifact production remains with its existing diagnostic owners; no Gallery input, CLI, or HTML compatibility surface remains.

---

### CID-NL differential evidence producer helpers

#### `cid_nl_default_activation_gallery_review.py`

**Purpose**: Write the no-RAW source/successor transition evidence consumed by CID-NL differential diagnostics.
**Topic group**: `cid_nl_default_activation_gallery_review.py`, `cid_nl_differential_overlay_review.py`, and `cid_nl_feature_inclusion_gate.py`.
**Originating spec/goal/plan**: `docs/superpowers/validation/cid_nl_default_activation_successor_authority_contract/README.md`; `docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`.
**Status note**: `write_transition_review_evidence(...)` consumes successor decisions and alignment review TSVs, then writes the deterministic `cid_nl_discovery_identity_differential_review.tsv` shape at the caller-supplied path. It preserves source/successor counts, coordinates, feature-inclusion and identity-authority gates, relationship state, and paired-overlay readiness. It has no CLI or HTML output and does not read RAW, mutate product outputs, or grant writer authority.

---

#### `cid_nl_differential_overlay_review.py`

**#441 disposition**: Migrated by #445 (#441 C3) to a curve bundle for offline XIC Inspector review. The summary's non-empty `review_path` points to that bundle; downstream gate and classification consumers use the locator.

**Purpose**: Generate paired source vs successor RAW-backed MS1 overlay review artifacts for CID-NL feature-inclusion and identity-relationship review.
**Topic group**: `cid_nl_default_activation_gallery_review.py`, `cid_nl_feature_inclusion_gate.py`, and the RAW `open_raw(...).extract_xic_many(...)` batch extraction path.
**Originating spec/goal/plan**: `docs/superpowers/validation/cid_nl_default_activation_gallery_review/README.md`.
**Status note**: `write_differential_evidence(...)` consumes the transition and successor-decision TSVs, filters ready source/successor transitions, and extracts both XICs in one RAW open per sample. It writes the paired queue, per-transition `xic_curve_bundle` JSON with the exact precomputed Gaussian15 series and transition review metadata, lossless trace JSON, and `cid_nl_differential_overlay_review_summary.tsv/json` beneath the requested output directory. XIC Inspector reads the bundle offline for paired source/successor curves, shared absolute scale, centre-RT markers, decision colours, max scatter, and gate/identity notes. The artifacts remain `diagnostic_only`: they can support feature-inclusion review, while replacement, merge, deduplication, Matrix writes, and product adoption retain their separate owners and gates.

---

---

### `tier2_raw_trace_reread_producer.py`

**Purpose**: Produce paired `diagnostic_only` Tier 2 RAW trace evidence and RAW manifest sidecars for retained provisional backfill candidates.
**Topic group**: `tier2_raw_trace_reread_producer.py` + `xic_extractor/alignment/tier2_trace_producer.py` + `xic_extractor/alignment/production_candidate_gate.py`
**Originating spec/plan**: `retired-provenance:443a0af82ac4`; follows the sidecar provenance gate checkpoint in `retired-provenance:d3d707f162f6`; diagnostic criteria review in `retired-provenance:38373345f4da` and `retired-provenance:e509e2788bf2`.
**Status note**: Writes diagnostic-only Tier 2 RAW trace evidence and RAW manifest sidecars. The criteria expose scan availability, signal/noise, shape, boundary-reference, apex-span, and neighbor-interference context, but do not provide positive Tier 2 support or change `alignment_matrix.tsv`. FULL COHORT is out of scope until a reviewed follow-up plan is approved after a successful 8RAW diagnostic rerun.

---

## Peak / Candidate Audits

---

---

### `selected_envelope_review_queue.py`

**Purpose**: Build `diagnostic_only` selected-envelope changed-row and boundary-oracle review queue artifacts from `selected_envelope_diagnostics.tsv`.
**Topic group**: `selected_envelope_review_queue.py` + `xic_extractor/peak_detection/selected_envelope_*`
**Originating owner/plan**: `docs/product/quantitation-context.md`; retired selected full-envelope boundary spec provenance `retired-provenance:13e9499ab37d`
**Status note**: Writes `selected_envelope_changed_rows.tsv`, `selected_envelope_oracle_review_queue.tsv`, `selected_envelope_diagnostic_manifest.tsv`, and `selected_envelope_review_queue.json` under an explicit output directory. This tool only packages the audit sidecar for manual/expert boundary review; it does not run RAW files, mutate selected `IntegrationResult`, change targeted workbook/CSV `Area`, or authorize FE4/8RAW by itself.

---

---

---

## Targeted Benchmarks & Reviews

### `targeted_istd_benchmark.py`

**Purpose**: Run strict targeted ISTD benchmark for untargeted alignment.
**Topic group**: `targeted_istd_benchmark.py` + `_loaders`, `_matching`, `_models`, `_stats`, `_summary`, `_writers` (7 files; largest group)
**Originating spec**: `retired-provenance:53a763b9ec7a`
**Recent doc**: `retired-provenance:5162156a9766`
**Status note**: Reads targeted workbook inputs plus existing alignment review/cell/public-matrix artifacts and writes benchmark summary, match, JSON, and Markdown outputs. Public `alignment_matrix.tsv` loading supports both legacy `feature_family_id` matrices and clean `Mz` / `RT` / sample-column matrices with `alignment_matrix_identity.tsv` provenance; matrix sample-column normalization is computed once per matrix read and reused across row expansion. This benchmark remains validation-only context and does not mutate alignment artifacts, workbook schemas, matrix identity, or production decisions.

---

---

---

---

---

---

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

### `pt_pattern_candidate_assembly_replay.py`

**Purpose**: Replay inclusion-list rows whose four list-relative m/z pools exist
against the canonical local MS1 feature table, then identify the first failed
mass, quality, RT, or matcher-emission condition.
**Topic group**: `pt_pattern_candidate_assembly_replay.py` +
`xic_extractor.discovery.pt_pattern_candidate_assembly_replay`
**Product owner**: `docs/product/discovery.md`
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

### `pt_pattern_conflict_graph_replay.py`

**Purpose**: Build a provenance-bound pairwise shared-claim graph over frozen Pt
candidate rows, then compare connected-component-wide `at_most_one` semantics
with the pairwise incompatibility structure represented by those rows.
**Topic group**: `pt_pattern_conflict_graph_replay.py` +
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

### `pt_pattern_ddms2_support_replay.py`

**Purpose**: Replay Fullscan-primary Pt pattern seeds against a DDMS2 local MS1
feature table to inspect whether the DDMS2 run contains matching base/+1/+2/+4
support near the Fullscan seed.
**Topic group**: `pt_pattern_ddms2_support_replay.py` +
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
