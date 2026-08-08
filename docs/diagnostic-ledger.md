# Diagnostic Ledger And Rerun Policy

Doc placement: formal_repo_doc
Doc kind: report
Doc lifecycle: active
Repo owner: docs/diagnostic-ledger.md
Doc exit rule: Keep active while expensive RAW rerun memory is needed; retire only after durable diagnostic memory moves to a replacement canonical owner.

**Status:** maintained repo-local diagnostic memory
**Last updated:** 2026-07-24

This ledger records diagnostic conclusions that should survive branch and
worktree changes. Use it before rerunning expensive RAW validation or treating a
known target as a new blocker.

This file is not a replacement for task-specific validation notes. It is the
small durable index that tells future agents which prior conclusions are already
known, where the evidence lives, and when a rerun is justified.

This file is also not the live productization tier board. Product maturity tier,
active lane, current writer counts, promotion-packet status, and Backfill
authority scopes are owned by the productization status index
`docs/superpowers/validation/productization_status_index_v1.tsv`, the authority
manifest
`docs/superpowers/schemas/productization_authority_manifest.v1.json`, and the
promotion contract `docs/agent/product-validation-contract.md`. When this ledger
cites a historical validation note, treat it as diagnostic/rerun memory unless
a current structured authority artifact still promotes the same claim. The
dated productization control-plane path is compatibility lookup only.

## Use Rules

- Read this ledger before investigating a previously seen target, family,
  failure mode, or validation gate.
- If this ledger already closes the diagnostic question, reuse the cited note or
  artifact instead of rerunning RAW.
- Rerun only when current code touched selection, boundary, identity,
  consolidation, integration, matrix delivery, or the cited artifacts are stale.
- Do not escalate from 8RAW to 85RAW when the 8RAW evidence is already `NO_GO`.
- Worktree `output/` paths are evidence references, not reusable inputs. Inputs
  that must survive worktrees belong under `local_validation_artifacts/`.
  Small diagnostic snapshots that must survive worktrees belong under
  `docs/superpowers/fixtures/` with a source note and hash.
- If a rerun changes a durable conclusion, update this ledger in the same PR.
- If a later status-index or authority-manifest entry changes maturity tier,
  writer scope, or product authority after a ledger entry was written, defer to
  the later productization authority and update this ledger only when the
  diagnostic/rerun conclusion itself changes.

## Stable Inputs

| Purpose | Stable path |
| --- | --- |
| Accepted P8b 8RAW discovery input | `local_validation_artifacts/discovery/accepted_p8b/8raw/discovery_batch_index.csv` |
| Accepted P8b 85RAW discovery input | `local_validation_artifacts/discovery/accepted_p8b/85raw/discovery_batch_index.csv` |
| Targeted GT 8RAW default workbook | `local_validation_artifacts/targeted_gt_workbooks/8raw/xic_results_20260512_1151.xlsx`, SHA256 `788892188C8419C82DC4618C98E160B90AC6C44C38676C53609248AA529889F7` |
| RAW root | `$env:XIC_RAW_ROOT` |
| Thermo DLL dir | `$env:THERMO_RAWFILE_READER_DLL_DIR` |
| RAW-capable Python | `"${env:XIC_REPO_ROOT}\.venv\Scripts\python.exe"` |

## Known Diagnostic Conclusions

### Retired AsLS / Linear-Edge Decision Gates

Verdict: old P2/P2b/P2c AsLS-versus-linear-edge diagnostics are closed history,
not active decision surfaces. Current area authority is owned by the LC-MS/MS
evidence rules, morphology-aware area policy, and product projection contracts.
Historical notes may mention linear-edge comparison, but active code and future
diagnostics must not provide a linear-edge fallback, comparator, or truth gate
unless a new product contract explicitly reopens that decision.

Use current owners instead:

- `docs/product/quant-matrix.md` for matrix authority and historical AsLS policy
  supersession.
- `docs/product/peak-model-selection.md` for baseline-aware local S/N evidence.
- `docs/lc-msms-evidence-rules.md` for evidence semantics and area policy.

### 2026-06-16/17 5-hmdC Own-Max Support Limited Rescue Smoke

Verdict: `production_ready` for three headless workflows: explicit reviewed
`targeted_ms1_shape_identity_support_tsv`, explicit
`xic-extractor-cli --targeted-ms1-shape-identity-auto-limited-default`, and the
canonical no-flag normal CLI default. The ready claim is limited to
`limited_5hmdc_5medc_v1`, `5-hmdC + 5-medC`, and `detected_flagged` output.
GUI is not connected, and broader targets need separate expected-diff evidence.

Update on the same date: the original five-row TSV was a handpicked review
surface, not a product limitation. A generic RAW-backed support producer now
finds all eligible baseline rows that are analyte `NL_FAIL` / `NO_MS2`, blocked
only by `analyte_nl_fail_requires_policy`, already supported by paired RT/area
ratio, and then passes Gaussian-smoothed own-max same-peak identity.

Generic producer smoke:
`output/ms1_shape_identity_generic_support_85raw_20260616/`

2026-06-17 support key-set gate update: the limited expected-diff gate now reads
the actual `targeted_ms1_shape_identity_v0.tsv` with `--support-tsv` and fails
closed unless accepted support keys exactly match product long-row diff keys.
The existing 85RAW generic artifact rerun passed with `long_changed_rows=11`,
`matrix_changed_cells=66`, and `support_tsv_supported_rows=11`.

2026-06-17 auto-limited CLI update: `scripts/run_extraction.py` now has
`--targeted-ms1-shape-identity-auto-limited-default`. It runs baseline CSV,
builds a limited RAW-backed support TSV, reruns final extraction with that TSV,
and writes `expected_diff_summary.tsv`, `matrix_diff_summary.tsv`, and
`limited_default_expected_diff_gate_summary.tsv` under the auto output root.
The 8RAW auto smoke passed with `1` support row, `1` changed long row, and
`6` matrix cells. A single 85RAW auto smoke passed with `11` support rows,
`11` changed long rows, `66` matrix cells, and wall-clock `369.2 s`.

2026-06-17 no-flag default update: canonical settings defaults now use
`targeted_ms1_shape_identity_activation_policy=limited_5hmdc_5medc_v1`. The
headless CLI dispatches the same auto-limited workflow when no support TSV is
configured, so no-flag normal CLI output is still gated by support TSV key-set
expected-diff before `final/output` is published. Reused 85RAW artifact gate:
`output/ms1_shape_identity_default_no_flag_existing_85raw_gate_20260617/limited_default_expected_diff_gate_summary.tsv`
passed with 11 changed long rows, 66 matrix cells, and 11 support TSV supported
rows. GUI and targets beyond `5-hmdC` / `5-medC` remain out of scope.

Current 8RAW smoke:
`output/ms1_shape_identity_optin_8raw_20260616/`

Current 85RAW smoke:
`output/ms1_shape_identity_optin_85raw_20260616/`

Current auto-limited 8RAW smoke:
`output/ms1_shape_identity_auto_limited_8raw_20260617/`

Current auto-limited 85RAW smoke:
`output/ms1_shape_identity_auto_limited_85raw_20260617/`

Key facts:

- Baseline and opt-in runs both used
  `$env:XIC_RAW_VALIDATION_DIR` for 8RAW and
  `$env:XIC_RAW_ROOT` for 85RAW; all runs used
  CSV-only output.
- Opt-in runs used
  `output/ms1_rescue_5hmdc_own_max_similarity_20260616/targeted_ms1_shape_identity_v0.tsv`.
- 8RAW `expected_diff_summary.tsv` found exactly one changed row:
  `TumorBC2263_DNA / 5-hmdC`.
- That row changed from `Product State=not_counted`,
  `Counted Detection=FALSE`, `RT=ND`, `Area=ND` to
  `Product State=detected_flagged`, `Counted Detection=TRUE`, `RT=9.1705`,
  `Area=145695.76`.
- 85RAW `expected_diff_summary.tsv` found exactly five changed rows, exactly
  matching the reviewed support TSV keys:
  `BenignfatBC1028_DNA / 5-hmdC`,
  `BenignfatBC1108_DNA / 5-hmdC`,
  `NormalBC2302_DNA / 5-hmdC`,
  `TumorBC2263_DNA / 5-hmdC`, and
  `TumorBC2294_DNA / 5-hmdC`.
- All five changed from `not_counted / FALSE` to `detected_flagged / TRUE`.
  Unexpected changed rows = `0`; support rows not changed = `0`.
- The 85RAW wide matrix changed `30` value cells, only those five samples'
  `5-hmdC_RT`, `5-hmdC_Int`, `5-hmdC_Area`, `5-hmdC_PeakStart`,
  `5-hmdC_PeakEnd`, and `5-hmdC_PeakWidth`.
- In both 8RAW and 85RAW, support reasons gained
  `own_max_same_peak_support` and `analyte_nl_fail_requires_policy` was
  removed.
- The generic producer read the existing 85RAW baseline long CSV and emitted
  `11` diagnostic support rows: `10` for `5-hmdC` and `1` for `5-medC`.
  All `11` were `own_max_same_peak_supported`.
- The auto-limited 8RAW workflow emitted one support row for
  `TumorBC2263_DNA / 5-hmdC`, changed exactly that long row from
  `not_counted / FALSE` to `detected_flagged / TRUE`, and wrote only the six
  expected `5-hmdC` matrix measurement cells.
- A new 85RAW opt-in run using that generic TSV changed exactly `11` rows:
  `BenignfatBC0980_DNA / 5-hmdC`,
  `BenignfatBC1028_DNA / 5-hmdC`,
  `BenignfatBC1108_DNA / 5-hmdC`,
  `NormalBC2259_DNA / 5-hmdC`,
  `NormalBC2264_DNA / 5-medC`,
  `NormalBC2270_DNA / 5-hmdC`,
  `NormalBC2272_DNA / 5-hmdC`,
  `NormalBC2294_DNA / 5-hmdC`,
  `NormalBC2302_DNA / 5-hmdC`,
  `TumorBC2263_DNA / 5-hmdC`, and
  `TumorBC2294_DNA / 5-hmdC`.
- All `11` changed from `not_counted / FALSE` to
  `detected_flagged / TRUE`. The wide matrix changed `66` value cells
  (`11` rows x `RT/Int/Area/PeakStart/PeakEnd/PeakWidth`). Baseline and
  generic opt-in `xic_diagnostics.csv` SHA256 values were identical.
- The auto-limited 85RAW workflow generated the same support TSV SHA256 as the
  existing generic artifact:
  `0556026CFD22CD178C6686F76C83C7ED8CDFC3D0CAA7BAA0BA22811BD84BE104`.
  Its expected-diff key set matched the existing generic artifact `11/11`,
  its matrix-diff key set matched `66/66`, and baseline/final diagnostics CSV
  SHA256 values were identical:
  `59D3572A85F0F2596388C9374D34628320DF1660FBF722AAFCBB4934A34FC135`.

Do not rerun 85RAW again for this path unless current code changes product
projection, support TSV parsing/production, auto CLI orchestration,
selected-candidate semantics, matrix writing, or the cited artifacts become
stale. If only the human review wording changes, reuse the 85RAW artifacts
above.

### 2026-06-05 Gaussian15 MS1 Morphology Primary Area Owner

Verdict: `production_ready` for the Gaussian15 MS1 morphology ownership
transition. Active primary matrix area now requires
`primary_matrix_area_source=gaussian15_positive_asls_residual`; historical
`asls_baseline_corrected` is compatibility/debug-only and must not write product
area. Missing typed morphology facts fail closed as
`missing_ms1_morphology_area`.

Durable closeout:
retired private-history closeout; the current durable facts are the verdict,
key facts, and retained artifact paths in this ledger section.

Current 85RAW foreground gate:
`output/gaussian15_ms1_morphology_85raw_20260605/alignment_validation_minimal_no_asls_fallback/`

Key facts:

- `matrix_value_policy=gaussian15_positive_asls_residual_primary`
- `primary_matrix_area_nonblank_count=1546489`
- `non_gaussian_source_with_area=0`
- `asls_source_with_area=0`
- `raw_source_with_area=0`
- `matrix_sample_count=85`
- `matrix_row_count=614`

Do not rerun 85RAW just to re-prove ASLS fallback retirement. Rerun only after
a new production behavior change, a new target/default validation contract, or a
current artifact contradicts the closeout facts above.

### 2026-06-05 Gaussian15 MS1 Peak-Group NL Scope

Verdict: `production_ready` for targeted candidate MS2/NL evidence ownership
under Gaussian15 MS1 peak-group scope. Selected `chrom_peak_segment` candidates
must not borrow active strict NL support from a different Gaussian15 MS1 peak
group. Outside-group strict NL stays diagnostic context.

Durable closeout:
retired private-history closeout; the current durable facts are the verdict,
key facts, and retained artifact paths in this ledger section.

Current 8RAW targeted gate:
`output/gaussian15_ms1_peak_group_nl_scope_8raw_20260605/nl_peak_group_scope_8raw/ms1_peak_group_nl_scope_gate/ms1_peak_group_nl_scope_gate_manifest.json`

Current 85RAW targeted gate:
`output/gaussian15_ms1_peak_group_nl_scope_85raw_20260605/nl_peak_group_scope_85raw/ms1_peak_group_nl_scope_gate/ms1_peak_group_nl_scope_gate_manifest.json`

Key facts:

- 8RAW: `gate_decision=promote`, `selected_chrom_count=80`,
  `borrowed_strict_nl_support_rows=0`, `review_row_count=0`,
  `context_row_count=15`.
- 85RAW: `gate_decision=promote`, `selected_chrom_count=811`,
  `borrowed_strict_nl_support_rows=0`, `review_row_count=0`,
  `context_row_count=156`.
- The gate also requires `ms1_peak_group_source=gaussian15_ms1_peak_group` and
  selected apex inside the group bounds.

Do not rerun 85RAW just to re-prove this scoped NL ownership behavior. Rerun
only after current code changes targeted candidate MS2/NL evidence ownership,
chrom peak segment selection, candidate-table projection, or the cited artifacts
are stale or contradictory.

### 2026-05-24 Resolver Default Switch / P2 Entry Gate

Verdict: `production_candidate` for P2 entry on the 8RAW method gate after the
hotfix evidence-chain continuation. This is not an 85RAW clearance and not
`production_ready`.

Durable closeout:
retired private-history validation note; the current durable facts are the
verdict, key facts, and rerun rule in this ledger section.

Key facts:

- The stale pre-hotfix artifact state was `NO-GO`; do not use it to block or
  approve current P2 work.
- The hotfix restored untargeted alignment production peak picking to
  `local_minimum` while keeping `region_first_safe_merge` as audit context for
  alignment runs.
- The strict ISTD blocker on `15N5-8-oxodG` was resolved in hotfix artifacts.
- Reviewed identity-coherence controls passed V0.4 acceptance, and the
  hotfix-reviewed identity-family decisions were byte-identical to the
  pre-change baseline.
- The same-surface `d3-N6-medA` probe reclassified the
  apparent mismatch as a mixed-surface diagnostic artifact, not a standalone
  evidence-spine blocker.

Do not rerun the P1/P2 resolver default gate just to re-prove P2 entry. Rerun
only after current code changes resolver default routing, candidate selection,
selected boundaries, strict ISTD benchmark behavior, identity-coherence
decisions, or the cited artifacts become stale or contradictory.

### 2026-05-26 P8b 85RAW Super-Window Acceptance

Verdict: `production_candidate` for explicit opt-in
`validation-minimal + production-equivalent + validation-fast + super-window`
85RAW alignment validation. This is a validated runner/performance mode, not a
new default behavior and not `production_ready` by itself.

Durable closeout:
retired private-history runtime note; the current durable facts are the
validated command profile and rerun rule in this ledger section.

Key facts:

- The 85RAW super-window run completed and emitted the expected machine
  artifacts.
- Matrix, cells, and review surfaces were byte-identical to the accepted
  P8b exact-window reference for the checked contract.
- No new RT, identity, or coverage failure was introduced by super-window.
- The CLI default remained exact-window batching; super-window stayed explicit
  opt-in.
- Known targeted-benchmark warnings from the prior accepted surface remained
  warning-class follow-up, not super-window regressions.

Do not rerun 85RAW just to re-prove super-window acceptance. Rerun only after
current code changes RAW locality, owner-backfill request grouping,
super-window batching/cropping, validation-fast settings, output surfaces used
by the gate, or the cited artifacts become stale or contradictory.

### 2026-06-15 Replay Executor Validation

Verdict: `run_ok` and `gate_ok` for targeted CLI CSV/workbook replay parity on
the reviewed 8RAW and 85RAW replay surfaces. This validates replayed analytical
output equivalence for the named contract; it is not a full exact artifact
replay, GUI parity proof, or timestamped workbook hash guarantee.

Durable closeout:
retired private-history replay note; the current durable facts are the verdict,
key facts, and rerun rule in this ledger section.

Key facts:

- CSV replay parity passed for the reviewed 8RAW and 85RAW surfaces.
- Workbook analytical parity passed for the reviewed 8RAW and 85RAW surfaces.
- Replay validation intentionally excluded full byte-identical artifact replay
  where timestamped workbook metadata makes exact hashes unsuitable.
- GUI replay parity was not part of the accepted gate.

Do not rerun replay validation just to re-prove these surfaces. Rerun only after
current code changes method-manifest binding, replay CLI behavior,
settings/targets artifact resolution, CSV/workbook writer semantics, or the
cited artifacts become stale or contradictory.

### 2026-05-28 Qualitative Selection / Owner-Backfill Scan Support Gate

Verdict: `production_candidate` for the Phase 1b qualitative selected-peak /
Backfill promotion blocker on the current production-equivalent alignment path.
This closes the qualitative promotion blocker for the next product-decision PR;
it does not declare the whole product `production_ready`.

Durable closeout:
retired private-history gate note; the current durable facts are the verdict,
key facts, and retained fixtures in this ledger section.

Key facts:

- The earlier `NO_GO` was valid, but the root cause was narrower than Backfill
  itself: `owner_backfill` was used as a support label without emitting the
  independent `scan_support_score` required by the shared promotion policy.
- Owner-backfill cells now compute `scan_support_score` from the extracted XIC
  trace and selected peak boundary.
- `trace_quality=owner_backfill` remains insufficient as independent support by
  itself.
- High-backfill promotion requires either at least two detected identity cells
  or one detected seed plus product-authorized same-peak rescue evidence; a
  single detected seed cannot promote a mostly backfilled row from local MS1
  peak presence alone.
- Supported high-backfill rows are capped at medium confidence and marked with
  `high_backfill_dependency_capped`.
- The accepted 85RAW foreground run used the canonical
  production-equivalent, audit-off, validation-fast, super-window, heartbeat
  command shape and produced the cited durable fixture summaries.

Do not rerun Phase 1b just to re-prove the owner-backfill scan-support fix.
Rerun only after current code changes owner-backfill trace support emission,
promotion-policy support semantics, selected peak boundaries, high-backfill
confidence/flag projection, or the cited artifacts become stale or
contradictory.

### d3-N6-medA

**Current classification:** known RT-drift / same-surface case; not a standalone
RT blocker.

Known facts:

- `d3-N6-medA` has severe biological-matrix RT drift. The 85RAW target-only RT
  trend audit recorded target-only RT range `2.1538 min`,
  global-median absolute RT delta p95 `1.4571 min`, local rolling-median p95
  `0.0483 min`, and local moderate/severe drift rows `0 / 85`.
- The `d3-N6-medA` evidence-spine mismatch was reclassified as mixed-surface
  diagnostic artifact after same-surface probes. Same-surface comparison had
  `8/8` non-missing rows consistent and reviewed row area ratio about
  `1.000001375`.
- Area mismatch alone must not block P2B or handoff progression for this target
  when identity, local RT coherence, selected peak, boundary ownership, and
  matrix delivery are accepted.
- Absolute RT delta alone must not prove absence for this target. Use local RT
  coherence and same-surface evidence.

Post-fix 8RAW primary-delivery conclusion:

- The prior blocker was primary delivery / ownership-consolidation, not absence
  of a plausible peak, RT drift, or area mismatch.
- `FAM000264` is the consolidated delivery family in the post-fix 8RAW run:
  `DNA_dR`, `owner_complete_link`, `include_in_primary_matrix=TRUE`,
  `8/8 present`, `3 detected`, `5 MS1 backfilled`, `22` event clusters,
  and `24` event members.
- `FAM000264` contains a detected representative and a rescued companion cell
  at RT `25.4204`, matching the strong target-derived MS1 apex RT.
- `FAM000264` is now present in `alignment_matrix.tsv`; the row carries
  `row_flags=rescue_heavy;weak_seed_tolerated` so the warning remains visible.
- The post-fix row-level gate has `GO blocker count: 0`. The `d3-N6-medA`
  representative resolves by an equivalent current artifact because the strong
  source candidate was an ambiguous owner row, while the primary `FAM000264`
  cell has the same sample class, target m/z class, and apex RT `25.4204`.
- A broad rule that promotes every `weak_seed_backfill_dependency` row with
  `q_detected >= 3` remains unacceptable. The accepted fix promoted 13
  additional `DNA_dR` rows, all flagged `rescue_heavy;weak_seed_tolerated`, and
  records the collateral table below.

Post-fix 85RAW primary-delivery conclusion before implementation-review
hardening:

- Foreground 85RAW `validation-minimal` completed with the canonical
  production-equivalent, audit-off, validation-fast, super-window, heartbeat
  command shape.
- `alignment_matrix.tsv` contains `597` primary rows. The primary warning
  surface is `376` `rescue_heavy` rows plus `5`
  `rescue_heavy;weak_seed_tolerated` rows.
- Original `single_dr_production_gate_decision_report.py` found `0` risky
  extreme-backfill rows, `0` risky weak-seed rows, and `0` duplicate
  rescue-pressure rows.
- After implementation-review hardening of the trusted-seed contract, the same
  committed artifacts reclassify the prior weak-seed tolerated rows as
  `risky_weak_seed_backfill`: 8RAW has `13` such primary rows, including
  `FAM000264`; 85RAW has `5`.
- `targeted_istd_benchmark.py` still reports strict `AREA_MISMATCH` for
  `d4-N6-2HE-dA` and `d3-N6-medA`. Treat this as a known quantitative /
  baseline follow-up surface, not a qualitative delivery blocker, because all
  active ISTDs have one selected primary family and `85/85` untargeted
  positives.
- Current qualitative classification is `NO_GO_FIX_SELECTION_OR_BOUNDARY_FIRST`
  for the production behavior as written. `d3-N6-medA` drift and area mismatch
  are not the blocker; the blocker is the weak-seed promotion contract.

Evidence notes and retained artifacts for this diagnostic conclusion:

| Evidence | Path / fact |
| --- | --- |
| P2B area-mismatch triage | retired private-history note; current facts summarized above |
| Resolver default hotfix / same-surface d3 finding | retired private-history note; current facts summarized above |
| 85RAW super-window runtime acceptance | retired private-history note; current facts summarized above |
| Product-priority Phase 1 gate | retired private-history note; current facts summarized above |
| Current 8RAW resolved matrix snapshot | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/phase1_review_matrix_resolved.tsv`, SHA256 `9A63FC81C811CF92925853884837B623878B644A26311799FAF64FA4947548E8` |
| Post-fix 8RAW resolved matrix snapshot | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/phase1_review_matrix_resolved_primary_delivery_fix.tsv`, SHA256 `B38EC06D01714B4AACA6825B1C003C9BB724264689FCD78FBCB8DD2F9AB0CE9D` |
| Current 8RAW row triage snapshot | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/target_derived_review_row_triage.tsv`, SHA256 `73601AE36C879CE827AEA5816F6E690DF023F8DEB59B63F97ED8BBDEE635A3F9` |
| Post-fix 8RAW row triage snapshot | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/target_derived_review_row_triage_primary_delivery_fix.tsv`, SHA256 `73601AE36C879CE827AEA5816F6E690DF023F8DEB59B63F97ED8BBDEE635A3F9` |
| Post-fix collateral promotion table | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/collateral_promoted_primary_rows_primary_delivery_fix.csv`, SHA256 `9CFD07DBDD067748DEFEA883086894E481E947C157C15727E7BACC6DDCB71296` |
| Post-fix 85RAW validation summary | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/85raw_primary_delivery_fix_summary.tsv`, SHA256 `4CF19B045F850B205E23888F1B3C5A6E3AF1C0BEC0C516E4447B61738CEAC24B` |
| Post-fix 85RAW weak-seed watch rows | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/85raw_weak_seed_tolerated_watch_rows.tsv`, SHA256 `E003FDEAC97E1DAE6E0D6AF929CDD7EA3A004BC9873A0340EF8A7B2E099683E4` |
| Post-review hardened single-dR gate summary | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/post_review_hardened_single_dr_gate_summary.tsv`, SHA256 `43FE4E4BCFF9DD20CA6B16F183F7A414EE89DCF1657A7E2E3B20559E32DE48E3` |
| Current 8RAW diagnostic tool usage snapshot | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/diagnostic_tool_usage_current_8raw.tsv` |
| Current 8RAW targeted GT default checkpoint | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/targeted_gt_alignment_audit_default_5medc_current_8raw_comparison.csv`, SHA256 `DB05B546B99CB9567D7873216906181FB20F57BA021BEFC3A503CC3128BE77D8`; historical narrative report retired after source-copy handling; durable summary lives in `docs/product/alignment.md` and the retirement record is `docs/superpowers/file-management/docs-cleanup/2026-06-30_source-copy-stub-removal-approval-packet.md` |
| Post-fix 8RAW targeted GT default checkpoint | `docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/targeted_gt_alignment_audit_default_5medc_primary_delivery_fix_comparison.csv`, SHA256 `DB05B546B99CB9567D7873216906181FB20F57BA021BEFC3A503CC3128BE77D8`; historical narrative report retired after source-copy handling; durable summary lives in `docs/product/alignment.md` and the retirement record is `docs/superpowers/file-management/docs-cleanup/2026-06-30_source-copy-stub-removal-approval-packet.md` |

Source worktree output for the snapshots above was
`output/product_priority_reset_phase1/`. The committed fixture copies are the
durable reference; do not require the original worktree output to exist before
using the ledger.

Recommended next check:

1. Do not rerun RT drift diagnostics just to re-prove drift.
2. Do not treat `d3-N6-medA` area shift or absolute RT delta as a blocker.
3. Do treat the current weak-seed promotion contract as the active blocker until
   a new reviewed production identity policy replaces it.
4. If production behavior changes again, rerun 8RAW validation-minimal first.
5. Do not rerun 85RAW just to re-prove this fix. Rerun 85RAW only after a new
   production behavior change or if a current artifact contradicts the 85RAW
   summary fixture.

## 2026-07-24 Acquisition Source And Reusable Artifact Capability

Issue #124 is source-qualified in
`docs/superpowers/validation/acquisition_source_capability_probe_v1/README.md`.

Reusable conclusion:

- Keep Thermo vendor XIC as the current acquisition oracle. Of the two
  vendor-backed modes measured in the current probe, super-window was faster
  and still obtained XIC through the vendor API; point-by-point trace parity
  between those two vendor modes was not established.
- The local MS1 scan index is useful diagnostic/replay evidence, not an exact
  vendor-XIC substitute. Current one-RAW evidence matched trace length and RT
  grid for 100/100 requests but had only 77/100 apexes within 0.05 min, 2.595%
  median area relative delta, and 1048.47% p95 area relative delta.
- The one-RAW NPZ was 1.6435 times the RAW size. Do not assume a whole-scan
  index is a storage win.
- mzML is not implemented as an acquisition adapter. Do not treat legacy
  `.mzML Peak area` columns or format expressibility as RAW-to-mzML fidelity.
- Existing overlay caches and Pt trace manifests prove diagnostic-specific
  no-RAW reuse patterns, not a universal persistent `EvidenceArtifact`.

Rerun policy:

1. Use this packet to avoid rerunning 8RAW/85RAW merely to re-prove the
   directional scan-index non-parity result. Exact replay additionally requires
   the hash-bound ignored `output/` inputs and RAW named in the packet; a clean
   checkout does not contain them. If those inputs cannot be restored and exact
   adjudication is required, produce a new source-qualified bounded probe.
2. Reopen a bounded RAW scan-mode probe only when profile/centroid or native
   controller identity can change the implementation decision.
3. Run a RAW-to-mzML crosswalk only when a real consumer requires mzML input;
   pin converter version and all precision/centroiding options.
4. Route retry, partial-result, cleanup, and cache lifecycle decisions to #128.

Status remains `diagnostic_only`. This entry grants no product-write authority.

## Reusable Diagnostics

Prefer existing tools before creating a new diagnostic:

| Question | Existing diagnostic |
| --- | --- |
| Targeted-vs-untargeted checkpoint for named targets | `tools/diagnostics/targeted_gt_alignment_audit.py` |
| Single-dR production gate pressure | `tools/diagnostics/single_dr_production_gate_decision_report.py` |
| Low-seed / high-backfill family review queue | `tools/diagnostics/family_ms1_backfill_review_report.py` |
| Alignment decision summary with known exceptions | `tools/diagnostics/alignment_decision_report.py` |
| RT normalization / target trend context | `tools/diagnostics/analyze_rt_normalization_anchors.py` |

If these tools cannot answer the current question, record why in the new plan or
validation note before adding another diagnostic.

## Current Tool Coverage

For the original product-priority 8RAW gate, the pre-fix tool coverage snapshot
is:

`docs/superpowers/fixtures/diagnostic_ledger_2026_05_28/diagnostic_tool_usage_current_8raw.tsv`

Pre-fix summary:

- `tools/diagnostics/INDEX.md` was consulted before adding the ledger.
- `single_dr_production_gate_decision_report.py` was run on the current 8RAW
  alignment. It found 269 single-dR primary rows and 0 risky weak-seed or
  extreme-backfill primary rows. This confirms the current issue is not a bad
  primary row already leaking into the matrix.
- `family_ms1_backfill_review_report.py` was run on the current 8RAW alignment
  with `DNA_dR` scope. It queued 0 primary families, which is expected because
  `FAM000264` is excluded before primary-family review.
- `owner_backfill_request_economics.py` was run on the current 8RAW alignment.
  It found 2756 request targets, with 733 production targets and 2023 non-primary
  targets. This supports keeping reruns narrow and not treating broad evidence
  expansion as free.
- `alignment_decision_report.py` was run on the current 8RAW alignment and
  returned `WARN` with `d3-N6-medA` declared as a known exception.
- `targeted_gt_alignment_audit.py` was run on the current 8RAW alignment using
  the default positive checkpoint `5-medC / d3-5-medC`. It used the stable
  targeted GT 8RAW workbook under `local_validation_artifacts/` and found
  `PASS 8 / 8`, with `SPLIT 0`, `DRIFT 0`, `DUPLICATE 0`, and `MISS 0`.
  Do not use the 85-sample workbook against an 8RAW alignment: that creates
  off-scope `MISS` rows and is a workbook/sample-set mismatch, not a product
  signal.
- `targeted_istd_benchmark.py`, `analyze_rt_normalization_anchors.py`, and
  `evidence_spine_consistency.py` were not rerun from stale worktree-specific
  targeted workbook artifacts. Their prior conclusions are reused from the
  authoritative notes listed above.

Post-fix coverage:

- Foreground 8RAW validation-minimal run:
  `output/product_priority_reset_phase1/alignment_8raw_primary_delivery_fix`.
- `phase1_review_matrix_resolved_primary_delivery_fix.tsv` has
  `GO blocker count: 0`.
- `single_dr_production_gate_decision_report.py` on the post-fix 8RAW alignment
  found `282` single-dR primary rows and `0` risky weak-seed or
  extreme-backfill primary rows.
- `targeted_gt_alignment_audit.py` default checkpoint remains `PASS 8 / 8`.
- `alignment_decision_report.py` returns `WARN`, not `FAIL`, because `40`
  primary rows are `rescue_heavy`; the 13 newly promoted rows are all flagged
  `rescue_heavy;weak_seed_tolerated`.

85RAW post-fix coverage:

- Foreground 85RAW run:
  `output/product_priority_reset_phase1/alignment_85raw_primary_delivery_fix`.
- `alignment_matrix.tsv` has `597` primary rows and SHA256
  `53EC16DA87D7BC3AE02C88120E5FBBD22CB45BB4C7FFCA07F605EE7D9EA8564D`.
- `single_dr_production_gate_decision_report.py` found `0` risky weak-seed,
  extreme-backfill, or duplicate-rescue-pressure primary rows.
- `alignment_decision_report.py` returned `WARN`, not `FAIL`.
- `targeted_istd_benchmark.py` failed only the known strict area mismatch rows:
  `d4-N6-2HE-dA` and `d3-N6-medA`.

This coverage is enough to stop treating `d3-N6-medA` as a qualitative blocker.
The remaining risk is explicit watch surface, not a GO/NO-GO blocker: five
85RAW primary rows carry `rescue_heavy;weak_seed_tolerated`, and strict area
benchmark mismatches stay in the quantitative follow-up lane.

## Issue #195 no-RAW Backfill decision ledger

The retained 85RAW artifact whose legacy manifest asserts revision
`7eef01e273ee62b91bd6c07e4a455994f76ca37b` was read without opening RAW files.
`tools/diagnostics/backfill_decision_ledger.py` rehydrated every Discovery CSV
through its owner receipt and streamed each large alignment projection once.
The bounded output is reproducible with:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.backfill_decision_ledger `
  --artifact-root <retained-artifact-root> `
  --output-dir output/issue195_backfill_decision_ledger `
  --expected-code-revision 7eef01e273ee62b91bd6c07e4a455994f76ca37b
```

The complete owner rehydration accepted `317,036` rows. Its parser requires a
unique `sample + neutral_loss_tag + ms1_feature_row_id` for every feature-backed
candidate; all `79,443` feature-backed populated identities passed that
invariant, no non-feature-backed row carried a populated identity, and
`59,214` accepted candidates retained multiple seed scans. The reported zero is
therefore an owner-invariant acceptance result, not a second post-owner counter
that could independently reproduce duplicates. The proposed candidate-layer
defect—retaining other MS2 scans creates duplicate candidates for the same
physical MS1 feature—was not reproduced within that owner invariant domain. The display grouping
still contains `14,133` multi-candidate groups and `13,852` multi-best-scan
groups, but those fields do not prove one physical peak.

The retained final projection has `36,608 / 48,139` `primary_loser` rows
(`76.0464%`). Owner Backfill cost was `909.689 s`, `1,843,631` XIC extractions,
`761` RAW calls, and `878,879,685` points. The displayed-coordinate request
proxy reproduced `133,547 / 3,083,400` duplicates (`4.331%`), but exact logical
request identity, equivalence classes, and request-to-batch/call/point lineage
are absent. Those claims remain explicitly unavailable; no savings estimate is
made.

Decision ranking is accepted only when the timing projection declares
`backfill_scope=full-audit`, its input/backfill/skipped feature counts close with
zero skipped features, and its Backfill feature count exactly matches the
review-group universe. Candidate projection links additionally bind
`sample_stem + source_candidate_id`, and request-proxy per-group counts use a
stable group representative so equivalent row reordering cannot change the
ledger.

The retained manifest is legacy `untargeted_provisional_matrix_run_v1` and is
rejected by the current manifest owner. The diagnostic records that
rehydration gap explicitly instead of promoting legacy embedded assertions,
including the asserted producer revision.
Its A–E layer verdicts are: A not reproduced at the owner-bound candidate
identity layer; B inconclusive without the physical-peak owner payload; C
aggregate-only/partial; D final-projection-only with pre-Backfill knowability
unavailable; and E unavailable without exact request and batch lineage.

Decision: value priority is an owner-valid early-loser-settlement shadow before
exact request coalescing, derived from the observed `36,608` loser rows while
exact request equivalence remains unavailable. A compatible artifact with zero
loser rows reports an inconclusive priority. Dependency order is decision
ledger, owner-valid early-settlement shadow, owner-minted exact request
identity/coalescing, artifact-format work, then recovery/resumability. This is
`diagnostic_only`, uses stale-revision cost evidence rather than current-head
parity, and grants no product or scientific authority.

## Issue #195 owner-valid early-loser shadow

Issue #208 adds an opt-in in-process shadow at the typed Alignment owner
boundary. It calls the existing pre-Backfill consolidation owner on the current
`OwnerAlignedFeature` population, measures the baseline/proposal workload with
the existing owner Backfill request-plan owner, and compares the immutable
proposal with the typed final Matrix after claim registry and primary
consolidation. The original owner features continue through Backfill; the
shadow report is not an execution input.

The retained Issue #195 artifact cannot supply this packet because it omits the
complete pre-Backfill owner population. No persisted display projection is used
to reconstruct that truth. No RAW, 8RAW, or 85RAW run was authorized for this
slice. Current evidence is therefore deterministic no-RAW owner fixtures and
pipeline control-flow oracles, not a current-head real-population result.

The lineage owner accepts legal sequential consolidation chains such as
`primary_loser → primary_loser → primary_winner`; it requires every reference
to exist, rejects cycles, and requires a loser chain to terminate at a
self-referencing primary winner. This closes the PR #207 direct-winner review
debt without normalizing or rejecting canonical two-stage producer output.

The emitted `alignment_early_loser_shadow.json` covers survivor/loser/
unresolved rows, direct/terminal winner identity, full proposal/final lineage,
owner request-plan feature/cell/logical-XIC workload counts, final comparison,
rollback, and every expected-diff surface. Actual shadow output differences are
hard-zero because the shadow never constructs an alternate Matrix. A
counterfactual activated skip has unavailable selected-observation,
missing/extra-cell, status/reason, area, Matrix identity/value, and review-
population evidence; these are explicit activation blockers.

Decision: `GO` for retaining the owner-valid shadow instrumentation only.
Activation remains `REDESIGN / blocked` until a separately authorized,
owner-valid survivor-only execution closes the counterfactual expected diff.
Any false loser, terminal-winner mismatch, missing/cyclic lineage, or nonzero
scientific/output diff is `KILL`. Status remains `diagnostic_only`.
