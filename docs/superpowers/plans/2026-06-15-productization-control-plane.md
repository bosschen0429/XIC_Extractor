# XIC Extractor productization control plane

Doc placement: formal_repo_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/superpowers/plans/2026-06-15-productization-control-plane.md
Doc exit rule: Keep current; update directly when productization tier, active lane, authority, or promotion gates change.

日期: 2026-06-15
狀態: living plan / maintenance checklist
目前 readiness: `diagnostic_only` for this control document
主要依據: [productization topic map](../../product/productization.md)
歷史盤點: summarized in this control plane; original branch inventory is retired private-history context
產品化狀態 anchor: [cc-framework-improvements productization status](../productization/status/cc-framework-improvements-productization.md)
可重用背景研究: stable takeaways absorbed into
[Untargeted Method](../../product/untargeted-method.md) and product topic pages

## Authority

這份 control plane 擁有產品化 tier、active lane、WIP limit、promotion
packet 的權威。白話 handoff 只負責讓下一個 agent/session 快速接手，不可
用「比較新」覆蓋本文件的 tier 判斷。若文件衝突，先停止並同步:

1. `git status` / `git diff` 決定目前工作樹實況。
2. 本文件決定 maturity tier、active lane、WIP owner、promotion gate。
3. product docs、named plans、schemas 決定 schema、CLI/config/output 行為契約。
4. validation notes 決定 RAW/benchmark evidence。
5. handoff 只做白話摘要、接手順序、下一步建議。

Global skills such as `handoff` / `worktree-report` can remind agents how to
write a handoff, but they are local environment helpers. The tracked handoff
contract is branch-scoped: productization status anchors stay separate from
branch/PR current handoffs. XIC's enforceable handoff contract is the
version-controlled combination of this document,
`docs/agent/obsidian-handoff-contract.md`,
`docs/superpowers/handoffs/README.md`, `docs/agent/codex-operating-system.md`,
`.codex/hooks/*`, and hook fixtures.

## Purpose

這份文件是日常產品化控制板。它解決三個反覆發生的問題:

1. 把 `diagnostic_only`、`shadow_ready`、`production_candidate` 誤以為已經正式推到產品。
2. 每次產品化只推進一小片，沒有形成使用者可重跑、可審閱、可稽核的完整 workflow。
3. 同時開太多 spec/diagnostic，最後都卡在「有產物，但不敢當正式產品」。

本文件不是 behavior spec，也不是 implementation plan。它是每次開工、收尾、review、規劃下一步時要更新的控制台。

## Hook enforcement

這份控制台不是只靠人記得。Repo-local hooks 會在三個位置提醒:

- `UserPromptSubmit`: prompt 提到產品化、promotion、正式功能、maturity tier、`method_manifest`、`review_roundtrip` 等詞時，自動把本文件拉回上下文。
- `PreToolUse`: write-like 工具準備修改 product/public surface 時，提醒必須更新本文件，或明確說明沒有 maturity tier 變動。
- `PostToolUse`: patch/write 類工具執行後，若工作樹已有 product/public surface diff 但本文件沒有變更，提醒 closeout 前補 board/log 或明確記錄 no-tier-change；若 product/control-plane 狀態可能變了但 branch-scoped handoff 沒有同步，也提醒 closeout 前解析正確分支 handoff 或明確記錄不需要 handoff 更新。

Hooks 是 guardrails，不是語意裁判。它們負責把你和 agent 拉回控制台；
真正的 tier 判斷仍要寫在 promotion packet、spec closeout 或 maintenance
log 裡。Project-local hooks 也需要 Codex runtime trust；如果 hooks 沒有被
trust 或沒有載入，本文件和 handoff checklist 仍是人工 closeout gate。

## Current medium-term active lanes

這次 goal 已擴大到中期可收斂項目，但仍維持 WIP limit。已完成的
`method_manifest_v1`、`targeted_schema_versioning_v1`、ReviewAction audited
apply copy、以及 sample metadata injection-order parity 不再佔 active lane。
Backfill standard-path activation 目前分成多個 release tier：72-row
high-signal-clean scoped writer、42-row low-scan-clean scoped writer、57-row
low-height-clean scoped writer、69-row low-height-low-scan-clean scoped
writer、以及 220-row low-height reintegration-stable scoped writer 都已用
explicit opt-in scope audit
filter 寫出 product matrix-only output，且各自的
`narrow_product_writer_expected_diff_acceptance.json` 通過並標
`readiness_tier=production_ready`。Low-height promotion 的前提不是降低
absolute height hard gate；舊的 full-trace heldout oracle 只有 19/20 pass，
`FAM008651/TumorBC2312_DNA` boundary error `1.16445 min` 超過 accepted
`0.1 min` ceiling。後續 diagnostic `expected_window_bounded` observed
reintegration mode 在既有 85RAW trace no-RAW replay 顯示 `padding=0.5 min`
可讓 selected 20/20 通過，最大 boundary error `0.0857986 min`、最大 area
relative error `0.0564106`；再接 explicit
`--low-height-clean-activation-scope-audit-tsv` writer，57/57 expected-diff
通過，duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank
皆為 0。因此 low-height 只可宣稱 explicit 57-row scoped writer ready，不可
外推到 broad 4613-row 或 default behavior。
Low-height-low-scan promotion 是低高度與低 scan 的交集，不是 broadening by
height alone：`standard_low_height_low_scan_clean_trace` 要求
height <2e6、scan 7-9，且 shape/local-global/width/apex 都維持 clean。
既有 85RAW trace no-RAW bounded oracle 有 210 eligible rows / 51 families，
selected 20/20 pass，最大 boundary error `4.80376e-05 min`、最大 area
relative error `0.00881912`；再接
`--low-height-low-scan-clean-activation-scope-audit-tsv` writer，69/69
expected-diff 通過且 duplicate/missing/unexpected/non-eligible/non-written/
unchanged/blank 皆為 0。因此只能宣稱 explicit 69-row scoped writer ready。
擦邊的 apex-delta clean 也只到 `production_candidate`：heldout trace oracle
有 78 candidates / 27 families，selected 20 cases 只有 17/20 pass，最大
boundary error `2.19621 min`，所以也不得新增 apex-delta writer。
Width-only clean 也只到 `production_candidate`：heldout trace oracle 有
4 candidates / 3 families，selected 3 cases 只有 1/3 pass，最大 boundary
error `1.86561 min`、最大 area relative error `0.599229`，所以也不得新增
width-only writer。
Shape-margin clean 也只到 `production_candidate`：這個 probe 把 shape
similarity 降成 `0.93 <= shape < 0.95`，其他條件仍要乾淨；85RAW no-RAW
heldout trace oracle 有 18 candidates / 8 families，selected 8 cases 只有
6/8 pass，summary 最大 area relative error `0.198393`，所以也不得新增
shape-margin writer。
Broad 4613-row auto-write is now `parked` by
`backfill_broad_autowrite_feasibility_gate_v1`: 4613 is the candidate/audit
universe, not a writer pool. The durable next product asset is authority,
mechanical adjudication, structured review, truth acquisition, or trace-evidence
recovery. Do not open another broad Backfill scoped writer or diagnostic unless
a new independent truth source is named and a later goal explicitly adds
masked/product-writer oracle plus expected-diff.
新的 boundary-stability / reintegration-agreement diagnostic 已補上第一個
broader evidence class：`standard_peak_reintegration_stability_audit.py` 對同一
stored trace 做 full-trace 與 expected-window-bounded 兩種再積分，兩者都必須
在 `0.1 min / 10% area` 內吻合才算 eligible。既有 85RAW no-RAW consolidated
scope 實跑得到 299 eligible written rows、3227 ineligible、1087 missing
evidence，其中 271 eligible rows 不在四個既有 ready scoped writer envelope
內。This remains historical candidate evidence only. It does not reopen broad
Backfill auto-write after the 2026-06-18 park decision and must not be used as a
new writer predicate.
後續 low-height reintegration-stable promotion 把這個 candidate pool 的一個
可驗證子集合推到 ready：直接把 299 個 stability eligible rows 全部寫入仍被
formal all-stability family oracle 擋住。該 oracle
`standard_reintegration_stable_candidate_family_trace` 記錄 299 個
audit-intersection rows / 77 families，從同 family 的 1694 個 detected trace
candidates 選 20 個代表案例，結果 19/20；失敗的
`FAM000949/NormalBC2261_DNA` area relative error 是 `0.19621`，超過 accepted
10% ceiling。可放行的是 stability eligible、
activation row 仍 written、且 `cell_height <2e6` 的 220 rows / 66 families。
Formal no-RAW 85RAW heldout family oracle
`standard_low_height_reintegration_stable_candidate_family_trace` is explicitly
family-level, not a row-identity oracle: the summary records
`candidate_family_scope_match_level=family_id`,
`candidate_family_scope_oracle_basis=detected_trace_rows_from_candidate_families`,
220 audit-intersection rows, 66 families, and 1520 detected trace candidates
from those families. It selected 20 family cases and passed 20/20，最大
boundary error `0.0830019 min`、最大 area relative error `0.0725986`；再接
explicit
`--low-height-reintegration-stable-activation-scope-audit-tsv` +
`--reintegration-stability-audit-tsv` writer，220/220 expected-diff 通過且
duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank 全為 0。
這個 fifth ready slice 新增 199 個不在前四個 writer scope 的 ready cells，
目前五個 ready scope 的 cell-level union 是 439 cells。
This is a release-safety boundary and a historical evidence trail, not a
license to keep broadening Backfill writer scope. The product direction is now
mechanical adjudication plus structured approval coverage, with automatic matrix
writes allowed only for explicitly authorized, expected-diff-passing scopes. The
72-row high-signal, 42-row low-scan, 57-row low-height, 69-row
low-height-low-scan, and 220-row low-height reintegration-stable slices remain
demonstrators for approved evidence classes, not templates for mining broader
writer predicates.
Shape-clean reintegration-stable is now a cleaner candidate evidence class, but
not a writer promotion. Formal target
`standard_shape_clean_reintegration_stable_candidate_family_trace` requires
reintegration-stability eligibility and activation-scope shape similarity
`>=0.95`; the no-RAW 85RAW family-level oracle under
`heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/`
records 104 audit-intersection rows / 33 families, 334 available detected trace
candidates / 31 families, selected 20/20 pass, max boundary error
`0.0830019 min`, and max area relative error `0.0725986`. A temporary writer
probe was intentionally not retained because it found `matrix_cells_written=0`
and `unchanged_delta_row_count=104`: those rows already have baseline matrix
values, so this evidence can explain policy decisions but cannot claim a new
`production_ready` writer until it targets actual missing cells or another
explicit product contract. The generated policy engine now records this class in
`backfill_policy_candidate_evidence_class` only: the prior shape-clean
explanation replay, before the row-specific observed-oracle bridge, had
439 `write_ready`, 72 `detected_flagged`, and 4102 `blocked`, with 439/439
writer expected-diff pass; 104 rows carried `shape_clean_reintegration_stable`
candidate evidence. At that point, by authority/decision, 30 of those rows were
`review_only` / `detected_flagged`, while 74 were already `writer_approved` /
`write_ready` through existing approved evidence classes.
The Backfill writer path now has a generated policy-engine entry point:
`--backfill-policy-source-audit-tsv` consumes a broad activation scope audit,
optionally joins reintegration-stability evidence, writes
`standard_peak_backfill_policy.tsv`, and classifies every supplied candidate as
`write_ready`, `detected_flagged`, or `blocked`. The writer only replays
generated `write_ready` rows through the existing matrix-only activation and
expected-diff gate. This removes the manual-TSV/white-list failure mode for
already-approved evidence classes; it is not a broadening mechanism and must not
grant authority to new predicates without a later authority manifest update,
independent truth basis, and expected-diff contract. The first generated-policy replay over the
existing 4613-row consolidated source audit passed with 439 generated
`write_ready` rows, 72 `detected_flagged`, and 4102 `blocked`; the product
writer wrote exactly the 439 then-approved evidence rows and
`backfill_policy_write_ready_rows` expected-diff passed with zero blockers. The
latest observed-oracle replay extends that writer surface to 511 `write_ready`
rows and 0 `detected_flagged`, described below. The generated policy schema is
now
`standard_peak_backfill_policy_v2`: every row carries decision-basis,
candidate-evidence, blocker, and next-evidence fields. The generated policy
explanation replay had `0` rows missing explanation; after the observed-oracle
bridge, next-evidence counts are 511 already approved, 1087 needing trace
overlay or reintegration evidence, and 3015 needing a new approved evidence
class or passing oracle.
The same generated policy path now also accepts a machine-generated paired
`--policy-observed-oracle-tsv` / `--policy-observed-oracle-summary-json`
evidence packet. This is not a manual allowlist: the oracle TSV must use
`standard_peak_policy_observed_oracle_v1`, the summary must bind the oracle TSV
SHA, source activation-scope audit SHA, and base generated-policy SHA, row SHA
must match the source audit row, the oracle must be `pass` and
`included_in_product_acceptance=TRUE`, and the source row must still be written
and trace matched. The current no-RAW 85RAW oracle under
`policy_observed_oracle_detected_flagged_full_trace/` evaluated the 72 previous
`detected_flagged` rows by full stored-trace reintegration and passed 72/72
within `0.1 min / 10% area` (max boundary error `8.91875e-05 min`, max area
relative error `0.098218`). The follow-up generated-policy replay under
`generated_policy_policy_observed_oracle_no_raw_productization/` classified the
4613 source-audit rows as 511 `write_ready`, 0 `detected_flagged`, and 4102
`blocked`, wrote exactly 511 matrix cells, and
`backfill_policy_write_ready_rows` expected-diff passed 511/511 with zero
duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank blockers.
This extends `production_ready` to the current approved evidence classes plus
`policy_observed_full_trace_reintegration`; it still does not claim broad
4613-row `production_ready`.
The latest generated-policy replay now also writes an explanation-only quality
sidecar,
`standard_peak_backfill_policy_quality_explanations.tsv`, under
`generated_policy_quality_explained_no_raw_productization/`. This sidecar keeps
the generated policy rows unchanged and does not add writer authority; it only
records source-audit quality blockers for human/debug review. The replay kept
the same product decision surface: 4613 policy rows, 511 `write_ready`, 0
`detected_flagged`, 4102 `blocked`, 511 matrix writes, and 511/511
expected-diff pass. The policy summary now records
`backfill_policy_quality_explanation_row_count=4613`, matching the sidecar TSV.
Repository retention records this as a compact tracked summary at
`docs/superpowers/validation/backfill_generated_policy_externalized_artifacts_v1/backfill_generated_policy_externalized_artifacts_summary.json`;
the full generated policy TSV, quality-explanation TSV, and expected-diff
acceptance JSON remain externalized under ignored `output/`, so this retention
change does not alter maturity tier, active lane, writer authority, selected
peak/area, counted detections, workbook, or matrix behavior.
The most common blocked bucket remains the 1087 rows with missing overlay path,
and the remaining blocked rows mainly carry combined shape/height/width/scan/
apex-delta blockers or still need a new approved evidence class/passing oracle.
The retired private Backfill production-gate research input reviewed on
2026-06-17 reinforces that `height >= 2e6` is only a high-signal demonstrator /
rollout guardrail, not a product hard gate. Low-height `19/20 pass + 1 boundary
fail` should be treated as boundary/reintegration risk evidence. Public
takeaways live in `docs/product/backfill.md` and product method/topic pages;
the private research input is not a source-of-truth repo path.
The 2026-06-18 Backfill strategy reset notes, summarized here and in
`docs/product/backfill.md`,
supersedes any next step that would choose another writer slice directly from
`quality_blockers`. The Backfill north star is mechanical adjudication of all
4613 candidates, not claiming all 4613 as writable. Current writer authority
stays at 511 approved cells. The 3015 dirty-but-trace-matched rows are now a
truth/review/adjudication target, not an auto-write pool, and the 1087
`missing_overlay_path` rows stay blocked until trace evidence exists. The
read-only
`backfill_broad_autowrite_feasibility_gate_v1` packet now closes this branch as
`park_broad_backfill`: existing artifacts do not support a short defensible
broad auto-write gate, and ISTD evidence cannot prove analyte peak-choice or
area truth. No broader ProductWriter authority should be implemented, and no
new broad Backfill sidecars/diagnostics should be added unless a genuinely new
independent truth source is named. The current next asset is
`productization_authority_manifest_v1` plus
`mechanical_adjudication_schema_v1` / `mechanical_adjudication_index_v1`: a
fail-closed authority and classification layer that makes all 4613 rows
machine-adjudicated without granting new writer authority.
The next checkpoint adds Review Packet / Approval Workflow v1 as a structured
human-review asset, not ProductWriter authority: the 3015 trace-matched
unresolved rows become review packets, the 1087 missing-overlay rows remain
evidence-recovery work, and reviewer approval can only write a decision log.
Peak-Choice Truth Set / Lockbox v1 then adds the first independent truth-label
acquisition contract: 72 deterministic cases across approved controls,
unresolved review rows, missing-overlay evidence gaps, failed-oracle negatives,
and manual wrong-peak/no-peak fixtures. It is `production_candidate` as a
non-mutating truth/review asset only; no labels have been collected, agreement
metrics are null, and lockbox membership cannot grant ProductWriter authority.
Lockbox Label Collection Pack v1 now materializes that lockbox as a
human-labelable package: 72 Markdown review packets, a 144-row empty label
template with two reviewer slots per case, a strict label schema, a validator,
and a plain-language labeling README. This still does not create truth labels
or write authority. Blank template rows are not labels; future reviewer labels
remain evidence inputs until a later import/summary gate and separate
expected-diff authority update say otherwise.
Lockbox Static Review UX v1 now adds a human-readable HTML bundle on top of the
same label package: 72 case pages, 53 Gaussian15-smoothed review plots with
Gaussian-derived review boundaries, 18 explicit missing-evidence pages, and 1
trace-present boundary-unavailable page. Gaussian15 is a review/morphology view
only; it is not a ProductWriter input, matrix area, selected peak switch,
counted-detection decision, or automatic truth label. The teal shaded review
window is Gaussian-derived; older candidate/raw boundaries are reference lines
only.
Lockbox Next-Action Plan v1 now splits the first one-reviewer import into
actionable, non-authoritative buckets: 53 plotted Gaussian15 cases are ready for
second independent review; 6 manual wrong-peak/no-peak cases are existing
negative controls, not missing-evidence unknowns; 12 failed round-trip oracle
negative cases stay parked because the oracle is not independent peak-choice or
area truth; and 1 Gaussian boundary-unavailable case needs signal/evidence
recovery or remains not assessable. This split narrows the next human step
without granting ProductWriter, matrix, workbook, selected-peak, selected-area,
counted-detection, GUI, or broad Backfill authority.
Lockbox Shadow Scoring Contract Adapter v1 turns that split plus the
single-owner/AI-challenge closure into a 72-case shadow-only contract manifest:
53 owner-clean Gaussian15 cases are non-authoritative accept challenges, 6
manual wrong-peak/no-peak controls are reject hard stops, and 13 excluded rows
remain `not_scored`. The adapter records shadow/truth/doublet/source-hash and
manifest-sha contract fields plus the Gaussian15 boundary policy explicitly:
Gaussian-smoothed boundaries are the review basis; raw-trace doublets are
acceptable only when the Backfill/detect reference is on the left peak, and
unclear or right-peak reference cases stay flagged. This is still
`production_candidate` evidence/control infrastructure only. Phase 2
`ProductionAcceptanceManifest v1` is now defined as an additive schema/checker
precursor; it is not a scorer run and writes no ProductWriter input, matrix,
workbook, selected peak/area, counted detection, reviewer slot2, GUI, default
extraction, or broad Backfill authority. Phase 3 `QuantMatrixVersion Activation`
is now implemented as an explicit manifest-driven activation surface with
expected-diff, `cell_provenance`, and `row_summary`; it still does not wire
ProductWriter default extraction, workbooks, GUI, selected peak/area, counted
detection, reviewer slot2, or broad Backfill authority. Phase 4
`Gallery/Report Alignment` is now implemented as a review-only report surface
over `QuantMatrixVersion` outputs and the validated manifest; it exposes
accepted Backfill versus detected cells, prevalence risk, manual-negative and
doublet closure, source paths/hashes, manifest sha, and the Gaussian-smoothed
trace-primary/raw-trace-auxiliary convention without granting ProductWriter,
matrix, workbook, GUI, selected peak/area, counted detection, reviewer slot2,
or broad Backfill authority. Phase 5 `Validation/Promotion Readiness` is now
implemented as a read-only promotion checker: it separates contract correctness
from scientific confidence, writes readiness summary/check sidecars, and cannot
claim `production_ready` from focused tests, 8RAW smoke evidence, or unbound
tier/status strings alone. Required science pass rows must bind artifact relpath,
SHA, and tier-specific provenance such as cohort/run, oracle packet, or
downstream scope. `QuantMatrix Promotion Validation Packet v1` now materializes
the first no-RAW evidence packet from existing artifacts: large-cohort and
heldout-oracle evidence are artifact-bound and pass, while downstream-impact
evidence is explicitly missing, so the Phase 5 readiness fixture remains
`contract_ready_science_inconclusive` with
`may_promote_default_quant_matrix=false`. Phase 6 now defines the no-RAW
`QuantMatrix downstream-impact smoke` contract: a downstream artifact must
prove that a real `QuantMatrixVersion` bundle improves numeric matrix coverage
while preserving detected-only claims through sidecars; contract fixtures and
tier strings cannot satisfy promotion. Phase 7 assembled the current real
511-cell `QuantMatrixVersion` bundle from
`seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`
and confirmed contract correctness plus downstream-impact smoke, while keeping
readiness science-inconclusive until evidence was rebound. Phase 8 now binds
that real downstream-impact artifact together with the existing large-cohort
and heldout-oracle artifacts into `QuantMatrix Promotion Packet v2`; real-bundle
readiness is `production_ready_candidate_packet` with `production_ready=true`
and `may_promote_default_quant_matrix=true`. At that point it was a candidate
promotion packet only: ProductWriter default extraction, default matrix
authority, workbook/GUI behavior, selected peak/area, counted detection, and
broad Backfill authority remained unchanged until the later expected-diff
activation gate described below.
The default matrix authority phrase here is retained as a productization-state
anchor for the Phase 8 boundary, as is the later expected-diff activation gate
phrase.
Phase 9 now adds that no-RAW default activation dry-run expected-diff gate:
manifest-driven activation is rerun in a temporary directory and matches the
Phase 7 reference `quant_matrix`, `cell_provenance`, `row_summary`, and
`expected_diff_summary` hashes exactly, with 511 expected, 511 written, and 0
unused expected-diff rows. The gate allows Product Ready closeout review to
proceed, but it still writes no default matrix files and changes no
ProductWriter/default behavior.
Phase 10 closes that evidence chain as
`product_ready_default_matrix_candidate`: Phase 8 and Phase 9 both validate,
the closeout checklist has six passing checks, and the activation contract is
ready for a separate explicit ProductWriter activation commit. This does not
itself change default outputs, workbook/GUI, selected peak/area, counted
detection, current 511-cell authority, or broad Backfill status.
Phase 11 now performs that explicit ProductWriter/default output activation:
the default output `quant_matrix.tsv` is written from the current
ProductionAcceptanceManifest plus expected-diff contract and contains detected
values plus 511 accepted Backfill quantification values. The activated product
surface also writes `cell_provenance`, `row_summary`, `expected_diff_summary`,
and `source_summary`; the summary is
`product_ready_default_matrix_activated`, with 17489 detected cells, 511
accepted Backfill cells, 18000 quant-available cells, and zero unused
expected-diff rows. This activates the current 511-cell default matrix output
only; it does not change workbook/GUI behavior, selected peak, selected area,
counted detection, review/replay behavior, broad Backfill status, or the
registered product authority scope.
The 2026-06-21 Backfill expansion follow-up now records a 666-cell candidate
replay packet only. `backfill_expansion_default_product_activation_v1` reuses
the ProductionAcceptanceManifest + expected-diff + QuantMatrixVersion writer
path as an externalized dry-run replay, but it is not a public default
activation and does not register a ProductWriter scope. The full-chain checker
now joins expected diff, sample-local evidence, RAW trace identity,
shift-aware standard-peak support, own-max metric support, and the MS1
product-authority sidecar by stable row/cell keys; current result is only
374/666 full-chain pass and 292 held. Therefore all 666 replayed cells and the
263 earlier-held cells stay outside public authority until a later gate reaches
666/666 and re-evaluates expected diff.
The 2026-06-22 clean-target selective follow-up activates only the 84 cells
whose peak-mode cleanup, selective source-family support, own-max evidence,
expected diff, and provenance all pass. The new registered scope is
`backfill_expansion_clean_target_selective_activation_84_cells`; the 28
projected-held clean-target cells, 37 boundary-review cells, and 29 off-target
hold/remap cells remain outside authority. This does not unpark broad Backfill,
does not rewrite workbooks/GUI/selected peak/selected area/counted detection,
and does not change the old 511 Backfill or 95 CID-NL writer scopes.
The current no-RAW productization runner is
`python -m scripts.run_backfill_expansion_full_evidence_chain
--reuse-existing-raw-overlay --reuse-existing-shift-aware`; it replays the
evidence chain through the clean-target selective activation and reports the
same 84-cell active scope. This runner is not hidden behind
`scripts/run_discovery.py`. The same rule is now also exposed through the normal
alignment preset surface as `--preset dna_dr_product_ready`, which runs base
alignment, standard-peak publication, then the bounded Backfill expansion
clean-target selective activation under the current alignment output directory.
This public CLI surface does not create a new tier, active lane, authority
scope, workbook/GUI behavior, selected-peak/area behavior, or counted-detection
change. The current GUI remains targeted-extraction-only, so no GUI alignment
preset surface is claimed by this update.
The `d4-N6-2HE-dA` target-row identity stop-ship is now traced to discovery
MS2 seed extraction, not ProductWriter or Backfill: Thermo MS2 filter precursor
can be an isolation/trigger mass, so CID-NL discovery must allow
product-plus-neutral-loss inferred precursor seeds within
`ms2_precursor_tol_da`. The discovery path now emits inferred
`300.160x / 184.113` seeds without deleting valid `301.165 / 185.116` isotope
seeds, and discovery `candidate_id` includes precursor/product row identity to
avoid same-scan candidate collisions. This fixes the generation path only; the
already activated default matrix bundle remains stale for this target until a
later explicit discovery/alignment/default-activation expected-diff rerun.
Missing-Overlay Evidence Recovery v1 now links the 1087
`missing_overlay_path` rows back to existing family-level trace/overlay
artifacts and sample-level trace fields across 114 families. This moves the
evidence explanation from "artifact link missing" to "trace recovered but still
needs review/truth/reintegration decision"; it does not grant write authority.
Goal 5 machine status lane ids are tracked in
`docs/superpowers/validation/productization_status_index_v1.tsv`:
`backfill_current_write_ready_scope`, `cid_nl_default_product_activation_v1`,
`backfill_expansion_default_product_activation_v1`, `broad_backfill_autowrite`,
`backfill_expansion_clean_target_selective_product_activation_v1`,
`productization_authority_firewall_v1`, `mechanical_adjudication_contract_v1`,
`review_packet_workflow_v1`, `peak_choice_truth_lockbox_v1`,
`missing_overlay_evidence_recovery_v1`, `quality_explanation_sidecar_v1`,
`targeted_ms1_shape_identity_limited_rescue_v1`,
`targeted_ms1_shape_identity_broader_targets`,
`sample_metadata_order_projection_v1`, `sample_metadata_role_value_behavior`,
`review_action_candidate_sidecar_v1`, `review_action_selected_candidate_switch`,
`review_action_manual_boundary_area_writer`,
`calibration_normalization_activation`, and `gui_replay_parity`.
Goal 6 bounded non-broad acceptance is tracked in
`docs/superpowers/validation/bounded_non_broad_lane_acceptance_v1.tsv` and
checked by `scripts/check_bounded_product_lanes.py`. It does not add product
authority; it only locks the current non-broad lane boundaries: Targeted MS1
limited rescue may stay within `limited_5hmdc_5medc_v1` / `5-hmdC + 5-medC` /
`detected_flagged`, SampleMetadata may stay no-output/order-projection only,
and ReviewAction candidate sidecar may verify identity only. Broader Targeted
MS1 targets, role-driven value behavior, selected-candidate switch, and
manual-boundary area writer remain blocked or parked until a separate product
contract plus expected-diff gate exists.
Targeted MS1 shape identity limited rescue 也已收斂成窄範圍
`production_ready`：headless explicit support-TSV workflow、headless
auto-limited CLI、以及 canonical no-flag normal CLI default 都可用，但都只限
`limited_5hmdc_5medc_v1`、`5-hmdC + 5-medC`、且產品輸出只能變成
`detected_flagged`。GUI wiring、以及其他 target 仍不在這個 ready claim 內。
ReviewAction selected candidate / manual boundary writer 已 parked for current
release claim；產品方向仍是減少人工審查，之後要用 stable IDs、
expected-diff、audit gate 重新開 lane，而不是要求使用者審完所有案例。
sample metadata cross-module
parity 的 no-output resolver slices 已收斂到 `production_ready` for
order projection only：extraction、instrument-QC、alignment、RT-normalization
anchor diagnostic 都可用同一個 `sample_metadata_v1` resolver 或 sidecar；
sample roles / blank / QC / batch / matrix / exclusion 仍 `blocked` for any
value-changing behavior，不可直接改 quant、counted detection、normalized value
或 main matrix。

| Slot | Lane | Owner | Allowed work | Stop rule |
|---|---|---|---|---|
| Primary | `pt_pattern_discovery_diagnostic_v1_closeout` | active on branch `codex/pt-pattern-discovery`; `xic_extractor.discovery` owns matcher/evidence contracts and diagnostic CLIs only orchestrate | close a trustworthy `diagnostic_only` v1: preserve candidate, same-scan, trace, review, conflict, and provenance observability while stating which surfaces are proxies/scaffolds; defer complete assignment generation, canonical `PeakHypothesis`, selection, quantitation, and product projection | stop if work promotes historical reviewed labels or threshold resubstitution, treats machine calls as detections, launches new RAW, tunes thresholds, or grants Alignment/Backfill/Matrix/count/export authority |
| Maintenance | `backfill_standard_seed_guard_scope_v1` | none; 72-row high-signal, 42-row low-scan, 57-row low-height, 69-row low-height-low-scan, and 220-row low-height reintegration-stable narrow writer ready slices done; generated policy replay is now ready for current approved evidence classes plus 72 row-specific observed-oracle rows, with 511/511 expected-diff pass and 0 remaining `detected_flagged`; broad Backfill auto-write is parked by `backfill_broad_autowrite_feasibility_gate_v1`; apex-delta, width-only, and shape-margin probes are candidate only; all-stability remains blocked by 19/20 formal oracle; shape-clean reintegration-stable is `production_candidate` evidence only because its oracle passed but the writer probe found 0 new writes / 104 unchanged pre-existing values | maintain existing explicit scoped writer contracts and current 511-cell authority only; route blocked rows through authority/adjudication, structured review, truth, or evidence-recovery assets; no more broad Backfill sidecars/diagnostics unless a new independent truth source is named | stop if the next step would silently broaden matrix writes, derive predicates from `quality_blockers`, use round-trip reintegration as peak-choice truth, revive all-stability/apex-delta/width-only/shape-margin under a new name, include `missing_overlay_path` rows without trace evidence, or create another broad diagnostic backlog |
| Supporting | `sample_metadata_cross_module_parity_v1` | none; no-output order projection is `production_ready`; role/value behavior remains `blocked` | release smoke/docs only; no further role/value behavior without expected-diff | stop if sample role changes extraction output, counted detection, normalized value, or matrix value |
| Parked | `review_action_reintegration_v1` | parked for this release claim; candidate-sidecar verifier is now `production_candidate` | selected-candidate writer and manual boundary area recompute remain blocked until expected-diff/product apply contracts exist; long-term product direction is low-manual-intervention automation with audit/review sampling | stop if a manual action changes selected peak/area/counting without expected-diff |
| Diagnostic-only | `pt_pattern_scan_proxy_v1` only as supporting evidence for the primary Pt lane | current diagnostic bridge under `tools/diagnostics/pt_pattern_scan_proxy.py` | may produce candidate inputs and review packets for manual inspection, but cannot become the product detector by itself | stop if proxy-derived rows are used as production readiness, ground truth, or Matrix authority without independent validation and an explicit product contract |
| Frozen queue | calibration/normalization activation | none | classification and planning only | unfreeze only after active lanes are promoted, killed, or explicitly parked |

Hard gate: a lane without a `WIP owner`, productization intake packet, and stop
rule must not enter implementation. Hooks only remind; this table decides active
scope.

## Non-negotiable rule

任何功能不得被稱為 `production_surface`，除非同時滿足以下條件:

- 有明確 public surface: CLI flag、GUI control、config key、CSV/TSV/XLSX schema、report contract、或 downstream handoff。
- 有一個 domain authority owner: 例如 `EvidenceVector`、`PeakHypothesis`、`ProductionDecisionSet`、canonical projection adapter，而不是 writer/report 自己重算決策。
- 有 schema 或 output contract: 欄位、enum、sheet order、hidden state、metadata key、或 JSON schema 必須可檢查。
- 有 replay/provenance: 至少能說出 inputs、config/settings、target/sample metadata、runtime/backend、version/hash。
- 有 focused tests 或 validation gate: 不只 synthetic smoke；若會改 selected area、counted detection、primary matrix，必須有 expected-diff。
- 有 rollback/stop rule: 失敗時知道是退回 shadow、保留 diagnostic，還是 kill 掉。

少一項，最高只能叫 `production_candidate`。如果會改 primary matrix 但缺 expected-diff，最高只能叫 `shadow_ready`。

## Maturity labels

| Label | 可宣稱什麼 | 不可宣稱什麼 | Exit rule |
|---|---|---|---|
| `missing` | 需求存在，尚未有穩定實作。 | 不可寫進 user-facing feature list。 | 寫 spec 或明確 kill。 |
| `partial_internal` | 內部模型/helper 存在。 | 不可當 public contract。 | 收斂到 owner/adaptor，或保留內部。 |
| `diagnostic_only` | 可調查、比較、產出 evidence。 | 不可改 product output；不可當 release feature。 | promote to shadow、kill、或外部化為工具。 |
| `shadow_ready` | 可 side-by-side 比較；主輸出不變。 | 不可 silent promotion；不可改 counted detection/primary matrix。 | expected-diff + activation contract。 |
| `production_candidate` | code/tests 接近正式；可以做 gated rollout。 | 不可宣稱 fully production-ready。 | schema/replay/review gate 通過。 |
| `production_surface` | 使用者或下游可依賴的正式 surface。 | 不代表 science readiness 已滿；仍要標驗證範圍。 | 納入 regression/release gate。 |
| `production_ready` | 可作正式 release claim。 | 不可只靠單一小 fixture 或 docs。 | 至少通過指定 fixture、expected-diff、downstream smoke。 |

## Active productization board

每次狀態改變都更新這張表。不要只在聊天裡說「應該已經產品化」。

| Lane | Current tier | Current owner / artifact | Product gap | Next checkpoint | WIP owner |
|---|---:|---|---|---|---|
| Targeted product projection: `Product State`, `Counted Detection`, `Reason` | `production_surface` | `targeted_product_projection.py`, CSV/workbook writers | schema version 已鎖；缺 canonical projection adapter 文檔 | `canonical_detection_contract_v1` | unassigned |
| Targeted MS1 shape identity limited rescue | `production_ready` for headless explicit support-TSV workflow, headless auto-limited CLI, and canonical no-flag normal CLI default; GUI still off | `targeted_ms1_shape_identity_activation_policy=limited_5hmdc_5medc_v1`, `--targeted-ms1-shape-identity-support-tsv`, `--targeted-ms1-shape-identity-auto-limited-default`, `xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer`, `targeted_ms1_shape_identity_auto_diff`, expected-diff gate, 8RAW/85RAW auto artifacts | default no-flag CLI now runs the same support-TSV key-set expected-diff gate when no support TSV is configured；limited policy `limited_5hmdc_5medc_v1` 限 `5-hmdC + 5-medC` 且只能寫 `detected_flagged`；explicit support TSV mode remains available with `explicit_support_tsv`; GUI 仍 out of scope | broaden beyond `5-hmdC + 5-medC` only with new expected-diff/RAW evidence; GUI wiring waits for main GUI reconnect | none for headless limited workflows |
| Targeted output schema versioning | `production_surface` | `output/schema.py`, manifest `output_schema`, workbook `Run Metadata` | CSV 欄位形狀未改；version 目前透過 manifest/metadata 暴露，不是每列 CSV 欄位 | schema snapshot / downstream handoff profile | none; slice done |
| `EvidenceVector` / `PeakHypothesis` / `IntegrationResult` spine | `production_candidate` | `peak_detection/hypotheses.py`, result assembly | 缺 stable detection id、typed `ReviewAction`、durable audit transition | `canonical_detection_contract_v1` | unassigned |
| `AuditTrail` | `partial_internal` | `PeakHypothesis.audit` | 不是 user-visible operation history | `review_roundtrip_v1` | unassigned |
| `Review Queue` | `production_surface` as worklist | workbook `Review Queue` sheet | 不能讀回 decision；不能 reintegrate | `review_roundtrip_v1` | unassigned |
| ReviewAction audited apply copy | `production_surface` for audited targeted-long copy; `production_candidate` for candidate-sidecar verification | `xic_extractor.review_actions`, `scripts/apply_review_action_changesets.py`, `scripts/plan_review_action_candidate_sidecars.py`, `review_action_apply_audit_v1`, `review_action_candidate_sidecar_v1` | accept/mark/reject 可寫 audited output copy；`select_candidate` candidate_id 現在可對 `peak_candidates.tsv` 做唯一性/SHA 驗證；selected candidate switch/manual boundary 仍 deferred | selected-candidate writer + manual boundary recompute writer only after expected-diff/product decision | none; audited apply and candidate-sidecar verifier slices done |
| Pt pattern Discovery: `delta_mass(pattern_id=pt)` | `diagnostic_only` for candidate generation, same-scan support, trace/anchor-local evidence, review labels, historically named oracle packets, threshold replay, conflict replay, and diagnostic machine calls | `xic_extractor.discovery.delta_mass_*`, `xic_extractor.discovery.pt_pattern_*`, `scripts/run_pt_pattern_discovery.py`, Pt diagnostics, `docs/product/discovery.md`, and the Pt validation note | The v1 matcher emits one greedy assignment representative per base rather than all compatible assignments. Same-scan evidence is theoretical-window occupancy, not chemical intensity attribution; anchor-local segments are boundary evidence; `trace_rule_call` is a diagnostic proxy, not a final detector call. The first bounded nine-case human slice has 6 accepts, rejects at Cases 06 and 09, and unresolved Case 08; the historical repaired 32-row packet and zero-error threshold fit are stale resubstitution context, not ground truth. No canonical `PeakHypothesis`, selected area, `IntegrationResult`, Alignment/Backfill intake, counted detection, Matrix, workbook, activation, or export authority exists. | Define complete or bounded Top-K assignment generation and bind same-scan/trace evidence to each assignment, then repeat bounded independent review. Product promotion requires a later explicit selection, validation, projection, and rollback contract; historical DDMS2 target-list absence remains a separate evidence limitation. | `codex/pt-pattern-discovery` |
| Manual boundary / reintegration | `parked`; `production_candidate` for deferred changeset and candidate-sidecar verification only | candidate sidecar verifier + action schema + changeset rows | 沒有 area recompute writer；沒有 selected candidate switch writer；candidate sidecar 只驗證 id，不授權改 selected peak/area；需要產品決策確認是否可改 selected peak/area | `review_action_reintegration_v1` product decision | parked |
| `Run Metadata` | `production_surface` as workbook metadata | workbook sheet + manifest/schema reverse reference | 不是 full replay manifest；只反向記錄 targeted output schema 與 manifest schema/path/hash | workbook hash capture / release metadata docs | unassigned |
| `method_manifest.json` | `production_ready` for targeted CLI replay parity | `xic_extractor.output.method_manifest`, `output/method_manifest.json` | 8RAW/85RAW CSV + normalized workbook replay parity passed；artifact policy says CSV exact, workbook normalized compare, manifest provenance-only | full byte-exact workbook replay only if a future release needs it | unassigned |
| Headless targeted CLI | `production_ready` for targeted CLI replay parity | `xic-extractor-cli`, `--replay-manifest`, method manifest invocation context | replay rejects runtime overrides；GUI replay 未接主線 | GUI parity after mainline wiring | unassigned |
| GUI/CLI parity | `partial_internal` | shared `load_config` / `extractor.run` | 缺 fixture-level parity diff | narrow parity smoke | unassigned |
| `injection_order_source` | `production_ready` for order only | settings/config/extraction pipeline | 只處理 order，不是 sample metadata universe；role/value behavior 仍 blocked | `sample_metadata_contract_v1` | unassigned |
| Sample metadata roles | `production_ready` for no-output injection-order projection and instrument-QC manifest projection; `blocked` for role-aware value behavior | `xic_extractor.sample_metadata`, `scripts/validate_sample_metadata.py`, `resolve_injection_order`, `run_alignment --sample-column-injection-order`, `instrument_qc_sample_metadata.tsv`, `analyze_rt_normalization_anchors.py --sample-info` | extraction 可用 `sample_metadata_v1` 當 injection-order source；alignment 可用 `sample_metadata_v1` 排 final matrix sample columns；instrument-QC method-doc manifest 可輸出 `sample_metadata_v1` sidecar；RT-normalization anchor diagnostic 可用 `sample_metadata_v1` 投影 injection order；roles/batch/matrix/exclusion 仍不得改 product values、counted detection 或 normalized values | role-aware QC/blank/batch/matrix/exclusion behavior only with expected-diff gate and product decision | none; cross-module projection slices done |
| Instrument-QC trend sidecar | `production_surface` sidecar | `run_instrument_qc.py`, instrument_qc package, `instrument_qc_sample_metadata.tsv` | 不改 main matrix；sample metadata sidecar 只做 metadata projection | release smoke / downstream docs | none; projection slice done |
| Calibration preview | `shadow_ready` / `diagnostic_only` | instrument-QC calibration preview | 不可寫 main matrix；response transfer blocked | `normalization_calibration_activation_v1` | unassigned |
| Alignment workbook Matrix/Review/Audit | `production_surface` | `alignment_results.xlsx`, `xlsx_writer.py`, `alignment-results-v3` | output-level wording now matches runtime; keep release tests guarding sheet/schema shape | alignment release gate | unassigned |

Pt pattern feature-source update (2026-07-02): the Pt Discovery CLI and
preparation CLI now accept normalized external feature TSVs and MZmine wide
feature table CSVs. Area-only feature-detector rows may seed feature-level Pt
candidate matching by using positive area as the intensity proxy, but those
candidates are labelled `feature_source_detected` with `MEDIUM` / tier `B`
review priority until trace/co-elution evidence evaluates them. A no-RAW smoke
against `output/mature_tools/mzmine_ctdna_probe/ctdna_probe_quant_mzmine.csv`
wrote 227,698 canonical detected-feature rows and 7,557 Pt candidates under
`output/pt_pattern_productization_v1/feature_source_real_smoke/mzmine_ctdna_probe/`;
all trace rule fields remained `not_evaluated`. A follow-up smoke on the
20251210 Fullscan/DDMS2 local RAW pair used MZmine only as the external feature
detector source, then ran Pt matching in this repo. The Fullscan MZmine feature
table produced 31,547 canonical detected features and 951 Pt-only seeds; the
first 50 traced seeds produced 36 shared-rule `accept` / `detected`, 14
`reject` / `non_detected`, and 901 not-evaluated rows when the 50-row trace
summary was joined back to the full candidate set. The DDMS2 MZmine table
produced 24,074 canonical detected features; Pt+CID-NL(dR) composite matching
produced 5 candidates, and RAW trace evidence called 4 `accept` / `detected`
and 1 `reject` / `non_detected`. Blind overlay HTML packets exist under
ignored `output/pt_pattern_productization_v1/feature_source_real_smoke/`.
This does not change the Pt pattern maturity tier and does not grant Matrix,
Backfill, counted-detection, selected-area, workbook, or export authority.
Local MS1 feature table generator outputs are `diagnostic_only` / feature-source
screening evidence. The initial all-centroid adjacent scan-linking path timed
out beyond 1000 seconds on the 20251210 Fullscan RAW. A first seeded redesign
was also rejected after review because intensity-order point assignment could
split one chromatographic trace before lower-intensity bridge points arrived.
The repaired default now uses high-intensity points only to define m/z seed
ranges, then segments each seed range in scan order. With the current lab
default `mass_detection_noise_level=25,000`, the Fullscan smoke under
`output/local_ms1_feature_table_v1/fullscan_seeded_mzmine_like_noise25000/`
completed with `wall_time_sec=11.1932`, 10,779 local feature rows, 386 Pt-only
candidates, and 19 manual-review packet rows. Full trace diagnosis over all 386
candidates produced 340 `pt_pattern_detected_diagnostic`, 43 partial, and 3
review rows; shared-rule replay recorded 211 `accept` / `detected` and 175
`reject` / `non_detected`, with zero `not_evaluated` rows. A blind overlay
gallery for the 19-row manual-review packet now exists under
`blind_overlay_manual_review_packet/`; it hides machine calls in HTML, seals the
machine calls separately, and provides minimal human fill-in XLSX/CSV files.
The 19-row human review was filled with 16 `accepted`, 3 `uncertain`, and 0
`rejected` rows; sealed machine calls were 7 `accept`, 8 `uncertain`, and 4
`reject`. The comparison has 8 exact agreements, 9 rows involving an uncertain
call, and 2 direct disagreements where the human accepted a machine reject. This
showed the previous trace rule was too strict about RT drift. Shift-aware trace
evidence now adds translated-shape metrics while retaining absolute apex/RT
metrics as diagnostics. Re-running all 386 candidates under
`trace_diagnosis_limit386_shiftaware/` produced 384
`pt_pattern_detected_diagnostic`, 1 partial, and 1 review row; replay under
`pt_pattern_with_trace_shiftaware/` produced 358 `accept` / `detected` and 28
`reject` / `non_detected` rows, including 147 shifted-shape accepts. On the same
19 human-reviewed cases, shift-aware replay produced 18 machine `accept` and 1
`reject`: all 16 human-accepted rows are now accepted, while 2 of 3 human
`uncertain` rows are also accepted. Current interpretation: shift-aware shape
fixes the over-strict RT drift failure, but the rule is not product-ready until
deliberately selected false-positive / borderline rejected cases validate that
shifted-shape accepts are not too broad. A 24-row stress packet under
`shiftaware_false_positive_stress_review/` now samples high-shift, low
shifted-similarity, low unshifted-similarity, near-boundary, and close-apex
controls while keeping the HTML/XLSX blind to sealed machine calls. After fixing
the packet schema so shifted summary metrics reach the overlay, sealed machine
calls are 24 `accept`: 18 shifted-shape and 6 close-apex. Human labels were
filled for all 24 rows: 19 `accepted`, 5 `uncertain`, and 0 `rejected`; the five
uncertain rows are window-limited notes rather than explicit false-positive
calls. A follow-up 90-second half-window overlay under
`shiftaware_false_positive_stress_review/uncertain_wide90_review/` now isolates
those five uncertain rows, with sealed machine calls still 5 `accept`, all
shifted-shape; the wide-window HTML preserves the original 24-row case ids
(`Case 02`, `Case 03`, `Case 04`, `Case 07`, `Case 11`) instead of renumbering
the subset 1-5. Human labels for the wide-window follow-up are 3 `accepted`, 2
`uncertain`, and 0 `rejected`; resolved across the full 24-row stress set this
is 22 `accepted`, 2 `uncertain` (`Case 04`, `Case 11`), and 0 `rejected`. This
was historical support for keeping shifted-shape metrics visible, but the later
Pt-isotope semantics update supersedes using shifted shape as an accept path.
All local-generator candidates remained
tier `B` / score `70` feature-source evidence. The summary now exposes
top-level `trace_builder_mode` so seeded-mode metrics are not mistaken for
scan-link bipartite assignment metrics. This does not change Matrix, Backfill,
counted-detection, workbook, export, active lane, maturity tier, or production
readiness authority.
Post-review ambiguity observability adds raw-vs-smoothed and shifted-only review
flags to trace metrics, trace summaries, review packets, sealed machine calls,
and `pt_pattern_evidence.csv`. A 24-row stress refresh under
`shiftaware_false_positive_stress_review/ambiguity_flags_refresh/` produced 16
`review_required` and 8 `clear` rows; the flags catch both remaining uncertain
rows but also many human-accepted shifted-only rows, especially `pt_plus_4`.
Current interpretation: these columns are review-sorting diagnostics for
low-scan-density, raw/smoothed apex mismatch, shifted-only support, and isolated
or mixed position shift. They are not hard accept/reject gates and do not change
the Pt pattern tier, active lane, Matrix, Backfill, counted-detection, workbook,
export, or production-readiness authority.
A focused wide-window recheck then used a 90-second half-window for `Case 04`,
`Case 11`, `Case 22`, `Case 14`, `Case 07`, and `Case 01` under
`shiftaware_false_positive_stress_review/ambiguity_flags_refresh/focused_recheck_case04_11_22_14_07_01_wide90/`.
Human review plus follow-up correction recorded 3 `accepted`, 1 `rejected`, and
2 `uncertain`, but the later isotope interpretation tightened the Pt pattern
diagnostic trace rule: shifted-shape evidence is review-only. The only positive
`trace_rule_call` proxy path is close-apex co-elution for the full
base/+1/+2/+4 pattern:
full diagnostic status, all three non-base positions supported,
`max_abs_apex_delta_sec <= max(0.6 sec, 1.25 * scan_interval_sec)`,
`min_pearson_r_to_base >= 0.90`, and `min_active_overlap_fraction >= 0.50`.
A shifted match with
`min_shifted_pearson_r_to_base >= 0.85` and
`max_abs_shape_shift_sec_to_base <= 45` is reported as
`shifted_shape_review_only`; larger shifted-only matches retain diagnostic
reason `shape_shift_above_threshold`. Neither result is an adjudicated human
reject or product non-detection.
The formal Discovery evidence replay with this scan-aware active rule lives at
`output/pt_pattern_productization_v1/two_raw_pt_only_scanaware_closeapex_20260703/`.
It changes no candidate universe and recovers one-scan Fullscan apex jitter
while keeping high-frequency DDMS2 unchanged:
Fullscan stays 386 evaluated candidates but moves from 39 accept / 347 reject
to 146 accept / 240 reject.
DDMS2 MZmine stays 400 evaluated candidates at 25 accept / 375 reject.
DDMS2 local stays 3249 evaluated candidates at 64 accept / 3185 reject. The
comparison CSV is
`output/pt_pattern_productization_v1/two_raw_pt_only_scanaware_closeapex_20260703/scanaware_closeapex_replay_summary.csv`.
No Pt pattern maturity tier, active lane, Matrix, Backfill, counted-detection,
workbook, export, selected-area, or production readiness authority changes.
A 12-case detected spotcheck then exposed that fixed-point Gaussian15 smoothing
is not time-equivalent across acquisition density: Fullscan replay has
`min_effective_smoothing_window_sec` around 19.7 seconds, while the DDMS2 replay
has only about 3.5-4.6 seconds. Trace metrics, trace summaries, review packets,
blind overlay HTML, and sealed machine-call sidecars now expose median RT step,
effective smoothing seconds, max trace point count, and
`short_effective_smoothing_window` review flags. The replay lives under
`output/pt_detected_stability_spotcheck_20260703/trace_density_replay/`. This
is a diagnostic/review observability update only. A follow-up visual replay now
renders each case as three side-by-side review panels: raw vendor trace,
Gaussian15 point-smoothed trace, and 20-second time-smoothed trace. The new HTML
lives under
`output/pt_detected_stability_spotcheck_20260703/trace_density_replay/combined_blind_overlay_visual_modes/`;
its sealed machine-call CSV is unchanged from the previous replay for the 12
cases. User adjudication on the visual replay keeps 10 accepted and 2 rejected
labels: visual Case 10 and Case 11 remain reject because +4 behaves like a
tail/window-dominant trace rather than a credible co-eluting peak; visual Case
12 remains accepted because the +4 difference is interpreted as a broader
co-eluting peak, not a tail-dominant artifact. Follow-up replay under
`output/pt_detected_stability_spotcheck_20260703/trace_density_replay/plus4_window_tail_dominance/`
adds `plus4_window_tail_dominance` review flags for `mzmine_row|18622` and
`mzmine_row|18908`, but not `mzmine_row|21440`. The visual replay
manual-vs-machine artifact lives at
`output/pt_detected_stability_spotcheck_20260703/trace_density_replay/combined_blind_overlay_visual_modes/visual_modes_manual_vs_machine.csv`
and records 2 machine-accept / human-reject disagreements. This does not change
the current accept/reject rule, maturity tier, active lane, Matrix, Backfill,
counted-detection, workbook, export, or production-readiness authority.

| Alignment output-level contract | `production_surface` | `output_levels.py`, `--output-level`, output contract spec | `alignment_matrix.tsv` is machine/validation, not production default；`alignment_matrix_identity.tsv` is production-level identity handoff | keep production/machine/debug tests in release gate | none; contract slice done |
| `ProductionDecisionSet` | `production_surface` for alignment matrix decisions | `alignment/production_decisions.py` | release gate 尚未集中檢查 all writers use it | matrix writer gate | unassigned |
| Backfill product-authority sidecars | `production_ready` for generated policy replay of current approved evidence classes plus 72 row-specific observed-oracle rows, explicit 72-row high-signal-clean scoped writer, explicit 42-row low-scan-clean scoped writer, explicit 57-row low-height-clean scoped writer, explicit 69-row low-height-low-scan-clean scoped writer, and explicit 220-row low-height reintegration-stable scoped writer; `production_candidate` for apex-delta diagnostic probe, width-only diagnostic probe, shape-margin diagnostic probe, all-stability 299-row pool, and shape-clean reintegration-stable 104-row evidence class; `parked` for broad 4613-row auto-write | `standard_peak_backfill_policy.tsv`, `--backfill-policy-source-audit-tsv`, paired `--policy-observed-oracle-tsv` / `--policy-observed-oracle-summary-json`, `standard_peak_backfill_productization.py`, `standard_peak_policy_observed_oracle.py`, `standard_peak_activation_scope_audit.py`, `standard_peak_heldout_trace_oracle.py`, `standard_peak_reintegration_stability_audit.py`, `seed_guard_decisions.tsv`, no-RAW 85RAW artifact bridge, heldout trace oracle, activation scope audit, reintegration-stability audit, and scoped writer outputs under `output/productization_realdata_seed_guard_85raw_20260617/` | standard-path activation 先經 N-band seed guard 且 join `activation_value_delta.tsv`；generated policy path classifies every supplied source-audit row as `write_ready` / `detected_flagged` / `blocked` and replays only generated `write_ready` rows with `expected_scope=backfill_policy_write_ready_rows`; hand-authored policy TSV is not a public product input；latest real generated policy replay under `generated_policy_policy_observed_oracle_no_raw_productization/` classified all 4613 consolidated source-audit rows as 511 `write_ready`, 0 `detected_flagged`, and 4102 `blocked`, wrote exactly 511 matrix cells, passed writer expected-diff 511/511 with zero duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank blockers, and records 72 accepted `policy_observed_full_trace_reintegration` rows from the SHA-bound `policy_observed_oracle_detected_flagged_full_trace/` packet; policy next-evidence counts are 511 already approved, 1087 needing trace overlay or reintegration evidence, and 3015 needing a new approved evidence class or passing oracle；既有 85RAW chunk `r1_120` no-RAW bridge passed with 2540 candidates, 1160 eligible writes, 1380 low-seed no-writes；既有 85RAW consolidated no-RAW bridge passed with 7307 candidates, 4613 eligible writes, 2694 low-seed no-writes；high-signal heldout trace oracle 有 20 個 originally detected、sample-local cases，20/20 pass、最大 boundary error 0.0820502 min、最大 area relative error 0.0762325；low-scan heldout trace oracle `heldout_trace_reintegration_oracle_low_scan_clean_probe/` 有 56 eligible candidates / 11 selected family cases，11/11 pass、最大 boundary error 4.86717e-05 min、最大 area relative error 0.038786；combined activation scope audit 證明目前 4613 writes 中 72 個 high-signal clean eligible、42 個 low-scan clean eligible、57 個 low-height clean eligible、69 個 low-height-low-scan clean eligible、1087 個 missing overlay path，broad scope 仍 not_ready；high-signal `narrow_product_writer_expected_diff_acceptance.json` 72/72 pass 且 `readiness_tier=production_ready`；low-scan `narrow_low_scan_clean_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json` 42/42 pass、duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank 都是 0，`expected_scope=low_scan_clean_eligible_activation_rows`、`product_surface_changed=TRUE`、`readiness_tier=production_ready`；low-height bounded oracle `heldout_trace_reintegration_oracle_low_height_bounded_probe_pad050/summary.json` 是 `status=pass`、20/20 pass、max boundary error `0.0857986 min`、max area relative error `0.0564106`，且 `narrow_low_height_clean_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json` 57/57 pass、duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank 都是 0，`expected_scope=low_height_clean_eligible_activation_rows`、`product_surface_changed=TRUE`、`readiness_tier=production_ready`；low-height-low-scan bounded oracle `heldout_trace_reintegration_oracle_low_height_low_scan_clean_probe/summary.json` 是 `status=pass`、210 eligible rows / 51 families、selected 20/20 pass、max boundary error `4.80376e-05 min`、max area relative error `0.00881912`，且 `narrow_low_height_low_scan_clean_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json` 69/69 pass、duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank 都是 0，`expected_scope=low_height_low_scan_clean_eligible_activation_rows`、`product_surface_changed=TRUE`、`readiness_tier=production_ready`；low-height reintegration-stable family oracle `heldout_trace_reintegration_oracle_low_height_reintegration_stable_family/summary.json` is `status=pass`; it records 220 audit-intersection rows / 66 families, `candidate_family_scope_match_level=family_id`, `candidate_family_scope_oracle_basis=detected_trace_rows_from_candidate_families`, 1520 available detected trace candidates from those families, selected 20/20 pass, max boundary error `0.0830019 min`, and max area relative error `0.0725986`; the matching writer `narrow_low_height_reintegration_stable_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json` passes 220/220 with `expected_scope=low_height_reintegration_stable_eligible_activation_rows`, `product_surface_changed=TRUE`, `readiness_tier=production_ready`, and zero duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank blockers；this fifth writer adds 199 cells outside the previous four ready scopes, making the five-scope cell-level union 439 cells before the policy-observed oracle adds 72 additional writer-approved rows；all-stability direct promotion remains blocked by formal target `standard_reintegration_stable_candidate_family_trace`: `heldout_trace_reintegration_oracle_all_stability_family/summary.json` records 299 audit-intersection rows / 77 families, 1694 available detected trace candidates, selected 20 family cases, and failed 19/20 because `FAM000949/NormalBC2261_DNA` has area relative error `0.19621` above the accepted 10% ceiling；shape-clean reintegration-stable oracle `heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/summary.json` records 104 audit-intersection rows / 33 families, 334 available detected trace candidates / 31 families, selected 20/20 pass, max boundary error `0.0830019 min`, and max area relative error `0.0725986`, but a temporary writer probe found `matrix_cells_written=0` and `unchanged_delta_row_count=104`, so no public writer flag was retained；apex-delta probe `heldout_trace_reintegration_oracle_apex_delta_clean_probe/summary.json` 是 `status=fail`、17/20 pass、max boundary error `2.19621 min`、max area relative error `0.424518`，所以沒有 writer approval；width-only probe `heldout_trace_reintegration_oracle_width_clean_probe/summary.json` 是 `status=fail`、1/3 pass、max boundary error `1.86561 min`、max area relative error `0.599229`，所以沒有 writer approval；shape-margin probe `heldout_trace_reintegration_oracle_shape_margin_clean_probe/summary.json` 是 `status=fail`、6/8 pass、max boundary error `0.0625542 min`、summary max area relative error `0.198393`，所以沒有 writer approval；observed provenance contract 禁止 oracle/manual/review row 自抄；非標準 peak 仍不可自動 promotion | release docs must say generated policy is the current production-ready writer surface for approved evidence classes plus accepted observed-oracle rows; 72-row, 42-row, 57-row, 69-row, and 220-row scoped writers are historical safe demonstrators; apex-delta/width-only/shape-margin/all-stability/shape-clean-stability are only candidate or explanatory probes; broad 4613 auto-write is parked | none for generated policy replay over current approved evidence classes plus the 72 observed-oracle rows and the five scoped writers; apex-delta/width-only/shape-margin/all-stability need narrower rules or passing oracles before writer work; shape-clean stability needs a missing-cell product scope or policy-explanation-only contract before writer work; broad 4613 can reopen only with a new independent truth source plus observed/masked/product-writer oracle and expected-diff approval |
| Provisional production-candidate gate | `diagnostic_only` with no-promotion guard | production-candidate sidecar, `tests/test_provisional_backfill_candidate_gate_cli.py` | legacy artifact name is still potentially confusing, but summary/test contract says `readiness_label=diagnostic_only`, `production_ready=false`, `matrix_contract_changed=false`, and the CLI does not mutate `alignment_matrix.tsv` | rename only if future public UX needs it; do not promote from this sidecar alone | none; diagnostic guard done |

Backfill row update, 2026-06-18: the long table row above is historical for the
approved 511-cell writer surface and prior probes. The broad 4613-row
auto-write branch is now superseded by
`backfill_broad_autowrite_feasibility_gate_v1` and is `parked`; do not follow
the older "next broadening step" wording without a new independent truth source.

Backfill row update, 2026-06-17: the low-height evidence is no longer only the
old full-trace 19/20 failure. The diagnostic heldout oracle now has an
`expected_window_bounded` observed reintegration mode; no-RAW replay on the
same 85RAW trace artifacts shows `padding=0.5 min` passes selected 20/20 with
max boundary error `0.0857986 min` and max area relative error `0.0564106`.
That evidence has now been connected to an explicit low-height writer flag:
`narrow_low_height_clean_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json`
passes 57/57 with `readiness_tier=production_ready`. This authorizes only the
explicit 57-row low-height scoped writer, not broad 4613-row activation or
default extraction behavior.

Backfill row update, 2026-06-17: the low-height-low-scan intersection is now a
separate explicit scoped writer. This class requires height <2e6, scan count
7-9, and otherwise clean shape/local-global/width/apex evidence. The formal
no-RAW 85RAW bounded heldout oracle
`heldout_trace_reintegration_oracle_low_height_low_scan_clean_probe/summary.json`
passes selected 20/20 from 210 eligible rows / 51 families, with max boundary
error `4.80376e-05 min` and max area relative error `0.00881912`. The explicit
writer run
`narrow_low_height_low_scan_clean_no_raw_productization/narrow_product_writer_expected_diff_acceptance.json`
passes 69/69 with `readiness_tier=production_ready`. This authorizes only the
explicit 69-row low-height-low-scan scoped writer, not broad 4613-row activation
or default extraction behavior.

Backfill row update, 2026-06-17: boundary-stability / reintegration-agreement is
now a named broader diagnostic evidence class. The first full no-RAW 85RAW run
`reintegration_stability_audit/` scans the committed 4613-row activation scope
and reports 299 eligible written rows, 3227 ineligible written rows, and 1087
missing-evidence rows. Of those 299 eligible rows, 271 are outside the four
currently production-ready scoped writer envelopes. This is stronger broad
`production_candidate` evidence only: no writer flag exists, no matrix output is
changed, and masked/product-writer oracle plus expected-diff approval are still
required before any activation claim. Subagent review caught that `status=pass`
was too easy to misread as writer approval; the summary now fails closed with
`status=candidate_pool_blocked`, `writer_authority_status=blocked`, and upstream
activation-scope schema/source_run_id/SHA provenance.

Backfill row update, 2026-06-17: low-height reintegration-stable is now the
fifth explicit scoped writer. It is not the full 299-row stability pool. The
formal family-scope heldout oracle
`heldout_trace_reintegration_oracle_low_height_reintegration_stable_family/`
uses the 299-row stability audit plus the current combined activation scope
audit to select only stability-eligible written rows with `cell_height <2e6`;
that gives 220 audit-intersection rows / 66 families. The oracle then expands
only to detected trace rows from those same families
(`candidate_family_scope_match_level=family_id`; 1520 available candidates),
selected 20 family cases, and passed 20/20 under the accepted
`0.1 min / 10% area` ceiling, with
max boundary error `0.0830019 min` and max area relative error `0.0725986`.
The explicit writer
`narrow_low_height_reintegration_stable_no_raw_productization/` then selected
and wrote exactly 220 cells from the pre-standard-backfill matrix and passed
`narrow_product_writer_expected_diff_acceptance.json` 220/220 with
`readiness_tier=production_ready`. This adds 199 cells outside the previous
four ready scopes; the five-scope ready union is now 439 cells. The broad
4613-row activation and the all-stability 299-row pool remain
`production_candidate` / writer-blocked because the formal all-stability
family oracle failed one selected case above the accepted area tolerance.

Backfill row update, 2026-06-17: shape-clean reintegration-stable is now
recorded as a `production_candidate` evidence class, not a writer. The formal
target `standard_shape_clean_reintegration_stable_candidate_family_trace`
filters the stability/activation intersection to rows with
`apex_aligned_shape_similarity >=0.95`; the real no-RAW 85RAW oracle
`heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/`
records 104 audit-intersection rows / 33 families, 334 available detected trace
candidates / 31 families, and selected 20/20 pass with max boundary error
`0.0830019 min` and max area relative error `0.0725986`. A local writer probe
with temporary wiring was intentionally removed before commit because it found
0 new matrix writes and 104 unchanged/pre-existing matrix values. This scope can
inform generated-policy explanation or a future missing-cell scope, but it is
not product writer authority.

## WIP limits

目前開發節奏拖慢的主因不是缺想法，而是同時開太多 diagnostic/spec，沒有把其中少數推完。

每個 2 週維護週期最多允許:

- 1 個 primary productization lane。
- 1 個 supporting infrastructure lane。
- 1 個 diagnostic-only lane。

若要新增 lane，必須先把現有 lane 之一改成:

- `production_surface`
- `shadow_ready` with explicit next gate
- `diagnostic_only` archived with kill/externalize decision
- abandoned with reason

## Weekly maintenance checklist

建議每週固定一次，或每次重大 PR 收尾後執行。

### 1. Board hygiene

- [ ] `Active productization board` 每一列的 tier 是否仍準確。
- [ ] 有沒有功能在聊天、README、報告、PR 中被過度宣稱。
- [ ] 新增的 diagnostic/report/sidecar 是否有 exit rule。
- [ ] `WIP owner` 是否明確；沒有 owner 的 lane 不應進入 active implementation。
- [ ] 有沒有超過 WIP limit。

### 2. Contract hygiene

- [ ] Public surface 是否有 schema/version/required columns。
- [ ] Workbook/CSV/TSV/JSON 欄位變更是否同步 tests。
- [ ] Config key / CLI flag / GUI setting 是否同步 docs。
- [ ] Downstream handoff 是否明確 owner 與 pass-through/exclusion rule。
- [ ] Report 是否只 render，不重新計算 domain decision。

### 3. Promotion hygiene

- [ ] 這週是否有任何 selected peak、area、confidence、reason、counted detection、matrix value 會改變。
- [ ] 若會改變，是否有 expected-diff artifact。
- [ ] 若只在 sidecar，主輸出是否被測試確認不變。
- [ ] 是否有 rollback/stop rule。
- [ ] 是否有 reviewer/audit trail 記錄。

### 4. Validation hygiene

- [ ] 本週 claims 使用的是 synthetic、8RAW、85RAW、targeted benchmark、manual EIC/MS2 review，還是 docs-only。
- [ ] 是否把 tests passing 誤寫成 production readiness。
- [ ] 是否有 large RAW gate 被偷偷跳過。
- [ ] 是否有 downstream workbook/TSV smoke。
- [ ] 是否有 stale output 被當成新 evidence。

### 5. Drift cleanup

- [ ] 舊 spec 是否仍和 runtime reality 一致。
- [ ] 有沒有多份 docs 對同一能力給不同 tier。
- [ ] 有沒有命名造成誤會，例如 `production_candidate_gate` 被誤認為 production promotion。
- [ ] 有沒有 legacy name 應保留為 compatibility，而不是立刻 rename。
- [ ] 是否需要把本週結論回寫到這份 control plane。
- [ ] 若本次工作需要交接，是否已解析並同步更新正確的 branch-scoped handoff；不要覆寫其他分支的 handoff。
- [ ] `git status --short -- docs/superpowers/handoffs/current/ docs/superpowers/plans/2026-06-15-productization-control-plane.md` 是否符合預期；若 handoff 是接手必需品，必須 tracked/staged/committed，或明確標成 local-only/non-authoritative。PR closeout 時，應把 current handoff 濃縮進 PR body。

## Pre-work checklist

任何產品化 task 開始前先填。

```markdown
## Productization intake

- Feature/lane:
- Current tier:
- Desired tier this PR:
- Product surface touched:
- Domain authority owner:
- Files/modules likely touched:
- Public contract affected:
- Expected output change:
- Expected-diff needed: yes/no
- Validation fixture: synthetic / 8RAW / 85RAW / targeted benchmark / manual review / docs-only
- Stop rule:
- Rollback rule:
- Downstream consumer:
```

如果 `Desired tier this PR` 是 `production_surface` 或 `production_ready`，但 `Expected-diff needed` 是空白，停止。

## Promotion packet template

每次要把功能從 lower tier 推到 higher tier，必須有一份 promotion packet。可以寫在 PR description、spec closeout note，或本檔下方 append。

```markdown
## Promotion packet

### Capability

- Name:
- Previous tier:
- Proposed tier:
- Owner:

### Public surface

- CLI/config/GUI/output/report:
- Schema/version:
- Downstream consumer:

### Domain authority

- Decision owner:
- Evidence source:
- Why writer/report is not recomputing domain logic:

### Behavior delta

- What changes:
- What must not change:
- Expected-diff artifact:
- Rows/targets/samples affected:

### Validation

- Synthetic tests:
- Focused unit/integration tests:
- 8RAW:
- 85RAW:
- Manual review:
- Downstream smoke:

### Audit and replay

- Metadata/provenance:
- Manifest/replay support:
- Review/action log:

### Stop/rollback

- Stop if:
- Roll back by:
- Residual risk:
```

## Definition of done by tier change

### `missing` -> `partial_internal`

- [ ] Internal owner named.
- [ ] Scope kept out of public docs/README.
- [ ] Tests prove helper behavior or parser behavior.
- [ ] Exit rule named.

### `partial_internal` -> `diagnostic_only`

- [ ] CLI/tool/report can run without changing product outputs.
- [ ] Output path under `output/` or documented diagnostic location.
- [ ] Summary labels itself `diagnostic_only`.
- [ ] No writer/import cycle from diagnostics back into domain/product code.
- [ ] Clear next decision: promote, kill, or externalize.

### `diagnostic_only` -> `shadow_ready`

- [ ] Shadow artifact is rejoinable to product rows/samples/targets.
- [ ] Product outputs are tested unchanged.
- [ ] Shadow schema has required columns and version.
- [ ] Summary states `shadow_ready`, not product-ready.
- [ ] Comparison report explains expected product impact if activated.

### `shadow_ready` -> `production_candidate`

- [ ] Activation/export contract exists.
- [ ] Domain authority owner is named.
- [ ] Expected-diff is produced and reviewed.
- [ ] Focused tests cover accepted/rejected/blocked cases.
- [ ] Downstream schema impact is documented.
- [ ] Rollback path is explicit.

### `production_candidate` -> `production_surface`

- [ ] Public surface is stable and documented.
- [ ] Schema/version is locked by tests.
- [ ] CLI/GUI/config/report docs are synchronized.
- [ ] Replay/provenance is available.
- [ ] Main output and audit output agree.
- [ ] Release gate includes this behavior.

### `production_surface` -> `production_ready`

- [ ] Validation fixture tier is named and sufficient for claim.
- [ ] 8RAW/85RAW or targeted benchmark requirement is met, if applicable.
- [ ] Manual EIC/MS2 review is documented when science judgment is required.
- [ ] Downstream consumer smoke passes.
- [ ] Known residual risks are documented.

## Monthly reset checklist

每月做一次，避免拖沓變成常態。

- [ ] Count active lanes. If >3, freeze new work.
- [ ] Pick exactly one lane to push to next tier.
- [ ] Pick exactly one lane to kill/archive/externalize.
- [ ] Review all `diagnostic_only` artifacts older than 30 days.
- [ ] Review all product docs/plans/schemas with no owner.
- [ ] Update this board with actual tier, not aspirational tier.
- [ ] Write one closeout note summarizing what changed in product surface.

## Historical immediate 2026-06 queue snapshot

This section is retained as a historical queue from the original 2026-06
planning pass. It is not the current WIP table; use `Current medium-term active
lanes` and `Active productization board` above for current owner/tier decisions.
Items below that say `primary lane` or `supporting lane` describe their status
at that older checkpoint, not the latest release claim.

依照當時的 current capability inventory 與 replay executor closeout，當時順序為:

1. `method_manifest_v1` - done
   - Result: `production_ready` for targeted CLI replay parity.
   - Evidence: focused tests, 8RAW CSV/workbook replay parity, and one targeted 85RAW initial+replay sequence.
   - Residual: no timestamped workbook hash capture; GUI replay not wired to mainline.

2. `targeted_schema_versioning_v1` - done
   - Reason: mature-tool parity 需要 output schema version，不然下游與 replay 只能猜欄位語意。
   - Result: `production_surface` for additive schema/version contract.
   - Scope: `output/schema.py` constants, manifest `output_schema`, workbook `Run Metadata`; no CSV data-column changes.

3. `review_action_apply_v1` - historical primary lane at this checkpoint
   - Reason: 解決 Review Queue 不能回寫，這是 Skyline parity floor。
   - Current baseline: `production_candidate` for action schema/import validator and dry-run application plan only.
   - Next target: audit/apply loop with expected-diff before touching selected outputs.

4. `sample_metadata_runtime_parity_v1` - historical supporting lane at this checkpoint
   - Reason: normalization/QC/alignment 都需要 sample type、QC、blank、batch、injection order。
   - Current baseline: `production_candidate` for schema/validator only.
   - Next target: project current legacy injection order behavior into the shared resolver with output parity; do not let sample role change matrix behavior in this slice.

5. `alignment_output_contract_v1` - done as contract/docs alignment
   - Reason: `alignment_results.xlsx` 已成熟，但 TSV production/machine wording 需要對齊。
   - Result: `production_surface` for output-level contract.
   - Guard: `alignment_matrix.tsv` remains machine/validation, while `alignment_matrix_identity.tsv` is a production-level identity handoff.

暫時不要優先做:

- 新 peak picker。
- 新 diagnostic gallery。
- 新 shadow sidecar。
- normalization/calibration main-matrix write。
- backfill product-authority primary matrix activation。

除非 `Current medium-term active lanes` 先把其中一個 lane 解凍，且該 lane 已經有 owner、spec、promotion packet，否則新增這些只會增加未收斂面積。

## Maintenance log

新增產品化狀態變更時，在這裡 append。格式:

```markdown
### YYYY-MM-DD - <lane>

- Previous tier:
- New tier:
- Evidence:
- Product surface changed:
- Validation:
- Remaining blocker:
```

### 2026-07-17 - pt_pattern_discovery_diagnostic_v1_closeout

- Previous tier: the repaired 32-row reviewed-label packet was described as
  `production_candidate`; other Pt detector/runtime artifacts were
  `diagnostic_only`.
- New tier: all current Pt pattern candidate, same-scan, trace, anchor-local,
  review, historically named oracle, threshold, conflict, and machine-call
  surfaces are `diagnostic_only`.
- Evidence: the 32-row threshold result is resubstitution against stale reviewed
  labels, not independent ground truth or held-out detector performance. The
  first bounded anchor-local nine-case human slice is 6 accept, 2 reject (Cases
  06 and 09), and 1 unresolved uncertain (Case 08). Current matcher rows are one
  greedy assignment representative per base; same-scan support is theoretical
  window occupancy; `trace_rule_call` is a diagnostic proxy.
- Product surface changed: none. No Pt selected area, canonical
  `PeakHypothesis`, `IntegrationResult`, Alignment/Backfill intake, counted
  detection, Matrix, workbook, activation, or export authority was granted.
- Validation: focused schema/behavior tests and repo docs gates are required for
  this branch closeout; no new RAW replay or threshold tuning is part of this
  tier correction.
- Remaining blocker: complete or bounded Top-K assignment generation,
  assignment-bound evidence, independent validation, canonical model selection,
  and an explicit projection/rollback contract.

### 2026-07-02 - pt_pattern_discovery_productization_v1

- Previous tier: `diagnostic_only` implementation pass with no active
  productization lane.
- New tier: `diagnostic_only` active primary lane, aiming for
  `production_candidate` only after package-owned trace evidence, stable
  replay/provenance, and manual-review oracle exist.
- Evidence: Pt candidate/review/sidecar schemas and real-sample diagnostic
  slices exist; scan-proxy Pt-only limit-50 trace diagnosis found 12 supported,
  32 partial, 5 review, and 1 incomplete cases, while Pt+CID-NL(dR) limit-50
  trace diagnosis found 34 supported, 14 partial, and 2 incomplete cases.
  Trace/co-elution support semantics are package-owned in
  `xic_extractor.discovery.pt_pattern_trace_evidence`, with the diagnostic JSON
  sidecar recording `readiness_tier=diagnostic_only` and evidence owner. The
  bounded manual-review packet builder is package-owned in
  `xic_extractor.discovery.pt_pattern_review_packet`, with generated rows
  remaining `oracle_status=pending_human_review`. Two bounded packets were
  generated under ignored output; after v2 rebucketing the current packet set is
  24 rows total. Blind overlay HTML and sealed machine-call CSVs were
  regenerated for both packets. Trace evidence schema v2 records
  `trace_decision_basis = gaussian15_smoothed_trace`; Gaussian15-smoothed
  intensity is the basis for apex/shape/overlap support and sealed machine
  calls, while raw vendor trace points remain audit/reference evidence only.
  Blind HTML hides machine calls and diagnostic status buckets, and sealed
  machine calls are not oracle authority. The earlier v1 user blind review
  completed 28/28 calls and yielded 10 accept / 18 reject rows. The first v2
  refresh carried forward only the 20 overlapping old row ids plus 4 new
  user-adjudicated rejects, so that 24-row artifact is an intermediate refresh
  output rather than the calibration baseline. The repaired v2 oracle rechecks
  all 8 stale old row ids against current Gaussian15 metrics and combines them
  with the 24-row refresh output, yielding 32 rows total: 10 accept / 22 reject.
  The repaired threshold calibration keeps the same candidate rule
  (`full pattern`, apex <= 10 s, Pearson >= 0.90, overlap >= 0.50) and gives
  10 TP / 22 TN / 0 FP / 0 FN on the repaired oracle. The smoothing and review
  artifacts do not change detector/runtime tier, Matrix authority, counted
  detection, workbook output, or quantitation authority.
- Product surface changed: none in the sense of Matrix, workbook, counted
  detection, selected area, Backfill, ProductWriter, or activation authority.
  The active lane and review-artifact scope changed in this control plane.
- Validation: focused Pt pattern tests, oracle-promotion tests, docs checks, and
  no-RAW real artifact replay passed locally. This entry itself is not a new RAW
  validation.
- Remaining blocker: derive and test detector thresholds against the bounded
  manual oracle before any detector/runtime tier promotion or downstream write
  authority.

### 2026-06-26 - docs-cleanup routing

- Previous tier: unchanged
- New tier: unchanged
- Evidence: repo public-doc cleanup replacing retired private-history note/spec
  path references with current public owner docs or opaque retired-provenance
  identifiers.
- Product surface changed: none. This does not change maturity tier, active
  lane, writer authority, output schema, review/replay behavior, selected values,
  selected area/counting, matrix values, or matrix authority.
- Validation: public-surface stub audit only; no RAW or product behavior
  validation required for this docs-only routing change.
- Remaining blocker: no productization blocker created by this cleanup.

### 2026-06-15 - Control plane initialized

- Previous tier: none
- New tier: living maintenance checklist
- Evidence: then-current capability inventory and five-area read-only review
- Product surface changed: none
- Validation: document smoke only
- Remaining blocker: no active owner assigned to the first four June focus specs

### 2026-06-15 - method_manifest_v1

- Previous tier: `missing`
- New tier: `production_ready` for targeted CLI replay parity; not full exact artifact replay
- Evidence: current owner `docs/product/run-provenance.md`; retired method-manifest spec provenance; retired replay-validation private note summarized here; `xic_extractor.output.method_manifest`; `xic-extractor-cli --replay-manifest`; focused manifest/output metadata/CLI replay tests; targeted 8RAW and 85RAW CSV/workbook replay parity; post-review test that replay rejects settings/targets artifact paths that do not bind to `invocation.config_dir`
- Product surface changed: additive `output/method_manifest.json`; additive workbook `Run Metadata` rows `method_manifest_schema`, `method_manifest_path`, `method_manifest_sha256`; additive `--replay-manifest` CLI mode
- Validation: synthetic/focused unit, output contract, CLI replay tests, targeted 8RAW CSV-only replay byte parity, targeted 8RAW Excel-mode workbook compare, and one targeted 85RAW initial+replay sequence with CSV byte parity plus workbook compare
- Remaining blocker: no timestamped workbook hash capture for full exact artifact replay; GUI parity intentionally skipped because GUI replay is not yet wired to mainline

### 2026-06-15 - targeted_schema_versioning_v1

- Previous tier: `missing` as an explicit targeted output schema contract
- New tier: `production_surface` for additive schema/version metadata
- Evidence: `xic_extractor.output.schema` version constants; `method_manifest.json` `output_schema`; workbook `Run Metadata` row `targeted_output_schema_version`
- Product surface changed: additive `targeted_output_schema_version` metadata row and additive manifest `output_schema` block; no CSV data-column changes
- Validation: `python -m pytest tests\test_output_schema_contract.py tests\test_output_metadata.py tests\test_method_manifest.py -q`
- Remaining blocker: downstream export profiles still need a handoff profile; schema version is surfaced through manifest/metadata, not embedded as a row-level CSV column

### 2026-06-15 - review_action_import_and_application_plan_v1

- Previous tier: `missing`
- New tier: `production_candidate` for ReviewAction import validation, dry-run application plan, expected-diff approval template/loader, apply-readiness planning, and changeset planning only
- Evidence: current owner `docs/product/review-roundtrip.md`; retired review-roundtrip spec provenance; `xic_extractor.review_actions`; `scripts/validate_review_actions.py`; `scripts/plan_review_action_applications.py`; `scripts/validate_review_action_expected_diffs.py`; `scripts/plan_review_action_apply_readiness.py`; `scripts/plan_review_action_apply_changesets.py`; post-review stale-approval guard that blocks expected-diff approvals when the current targeted row no longer matches the approved baseline state
- Product surface changed: additive `review_action_v1` TSV/CSV schema, validator CLI, additive `review_action_application_plan_v1` dry-run TSV, optional `review_action_expected_diff_v1` template TSV with baseline target-state columns, expected-diff approval loader, approval validator CLI, additive `review_action_apply_readiness_v1` TSV planner, and additive `review_action_apply_changeset_v1` TSV planner; no extraction output mutation
- Validation: `python -m pytest tests\test_review_actions.py -q`; `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor\review_actions.py scripts\validate_review_actions.py scripts\plan_review_action_applications.py scripts\validate_review_action_expected_diffs.py scripts\plan_review_action_apply_readiness.py scripts\plan_review_action_apply_changesets.py tests\test_review_actions.py`; `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor\review_actions.py scripts\validate_review_actions.py scripts\plan_review_action_applications.py scripts\validate_review_action_expected_diffs.py scripts\plan_review_action_apply_readiness.py scripts\plan_review_action_apply_changesets.py`
- Remaining blocker: no product-writing action application/reintegration loop, no manual boundary recompute, no selected candidate switch writer, no audited output writer that consumes changeset rows

### 2026-06-15 - sample_metadata_contract_v1

- Previous tier: `partial_internal`
- New tier: `production_candidate` for shared schema/validator only
- Evidence: current owner `docs/product/sample-metadata-qc.md`; retired sample-metadata spec provenance; `xic_extractor.sample_metadata`; `scripts/validate_sample_metadata.py`; post-review alias-collision tests for shared `sample_name`/`raw_stem` injection-order namespace
- Product surface changed: additive `sample_metadata_v1` TSV/CSV schema and validator CLI; no extraction/QC/alignment runtime change
- Validation: `python -m pytest tests\test_sample_metadata.py -q`; `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor\sample_metadata.py scripts\validate_sample_metadata.py tests\test_sample_metadata.py`; `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor\sample_metadata.py scripts\validate_sample_metadata.py`
- Remaining blocker: extraction still reads legacy `injection_order_source`; instrument-QC sequence manifest is not yet projected into `sample_metadata_v1`; alignment and normalization do not consume this resolver; sample roles cannot alter matrix values without expected-diff gates

### 2026-06-16 - review_action_audited_apply_copy_v1

- Previous tier: `production_candidate` for import/apply-readiness/changeset planning only
- New tier: `production_surface` for audited targeted-long output copy and `review_action_apply_audit_v1`
- Evidence: `xic_extractor.review_actions`; `scripts/apply_review_action_changesets.py`; `tests/test_review_actions.py`
- Product surface changed: additive CLI that consumes `review_action_apply_changeset_v1` and writes an audited targeted-long copy plus audit TSV. The command refuses input overwrite and rejects blocked changesets by default.
- Safe behavior boundary: `accept_current` is audit-only; `mark_unresolved` writes only `Review State = unresolved_by_review`; `reject_current` may write `Product State = rejected_by_review`, `Counted Detection = FALSE`, and `Review State = rejected_by_review` only after approved expected-diff. `select_candidate` and `set_manual_boundary` remain deferred because they need candidate sidecar or area recompute.
- Validation: `python -m pytest tests\test_review_actions.py -q`
- Remaining blocker: no selected-candidate switch writer, no manual-boundary area recompute writer, no workbook or primary matrix rewrite.

### 2026-06-16 - sample_metadata_runtime_parity_v1

- Previous tier: `production_candidate` for schema/validator only
- New tier: `production_surface` for extraction injection-order parity; `production_candidate` for roles/batch/matrix/exclusion semantics
- Evidence: `xic_extractor.sample_metadata`; `xic_extractor.extraction.pipeline.resolve_injection_order`; `tests/test_sample_metadata.py`; `tests/test_extractor_run.py`
- Product surface changed: existing `injection_order_source` can now point to a `sample_metadata_v1` CSV/TSV. Legacy `Sample_Name,Injection_Order` files still use the legacy parser.
- Safe behavior boundary: only injection-order mapping is projected; `sample_role`, batch, matrix, group, and exclusion fields do not alter extraction output, counted detection, or matrix values.
- Validation: `python -m pytest tests\test_sample_metadata.py tests\test_injection_rolling.py tests\test_extractor_run.py -q`
- Remaining blocker: instrument-QC, alignment, and normalization do not yet consume the shared sample metadata resolver.

### 2026-06-16 - review_action_reintegration_v1

- Previous tier: `production_candidate` for deferred changeset rows; selected-candidate switch and manual-boundary area recompute deferred
- New tier: `parked`; audited apply copy remains `production_surface`, deferred changesets remain `production_candidate`
- Evidence: current owner `docs/product/review-roundtrip.md`; retired review-roundtrip spec provenance; current `xic_extractor.review_actions` apply path records `select_candidate` and `set_manual_boundary` as deferred operations only
- Product surface changed: none this round
- Validation: no code path changed in this lane this round
- Remaining blocker: human product decision and expected-diff acceptance are required before any writer may change selected peak, selected area, workbook, primary matrix, or counted detection for these actions

### 2026-06-17 - review_action_candidate_sidecar_verifier_v1

- Lane: `review_action_reintegration_v1`.
- Previous tier: selected-candidate switch was `parked`; candidate identity could
  only appear as a free `candidate_id` string inside ReviewAction/expected-diff
  rows.
- New tier: `production_candidate` for candidate-sidecar verification only.
  Selected-candidate switch and manual-boundary area recompute remain parked.
- Evidence: `xic_extractor.review_actions` now defines
  `review_action_candidate_sidecar_v1`; `scripts/plan_review_action_candidate_sidecars.py`
  reads `review_action_v1` plus targeted `peak_candidates.tsv`, verifies that
  each `select_candidate` action's `candidate_id` is present and unique for the
  same sample/target, records a candidate row SHA-256 plus RT/area/confidence
  audit fields, and fails closed for multiple `select_candidate` actions on the
  same target, missing target rows, missing candidates, or duplicate candidate
  ids.
- Product surface changed: additive CLI and additive TSV schema only. No
  selected peak, selected area, counted detection, workbook, primary matrix, or
  extraction output is changed.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_review_actions.py -q`
  (`32 passed`); focused ruff/mypy for `xic_extractor\review_actions.py`,
  `scripts\plan_review_action_candidate_sidecars.py`, and
  `tests\test_review_actions.py`.
- Remaining blocker: a future product-writing selected-candidate switch still
  needs an explicit expected-diff/apply contract that consumes the verified
  candidate sidecar row. Manual boundary still needs an area recompute writer and
  product decision.
- Next checkpoint: if reopening ReviewAction mutation, consume
  `review_action_candidate_sidecar_v1` together with approved expected-diff; do
  not infer candidate identity from workbook display text.

### 2026-06-16 - sample_metadata_instrument_qc_projection_v1

- Previous tier: `production_candidate` for roles/batch/matrix metadata outside extraction
- New tier: `production_surface` for additive instrument-QC `sample_metadata_v1` sidecar; roles remain non-mutating metadata only
- Evidence: `xic_extractor.instrument_qc.sequence_manifest_writers.write_sample_metadata_tsv`; `scripts/run_instrument_qc.py --method-doc`; `tests/test_instrument_qc_sequence_manifest.py`; `tests/test_run_instrument_qc.py`
- Product surface changed: additive `instrument_qc_sample_metadata.tsv` written next to `instrument_qc_sequence_manifest.tsv` and `instrument_qc_injection_order.csv` when `--method-doc` is used. It includes actual matched RAW rows and raw-dir-only instrument-QC rows, maps instrument-QC class to `sample_role`, and does not feed role metadata into quant behavior.
- Validation: `python -m pytest tests\test_instrument_qc_sequence_manifest.py tests\test_run_instrument_qc.py -q`; touched-file ruff/mypy passed
- Remaining blocker: alignment and normalization still do not consume the shared resolver; sample roles/exclusions must not alter matrix values without a separate expected-diff gate

### 2026-06-17 - sample_metadata_alignment_column_order_v1

- Previous tier: `production_surface` for extraction injection-order parity and instrument-QC sidecar; alignment remained a resolver-parity blocker
- New tier: `production_surface` for alignment sample-column injection-order parity; roles remain non-mutating metadata only
- Evidence: `xic_extractor.alignment.pipeline.run_alignment`; `scripts/run_alignment.py --sample-column-injection-order`; `tests/test_alignment_pipeline_outputs.py`
- Product surface changed: `--sample-column-injection-order` may now receive a `sample_metadata_v1` CSV/TSV in addition to legacy `Sample_Name,Injection_Order` CSV/XLSX. The shared metadata parser projects `sample_name`/`raw_stem` aliases into the same injection-order mapping used for final matrix/status sample-column ordering.
- Safe behavior boundary: this reorders output sample columns only when the user explicitly supplies the input path. Sample roles, exclusions, batch, matrix type, and group still do not alter matrix values, counted detection, feature acceptance, or backfill activation.
- Validation: `python -m pytest tests\test_alignment_pipeline_outputs.py::test_pipeline_orders_sample_columns_by_sample_metadata_v1 tests\test_alignment_pipeline_outputs.py::test_pipeline_orders_sample_columns_by_injection_order tests\test_alignment_pipeline_outputs.py::test_pipeline_keeps_input_sample_order_without_injection_source -q`; touched-file ruff passed
- Remaining blocker: normalization still does not consume the shared resolver; sample roles/exclusions must not alter matrix values without a separate expected-diff gate

### 2026-06-17 - sample_metadata_rt_normalization_anchor_resolver_v1

- Previous tier: `production_surface` for extraction/alignment injection-order parity and instrument-QC sidecar; RT-normalization anchor diagnostic remained a resolver-parity blocker
- New tier: `production_surface` for RT-normalization anchor diagnostic injection-order parity; roles remain non-mutating metadata only
- Evidence: `tools/diagnostics/rt_normalization_anchor_loaders.py`; `tools/diagnostics/analyze_rt_normalization_anchors.py --sample-info`; `tests/test_rt_normalization_anchors.py`
- Product surface changed: `--sample-info` for injection-based RT-normalization anchor references may now receive a `sample_metadata_v1` CSV/TSV in addition to legacy `Sample_Name,Injection_Order` input. The diagnostic projects `sample_name`/`raw_stem` aliases into the same injection-order mapping used by legacy rolling-anchor references.
- Safe behavior boundary: this affects only injection-order lookup for `injection-local-median` and `injection-loess` RT-normalization anchor diagnostics. Sample roles, exclusions, batch, matrix type, and group do not alter normalized values, matrix values, counted detection, feature acceptance, or calibration/main-matrix writes.
- Validation: `uv run pytest tests\test_rt_normalization_anchors.py -q` (`16 passed`); `uv run ruff check tools\diagnostics\rt_normalization_anchor_loaders.py tests\test_rt_normalization_anchors.py` (`All checks passed!`); `uv run mypy tools\diagnostics\rt_normalization_anchor_loaders.py` (`Success: no issues found in 1 source file`); subagent reviewer `Descartes` found no P1/P2 blocker and the P3 request to cover `injection-loess` parity was fixed.
- Remaining blocker: role-aware QC/blank/batch/matrix behavior and calibration/normalization writes remain blocked until a separate expected-diff gate and product decision exist.

### 2026-06-16 - targeted_ms1_shape_identity_explicit_optin_v1

- Previous tier: `diagnostic_only` for shared MS1 shape evidence; explicit opt-in workflow pending tier decision
- New tier: `production_candidate` for explicit support-TSV workflow only
- Evidence: `docs/product/evidence-spine.md`; 8RAW expected-diff artifact `output/ms1_shape_identity_optin_8raw_20260616/expected_diff_summary.tsv`; 85RAW manual-support and generic-support artifacts under `output/ms1_shape_identity_optin_85raw_20260616/` and `output/ms1_shape_identity_generic_support_85raw_20260616/`
- Product surface changed: none this round; existing explicit config/CLI support path remains opt-in
- Validation: reused existing 8RAW/85RAW artifacts; no new 85RAW run because the existing artifacts already answer the candidate-tier decision
- Remaining blocker: default automatic support generation/consumption remains
  off. Broader target expansion, default extraction, and GUI rescue remain not
  production-ready.

### 2026-06-16 - standard_peak_seed_guard_v1

- Previous tier: `shadow_ready` for backfill product-authority sidecars; seed guard spec was `implementation_candidate`
- New tier: `production_candidate` for standard-path seed guard slice only
- Evidence: `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`; `xic_extractor.diagnostics.standard_peak_backfill_productization`; additive `seed_guard_decisions.tsv`; heldout oracle result schema/evaluator; `tests/test_standard_peak_shadow_activation_inputs.py`; `tests/test_standard_peak_backfill_productization.py`
- Product surface changed: standard-peak matrix activation now evaluates standard-path promotion candidates against N-band seed support before writing activation decisions. The acceptance artifact proves candidate coverage, low-seed no-write expectations, and actual write attribution through `activation_value_delta.tsv`. No workbook schema, review-row enum, primary alignment schema, or non-standard peak policy changed.
- Validation: `python -m pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q`; touched-file ruff/mypy passed
- Remaining blocker: production-ready still needs heldout oracle result rows from real/reviewed cases plus an 85RAW expected-diff gate. Non-standard peak automatic promotion remains out of scope.

### 2026-06-17 - standard_peak_heldout_oracle_results_cli_v1

- Previous tier: `production_candidate` for standard-path seed guard with package-level heldout oracle evaluator
- New tier: still `production_candidate`; heldout oracle result gate is now executable, but not satisfied by real/reviewed rows
- Evidence: `tools/diagnostics/standard_peak_heldout_oracle_results.py`; `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs.build_heldout_oracle_results`; `tests/test_standard_peak_shadow_activation_inputs.py`
- Product surface changed: additive diagnostic CLI that reads deterministic `heldout_oracle_manifest.tsv` plus observed boundary/area result rows and writes `heldout_oracle_results.tsv` with source artifact SHA. It does not run RAW, generate reviewed oracle rows, mutate matrices, or authorize non-standard peaks.
- Validation: `python -m pytest tests\test_standard_peak_shadow_activation_inputs.py::test_standard_peak_heldout_oracle_results_cli_writes_contract_tsv tests\test_standard_peak_shadow_activation_inputs.py::test_standard_peak_heldout_oracle_results_classify_boundary_and_area -q`; touched-file ruff passed
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows and a bounded 85RAW expected-diff gate.
  Later `standard_peak_heldout_trace_reintegration_oracle_v1` supplies a
  high-signal clean heldout slice; broad activation still needs scope/expected
  diff acceptance.

### 2026-06-17 - standard_peak_seed_guard_realdata_85raw_artifact_v1

- Previous tier: `production_candidate` for standard-path seed guard with synthetic/focused coverage and executable heldout-oracle CLI
- New tier: still `production_candidate`; real-data artifact evidence now covers seed guard/write attribution on one existing 85RAW chunk without rerunning RAW
- Evidence: `output/productization_realdata_seed_guard_85raw_20260617/r1_120_no_raw_productization/standard_peak_backfill_productization_summary.json`; `standard_peak_activation_inputs/seed_guard_decisions.tsv`; `activated_matrix/activation_value_delta.tsv`
- Product surface changed: none beyond the existing explicit productization bridge. The run reused `output/standard_peak_backfill_preset_85raw_20260610/alignment_preset_dna_dr_85raw_validation_minimal` pre-backfill matrix, identity, review, and chunk `r1_120` shadow projection; it did not open RAW files, rewrite workbook schema, or enable non-standard promotion.
- Validation: `uv run python -m tools.diagnostics.standard_peak_backfill_productization ... --source-run-id seed-guard-realdata-85raw-r1-120-20260617`; status `pass`; 2540 seed-guard candidate rows; 1160 `eligible_continue_existing_gates` rows; 1380 `blocked_low_seed_support` rows; `activation_value_delta.tsv` has 1160 rows and summary reports `matrix_cells_written=1160`.
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows and a bounded expected-diff acceptance that
  explicitly approves the 85RAW product value changes. Later high-signal clean
  heldout trace evidence narrows, but does not remove, the broad activation
  scope blocker.

### 2026-06-17 - standard_peak_seed_guard_realdata_85raw_consolidated_no_raw_v1

- Previous tier: `production_candidate` for standard-path seed guard with synthetic/focused coverage and one existing 85RAW chunk no-RAW bridge
- New tier: still `production_candidate`; real-data artifact evidence now covers seed guard/write attribution on the existing 85RAW consolidated shadow projection without rerunning RAW
- Evidence: `output/productization_realdata_seed_guard_85raw_20260617/consolidated_no_raw_productization/standard_peak_backfill_productization_summary.json`; `standard_peak_activation_inputs/seed_guard_decisions.tsv`; `activated_matrix/activation_value_delta.tsv`
- Product surface changed: none beyond the existing explicit productization bridge. The run reused `output/standard_peak_backfill_preset_85raw_20260610/alignment_preset_dna_dr_85raw_validation_minimal/standard_peak_backfill_preset/consolidated/consolidated_shadow_projection_cells.tsv` plus pre-standard-backfill matrix/identity inputs; it did not open RAW files, rewrite workbook schema, or enable non-standard promotion.
- Validation: `uv run python -m tools.diagnostics.standard_peak_backfill_productization ... --source-run-id seed-guard-realdata-85raw-consolidated-20260617`; elapsed about 6.93 s; status `pass`; 7307 seed-guard candidate rows; 4613 `eligible_continue_existing_gates` rows; 2694 `blocked_low_seed_support` rows; `activation_value_delta.tsv` has 4613 rows and summary reports `matrix_cells_written=4613`; `write_authority_status` counts are 4613 `cohort_scale_standard_backfill` and 2694 `no_write`, with zero `blocked_unattributed_write`. Subagent reviewer `Popper` found no blocking issue and confirmed the docs keep this as `production_candidate` evidence, not `production_ready` or reviewed-oracle evidence.
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows and a bounded expected-diff acceptance that
  explicitly approves the 85RAW product value changes. Later high-signal clean
  heldout trace evidence narrows, but does not remove, the broad activation
  scope blocker.

### 2026-06-17 - standard_peak_heldout_oracle_source_audit_v1

- Previous tier: `production_candidate` for standard-path seed guard with no-RAW 85RAW consolidated evidence
- New tier: still `production_candidate`; `production_ready` checkpoint is explicitly blocked on independent observed boundary evidence
- Evidence: `output/productization_realdata_seed_guard_85raw_20260617/heldout_oracle_source_audit/summary.json`; `raw85_manual_verdict_seed_guard_crosswalk.tsv`; source manual verdicts under `output/backfill_peakhypothesis_promotion_8raw_20260608/raw85_hypothesis_manual_review_top14_user_standard/`
- Product surface changed: none. This is a source audit only; it did not open RAW, generate heldout oracle manifest/results, mutate matrices, or promote non-standard peaks.
- Validation: no existing `heldout_oracle_manifest.tsv`, observed oracle TSV, or `heldout_oracle_results.tsv` was found under `output/`. The audit reviewed 11 raw85 manual verdict rows and 11 matching review-queue rows: all 11 have oracle boundary/area source values, but only 2 match current consolidated seed-guard candidate keys; those 2 have observed area through `activation_value_delta.tsv` but lack an independent observed start/end boundary artifact. The remaining 9 rows do not match the current seed-guard candidate keys.
- Remaining blocker: provide or generate `observed_start_rt`, `observed_end_rt`, and `observed_area` for matched reviewed cases from an independent implementation result source; the observed-result provenance contract is defined in the following log entry and must be satisfied before the oracle gate can count for production readiness.

### 2026-06-17 - standard_peak_heldout_oracle_observed_provenance_contract_v1

- Previous tier: `production_candidate` for standard-path seed guard with no-RAW 85RAW consolidated evidence and source-audit blocker
- New tier: still `production_candidate`; observed-result contract gap is closed.
  At this checkpoint no reviewed observed rows had been generated yet; later
  `standard_peak_heldout_trace_reintegration_oracle_v1` supplies a high-signal
  clean independent reintegration slice.
- Evidence: `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`; `tools/diagnostics/standard_peak_heldout_oracle_results.py`; `tests/test_standard_peak_shadow_activation_inputs.py`; current owners `docs/product/backfill.md` and `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance
- Product surface changed: `heldout_observed_results.tsv` now requires observed provenance columns: `observed_result_source`, `observed_boundary_source`, `observed_area_source`, and `observed_independence_basis`. Allowed independence bases are `product_writer_observed_result`, `masked_rerun_observed_result`, and `independent_boundary_reintegration_result`; oracle/manual/review-queue source copies fail closed after source-label canonicalization. `heldout_oracle_results.tsv` now carries those provenance columns forward for auditability, and `result_source_artifact_path` must exist so the output SHA cannot be blank. No RAW, matrix writer, workbook schema, default extraction, or non-standard peak policy changed.
- Validation: red/green focused tests proved missing provenance, oracle/manual/review-queue source-copy variants, and missing result-source artifact rows failed after implementation; `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py -k heldout_oracle -q` (`20 passed` at that checkpoint; later `25 passed` after original-cell-status and source-cross-check guards); `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`37 passed` at that checkpoint; later `42 passed` after the same guards); latest full local gate after the source-cross-check guard also passed: ruff, mypy, `pytest -v --tb=short -x` (`3698 passed, 1 skipped`), diagnostics index, and `git diff --check` with LF/CRLF warnings only. Subagent reviewer `Gibbs` found the source-label and missing-artifact gaps; both were fixed.
- Remaining blocker: production-ready still needs observed start/end/area rows for reviewed heldout cases from an allowed independent source, plus bounded 85RAW expected-diff approval.

### 2026-06-17 - standard_peak_heldout_original_detected_guard_v1

- Previous tier: `production_candidate` for standard-path seed guard with observed-result provenance contract
- New tier: still `production_candidate`; invalid reviewed/rescued rows are now blocked from serving as heldout oracle product-readiness evidence
- Evidence: `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`; `tests/test_standard_peak_shadow_activation_inputs.py`; current owners `docs/product/backfill.md` and `docs/product/quant-matrix.md`; retired backfill integration policy spec provenance; exploratory read of existing trace artifacts for `FAM002634 / Breast_Cancer_Tissue_pooled_QC3` and `FAM017068 / Breast_Cancer_Tissue_pooled_QC5`
- Product surface changed: `heldout_oracle_manifest.tsv` now requires `heldout_original_cell_status`. Accepted values are `detected`, `detected_seed`, `quantifiable_detected`, and `accepted_detected`; `rescued` and unknown statuses fail before result evaluation. No RAW, matrix writer, workbook schema, default extraction, or non-standard peak policy changed.
- Validation: red/green focused test `test_standard_peak_heldout_oracle_results_requires_original_detected_cell_status`; `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py -k heldout_oracle -q` (`21 passed` at that checkpoint; later `25 passed` after the observed-source cross-check guard); `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`38 passed` at that checkpoint; later `42 passed` after the observed-source cross-check guard)
- Remaining blocker at this checkpoint: production-ready needed true originally
  detected heldout cases, not the current two matched reviewed rows, plus
  independent observed start/end/area rows and bounded 85RAW expected-diff
  approval. Later high-signal clean heldout trace evidence supplies that slice;
  broad activation still needs scope/expected-diff acceptance.

### 2026-06-17 - standard_peak_heldout_observed_source_crosscheck_v1

- Previous tier: `production_candidate` for standard-path seed guard with observed-result provenance and original-cell-status guard
- New tier: still `production_candidate`; the heldout oracle gate is harder to
  spoof. At this checkpoint the valid observed heldout artifact was still
  pending; later `standard_peak_heldout_trace_reintegration_oracle_v1` supplies
  a high-signal clean independent reintegration slice.
- Evidence: subagent reviewer `Ptolemy` found that neutral observed source labels could still copy the manifest `oracle_source`; fix landed in `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs` plus tests
- Product surface changed: fail-closed validation only. Observed provenance source labels are still additive TSV inputs, but now each observed row is validated against its matching manifest row and `observed_result_source`, `observed_boundary_source`, or `observed_area_source` cannot canonicalize to the same label as manifest `oracle_source`. Source-label canonicalization also collapses repeated punctuation/whitespace separators. No RAW, matrix writer, workbook schema, default extraction, or non-standard peak policy changed.
- Validation: red/green neutral oracle-source self-copy test; original-cell-status allowlist/blank/unknown coverage; `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py -k heldout_oracle -q` (`25 passed`); `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`42 passed`); touched-file ruff/mypy passed.
- Remaining blocker at this checkpoint: production-ready still needed true
  originally detected heldout cases with independent observed start/end/area
  rows and bounded 85RAW expected-diff approval. Later high-signal clean
  heldout trace evidence supplies that slice; broad activation still needs
  scope/expected-diff acceptance.

### 2026-06-17 - standard_peak_oracle_gate_review_hardening_v1

- Previous tier: `production_candidate` for standard-path seed guard with executable oracle gate and no-RAW 85RAW artifact bridge
- New tier: still `production_candidate`; review blockers were fixed without expanding product authority
- Evidence: subagent reviewer `Peirce` found heldout-oracle manifest schema enforcement gaps, duplicate/extra observed result ambiguity, and missing hard failure for `blocked_unattributed_write`; reviewer `Euclid` found a non-blocking schema-version value hardening gap; reviewer `Noether` found a package-evaluator direct-call required-column hardening gap. Fixes landed in `tools/diagnostics/standard_peak_heldout_oracle_results.py`, `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`, `xic_extractor.diagnostics.standard_peak_backfill_productization`, and focused tests.
- Product surface changed: fail-closed behavior only. Heldout oracle CLI and package evaluator now require the full spec manifest schema and manifest schema-version value, reject duplicate/stale observed oracle rows, and productization now reports `status=fail` if post-apply seed-guard attribution marks `blocked_unattributed_write`.
- Validation: new red/green tests plus `uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`27 passed` after tolerance-ceiling and exact-threshold tests); focused ruff/mypy passed; no-RAW 85RAW artifact bridge rerun still `pass` with 1160 writes and zero `blocked_unattributed_write`.
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows and a bounded expected-diff acceptance that
  explicitly approves the 85RAW product value changes. Later high-signal clean
  heldout trace evidence narrows, but does not remove, the broad activation
  scope blocker.

### 2026-06-17 - product_decision_backfill_oracle_and_nlfail_limited_optin_v1

- Previous tier: `production_candidate` for standard-path seed guard and targeted MS1 shape explicit opt-in
- New tier: unchanged; product decisions accepted, runtime defaults not changed
- Evidence: user accepted Backfill heldout oracle first-gate tolerance of boundary error `<=0.1 min` and area relative error `<=10%`; user also accepted `NL_FAIL/NO_MS2` limited opt-in first scope as `5-hmdC + 5-medC` only, with product output limited to `detected_flagged`
- Product surface changed: docs/control-plane decision only. No default extraction, GUI rescue, matrix writer, workbook schema, or primary matrix behavior changed in this entry.
- Validation: docs-only decision record; prior gates remain `ruff`, `mypy`, full `pytest`, diagnostics index, `git diff --check`, and no-RAW 85RAW bridge.
- Remaining blocker: Backfill still needs real/reviewed heldout oracle rows generated under the accepted tolerance plus bounded 85RAW expected-diff approval. `NL_FAIL` limited opt-in still needs implementation evidence before it can be treated as no-flag default rescue.

### 2026-06-17 - standard_peak_oracle_tolerance_ceiling_v1

- Previous tier: `production_candidate` for standard-path seed guard and executable heldout oracle result gate
- New tier: still `production_candidate`; oracle manifest guard now enforces the accepted first-gate tolerance ceiling
- Evidence: `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`; `tests/test_standard_peak_shadow_activation_inputs.py`
- Product surface changed: fail-closed gate only. Heldout oracle manifest rows may specify stricter tolerance, but values looser than boundary error `0.1 min` or area relative error `0.10` now raise before result evaluation. No default extraction, matrix writer, workbook schema, or non-standard promotion changed.
- Validation: `uv run pytest tests\test_standard_peak_shadow_activation_inputs.py::test_standard_peak_heldout_oracle_results_rejects_loose_tolerances tests\test_standard_peak_shadow_activation_inputs.py::test_standard_peak_heldout_oracle_results_accepts_strict_tolerances -q` (`2 passed`); `uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`25 passed`)
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows under this ceiling and bounded 85RAW
  expected-diff approval. Later high-signal clean heldout trace evidence
  narrows, but does not remove, the broad activation scope blocker.

### 2026-06-17 - standard_peak_oracle_exact_threshold_float_fix_v1

- Previous tier: `production_candidate` for standard-path seed guard and heldout oracle tolerance ceiling
- New tier: still `production_candidate`; subagent review blocker fixed
- Evidence: reviewer `Linnaeus` identified exact-threshold float comparison risk; `xic_extractor.diagnostics.standard_peak_shadow_activation_inputs`; `tests/test_standard_peak_shadow_activation_inputs.py`
- Product surface changed: comparison helper only. Mathematically exact boundary error `0.1 min` and area relative error `0.10` are now accepted despite binary float representation; values meaningfully above the ceiling still fail. No runtime default, matrix writer, workbook schema, or non-standard promotion changed.
- Validation: exact-boundary tests `2 passed`; `uv run pytest tests\test_standard_peak_shadow_activation_inputs.py tests\test_standard_peak_backfill_productization.py -q` (`27 passed`)
- Remaining blocker at this checkpoint: production-ready still needed
  real/reviewed heldout oracle rows under this ceiling and bounded 85RAW
  expected-diff approval. Later high-signal clean heldout trace evidence
  narrows, but does not remove, the broad activation scope blocker.

### 2026-06-17 - standard_peak_heldout_trace_reintegration_oracle_v1

- Previous tier: `production_candidate` for broad standard-path seed guard with
  an executable heldout oracle gate but no valid originally detected observed
  rows.
- New tier: still `production_candidate` for broad standard-path activation;
  bounded high-signal clean standard trace heldout oracle evidence now passes.
- Evidence:
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle/summary.json`;
  `heldout_oracle_manifest.tsv`; `heldout_observed_results.tsv`;
  `heldout_oracle_results.tsv`;
  `heldout_trace_reintegration_full_eligible_pool.tsv`; source 85RAW
  validation-minimal trace/evidence artifacts under
  `output/standard_peak_backfill_preset_85raw_20260610/alignment_preset_dna_dr_85raw_validation_minimal/`.
- Product surface changed: none. The artifact reuses existing stored 85RAW trace
  arrays and existing package helpers (`find_peak_and_area` with
  `resolver_mode=local_minimum`, `integration_from_peak_trace` with AsLS /
  gaussian15 morphology area). It does not open RAW, mutate matrices, rewrite
  workbook schema, enable default rescue, or authorize non-standard promotion.
- Validation: deterministic selection produced 20 originally detected,
  sample-local, high-signal clean standard trace cases across 20 families
  (`cell_height>=2e6`, width 0.30-0.65 min, shape similarity >=0.95,
  local/global max ratio >=0.95, apex within 0.15 min of family center, at
  least 10 scans, one case per family). The formal heldout oracle CLI wrote
  20 rows; all 20 have `oracle_case_status=pass` and
  `included_in_product_acceptance=TRUE`. Maximum boundary error is 0.0820502
  min and maximum area relative error is 0.0762325, inside the accepted
  `0.1 min / 10% area` ceiling. The full eligible pool artifact persists all
  80 pre-observed eligible rows with quality rank, selected flag, and rejection
  reason; unselected rows do not carry observed reintegration outcomes.
- Remaining blocker: this evidence supports only the high-signal clean standard
  trace slice. The existing consolidated productization bridge still emits
  4613 eligible activation writes and is not scoped by those high-signal clean
  eligibility thresholds. To claim broad `production_ready`, either narrow the
  activation contract to this eligible scope and run an expected-diff
  acceptance, or generate a broader masked/product-writer observed oracle that
  covers the full activation scope.

### 2026-06-17 - standard_peak_activation_scope_audit_v1

- Previous tier: `production_candidate` for broad standard-path seed guard with
  passing high-signal clean heldout trace oracle, but no quantified bridge from
  that oracle envelope to the actual consolidated activation writes.
- New tier: still `production_candidate` for broad 4613-row standard-path
  activation; explicit high-signal clean scoped activation is now a bounded
  product-decision candidate, not an implicit broad ready claim.
- Evidence:
  `tools/diagnostics/standard_peak_activation_scope_audit.py`;
  `xic_extractor/diagnostics/standard_peak_activation_scope_audit.py`;
  `output/productization_realdata_seed_guard_85raw_20260617/high_signal_clean_activation_scope_audit/activation_high_signal_clean_scope_summary.json`;
  `activation_high_signal_clean_scope_audit.tsv`;
  `eligible_activation_value_delta.tsv`.
- Product surface changed: none. The audit reads existing
  `activation_value_delta.tsv`, consolidated
  `consolidated_shadow_projection_cells.tsv`, and sibling
  `*_trace_data.json` artifacts. It does not open RAW, mutate matrices, rewrite
  workbooks, change default extraction, or enable non-standard promotion.
- Validation: focused TDD test
  `uv run pytest tests\test_standard_peak_activation_scope_audit.py -q`
  passed. The real no-RAW audit found 4613 written activation rows, all joined
  to projection rows; 3526 joined to trace JSON, 1087 had missing overlay path,
  72 were high-signal clean eligible, 3454 were trace-matched but outside the
  high-signal clean envelope, and broad scope status is `not_ready`.
- Remaining blocker: choosing the first release product scope. To claim
  `production_ready`, either make the activation contract explicitly write only
  the 72 high-signal-clean eligible rows and run expected-diff acceptance, or
  produce a broader masked/product-writer observed oracle for the full 4613-row
  activation scope.
- Next checkpoint: ask the product-scope decision before changing matrix-writing
  behavior; do not silently convert the 4613-row broad bridge into a 72-row
  product output.

### 2026-06-17 - standard_peak_narrow_expected_diff_acceptance_v1

- Previous tier: `production_candidate` for broad standard-path seed guard;
  high-signal-clean 72-row subset was quantified but did not yet have a
  delta-level expected-diff acceptance gate.
- New tier: still `production_candidate` for broad 4613-row standard-path
  activation. The 72-row high-signal-clean subset now has passing narrow
  expected-diff acceptance, but it is not product output until a writer contract
  explicitly limits activation to that scope.
- Evidence:
  `output/productization_realdata_seed_guard_85raw_20260617/high_signal_clean_activation_scope_audit/narrow_activation_expected_diff_acceptance.json`;
  `narrow_activation_expected_diff_acceptance.tsv`;
  `eligible_activation_value_delta.tsv`.
- Product surface changed: none. The gate reads existing activation delta,
  scope audit, and filtered eligible delta. It writes only diagnostic acceptance
  artifacts and records `product_surface_changed=FALSE`.
- Validation: focused tests
  `uv run pytest tests\test_standard_peak_activation_scope_audit.py -q` now
  pass 5 tests, including provenance-SHA join and fail-closed non-eligible
  delta coverage. Real no-RAW acceptance on the 85RAW consolidated artifact
  reports `acceptance_status=pass`, `full_written_delta_row_count=4613`,
  `eligible_audit_row_count=72`, `eligible_delta_row_count=72`,
  `duplicate_delta_key_count=0`, `missing_delta_row_count=0`,
  `unexpected_delta_row_count=0`, `non_eligible_delta_row_count=0`,
  `not_written_delta_row_count=0`, `unchanged_delta_row_count=0`, and
  `blank_activated_value_count=0`. Closeout gates also passed:
  `uv run ruff check xic_extractor tests tools scripts`,
  `uv run mypy xic_extractor` (`343 source files`),
  `uv run pytest -v --tb=short -x` (`3704 passed, 1 skipped`),
  `uv run python scripts\check_diagnostics_index.py` (`87 entry points, 166
  total files`), and `git diff --check` with only LF/CRLF warnings.
- Remaining blocker: product-scope decision and writer contract. If the first
  release uses the 72-row scope, the writer must be explicitly narrowed and
  gated before any formal matrix output is produced. If the release uses broad
  4613-row scope, this acceptance is insufficient and a broader oracle is still
  required.
- Next checkpoint: choose narrow writer contract versus broad oracle. Do not
  rerun RAW unless it will decide that checkpoint.

### 2026-06-17 - standard_peak_narrow_product_writer_v1

- Previous tier: 72-row high-signal-clean Backfill subset had passing
  delta-level expected-diff acceptance, but no product writer was limited to
  that scope. Broad 4613-row standard-path activation remained
  `production_candidate`.
- New tier: `production_ready` for the explicit 72-row high-signal-clean scoped
  writer; broad 4613-row standard-path activation remains
  `production_candidate`.
- Evidence:
  `output/productization_realdata_seed_guard_85raw_20260617/narrow_high_signal_clean_no_raw_productization/standard_peak_backfill_productization_summary.json`;
  `narrow_product_writer_expected_diff_acceptance.json`;
  `activated_matrix/activation_value_delta.tsv`.
- Product surface changed: additive opt-in CLI surface
  `standard_peak_backfill_productization.py --high-signal-clean-activation-scope-audit-tsv`.
  It filters productization input to audit rows with
  `high_signal_clean_status=eligible`, then writes the existing matrix-only
  activated-matrix output under the task output directory. It does not change
  default extraction, workbook schema, GUI behavior, non-standard promotion, or
  the broad activation bridge. The additive summary fields bump the
  productization summary schema to `standard_peak_backfill_productization_v1`;
  reviewer `Mill` found the missing schema bump as P2, and the fix was verified
  with a focused schema assertion plus a rerun of the narrow no-RAW artifact.
- Validation: focused tests
  `uv run pytest tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py -q`
  passed 10 tests at implementation checkpoint. Touched-file ruff and mypy
  passed. The real no-RAW 85RAW consolidated artifact run selected 72 eligible
  shadow rows, wrote 72 matrix cells and 72 activation-delta rows, and
  `narrow_product_writer_expected_diff_acceptance.json` reports
  `acceptance_status=pass`, `readiness_tier=production_ready`,
  `eligible_audit_row_count=72`, `product_written_delta_row_count=72`,
  `duplicate_delta_key_count=0`, `missing_delta_row_count=0`,
  `unexpected_delta_row_count=0`, `non_eligible_delta_row_count=0`,
  `not_written_delta_row_count=0`, `unchanged_delta_row_count=0`, and
  `blank_activated_value_count=0`.
- Remaining blocker: none for the 72-row narrow release slice. Broad 4613-row
  activation still needs broader masked/product-writer observed oracle evidence
  before it can claim `production_ready`.
- Next checkpoint: preserve the 72-row scope as an explicit release claim; do
  not broaden the writer without a new expected-diff/oracle packet.

### 2026-06-17 - standard_peak_low_scan_trace_oracle_v1

- Previous tier: broad 4613-row Backfill activation remained
  `production_candidate`; 72-row high-signal clean scoped writer was
  `production_ready`.
- New tier: still `production_candidate` for broad activation; low-scan clean
  trace evidence now has a passing held-out oracle packet.
- Evidence:
  `xic_extractor/diagnostics/standard_peak_heldout_trace_oracle.py`;
  `tools/diagnostics/standard_peak_heldout_trace_oracle.py`;
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_low_scan_clean_probe/summary.json`;
  `heldout_oracle_results.tsv`.
- Product surface changed: additive diagnostic CLI that builds reproducible
  held-out trace oracle packets from existing detected cell evidence and stored
  trace JSON. It does not open RAW, mutate matrices, change workbook schema,
  authorize non-standard peaks, or broaden the Backfill writer by itself.
- Validation: focused test
  `uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  passed. The real no-RAW 85RAW low-scan run found 56 eligible candidates
  across 11 families, selected 11 cases, and `heldout_oracle_results.tsv`
  reports 11/11 `pass`, 11/11 `included_in_product_acceptance=TRUE`, max
  boundary error `4.86717e-05` min, and max area relative error `0.038786`
  under the accepted `0.1 min / 10% area` ceiling.
- Remaining blocker: this oracle only supports the explicit low-scan clean
  trace class; a writer scope and expected-diff packet are still required
  before product claims.
- Next checkpoint: connect low-scan clean eligibility to activation scope audit
  and a scoped product writer.

### 2026-06-17 - standard_peak_low_scan_scoped_writer_v1

- Previous tier: low-scan clean Backfill trace class had a passing held-out
  oracle but no product writer limited to that scope.
- New tier: `production_ready` for the explicit 42-row low-scan-clean scoped
  writer; broad 4613-row standard-path activation remains
  `production_candidate`.
- Evidence:
  `output/productization_realdata_seed_guard_85raw_20260617/high_signal_clean_activation_scope_audit/activation_high_signal_clean_scope_summary.json`;
  `low_scan_clean_activation_expected_diff_acceptance.json`;
  `output/productization_realdata_seed_guard_85raw_20260617/narrow_low_scan_clean_no_raw_productization/standard_peak_backfill_productization_summary.json`;
  `narrow_product_writer_expected_diff_acceptance.json`;
  `activated_matrix/activation_value_delta.tsv`.
- Product surface changed: additive opt-in CLI surface
  `standard_peak_backfill_productization.py --low-scan-clean-activation-scope-audit-tsv`.
  It filters productization input to audit rows with
  `low_scan_clean_status=eligible` and writes the existing matrix-only
  activated-matrix output under the task output directory. Only one scoped
  audit flag may be supplied at a time. No default extraction, workbook schema,
  GUI behavior, non-standard promotion, or broad activation bridge changed.
- Validation: focused tests
  `uv run pytest tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py tests\test_standard_peak_heldout_trace_oracle.py -q`
  passed 19 tests after fail-closed scope hardening; touched-file ruff/mypy
  passed. The real combined-scope
  audit found 42 low-scan clean eligible writes out of 4613 and
  `low_scan_clean_activation_expected_diff_acceptance.json` reports
  `acceptance_status=pass`, 42/42 eligible rows, and zero duplicate, missing,
  unexpected, non-eligible, non-written, unchanged, or blank-value rows. The
  real no-RAW scoped writer selected 42 shadow rows, wrote 42 matrix cells and
  42 activation-delta rows, and `narrow_product_writer_expected_diff_acceptance.json`
  reports `acceptance_status=pass`, `readiness_tier=production_ready`,
  `expected_scope=low_scan_clean_eligible_activation_rows`, and zero blockers.
- Remaining blocker: none for the explicit 42-row low-scan release slice. The
  old broad-scope expansion language is historical and superseded by the later
  `park_broad_backfill` decision.
- Next checkpoint: superseded. Later low-height, apex-delta, and width-only
  probes all failed closed, and broad auto-write is now parked; do not use this
  entry to justify another scoped writer.

### 2026-06-17 - standard_peak_low_scan_review_fix_v1

- Previous tier: explicit 42-row low-scan-clean scoped writer was
  `production_ready`, with broad 4613-row standard-path activation still
  `production_candidate`.
- New tier: unchanged; the 42-row low-scan-clean scoped writer remains
  `production_ready`, and broad 4613-row activation remains
  `production_candidate`.
- Evidence: subagent reviewers found no P1/P2 issue. Review feedback identified
  P3 docs drift where the control-plane intro and spec residual-blocker text
  still emphasized only the 72-row high-signal slice. The docs now explicitly
  name both release-ready slices: 72-row high-signal clean and 42-row low-scan
  clean. Focused tests now cover the low-scan scope writer's fail-closed paths:
  multiple scope audit flags, no eligible low-scan rows, duplicate eligible
  audit SHA, audit SHA missing from shadow projection, and duplicate shadow
  projection SHA.
- Product surface changed: none. This is docs/test hardening only; no default
  extraction, workbook schema, GUI behavior, non-standard promotion, matrix
  identity, selected peak, selected area, or broad activation behavior changed.
- Validation: `uv run pytest tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py tests\test_standard_peak_heldout_trace_oracle.py -q`
  (`19 passed`); `uv run ruff check xic_extractor tests tools scripts`
  (pass); `uv run mypy xic_extractor` (pass, 346 source files);
  `uv run pytest -v --tb=short -x` (`3721 passed, 1 skipped`);
  `uv run python scripts\check_diagnostics_index.py`
  (`88 entry points, 167 total files`); `git diff --check` (no whitespace
  errors; Windows LF/CRLF warnings only).
- Remaining blocker: none for the explicit 42-row low-scan release slice. Broad
  4613-row activation still needs additional named evidence classes and
  expected-diff approval before broad `production_ready`.
- Next checkpoint: choose one additional evidence-sufficient class, produce or
  reuse a matching held-out oracle packet, then connect it through activation
  scope audit and scoped writer expected-diff before adding more automatic
  writes.

### 2026-06-17 - standard_peak_low_height_probe_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; the next proposed single-blocker class
  was height-only or apex-delta-only.
- New tier at this checkpoint: low-height clean was `production_candidate` only.
  The explicit
  72-row high-signal-clean and 42-row low-scan-clean scoped writers remained
  `production_ready`; the broad-scope statement is superseded by the later
  `park_broad_backfill` decision.
- Evidence:
  `tools/diagnostics/standard_peak_heldout_trace_oracle.py` now accepts
  `--target-shape-class standard_low_height_clean_trace`, where all clean
  thresholds remain in force except cell height must be below `2e6`.
  The no-RAW 85RAW heldout packet under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_low_height_clean_probe/`
  found 230 eligible candidate rows across 54 families and selected 20
  family-representative cases. It failed closed with 19/20 pass and one
  `fail_boundary` case:
  `HOLDOUT85TRACE001_FAM008651_TumorBC2312_DNA`, boundary error
  `1.16445 min`, area relative error about `0.033`.
- Additional audit evidence:
  `standard_peak_activation_scope_audit.py` now writes
  `low_height_clean_activation_value_delta.tsv` and
  `low_height_clean_activation_expected_diff_acceptance.tsv/json`. The real
  combined audit found 57 low-height clean eligible writes out of 4613; the
  diagnostic expected-diff packet passed 57/57 with zero duplicate, missing,
  unexpected, non-eligible, non-written, unchanged, or blank rows, and
  `product_surface_changed=FALSE`.
- Product surface changed: no matrix writer was added for low-height; no
  default extraction behavior, workbook schema, GUI behavior, non-standard
  promotion, matrix identity, selected peak, selected area, or broad activation
  behavior changed.
- Review: subagent reviewers `Jason` and `Volta` found no P1/P2 blockers. P3
  docs/index wording and CLI labeling gaps were fixed so low-height is visibly
  diagnostic/candidate-only and not product writer approval.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py -q`
  (`22 passed`); `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests tools scripts`
  (pass); `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor` (pass,
  346 source files); `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3724 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts\check_diagnostics_index.py`
  (`88 entry points, 167 total files`); `git diff --check` (no whitespace
  errors; Windows LF/CRLF warnings only). The no-RAW heldout oracle command
  exited `1` by design because the summary status is `fail`; the activation
  scope audit rerun exited `0` and emitted the 57-row expected-diff packet
  above.
- Remaining blocker: superseded by
  `standard_peak_low_height_bounded_reintegration_probe_v1` and
  `standard_peak_low_height_scoped_writer_v1`. Low-height later passed a
  bounded-window oracle packet and the explicit 57-row writer expected-diff
  gate.
- Next checkpoint: see `standard_peak_low_height_scoped_writer_v1`; apex-delta
  and width-only were evaluated after this entry and still failed closed.

### 2026-06-17 - standard_peak_apex_delta_probe_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; apex-delta-only was the next small
  single-blocker class after low-height failed its oracle.
- New tier: apex-delta clean is `production_candidate` only. The explicit
  72-row high-signal-clean and 42-row low-scan-clean scoped writers remained
  `production_ready`; the broad-scope statement is superseded by the later
  `park_broad_backfill` decision.
- Evidence:
  `tools/diagnostics/standard_peak_heldout_trace_oracle.py` now accepts
  `--target-shape-class standard_apex_delta_clean_trace`, where supported trace
  status, shape >=0.95, local/global >=0.95, height >=2e6, width 0.30-0.65 min,
  and at least 10 boundary scans remain required, but apex delta from family
  center must exceed 0.15 min. The no-RAW 85RAW heldout packet under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_apex_delta_clean_probe/`
  found 78 eligible candidate rows across 27 families and selected 20
  family-representative cases. It failed closed with 17/20 pass and three
  `fail_boundary` cases. Max boundary error was `2.19621 min`; max area
  relative error was `0.424518`.
- Product surface changed: no matrix writer was added for apex-delta; no
  activation scope audit columns, default extraction behavior, workbook schema,
  GUI behavior, non-standard promotion, matrix identity, selected peak,
  selected area, or broad activation behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  (`3 passed`); the no-RAW heldout oracle command exited `1` by design because
  the summary status is `fail`.
- Remaining blocker: apex-delta cannot be promoted to `production_ready` until
  a narrower fail-closed evidence rule or a new accepted oracle packet passes
  the `0.1 min / 10% area` gate. Current failures include apex deltas around
  `0.2493` and `0.273`, so a broad threshold-only promotion is not justified.
- Next checkpoint: do not add an apex-delta writer. Width-only was evaluated in
  the following entry and also failed its oracle; remaining single-blocker
  classes should be parked candidate-only unless a stronger evidence model
  exists.

### 2026-06-17 - standard_peak_width_probe_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; width-only was the smallest remaining
  single-blocker class after low-height and apex-delta failed their oracles.
- New tier: width-only clean is `production_candidate` only. The explicit
  72-row high-signal-clean and 42-row low-scan-clean scoped writers remained
  `production_ready`; the broad-scope statement is superseded by the later
  `park_broad_backfill` decision.
- Evidence:
  `tools/diagnostics/standard_peak_heldout_trace_oracle.py` now accepts
  `--target-shape-class standard_width_clean_trace`, where supported trace
  status, shape >=0.95, local/global >=0.95, height >=2e6, apex delta <=0.15
  min, and at least 10 boundary scans remain required, but boundary width must
  fall outside 0.30-0.65 min. The no-RAW 85RAW heldout packet under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_width_clean_probe/`
  found 4 eligible candidate rows across 3 families and selected 3
  family-representative cases. It failed closed with 1/3 pass, one `fail_area`
  case, and one `fail_boundary` case. Max boundary error was `1.86561 min`;
  max area relative error was `0.599229`.
- Product surface changed: no matrix writer was added for width-only; no
  activation scope audit columns, default extraction behavior, workbook schema,
  GUI behavior, non-standard promotion, matrix identity, selected peak,
  selected area, or broad activation behavior changed.
- Review: subagent reviewer `Ohm` found one P2 test-strength issue: the
  initial width-only test only locked the over-wide happy path and not the
  narrow-width branch, inclusive `0.30` / `0.65` boundaries, or dirty
  shape/local-global/height/apex/scan sentinels. This was fixed with a direct
  selector contract test. Subagent reviewer `Hubble` found no P1/P2
  docs/product-claim blocker.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  (`5 passed`); `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests tools scripts`
  (pass); `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor` (pass,
  346 source files); `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3727 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts\check_diagnostics_index.py`
  (`88 entry points, 167 total files`); `git diff --check` (no whitespace
  errors; Windows LF/CRLF warnings only). The no-RAW heldout oracle command
  exited `1` by design because the summary status is `fail`.
- Remaining blocker: width-only cannot be promoted to `production_ready` until
  a narrower fail-closed evidence rule or a new accepted oracle packet passes
  the `0.1 min / 10% area` gate. Current evidence is too weak for a writer
  because only one of three selected family cases passed.
- Next checkpoint: park low-height, apex-delta, and width-only as
  candidate-only. Do not add writers for these single-blocker classes without a
  stronger evidence model or a new passing oracle packet.

### 2026-06-17 - standard_peak_shape_margin_probe_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; high-signal and low-scan scoped writers
  were already `production_ready`; low-height, apex-delta, and width-only probes
  were candidate-only after failing heldout oracles.
- New tier: shape-margin clean is `production_candidate` only. The explicit
  72-row high-signal-clean and 42-row low-scan-clean scoped writers remained
  `production_ready`; the broad-scope statement is superseded by the later
  `park_broad_backfill` decision.
- Evidence:
  `tools/diagnostics/standard_peak_heldout_trace_oracle.py` now accepts
  `--target-shape-class standard_shape_margin_clean_trace`, where supported
  trace status, local/global >=0.95, height >=2e6, boundary width 0.30-0.65
  min, apex delta <=0.15 min, and at least 10 boundary scans remain required,
  but shape similarity is limited to the near-threshold band
  `0.93 <= shape < 0.95`. The no-RAW 85RAW heldout packet under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_shape_margin_clean_probe/`
  found 18 eligible candidate rows across 8 families and selected 8
  family-representative cases. It failed closed with 6/8 pass and two
  `fail_area` cases. Max boundary error was `0.0625542 min`; max area relative
  error was `0.198393`.
- Product surface changed: no matrix writer was added for shape-margin; no
  activation scope audit columns, default extraction behavior, workbook schema,
  GUI behavior, non-standard promotion, matrix identity, selected peak,
  selected area, or broad activation behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  (`7 passed`); focused ruff for the oracle module and test passed. Subagent
  reviewers `Herschel` and `Kuhn` found no P1/P2 blockers. Their P3 findings
  were fixed by adding the shape-margin upper-width exclusion test and aligning
  docs to the summary JSON max area value. The no-RAW heldout oracle command
  exited `1` by design because the summary status is `fail`. Full local gate
  passed: `ruff check xic_extractor tests tools scripts`, `mypy
  xic_extractor`, `pytest -v --tb=short -x` (`3730 passed, 1 skipped`),
  diagnostics index, and `git diff --check`.
- Remaining blocker: shape-margin cannot be promoted to `production_ready`
  until a narrower fail-closed evidence rule or a new accepted oracle packet
  passes the `0.1 min / 10% area` gate. The failures do not reduce to a simple
  shape-threshold tweak because one failed case had shape `0.949526`.
- Next checkpoint: do not add a shape-margin writer. Continue broadening only
  with another named evidence class that can pass heldout oracle and
  expected-diff writer acceptance.

### 2026-06-17 - targeted_ms1_shape_identity_limited_policy_gate_v1

- Previous tier: `production_candidate` for explicit support-TSV workflow only; limited opt-in policy was accepted by user but still missing implementation gate
- New tier: `production_candidate` for explicit/limited opt-in support-TSV workflow; no-flag default rescue remains off
- Evidence: `targeted_ms1_shape_identity_activation_policy`; CLI flag `--targeted-ms1-shape-identity-activation-policy`; `xic_extractor.targeted_ms1_shape_identity_policy`; `xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff`; `tools/diagnostics/targeted_ms1_shape_identity_expected_diff_gate.py`; existing 85RAW gate summary `output/ms1_shape_identity_generic_support_85raw_20260616/limited_default_expected_diff_gate_summary.tsv`
- Product surface changed: additive settings key, additive CLI override, manifest provenance field, and additive expected-diff gate. Replay mode rejects the new runtime override. Default settings stay `explicit_support_tsv`; no GUI, workbook schema, selected-candidate switch, manual boundary, or default automatic support producer changed.
- Safe behavior boundary: limited policy accepts supported rows only for `5-hmdC` and `5-medC`; expected-diff gate requires analyte `NL_FAIL` rows to move from `not_counted/FALSE` to `detected_flagged/TRUE` with `own_max_same_peak_support`; matrix diffs are limited to those target measurement columns and must have the same sample/target key set as the long-row diff.
- Validation: `uv run pytest tests\test_targeted_ms1_shape_identity_expected_diff_gate.py tests\test_extractor_run.py::test_run_limited_shape_identity_policy_without_support_tsv_keeps_output -q` (`7 passed`); existing 85RAW generic artifact gate rerun `pass` with 11 long-row changes and 66 matrix cells; checkpoint full local gate `ruff`, `mypy`, `pytest -v --tb=short -x` (`3693 passed, 1 skipped`), diagnostics index, and `git diff --check` passed. Latest repo full gate after the later Backfill source-cross-check guard is recorded above.
- Remaining blocker: production-ready/default behavior still needs a separate decision and evidence to auto-build or auto-consume supports during normal extraction; GUI remains out of scope; broader targets need their own expected-diff evidence.

### 2026-06-17 - targeted_ms1_shape_identity_support_keyset_gate_v1

- Previous tier: `production_candidate` for explicit/limited opt-in support-TSV workflow.
- New tier: `production_ready` for the headless explicit limited support-TSV workflow only; unflagged normal-extraction rescue remained `blocked` at this checkpoint.
- Evidence: `tools/diagnostics/targeted_ms1_shape_identity_expected_diff_gate.py` now requires `--support-tsv`; `xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff` validates that accepted support TSV sample/target keys exactly match the long-row expected diff; the existing 85RAW generic support artifact still gates at 11 long rows and 66 matrix cells, with 11 supported support-TSV rows and target counts `5-hmdC=10;5-medC=1`.
- Product surface changed: additive CLI gate input and additive summary metrics (`support_tsv_supported_rows`, `support_tsv_target_counts`). No default settings, normal extraction behavior, GUI, workbook schema, selected candidate, area recompute, or target scope changed.
- Validation: `uv run pytest tests\test_targeted_ms1_shape_identity_expected_diff_gate.py -q` (`9 passed` after the later support-required hardening); no-RAW 85RAW artifact gate rerun with `--support-tsv output\ms1_shape_identity_generic_support_85raw_20260616\targeted_ms1_shape_identity_v0.tsv`, status `pass`, `long_changed_rows=11`, `matrix_changed_cells=66`, `support_tsv_supported_rows=11`; latest full local gate after support-required hardening passed with `ruff`, `mypy`, `pytest -v --tb=short -x` (`3707 passed, 1 skipped`), diagnostics index, and `git diff --check`.
- Remaining blocker: unflagged normal-extraction rescue still needed a separate activation/call-cost/product contract before support generation could run without an explicit auto/support flag. Broader targets beyond `5-hmdC + 5-medC` need separate expected-diff evidence.
- Next checkpoint: keep release wording to "headless explicit limited support-TSV workflow" for this entry; do not call no-flag default extraction, GUI, or broader target rescue production-ready.

### 2026-06-17 - targeted_ms1_shape_identity_default_rescue_blocker_audit_v1

- Previous tier: `blocked` for unflagged default `NL_FAIL` rescue; `production_ready` only for the headless explicit limited support-TSV workflow.
- New tier: still `blocked` for unflagged default rescue at this checkpoint; explicit limited support-TSV workflow remains `production_ready`.
- Evidence: user accepted the limited default product direction (`5-hmdC + 5-medC`, `detected_flagged` only), but at this checkpoint the implementation had no automatic support producer or default support input contract. Normal extraction consumed `targeted_ms1_shape_identity_support_tsv` only when explicitly configured, and default settings remained `explicit_support_tsv` with an empty support TSV. A later entry adds an explicit headless auto CLI, but no-flag default behavior remains separate.
- Product surface changed: docs/control-plane wording only. No default settings, normal extraction behavior, GUI, workbook schema, matrix identity, counted detection, selected candidate, or manual boundary behavior changed.
- Validation: focused gates rerun after the audit: `uv run pytest tests\test_targeted_ms1_shape_identity_expected_diff_gate.py -q` (`9 passed` after the later support-required hardening); `uv run pytest tests\test_standard_peak_backfill_productization.py -q` (`5 passed`); `uv run pytest tests\test_provisional_backfill_candidate_gate_cli.py -q` (`8 passed`); `uv run pytest tests\test_settings_new_fields.py tests\test_extractor_run.py::test_run_applies_targeted_ms1_shape_identity_support_tsv tests\test_extractor_run.py::test_run_limited_shape_identity_policy_without_support_tsv_keeps_output tests\test_run_extraction.py::test_cli_passes_targeted_ms1_shape_identity_support_override tests\test_run_extraction.py::test_cli_passes_targeted_ms1_shape_identity_activation_policy_override tests\test_run_extraction.py::test_cli_replay_rejects_targeted_ms1_shape_identity_support_override tests\test_run_extraction.py::test_cli_replay_rejects_targeted_ms1_shape_identity_activation_policy_override tests\test_targeted_ms1_shape_identity_projection.py -q` (`18 passed`); existing 85RAW generic support expected-diff gate rerun with `--support-tsv` still `pass`, 11 long rows, 66 matrix cells, 11 supported support rows.
- Remaining blocker: to promote no-flag default rescue, implement and review an activation/call-cost/default-UX contract for automatically producing or selecting support rows during normal extraction, then provide expected-diff evidence for that default path. Do not promote by merely changing the default activation policy.
- Next checkpoint: design no-flag default activation only if it can be bounded by foreground RAW cost, expected-diff evidence, and a user-readable activation packet.

### 2026-06-17 - targeted_ms1_shape_identity_support_tsv_required_gate_review_fix_v1

- Previous tier: `production_ready` for the headless explicit limited support-TSV workflow, but subagent reviewer `Cicero` found the CLI/package gate still allowed an output-only pass when `--support-tsv` was omitted.
- New tier: still `production_ready` for the same narrow workflow after fail-closed gate hardening; unflagged normal-extraction rescue remained `blocked` at this checkpoint.
- Evidence: `tools/diagnostics/targeted_ms1_shape_identity_expected_diff_gate.py --support-tsv` is now required; `xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff` raises when support rows are missing, so the release gate cannot pass without proving support TSV key-set equality.
- Product surface changed: diagnostic gate contract tightened. No default settings, normal extraction behavior, GUI, workbook schema, matrix identity, counted detection, selected candidate, or manual boundary behavior changed.
- Validation: focused tests added for missing support rows and missing CLI `--support-tsv`; `uv run pytest tests\test_targeted_ms1_shape_identity_expected_diff_gate.py -q` (`9 passed`); focused `ruff` and `mypy` passed; existing 85RAW generic support expected-diff gate rerun with required `--support-tsv` still `pass`; full local gate passed with `ruff`, `mypy`, `pytest -v --tb=short -x` (`3707 passed, 1 skipped`), diagnostics index, and `git diff --check`.
- Remaining blocker: none for the explicit limited release gate. Unflagged normal-extraction rescue still needed a separate activation/call-cost/default-support contract at this checkpoint.
- Next checkpoint: keep any future output-only diff check under a separate `diagnostic_only` name if it is ever needed; do not share the production-ready gate pass wording.

### 2026-06-17 - targeted_ms1_shape_identity_support_tsv_required_spec_drift_fix_v1

- Previous tier: `production_ready` for the headless explicit limited support-TSV workflow after support-required gate hardening.
- New tier: unchanged; no product tier movement.
- Evidence: subagent reviewer `Dirac` found one P3 docs drift in the retired
  shared identity-spine private spec where the gate still used old output-only
  support-gate wording; the current public owner is `docs/product/evidence-spine.md`.
- Product surface changed: docs wording only; the spec now says the gate requires the actual support TSV.
- Validation: `uv run pytest tests\test_targeted_ms1_shape_identity_expected_diff_gate.py -q` (`9 passed`); support-optional wording grep no longer finds Targeted MS1 support-TSV contract drift; `git diff --check` has only LF/CRLF warnings.
- Remaining blocker: none for this docs drift. Unflagged normal-extraction rescue remained blocked separately at this checkpoint.
- Next checkpoint: commit this checkpoint after staging only the reviewed 9-file productization diff.

### 2026-06-17 - targeted_ms1_shape_identity_auto_limited_cli_v1

- Previous tier: `blocked` for automatic `NL_FAIL/NO_MS2` rescue beyond explicit support TSV; `production_ready` only for headless explicit limited support-TSV workflow.
- New tier: `production_ready` for the headless auto-limited CLI workflow; at
  that checkpoint unflagged normal extraction and GUI rescue remained off.
- Evidence: new CLI flag `--targeted-ms1-shape-identity-auto-limited-default`; package support producer `xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer`; auto diff/gate helper `xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff`; 8RAW auto smoke at `output/ms1_shape_identity_auto_limited_8raw_20260617/`; 85RAW auto smoke at `output/ms1_shape_identity_auto_limited_85raw_20260617/`; existing 85RAW no-RAW gate mirror at `output/ms1_shape_identity_auto_limited_existing_85raw_gate_20260617/`.
- Product surface changed: additive CLI flag and additive auto output artifact layout (`baseline/output`, `support/targeted_ms1_shape_identity_v0.tsv`, `final_unverified/output` staging before the gate, `final/output` only after the expected-diff gate passes, expected-diff summaries). At that checkpoint, default settings, no-flag extraction behavior, GUI, workbook schema, selected candidate, manual boundary, and broader target scope did not change.
- Safe behavior boundary: auto workflow always builds support only for `5-hmdC + 5-medC`, applies `limited_5hmdc_5medc_v1`, and fails closed through the same support-TSV key-set expected-diff gate. Accepted changes remain `not_counted/FALSE` to `detected_flagged/TRUE`; clean `detected` is not allowed.
- Validation: focused lint passed for changed files; focused tests passed (`tests/test_targeted_ms1_shape_identity_auto_diff.py`, support builder, projection, expected-diff gate, and new CLI auto tests; latest focused command reported `29 passed`). 8RAW auto real run passed with `1` support row, `1` long-row change, `6` matrix cells. Existing 85RAW artifact no-RAW auto diff gate passed with `11`/`66`. One foreground 85RAW auto run passed with `11` support rows, `11` long-row changes, `66` matrix cells, diagnostics SHA256 unchanged between baseline/final, and wall-clock `369.2 s`.
- Remaining blocker: none for headless limited auto CLI. At this checkpoint,
  broader targets still needed their own evidence, and making auto rescue run
  when the user provides no flag remained a separate product/default UX
  decision. The later `targeted_ms1_shape_identity_no_flag_default_v1` entry
  records that default decision and guard. GUI remains out of scope.
- Next checkpoint: after subagent review and full local gate, commit this slice. Do not rerun 85RAW again for this lane unless support production/projection/matrix semantics change.

### 2026-06-17 - targeted_ms1_shape_identity_auto_limited_review_fix_v1

- Previous tier: `production_ready` for headless auto-limited CLI, pending
  subagent review fixes.
- New tier: unchanged; review blockers fixed.
- Evidence: subagent reviewers found stale handoff wording and a CLI
  fail-closed gap where support/gate failures could traceback and leave
  product-shaped `final/output` CSVs before the expected-diff gate passed.
- Product surface changed: fail-closed CLI behavior only. The auto workflow now
  writes the second extraction to `final_unverified/output`, runs the support
  TSV expected-diff gate there, publishes to `final/output` only after pass, and
  writes the final method manifest after publish. Support/gate/schema failures
  return CLI exit code `2` with a concise stderr message and do not publish
  verified final output.
- Validation: focused auto CLI tests now include gate-failure clean error and
  unpublished-final assertions; targeted auto/support focused suite reports
  `30 passed`. Full local gate is recorded in the handoff after this entry.
- Remaining blocker: none for explicit headless auto-limited CLI. No-flag
  default extraction, GUI, and broader target rescue remain separate decisions.
- Next checkpoint: commit after final subagent acceptance and full local gate.

### 2026-06-17 - targeted_ms1_shape_identity_no_flag_default_v1

- Previous tier: `production_ready` for headless explicit support-TSV workflow
  and explicit auto-limited CLI; no-flag normal extraction remained blocked.
- New tier: `production_ready` for canonical no-flag headless normal CLI
  default, limited to `limited_5hmdc_5medc_v1`, `5-hmdC + 5-medC`, and
  `detected_flagged` product output. GUI and broader targets remain out of
  scope.
- Evidence: canonical settings defaults, `config/settings.example.csv`, and
  `CANONICAL_SETTINGS_DEFAULTS` now default
  `targeted_ms1_shape_identity_activation_policy` to
  `limited_5hmdc_5medc_v1`. `scripts/run_extraction.py` dispatches the same
  auto workflow when the effective config has this policy and no
  `targeted_ms1_shape_identity_support_tsv`; explicit support TSV or
  `explicit_support_tsv` keeps the old manual/normal path. The no-RAW 85RAW
  existing auto artifact gate was rerun at
  `output/ms1_shape_identity_default_no_flag_existing_85raw_gate_20260617/limited_default_expected_diff_gate_summary.tsv`
  and passed with `long_changed_rows=11`, `matrix_changed_cells=66`,
  `support_tsv_supported_rows=11`, `target_counts=5-hmdC=10;5-medC=1`, and
  `matrix_target_counts=5-hmdC=60;5-medC=6`.
- Product surface changed: canonical settings default and config/example
  default changed; no new workbook schema, GUI behavior, selected candidate,
  manual boundary, target scope, or expected-diff semantics changed. The
  default still publishes final output only after the support TSV key-set
  expected-diff gate passes.
- Validation: focused no-flag/default shard
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_settings_new_fields.py tests\test_settings_section.py tests\test_run_extraction.py tests\test_extractor_run.py::test_run_applies_targeted_ms1_shape_identity_support_tsv tests\test_extractor_run.py::test_run_limited_shape_identity_policy_without_support_tsv_keeps_output tests\test_targeted_ms1_shape_identity_projection.py tests\test_targeted_ms1_shape_identity_expected_diff_gate.py tests\test_targeted_ms1_shape_identity_auto_diff.py -q`
  passed `65` tests after review fixes. Focused ruff for changed code/tests
  passed. Subagent reviewers `Kant` and `Socrates` found that manual
  `--targeted-ms1-shape-identity-support-tsv` could inherit the limited default
  instead of preserving the explicit path, and that docs still had stale
  no-flag blocked wording. Both findings were fixed before commit. Full local
  gate passed: `ruff check xic_extractor tests tools scripts`, `mypy
  xic_extractor`, `pytest -v --tb=short -x` (`3728 passed, 1 skipped`), and
  `python scripts\check_diagnostics_index.py`.
- Remaining blocker: none for headless no-flag limited default after final
  review/gate. GUI rescue and any target beyond `5-hmdC + 5-medC` still need
  separate expected-diff evidence and UX/product contract.
- Next checkpoint: historical. Targeted MS1 may broaden only with separate
  evidence for more targets. The Backfill broadening clause is superseded by
  `park_broad_backfill` and must not be used as a current next step.

### 2026-06-17 - backfill_production_gate_research_input_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: unchanged. Explicit 72-row high-signal-clean and 42-row
  low-scan-clean scoped writers remain `production_ready`; broad 4613-row
  standard-path activation plus low-height/apex-delta/width-only/shape-margin
  probes remain `production_candidate`.
- New tier: unchanged; this is design input and product-gate framing, not a
  behavior promotion.
- Evidence: reviewed the retired private Backfill production-gate research
  input; public takeaways now live in `docs/product/backfill.md`. The conclusion is that
  absolute `height >= 2e6` should not become a universal Backfill product hard
  gate because LC-MS height depends on analyte response, matrix effect, batch
  state, local background, and co-elution. The low-height heldout result
  `19/20 pass + 1 boundary fail` points to boundary/reintegration consistency,
  not a proof that low-height cells are globally unusable.
- Product surface changed: docs/control-plane/handoff only. No writer, matrix,
  schema, CLI flag, workbook, RAW artifact, or expected-diff output changed.
- Validation: docs-only targeted grep and `git diff --check` for this refresh.
- Remaining blocker: no new low-height or moderate-height writer may be added
  just because expected-diff is clean. This entry is now background for future
  truth/review/evidence design, not a writer prompt.
- Next checkpoint: superseded by the later park decision. Future work should
  predeclare truth/review/evidence assets before any new writer authority is
  considered.

### 2026-06-17 - product_direction_low_manual_intervention_v1

- Previous tier at the time: unchanged. Backfill narrow 72-row writer was
  `production_ready`; broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; headless Targeted MS1 auto-limited CLI
  was `production_ready`; ReviewAction selected-candidate/manual-boundary apply
  remained parked for this release claim.
- New tier: unchanged; this entry records product direction, not a new behavior
  promotion.
- Evidence: user decision on 2026-06-17: Backfill north star is to fill whenever
  evidence is sufficient, with the 72-row writer as a demonstrator rather than
  a permanent scope cap; `NL_FAIL` limited rescue should move toward automatic
  low-manual operation, initially `5-hmdC + 5-medC` and `detected_flagged`
  only; ReviewAction/reintegration should eventually minimize manual review,
  with the system responsible for alertness and auditability while the user
  reviews only a small number of obvious/representative cases.
- Product surface changed: docs/control-plane wording only. The later
  `targeted_ms1_shape_identity_no_flag_default_v1` entry changes headless
  no-flag CLI default behavior under the bounded `limited_5hmdc_5medc_v1`
  contract; this direction entry itself does not change GUI wiring, workbook
  schema, matrix schema, selected-candidate switch, manual boundary area
  recompute, or broad Backfill write scope.
- Remaining blocker: broad Backfill still needs broader masked/product-writer
  oracle and expected-diff evidence for any added writes beyond the current
  scoped ready slices. Headless no-flag `NL_FAIL` limited rescue is now covered
  by `targeted_ms1_shape_identity_no_flag_default_v1`; GUI rescue and targets
  beyond `5-hmdC + 5-medC` still need separate expected-diff evidence and UX
  contract. ReviewAction mutation still needs stable IDs, sidecar contracts,
  expected-diff approval, and audited apply output before it can write selected
  peak/area/counting changes.
- Next checkpoint: prioritize bounded automation packets that reduce manual
  review without broad silent writes: broaden Backfill evidence by evidence
  class, extend Targeted MS1 only after separate evidence for more targets, and
  reopen ReviewAction mutation only after the ID/expected-diff contract is
  concrete.

### 2026-06-17 - standard_peak_low_height_bounded_reintegration_probe_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: low-height clean Backfill slice was `production_candidate`.
  Activation scope audit had 57 eligible low-height writes and diagnostic
  expected-diff 57/57 pass, but full-trace heldout oracle failed 19/20 because
  `FAM008651/TumorBC2312_DNA` had boundary drift over the accepted `0.1 min`
  ceiling.
- New tier: still `production_candidate`; evidence improved, but no writer
  approval and no `production_ready` claim.
- Evidence: `standard_peak_heldout_trace_oracle.py` now supports diagnostic
  `--observed-reintegration-mode expected_window_bounded` with
  `--expected-window-padding-min`. Default observed reintegration remains
  `full_trace`, so existing oracle behavior is preserved unless the new mode is
  explicit. Existing 85RAW trace artifacts were reused; no RAW files were
  reopened. Low-height bounded replay at `padding=0.1/0.2/0.3 min` still failed
  19/20 because `FAM000883/NormalBC2292_DNA` exceeded the 10% area ceiling, but
  `padding=0.5 min` passed 20/20 with max boundary error `0.0857986 min` and
  max area relative error `0.0564106`.
- Product surface changed: diagnostic CLI and summary fields only:
  `observed_reintegration_mode` and `expected_window_padding_min`. No product
  writer, primary matrix, workbook schema, selected peak/area, counted
  detection, extraction default, or RAW artifact changed.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest
  tests\test_standard_peak_heldout_trace_oracle.py -q` passed `10`. Subagent
  reviewer `Tesla` found no blocking issue and no product overclaim; its P3
  stale-handoff wording and edge-test findings were fixed by clarifying that
  bounded oracle pass does not authorize a writer, adding CLI negative-padding
  coverage, and adding a bounded-mode missing-observed-result fail-closed test.
- Remaining blocker: superseded by
  `standard_peak_low_height_scoped_writer_v1`, which added the explicit
  low-height writer flag and passed the writer expected-diff gate. The bounded
  replay still cannot be silently converted into broad matrix writes.
- Next checkpoint: see `standard_peak_low_height_scoped_writer_v1`.

### 2026-06-17 - standard_peak_low_height_scoped_writer_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: low-height clean was a strong `production_candidate` after
  bounded-window heldout oracle evidence passed selected 20/20, but there was
  no explicit product writer flag or writer expected-diff approval.
- New tier: `production_ready` for the explicit 57-row low-height-clean scoped
  writer only. Broad 4613-row standard-path activation remains
  `production_candidate`; apex-delta, width-only, and shape-margin remain
  candidate-only probes.
- Evidence: `standard_peak_backfill_productization.py` now accepts
  `--low-height-clean-activation-scope-audit-tsv`, which fail-closed filters
  matrix-only activation to audit rows with
  `low_height_clean_status=eligible`. The real no-RAW 85RAW run under
  `output/productization_realdata_seed_guard_85raw_20260617/narrow_low_height_clean_no_raw_productization/`
  reports `status=pass`, `activation_scope_contract=low_height_clean_eligible_activation_rows`,
  57 selected/written rows, and `narrow_product_writer_expected_diff_acceptance.json`
  reports `acceptance_status=pass`, `readiness_tier=production_ready`,
  `product_surface_changed=TRUE`, duplicate/missing/unexpected/non-eligible/
  non-written/unchanged/blank all `0`.
- Product surface changed: additive explicit CLI flag and additive scoped
  writer behavior. No default extraction behavior, workbook schema, broad
  Backfill activation, GUI wiring, selected peak/area, or non-standard peak
  policy changed.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest
  tests\test_standard_peak_backfill_productization.py -q` passed `12`; touched
  ruff for productization package/CLI/test passed; real no-RAW writer command
  exited `0`.
- Remaining blocker: none for the explicit 57-row low-height scoped writer.
  Broad 4613-row activation still needs additional named evidence classes and
  expected-diff approval before it can claim `production_ready`.
- Next checkpoint: request subagent review of this writer promotion, then run
  the combined Backfill focused shard and PR gate before commit.

### 2026-06-17 - standard_peak_low_height_low_scan_scoped_writer_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: low-height-low-scan clean was not a named product slice; broad
  4613-row standard-path activation remained `production_candidate`.
- New tier: `production_ready` for the explicit 69-row
  low-height-low-scan-clean scoped writer only. Broad 4613-row activation
  remains `production_candidate`; apex-delta, width-only, and shape-margin
  remain candidate-only probes.
- Evidence: `standard_peak_heldout_trace_oracle.py` now supports
  `standard_low_height_low_scan_clean_trace`, which requires height <2e6,
  scan count 7-9, shape >=0.95, local/global >=0.95, width 0.30-0.65 min, and
  apex delta <=0.15 min. The formal no-RAW 85RAW bounded oracle under
  `heldout_trace_reintegration_oracle_low_height_low_scan_clean_probe/` found
  210 eligible rows / 51 families, selected 20 family cases, and passed 20/20
  with max boundary error `4.80376e-05 min` and max area relative error
  `0.00881912`. `standard_peak_activation_scope_audit.py` now emits
  `low_height_low_scan_clean_status` and found 69 eligible writes in the
  consolidated 4613-row bridge. `standard_peak_backfill_productization.py` now
  accepts `--low-height-low-scan-clean-activation-scope-audit-tsv`, and the
  real no-RAW writer run under
  `narrow_low_height_low_scan_clean_no_raw_productization/` passed 69/69 with
  `readiness_tier=production_ready`, `product_surface_changed=TRUE`, and
  duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank all
  `0`.
- Product surface changed: additive heldout oracle target class, additive audit
  TSV/summary/expected-diff fields and sidecars, additive explicit CLI flag,
  and additive scoped writer behavior. No default extraction behavior,
  workbook schema, broad Backfill activation, GUI wiring, selected peak/area,
  or non-standard peak policy changed.
- Validation: focused Backfill shard
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py -q`
  passed `39` after the reviewer-requested low-height-low-scan blocker table
  test was added; full PR gate passed with `ruff`, `mypy`, `pytest -v
  --tb=short -x` (`3742 passed, 1 skipped`), diagnostics index, and
  `git diff --check`; formal no-RAW oracle/audit/writer commands exited `0`.
- Remaining blocker: none for the explicit 69-row low-height-low-scan scoped
  writer. Broad 4613-row activation still needs additional named evidence
  classes and expected-diff approval before it can claim `production_ready`.
- Next checkpoint: done for this scoped writer. Subagent reviewer `Parfit`
  found no blocking finding; remaining Backfill work should move to a new named
  evidence class rather than another existing-predicate writer.

### 2026-06-17 - backfill_remaining_scope_evidence_class_blocker_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: broad 4613-row standard-path activation was still
  unresolved under the pre-park framing; direct writer work was tempting because
  four narrow scoped writers had reached `production_ready`.
- New tier at the time: direct promotion from the existing
  high-signal/low-scan/low-height/apex/width/shape predicates was blocked. The
  stronger-evidence-class framing is superseded by the later
  `park_broad_backfill` decision and the authority/adjudication contract.
- Evidence: read-only Backfill subagent review confirmed the current safe
  demonstrators are 72 high-signal, 42 low-scan, 57 low-height, and 69
  low-height-low-scan rows. The broad bridge remains 4613 selected writes out
  of 7307 seed-guard candidates, with 2694 low-seed no-writes and 1087 writes
  missing trace/overlay evidence in the activation scope audit. Existing
  candidate probes are not writer-ready: apex-delta is 17/20 with max boundary
  error `2.19621 min`, width-only is 1/3 with max boundary error `1.86561 min`
  and max area relative error `0.599229`, and shape-margin is 6/8 with max
  area relative error `0.198393`.
- Product surface changed: docs/control-plane and handoff wording only. No CLI,
  schema, matrix, workbook, GUI, or extraction default changed.
- Validation: read-only subagent audits `Galileo` and `Feynman`; artifact
  spot-checks of the committed no-RAW 85RAW summaries; no RAW rerun because
  the existing failed probes already answer the direct-writer decision.
- Remaining blocker: superseded for broad auto-write. These ideas can only be
  background for future truth/review/evidence assets unless a later goal names a
  new independent truth source.
- Next checkpoint: historical. Do not design another broad writer evidence
  class from this entry; use the authority/adjudication index to decide whether
  a row needs review, truth labels, or recovered trace evidence.

### 2026-06-17 - standard_peak_reintegration_stability_audit_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: broad 4613-row standard-path activation was
  `production_candidate`, blocked on a new named evidence class beyond the four
  explicit scoped writers.
- New tier: still `production_candidate` for broad activation, now with a
  boundary-stability / reintegration-agreement candidate pool. No
  `production_ready` writer is claimed.
- Evidence: new package/CLI diagnostic
  `standard_peak_reintegration_stability_audit.py` consumes
  `activation_high_signal_clean_scope_audit.tsv`, reloads referenced trace
  JSON, and reintegrates each written trace twice: full trace and
  expected-window-bounded (`padding=0.5 min`). A row is eligible only if both
  reintegration views match the stored reference boundary within `0.1 min`,
  match area within `10%`, and agree with each other within the same boundary
  and area tolerances. The real no-RAW 85RAW run under
  `output/productization_realdata_seed_guard_85raw_20260617/reintegration_stability_audit/`
  reports 4613 written rows, 299 eligible, 3227 ineligible, and 1087 missing
  trace/overlay evidence. 271 eligible rows are outside the existing four ready
  scoped writer envelopes. The summary is intentionally not pass-like:
  `status=candidate_pool_blocked`, `writer_authority_status=blocked`, and
  `production_ready=FALSE`; it also records the upstream activation scope TSV
  SHA and source run id.
- Product surface changed: additive diagnostic CLI/package and additive
  diagnostic TSV/JSON output only. No writer flag, default extraction behavior,
  workbook schema, broad Backfill activation, GUI wiring, selected peak/area,
  or non-standard peak policy changed.
- Validation: TDD red/green focused tests
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_reintegration_stability_audit.py -q`
  now pass `8`; related Backfill shard
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_reintegration_stability_audit.py tests\test_standard_peak_heldout_trace_oracle.py tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py -q`
  passes `47`; focused ruff and focused mypy passed; subagent reviewer `Hypatia`
  flagged status/provenance/padding fail-closed gaps and they were fixed; real
  no-RAW 85RAW artifact command exited `0` in about 31 seconds.
- Remaining blocker: this is stored-trace self-consistency evidence, not a
  masked product-writer oracle. Before any writer or `production_ready` claim,
  this candidate pool needs masked/product-writer observed results and
  expected-diff approval.
- Next checkpoint: build the masked/product-writer oracle for the 299-row
  candidate pool, or add another independent evidence class before any writer
  claim.

### 2026-06-17 - standard_peak_low_height_reintegration_stable_writer_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: reintegration-stability audit had promoted a 299-row broader
  candidate pool to stronger `production_candidate`, but writer authority was
  blocked because stored-trace self-consistency alone is not product approval.
- New tier: `production_ready` for the explicit 220-row low-height
  reintegration-stable scoped writer only. Broad 4613-row activation and the
  full 299-row all-stability pool remain `production_candidate`.
- Evidence: the all-stability family check was intentionally not promoted; it
  was later formalized as target
  `standard_reintegration_stable_candidate_family_trace` and confirmed one
  `fail_area` case
  (`FAM000949/NormalBC2261_DNA`, area relative error `0.19621`). The promoted
  scope is narrower: stability-eligible written rows whose activation audit
  `cell_height` is below `2e6`. Formal no-RAW 85RAW heldout trace oracle
  target `standard_low_height_reintegration_stable_candidate_family_trace`
  found 220 audit-intersection rows across 66 families, then evaluated detected
  trace candidates from those same families (`match_level=family_id`,
  1520 available candidates), selected 20 family cases, and passed 20/20 with
  max boundary error `0.0830019 min` and max area relative error `0.0725986`.
  The real no-RAW writer under
  `output/productization_realdata_seed_guard_85raw_20260617/narrow_low_height_reintegration_stable_no_raw_productization/`
  selected and wrote 220 cells from the pre-standard-backfill matrix.
  `narrow_product_writer_expected_diff_acceptance.json` reports
  `acceptance_status=pass`, `readiness_tier=production_ready`,
  `expected_scope=low_height_reintegration_stable_eligible_activation_rows`,
  and zero duplicate/missing/unexpected/non-eligible/non-written/unchanged/blank
  blockers. This adds 199 ready cells outside the previous four ready scopes;
  the five-scope ready union is 439 cells.
- Product surface changed: additive CLI/package flags only:
  `standard_peak_heldout_trace_oracle.py --reintegration-stability-audit-tsv`
  / `--activation-scope-audit-tsv` for the family-scope oracle and
  `standard_peak_backfill_productization.py --low-height-reintegration-stable-activation-scope-audit-tsv`
  / `--reintegration-stability-audit-tsv` for the writer. No default
  extraction behavior, workbook schema, GUI, broad Backfill activation,
  selected peak/area, or non-standard peak policy changed.
- Validation: TDD/focused gates passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py::test_heldout_trace_oracle_cli_writes_low_height_stability_family_packet tests\test_standard_peak_heldout_trace_oracle.py::test_low_height_stability_family_scope_requires_scope_inputs -q`
  (`2 passed`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  (`14 passed`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py::test_standard_peak_productization_can_limit_writer_to_low_height_stability_scope tests\test_standard_peak_backfill_productization.py::test_low_height_stability_scope_filters_rows_not_whole_family tests\test_standard_peak_backfill_productization.py::test_standard_peak_productization_rejects_wrong_activation_scope_schema tests\test_standard_peak_backfill_productization.py::test_standard_peak_productization_rejects_wrong_stability_schema -q`
  (`4 passed`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  (`19 passed`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_reintegration_stability_audit.py tests\test_standard_peak_heldout_trace_oracle.py tests\test_standard_peak_activation_scope_audit.py tests\test_standard_peak_backfill_productization.py -q`
  (`52 passed`); focused ruff and focused mypy passed for the touched Backfill
  modules and tests. The real no-RAW oracle and writer commands exited `0`;
  no RAW/85RAW rerun was needed because existing 85RAW trace/productization
  artifacts answered this scope decision. Subagent reviewer `Bernoulli` found
  two blockers before closeout: the oracle wording overclaimed row identity and
  the writer lacked activation-scope schema-version validation. Both were
  fixed. Follow-up reviewer `Avicenna` found no blocking finding; it confirmed
  the claim is now limited to the explicit 220-row writer while all-stability
  299 and broad 4613 remain candidate/blocked.
- Remaining blocker: all-stability direct writer remains blocked by the
  family-check area failure; broad 4613-row activation still needs broader
  masked/product-writer oracle and expected-diff approval. Apex-delta,
  width-only, and shape-margin probes remain blocked by their failed heldout
  oracles.
- Next checkpoint: run the remaining PR gate before commit. The next
  productization slice should broaden by a new evidence class or masked
  product-writer oracle, not by silently widening the new 220-row writer.

### 2026-06-17 - standard_peak_all_stability_family_oracle_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: all-stability 299-row reintegration-stable candidate pool was
  `production_candidate` / writer-blocked based on a quick family check.
- New tier: still `production_candidate` / writer-blocked, now with a formal
  fail-closed oracle artifact. No writer, matrix, workbook, GUI, selected
  peak/area, or default extraction behavior changed.
- Evidence: `standard_peak_heldout_trace_oracle.py` now supports formal target
  class `standard_reintegration_stable_candidate_family_trace`. The real
  no-RAW 85RAW oracle packet under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_all_stability_family/`
  records 299 audit-intersection rows across 77 families, expands to 1694
  available detected trace candidates from those families, selects 20 family
  cases, and reports `status=fail`: 19 pass and 1 `fail_area`.
  `FAM000949/NormalBC2261_DNA` has boundary error `0.0626 min` but area
  relative error `0.19621`, above the accepted `0.10` ceiling. This confirms
  that all-stability rows cannot be promoted directly.
- Validation: focused oracle tests
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_heldout_trace_oracle.py -q`
  passed `16` after reviewer `Franklin` requested the high-height/low-height
  boundary regression test; focused ruff and mypy passed for the touched oracle
  module/test. Docs/evidence reviewer `Archimedes` found no blocker and
  confirmed the artifact counts/status/failing case match the docs. The real
  no-RAW oracle command intentionally exited `1` because the artifact failed
  the product oracle, not because the command crashed.
- Remaining blocker: all-stability direct writer needs a narrower
  product-meaningful evidence class, a different independent oracle, or a
  writer contract that can explain and exclude the failing family before any
  matrix write. Broad 4613-row activation still needs broader generated-policy
  evidence with oracle + expected-diff approval.
- Next checkpoint: do not retry direct all-stability writer work. Either add a
  simpler evidence class that explains the 19 pass / 1 fail split, or move the
  next Backfill expansion through the generated policy engine with a separate
  passing oracle and expected-diff gate.

### 2026-06-17 - standard_peak_generated_backfill_policy_explanation_v2

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: generated policy replay over current approved evidence classes
  was `production_ready`, but blocked/detected rows still relied on coarse
  reason strings and the policy summary did not say what evidence each row
  still needed.
- New tier: remains `production_ready` for generated policy replay of current
  approved evidence classes; the policy explanation contract is now
  `standard_peak_backfill_policy_v2`. No broad 4613-row tier promotion.
- Evidence: `standard_peak_backfill_policy.tsv` now requires
  `backfill_policy_decision_basis`, `backfill_policy_next_evidence`, and
  `backfill_policy_candidate_evidence_class` for every generated row. Missing
  explanation fields fail closed before matrix activation. The real no-RAW
  85RAW replay under
  `output/productization_realdata_seed_guard_85raw_20260617/generated_policy_explained_no_raw_productization/`
  produced 4613 explained policy rows with `0` missing explanation rows:
  439 `write_ready`, 72 `detected_flagged`, and 4102 `blocked`. The
  next-evidence distribution is 439 already current-scope approved, 72 needing
  masked/product-writer oracle, 1087 needing trace overlay or reintegration
  evidence, and 3015 needing a new approved evidence class or passing oracle.
  The writer still wrote exactly 439 matrix cells and expected-diff passed
  439/439 with zero blockers.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  passed with `22 passed`; the no-RAW 85RAW generated-policy explanation replay
  exited `0` in about 2.36 sec.
- Remaining blocker: the explanation contract makes every current candidate
  accountable, but broad 4613, all-stability 299, apex-delta, width-only, and
  shape-margin still need their own oracle/expected-diff evidence before
  writer authority.
- Next checkpoint: add one new product evidence class to policy v2 only when it
  has a passing oracle/expected-diff gate; otherwise leave the row explained as
  blocked or `detected_flagged`.

### 2026-06-17 - standard_peak_policy_observed_oracle_writer_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: generated policy replay was `production_ready` for 439 current
  approved-evidence rows; 72 boundary-stable rows remained
  `detected_flagged` because they lacked a row-specific product-writer oracle.
- New tier: `production_ready` for generated policy replay of current approved
  evidence classes plus `policy_observed_full_trace_reintegration`. No broad
  4613-row tier promotion.
- Evidence: `standard_peak_policy_observed_oracle.py` evaluates generated
  `detected_flagged` rows against their source activation-scope audit row,
  reloads the full stored trace, recomputes peak boundaries/area, and only
  accepts rows that pass `0.1 min / 10% area`, have
  `included_in_product_acceptance=TRUE`, keep the same row SHA/family/sample,
  remain trace matched/written, and are bound by companion summary JSON to the
  oracle TSV SHA, source audit SHA, and base generated-policy SHA. The real
  no-RAW 85RAW oracle packet under
  `output/productization_realdata_seed_guard_85raw_20260617/policy_observed_oracle_detected_flagged_full_trace/`
  passed 72/72 with max boundary error `8.91875e-05 min` and max area relative
  error `0.098218`. The follow-up generated-policy replay under
  `output/productization_realdata_seed_guard_85raw_20260617/generated_policy_policy_observed_oracle_no_raw_productization/`
  classified 4613 source-audit rows as 511 `write_ready`, 0
  `detected_flagged`, and 4102 `blocked`; writer expected-diff passed 511/511
  with `readiness_tier=production_ready` and zero duplicate/missing/unexpected/
  non-eligible/non-written/unchanged/blank blockers.
- Validation: focused TDD red/green for the new oracle producer and product
  policy connection; `uv run pytest tests\test_standard_peak_policy_observed_oracle.py -q`
  passed with `2 passed`; `uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  passed with `29 passed`; focused ruff and mypy passed for the touched
  diagnostics/CLI modules. After subagent review found the bare-TSV allowlist
  gap, the productization consumer now requires
  `--policy-observed-oracle-summary-json` and verifies the oracle/source/base
  policy hashes before promotion. The refreshed no-RAW oracle command exited
  `0` in about 2.08 s and the refreshed no-RAW replay command exited `0` in
  about 3.08 s.
- Remaining blocker at the time: the remaining 4102 blocked rows still needed
  trace overlay / reintegration evidence or another approved evidence class and
  oracle before they could write. The old broad-scope
  `production_candidate` framing is superseded by the 2026-06-18
  `park_broad_backfill` decision.
- Next checkpoint: superseded. Do not use this entry to continue broad
  Backfill broadening; route unresolved rows through authority/adjudication,
  structured review, truth acquisition, or trace-evidence recovery.

### 2026-06-17 - standard_peak_shape_clean_policy_explanation_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: shape-clean reintegration-stable was `production_candidate`
  oracle evidence only; generated policy v2 did not identify it separately from
  generic `reintegration_stable` candidate evidence.
- New tier: still `production_candidate` / explanation evidence only. Generated
  policy replay remains `production_ready` for current approved evidence
  classes, with no broad 4613-row tier promotion and no shape-clean writer
  authority.
- Evidence: `standard_peak_backfill_productization.py` now treats
  `apex_aligned_shape_similarity` as part of the source activation-scope audit
  contract and records `shape_clean_reintegration_stable` in
  `backfill_policy_candidate_evidence_class` when the row is reintegration
  stable, has `matrix_value_effect=written`, is trace matched and family
  matched, and shape similarity is `>=0.95`. This class is never added to
  `backfill_policy_evidence_class` or
  `ready_evidence_classes` by itself. The real no-RAW 85RAW replay under
  `output/productization_realdata_seed_guard_85raw_20260617/generated_policy_shape_clean_explained_no_raw_productization/`
  still classifies 4613 rows as 439 `write_ready`, 72 `detected_flagged`, and
  4102 `blocked`; the writer still selected/wrote 439 cells and
  `backfill_policy_write_ready_rows` expected-diff passed 439/439. Candidate
  evidence distribution now shows 104 rows containing
  `shape_clean_reintegration_stable`: by authority/decision, 30 are
  `review_only` / `detected_flagged` and 74 are already `writer_approved` /
  `write_ready`; by candidate-evidence string, 76 are
  `shape_clean_reintegration_stable,reintegration_stable`, 21 overlap
  `low_height_clean`, and 7 overlap `high_signal_clean`.
- Validation: focused productization tests
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  passed after reviewer fixes; focused ruff and mypy passed for the
  productization module/test; the real no-RAW replay exited `0` in about
  2.45 sec. Subagent review found no blocker: Erdos requested negative guard
  tests for trace/shape fail-closed behavior, and Aquinas found the original
  104-row split wording was wrong; both were fixed. Full local gate passed:
  ruff, mypy, pytest `3767 passed, 1 skipped`, diagnostics index, and
  `git diff --check` with Windows LF/CRLF warnings only.
- Remaining blocker: shape-clean stability is explanation evidence until a
  missing-cell scope or masked/product-writer oracle proves nonzero product
  delta. It must not become a public writer flag or a ready evidence class from
  this change alone.
- Next checkpoint: superseded for broad Backfill. Future work should use
  authority/adjudication, structured review, truth acquisition, or trace
  evidence recovery rather than new nested writer flags.

### 2026-06-17 - standard_peak_shape_clean_reintegration_stable_oracle_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier at the time: shape-clean stability was not a named Backfill
  evidence class; all-stability 299 and broad 4613 were still unresolved /
  writer-blocked under the pre-park framing.
- New tier: `production_candidate` evidence class only. No public writer flag,
  no matrix write, no workbook/schema/GUI/default extraction behavior change.
- Evidence: `standard_peak_heldout_trace_oracle.py` now supports
  `standard_shape_clean_reintegration_stable_candidate_family_trace`, requiring
  reintegration-stability eligible written rows plus activation-scope
  `apex_aligned_shape_similarity >=0.95`. The real no-RAW 85RAW oracle packet
  under
  `output/productization_realdata_seed_guard_85raw_20260617/heldout_trace_reintegration_oracle_shape_clean_reintegration_stable_family/`
  reports `status=pass`, 104 audit-intersection rows / 33 families, 334
  available detected trace candidates / 31 families, selected 20/20 pass, max
  boundary error `0.0830019 min`, and max area relative error `0.0725986`.
  A local temporary writer probe under
  `output/productization_realdata_seed_guard_85raw_20260617/narrow_shape_clean_reintegration_stable_no_raw_productization/`
  was not retained as product code because it failed expected-diff:
  `matrix_cells_written=0`, `product_written_delta_row_count=0`,
  `not_written_delta_row_count=104`, and `unchanged_delta_row_count=104`.
- Validation: real no-RAW oracle command exited `0` in about 7.84 sec. Focused
  oracle tests passed with `19 passed` after reviewer `Sartre` requested an
  explicit missing-`apex_aligned_shape_similarity` fail-closed regression test.
  Docs/evidence reviewer `Heisenberg` found no overclaim blocker and requested
  the WIP-table stop rule now reflected above. Full local gate passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3764 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts\check_diagnostics_index.py`;
  `git diff --check` had no whitespace errors, only Windows LF/CRLF warnings.
- Remaining blocker: this shape-clean class currently proves that already
  detected/pre-existing matrix-value families reintegrate cleanly. It does not
  prove a missing-cell writer effect. To promote it, route it through generated
  policy as explanation-only evidence or build a missing-cell candidate scope
  with nonzero expected-diff and the same oracle/expected-diff acceptance.
- Next checkpoint: do not add a shape-clean public writer flag. The broadening
  clause is superseded; shape-clean evidence can remain explanatory candidate
  context only unless a future authority/truth/expected-diff goal reopens it.

### 2026-06-17 - standard_peak_generated_backfill_policy_path_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: five explicit scoped writers were `production_ready`; broad
  4613-row activation and the all-stability 299-row pool remained
  `production_candidate` / writer-blocked. The implementation path was drifting
  toward more nested scoped writer flags.
- New tier: `production_ready` for generated policy replay of the current
  approved evidence classes. No broad 4613-row tier promotion: rows outside the
  approved evidence classes remain `detected_flagged` or `blocked` until their
  own oracle and expected-diff evidence exists.
- Evidence: `standard_peak_backfill_productization.py` now accepts
  `--backfill-policy-source-audit-tsv`, generates
  `standard_peak_backfill_policy.tsv` plus summary, and classifies each source
  audit row as `write_ready`, `detected_flagged`, or `blocked`. The generated
  policy schema is now `standard_peak_backfill_policy_v2`; every row must carry
  `backfill_policy_decision_basis`, `backfill_policy_next_evidence`, and
  `backfill_policy_candidate_evidence_class`. The CLI does not expose a
  human-authored activation policy TSV, and the public package API does not
  accept `activation_policy_tsv`. `write_ready` rows fail closed unless they are
  written rows with matched trace evidence, a nonblank evidence class, and
  `writer_approved` authority; every policy row fails closed if explanation
  fields are blank. Focused tests prove a four-row broad source audit produces
  one high-signal `write_ready`, one low-height stability `write_ready`, one
  `detected_flagged`, and one `blocked`, and only the two `write_ready` rows
  alter the matrix. Additional negative tests cover trace-mismatch clean-status
  contradictions and the missing public manual policy TSV entry point. The real
  no-RAW 85RAW replay under
  `output/productization_realdata_seed_guard_85raw_20260617/generated_policy_explained_no_raw_productization/`
  consumed the existing 4613-row consolidated source audit plus the
  reintegration-stability audit, generated 439 `write_ready`, 72
  `detected_flagged`, and 4102 `blocked` policy rows, selected/wrote 439 matrix
  cells, passed `backfill_policy_write_ready_rows` expected-diff with zero
  blockers, and had `0` missing explanation rows. Its next-evidence counts are
  439 `none_current_scope_writer_approved`, 72
  `masked_or_product_writer_oracle_required`, 1087
  `trace_overlay_or_reintegration_evidence_required`, and 3015
  `approved_evidence_class_or_passing_oracle_required`.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor\diagnostics\standard_peak_backfill_productization.py tools\diagnostics\standard_peak_backfill_productization.py tests\test_standard_peak_backfill_productization.py`
  passed; `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  passed with `22 passed`. Subagent reviewers `Faraday` and `Meitner` found
  policy/manual-allowlist, trace-evidence, stale acceptance-scope, stale board,
  and next-action wording gaps; all were fixed before closeout. Full local gate
  then passed: `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`; `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3759 passed, 1 skipped`); `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts\check_diagnostics_index.py`;
  and the latest no-RAW generated-policy explanation replay command exited `0`
  in about 2.36 sec.
- Remaining blocker: generated policy currently only unifies existing approved
  evidence classes. It does not make apex-delta, width-only, shape-margin,
  all-stability, or broad 4613 rows production-ready without their own oracle
  and expected-diff approval.
- Next checkpoint: superseded for broad Backfill. Future evidence classes may be
  considered only under a new independent truth-source / expected-diff goal;
  this entry must not be used as permission to mine another writer slice.

### 2026-06-18 - bounded_non_broad_lane_acceptance_v1

- Lane: Targeted non-broad production lanes / bounded continuation guard.
- Previous tier: Targeted MS1 limited rescue and SampleMetadata no-output
  projection were individually `production_ready`; ReviewAction candidate
  sidecar was `production_candidate`; broader target rescue, role-driven value
  behavior, selected-candidate switch, and manual-boundary area writer were
  blocked or parked, but there was no compact guard tying these non-broad lanes
  to the machine status index.
- New tier: `production_candidate` bounded-lane guard. This does not change
  product behavior or writer authority.
- Evidence: `docs/superpowers/schemas/bounded_non_broad_product_lanes.v1.json`
  defines the acceptance schema.
  `docs/superpowers/validation/bounded_non_broad_lane_acceptance_v1.tsv`
  records seven non-broad lane outcomes and binds to
  `docs/superpowers/validation/productization_status_index_v1.tsv`
  (`sha256=16C5CF817D216C82C891EFE7FBC319D89F477353BF50E7EBE004EF6411625A95`).
  `scripts/check_bounded_product_lanes.py` validates that broad Backfill is
  absent, readiness matches the status index, no row grants new product
  authority or ProductWriter input, GUI/target expansion are disabled, blocked
  or parked rows have no effect/scope, Targeted MS1 is limited to
  `limited_5hmdc_5medc_v1;5-hmdC;5-medC` with `detected_flagged_only`, and
  SampleMetadata / ReviewAction effects stay no-output or identity-only.
- Product surface changed: docs/spec/validation/test/helper script only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, or GUI behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`
  returned `Bounded non-broad product lanes are consistent and fail-closed.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_bounded_product_lanes_contract.py -v --tb=short`
  passed `12`; focused ruff passed for `scripts/check_bounded_product_lanes.py`
  and `tests/test_bounded_product_lanes_contract.py`. Subagent review found
  status-index/source-hash self-attestation, status-index extra/risk-row, and
  schema+TSV+status coordinated-promotion gaps. They were fixed by chaining
  `check_productization_state.py`, binding hash checks to the supplied status
  index, hardcoding the bounded readiness sets in the checker, documenting
  `current_bounded_surface` as non-authority, and adding focused negative
  tests for each failure mode. Post-fix subagent review re-ran the original
  mutation probes and found no P0/P1/P2/P3 findings. Full local gate passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_trace_overlay_recovery_report.py scripts/build_peak_choice_truth_lockbox.py scripts/check_productization_authority.py scripts/check_productization_state.py scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3825 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `git diff --check` passed with LF/CRLF warnings only.
- Remaining blocker: this guard does not collect new Targeted MS1 evidence,
  alter SampleMetadata role behavior, or implement ReviewAction selected
  candidate / manual-boundary writeback.
- Next checkpoint: commit this Goal 6 slice. Future non-broad lane changes must
  update this guard or explicitly supersede it.

### 2026-06-18 - lockbox_label_collection_pack_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: `production_candidate` truth-lockbox protocol and 72-case
  sampling manifest existed, but the lockbox was not yet packaged for
  structured human labeling.
- New tier: unchanged for product authority. The label-collection pack is a
  `production_candidate` truth/approval substrate: it can collect independent
  labels later, but it does not grant ProductWriter authority.
- Evidence: `docs/superpowers/schemas/lockbox_label_schema_v1.json` defines
  legal labels for peak choice, area, boundary, reviewer confidence, reason
  code, notes, source hashes, and no-authority flags.
  `scripts/build_lockbox_label_collection_pack.py` generates 72 Markdown
  packets under
  `docs/superpowers/validation/lockbox_review_packets_v1/`, the paired
  `packet_index.tsv`, and
  `docs/superpowers/validation/lockbox_label_template_v1.tsv` with 144 blank
  reviewer rows. Each packet records row identity, candidate peak/area summary,
  trace/overlay/hypothesis paths and hashes when available, recovered-evidence
  context or an explicit missing-evidence reason, current machine decision, and
  the exact review question. `scripts/check_lockbox_label_schema.py` validates
  schema, packets, hashes, template structure, enum values, duplicate completed
  reviewer IDs, label identity fields, source-hash binding, canonical packet
  paths, and no-authority flags. Its default mode is hermetic for clean
  checkouts and does not require ignored local `output/` artifacts;
  `--verify-evidence-files` performs the stronger local file-existence/hash
  check on machines that have the referenced evidence files. `--require-complete`
  is reserved for the later completed-label import gate.
- Product surface changed: docs/spec/validation/helper script/test only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, broad Backfill, or GUI
  behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_label_collection_pack.py`
  built 72 packets and 144 template rows;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_label_schema.py`
  returned `Lockbox label collection pack is structurally valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_label_schema.py --verify-evidence-files`
  also passed on the current machine;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_label_collection_pack.py -v --tb=short`
  passed `15`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_label_collection_pack.py scripts/check_lockbox_label_schema.py tests/test_lockbox_label_collection_pack.py`
  passed. Subagent review found no authority-boundary findings, then identified
  data-contract gaps for non-hermetic evidence validation, completed-label
  evidence/hash binding, free-form reason codes, noncanonical packet paths, and
  label identity drift. All were fixed with focused regression tests; final
  post-fix review found no P0/P1/P2/P3 findings. Final full local gate is still
  passed before committing this checkpoint:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_label_collection_pack.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_label_schema.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_label_schema.py --verify-evidence-files`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_trace_overlay_recovery_report.py scripts/build_peak_choice_truth_lockbox.py scripts/check_productization_authority.py scripts/check_productization_state.py scripts/check_bounded_product_lanes.py scripts/build_lockbox_label_collection_pack.py scripts/check_lockbox_label_schema.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3840 passed, 1 skipped`); `git diff --check` passed with LF/CRLF warnings
  only.
- Remaining blocker: no human labels have been collected. The next gate must
  import completed labels and summarize independent peak-choice / area truth;
  it must not treat round-trip oracle, ISTD, empty template rows, or lockbox
  membership as ground truth.
- Historical next checkpoint at the time: commit Goal 7, then proceed to a label
  import / truth summary gate only after completed labels exist. Goal 7 was
  later committed as `dcd8878a`; the active follow-up is the Goal 9 static
  review UX checkpoint below.

### 2026-06-18 - lockbox_static_review_ux_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: `production_candidate` label-collection pack existed with 72
  Markdown packets and a 144-row empty label template, but human review still
  required opening TSV/Markdown plus external trace/overlay artifacts by hand.
- New tier: unchanged for product authority. The static review UX is a
  `production_candidate` review substrate only; it does not collect labels and
  does not grant ProductWriter authority.
- Evidence: `scripts/build_lockbox_static_review_bundle.py` reads the existing
  `lockbox_review_packets_v1/packet_index.tsv`, selects the matching sample
  trace from each trace JSON, applies the existing
  `gaussian15_morphology_trace` (`gaussian_15`, 15 points), derives the
  primary review boundary from the existing chromatographic peak-segment
  baseline-return logic, and writes
  `docs/superpowers/validation/lockbox_static_review_v1/`. The generated bundle
  contains `index.html`, `bundle_index.tsv`, 72 case HTML pages, and 53 PNG
  review plots with Gaussian-derived boundaries. The 18 rows without trace
  evidence are rendered as explicit missing-evidence pages, and one trace file
  with no positive signal is rendered as
  `gaussian_review_boundary_unavailable`, instead of synthetic plots.
- Product surface changed: docs/validation/helper script/test only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, broad Backfill, or GUI
  behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_static_review_bundle.py`
  built 72 cases and 53 Gaussian15 plots;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_static_review_bundle.py --check-only`
  returned `Lockbox static review bundle is valid and non-authoritative.`
  after checking the current packet-index hash, label-template hash, source
  artifact hashes, and visible row identity fields;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_static_review_bundle.py -v --tb=short`
  passed `11`, including stale packet-index, label-template, source-hash,
  browser-case Gaussian-boundary, and synthetic zero-signal regression tests;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_static_review_bundle.py tests/test_lockbox_static_review_bundle.py`
  passed. Subagent review found one stale-bundle checker gap and one stale
  historical-checkpoint wording gap; both were fixed. Follow-up subagent review
  found no P1/P2/P3 findings for the Gaussian-boundary display fix. Final local
  gate passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_lockbox_static_review_bundle.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_static_review_bundle.py --check-only`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3851 passed, 1 skipped`); `git diff --check` passed with LF/CRLF
  warnings only.
- Remaining blocker: no human labels have been collected. The UX reduces manual
  review friction but does not replace independent reviewer labels or the later
  label import / truth summary gate.
- Next checkpoint: use the HTML bundle for a small first labeling batch before
  starting label import.

### 2026-06-18 - lockbox_truth_summary_gate_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: `production_candidate` label-collection and static-review
  substrate existed, but no reviewer labels had been imported into a truth
  summary.
- New tier: unchanged for product authority. The truth-summary gate is a
  `production_candidate` review-only evidence packet. It does not grant
  ProductWriter authority and does not reopen broad Backfill.
- Evidence: `scripts/import_lockbox_labels.py` imports the user's first
  2026-06-18 visual pass over
  `docs/superpowers/validation/lockbox_static_review_v1/bundle_index.tsv`.
  It writes
  `docs/superpowers/validation/lockbox_reviewer_label_log_v1.tsv`,
  `docs/superpowers/validation/lockbox_truth_summary_v1.json`,
  `docs/superpowers/validation/lockbox_truth_confusion_table_v1.tsv`, and
  `docs/superpowers/validation/lockbox_failure_modes_v1.tsv`. The import binds
  each label to the static bundle hash, case HTML hash, plot hash or plot
  status, source artifact hashes, row identity, legal label enums, and
  no-authority flags. Current summary: 72 one-reviewer labels imported; 53
  assessable Gaussian15 static review plots are labeled `correct` /
  `acceptable` / `acceptable`; 19 cases are `insufficient_evidence` /
  `not_assessable`; decision is `truth_supports_review_only`.
- Product surface changed: docs/validation/helper script/test only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, broad Backfill, or GUI
  behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/import_lockbox_labels.py --generate-user-batch-log`
  built the label log and truth-summary artifacts;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/import_lockbox_labels.py --check-only`
  returned `Lockbox truth summary gate is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_truth_summary.py -v --tb=short`
  passed `8`, including stale static-hash, authority-flag,
  duplicate-reviewer, and two-reviewer clean-label future-path regression
  tests;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_state_index.py -v --tb=short`
  passed with `peak_choice_truth_lockbox_v1` bound to
  `lockbox_truth_summary_v1.json`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/import_lockbox_labels.py tests/test_lockbox_truth_summary.py`
  passed. Subagent review found one P2 status-index binding gap and one P3
  future two-reviewer import-path gap; both were fixed by updating the status
  index/checker coverage and allowing multiple reviewer rows per case while
  rejecting duplicate reviewer IDs.
- Remaining blocker: this is one reviewer pass, not the completed two-reviewer
  lockbox. The 19 not-assessable cases still need recovered visual evidence or
  explicit park/insufficient-evidence handling. This gate cannot support an
  automation or writer experiment until independent truth coverage and
  expected-diff authority exist.
- Next checkpoint: superseded by `lockbox_next_action_plan_v1`, which splits the
  coarse 19 not-assessable cases into manual-negative controls,
  round-trip-oracle parked negatives, and one boundary-unavailable evidence gap.

### 2026-06-18 - lockbox_next_action_plan_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` review-only truth-summary
  gate. The imported batch had 53 assessable labels and 19 coarse
  not-assessable labels.
- New tier: unchanged for product authority. The next-action packet is a
  `production_candidate` review-routing packet only; it does not grant
  ProductWriter authority and does not reopen broad Backfill.
- Evidence: `scripts/build_lockbox_next_action_plan.py` reads the static review
  bundle, imported reviewer label log, and truth-summary gate, then writes
  `docs/superpowers/validation/lockbox_next_action_plan_v1.tsv` and
  `docs/superpowers/validation/lockbox_next_action_summary_v1.json`. Current
  split: 53 `ready_for_second_independent_review`, 6
  `use_existing_manual_negative_control`, 12
  `park_roundtrip_oracle_negative_as_nontruth`, and 1
  `recover_or_mark_gaussian_boundary_unavailable`. Every row has
  `may_feed_product_writer=FALSE`, `may_touch_matrix=FALSE`, and
  `may_grant_product_authority=FALSE`.
- Product surface changed: docs/validation/helper script/test only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, broad Backfill, or GUI
  behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_next_action_plan.py`
  built the next-action artifacts;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_next_action_plan.py --check-only`
  returned `Lockbox next-action plan is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_next_action_plan.py -v --tb=short`
  passed `13`, including manual-negative separation, round-trip-oracle parking,
  second-review routing, boundary-unavailable routing, stale-plan, stale-summary,
  summary-authority, extra-summary-authority-key, parked-flag, and
  row-authority regressions;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_next_action_plan.py tests/test_lockbox_next_action_plan.py`
  passed. Subagent review found two P2 fail-closed gaps and three P3
  data-flow/test gaps; all were fixed. Final post-fix subagent review found no
  P0/P1/P2/P3 findings. Full local gate after fixes passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_lockbox_next_action_plan.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3873 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_next_action_plan.py --check-only`.
- Remaining blocker: the 53 plotted clean cases still need a second independent
  reviewer before any automation experiment. The 12 round-trip oracle negatives
  remain parked as non-truth; the 6 manual negatives are controls only; the 1
  boundary-unavailable case needs signal/evidence recovery or remains
  not-assessable.
- Next checkpoint: superseded by `lockbox_second_review_pack_v1`, which creates
  the blank reviewer-slot-2 collection surface for the 53 plotted clean cases.
  Do not mine another broad Backfill heuristic.

### 2026-06-18 - lockbox_second_review_pack_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` next-action routing packet.
  The current gate had 53 plotted Gaussian15 cases ready for a second
  independent reviewer, plus 19 cases excluded from that route.
- New tier: unchanged `production_candidate`. This is a second-review
  collection packet only; it does not grant ProductWriter, matrix, workbook,
  selected peak/area, counted-detection, default extraction, GUI, or broad
  Backfill authority.
- Evidence: `scripts/build_lockbox_second_review_pack.py` reads
  `lockbox_next_action_plan_v1.tsv`, the static Gaussian15 review bundle, and
  the first reviewer label log. It writes
  `docs/superpowers/validation/lockbox_second_review_queue_v1.tsv`,
  `docs/superpowers/validation/lockbox_second_review_template_v1.tsv`,
  `docs/superpowers/validation/lockbox_second_review_summary_v1.json`, and
  `docs/superpowers/validation/lockbox_second_review_v1/index.html`.
  Current split: 53 reviewer-slot-2 rows are blank and linked to existing
  Gaussian15 overlay pages; 19 non-ready cases are excluded from this collection
  pack. Every queue/template row keeps no-authority flags false.
- Product surface changed: docs/validation/helper script/test only. It adds a
  review-collection TSV/HTML surface, not a writer surface. No ProductWriter,
  matrix, workbook, selected peak/area, counted detection, workbook schema,
  CLI/config, extraction default, broad Backfill, or GUI behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_second_review_pack.py`
  built the second-review artifacts;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_second_review_pack.py --check-only`
  returned `Lockbox second-review pack is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_second_review_pack.py -v --tb=short`
  passed `11`, including exact 53-case routing, blank slot-2 template,
  Gaussian15 contract, HTML link smoke, actual PNG hash anchoring,
  no-authority flags, stale-summary, stale-template, and queue-authority
  regressions;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_second_review_pack.py tests/test_lockbox_second_review_pack.py`
  passed. Subagent review found one P2 stale plot-hash anchoring gap and one P3
  broken HTML template link; both were fixed before commit. Post-fix subagent
  re-review found no P0/P1/P2/P3 findings. Final full local gate after those
  fixes passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_lockbox_second_review_pack.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3884 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_second_review_pack.py --check-only`.
- Remaining blocker: a second independent human reviewer is not currently
  available because the user is the sole domain owner. Subagents may perform
  adversarial QA and visual contradiction checks, but they must not be recorded
  as `reviewer_slot=2` human truth labels. These labels still cannot become
  write authority without a later expected-diff product goal.
- Next checkpoint: finish subagent QA and route any flagged cases back to the
  owner. If no second human reviewer exists, create a separate
  non-authoritative AI-challenge record or an explicitly approved downgraded
  single-owner + AI-challenge evidence contract before rerunning the truth
  summary gate. Do not route the 19 excluded cases into broad Backfill
  heuristics.

### 2026-06-18 - lockbox_reviewer_identity_guard_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` second-review collection
  packet. Docs said subagents could only do QA/challenge review, but the
  completed-label checker and truth-summary import gate still only required
  distinct reviewer IDs.
- New tier: unchanged `production_candidate`. This is a fail-closed review
  identity guard and owner-boundary record; it does not grant ProductWriter,
  matrix, workbook, selected peak/area, counted-detection, default extraction,
  GUI, or broad Backfill authority.
- Evidence: `scripts/lockbox_reviewer_identity.py` enforces the explicit human
  truth reviewer registry in `lockbox_label_schema_v1.json`; unregistered
  reviewer IDs, including agent/subagent/model-looking IDs, cannot satisfy
  human truth labels. The guard is enforced by
  `scripts/check_lockbox_label_schema.py --require-complete` and
  `scripts/import_lockbox_labels.py --check-only`. The truth-summary import gate
  also now rejects malformed reviewer-slot logs and requires valid slot 1/2
  semantics before the automation-experiment decision can be reached. The label
  schema records that AI challenge review must be stored outside human truth
  slots. The owner boundary confirmation is recorded in
  `docs/superpowers/validation/lockbox_owner_boundary_confirmation_v1.json`,
  with hashes for the static bundle, label log, truth summary, and next-action
  summary; it explicitly forbids ProductWriter,
  matrix, workbook, selected peak/area, counted-detection, default extraction,
  GUI, and broad Backfill authority.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_owner_boundary_confirmation.py tests/test_lockbox_label_collection_pack.py tests/test_lockbox_truth_summary.py -v --tb=short`
  passed `30`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_label_schema.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/import_lockbox_labels.py --check-only`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/check_lockbox_label_schema.py scripts/import_lockbox_labels.py scripts/lockbox_reviewer_identity.py tests/test_lockbox_label_collection_pack.py tests/test_lockbox_truth_summary.py tests/test_lockbox_owner_boundary_confirmation.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`
  passed.
- Remaining blocker: no second independent human reviewer exists. Subagents may
  flag contradictions and implementation issues, but cannot satisfy
  `reviewer_slot=2`.
- Next checkpoint: superseded by `lockbox_ai_challenge_packet_v1`, which creates
  the separate non-authoritative AI/subagent QA surface without filling
  reviewer slot 2.

### 2026-06-18 - lockbox_ai_challenge_packet_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` reviewer-identity guard. The
  owner/domain review was recorded, and AI/subagent review was explicitly kept
  out of human truth slots, but there was no separate artifact for low-manual
  challenge review.
- New tier: unchanged `production_candidate`. This is a non-authoritative
  challenge QA packet; it does not grant ProductWriter, matrix, workbook,
  selected peak/area, counted-detection, default extraction, GUI, truth-label,
  reviewer-slot-2, or broad Backfill authority.
- Evidence: `scripts/build_lockbox_ai_challenge_pack.py` reads
  `lockbox_next_action_plan_v1.tsv`, the static Gaussian15 review bundle, the
  owner label log, and
  `docs/superpowers/validation/lockbox_owner_boundary_confirmation_v1.json`.
  It writes `docs/superpowers/validation/lockbox_ai_challenge_queue_v1.tsv`,
  `docs/superpowers/validation/lockbox_ai_challenge_template_v1.tsv`,
  `docs/superpowers/validation/lockbox_ai_challenge_summary_v1.json`, and
  `docs/superpowers/validation/lockbox_ai_challenge_v1/index.html`. Current
  split: all 72 lockbox cases are present; 53 are visual contradiction checks
  against owner-confirmed Gaussian15 plots, and 19 are route/evidence integrity
  checks only. Only the 53 visual rows allow
  `visual_contradiction_suspected`; the 19 route/evidence rows do not. The
  template is blank and every no-authority flag is false.
- Product surface changed: docs/validation/helper script/test only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, GUI, broad Backfill, or
  truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_ai_challenge_pack.py`
  built the packet;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_ai_challenge_pack.py --check-only`
  returned `Lockbox AI challenge packet is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_ai_challenge_pack.py -v --tb=short`
  passed `12`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_ai_challenge_pack.py tests/test_lockbox_ai_challenge_pack.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_state_index.py tests/test_lockbox_ai_challenge_pack.py -v --tb=short`
  passed `20`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_ai_challenge_pack.py tests/test_lockbox_ai_challenge_pack.py tests/test_productization_state_index.py`
  passed.
- Subagent review: read-only reviewer found the route/evidence output-scope
  leak and stale HTML/summary coverage gap. Both were fixed before commit.
- Remaining blocker: AI challenge results are not yet filled or reviewed, and
  even filled AI findings can only flag owner re-review. They cannot satisfy
  human truth completion.
- Next checkpoint: commit this non-authoritative challenge packet, then run or
  fill challenge results and route only flagged cases back to the owner.

### 2026-06-18 - lockbox_ai_challenge_result_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` challenge packet ready for 72
  cases, but challenge results were not yet filled.
- New tier: unchanged `production_candidate`. This is a non-authoritative
  challenge result log and summary; it does not satisfy reviewer slot 2 and
  does not grant ProductWriter, matrix, workbook, selected peak/area,
  counted-detection, default extraction, GUI, truth-label, or broad Backfill
  authority.
- Evidence: `docs/superpowers/validation/lockbox_ai_challenge_result_log_v1.tsv`
  records 72 challenge rows from three read-only subagent chunks plus one main
  agent chunk. `docs/superpowers/validation/lockbox_ai_challenge_result_summary_v1.json`
  reports `decision=ai_challenge_owner_recheck_required`: 71 rows are
  `no_issue`; 1 row,
  `LOCKBOXV1_60CEB35837FAF38CC4DE9021`, is
  `visual_contradiction_suspected` and must be routed only to owner re-review.
- Product surface changed: docs/validation/helper script/test/output artifact
  only. No ProductWriter, matrix, workbook, selected peak/area, counted
  detection, workbook schema, CLI/config, extraction default, GUI, broad
  Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_ai_challenge_results.py`
  built the result summary with 72 cases and 1 flagged case;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_ai_challenge_results.py --check-only`
  returned `Lockbox AI challenge results are valid and non-authoritative.`;
  focused pytest and ruff for the result checker were added and run.
- Remaining blocker: the single flagged case needs owner re-review before it can
  be treated as resolved. Even owner resolution still cannot become writer
  authority without a later authority manifest and expected-diff product goal.
- Next checkpoint: build a tiny owner re-review packet for
  `LOCKBOXV1_60CEB35837FAF38CC4DE9021`, or wait for owner review if the current
  HTML/plot is enough.

### 2026-06-18 - lockbox_ai_challenge_owner_rule_resolution_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` with one
  `ai_challenge_owner_recheck_required` flag for
  `LOCKBOXV1_60CEB35837FAF38CC4DE9021`.
- New tier: unchanged `production_candidate`; the AI challenge result now has
  no open owner re-review flag. This closure does not satisfy reviewer slot 2
  and does not grant ProductWriter, matrix, workbook, selected peak/area,
  counted-detection, default extraction, GUI, truth-label, or broad Backfill
  authority.
- Evidence: the owner clarified the double-peak rule for raw traces: if the
  Backfill/detect reference apex is on the left peak, keep the current clean
  decision; if it is indistinguishable or on the right peak, keep the case
  flagged. Existing recovered trace evidence for
  `LOCKBOXV1_60CEB35837FAF38CC4DE9021` records
  `cell_apex_rt=15.1553` and `trace_apex_rt=15.1553` on the left peak, while
  the competing right peak is around `15.4366`. The result row now records
  `challenge_result=no_issue` and
  `challenge_reason_code=owner_rule_detected_left_peak_resolved`.
  `docs/superpowers/validation/lockbox_ai_challenge_result_summary_v1.json`
  reports `decision=ai_challenge_no_owner_recheck_required`: 72 `no_issue`,
  0 flagged.
- Product surface changed: validation artifact/schema enum, focused tests, and
  docs/status indexes only. No ProductWriter, matrix, workbook, selected
  peak/area, counted detection, workbook schema, CLI/config, extraction
  default, GUI, broad Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_ai_challenge_results.py`
  rebuilt the result summary with 72 cases and 0 flagged cases;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_ai_challenge_results.py --check-only`
  returned `Lockbox AI challenge results are valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`
  returned `Productization state index is consistent and fail-closed.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_bounded_product_lanes.py`
  returned `Bounded non-broad product lanes are consistent and fail-closed.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_ai_challenge_results.py tests/test_productization_state_index.py -v --tb=short`
  passed `16`; focused ruff passed for the changed checker/tests.
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`
  returned `INDEX.md in sync: 90 entry points, 169 total files.`;
  `git diff --check` returned only LF/CRLF warnings.
  Read-only subagent review passed with no blocking findings and confirmed the
  trace artifact, hashes, status index, and no-authority boundary.
- Remaining blocker: none for the AI challenge flag. Future automation still
  needs an explicit authority manifest update and expected-diff product goal
  before any label, challenge output, or review evidence can write product
  values.
- Next checkpoint: continue from the truth/review substrate. Do not convert this
  closure into writer authority; use it only to unblock later truth-summary or
  review-workflow experiments.

### 2026-06-18 - lockbox_second_review_ai_challenge_gate_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` AI challenge result with
  `decision=ai_challenge_no_owner_recheck_required`, 72 `no_issue`, and
  0 flagged cases. The second-review collection pack existed, but it was not
  mechanically gated on the closed AI challenge result.
- New tier: unchanged `production_candidate`. The current active artifact is
  now `docs/superpowers/validation/lockbox_second_review_summary_v1.json`
  (`decision=second_review_collection_ready_for_53_cases`). It remains a
  truth/review collection surface only; it grants no ProductWriter, matrix,
  workbook, selected peak/area, counted-detection, GUI, default extraction, or
  broad Backfill authority.
- Evidence: `scripts/build_lockbox_second_review_pack.py` now requires the AI
  challenge result summary to be current and to report
  `ai_challenge_no_owner_recheck_required` with zero flagged cases before the
  second-review pack can validate. The summary records the upstream AI
  challenge decision and hash. `lockbox_owner_boundary_confirmation_v1.json`
  was corrected to avoid a cyclic source-artifact chain: owner boundary now
  records only upstream human-review evidence and no longer hashes downstream
  second-review summaries. AI challenge queue/template/result metadata were
  regenerated only to point at that non-cyclic owner-boundary artifact; the
  challenge results stayed 72 `no_issue`.
- Product surface changed: validation artifact schemas/content, helper script,
  focused tests, status index, and docs only. No ProductWriter, matrix,
  workbook, selected peak/area, counted detection, workbook schema, CLI/config,
  extraction default, GUI, broad Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_ai_challenge_pack.py --check-only`
  returned `Lockbox AI challenge packet is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_lockbox_ai_challenge_results.py --check-only`
  returned `Lockbox AI challenge results are valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_second_review_pack.py --check-only`
  returned `Lockbox second-review pack is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_second_review_pack.py tests/test_lockbox_ai_challenge_results.py tests/test_lockbox_ai_challenge_pack.py tests/test_lockbox_owner_boundary_confirmation.py -v --tb=short`
  passed `32`.
- Remaining blocker: this still does not create second human labels. The next
  product step is to collect/import the second independent review labels, or
  explicitly define a future single-owner plus AI-challenge evidence contract.
  Either path still needs a separate expected-diff and authority goal before
  writer authority can expand.
- Next checkpoint: run productization state/lane/diagnostics checks, subagent
  review, then commit the scoped gate. Do not use this gate to unpark broad
  Backfill or promote labels/challenge output into writer authority.

### 2026-06-18 - lockbox_single_owner_ai_challenge_gate_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` second-review collection
  gate. The second-review pack was valid only after AI challenge closure, but
  the lane still required either second-human labels or a separate contract for
  the single-owner + AI-challenge evidence path.
- New tier: unchanged `production_candidate`. Current active artifact is
  `docs/superpowers/validation/lockbox_single_owner_ai_challenge_gate_v1.json`
  with
  `decision=single_owner_ai_challenge_supports_shadow_automation_experiment`.
  This allows only a later shadow automation experiment design. It is not
  two-human truth completion and does not grant ProductWriter, matrix, workbook,
  selected peak/area, counted-detection, GUI, default extraction, or broad
  Backfill authority.
- Evidence: `scripts/build_lockbox_single_owner_ai_challenge_gate.py` checks
  the current truth summary (`truth_supports_review_only`), AI challenge result
  summary (`ai_challenge_no_owner_recheck_required`, zero flagged), second
  review summary (`second_review_collection_ready_for_53_cases`), and
  non-cyclic owner-boundary confirmation. Output records 53 owner-clean
  Gaussian15 cases, 19 excluded/not-assessable cases, and 0 product-authority
  rows.
- Product surface changed: validation decision JSON, helper script, focused
  tests, status index, and docs only. No ProductWriter, matrix, workbook,
  selected peak/area, counted detection, workbook schema, CLI/config,
  extraction default, GUI, broad Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_single_owner_ai_challenge_gate.py`
  built the gate;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_single_owner_ai_challenge_gate.py --check-only`
  returned `Lockbox single-owner AI challenge gate is valid and non-authoritative.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_single_owner_ai_challenge_gate.py -v --tb=short`
  passed `8`, including stale truth summary, stale second-review summary,
  AI-flag, owner-boundary-cycle, owner-boundary-hash, and authority-drift
  regressions;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_single_owner_ai_challenge_gate.py tests/test_lockbox_single_owner_ai_challenge_gate.py`
  passed.
- Remaining blocker: this gate supports only shadow experiment design. Any
  ProductWriter authority expansion still needs a separate masked/product-writer
  oracle, expected-diff, and explicit authority-manifest update.
- Next checkpoint: design the shadow automation experiment around the 53
  owner-clean + AI-no-flag cases while keeping 19 excluded cases out of writer
  scope. Do not treat this gate as `reviewer_slot=2` or broad Backfill
  authority.

### 2026-06-19 - QuantMatrix Default Product Activation v1

- Lane: Backfill default QuantMatrix ProductWriter activation / Phase 11.
- Previous tier: `product_ready_default_matrix_candidate`, which still
  required an explicit ProductWriter/default output activation commit.
- New tier: `product_ready_default_matrix_activated`. The current 511-cell
  authority is now materialized as default quant matrix output. Authority scope
  and row count remain unchanged: `backfill_policy_write_ready_rows`, 511
  accepted Backfill cells, broad 4613-row Backfill still parked.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_default_product_activation_schema.v1.json`,
  `scripts/build_quant_matrix_default_product_activation.py`,
  `tests/test_quant_matrix_default_product_activation.py`, and
  `docs/superpowers/validation/quant_matrix_default_product_activation_v1/`.
- Product surface changed: explicit default output files are now written:
  `default_output/quant_matrix.tsv`, `default_output/cell_provenance.tsv`,
  `default_output/row_summary.tsv`,
  `default_output/expected_diff_summary.tsv`,
  `default_output/source_summary.tsv`,
  `default_product_activation_checks.tsv`, and
  `quant_matrix_default_product_activation_summary.json`.
- Evidence: default `--check-only` fail-closes to
  `source_run_id=seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`,
  `downstream_scope=current_511_authority_replay`, and
  `accepted_backfill_count=511`. The checker revalidates the Phase 7 real
  bundle and Phase 10 Product Ready closeout, reruns manifest-driven
  activation, checks source hashes, requires expected-diff closure, and compares
  the default outputs with the Phase 7 reference hashes.
- Current validation result: activation status is `pass`; 511 expected,
  511 accepted Backfill written, and 0 unused expected-diff rows. Default
  `quant_matrix.tsv`, `cell_provenance.tsv`, `row_summary.tsv`, and
  `expected_diff_summary.tsv` match the Phase 7 reference hashes. The sidecar
  records 17489 detected cells, 511 accepted Backfill cells, and 18000
  quant-available cells.
- Product surface not changed: No workbook, GUI, selected peak, selected area,
  counted detection, review/replay behavior, broad Backfill authority, scorer,
  RAW, or 85RAW changed. Accepted Backfill values are quantification values in
  the default matrix, not detections or truth claims.
- Status-index update: updated the writer row's current artifact/public surface
  to this activation summary and `quant_matrix_default_product_activation_v1`.
  The writer row count and product authority scope remain exactly 511 and
  `backfill_policy_write_ready_rows`.
- Next checkpoint: maintain this default output through the authority manifest,
  expected-diff checks, and focused public-surface tests; do not expand Backfill
  authority without a new authority goal.

### 2026-06-19 - Target-row identity gap stop-ship addendum

- Lane: Default QuantMatrix target-row identity / targeted ISTD benchmark
  semantics.
- Previous tier: `product_ready_default_matrix_activated` for the 511-cell
  default matrix activation bundle.
- New tier: unchanged. The 511-cell matrix writer authority, active lane,
  `backfill_policy_write_ready_rows` scope, and broad Backfill parked state are
  unchanged. This addendum changes diagnostic/review semantics and records a
  target-row identity blocker; it does not change ProductWriter output files or
  matrix authority.
- Evidence: user review found `d4-N6-2HE-dA` target `m/z=300.1605` absent from
  `quant_matrix.tsv`. Investigation showed the final matrix has nearby rows
  `299.155` and valid isotope-shift dR-tag feature row `301.165`, but no exact
  target row for `300.1605`.
  Discovery artifacts under `local_validation_artifacts/discovery/accepted_p8b/85raw`
  contain `0` 22-25 min monoisotopic `300.1605 -> 184.113` candidates and
  `160` isotope-shift `301.165 -> 185.116` candidates spanning all 85 samples.
  Existing targeted ISTD benchmark output had incorrectly selected `FAM007866`
  as a primary `d4-N6-2HE-dA` row despite `match_type=isotope_shift`.
- Product surface changed: targeted ISTD benchmark summaries now count only
  `match_type=exact` rows as primary matches, and matrix identity blast-radius
  joins no longer promote isotope-shift benchmark rows to
  `active_dna_istd_candidate`. Output schemas are unchanged; review/replay
  classification semantics are stricter.
- Validation: focused red/green tests now cover isotope-only active ISTD
  failing as `MISS` and blast-radius refusing to promote isotope-shift target
  aliases. `uv run pytest tests/test_targeted_istd_benchmark.py tests/test_matrix_identity_blast_radius.py -v --tb=short`
  passed 25 tests. A no-RAW recheck over the existing targeted workbook and
  alignment TSV/XLSX artifacts writes
  `tmp_runtime/targeted_istd_benchmark_d4_recheck_20260619/` and reports
  `d4-N6-2HE-dA status=FAIL`, `failure_modes=MISS`,
  `primary_match_count=0`; `FAM007866` remains visible only as
  `match_type=isotope_shift`.
- Remaining blocker: the activated default matrix remains numerically usable for
  downstream wide-matrix analysis, but release-facing target/ISTD lookup is not
  product-ready for `d4-N6-2HE-dA` until active targets can materialize exact
  target-anchored product rows and the default activation expected-diff bundle
  is regenerated. Keep `301.165` as its own valid feature row; do not bridge it
  back to `300.1605` as authority or treat it as a reason to suppress the
  missing monoisotopic row.

### 2026-06-19 - Discovery precursor inference root-cause fix v1

- Lane: Untargeted discovery row identity / target-row stop-ship remediation.
- Previous tier: unchanged from
  `product_ready_default_matrix_activated` for the current 511-cell default
  matrix activation bundle, with `d4-N6-2HE-dA` release-facing target lookup
  blocked by the missing exact row.
- New tier: unchanged. This fixes discovery generation behavior and artifact
  identity only. It does not promote a new maturity tier, change active lane,
  alter ProductWriter authority, regenerate the default matrix, unpark broad
  Backfill, or change workbook/GUI, selected peak, selected area, counted
  detection, review/replay, scorer, RAW/85RAW authority, or current 511-cell
  Backfill authority.
- Root cause: `xic_extractor.discovery.ms2_seeds` used the Thermo MS2 filter
  precursor as the only exact precursor when searching for
  `precursor - neutral_loss` products. For `TumorBC2312_DNA`, RAW MS2 scans in
  the 22-25 min window include filter precursor `300.2028` with product
  `184.1132`; `184.1132 + 116.0474 = 300.1606`, but the old direct-only search
  looked near `184.1554` and rejected the row before grouping/alignment.
- Product surface changed: `DiscoverySettings` and `scripts/run_discovery.py`
  now expose `ms2_precursor_tol_da` / `--ms2-precursor-tol-da` for Da-level
  MS2 filter precursor/isolation matching. Discovery seed extraction emits
  product-plus-neutral-loss inferred precursor seeds in addition to direct
  scan-precursor neutral-loss seeds. Discovery `candidate_id` now keeps the
  `sample#scan` anchor but appends precursor/product row identity, preventing
  same-scan candidate collisions. Discovery `tag_evidence_json` records
  `precursor_mz_basis`, `scan_precursor_mz`, and scan-precursor delta fields.
  CSV column schemas are unchanged.
- Evidence: focused tests cover inferred monoisotopic seed recovery, direct and
  inferred same-scan coexistence, old out-of-tolerance rejection behavior,
  public CLI/preset plumbing, and same-scan candidate ID uniqueness. Single RAW
  validation under
  `docs/superpowers/validation/discovery_precursor_inference_v1/` reran only
  `TumorBC2312_DNA.raw` over 22-25 min and observed
  `duplicate_candidate_ids=0`, emitted `300.161 / 184.113` rows with
  `precursor_mz_basis=product_plus_neutral_loss`, and retained the valid
  `301.165 / 185.116` dR-tag row.
- Validation:
  `uv run pytest tests/test_discovery_ms2_seeds.py tests/test_discovery_grouping.py tests/test_discovery_csv.py tests/test_discovery_pipeline.py tests/test_run_discovery.py tests/test_presets_apply.py -v --tb=short`
  passed `92`; focused ruff over the touched discovery/CLI/preset/tests passed.
  The one-RAW command in
  `docs/superpowers/validation/discovery_precursor_inference_v1/README.md`
  exited `0`.
- Remaining blocker: the existing activated default `quant_matrix.tsv` has not
  been regenerated and still reflects pre-fix discovery artifacts. A later
  explicit discovery/alignment/default-activation expected-diff rerun is needed
  before claiming the released default matrix contains the `300.1605` target
  row.

### 2026-06-19 - QuantMatrix Product Ready Closeout v1

- Lane: Backfill default QuantMatrix Product Ready candidate closeout /
  Phase 10.
- Previous tier: Phase 9 default activation dry-run gate passed, but Product
  Ready closeout had not yet collected Phase 8 packet readiness and Phase 9
  activation replay into one explicit activation contract.
- New tier: `product_ready_default_matrix_candidate`. This means the current
  511-cell default QuantMatrix candidate is ready for a separate explicit
  ProductWriter activation commit. It is not itself ProductWriter activation,
  default matrix output writing, or expanded matrix authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_product_ready_closeout_schema.v1.json`,
  `scripts/build_quant_matrix_product_ready_closeout.py`,
  `tests/test_quant_matrix_product_ready_closeout.py`, and
  `docs/superpowers/validation/quant_matrix_product_ready_closeout_v1/`.
- Product surface changed: additive Phase 10 closeout outputs only:
  `quant_matrix_product_ready_closeout_summary.json` and
  `quant_matrix_product_ready_closeout_checks.tsv`.
- Evidence: default `--check-only` fail-closes to
  `source_run_id=seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`,
  `downstream_scope=current_511_authority_replay`, and
  `accepted_backfill_count=511`. The checker revalidates Phase 8 promotion
  packet v2 and Phase 9 default activation dry-run, checks their source hashes,
  and rebuilds the closeout checklist from those inputs.
- Current validation result: closeout status is `pass`; checklist rows
  `promotion_packet_v2_validated`, `science_evidence_complete`,
  `default_activation_dry_run_passed`, `expected_diff_closed`,
  `authority_unchanged`, and `product_ready_candidate_closeout` all pass. The
  summary records `product_ready_candidate=true`,
  `default_quant_matrix_product_ready_candidate=true`,
  `may_activate_default_quant_matrix_with_explicit_contract=true`,
  `requires_product_writer_activation_commit=true`,
  `explicit_activation_not_in_this_commit=true`, `write_authority=false`, and
  `default_matrix_files_written=false`.
- Product surface not changed: No ProductWriter default extraction, default
  matrix files, workbook, GUI, selected peak, selected area, counted detection,
  review/replay behavior, current 511-cell writer authority, or broad Backfill
  authority changed.
- Status-index update: not needed for Phase 10 because no active lane authority,
  writer scope, row count, or output-changing flag changed. The status-index
  writer row remains the current 511-cell Backfill scope only.
- Next checkpoint: explicit ProductWriter activation commit, if/when requested,
  with expected-diff output tests and public-surface review.

### 2026-06-19 - QuantMatrix Default Activation Dry-Run v1

- Lane: Backfill default matrix activation dry-run / Phase 9.
- Previous tier: Phase 8 produced a `production_ready_candidate_packet`, but no
  gate had replayed the actual manifest-driven default matrix activation path
  against the Phase 7 real bundle expected diff.
- New tier: default activation dry-run gate pass. This permits Phase 10 Product
  Ready closeout review to proceed, but it is still a no-RAW, read-only
  validation artifact: no ProductWriter default extraction, default matrix
  output, selected peak/area/counting behavior, workbook/GUI behavior,
  review/replay behavior, broad Backfill unpark, or new matrix write changed.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_default_activation_dry_run_schema.v1.json`,
  `scripts/build_quant_matrix_default_activation_dry_run.py`,
  `tests/test_quant_matrix_default_activation_dry_run.py`, and
  `docs/superpowers/validation/quant_matrix_default_activation_dry_run_v1/`.
- Product surface changed: additive Phase 9 dry-run outputs only:
  `quant_matrix_default_activation_dry_run_summary.json` and
  `default_activation_dry_run_comparison.tsv`. The dry-run activation writes
  temporary outputs only during build/check and commits no duplicate full matrix
  outputs.
- Evidence: default `--check-only` fail-closes to
  `source_run_id=seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`,
  `downstream_scope=current_511_authority_replay`, and
  `accepted_backfill_count=511`. Focused tests cover schema parity, candidate
  expected-diff replay, stale comparison rejection, non-current 511 bundle
  rejection, and CLI round trip.
- Current validation result: default activation dry-run gate is `pass`; the
  Phase 9 rerun writes `511` expected Backfill rows, `511` accepted Backfill
  cells, and `0` unused expected-diff rows in temporary output. The rerun's
  `quant_matrix`, `cell_provenance`, `row_summary`, and
  `expected_diff_summary` hashes match the Phase 7 reference bundle exactly.
  The summary records `dry_run_only=true`, `write_authority=false`,
  `default_quant_matrix_changed=false`, and
  `default_matrix_files_written=false`.
- Product surface not changed: No ProductWriter default extraction, default
  matrix files, workbook, GUI, selected peak, selected area, counted detection,
  review/replay behavior, current 511-cell writer authority, or broad Backfill
  authority changed.
- Remaining blocker: Product Ready still needs Phase 10 closeout to collect the
  Phase 8/9 gates, review residual risk, and state the activation contract.
- Next checkpoint: Phase 10 Product Ready closeout gate.

### 2026-06-19 - QuantMatrix Promotion Packet v2

- Lane: Backfill promotion evidence packet / Phase 8.
- Previous tier: Phase 7 produced a real 511-cell `QuantMatrixVersion` bundle
  with downstream-impact smoke, but readiness was still
  `contract_ready_science_inconclusive` because the real downstream artifact had
  not been rebound with the existing large-cohort and heldout-oracle evidence.
- New tier: `production_ready_candidate_packet` for the packet/readiness gate
  only. This is not ProductWriter default extraction, default matrix authority,
  selected peak/area/counting behavior, workbook/GUI behavior, review/replay
  behavior change, broad Backfill unpark, or a new matrix write.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_promotion_packet_v2_schema.v1.json`,
  `scripts/build_quant_matrix_promotion_packet_v2.py`,
  `tests/test_quant_matrix_promotion_packet_v2.py`, and
  `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v2/`.
- Product surface changed: additive Phase 8 promotion packet outputs only:
  `quant_matrix_promotion_packet_v2_summary.json`,
  `quant_matrix_validation_evidence_v1.json`,
  `quant_matrix_validation_evidence_rows.tsv`,
  `quant_matrix_validation_evidence_summary.json`, copied `artifacts/*`, and
  `real_bundle_readiness/`. The packet copies the downstream-impact smoke bundle
  plus packet-local downstream input TSVs so content validation can rerun
  without trusting tier strings alone.
- Evidence: default `--check-only` fail-closes to
  `source_run_id=seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`,
  `downstream_scope=current_511_authority_replay`, and
  `accepted_backfill_count=511`. Focused tests cover schema parity, real-bundle
  candidate readiness, stale real-bundle readiness rejection, non-current 511
  bundle rejection, and CLI round trip.
- Current validation result: large-cohort, heldout-oracle, and real
  downstream-impact evidence all pass. Real-bundle readiness is
  `production_ready_candidate_packet`, `contract_correctness_status=pass`,
  `scientific_confidence_status=pass`, `production_ready=true`, and
  `may_promote_default_quant_matrix=true`.
- Product surface not changed: No ProductWriter default extraction, default
  matrix authority, workbook, GUI, selected peak, selected area, counted
  detection, review/replay behavior, current 511-cell writer authority, or
  broad Backfill authority changed.
- Remaining blocker: default matrix activation still needs a Phase 9 dry-run
  expected-diff gate before any public writer/default behavior changes.
- Next checkpoint: Phase 9 default-matrix activation dry-run expected-diff gate.

### 2026-06-19 - QuantMatrix Real Bundle v1

- Lane: Backfill real `QuantMatrixVersion` bundle assembly / Phase 7.
- Previous tier: Phase 6 defined a downstream-impact smoke contract, but the
  durable productization packet still did not include a real
  `QuantMatrixVersion` bundle produced from the current 511-cell authority
  replay.
- New tier: unchanged. This is a no-RAW, read-only real-bundle builder/checker
  and validation artifact, not a maturity-tier promotion, ProductWriter default
  extraction change, selected peak/area/counting change, workbook/GUI change,
  review/replay behavior change, broad Backfill unpark, or new matrix
  authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_real_bundle_schema.v1.json`,
  `scripts/build_quant_matrix_real_bundle.py`,
  `tests/test_quant_matrix_real_bundle.py`, and
  `docs/superpowers/validation/quant_matrix_real_bundle_v1/`.
- Product surface changed: additive Phase 7 bundle outputs only. The bundle
  contains a `ProductionAcceptanceManifest`, expected diff,
  `QuantMatrixVersion`, cell provenance, row summary, review summary/report,
  downstream-impact smoke, and contract-only readiness sidecars generated from
  the current source run
  `seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`.
- Evidence: default `--check-only` now fail-closes to
  `source_run_id=seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617`,
  `downstream_scope=current_511_authority_replay`, and
  `accepted_backfill_count=511`; synthetic/partial bundles require explicit
  test overrides and cannot satisfy the default Phase 7 check. Focused tests
  cover schema parity, self-validation, source-copy tamper rejection,
  non-current 511 bundle rejection, downstream rows deletion rejection, and CLI
  round trip.
- Current validation result: the real bundle check passes, contract correctness
  passes, downstream-impact smoke passes, and readiness remains
  `contract_ready_science_inconclusive` with `production_ready=false` and
  `may_promote_default_quant_matrix=false` before Phase 8 evidence binding.
- Product surface not changed: No ProductWriter default extraction, default
  matrix authority, workbook, GUI, selected peak, selected area, counted
  detection, review/replay behavior, current 511-cell writer authority, or
  broad Backfill authority changed.
- Remaining blocker: historical for Phase 7. Phase 8 now binds the real
  downstream-impact artifact into promotion packet v2; default matrix activation
  still requires a later expected-diff gate.
- Next checkpoint: historical. Use `QuantMatrix Promotion Packet v2` above for
  the current promotion candidate packet.

### 2026-06-19 - QuantMatrix Downstream Impact Smoke v1

- Lane: Backfill downstream-impact evidence contract / Phase 6.
- Previous tier: `QuantMatrix Promotion Validation Packet v1` required
  downstream-impact evidence, but the tier could only bind artifact relpath,
  hash, and `downstream_scope`; it did not define the artifact content needed to
  distinguish a real `QuantMatrixVersion` downstream smoke from a contract
  fixture or arbitrary tier string.
- New tier: unchanged. This is a no-RAW, read-only downstream-impact contract
  and checker/builder surface, not a maturity-tier promotion, ProductWriter
  default extraction, selected peak/area/counting change, workbook/GUI change,
  or new matrix authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_downstream_impact_smoke_schema.v1.json`,
  `scripts/build_quant_matrix_downstream_impact_smoke.py`, and
  `xic_extractor/alignment/quant_matrix_downstream_impact.py`.
- Product surface changed: additive downstream-impact smoke outputs only:
  `quant_matrix_downstream_impact_smoke.json` and
  `quant_matrix_downstream_impact_rows.tsv`. The existing validation packet and
  promotion readiness check now content-validate the
  `downstream_impact_smoke` artifact instead of trusting a tier string alone.
  Validation packets copy the downstream smoke bundle as summary plus rows TSV.
- Evidence: focused tests cover schema parity, real-bundle pass behavior,
  contract-fixture no-promotion behavior, row-metrics hash drift rejection,
  input-artifact hash drift rejection, recomputed row/metric mismatch rejection,
  script `--check-only` round trip, promotion-readiness science-ready fixture
  content validation, validation-packet rejection of downstream contract
  fixtures, and validation-packet acceptance of real downstream bundles.
- Current validation result: Phase 6 contract tests pass, but the current
  durable promotion packet still remains `contract_ready_science_inconclusive`
  until a real downstream-impact artifact is generated from a real
  `QuantMatrixVersion` bundle and rebound into the packet.
- Product surface not changed: No ProductWriter default extraction, workbook,
  GUI, selected peak, selected area, counted detection, review/replay behavior,
  current 511-cell writer authority, or broad Backfill authority changed.
- Remaining blocker: production-ready promotion still needs a real
  `QuantMatrixVersion` bundle plus downstream-impact smoke generated from that
  bundle. Contract fixtures cannot satisfy promotion.
- Next checkpoint: assemble the real `QuantMatrixVersion` bundle and run the
  downstream-impact smoke against it before building a promotion packet v2.

### 2026-06-19 - QuantMatrix Promotion Validation Packet v1

- Lane: Backfill validation/promotion evidence packet / Phase 5 follow-up.
- Previous tier: `QuantMatrix Promotion Readiness v1` could parse validation
  evidence rows, but no durable packet existed to bind source artifacts, copied
  packet artifacts, hashes, and science-evidence gaps.
- New tier: unchanged. This is a no-RAW, read-only validation evidence packet
  and checker/builder surface, not a maturity-tier promotion, ProductWriter
  default extraction, or new matrix authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_validation_evidence_schema.v1.json`,
  `scripts/build_quant_matrix_promotion_validation_packet.py`,
  `xic_extractor/alignment/quant_matrix_validation_packet.py`, and
  `docs/superpowers/validation/quant_matrix_promotion_validation_packet_v1/`.
- Product surface changed: additive validation packet outputs only:
  `quant_matrix_validation_evidence_v1.json`,
  `quant_matrix_validation_evidence_rows.tsv`,
  `quant_matrix_validation_evidence_summary.json`, copied `artifacts/*`, and a
  labeled readiness integration fixture under
  `readiness_integration_fixture/`.
- Evidence: the packet copies and hash-binds the existing no-RAW 85RAW
  consolidated standard-peak activation input summary and the 20-case heldout
  trace reintegration oracle smoke summary. Focused tests cover schema parity,
  source/copy hash recording, copied-artifact hash drift rejection, authority
  drift rejection, source-root binding, Phase 5 checker integration, rows/summary
  drift rejection, readiness fixture rerun, and CLI `--check-only` round trip.
- Current validation result: large-cohort evidence is `pass`,
  heldout-oracle evidence is `pass`, downstream-impact evidence is `missing`.
  The readiness integration fixture is `contract_ready_science_inconclusive`,
  `production_ready=false`, and `may_promote_default_quant_matrix=false`.
- Product surface not changed: No ProductWriter default extraction, workbook,
  GUI, selected peak, selected area, counted detection, review/replay behavior,
  current 511-cell writer authority, or broad Backfill authority changed.
- Remaining blocker: production-ready promotion still needs a real
  downstream-impact smoke artifact and a real `QuantMatrixVersion` contract
  bundle. Do not treat this packet as `production_ready`.
- Next checkpoint: define and validate the downstream-impact evidence source
  for accepted Backfill values; do not revive scoring or broad Backfill as the
  promotion path.

### 2026-06-19 - QuantMatrix Promotion Readiness v1

- Lane: Backfill validation/promotion readiness / Phase 5.
- Previous tier: Phase 4 `QuantMatrixVersion Review Report v1` provided a
  review-only surface but did not define when contract correctness can be
  separated from scientific production readiness.
- New tier: unchanged. This is a read-only readiness checker and public output
  schema, not a maturity-tier promotion, ProductWriter default extraction, or
  new matrix authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_promotion_readiness_schema.v1.json`,
  `scripts/check_quant_matrix_promotion_readiness.py`, and
  `xic_extractor/alignment/quant_matrix_promotion.py`.
- Product surface changed: additive readiness sidecars only:
  `quant_matrix_promotion_readiness_summary.json` and
  `quant_matrix_promotion_readiness_checks.tsv`.
- Evidence: focused tests cover schema-column parity, contract-ready but
  science-inconclusive behavior, focused-test/8RAW-only no-promotion guard,
  unbound tier/status-string no-promotion guard, duplicate validation tier
  fail-closed behavior, artifact-bound synthetic large-cohort + oracle +
  downstream pass behavior, invalid accepted-cell hash format, contract-failure
  dominance, script entrypoint, and `--require-production-ready` nonzero exit
  when science evidence is missing.
- Product surface not changed: No ProductWriter default extraction, workbook,
  GUI, selected peak, selected area, counted detection, review/replay behavior,
  current 511-cell writer authority, or broad Backfill authority changed.
- Production-ready claim: the focused tests prove the gate behavior only.
  Current status is contract-ready/science-inconclusive unless a later explicit
  validation packet supplies artifact-bound passing large-cohort,
  heldout-oracle or manual review, and downstream-impact evidence.
- Remaining blocker: actual promotion still needs a named validation tier and
  evidence packet with downstream-impact evidence. Do not run RAW/85RAW unless
  an active goal names that tier.
- Next checkpoint: current evidence packet is represented by
  `QuantMatrix Promotion Validation Packet v1` above.

### 2026-06-19 - Discovery row-identity/evidence contract gate v1

- Lane: Discovery row identity and evidence contract / default-output unblocker.
- Previous tier: default QuantMatrix activation existed for the current
  511-cell Backfill authority, but user review found that the activated bundle
  predates a missing `300.1605 -> 184.113` Discovery row. The nearby
  `301.165 -> 185.116` isotope feature row is valid and must not be deleted or
  bridged into target authority.
- New tier: unchanged. This is a Discovery contract/gate hardening and
  one-RAW validation artifact; it does not regenerate the default matrix,
  change active lane, or grant new matrix/Backfill/ProductWriter authority.
- Evidence: `docs/product/discovery.md`
  now defines direct scan-precursor and product-plus-neutral-loss inferred
  seed bases; `discovery_candidates.csv` carries
  `neutral_loss_error_basis`, `precursor_mz_basis`, `scan_precursor_mz`,
  `scan_precursor_delta_da`, and `max_scan_precursor_abs_delta_da`; alignment
  CSV replay rejects stale `sample#scan` candidate ids, mismatched
  candidate-id suffixes, duplicate candidate ids, and invalid basis enums.
  The one-RAW validation artifact under
  `docs/superpowers/validation/discovery_precursor_inference_v1/` emits both
  inferred `300.1605 / 184.113` and valid `301.165 / 185.116` rows with
  duplicate candidate ids equal to 0.
- Product surface changed: Discovery full-candidate TSV schema and alignment
  replay behavior changed fail-closed for stale candidate ids. ProductWriter,
  matrix contents, workbook, GUI, selected peak/area, counted detection,
  scorer, 85RAW validation, current 511-cell writer authority, and broad
  Backfill authority did not change.
- Validation: focused Discovery/CSV/alignment tests, one-RAW
  `scripts/run_discovery.py` over `TumorBC2312_DNA.raw` in the 22-25 min
  window, and
  `scripts/check_discovery_precursor_inference_artifact.py --check-only`
  passed. The candidate artifact SHA-256 is
  `B1B4956C3F0296D51E144659DB127CFB453140A66030C97C22FEED8C11326E2B`.
- Remaining blocker: the activated default `quant_matrix.tsv` still predates
  this Discovery fix. Claiming `300.1605` target-row presence requires a later
  explicit discovery/alignment/default-activation expected-diff rerun.

### 2026-06-19 - QuantMatrixVersion Review Report v1

- Lane: Backfill gallery/report alignment / Phase 4.
- Previous tier: Phase 3 `QuantMatrixVersion Activation v1` produced explicit
  quant/provenance/row/source sidecars but did not provide a user-facing review
  surface.
- New tier: unchanged. This is a review-only report adapter and public output
  schema, not a maturity-tier promotion, ProductWriter default extraction, or
  new matrix authority.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_review_report_schema.v1.json`,
  `scripts/build_quant_matrix_version_report.py`, and
  `xic_extractor/alignment/quant_matrix_report.py`.
- Product surface changed: additive review outputs only:
  `quant_matrix_review_rows.tsv`, `quant_matrix_review_summary.json`, and
  `quant_matrix_review_report.html`.
- Evidence: focused tests cover schema-column parity, review row rendering for
  detected plus accepted Backfill cells, summary counts, manifest enrichment,
  source-summary manifest file-hash drift, hash/authority-bound manifest joins,
  fail-closed missing manifest join, HTML escaping, and script entrypoint.
- Product surface not changed: No ProductWriter default extraction, workbook,
  GUI, selected peak, selected area, counted detection, review/replay behavior,
  current 511-cell writer authority, or broad Backfill authority changed.
- Remaining blocker: none for the review-only report adapter itself. Phase 5
  readiness separation is represented by `QuantMatrix Promotion Readiness v1`
  above.
- Next checkpoint: historical. Use the Phase 5 readiness checker above before
  any promotion packet.

### 2026-06-19 - QuantMatrixVersion Activation v1

- Lane: Backfill quant-matrix activation / Phase 3.
- Previous tier: Phase 2 `ProductionAcceptanceManifest v1` schema/checker was
  an authority precursor and did not write matrix outputs.
- New tier: unchanged. This is an explicit activation adapter and public output
  schema for manifest-authorized Backfill values, not a maturity-tier
  promotion and not ProductWriter default extraction.
- Current artifacts:
  `docs/superpowers/schemas/quant_matrix_version_schema.v1.json`,
  `scripts/build_quant_matrix_version.py`, and
  `xic_extractor/alignment/quant_matrix_version.py`.
- Product surface changed: additive explicit outputs only:
  `quant_matrix.tsv`, `cell_provenance.tsv`, `row_summary.tsv`,
  `expected_diff_summary.tsv`, and `source_summary.tsv`.
- Evidence: focused tests cover writing detected plus accepted Backfill values
  from a validated `ProductionAcceptanceManifest`, expected-diff enforcement,
  provenance completeness, row summary counts, detected-only view is reconstructable
  from `quant_matrix + cell_provenance`, missing expected-diff
  rejection, and detected-value overwrite rejection.
- Product surface not changed: No ProductWriter default extraction, workbook,
  GUI, selected peak, selected area, counted detection, review/replay behavior,
  current 511-cell writer authority, or broad Backfill authority changed.
- Remaining blocker: validation/promotion readiness still needs a separate
  Phase 5 gate before any production-readiness claim.
- Next checkpoint: Phase 4 Gallery/Report Alignment was the next phase from
  this checkpoint and is now represented by
  `QuantMatrixVersion Review Report v1` above.

### 2026-06-19 - ProductionAcceptanceManifest v1 schema/checker

- Lane: Backfill quant-matrix authority contract / Phase 2.
- Previous tier: Phase 1 lockbox shadow adapter was
  `shadow_only=true`, `write_authority=false`, and allowed only
  `define_production_acceptance_manifest_v1`.
- New tier: unchanged. This is an additive contract/checker surface and a
  precursor to Phase 3 activation, not ProductWriter authority and not a
  maturity-tier promotion.
- Current artifact:
  `docs/superpowers/schemas/production_acceptance_manifest_schema.v1.json`.
  It defines `ProductionAcceptanceManifest v1`, where the authority key is
  `peak_hypothesis_id + sample_stem`; `feature_family_id is context/provenance only`.
- Evidence: `scripts/check_production_acceptance_manifest.py` validates the
  schema and any candidate manifest TSV. The checker enforces acceptance
  vocabulary, source/doublet artifact path containment, existence, and
  file-hash match, source row hash and canonical manifest sha, finite
  non-negative accepted quant values, Backfill-fraction consistency,
  manual-negative hard stop, blocked doublet hard stop, authority-lane
  contradictions, primary-key uniqueness, report-only prevalence risks, and
  strict risk-specific closure.
- Product surface changed: additive schema JSON, checker script, focused tests,
  control-plane section, and handoff only. No ProductWriter, matrix, workbook,
  selected peak/area, counted detection, workbook schema, CLI/config,
  extraction default, GUI, broad Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_production_acceptance_manifest.py`
  returned `ProductionAcceptanceManifest schema is valid.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_production_acceptance_manifest_contract.py -v --tb=short`
  passed `16`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_state_index.py -v --tb=short`
  passed `13`.
- Remaining blocker: this checker can validate write-authority rows, but it does
  not write the default matrix. Any matrix behavior still requires Phase 3
  QuantMatrixVersion Activation with expected-diff, `cell_provenance`, and
  focused output tests.
- Next checkpoint: Phase 3 QuantMatrixVersion Activation, not default matrix
  activation from a shadow/report/gallery/candidate artifact.

### 2026-06-19 - lockbox_shadow_automation_experiment_v1

- Lane: Peak-choice truth acquisition / `peak_choice_truth_lockbox_v1`.
- Previous tier: unchanged `production_candidate` single-owner + AI-challenge
  gate. That packet allowed only a later shadow automation experiment design.
- New tier: unchanged `production_candidate`. Current active artifact is
  `docs/superpowers/validation/lockbox_shadow_automation_experiment_v1.json`
  with `decision=shadow_scoring_contract_adapter_v1_ready` and
  `allowed_next_step=define_production_acceptance_manifest_v1`.
- Evidence: `scripts/build_lockbox_shadow_automation_experiment_design.py`
  consumes `lockbox_next_action_plan_v1.tsv`,
  `lockbox_next_action_summary_v1.json`, and
  `lockbox_single_owner_ai_challenge_gate_v1.json`, then writes a 72-row shadow
  contract manifest. It routes 53 owner-clean Gaussian15 cases as
  non-authoritative accept challenges, 6 existing manual wrong-peak/no-peak
  controls as reject hard stops, and 13 excluded rows as `not_scored`. It
  records the Gaussian15 boundary policy, shadow/truth/doublet enums, row-level
  doublet fields, source paths/hashes, manifest sha, `shadow_only=true`,
  `write_authority=false`, `matrix_write_allowed=false`,
  `may_satisfy_reviewer_slot2=false`, and
  `single_owner_evidence_is_truth_completion=false`.
- Product surface changed: validation decision JSON, case-manifest TSV, helper
  script, focused tests, status index, label README, control plane, and handoff
  only. No ProductWriter, matrix, workbook, selected peak/area, counted
  detection, workbook schema, CLI/config, extraction default, GUI, broad
  Backfill, or truth-label behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_shadow_automation_experiment_design.py`
  built the design packet;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_lockbox_shadow_automation_experiment_design.py --check-only`
  returned `Lockbox shadow automation experiment design is valid.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_lockbox_shadow_automation_experiment_design.py -v --tb=short`
  passed `14`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check scripts/build_lockbox_shadow_automation_experiment_design.py tests/test_lockbox_shadow_automation_experiment_design.py tests/test_productization_state_index.py`
  passed;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`
  returned `Productization state index is consistent and fail-closed.`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_state_index.py tests/test_lockbox_shadow_automation_experiment_design.py -v --tb=short`
  passed `25`.
- Remaining blocker: this adapter cannot grant writer authority. Phase 2 now
  defines the separate `ProductionAcceptanceManifest v1` schema/checker; any
  ProductWriter authority expansion still needs Phase 3 QuantMatrixVersion
  Activation with expected-diff and focused output tests.
- Next checkpoint: Phase 3 QuantMatrixVersion Activation. Do not promote shadow
  results to writer authority.

### 2026-06-18 - productization_status_index_v1

- Lane: Productization control-plane cleanup / machine-checkable lane status.
- Previous tier: the handoff and control plane contained current lane status in
  prose, but there was no compact machine-readable index that could reject
  authority drift.
- New tier: `production_candidate` control-plane guard. This does not change
  product behavior or writer authority.
- Evidence: `docs/superpowers/schemas/productization_control_plane_schema.v1.json`
  defines the status-index schema and allowed readiness states.
  `docs/superpowers/validation/productization_status_index_v1.tsv` records each
  current lane exactly once, including parked broad Backfill, the 511-cell
  current writer scope, review/truth/recovery assets, targeted MS1 limited
  rescue, sample metadata no-output projection, ReviewAction parked writebacks,
  calibration freeze, and GUI out-of-scope state.
  `scripts/check_productization_state.py` validates lane uniqueness, required
  lanes, artifact hashes, doc anchors, authority-manifest alignment, and that
  parked/blocked/diagnostic/frozen/out-of-scope/review/truth/evidence lanes
  cannot write.
- Product surface changed: docs/spec/validation/test/helper script only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, or GUI behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`
  returned `Productization state index is consistent and fail-closed.` Focused
  tests passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_state_index.py -v --tb=short`
  (`9 passed`). Focused ruff passed for `scripts/check_productization_state.py`
  and `tests/test_productization_state_index.py`. Subagent review findings were
  fixed by adding workbook/selected-peak/selected-area flags, rejecting
  non-writer authority scopes and product-output changes, and requiring
  handoff/control-plane anchors. Full local gate also passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts/build_trace_overlay_recovery_report.py scripts/build_peak_choice_truth_lockbox.py scripts/check_productization_authority.py scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_state.py`;
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -v --tb=short -x`
  (`3813 passed, 1 skipped`);
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py`;
  `git diff --check` passed with LF/CRLF warnings only.
- Remaining blocker: this is a guardrail, not product behavior. It does not
  collect lockbox labels, recover new RAW evidence, or promote any parked lane.
- Next checkpoint: Goal 6 bounded non-broad lane hardening. Keep the status
  index in future productization gates.

### 2026-06-18 - missing_overlay_evidence_recovery_v1

- Lane: Missing-overlay evidence recovery / low-manual review infrastructure.
- Previous tier: 1087 rows were `evidence_required` because the source audit had
  `missing_overlay_path` and no trace/overlay links.
- New tier: `production_candidate` evidence-recovery asset. The rows remain
  `evidence_required`; no writer authority changes.
- Evidence: `scripts/build_trace_overlay_recovery_report.py` links all 1087
  missing-overlay rows from `mechanical_adjudication_index_v1.tsv` to existing
  family-level trace JSON, overlay PNG, hypothesis PNG, and sample-level trace
  fields across 114 families. The generated
  `docs/superpowers/validation/trace_overlay_recovery_report_v1.tsv` records
  `recovery_status=family_trace_overlay_recovered`, `sample_trace_present=TRUE`,
  and `post_recovery_evidence_grade=C_trace_recovered` for all 1087 rows.
  `docs/superpowers/validation/missing_overlay_resolution_summary_v1.json`
  records `status=all_existing_family_trace_overlays_recovered`.
- Product surface changed: docs/spec/validation/test/helper script only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, or GUI behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_trace_overlay_recovery_report.py`
  generated the recovery report. Focused tests passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_trace_overlay_recovery_contract.py -v --tb=short`
  (`5 passed`). Focused ruff passed for
  `scripts/build_trace_overlay_recovery_report.py` and
  `tests/test_trace_overlay_recovery_contract.py`. JSON parse passed for the
  recovery schema and summary.
- Remaining blocker: recovered trace/overlay evidence is not peak-choice truth
  and not area approval. These rows need structured review, truth labels, or a
  later reintegration/expected-diff authority goal before any product write can
  be considered.
- Next checkpoint: Goal 5 productization control-plane cleanup. Do not use this
  recovery report as ProductWriter input.

### 2026-06-18 - peak_choice_truth_lockbox_v1

- Lane: Peak-choice truth acquisition / low-manual review infrastructure.
- Previous tier: Review Packet / Approval Workflow v1 made the 3015
  trace-matched unresolved rows reviewable, but there was no independent
  truth-label protocol or family-split lockbox.
- New tier: `production_candidate` for a non-mutating truth acquisition
  contract. This does not change writer authority.
- Evidence: current lockbox schema
  `docs/superpowers/schemas/truth_label_schema.v1.json`
  defines the sampling manifest and label-log schema. The generated
  `docs/superpowers/validation/lockbox_sampling_manifest_v1.tsv` contains 72
  deterministic cases: 18 approved write-ready controls, 24 unresolved
  review-ready rows split across high-signal dirty / low-height / apex-delta /
  shape-width-scan contexts, 12 missing-overlay evidence-gap rows, 12 failed
  heldout-oracle negative cases, and 6 manual wrong-peak/no-peak fixtures.
  `docs/superpowers/validation/reviewer_label_log_v1.tsv` is intentionally
  header-only, and
  `docs/superpowers/validation/inter_reviewer_agreement_summary_v1.json`
  records `status=no_labels_collected`. Missing-overlay evidence-gap rows are
  not forced into fake area labels: they have `area_label_required=FALSE`, and
  all rows allow `not_assessed` / `unavailable`.
- Product surface changed: docs/spec/validation/test/helper script only. No
  ProductWriter, matrix, workbook, selected peak/area, counted detection,
  workbook schema, CLI/config, extraction default, or GUI behavior changed.
- Validation:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/build_peak_choice_truth_lockbox.py`
  generated the 72-case lockbox. Focused tests passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_peak_choice_truth_lockbox_contract.py -v --tb=short`
  (`6 passed`). Focused ruff passed for
  `scripts/build_peak_choice_truth_lockbox.py` and
  `tests/test_peak_choice_truth_lockbox_contract.py`. JSON parse passed for the
  truth schema and agreement summary. Subagent review found one P2 issue where
  missing-overlay rows could be forced into area labels; the generated manifest
  and tests now require honest `not_assessed` / `unavailable` handling.
- Remaining blocker: no reviewer labels or agreement metrics exist yet; lockbox
  membership and future labels cannot grant ProductWriter authority without a
  later authority manifest plus expected-diff goal.
- Next checkpoint: Goal 4 missing-overlay evidence recovery. Do not make the
  1087 missing-overlay rows writable simply because they are sampled here.

### 2026-06-18 - productization_review_packet_v1

- Lane: Backfill product-authority control plane /
  low-manual review workflow.
- Previous tier: Goal 0/1 authority/adjudication contract existed as a
  `production_candidate` control asset, but unresolved rows had no structured
  human approval packet.
- New tier: `production_candidate` for Review Packet / Approval Workflow v1 as
  a non-mutating review contract. This does not change writer authority.
- Evidence: `docs/superpowers/schemas/review_packet_schema.v1.json` defines
  packet/log schemas and forbids free-form value entry, matrix touch, and
  product authority from review approval.
  `docs/superpowers/validation/review_queue_v1.tsv` contains 3015
  trace-matched unresolved rows as `review_ready`; each row has candidate value,
  area/height/RT fields, trace JSON path, overlay PNG path, one review question,
  and allowed actions
  `approve_candidate;reject_candidate;escalate_unresolved`.
  `docs/superpowers/validation/review_decision_log_v1.tsv` is a structured
  header-only decision log template. The 1087 missing-overlay rows are not in
  this review queue and remain Goal 4 evidence-recovery work.
- Product surface changed: docs/spec/validation/test only. No ProductWriter,
  matrix, workbook, selected peak/area, counted detection, workbook schema,
  CLI/config, extraction default, or GUI behavior changed.
- Validation: focused checkpoint shard passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_review_packet_contract.py tests/test_check_productization_authority.py tests/test_productization_authority_mechanical_adjudication.py -v --tb=short`
  (`12 passed`); focused ruff passed for the checker/tests; authority checker
  passed; review packet schema JSON parse passed. Full local gate passed:
  `ruff check xic_extractor tests`, `mypy xic_extractor`,
  `pytest -v --tb=short -x` (`3792 passed, 1 skipped`), and
  `scripts/check_diagnostics_index.py`.
- Remaining blocker: this checkpoint does not implement truth labels, review UI,
  ProductWriter integration, or trace overlay recovery.
- Next checkpoint: after review/commit, proceed to Peak-Choice Truth Set /
  Lockbox v1. Do not treat review approval as ProductWriter authority.

### 2026-06-18 - productization_authority_checker_v1

- Lane: Productization authority firewall / Goal 0-1 hardening.
- Previous tier: `productization_authority_manifest_v1` and
  `mechanical_adjudication_index_v1` were `production_candidate` control
  assets validated by focused pytest only.
- New tier: unchanged, but the authority firewall now has a reusable checker
  entry point.
- Evidence: `scripts/check_productization_authority.py` validates the manifest,
  mechanical adjudication schema, and 4613-row index. It checks fail-closed
  unregistered scope behavior, the 511/4102 authority split, 3015/1087 evidence
  classes, parked broad Backfill, negative-evidence scopes, explanation-only
  sources, and source hashes. `tests/test_check_productization_authority.py`
  adds a forbidden-scope regression test by mutating a non-write row to
  `all_stability`.
- Product surface changed: checker/test only. It does not modify or call
  ProductWriter.
- Validation: focused checker tests passed and
  `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_productization_authority.py`
  returned `Productization authority contract is fail-closed.` Full local gate
  passed with the review-packet checkpoint.
- Remaining blocker: none for the current non-mutating checker contract; it must
  be kept in future productization authority gates.
- Next checkpoint: keep this checker in the PR/local gate set for future
  productization authority changes.

### 2026-06-18 - productization_authority_mechanical_adjudication_v1

- Lane: Backfill product-authority control plane /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: broad Backfill auto-write was `parked`; generated policy replay
  remained `production_ready` only for 511 current `write_ready` cells.
- New tier: unchanged for writer authority. The authority/adjudication control
  asset is `production_candidate`: it classifies the current 4613-row
  candidate/audit universe without adding ProductWriter authority.
- Evidence:
  `docs/superpowers/schemas/productization_authority_manifest.v1.json` freezes
  current product authority at `backfill_policy_write_ready_rows` / 511 cells
  and marks broad Backfill, quality explanations, quality blockers, and known
  negative-evidence scopes as non-authority. `docs/superpowers/schemas/mechanical_adjudication_schema.v1.json`
  defines fail-closed row decisions. `docs/superpowers/validation/mechanical_adjudication_index_v1.tsv`
  classifies all 4613 rows as 511 `write_ready`, 3015 requiring independent
  peak-choice/area truth, and 1087 requiring trace/overlay or reintegration
  evidence.
- Product surface changed: docs/spec/validation/test only. No ProductWriter,
  matrix, workbook, selected peak/area, counted detection, workbook schema,
  CLI/config, extraction default, or GUI behavior changed.
- Validation: focused contract shard passed:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests/test_productization_authority_mechanical_adjudication.py -v --tb=short`
  (`5 passed`), focused ruff passed for the new test file, full ruff passed,
  mypy passed, diagnostics index passed, `git diff --check` passed with only
  LF/CRLF warnings, and full pytest passed (`3785 passed, 1 skipped`).
  Subagent review found stale broad-Backfill wording; current-summary and
  historical-log entries were fixed so broad auto-write is `parked` and future
  work routes through authority/adjudication, structured review, truth, or trace
  evidence.
- Remaining blocker: none for the authority/adjudication contract itself. This
  contract does not implement review packets, truth lockbox labels, or trace
  overlay recovery.
- Next checkpoint: run review/validation, then build structured review packets
  or peak-choice truth/lockbox assets. Do not convert the 3015 or 1087 classes
  into writer pools without a later authority manifest update plus expected-diff
  gate.

### 2026-06-18 - backfill_broad_autowrite_feasibility_gate_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: broad Backfill was on implementation hold pending one read-only
  feasibility gate; current writer authority remained 511 `write_ready` cells.
- New tier: `parked` for broad Backfill auto-write. Current 511-cell
  `production_ready` writer authority is unchanged.
- Evidence: this control-plane entry and `docs/product/backfill.md` retain the
  durable `park_broad_backfill` decision after the historical note was retired.
  The retired note revalidated artifact hashes and counts, assessed ISTD
  reference semantics, compared the 3015 unresolved trace-matched rows against
  the 511 approved rows, and output exactly `park_broad_backfill`; its retirement
  record is
  `docs/superpowers/file-management/docs-cleanup/2026-06-30_source-copy-stub-removal-approval-packet.md`.
- Product surface changed: docs/control-plane only. No ProductWriter, matrix,
  workbook, selected area, counted detection, workbook schema, CLI/config, or
  TSV schema behavior changed.
- Validation: no-RAW artifact inspection only; no 85RAW rerun because existing
  artifacts were sufficient to decide the gate. `git diff --check` passed with
  only LF/CRLF warnings.
- Remaining blocker: broad Backfill can reopen only with a new independent
  truth source for peak-choice / family identity. `quality_blockers`,
  round-trip reintegration, all-stability, apex-delta, width-only, and
  shape-margin cannot be repackaged as a new broad writer path.
- Next checkpoint: stop broad Backfill diagnostics. Continue only existing
  511-cell release hardening or non-broad lanes unless the user approves a new
  independent truth-source proposal.

### 2026-06-18 - backfill_autowrite_ground_truth_strategy_reset_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: generated policy replay was `production_ready` for 511 current
  approved-evidence / observed-oracle rows; broad 4613-row Backfill remained
  `production_candidate`, with handoff wording still nudging the next step
  toward choosing another evidence class from blocker-token distribution.
- New tier: unchanged for writer authority. Broad Backfill expansion is on
  implementation hold until a read-only ground-truth packet is reviewed. This is
  a strategy reset, not a product behavior change.
- Evidence: reviewed the 2026-06-18 ChatGPT reset note and Backfill auto-write
  ground-truth strategy note, then recorded the blocking critique in
  this maintenance log and `docs/product/backfill.md`.
  Revalidated key no-RAW facts: Backfill promotes only MS1 morphology
  `primary_matrix_area`; existing replay artifacts still report 4613 policy
  rows, 511 `write_ready`, 0 `detected_flagged`, 4102 `blocked`, and 511/511
  writer expected-diff pass; targeted ISTD benchmark maps six active ISTDs to
  selected family IDs with 85/85 untargeted positives; a small matrix check over
  those six families found no `>3x median` high outliers and low-side outliers
  in every family.
- Validation: docs/no-RAW artifact inspection only; no RAW or 85RAW rerun.
  `git diff --check` passed with only LF/CRLF warnings.
- Remaining blocker: superseded by
  `backfill_broad_autowrite_feasibility_gate_v1`, which outputs
  `park_broad_backfill`.
- Next checkpoint: do not derive a writer predicate directly from
  `quality_blockers`; do not train/approve rows from the current round-trip
  reintegration oracle alone; do not include `missing_overlay_path` rows without
  regenerated trace evidence; if the gate cannot stay simple, park broad
  Backfill instead of adding more diagnostics.

### 2026-06-17 - sample_metadata_no_output_parity_tier_closeout_v1

- Lane: `sample_metadata_cross_module_parity_v1`.
- Previous tier: `production_surface` for extraction/alignment/
  RT-normalization injection-order parity and instrument-QC sidecar projection;
  `production_candidate` wording for roles.
- New tier: `production_ready` for no-output injection-order projection and
  additive instrument-QC metadata sidecar; `blocked` for any role/batch/matrix/
  exclusion behavior that would change quant output, counted detection,
  normalized values, workbook values, or primary matrix values.
- Evidence: the existing shared resolver in `xic_extractor.sample_metadata` is
  consumed by extraction `injection_order_source`, alignment
  `--sample-column-injection-order`, RT-normalization anchor `--sample-info`,
  and instrument-QC method-doc sidecar projection. The latest full local gate
  after the ReviewAction sidecar slice passed with `3779 passed, 1 skipped`,
  covering these parity tests without changing product values.
- Product surface changed: docs/tier wording only in this closeout. No CLI flag,
  schema column, workbook sheet, matrix schema, selected peak, selected area, or
  counted detection behavior changed.
- Validation: focused sample-metadata parity tests were rerun for this docs
  closeout:
  `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_sample_metadata.py tests\test_injection_rolling.py tests\test_extractor_run.py tests\test_instrument_qc_sequence_manifest.py tests\test_run_instrument_qc.py tests\test_alignment_pipeline_outputs.py::test_pipeline_orders_sample_columns_by_sample_metadata_v1 tests\test_alignment_pipeline_outputs.py::test_pipeline_orders_sample_columns_by_injection_order tests\test_alignment_pipeline_outputs.py::test_pipeline_keeps_input_sample_order_without_injection_source tests\test_rt_normalization_anchors.py::test_injection_reference_accepts_sample_metadata_without_value_effect -q`
  (`63 passed`). No RAW/85RAW rerun is needed because this lane only projects
  metadata into existing order lookup behavior.
- Remaining blocker: role-aware QC/blank/batch/matrix/exclusion behavior still
  needs a separate expected-diff gate and product decision before any value can
  change.
- Next checkpoint: keep role metadata as pass-through context only unless a new
  product contract names the exact output values allowed to change.

### 2026-06-17 - standard_peak_generated_policy_quality_explanation_sidecar_v1

- Lane: Backfill product-authority sidecars /
  `backfill_standard_seed_guard_scope_v1`.
- Previous tier: generated policy replay was `production_ready` for the 511
  current approved-evidence / observed-oracle rows, but the 4102 blocked rows
  were still hard to explain beyond coarse `next_evidence` buckets.
- New tier at the time: unchanged for writer authority. Generated policy replay
  remained `production_ready` only for 511 `write_ready` rows; broad 4613-row
  Backfill was still described as `production_candidate`. This broad-scope tier
  statement is superseded by the 2026-06-18 `park_broad_backfill` decision; the
  sidecar itself remains explanation-only.
- Evidence: `standard_peak_backfill_productization.py` now writes
  `standard_peak_backfill_policy_quality_explanations.tsv` beside
  `standard_peak_backfill_policy.tsv` and records its path/SHA/row count in
  `standard_peak_backfill_policy_summary.json`. The sidecar has one row per
  generated policy row,
  `schema_version=standard_peak_backfill_policy_quality_explanation_v1`,
  `explanation_only=TRUE`, quality blocker count/tokens, and source clean-status
  summary. It does not modify policy row hashes, evidence classes, writer
  authority, expected-diff scope, workbook output, selected peak/area, counted
  detection, or primary matrix semantics.
- Real-data replay: no new RAW run. Existing 85RAW artifacts were replayed
  no-RAW under
  `output/productization_realdata_seed_guard_85raw_20260617/generated_policy_quality_explained_no_raw_productization/`.
  The replay classified 4613 rows as 511 `write_ready`, 0 `detected_flagged`,
  and 4102 `blocked`; writer expected-diff remained 511/511 pass. The
  summary records `backfill_policy_quality_explanation_row_count=4613`, and the
  explanation sidecar has 4613 rows. The top explanation bucket is 1087
  `missing_overlay_path` rows, followed by combined shape/height/width/scan or
  apex-delta blocker combinations.
- Validation: `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest tests\test_standard_peak_backfill_productization.py -q`
  passed `31`; focused ruff and mypy passed for the productization module/test;
  the no-RAW real-data replay command exited `0`.
- Remaining blocker: this sidecar explains blocked rows; it does not make them
  writable. The old broadening checkpoint is superseded; do not use blocker
  distribution to choose another writer slice.
- Next checkpoint: superseded by
  `backfill_broad_autowrite_feasibility_gate_v1` and
  `productization_authority_mechanical_adjudication_v1`. Future work should
  build review/truth/evidence assets, not a new broad Backfill writer.

### 2026-06-17 - handoff_state_refresh_after_shape_margin_commit_v1

Historical checkpoint before `standard_peak_low_height_scoped_writer_v1`; keep
the later low-height writer entry above as the current tier source.

- Lane: documentation/control-plane closeout for the non-GUI productization
  goal.
- Previous tier: unchanged. Backfill high-signal and low-scan scoped writers
  remained `production_ready`; broad 4613-row standard-path activation and the
  low-height/apex-delta/width-only/shape-margin probes remained
  `production_candidate`; Targeted MS1 headless no-flag limited default
  remained `production_ready`; ReviewAction mutation remained parked.
- New tier: unchanged; this is a handoff drift correction, not a behavior
  promotion.
- Evidence: `cc-framework-improvements-productization.md` still had current
  state wording implying the shape-margin probe was uncommitted, the worktree
  still contained productization diff/untracked files, and no-flag `NL_FAIL`
  default rescue was still unavailable. The handoff now points at committed
  HEAD `3581a9e`, records that productization code/output scope was clean and
  the branch was ahead 12 before this docs-only refresh, and separates the ready
  Targeted MS1 headless default from the still-blocked GUI/broader-target
  surfaces. During this refresh, the only intended dirty scope is the
  handoff/control-plane docs diff.
- Validation: targeted stale-wording grep over the handoff after edit; docs-only
  diff, so no RAW rerun and no matrix/product output validation needed.
- Remaining blocker: none for this handoff drift. Product blockers remain the
  same except broad Backfill is now parked by the later 2026-06-18 feasibility
  gate; ReviewAction mutation needs stable IDs/sidecar/expected-diff approval,
  and GUI/broader Targeted MS1 rescue remain out of this ready claim.
- Next checkpoint: historical. Use the later authority/adjudication handoff as
  the current baseline; do not treat this entry as a prompt for a Backfill
  broadening slice.

### 2026-06-17 - provisional_production_candidate_gate_guard_audit_v1

- Previous tier: `diagnostic_only` with wording/no-promotion guard listed as next checkpoint.
- New tier: still `diagnostic_only`, now recorded as guarded; no product tier promotion.
- Evidence: existing CLI contract in `tools/diagnostics/provisional_backfill_candidate_gate.py` writes `alignment_production_candidate_gate.tsv/json` only; `tests/test_provisional_backfill_candidate_gate_cli.py` verifies the source matrix hash is unchanged and summary fields stay `readiness_label=diagnostic_only`, `production_ready=false`, and `matrix_contract_changed=false`.
- Product surface changed: docs/control-plane wording only. No CLI/schema/output behavior changed in this round.
- Validation: covered by full local gate `pytest -v --tb=short -x` (`3705 passed, 1 skipped`) plus `ruff`, `mypy`, diagnostics index, and `git diff --check`.
- Remaining blocker: none for diagnostic-only guard status. Any future rename is UX cleanup, not a product promotion.
- Next checkpoint: do not consume `alignment_production_candidate_gate.tsv` as product authority without a separate activation contract and expected-diff gate.

### 2026-06-16 - method_manifest_artifact_replay_policy_v1

- Previous tier: `production_ready` for targeted CLI replay parity, with ambiguous full-artifact wording
- New tier: `production_ready` for targeted CLI replay parity with explicit artifact policy; not full byte-exact workbook replay
- Evidence: `xic_extractor.output.method_manifest`; `tests/test_method_manifest.py`; `tests/test_workbook_compare.py`
- Product surface changed: additive `artifact_replay_policy` block in `method_manifest.json`.
- Safe behavior boundary: CSV artifacts are byte-exact replay artifacts; timestamped workbook parity is normalized through `scripts.compare_workbooks`; `method_manifest.json` is provenance-only because each run emits a new manifest.
- Validation: `python -m pytest tests\test_method_manifest.py tests\test_workbook_compare.py -q`
- Remaining blocker: full byte-exact workbook hash replay remains intentionally out of scope unless a future release needs stable workbook file identity.

### 2026-06-15 - alignment_output_contract_v1

- Previous tier: `production_candidate` / wording drift in control plane
- New tier: `production_surface` for output-level contract
- Evidence: `docs/product/alignment.md`; `xic_extractor.alignment.output_levels`; `scripts/run_alignment.py --output-level`; focused output-level tests
- Product surface changed: docs wording only; production level is `alignment_results.xlsx`, `alignment_matrix_identity.tsv`, and `review_report.html`; `alignment_matrix.tsv` remains machine/validation
- Validation: `python -m pytest tests\test_alignment_output_levels.py tests\test_alignment_pipeline_outputs.py::test_run_alignment_production_level_writes_user_artifacts_and_identity_tsv tests\test_alignment_pipeline_outputs.py::test_run_alignment_default_stays_machine_until_owner_validation_acceptance tests\test_run_alignment.py::test_run_alignment_cli_accepts_output_level_debug tests\test_run_alignment.py::test_run_alignment_cli_accepts_validation_minimal_output_level -q`
- Remaining blocker: release gate should continue guarding production/machine/debug/validation artifact separation; this does not claim full untargeted scientific production readiness
### 2026-06-19 - discovery_row_identity_and_claim_resolved_matrix_decision_v1

- Previous tier: `product_ready_default_matrix_activated` for the tracked
  default matrix bundle; new Discovery/alignment rerun evidence was
  `diagnostic_only`.
- New tier: unchanged. No active lane or Backfill writer authority change.
- Product surface changed: future alignment matrix decision behavior changes
  for a narrow resolved-claim case. A rescued cell that was moved into its
  duplicate-claim winner family by primary consolidation can write as
  `accepted_rescue`; a true wrong-hypothesis duplicate loser remains
  `review_rescue`.
- Evidence: `xic_extractor.alignment.csv_io` accepts discovery writer display
  rounding in candidate row identity checks; `xic_extractor.alignment.promotion_policy`
  treats duplicate claims as blocking only when the current row is not the claim
  winner. The QC3 `d4-N6-2HE-dA` cell at apex RT `23.261` now writes in the
  8RAW validation-minimal matrix row `Mz=300.16`, `RT=23.4313`.
- Validation: parser regression was RED before fix; focused parser/discovery
  tests passed (`44 passed`); production decision tests passed (`23 passed`);
  8RAW alignment preflight and rerun completed under
  `output/alignment/step3_precursor_inference_8raw_20260619_parserfix_claimfix/`.
- Remaining blocker: this does not regenerate the tracked default activation
  bundle. A separate default activation expected-diff task is still required
  before claiming the tracked default `quant_matrix.tsv` contains this row.

### 2026-06-20 - CID-NL product-ready alignment evidence v1

- Lane: CID-NL Discovery-to-alignment row identity / default-output unblocker.
- Previous tier: `product_ready_default_matrix_activated` for the tracked
  511-cell Backfill default matrix bundle; CID-NL rerun evidence was not yet a
  default activation source.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state
  changed. Current default matrix authority remains exactly the existing
  511-cell `backfill_policy_write_ready_rows` activation.
- Product surface changed: alignment primary-family consolidation is stricter.
  The shared-MS1 duplicate-claim fallback now only merges different
  neutral-loss tags; same-tag rows with distinct product identity must pass the
  normal product/loss identity tolerance or stay separate. The Discovery MS1
  candidate merge also reuses `ms1_feature_row_id` to merge same-feature seed
  evidence without requiring tail seed ranges to touch the peak center. These
  changes affect future Discovery/alignment row identity, not the tracked
  default ProductWriter bundle.
- Evidence: `docs/superpowers/validation/cid_nl_product_ready_alignment_v1/`
  records the 8RAW and 85RAW reruns. 8RAW alignment over
  `output/discovery/cid_nl_product_ready_8raw_20260620_fix2` produced
  `300.1605 -> 184.113` as `FAM001386`, 8/8,
  `product_matrix_identity_complete`, `identity_confidence=high`; it also
  preserved `301.165 -> 185.116` as its own `FAM001414`, 8/8,
  `product_matrix_identity_complete`, `identity_confidence=high`, with
  `consolidation_state=not_consolidated`.
- 85RAW alignment over
  `output/discovery/cid_nl_product_ready_85raw_20260620_fix2` produced
  `300.1605 -> 184.113` as `FAM011499`, 85/85,
  `product_matrix_identity_complete`, `identity_confidence=high`. The valid
  `301.165 -> 185.116` dR-tag row is preserved as `FAM011783`, 83/85,
  `product_matrix_identity_complete`, centered at
  `family_center_mz=301.165` and `family_product_mz=185.116`; it is no longer
  collapsed into the earlier erroneous `301.171 / 185.123` winner identity.
  This preserved 301 row still has `identity_confidence=review` and review
  flags for backfill evidence / missing independent backfill identity evidence;
  that residual risk is why this entry is evidence for a later expected-diff
  gate, not default activation readiness.
  TumorBC2312 provenance for the preserved row points to
  `TumorBC2312_DNA#19561@mz301.164978_p185.115845` with
  `write_matrix_value=TRUE`, while the Discovery source row remains
  `discovery_candidate_state=ms1_feature_nl_rescued` with
  `ms1_feature_row_id=TumorBC2312_DNA|DNA_dR|301.164978|23.341692`.
- Validation: red/green focused consolidation regression passed after the fix;
  `python -m pytest tests/test_alignment_primary_consolidation.py -q` passed
  `9`, and `python -m pytest tests/test_discovery_ms1_backfill.py -q` passed
  `20`. 8RAW and 85RAW alignment preflights and reruns completed with documented
  `validation-minimal`, `production-equivalent`, `audit-evidence-mode none`;
  85RAW used the canonical `validation-fast`, `raw-workers 11`, and
  `super-window` settings.
- Remaining blocker: this is `production_candidate` evidence for CID-NL
  Discovery-to-alignment row identity, not a default matrix promotion. A
  separate expected-diff/default-activation goal must decide whether these
  artifacts change the released default `quant_matrix.tsv`. That future goal
  must not treat Discovery candidates as matrix rows and must not let CID-NL/MS2
  evidence directly become ProductWriter authority.

### 2026-06-20 - CID-NL default activation preflight v1

- Lane: CID-NL Discovery-to-alignment row identity / default-output unblocker.
- Previous tier: unchanged from the tracked default
  `product_ready_default_matrix_activated` bundle plus CID-NL
  `production_candidate` alignment evidence.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state changed.
  Current default matrix authority remains exactly the existing 511-cell
  `backfill_policy_write_ready_rows` activation.
- Product surface changed: additive no-write checker/report only:
  `scripts/check_cid_nl_default_activation_preflight.py`. It reads existing
  alignment/default-activation artifacts and reports whether the current
  authority bundle can replay on the new CID-NL alignment identity. It does not
  write ProductWriter/default matrix outputs and does not add a new manifest.
- Evidence:
  `docs/superpowers/validation/cid_nl_default_activation_preflight_v1/`
  records `overall_status=blocked`, `target_alignment_evidence_status=pass`,
  `replay.status=blocked`, 511 accepted authority cells, 511 expected-diff rows,
  and 506 accepted cells whose old `peak_hypothesis_id` is missing from the new
  85RAW matrix identity. The first replay blocker is
  `FAM000380/BenignfatBC0980_DNA: peak_hypothesis_id missing from matrix identity`.
  The same checker confirms `300.1605 -> 184.113` as `FAM011499`, 85/85,
  high-confidence `DNA_dR` evidence with unique TumorBC2312 provenance,
  matching family/public/group identity, and exact TumorBC2312 source
  `TumorBC2312_DNA#19561@mz300.160635_p184.113235`, and preserves
  `301.165 -> 185.116` as `FAM011783`, 83/85, review-confidence `DNA_dR`
  evidence with unique TumorBC2312 provenance, matching family/public/group
  identity, and exact TumorBC2312 source
  `TumorBC2312_DNA#19561@mz301.164978_p185.115845`.
- Heartbeat audit: the 8RAW and 85RAW alignment reruns have
  `timing.live.json`; the Discovery input artifacts have `timing.json` only.
  No new RAW or default activation run was launched for this preflight.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_preflight.py -q`
  passed `7`, including focused negative tests for wrong source ID,
  family/public identity mismatch, and duplicate target provenance rows;
  `uv run ruff check scripts/check_cid_nl_default_activation_preflight.py tests/test_cid_nl_default_activation_preflight.py`
  passed; the real preflight command exited `0` and wrote the blocked summary.
- Remaining blocker: default activation needs an explicit ID bridge /
  expected-diff contract that proves the current 511-cell Backfill authority is
  preserved exactly while the recovered `300.1605 -> 184.113` row is
  materialized. Do not promote by treating Discovery candidates as matrix rows
  or by letting CID-NL/MS2 evidence directly become ProductWriter authority.

### 2026-06-20 - CID-NL default activation bridge gate v1

- Lane: CID-NL Discovery-to-alignment row identity / default-output unblocker.
- Previous tier: unchanged from the tracked default
  `product_ready_default_matrix_activated` bundle plus CID-NL
  `production_candidate` alignment evidence and blocked preflight.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state changed.
  Current default matrix authority remains exactly the existing 511-cell
  `backfill_policy_write_ready_rows` activation.
- Product surface changed: additive no-write checker/report only:
  `scripts/check_cid_nl_default_activation_bridge_gate.py`. It reads the old
  default activation identity, the new CID-NL alignment identity/matrix, the
  current ProductionAcceptanceManifest, expected-diff rows, and the target
  preflight summary. It emits a diagnostic bridge summary/audit; it does not
  write default ProductWriter outputs and does not create a replacement
  authority manifest.
- Evidence:
  `docs/superpowers/validation/cid_nl_default_activation_bridge_gate_v1/`
  records `overall_status=blocked`, `target_preflight.status=pass`, 511
  accepted authority cells, 511 expected-diff rows, 0 expected-diff content
  problems, and 83 accepted peak IDs. Peak-level bridge counts are 72 `pass`
  and 11 `blocked`; cell-level bridge counts are 147 `pass` and 364 `blocked`.
  The blocked cells split into 263 `new_baseline_already_has_value`, 82
  `new_identity_ambiguous`, and 19 `new_identity_missing`. Because bridge
  blockers are present, `activation_replay.status=not_run`.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_bridge_gate.py -q`
  passed `7`, including review-driven negative tests for expected-diff content
  mismatch and stale identity-to-matrix coordinates; `uv run ruff check scripts/check_cid_nl_default_activation_bridge_gate.py tests/test_cid_nl_default_activation_bridge_gate.py`
  passed; the real bridge gate command exited `0` and wrote the blocked
  summary/audit.
- Remaining blocker: a simple m/z/RT identity bridge cannot safely promote the
  CID-NL alignment artifact to default activation. The next product gate must
  reconstruct canonical row identity / authority classification first,
  separating still-blank accepted Backfill cells from cells that are now
  detected in the new baseline, before any expected-diff packet can be used for
  default activation.

### 2026-06-20 - CID-NL default activation authority reconstruction gate v1

- Lane: CID-NL default activation authority reconstruction / canonical identity
  unblocker.
- Previous tier: unchanged from `product_ready_default_matrix_activated` plus
  blocked CID-NL bridge evidence.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state changed.
  Current default matrix authority remains exactly the existing 511-cell
  `backfill_policy_write_ready_rows` activation.
- Product surface changed: additive no-write checker/report only:
  `scripts/check_cid_nl_default_activation_authority_reconstruction_gate.py`.
  It reuses the bridge gate evaluation and `QuantMatrixVersion` in-memory
  replay oracle, emits a diagnostic reconstruction summary/audit, and does not
  write ProductWriter/default matrix outputs or create a replacement authority
  manifest.
- Evidence:
  `docs/superpowers/validation/cid_nl_default_activation_authority_reconstruction_gate_v1/`
  records `overall_status=blocked`, 511 accepted authority cells, 511
  expected-diff rows, `candidate_replay.status=pass`, and 147 candidate
  Backfill writes. The 511 authority cells classify as 147 `write_ready_blank`,
  263 `superseded_by_detected_baseline`, 82 `blocked_identity_ambiguous`, and
  19 `blocked_identity_missing`; unresolved authority cells are therefore 101.
  Detected-baseline supersession is explicitly not Backfill writer authority.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_authority_reconstruction_gate.py -q`
  passed `6`, covering blank write-ready replay, detected-baseline no-write
  supersession, missing/ambiguous identity blockers, expected-diff content
  mismatch, and CLI artifact writes; focused ruff passed; the real
  reconstruction gate command exited `0` and wrote the blocked summary/audit.
- Remaining blocker: resolve the 11 old authority peaks behind the 101
  missing/ambiguous cells into durable canonical identity decisions before any
  default activation candidate can be built.

### 2026-06-20 - CID-NL default activation cell-local identity gate v1

- Lane: CID-NL default activation canonical identity unblocker.
- Previous tier: unchanged from `product_ready_default_matrix_activated` plus
  blocked CID-NL authority reconstruction evidence.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state changed.
  Current default matrix authority remains exactly the existing 511-cell
  `backfill_policy_write_ready_rows` activation.
- Product surface changed: additive no-write checker/report only:
  `scripts/check_cid_nl_default_activation_cell_local_identity_gate.py`. It
  reuses the authority reconstruction gate and resolves ambiguous cells only
  when exactly one candidate row already has a detected baseline value for the
  same sample. This is no-write detected-baseline supersession, not Backfill
  writer authority, and cannot authorize blank writes. Ambiguous candidates must
  also pass identity-to-matrix `Mz`/`RT` coordinate checks before their sample
  value can be considered.
- Evidence:
  `docs/superpowers/validation/cid_nl_default_activation_cell_local_identity_gate_v1/`
  records `overall_status=blocked`. The previous unresolved authority cell
  count was 101; this gate resolves 74 ambiguous cells as
  `cell_local_unique_detected_candidate_supersession`, leaving 27 unresolved
  cells. Full classification is 147 `write_ready_blank`, 263
  `superseded_by_detected_baseline`, 74
  `cell_local_unique_detected_candidate_supersession`, 19
  `blocked_identity_missing`, 5 `blocked_ambiguous_all_blank`, and 3
  `blocked_ambiguous_multiple_detected_candidates`; candidate coordinate
  mismatches are 0 in the real packet.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_cell_local_identity_gate.py -q`
  passed `6`, covering unique-detected candidate resolution, all-blank
  ambiguous blocking, multiple-detected ambiguous blocking, missing identity
  blocking, stale ambiguous identity-to-matrix coordinate rejection, and CLI
  artifact writes; focused ruff passed; the real cell-local identity gate
  command exited `0` and wrote the blocked summary/audit.
- Remaining blocker: 27 cells still need a durable canonical identity decision
  or explicit product-scope removal before any default activation candidate can
  be built.

### 2026-06-20 - CID-NL default activation remaining identity gate v1

- Lane: CID-NL default activation canonical identity unblocker / scope-removal
  contract.
- Previous tier: unchanged from `product_ready_default_matrix_activated` plus
  blocked CID-NL cell-local identity evidence.
- New tier: unchanged. No maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default activation bundle, or broad Backfill state changed.
  Current default matrix authority remains exactly the existing 511-cell
  `backfill_policy_write_ready_rows` activation.
- Product surface changed: additive no-write checker/report only:
  `scripts/check_cid_nl_default_activation_remaining_identity_gate.py`. It
  reuses the cell-local identity gate and converts only the three known
  terminal blocker classes into explicit no-write scope removals:
  `blocked_identity_missing`,
  `blocked_ambiguous_all_blank`, and
  `blocked_ambiguous_multiple_detected_candidates`. It fails closed for stale
  candidate coordinates or unknown blocked states. It does not write
  ProductWriter/default matrix outputs, choose ambiguous candidate rows, or
  create a replacement authority manifest.
- Evidence:
  `docs/superpowers/validation/cid_nl_default_activation_remaining_identity_gate_v1/`
  records `overall_status=pass`, 511 accepted authority cells, input unresolved
  authority cells `27`, and output unresolved authority cells `0`. The future
  activation-candidate contract is 147 `write_ready_blank` candidate writes,
  337 detected-baseline/no-write cells, and 27 explicit no-write scope
  removals. The 27 split into 19
  `scope_removed_missing_identity_no_write`, 5
  `scope_removed_ambiguous_blank_no_write`, and 3
  `scope_removed_ambiguous_multiple_detected_no_write`. The audit records row
  identity/source context, including the `FAM017604` exact/source identity row
  that is not a safe bridge replay target.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_remaining_identity_gate.py -q`
  passed `8`, covering write-ready pass-through, missing identity scope
  removal, non-bridge source identity context, all-blank ambiguity removal,
  multiple-detected ambiguity removal without choosing a candidate, unique
  detected no-write pass-through, stale candidate coordinate fail-closed, and
  CLI artifact writes; focused ruff passed; the real remaining-identity gate
  command with `--require-pass` exited `0` and wrote the passing
  summary/audit.
- Remaining blocker: default activation now needs a separate expected-diff /
  candidate-build gate that writes only the 147 blank cells, preserves the 337
  detected/no-write cells, omits the 27 scope removals, and continues to prove
  that CID-NL/MS2 evidence and candidate rows are not direct ProductWriter
  authority.

### 2026-06-20 - CID-NL default activation successor authority contract v1

- Lane: CID-NL default activation successor authority / candidate adoption
  gate.
- Previous tier: unchanged active default bundle plus resolved CID-NL remaining
  identity gate evidence.
- New tier: active default bundle unchanged. CID-NL default activation now has
  a `production_candidate` successor authority packet: it is a formal
  allow-list and replay artifact, but it is not installed as the active default
  ProductWriter output. No active lane, workbook/GUI behavior, selected
  peak/area, counted detection, broad Backfill state, or active default matrix
  bundle changed.
- Product surface changed: additive authority-candidate builder/report only:
  `scripts/build_cid_nl_default_activation_successor_authority_contract.py`.
  It reuses the remaining-identity gate, writes a successor
  `ProductionAcceptanceManifest v1` allow-list, writes matching expected-diff
  rows, writes a 511-cell human-readable decision ledger, and uses
  `QuantMatrixVersion` replay as the acceptance oracle. It does not treat
  CID-NL/MS2 evidence as direct ProductWriter authority and does not install the
  candidate as default output.
- Evidence: versioned report
  `docs/superpowers/validation/cid_nl_default_activation_successor_authority_contract_v1/README.md`
  plus ignored generated packet under
  `output/validation/cid_nl_default_activation_successor_authority_contract_v1/`.
  The packet records `overall_status=pass`, 147 successor write-authority
  manifest rows, 147 expected-diff rows, 511 decision rows, 337
  `no_write_detected_baseline_preserved`, 27 `no_write_omitted`, and 0
  unresolved decisions. Candidate replay writes exactly 147 cells with 0 unused
  expected-diff rows. Matrix delta summary records 147 changed cells, 0 missing
  writes, and 0 unexpected writes.
- Validation: focused checker tests passed `32`; focused ruff passed;
  `python -m scripts.check_production_acceptance_manifest --manifest output/validation/cid_nl_default_activation_successor_authority_contract_v1/successor_authority_manifest.tsv`
  passed; the real successor authority command with `--require-pass` exited `0`
  and wrote the passing authority packet under ignored `output/validation/`.
- Remaining blocker: human review / adoption gate must decide whether this
  successor authority candidate should become the active default activation.
  That later gate is the first point where default ProductWriter output or
  active default matrix files may change.

### 2026-06-20 - CID-NL default activation gallery review v1

- Lane: CID-NL default activation successor authority human review / adoption
  surface.
- Previous tier: active default bundle unchanged plus a `production_candidate`
  successor authority packet.
- New tier: unchanged. This gate creates a `diagnostic_only` human review
  surface; it does not change maturity tier, active lane, ProductWriter
  authority, Backfill writer authority, workbook/GUI behavior, selected
  peak/area, counted detection, or the active default QuantMatrix bundle.
- Product surface changed: additive diagnostic adapter/report only:
  `tools/diagnostics/cid_nl_default_activation_gallery_review.py`. It maps the
  successor authority decisions into the existing Backfill reconciliation
  Gallery row model and writes an overlay review queue for the existing
  `family_ms1_overlay_batch.py` producer. It intentionally does not create a
  second CID-NL-specific standalone HTML review system and does not accept
  RAW/DLL paths directly.
- Evidence: versioned report
  `docs/superpowers/validation/cid_nl_default_activation_gallery_review_v1/README.md`
  plus ignored generated review outputs under
  `output/validation/cid_nl_default_activation_gallery_review_v1/`. The gallery
  packet records `overall_status=pass`, 90 review groups, 529 representative
  cells, 85 overlay queue rows, 85 overlay-linked groups, 5 cellless overlay
  skips, 147 `write_authorized` successor cells, 337
  `no_write_detected_baseline_preserved` cells, and 27 `no_write_omitted`
  cells. The RAW-backed overlay batch succeeded for all 85 queued rows with 85
  RAW opens, 5872 XIC requests, 647 RAW chromatogram calls, and 633815 trace
  points. The generated summary records `product_writer_changed=false`,
  `default_quant_matrix_changed=false`, and
  `candidate_rows_are_matrix_rows=false`.
- Validation: focused gallery adapter tests passed; focused ruff passed; the
  real adapter command without overlay summary builds the queue but does not
  satisfy `--require-pass`; the command with a provenance-matched overlay
  summary and `--require-pass` passed. The RAW-backed overlay batch completed
  successfully; existing `gallery_browser_smoke.py` passed desktop, mobile,
  and 200 percent zoom checks against the generated gallery HTML.
- Remaining blocker: human adopt / hold / reject is still pending. Only an
  explicit adopt gate may proceed to a public-surface activation change, and
  that later gate must still prove exactly 147 writes, 337 preserved no-writes,
  27 omitted no-writes, and no matrix/provenance drift outside the approved
  expected diff.

### 2026-06-20 - CID-NL gallery review semantics correction v1

- Lane: CID-NL default activation successor authority human review / adoption
  surface.
- Previous tier: unchanged active default bundle plus the diagnostic Gallery
  review packet above.
- New tier: unchanged. This is a review-surface semantics correction only; it
  does not change maturity tier, active lane, ProductWriter authority, Backfill
  writer authority, workbook/GUI behavior, selected peak/area, counted
  detection, or the active default QuantMatrix bundle.
- Product surface changed: diagnostic HTML/review wording only in the existing
  Gallery owner. CID-NL successor review groups now render `Write / Preserve /
  Omit` impact labels instead of reusing the generic Backfill `NL /
  Candidate-only / Review` language. Overlay links are labeled as MS1 trace
  context, and the page states that orange detected/rescued overlay traces are
  MS1 trace status only, not NL-tag coverage and not ProductWriter authority.
- Evidence: regenerated Gallery packet under
  `output/validation/cid_nl_default_activation_gallery_review_v1/` still
  records 90 review groups and 85 overlay-linked groups. Text checks confirm
  the previous misleading strings `NL=detected required-tag anchors`,
  `No detected NL anchor on this hypothesis`, and
  `detected-anchor hypothesis evidence` are absent from the generated HTML.
- Validation: `python -m pytest tests/test_cid_nl_default_activation_gallery_review.py tests/test_backfill_evidence_reconciliation_gallery.py -q`
  passed with 51 tests; focused ruff passed; real Gallery regeneration with
  overlay summary passed; `gallery_browser_smoke.py` passed desktop, mobile,
  and 200 percent zoom checks; validation artifact retention passed.
- Remaining blocker: unchanged. Human adopt / hold / reject is still pending
  before any default ProductWriter activation can be considered.

### 2026-06-20 - CID-NL gallery Discovery-vs-Backfill review split v1

- Lane: CID-NL default activation successor authority human review / adoption
  surface.
- Previous tier: unchanged active default bundle plus the diagnostic Gallery
  review packet and semantics correction above.
- New tier: unchanged. This is a diagnostic review schema/HTML clarity change;
  it does not change maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area, counted
  detection, or the active default QuantMatrix bundle.
- Product surface changed: diagnostic HTML only. The shared
  `backfill_evidence_reconciliation_representative_cells.tsv` remains on the
  existing Backfill Gallery v0 schema; CID-NL old-to-successor identity is
  rendered from internal representative-cell context in HTML and from the
  CID-specific differential TSV below. CID-NL detail rows split the human
  review into `Discovery identity question` and
  `Backfill activation question`, and show explicit `old -> successor`
  identity mappings. Target guardrail rows are labeled as Discovery target
  context, not Backfill write candidates.
- Evidence: regenerated Gallery packet under
  `output/validation/cid_nl_default_activation_gallery_review_v1/` still
  records 90 review groups and 85 overlay-linked groups. Text checks confirm
  the new split labels and old-to-successor HTML mappings are present, while
  the shared representative-cells TSV keeps its v0 columns and old misleading
  NL-anchor wording remains absent.
- Validation: red-capable focused test first failed on the missing Discovery /
  Backfill split, then passed after the renderer/schema change. The focused
  Gallery pytest pair passed with 51 tests; focused ruff passed; real Gallery
  regeneration passed; Codex in-app browser verified an opened detail row
  exposes the split review questions and old-to-successor mapping;
  `gallery_browser_smoke.py` passed desktop, mobile, and 200 percent zoom
  checks.
- Remaining blocker: unchanged. Human adopt / hold / reject is still pending
  before any default ProductWriter activation can be considered.

### 2026-06-20 - CID-NL Evidence Review Gallery differential index v1

- Lane: CID-NL default activation successor authority differential human
  review.
- Previous tier: unchanged active default bundle plus the diagnostic Gallery
  review packet and Discovery-vs-Backfill split above.
- New tier: unchanged. This is an additive diagnostic review index and Gallery
  title/hero semantics correction only; it does not change maturity tier,
  active lane, ProductWriter authority, Backfill writer authority,
  workbook/GUI behavior, selected peak/area, counted detection, or the active
  default QuantMatrix bundle.
- Product surface changed: diagnostic HTML wording, provenance-checked overlay
  linking behavior, and additive ignored output TSV
  `cid_nl_discovery_identity_differential_review.tsv` from
  `tools/diagnostics/cid_nl_default_activation_gallery_review.py`. The Gallery
  now presents this mode as `CID-NL Discovery Identity Review` under the reused
  Evidence Review Gallery shell, not as a Backfill-domain report. The
  differential TSV joins old `source_peak_hypothesis_id` to
  `successor_peak_hypothesis_id`, source/successor m/z, RT, product m/z, tag,
  identity state, accepted-cell count, decision counts, transition type, and
  paired-overlay readiness. The shared Backfill representative-cells TSV schema
  remains v0; CID-NL identity machine rows live in the CID-specific
  differential TSV.
- Evidence: regenerated packet under
  `output/validation/cid_nl_default_activation_gallery_review_v1/` records 90
  review groups, 85 overlay-linked groups, and 87 old-to-successor/no-successor
  differential transitions. Of those transitions, 78 are
  `ready_for_paired_overlay` and 9 are `no_successor_target`. Example checked:
  `FAM000380 -> FAM000736` exposes the source audit row
  (`Mz=243.099`, `RT=23.6623`, `product=127.052`, `tag=DNA_dR`,
  `accepted_cell_count=0`) beside the successor production row
  (`Mz=246.136`, `RT=12.2645`, `product=130.089`, `tag=DNA_dR`,
  `accepted_cell_count=54`).
- Validation: focused CID-NL Gallery tests pass and assert the Evidence Review
  title, Discovery-vs-Backfill split, old-to-successor mappings, differential
  TSV rows, source/successor coordinate and tag state, and no misleading
  NL-anchor wording. They also assert stale overlay summaries are rejected and
  `--require-pass` fails until overlay-queued rows are linked to matching
  overlay-summary provenance. Focused ruff passed. The real Gallery adapter
  command with overlay summary passed and wrote the new differential TSV.
- Remaining blocker: paired differential overlay review is now the next human
  review step before adopt / hold / reject. Only a later explicit adopt gate
  may proceed to public-surface activation, and it must still prove exactly 147
  writes, 337 preserved detected-baseline no-writes, 27 omitted no-writes, and
  no matrix/provenance drift outside the approved expected diff.

### 2026-06-20 - CID-NL paired differential overlay review v1

- Lane: CID-NL default activation successor authority differential human
  review.
- Previous tier: unchanged active default bundle plus the diagnostic
  differential index above.
- New tier: unchanged. This is a `diagnostic_only` human-review evidence pass;
  it does not change maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, or the active default QuantMatrix bundle. No control-plane
  tier promotion is made by this pass.
- Product surface changed: additive diagnostic tool/report only:
  `tools/diagnostics/cid_nl_differential_overlay_review.py` plus ignored
  outputs under
  `output/validation/cid_nl_default_activation_gallery_review_v1/differential_overlays/`.
  It consumes the differential TSV and successor decisions, extracts source and
  successor XIC traces batched by RAW sample, renders paired PNG overlays and
  trace JSON, and reuses the existing Evidence Review Gallery shell. The PNGs
  now use the existing Gaussian15 visual convention and label the review as a
  source/successor `PeakHypothesis` differential overlay rather than family
  context. It does not create a second Discovery system, does not write default
  matrix files, and does not treat CID-NL/MS2 evidence or candidate rows as
  ProductWriter authority.
- Evidence: paired review output records `overall_status=pass`,
  `validation_label=diagnostic_only`, 78 rendered transitions, 0 failed
  transitions, 484 reviewed source/successor decision cells, 147
  `write_authorized` cells, and 337 detected-baseline preserved cells. The run
  opened 85 RAW samples and issued 968 XIC requests (`source` plus `successor`
  per reviewed cell). It wrote 78 PNG overlays, 78 trace JSON files,
  `cid_nl_differential_overlay_review_summary.tsv/json`, and
  `cid_nl_differential_overlay_gallery.html`. The trace JSON keeps raw trace
  arrays and records Gaussian15 max intensities for review.
- Validation: focused differential overlay pytest passed 3 tests and asserts
  ready-row filtering, priority ordering, source/successor request batching by
  sample, diagnostic-only payload flags, reused Gallery output, HTML
  source-to-successor identity, and shared representative TSV schema
  preservation. Focused ruff passed. The real `--require-pass` command
  completed with heartbeat at every 10 RAW samples and every 10 rendered
  overlays. Codex in-app Browser smoke, via a temporary localhost server after
  `file://` was blocked by browser policy, verified the generated HTML title,
  232 PNG lightbox links, collapsed-row `source->successor` identity,
  detail-row `source -> successor` identity, no stale "No mapping supplied"
  warning, and visible ProductWriter/default-matrix boundary text. A PNG pixel
  check confirmed the first overlay is nonblank at 2025 x 1140.
- Remaining blocker: human adopt / hold / reject is still pending. These 78
  paired overlays are the review material for the user decision; they do not by
  themselves promote the 147 successor writes or activate a new default
  ProductWriter bundle. Only a later explicit adopt gate may proceed to
  public-surface activation, and it must still prove exactly 147 writes, 337
  preserved no-writes, 27 omitted no-writes, and no matrix/provenance drift
  outside the approved expected diff.

### 2026-06-20 - overlay interpretation guide and differential wording v1

- Lane: shared human review surface for Backfill overlays and CID-NL Discovery
  differential overlays.
- Previous tier: unchanged active default bundle plus the diagnostic Gallery /
  differential overlay surfaces above.
- New tier: unchanged. This is a readability and review-surface correction
  only; it does not change maturity tier, active lane, ProductWriter authority,
  Backfill writer authority, workbook/GUI behavior, selected peak/area,
  counted detection, default matrix values, or Backfill authority. No
  control-plane tier promotion is made by this pass.
- Product surface changed: additive human-review HTML only. The maintained
  visual reader guide is now
  `docs/superpowers/validation/evidence_overlay_interpretation_guide.html`
  with retention category `human_guide_html`. Generated Gallery outputs copy
  that guide beside the rendered HTML and link it from the summary callout, so
  local artifact review works in the Codex in-app Browser without checking in
  generated gallery bundles. The guide itself now uses a generic imagegen
  concept illustration when opened from ignored output, plus clean
  deterministic SVG/HTML fallback diagrams and on-figure annotation pins. It no
  longer depends on a specific sample, family id, m/z, RT, or real-output PNG
  example. The differential PNG wording now explains that the lower-left panel
  compares same-sample source/successor identity support; different source and
  successor m/z values are expected when two row identities are being compared.
- Evidence: focused tests passed for the CID-NL differential overlay review,
  CID-NL default activation Gallery review, generic Backfill reconciliation
  Gallery, and validation artifact retention checker (`68 passed`). The real
  Gallery packet and paired differential overlays were regenerated
  successfully. Codex in-app Browser smoke over temporary localhost verified
  both generated Gallery outputs link the local HTML guide, the guide loads,
  PNG lightbox links remain present, and the ProductWriter/default-matrix
  boundary text remains visible. A visual PNG inspection confirmed the
  identity-support labels are visible after the layout padding fix.
- Remaining blocker: the guide makes the review surface readable, but it is not
  an adopt decision. CID-NL successor authority still needs an explicit human /
  model adopt-hold-reject decision and a separate expected-diff public-surface
  gate before any 147-cell successor write set can become active product
  behavior.

### 2026-06-21 - CID-NL feature-inclusion / identity-authority reframe v1

- Lane: CID-NL Discovery review-surface semantics and diagnostic contract.
- Previous tier: unchanged active default bundle plus the diagnostic
  successor-authority Gallery / differential overlay surfaces above.
- New tier: unchanged. This pass changes diagnostic/review wording and additive
  review TSV/JSON fields only. It does not change maturity tier, active lane,
  ProductWriter authority, Backfill writer authority, workbook/GUI behavior,
  selected peak/area, counted detection, default matrix values, or Backfill
  authority. No control-plane tier promotion is made by this pass.
- Product surface changed: diagnostic review schema and plain-language
  contract. `cid_nl_discovery_identity_differential_review.tsv` and
  `cid_nl_differential_overlay_review_summary.tsv/json` now expose
  `feature_inclusion_gate`, `identity_authority_gate`, and
  `source_successor_relationship`; the differential overlay summary also emits
  visual successor-MS1 and source/successor relationship states. Gallery labels
  changed from old `Write / Preserve` adoption wording to
  `Candidate / Existing` feature-review wording for CID-NL rows.
- Decision correction: source/successor overlays no longer answer "does
  successor beat source?" for every case. They first expose whether the
  successor is an MS1-backed untargeted feature candidate, then separately
  support replacement, merge, dedupe, migration, or co-existing-feature review.
  A source peak does not invalidate successor feature inclusion.
- Evidence: stable external-method takeaways are represented in
  `docs/product/untargeted-method.md`.
  Focused tests cover the updated Gallery wording, differential TSV gates,
  diagnostic-only payload flags, and visual relationship classification.
- Remaining blocker: feature inclusion is still not ProductWriter authority.
  Any future activation must build a narrowed expected-diff contract for rows
  that actually replace, merge, dedupe, or migrate existing product values.

### 2026-06-21 - CID-NL feature-inclusion gate v1

- Lane: CID-NL Discovery feature-inclusion product-gate diagnostic.
- Previous tier: unchanged active default bundle plus the diagnostic reframe
  above.
- New tier: unchanged. This pass adds a no-RAW diagnostic gate surface and does
  not change maturity tier, active lane, ProductWriter authority, Backfill
  writer authority, workbook/GUI behavior, selected peak/area, counted
  detection, default matrix values, or Backfill authority. No control-plane
  tier promotion is made by this pass.
- Product surface changed: additive diagnostic tool/output schema only:
  `tools/diagnostics/cid_nl_feature_inclusion_gate.py` reads existing
  differential review, paired-overlay summary, AI identity-triage TSVs, and an
  optional versioned manual review TSV. It writes ignored outputs under
  `output/validation/cid_nl_default_activation_gallery_review_v1/feature_inclusion_gate/`.
  The output surface is
  `cid_nl_feature_inclusion_gate_summary.tsv/json`,
  `cid_nl_feature_inclusion_gate_transitions.tsv`,
  `cid_nl_identity_expected_diff_queue.tsv`,
  `cid_nl_supported_candidate_expected_diff_contract.tsv`,
  `cid_nl_feature_inclusion_review_queue.tsv`, and
  `cid_nl_feature_inclusion_blocked_queue.tsv`, plus
  `cid_nl_manual_resolved_expected_diff_queue.tsv`,
  `cid_nl_manual_resolved_expected_diff_contract.tsv`,
  `cid_nl_manual_resolved_hold_queue.tsv`, and
  `cid_nl_user_review_queue.tsv`.
- Evidence: the gate completed with `overall_status=pass`,
  `validation_label=diagnostic_only`, 87 transitions, 78 overlay-ready
  transitions, 78 AI-adjudicated transitions, 147 candidate cells, 73 supported
  candidate cells, 46 review-required candidate cells, 28 current-bundle-blocked
  candidate cells, 337 existing-successor context cells, and 27 omitted
  no-target cells. The 73 supported cells span 14 expected-diff candidate
  transitions and have 73 cell-level expected-diff contract rows; the 46 review
  cells span 12 review transitions; the 28 blocked cells span 17 transitions
  excluded from the current activation bundle. Manual review subsequently
  resolves the 13 formerly user-review cells across 4 transitions into
  expected-diff design, leaving 0 user-review cells.
- Validation: focused pytest for the new gate passed and asserts diagnostic
  authority flags, expected-diff queue selection, review queue selection,
  blocked-queue exclusion, and guardrail behavior. Focused ruff passed. The
  real `--require-pass` command wrote the gate packet from existing artifacts
  without opening RAW or mutating product outputs.
- Remaining blocker: only the 95 expected-diff cells may be tested in an
  activated-copy candidate: 73 primary supported cells, 9 agent-resolved cells,
  and 13 manual-resolved cells. The 24 agent-held cells and 28 blocked cells
  stay out of the current activation bundle. Any future adoption still requires
  a separate public-surface activation check proving matrix/provenance changes
  and preserved existing/omitted behavior.

### 2026-06-21 - CID-NL activated-copy candidate v1

- Lane: CID-NL Discovery copy-only activation validation.
- Previous tier: unchanged active default bundle plus the diagnostic
  feature-inclusion gate above.
- New tier: unchanged. This pass writes an ignored validation-copy matrix and
  value-delta checker only. It does not change maturity tier, active lane,
  ProductWriter authority, Backfill writer authority, workbook/GUI behavior,
  selected peak/area, counted detection, default matrix values, or Backfill
  authority. No control-plane tier promotion is made by this pass.
- Product surface changed: additive diagnostic tool/output schema only:
  `tools/diagnostics/cid_nl_activation_copy_candidate.py` reads the 73-cell
  primary expected-diff contract, the 9-cell agent-resolved expected-diff
  contract, and the 13-cell manual-resolved expected-diff contract. It fails
  closed if user-review/agent-hold/manual-hold/blocked transitions are
  included, copies the current public `alignment_matrix.tsv`, and writes
  validation outputs under
  `output/validation/cid_nl_default_activation_gallery_review_v1/activation_copy_candidate/`.
  The output surface is `alignment_matrix_activated_copy.tsv`,
  `alignment_matrix_identity_activated_copy.tsv`,
  `cid_nl_activation_copy_value_delta.tsv`, and
  `cid_nl_activation_copy_candidate_summary.tsv/json`.
  `tools/diagnostics/cid_nl_activation_copy_acceptance.py` then writes
  `cid_nl_activation_copy_acceptance_summary.tsv/json` and
  `cid_nl_activation_copy_matrix_diff.tsv` under the activation-copy
  `acceptance/` subdirectory.
- Evidence: the activated-copy command completed with
  `activation_copy_status=pass`,
  `validation_label=diagnostic_only_activated_copy_candidate`, 95 contract
  cells, 95 changed blank matrix cells, and 20 candidate transitions. The
  summary reports `product_writer_changed=FALSE`,
  `default_quant_matrix_changed=FALSE`, `workbook_gui_changed=FALSE`, and
  `candidate_rows_are_matrix_rows=FALSE`.
- Review split: the former 46 review cells are now split into 9
  agent-resolved expected-diff cells, 24 agent-held cells, and 13
  manual-resolved expected-diff cells. The user-review queue is now 0 after
  human/domain review accepted successor feature inclusion for
  `FAM011440 -> FAM015713`, `FAM011837 -> FAM016144`,
  `FAM018342 -> FAM026285`, and `FAM020176 -> FAM030972`. The
  `FAM020176` source trace remains explicitly `unjudgeable_bad_trace`; that
  verdict supports only `FAM030972` feature inclusion, not source deletion or
  migration.
- Validation: focused pytest for feature-inclusion and activated-copy gates
  passed, focused ruff passed, and the real `--require-pass` activated-copy and
  acceptance commands wrote the 95-cell value delta and exact matrix-diff
  acceptance without opening RAW or mutating product outputs. Acceptance
  reports `acceptance_status=pass`, 95 contract cells, 95 value-delta rows, 95
  matrix changed cells, 20 candidate transitions, 0 forbidden overlaps, 0
  unexpected matrix changes, 0 missing matrix changes, and
  `production_ready=FALSE`.
- Remaining blocker: this is still not the default activation bundle. Before
  adoption, an explicit adopt gate must preserve the 337 existing context cells
  and 27 omitted no-target cells, prove provenance/default-matrix behavior, and
  make a deliberate public-surface promotion decision.

### 2026-06-21 - CID-NL activation adopt gate v1

- Lane: CID-NL Discovery adopt/hold decision for the narrowed activated-copy
  validation bundle.
- Previous tier: unchanged active default bundle plus the 95-cell
  diagnostic activated-copy candidate and acceptance gate above.
- New tier: unchanged for the active product. The 95-cell CID-NL bundle is now
  `production_candidate_activation_adopt_gate`, meaning it is adopt-ready as
  input to a later explicit public activation change. This pass does not change
  the active default QuantMatrix tier, ProductWriter authority, Backfill writer
  authority, workbook/GUI behavior, selected peak/area, counted detection,
  default matrix values, or current 511-cell Backfill authority.
- Product surface changed: additive diagnostic/adoption tool and output schema
  only. `tools/diagnostics/cid_nl_activation_adopt_gate.py` reads the 73-cell
  primary expected-diff contract, 9-cell agent-resolved contract, 13-cell
  manual-resolved contract, feature-inclusion summary, activated-copy summary,
  activated-copy acceptance summary, value-delta TSV, and forbidden
  user-review/hold/blocked queues. It writes ignored outputs under
  `output/validation/cid_nl_default_activation_gallery_review_v1/activation_adopt_gate/`:
  `cid_nl_activation_adopt_gate_summary.tsv/json` and the compact
  `cid_nl_activation_adopt_manifest.tsv` grouped at transition level. The full
  95-cell diff stays in ignored generated outputs, not version control.
- Evidence: the adopt gate completed with `adopt_gate_status=adopt_ready`,
  `validation_label=production_candidate_activation_adopt_gate`,
  `activation_bundle_adopt_ready=TRUE`, 95 contract cells, 95 changed
  validation-copy matrix cells, 20 candidate transitions, 73 primary
  expected-diff cells, 9 agent-resolved expected-diff cells, 13
  manual-resolved expected-diff cells, 24 agent-held cells, 0 manual-held
  cells, 0 user-review cells, 28 blocked cells, 337 existing-successor context
  cells, 27 omitted no-target cells, 0 forbidden overlaps, 0 unexpected matrix
  changes, and 0 missing matrix changes. The gate reports
  `product_writer_changed=FALSE`, `default_quant_matrix_changed=FALSE`,
  `workbook_gui_changed=FALSE`, `candidate_rows_are_matrix_rows=FALSE`, and
  `production_ready=FALSE`.
- Validation: focused ruff, pytest, and mypy passed for the new adopt gate, and
  the real `uv run python -m tools.diagnostics.cid_nl_activation_adopt_gate
  --require-pass` command produced the adopt-ready summary and 20-transition
  manifest from existing artifacts without opening RAW or mutating product
  outputs.
- Remaining blocker: default activation has not happened. The next gate, if
  chosen, is an explicit public-surface activation change that intentionally
  updates the default matrix/ProductWriter contract and proves the same 95-cell
  expected diff, provenance, preserved 337 existing-successor context cells,
  preserved 27 omitted no-target cells, and no unrelated output drift.

### 2026-06-21 - CID-NL Default Product Activation v1

- Lane: CID-NL Discovery explicit public default activation for the narrowed
  adopt-ready bundle.
- Previous tier: `production_candidate_activation_adopt_gate` for 95
  CID-NL expected-diff cells across 20 transitions. The existing Backfill
  default bundle remained `product_ready_default_matrix_activated` for exactly
  511 cells under `backfill_policy_write_ready_rows`.
- New tier: `production_ready` / `product_ready_default_matrix_activated` for
  a new registered CID-NL writer scope:
  `cid_nl_adopt_ready_feature_inclusion_95_cells`. This is an explicit
  default matrix/ProductWriter authority change for the 95 CID-NL cells only.
  The existing Backfill writer scope remains exactly 511 cells and broad
  Backfill remains parked.
- Product surface changed: yes. `scripts/build_cid_nl_default_product_activation.py`
  is a fail-closed activation builder/checker that reuses
  `ProductionAcceptanceManifest` and `QuantMatrixVersion` via
  `scripts/build_quant_matrix_version.py`. It filters the successor-authority
  manifest to the 95 adopt-ready keys, writes a generated activation manifest
  and expected-diff packet under ignored `output/validation/`, runs the existing
  activation writer, and publishes only compact docs artifacts under
  `docs/superpowers/validation/cid_nl_default_product_activation_v1/`.
- Authority boundary: CID-NL/MS2 evidence, overlays, Gallery review, and
  candidate rows are not direct ProductWriter authority. Writer authority is
  granted only by the generated `ProductionAcceptanceManifest` rows plus
  `QuantMatrixVersion` expected-diff replay. `workbook_or_gui_changed=FALSE`,
  `selected_peak_area_or_counting_changed=FALSE`,
  `backfill_writer_authority_changed=FALSE`,
  `cid_nl_ms2_direct_productwriter_authority=FALSE`, and
  `candidate_rows_are_matrix_rows=FALSE`.
- Evidence: the real no-RAW activation command
  `uv run python -m scripts.build_cid_nl_default_product_activation --require-pass`
  passed. The summary records `status=pass`,
  `activation_label=product_ready_default_matrix_activated`, 95 accepted
  Discovery default writes, 20 transitions, 337 existing-successor context
  cells preserved, 27 omitted no-target cells preserved,
  `expected_diff_count=95`, `written_discovery_cell_count=95`,
  `unused_expected_diff_count=0`, exact matrix delta keyset pass, and cell
  provenance exact keyset pass.
- Retention: the full generated matrix/provenance/default-output TSVs stay
  externalized under ignored
  `output/validation/cid_nl_default_product_activation_v1/`. Version control
  keeps only `README.md`, `cid_nl_default_product_activation_summary.json`,
  `cid_nl_default_product_activation_checks.tsv`, and the 20-row compact
  `cid_nl_default_product_activation_manifest.tsv`.
- Control plane update: updated. `productization_status_index_v1.tsv` now
  contains the new writer lane `cid_nl_default_product_activation_v1`, and
  `productization_authority_manifest.v1.json` registers the new allowed writer
  scope alongside the existing 511-cell Backfill scope. The state checker was
  changed from "only Backfill can write" to "only registered writer lanes can
  write".
- Validation: focused tests pass for the new builder:
  `uv run pytest tests/test_cid_nl_default_product_activation.py -q`; focused
  lint passes for the new builder/test; the real activation command and
  `--check-only` validation pass. Broader state/checker tests are required
  after the control-plane/handoff/hash updates in this section.
- Next checkpoint: keep CID-NL default activation bounded to the 95-cell
  expected-diff/provenance contract. Any expansion beyond these 95 cells, any
  source/successor deletion or migration, or any attempt to treat CID-NL/MS2
  evidence as direct ProductWriter authority requires a new expected-diff gate.

### 2026-06-21 - CID-NL Discovery Lane Terminology Cleanup v1

- Lane: CID-NL Discovery public artifact vocabulary and roadmap cleanup.
- Tier change: none. `cid_nl_default_product_activation_v1` remains
  `production_ready` / `product_ready_default_matrix_activated` for the same
  95-cell scope only. Backfill remains 511 cells, and broad Backfill remains
  parked.
- Product surface changed: yes, but only the compact CID-NL public artifacts
  changed schema/wording. The default matrix values, ProductWriter authority,
  workbook/GUI behavior, selected peak/area, counted detection, and active lane
  set did not change.
- Cleanup decision: the CID-NL summary now exposes Discovery-first fields:
  `product_lane=cid_nl_discovery`,
  `product_scope_kind=discovery_default_activation`,
  `accepted_discovery_cell_count=95`,
  `written_discovery_cell_count=95`, and
  `default_activation_effect=write_cid_nl_discovery_default_cell`. Legacy
  `accepted_backfill`, `written_backfill_count`,
  `cell_status=accepted_backfill`, and
  `write_accepted_backfill` terms remain only as QuantMatrixVersion
  compatibility fields, not as CID-NL product-scope language.
- Evidence: `cid_nl_default_product_activation_checks.tsv` adds
  `discovery_terminology_boundary=pass`, and the compact transition manifest
  now separates `default_activation_effect` from
  `legacy_quant_matrix_effect`.
- Next checkpoint: finish Discovery first. Do not reopen broad Backfill while
  the active product question is CID-NL Discovery expansion. Any CID-NL
  expansion beyond the 95-cell scope needs its own Discovery expected-diff,
  provenance, and row-identity gate.

### 2026-06-21 - CID-NL Discovery Release Slice Checker v1

- Lane: CID-NL Discovery release-slice stabilization for the already activated
  95-cell scope.
- Tier change: none. `cid_nl_default_product_activation_v1` remains
  `production_ready` / `product_ready_default_matrix_activated` for the same
  95 cells only. No active lane, writer scope, matrix value, workbook/GUI,
  selected peak/area, counted detection, or Backfill authority changed.
- Product surface changed: validation/replay surface only. Added
  `scripts/check_cid_nl_discovery_release_slice.py` to assert the current
  95-cell CID-NL Discovery release claim is stable without rerunning RAW or
  producing product outputs.
- The checker binds the release slice to the existing activation summary,
  compact manifest, checks TSV, status index, authority manifest, roadmap,
  handoff, and control-plane anchors. It requires Discovery-first public terms
  while allowing legacy `QuantMatrixVersion` writer compatibility fields only
  as compatibility.
- Evidence: `uv run python -m scripts.check_cid_nl_discovery_release_slice`
  passes; `uv run pytest tests/test_cid_nl_discovery_release_slice.py -q`
  passes; focused ruff for the checker/test passes.
- Next checkpoint: after committing this cleanup, continue Discovery-only work.
  Either keep the 95-cell slice as the release claim, or define the next CID-NL
  expansion slice with a new expected-diff/provenance/row-identity gate.

### 2026-06-21 - CID-NL Discovery Full-Scope Classification v1

- Lane: CID-NL Discovery full-scope classification for the current candidate
  universe.
- Tier change: none. `cid_nl_default_product_activation_v1` remains
  `production_ready` / `product_ready_default_matrix_activated` for exactly the
  same 95 cells. No active lane, writer scope, matrix value, workbook/GUI,
  selected peak/area, counted detection, or Backfill authority changed.
- Product surface changed: validation/replay surface only. Added
  `scripts/check_cid_nl_discovery_full_scope_classification.py` and retained
  the compact summary/checks/manifest under
  `docs/superpowers/validation/cid_nl_discovery_full_scope_classification_v1/`.
- Classification decision: the current 147 candidate-cell Discovery universe is
  now explicitly partitioned into 95 accepted default-output cells, 24 held
  cells, and 28 blocked cells. The same gate also preserves 337
  existing-successor context cells and 27 omitted no-target cells as no-write
  context.
- Evidence: the checker passes with `candidate_partition_complete=pass`,
  `accepted_matches_default_activation=pass`,
  `no_product_authority_in_classifier=pass`,
  `target_300_184_source_context_preserved=pass`, and
  `target_301_185_exact_pair_preserved=pass`. The release-slice checker now
  calls this full-scope checker, validation artifact retention, and bounded
  non-broad lane validation so the 95-cell release claim cannot drift away from
  the 147-cell Discovery classification or retained product-gate surface.
- Next checkpoint: do not open another expansion slice for the current CID-NL
  universe. Future CID-NL expansion requires new evidence or a new product
  question plus its own expected-diff/provenance/row-identity gate.

### 2026-06-21 - CID-NL 85RAW Universe Closure v1

- Lane: CID-NL Discovery 85RAW-derived universe closure for the already active
  default scope.
- Tier change: none. `cid_nl_default_product_activation_v1` remains
  `production_ready` / `product_ready_default_matrix_activated` for exactly the
  same 95 cells. No active lane, writer scope, matrix value, workbook/GUI,
  selected peak/area, counted detection, or Backfill authority changed.
- Product surface changed: validation/replay surface only. Added
  `scripts/check_cid_nl_85raw_universe_closure.py` and retained the compact
  summary/checks/manifest under
  `docs/superpowers/validation/cid_nl_85raw_universe_closure_v1/`.
- Closure decision: the current 85RAW-derived successor-authority universe is
  closed for the active CID-NL Discovery product question. The checker binds
  the activation to the 85RAW fix3 alignment inputs and proves 511 successor
  decisions = 147 write-authorized candidate cells + 337 detected-baseline
  preserved cells + 27 omitted no-target cells. The 147 write-authorized
  candidates are exactly 95 accepted default-active cells + 24 held cells + 28
  blocked cells.
- Evidence: the checker passes with `successor_decision_total_count=pass`,
  `successor_decision_partition=pass`,
  `candidate_transition_partition_exact=pass`,
  `accepted_default_keyset_exact=pass`,
  `default_matrix_delta_stays_95=pass`, and `85raw_fix3_input_binding=pass`.
  The release-slice checker now calls this closure checker so the 95-cell
  release claim cannot drift away from the 511-cell 85RAW-derived successor
  decision universe.
- Control-plane decision: no maturity tier or active lane update is needed.
  This gate adds proof around the existing default activation; it does not
  register new ProductWriter authority or broaden CID-NL/Backfill scope.
- Next checkpoint: future CID-NL expansion requires new evidence or a new
  product question plus its own expected-diff/provenance/row-identity gate. Do
  not reopen the current 147-cell candidate universe as another expansion slice.

### 2026-06-21 - CID-NL Discovery Feature-Inclusion Boundary Clarification v1

- Lane: CID-NL Discovery default activation contract wording and release-gate
  hardening for the already active 95-cell scope.
- Tier change: none. `cid_nl_default_product_activation_v1` remains
  `production_ready` / `product_ready_default_matrix_activated` for exactly the
  same 95 cells. No active lane, writer scope, matrix value, workbook/GUI,
  selected peak/area, counted detection, or Backfill authority changed.
- Product surface changed: compact validation/replay surface only. The CID-NL
  default activation summary/checks/README now explicitly records
  `feature_inclusion_authority_basis=successor_self_evidence_manifest_expected_diff_and_cid_nl_tag`,
  `matrix_row_universe_policy=discovery_expanded_85raw_alignment_rows`,
  `low_prevalence_feature_policy=allowed_for_untargeted_downstream_filter`,
  and
  `source_successor_identity_scope=identity_review_only_not_feature_inclusion_blocker`.
- Decision correction: sparse untargeted rows are not a CID-NL false-positive
  blocker by themselves; downstream feature filtering owns prevalence and
  missingness filtering. Source/successor m/z or RT similarity is also not the
  feature-inclusion gate. It only belongs to identity authority questions such
  as merge, dedupe, replacement, or migration.
- Evidence: the activation builder now emits
  `successor_self_evidence_contract=pass`,
  `matrix_row_universe_policy=pass`, and
  `source_successor_identity_scope=pass`. The release-slice checker requires
  these checks and the new summary fields, so the 95-cell activation can only
  be replayed when successor-self tag/quant/manifest/provenance evidence is
  explicit.
- Control-plane decision: no maturity tier or active lane update is needed.
  This entry updates the product-gate contract language and checker surface
  only; it does not register new ProductWriter authority or broaden CID-NL /
  Backfill scope.

### 2026-06-21 - Backfill Expansion Census v1

- Lane: Backfill diagnostic census after CID-NL Discovery default activation.
- Tier change: none. `backfill_current_write_ready_scope` remains
  `production_ready` for exactly 511 cells under
  `backfill_policy_write_ready_rows`; `cid_nl_default_product_activation_v1`
  remains `production_ready` for exactly 95 CID-NL Discovery cells. Broad
  Backfill remains parked.
- Product surface changed: additive validation/replay surface only. Added
  `scripts/check_backfill_expansion_census.py` and retained compact docs
  artifacts under
  `docs/superpowers/validation/backfill_expansion_census_v1/`. The full
  opportunity cell map is externalized under
  `output/validation/backfill_expansion_census_v1/`.
- Census decision: the 20 CID-NL active successor rows span 1700 sample-cells:
  676 directly detected cells, 95 Discovery default-written cells, and 929
  blank cells. Those 929 blank cells are Backfill pressure only, not writable
  cells. They require sample-local MS1/identity evidence before any future
  Backfill expected-diff gate.
- Parked pressure: 23 write-authorized but non-active CID-NL rows add 1010
  blank cells of future pressure. They remain parked until Discovery
  feature-inclusion authority changes; they must not enter Backfill review as
  active candidates.
- Authority boundary: no RAW/85RAW rerun, ProductWriter change, default matrix
  change, workbook/GUI change, selected peak/area change, counted-detection
  change, Backfill writer authority change, or broad Backfill unpark happened.
  Candidate blank cells are not matrix rows.
- Evidence: `python -m scripts.check_backfill_expansion_census` and
  `python -m scripts.check_backfill_expansion_census --check-only` pass from
  existing 85RAW-derived artifacts.
- Control-plane decision: no maturity tier, active lane, writer scope, or
  default authority update is needed. This census is the next Backfill planning
  gate, not a promotion.

### 2026-06-21 - Backfill Expansion Evidence Availability v1

- Lane: Backfill no-RAW evidence availability gate for the CID-NL-created
  active blank cells.
- Tier change: none. `backfill_current_write_ready_scope` remains
  `production_ready` for exactly 511 cells under
  `backfill_policy_write_ready_rows`; `cid_nl_default_product_activation_v1`
  remains `production_ready` for exactly 95 CID-NL Discovery cells. Broad
  Backfill remains parked.
- Product surface changed: additive validation/replay surface only. Added
  `scripts/check_backfill_expansion_evidence_availability.py` and retained
  compact docs artifacts under
  `docs/superpowers/validation/backfill_expansion_evidence_availability_v1/`.
  The full 1939-cell availability map is externalized under
  `output/validation/backfill_expansion_evidence_availability_v1/`.
- Availability decision: exact sample-local matching is required by
  `peak_hypothesis_id + sample_stem`. Existing mechanical adjudication and
  trace recovery artifacts cover 0/929 active Backfill pressure cells; 0 cells
  are immediate expected-diff-ready; all 929 active blank cells require new
  sample-local MS1/identity evidence before any Backfill writer gate can be
  proposed.
- Parked pressure: the 1010 parked future-pressure cells remain parked. They
  are not routed through Backfill until Discovery feature-inclusion authority
  changes.
- Authority boundary: no RAW/85RAW rerun, ProductWriter change, default matrix
  change, workbook/GUI change, selected peak/area change, counted-detection
  change, Backfill writer authority change, row/family evidence projection, or
  broad Backfill unpark happened. Candidate blank cells are not matrix rows.
- Evidence: `python -m scripts.check_backfill_expansion_evidence_availability`
  and
  `python -m scripts.check_backfill_expansion_evidence_availability --check-only`
  pass from existing 85RAW-derived artifacts.
- Control-plane decision: no maturity tier, active lane, writer scope, or
  default authority update is needed. This gate is a hold decision:
  `hold_for_new_sample_local_ms1_identity_evidence`.

### 2026-06-21 - Backfill Expansion Sample-Local MS1 Evidence v1

- Lane: Backfill no-RAW sample-local MS1/identity evidence gate for the
  CID-NL-created active blank cells.
- Tier change: none. `backfill_current_write_ready_scope` remains
  `production_ready` for exactly 511 cells under
  `backfill_policy_write_ready_rows`; `cid_nl_default_product_activation_v1`
  remains `production_ready` for exactly 95 CID-NL Discovery cells. Broad
  Backfill remains parked.
- Product surface changed: additive validation/replay surface only. Added
  `scripts/check_backfill_expansion_sample_local_ms1_evidence.py` and retained
  compact docs artifacts under
  `docs/superpowers/validation/backfill_expansion_sample_local_ms1_evidence_v1/`.
  The full 929-cell evidence map and 20-row RAW overlay queue are externalized
  under `output/validation/backfill_expansion_sample_local_ms1_evidence_v1/`.
- Evidence decision: exact sample-local matching remains required by
  `peak_hypothesis_id + sample_stem`. The current fix3 85RAW-derived alignment
  evidence contains exact sample-local MS1/identity rows for 675/929 active
  Backfill pressure cells. The remaining 254/929 cells have no exact
  alignment cell evidence row and stay held.
- Gate decision: the 675 present cells are bounded inputs for a future RAW
  overlay trace/identity gate, not write-ready cells. The 254 missing cells
  need separate missing-alignment-cell review. Row/family identity is not
  projected onto sample cells.
- Authority boundary: no RAW/85RAW rerun, ProductWriter change, default matrix
  change, workbook/GUI change, selected peak/area change, counted-detection
  change, Backfill writer authority change, row/family evidence projection, or
  broad Backfill unpark happened. Candidate blank cells are not matrix rows.
- Evidence: `python -m scripts.check_backfill_expansion_sample_local_ms1_evidence`
  and
  `python -m scripts.check_backfill_expansion_sample_local_ms1_evidence --check-only`
  pass from existing 85RAW-derived artifacts.
- Control-plane decision: no maturity tier, active lane, writer scope, or
  default authority update is needed. This gate narrows the next Backfill
  evidence work to `675 present -> RAW overlay trace/identity gate` and
  `254 missing -> hold/review`.

### 2026-06-21 - Backfill Expansion RAW Overlay Trace Identity v1

- Lane: Backfill RAW-backed evidence-only overlay gate for the CID-NL-created
  active blank cells that already had exact sample-local alignment evidence.
- Tier change: none. `backfill_current_write_ready_scope` remains
  `production_ready` for exactly 511 cells under
  `backfill_policy_write_ready_rows`; `cid_nl_default_product_activation_v1`
  remains `production_ready` for exactly 95 CID-NL Discovery cells. Broad
  Backfill remains parked.
- Product surface changed: additive validation/replay surface only. Added
  `scripts/check_backfill_expansion_raw_overlay_trace_identity.py` and retained
  compact docs artifacts under
  `docs/superpowers/validation/backfill_expansion_raw_overlay_trace_identity_v1/`.
  The full 929-cell RAW trace gate map and evidence-only overlay batch
  summaries are externalized under
  `output/validation/backfill_expansion_raw_overlay_trace_identity_v1/`.
- RAW run: bounded foreground `family_ms1_overlay_batch.py --evidence-only`
  over the 20-row overlay queue. This was not an 85RAW alignment rerun and did
  not render PNG/PDF review artifacts. The batch succeeded for 20/20 families;
  all 20 families returned `ms1_shape_supports_family_backfill`.
- Gate decision: exact sample-local trace rows exist for all 675
  alignment-present cells. RAW trace signal is observed in 666/675 cells. The
  9 trace-absent cells plus the 254 missing-alignment cells remain held, so the
  next expected-diff design universe is bounded to 666 cells. The 43 metric
  warning cells stay in the candidate set with review notes; they are not
  direct writer authority.
- Authority boundary: no ProductWriter change, default matrix change,
  workbook/GUI change, selected peak/area change, counted-detection change,
  Backfill writer authority change, row/family evidence projection, or broad
  Backfill unpark happened. Candidate blank cells are not matrix rows.
- Evidence:
  `python -m scripts.check_backfill_expansion_raw_overlay_trace_identity` and
  `python -m scripts.check_backfill_expansion_raw_overlay_trace_identity --check-only`
  pass from the evidence-only RAW overlay artifacts.
- Control-plane decision: no maturity tier, active lane, writer scope, or
  default authority update is needed. This gate narrows future Backfill
  expected-diff design to 666 RAW-observed cells and holds 263 cells.

### 2026-06-21 - Backfill Expansion Expected-Diff Provenance v1

- Lane: Backfill candidate activation packet for the 666 RAW-observed,
  sample-local CID-NL-created blank cells.
- Tier change: none. `backfill_current_write_ready_scope` remains
  `production_ready` for exactly 511 cells under
  `backfill_policy_write_ready_rows`; `cid_nl_default_product_activation_v1`
  remains `production_ready` for exactly 95 CID-NL Discovery cells. Broad
  Backfill remains parked.
- Product surface changed: additive validation/replay surface only. Added
  `scripts/check_backfill_expansion_expected_diff_provenance.py` and retained
  compact docs artifacts under
  `docs/superpowers/validation/backfill_expansion_expected_diff_provenance_v1/`.
  The full 666-row source evidence, candidate ProductionAcceptanceManifest,
  expected-diff TSV, dry-run matrix, row summary, source summary, and full cell
  provenance are externalized under
  `output/validation/backfill_expansion_expected_diff_provenance_v1/`.
- Gate decision: the 666 cells are now a bounded
  `production_candidate_contract_only` packet. All 666 have blank baseline
  values in the CID-NL default-activated baseline matrix, finite
  `primary_matrix_area` values, schema-valid candidate manifest rows, 666
  expected-diff rows, dry-run writer output with 666 written Backfill cells,
  0 unused expected-diff rows, and 666 accepted cell-provenance rows from
  `ProductionAcceptanceManifest`.
- Held boundary: the 263 held cells remain out of this packet: 254 missing exact
  sample-local alignment evidence plus 9 trace-absent cells. The 43 metric
  warning cells remain in the packet as review notes only, not direct writer
  authority.
- Authority boundary: the dry-run writer output is validation-only. No public
  default matrix, ProductWriter authority, active lane, workbook/GUI behavior,
  selected peak/area, counted detection, row/family projection, or broad
  Backfill unpark changed.
- Evidence:
  `python -m scripts.check_backfill_expansion_expected_diff_provenance` and
  `python -m scripts.check_backfill_expansion_expected_diff_provenance --check-only`
  pass from current artifacts.
- Control-plane decision: no maturity tier, active lane, writer scope, or
  default authority update is made by this gate. The next gate is an explicit
  public default activation change decision over this 666-cell packet.

### 2026-06-21 - Backfill Expansion Candidate Replay v1

- Lane: Backfill bounded candidate replay for the exact 666-cell packet
  created by `backfill_expansion_expected_diff_provenance_v1`.
- Tier change: yes, demotion/correction. The lane is `production_candidate`
  with `write_authority=FALSE`, empty `product_authority_scope`,
  `may_touch_matrix=FALSE`, and `may_change_quant_output=FALSE`. Existing
  `backfill_current_write_ready_scope` remains exactly 511 cells and
  `cid_nl_default_product_activation_v1` remains exactly 95 cells. Broad
  Backfill remains parked.
- Product surface changed: no public output change. Added/kept
  `scripts/build_backfill_expansion_default_product_activation.py` and retained
  compact docs artifacts under
  `docs/superpowers/validation/backfill_expansion_default_product_activation_v1/`.
  The full replay `quant_matrix.tsv`, full `cell_provenance.tsv`,
  `row_summary.tsv`, `expected_diff_summary.tsv`, and `source_summary.tsv` are
  externalized under
  `output/validation/backfill_expansion_default_product_activation_v1/`.
- Replay decision: the same `ProductionAcceptanceManifest` + expected-diff +
  `QuantMatrixVersion` writer path is used as a dry-run replay. The replay
  produces 666 candidate Backfill values, changes exactly 666 matrix cells in
  the externalized replay output, records 666 accepted cell-provenance rows
  from `ProductionAcceptanceManifest`, and leaves 0 unused expected-diff rows.
  These are replay facts only, not public matrix writes.
- Blocking evidence: shift-aware standard-peak support and MS1 own-max evidence
  were not yet wired into this 666-cell packet at this gate. A later full-chain
  checker wires them by stable row/cell keys and still holds the packet because
  only 374/666 cells pass the complete chain.
- Held boundary: the 263 held cells are explicitly outside ProductWriter
  authority: 254 cells lack exact sample-local alignment evidence and 9 cells
  were trace-absent in the RAW overlay trace identity gate. They are not
  silently promoted and not treated as matrix rows.
- Authority boundary: no public ProductWriter authority, no second
  ProductWriter, no workbook/GUI change, no selected peak change, no selected
  area change, no counted-detection change, no row/family projection, no broad
  Backfill unpark, no RAW rerun, no scorer run, and no 85RAW rerun.
- Next required gate: join shift-aware standard-peak support and MS1 own-max
  evidence by stable row/cell keys. Missing or unjoinable evidence must keep
  the cell held.
- Evidence:
  `python -m scripts.build_backfill_expansion_default_product_activation` and
  `python -m scripts.build_backfill_expansion_default_product_activation --check-only`
  pass from current artifacts. Focused tests cover stable counts, artifact hash
  binding, empty authority scope, and fail-closed product-surface overclaims.
- Control-plane decision: updated. The schema, authority manifest, status
  index, handoff, and retention inventory now hold this as candidate replay
  evidence rather than registering a 666-cell ProductWriter authority scope.

### 2026-06-21 - Backfill Expansion Full Evidence Chain v1

- Lane: Backfill full evidence-chain gate for the exact 666 candidate replay
  cells.
- Tier change: none. Existing `backfill_current_write_ready_scope` remains
  exactly 511 cells, `cid_nl_default_product_activation_v1` remains exactly
  95 cells, and `backfill_expansion_default_product_activation_v1` remains
  `production_candidate` with no write authority.
- Product surface changed: additive validation/checker surface only. Added
  `scripts/check_backfill_expansion_full_evidence_chain.py`, compact docs under
  `docs/superpowers/validation/backfill_expansion_full_evidence_chain_v1/`, and
  externalized full outputs under
  `output/validation/backfill_expansion_full_evidence_chain_v1/`. The
  `standard_peak_ms1_authority_bundle` path now enforces own-max threshold
  before producing product-authorized sidecar rows.
- Gate decision: expected diff, sample-local source evidence, and RAW trace
  identity are present for 666/666 cells. Shift-aware support covers 14/20
  families and 492/666 cells; raw own-max metric support covers 496/666 cells;
  product-authorized MS1 sidecar rows cover 374/666 cells inside this keyset.
  Full-chain pass is 374/666 and held is 292/666.
- Held blockers: 174 cells fail the shift-aware standard-peak gate, 99 cells
  have own-max metric below threshold, and 19 cells are missing own-max metric
  evidence.
- Authority boundary: no default matrix, ProductWriter, workbook/GUI, selected
  peak/area, counted detection, broad Backfill, RAW rerun, or 85RAW rerun
  changed. `--require-full-chain` currently blocks.
- Evidence: `python -m scripts.check_backfill_expansion_full_evidence_chain`
  passes from current artifacts; `python -m
  scripts.check_backfill_expansion_full_evidence_chain --check-only
  --require-full-chain` fails as intended with 374/666 pass and 292 held.
  Focused tests cover the checker counts and the own-max enforcement fix.
- Control-plane decision: no maturity tier or active-lane update. The new
  checker prevents any 666-cell promotion until 666/666 pass and expected-diff
  is re-evaluated.

### 2026-06-22 - Backfill Expansion Clean-Target Selective Default Activation v1

- Lane: Backfill clean-target selective default activation for the exact
  84-cell subset that passes the clean-target full-chain replay.
- Tier change: yes. A new production-ready writer lane is registered:
  `backfill_expansion_clean_target_selective_product_activation_v1` with
  `product_authority_scope=backfill_expansion_clean_target_selective_activation_84_cells`.
  Existing `backfill_current_write_ready_scope` remains exactly 511 cells,
  `cid_nl_default_product_activation_v1` remains exactly 95 cells, and
  `backfill_expansion_default_product_activation_v1` remains a 666-cell
  candidate replay with no write authority. Broad Backfill remains parked.
- Product surface changed: yes, but only for this bounded 84-cell Backfill
  expansion scope. The activation reuses the same
  `ProductionAcceptanceManifest` + expected-diff + `QuantMatrixVersion` writer
  path and writes externalized default matrix/provenance artifacts under
  `output/validation/backfill_expansion_clean_target_selective_product_activation_v1/`.
- Activation decision: the packet writes 84 Backfill values, changes exactly 84
  matrix cells, records 84 accepted cell-provenance rows, and leaves 0 unused
  expected-diff rows. It covers 7 peak hypotheses.
- Exclusion boundary: the 28 projected-held clean-target cells, 37
  boundary-review cells, 29 off-target hold/remap cells, the older 374/666
  full-chain subset, and the broader 491/666 selective diagnostic-pass set are
  not silently promoted.
- Authority boundary: no second ProductWriter, no workbook/GUI change, no
  selected peak change, no selected area change, no counted-detection change,
  no row/family projection, no broad Backfill unpark, no RAW rerun, no scorer
  run, and no 85RAW rerun.
- Preset runner: `python -m scripts.run_backfill_expansion_full_evidence_chain
  --reuse-existing-raw-overlay --reuse-existing-shift-aware` now replays the
  evidence chain through this 84-cell activation. `--check-only` validates both
  the full-chain diagnostic artifact and the clean-target activation summary.
  The same rule is now reachable from `scripts.run_alignment` via
  `--preset dna_dr_product_ready`; that preset runs the productization tail
  against the just-finished alignment output instead of re-reading a fixed
  validation folder.
- Future preset requirement: future batches should use a single CLI/GUI preset
  surface instead of separate ad hoc runners. The original update added the CLI
  preset path first. The 2026-06-28 Untargeted GUI refresh now routes GUI
  `full` / `align_only` runs through the same preset alignment runtime and
  standard-peak publisher when the preset enables `standard_peak_backfill`.
  This is GUI reachability for existing preset behavior, not a second product
  authority path: GUI still rejects `backfill_expansion_productization` modes
  other than `off` and tells users to use `scripts.run_alignment` for those
  reviewed clean-target selective activation flows.
- Evidence:
  `python -m scripts.build_backfill_expansion_clean_target_selective_product_activation`
  and
  `python -m scripts.build_backfill_expansion_clean_target_selective_product_activation --check-only`
  pass from current artifacts. The productization runner command above exits
  `0` and reports `Active clean-target activation: 84 cells`. Focused tests
  cover stable counts, artifact hash binding, registered authority scope,
  runner ordering, and fail-closed product-surface underclaims.
- Control-plane decision: updated for the 84-cell authority scope. The preset
  routing follow-ups do not change maturity tier, active lane, authority scope,
  output schema, ProductWriter authority, selected peak/area, matrix authority,
  or counted detection. The CLI remains the owner for clean-target selective
  backfill-expansion activation; the GUI only reaches the existing
  standard-peak preset path.

### 2026-06-23 - Row-Completion Confidence Shadow Gate v1

- Lane: row-completion confidence benchmark for alignment/backfill method
  validation.
- Previous tier: `diagnostic_only` for row-completion confidence artifacts.
- New tier: `shadow_ready` only for the baseline-bound product-gate mode. The
  default diagnostic mode remains `diagnostic_only`; product readiness remains
  false.
- Product/public surface changed: additive CLI/API behavior only. The
  `row_completion_confidence` CLI now accepts `--gate-mode product-gate`; the
  summary JSON records `gate_mode`, `product_gate_eligible`, and a
  gate-specific `no_authority_statement`; outputs keep the legacy
  `row_completion_family_sentinels.tsv` compatibility alias alongside
  `row_completion_sentinels.tsv`. No matrix, workbook, selected peak, selected
  area, counted detection, ProductWriter authority, Backfill authority, active
  lane, or default preset behavior changes.
- Gate decision: product-gate mode fails closed without
  `--baseline-alignment-dir` using
  `missing_evidence_code=baseline_current_unbound` and CLI exit code `1`.
  With a bound baseline and no selected-value, `Mz`/`RT` anchor, or
  matrix-identity drift, it can report `validation_tier=shadow_ready` and CLI
  exit code `0`; with drift, it reports `gate_ok=false`,
  `authority_decision=expected_diff_required`, and CLI exit code `1`.
- Evidence: focused tests
  `python -m pytest tests\test_row_completion_confidence_daily.py tests\test_row_completion_confidence_cli.py tests\test_row_completion_confidence_schema.py -q`
  passed 31 tests; focused ruff passed on the changed row-completion modules,
  CLI, and tests. The 8RAW no-RAW product-gate smoke under
  `output/diagnostics/row_completion_confidence_8raw_product_gate_20260623`
  compares
  `output/performance/dna_dr_product_ready_8raw_baseline_20260622_freshinput`
  to
  `output/performance/dna_dr_product_ready_8raw_stage_replay_cache_reviewfix_v3_20260623`
  and reports `run_ok=true`, `gate_ok=true`, `status=PASS`,
  `validation_tier=shadow_ready`, and `selected_value_drift=0`.
- Remaining blocker: this is still a non-mutating shadow gate. Promotion beyond
  shadow readiness requires 85RAW baseline/current evidence plus a separate
  expected-diff/control-plane decision if any product output or writer authority
  would change.

### 2026-06-27 - Docs Placement Refactor No-Impact Record

- Lane: documentation placement and agent workflow governance only.
- Change: moved productization status/evidence, file-management cleanup
  manifests, referrer sidecars, and PR closeout summaries out of
  `docs/superpowers/handoffs/` into their explicit repo owners:
  `docs/superpowers/productization/`, `docs/superpowers/file-management/`, and
  `docs/superpowers/closeouts/`.
- Control-plane decision: no maturity tier, active lane, ProductWriter
  authority, matrix/workbook schema, review/replay behavior, selected peak,
  selected area, counted detection, matrix values, or matrix authority changes.
- Rationale: the old handoff paths were public evidence and status anchors, not
  active branch handoffs. Moving them to semantic owner directories prevents
  future handoff archives from becoming a second file-management or
  productization system.

### 2026-06-29 - Standard-Peak Evidence Cache RAW-Identity Fail-Closed Fix

- Lane: standard-peak repeated method-development replay cache / review-replay
  safety.
- Product/public surface changed: opt-in replay/cache behavior only. Warm cache
  hits now require per-sample RAW identity (`path_stat_v1`: resolved path, size,
  mtime, device, inode) to match cached trace provenance; missing or mismatched
  identity fails closed to normal RAW extraction.
- Control-plane decision: no maturity tier, active lane, ProductWriter
  authority, matrix/workbook schema, selected peak/area, counted detection,
  default preset, or matrix authority change. This only tightens reusable
  evidence-cache safety for repeated standard-peak method-development runs.
- Evidence: focused cache regression tests cover direct index hits and manifest
  fallback after RAW replacement; related standard-peak/cache tests and docs
  gates should pass before PR update.

### 2026-07-02 - Pt Pattern Diagnostic Gate Replay v1

- Lane: Pt pattern Discovery `delta_mass(pattern_id=pt)`.
- Previous tier: `production_candidate` only for the repaired v2 bounded manual
  oracle packet; `diagnostic_only` for detector/runtime, trace/review/overlay,
  refresh, and threshold artifacts.
- New tier: unchanged. The repaired v2 bounded manual oracle remains the only
  `production_candidate` Pt artifact. The new gate replay is
  `diagnostic_only`.
- Product/public surface changed: additive diagnostic CLI/schema/replay
  behavior only. `tools/diagnostics/pt_pattern_diagnostic_gate.py` and
  `xic_extractor.discovery.pt_pattern_diagnostic_gate` write
  `pt_pattern_diagnostic_gate.v1` row/summary artifacts and support
  `--require-zero-fpfn` for bounded-oracle replay. The CLI now also accepts
  `--trace-summary-input SOURCE_LABEL=PATH` so the same rule can run against
  full trace-diagnosed candidate slices, not only bounded manual-review packets.
  The reusable row-level accept/reject rule now lives in
  `xic_extractor.discovery.pt_pattern_evidence_rule`; threshold calibration and
  diagnostic gate replay call that rule instead of owning duplicate rule logic.
  No default preset, Matrix, workbook, selected peak/area, counted detection,
  Backfill authority, ProductWriter authority, export behavior, or detector
  activation changed.
- Evidence: the older no-RAW replay under
  `output/pt_pattern_productization_v1/diagnostic_gate/pt_detected_oracle_v2_repaired_32/`
  applied the historical 10-second calibrated rule to the repaired 32-row v2
  oracle and reported 10 TP, 22 TN, 0 FP, 0 FN with
  `changes_matrix=false`, `changes_counted_detection=false`, and
  `changes_export=false`. That 10-second gate is superseded for active Pt
  machine calls by the scan-aware isotope-constrained
  `max_abs_apex_delta_sec <= max(0.6 sec, 1.25 * scan_interval_sec)` rule.
  The older full trace-summary diagnostic replay under
  `output/pt_pattern_productization_v1/diagnostic_gate/trace_summary_limit50_current/`
  ran the historical diagnostic rule on a 100-row real-data trace-diagnosis slice
  (50 Pt-only Fullscan + 50 Pt+CID-NL DDMS2 rows): 45 accept, 55 reject;
  source-level calls are 15/35 accept/reject for Pt-only and 30/20 for
  Pt+CID-NL.
- Next checkpoint: do not use the diagnostic gate as product authority until a
  real feature-detector source or explicit activation/export contract is
  reviewed with expected-diff evidence.
