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
- Treat programme-level `Frozen Inputs` as rerun memory when they point to a
  canonical owner or retained artifact. Reuse them until their checksum, public
  contract, owner, or implementation assumption materially changes; a new date
  alone is not invalidation. Keep programme status in its issue/map rather than
  copying it into this ledger.
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

The shadow requires `full-audit` and rejects simultaneous activated
preconsolidation so the measured baseline remains the execution baseline. Its
persisted projection binds the run, canonical typed source-owner snapshot,
`AlignmentConfig`, and final comparison rows with SHA-256 digests. It
also binds the sample order and available RAW sample-stem request universe. It
explicitly does not bind Matrix cells/values and cannot be rehydrated as
scientific authority. A proposal with any non-positive requested-feature,
cell, or logical XIC-request reduction is insufficient decision value and
returns `KILL`.

Decision: `GO` for retaining the owner-valid shadow instrumentation only.
Activation remains `REDESIGN / blocked` until a separately authorized,
owner-valid survivor-only execution closes the counterfactual expected diff.
Any false loser, terminal-winner mismatch, missing/cyclic lineage,
non-positive projected workload reduction, or nonzero scientific/output diff
is `KILL`. Status remains `diagnostic_only`.

## Issue #195 owner-minted exact request reuse

Issue #210 implements the current in-process owner path for exact Backfill
request equivalence and typed result fan-out. The request-plan owner binds the
canonical sample, full `AcquisitionSourceIdentity`, exact m/z/RT/ppm request,
backend, window strategy, validation phase, and trace-result semantics. Every
hypothesis-bound logical request belongs to exactly one deterministic class.
The extraction owner evaluates one representative per class and fans the typed
trace back to the complete logical population before the unchanged
per-hypothesis peak, candidate-audit, claim-registry, consolidation, Matrix, and
writer owners run.

No RAW, 2RAW, 8RAW, or 85RAW input was opened for this slice. Deterministic
owner fixtures observed two logical requests in one canonical class, one
extraction request, and two hypothesis-bound results. Mutation and negative
oracles separate sample/acquisition, m/z, both RT bounds, ppm, backend, window,
validation, and result semantics; missing or mismatched acquisition ownership
cannot emit an exact-equivalence observation. Synthetic baseline-versus-exact
cells, candidate audit, final claim/consolidation Matrix, row identity, and the
rendered cells TSV are hard-zero, including byte-for-byte writer parity.

The PR #207 displayed-coordinate proxy remains historical diagnostic context,
not proof. Its exact-owner debt is closed for the current owner-closed in-process
runtime, but the retained stale-revision artifact still lacks the typed request
population and therefore cannot be rehydrated into current exact class counts.
No comparable current owner-correlated population, elapsed-time, RAW-call,
chromatogram-call, point-count, or monetary evidence exists, so no savings or
real-dataset prevalence claim is made. PR #209's early-loser shadow remains
`diagnostic_only`, does not feed Backfill or Pass B, and is not used by this
reuse path. PR E streaming/artifact format and PR F recovery/checkpoint scope
remain excluded.

Decision: `GO` for the exact-safe implementation under owner closure, with
validation status `diagnostic_only` and synthetic/no-RAW evidence only.
Any incomplete owner partition, cross-semantics reuse, logical fan-out loss, or
nonzero final scientific/output diff is `KILL / REDESIGN`.

## Issue #195 streamed provisional publication

Issue #212 replaces retained dense provisional matrix, identity, and cell-
provenance row dictionaries with an immutable Alignment-owned projection plan.
The existing provisional writers consume that plan as bounded row streams; the
existing whole-set temporary-file/rename publication owner remains the only
final visibility path. Matrix values, row identity, schema, ordering, filenames,
summary content, and atomic visibility are unchanged. PR #211 exact request
identity is consumed only through the typed Matrix input and is not reconstructed
from a persisted projection. PR #209 remains an opt-in shadow and is not an
execution input.

No RAW, 2RAW, 8RAW, or 85RAW input was opened. A deterministic no-RAW allocation
probe over 400 groups by 100 samples retained zero dense row objects, zero cells,
and 400 compact per-row count entries. Building the compact projection peaked at
130,364 traced bytes; materializing the legacy-compatible 40,000-row provenance
view peaked at 18,940,952 bytes (145.29 times the compact peak). This bounded
probe is mechanism evidence only, not a real-population elapsed-time or savings
claim. Frozen representative and formula/unquantified edge-case byte hashes,
schema/order/identity oracles, and whole-bundle failure tests remain hard-zero.

The reproducible probe owner and assertion are
`tests/test_alignment_provisional_matrix.py::test_projection_has_bounded_no_raw_peak_allocation`.
The exact observation above is emitted with:

```powershell
@'
import json, runpy
probe = runpy.run_path('tests/test_alignment_provisional_matrix.py')['_allocation_probe']
observed = probe()
observed['peak_ratio'] = round(observed['materialized_provenance_peak_bytes'] / observed['compact_projection_peak_bytes'], 2)
print(json.dumps(observed, sort_keys=True))
'@ | .venv\Scripts\python.exe -
```

The compact-plan build retains its shared measured stage. Per-artifact lazy row
projection is interleaved with rendering and temporary-file writes, so those
phases remain explicitly unavailable or partial rather than receiving fabricated
attribution; rename and final-file hash remain measured. Manifest-bound immutable
hashing and directory publication belong to Issue #213, which is sequentially
blocked by #212; recovery/checkpoint work remains excluded as PR F.

Decision: `GO` for retaining the exact-safe W5a streaming implementation with
validation status `diagnostic_only`. Any byte/schema/order/identity/atomic-
visibility difference is `KILL`; a need to reconstruct semantic truth from a
projection or to combine W5a with manifest ownership is `REDESIGN`.

## Issue #213 manifest-bound atomic publication

Issue #213 closes W5b inside the existing provisional-run manifest owner. The
runner now submits one complete attempt directory to
`publish_provisional_run_bundle_atomic`. Its upstream Alignment writer supplies
one complete owner-minted writer fingerprint batch containing the four
provisional artifacts, and the publication owner mints a path/root-bound receipt
while verifying the retained sample-metadata copy. The manifest owner reuses
those five facts, hashes the Discovery batch index once while building the
manifest, and validates the
exact owner serialization against a private six-role transaction. Before every
directory-replace attempt it fully rehashes all six bound files, compares the
owner-written `run_manifest.json` to the exact in-memory bytes, and makes the
whole directory visible with one atomic rename. No filesystem timestamp is used
as a byte-identity oracle. This is a closed single-writer visibility contract:
the publication owner and its in-process collaborators stop writing staging
before binding, but W5b does not claim an OS-level seal against another
same-permission process writing during the final check-to-rename interval. A
stronger concurrent-writer threat model is a separate storage/lifecycle redesign;
visible bundles remain untrusted until the independent validator rehashes them.
Foreign receipts or transactions, duplicate or
missing roles, path/schema substitution, valid manifest substitution, same-size
artifact mutation with restored mtime, and retry-time artifact or manifest
mutation fail before the final directory exists. The public
`validate_provisional_run_manifest` path is unchanged and still reads and hashes
the visible bytes independently.

The PR-family finding census classified the two hosted P2 findings plus the
follow-up cross-role substitution findings as trust-boundary re-derivation. The
bounded Alignment ingress is four manifest roles within the Alignment writer's
complete receipt batch: publication requires each role to map to one unique
receipt path and its canonical writer filename before assigning schema or
SHA/size facts. Other owner receipts remain an unclaimed part of the complete
writer census. The class oracles enumerate all 23 non-identity role
permutations plus a duplicate-path census and reject each before manifest write
or final visibility; a real no-RAW writer integration proves the normal eight-
receipt batch can publish its four manifest-bound artifacts.

Manifest schema, claim levels, relative paths, artifact SHA/size rows, and exact
manifest plus bound-artifact bytes are hard-zero against the legacy write path.
The private transaction identity, writer batch, and metadata receipt are not
persisted; the manifest remains byte-bound only and gains no authenticity,
semantic replay, scientific, Matrix, writer, or activation authority. PR #209
remains shadow-only, PR #211 exact request identity remains typed upstream input,
W5a remains the merged projection prerequisite, PR #216 is the sequential E2a
writer-receipt prerequisite, and PR F recovery/checkpoint work is excluded.

A 9-pair, order-interleaved, no-RAW probe used one 8 MiB synthetic provisional
matrix plus the five smaller bound files. The legacy manifest write/immediate-
validation path observed 12 fingerprint calls and 16,779,410 bytes per run;
median summed fingerprint time was 0.022091 s (range 0.020313-0.024616 s). The
closed atomic-bundle path observed 7 calls and 8,390,689 bytes per run: one
Discovery-index hash while building, followed by one full six-file confirmation
before publication. Median summed fingerprint time was 0.011333 s (range
0.010254-0.013194 s); the paired median removed fingerprint time was 0.010464 s.
All nine pairs preserved exact file bytes. This is synthetic owner-call,
byte, and function-time evidence only; it is not physical disk I/O, end-to-end
elapsed, RAW-call, chromatogram-call, point-count, monetary, or real-population
savings evidence.

Focused no-RAW manifest/runner/projection/atomic regressions passed 88 tests.
The principal census/parity oracle is
`tests/test_alignment_provisional_run_manifest.py::test_manifest_bound_atomic_publish_reuses_owner_fingerprints_without_diff`;
the exact-manifest substitution, same-size restored-mtime mutation, retry-time
artifact/manifest mutation, and publication-failure tests in the same file prove
no partial final directory. Validation status remains `diagnostic_only`.

Decision: `GO` for the exact-safe W5b mechanism when all remaining static,
architecture, whole-PR review, and independent QA gates pass. Any manifest or
bound-artifact byte diff, caller-minted authority, skipped independent validator,
or partial final visibility within the closed single-writer protocol is `KILL`;
a requirement to resist another same-permission writer during publication, or a
schema/lifecycle/recovery dependency, is `REDESIGN`.

## Issue #234 CID-NL Discovery MS1 telemetry and batching gate

Issue #234 initially recorded Terminal A at clean revision `3b891479`; that
threshold-only disposition is superseded by the maintainer refreeze and paired
evidence below. Transaction 1
maps every non-empty `DiscoverySeedGroup` to one canonical ordered `XICRequest`,
acquires singleton owner-minted `TraceAcquisitionBatch` receipts, and preserves
the existing peak/candidate path. `execution_runtime_owner` alone aggregates the
accepted acquisition receipts into sealed request, batch, adapter-call, and
trace-point metrics; `TimingRecorder` renders those values plus the Discovery
input census and exact native-scan-window characterization. Worker projections
cannot submit those counts. RSS remains explicitly unsupported with the reason
`unsupported: Discovery runner has no owner-bound RSS sampler`.

Focused no-RAW verification used the Discovery MS1/pipeline, acquisition
contract, and runtime-observability suites: 100 tests passed. Scalar-versus-owner
candidate equality, duplicate logical members, input population completeness,
wrong cardinality, caller-forged counts, duplicate/unknown measurement fields,
terminal peak failure, RAW disposal, and original-exception preservation were
covered. Ruff, focused mypy, the architecture census (42 tests plus the direct
script smoke), and commit hooks passed.

The fresh current 2RAW characterization used the historical CID-NL 8RAW lane's
`NormalBC2312_DNA.raw` (199,440,593 bytes) and `TumorBC2312_DNA.raw`
(202,128,099 bytes), exposed through task-local hardlinks. The clean producing
revision, default `scripts.run_discovery` CID-NL/peak settings, Python project
environment, and Thermo RawFileReader 4.0.19.0 were fixed across both runs; the
RawFileReader DLL SHA-256 was
`ca452149b7fd4a7dd99b1b86daea4b23485ec87c43fda213e9f9ab97bbcf240d`.
No application XIC cache was enabled. OS filesystem cache was uncontrolled: the
one-worker scalar run executed first and the two-worker process smoke second.
Both used full Discovery candidate/review CSV output and one repetition. Shell
elapsed observations were approximately 42.0 s scalar and 23.7 s process; these
single, ordered runs are process-smoke evidence, not a benchmark conclusion.

| Sample | N logical | U exact windows | C256 | Predicted reduction | Singleton batches/calls | Trace points | Candidate rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NormalBC2312_DNA | 3,358 | 3,102 | 3,102 | 7.62% | 3,358 / 3,358 | 50,703 | 3,358 |
| TumorBC2312_DNA | 3,792 | 3,502 | 3,503 | 7.62% | 3,792 / 3,792 | 58,035 | 3,789 |
| Aggregate | 7,150 | 6,604 | 6,605 | 7.6224% | 7,150 / 7,150 | 108,738 | 7,147 |

For both samples `input_group_count = empty_group_count + N`, with zero empty
groups, and singleton physical calls equal logical requests. The process run
reported identical owner counts and characterization. Its pickled request/result
payload observations were 3,646/1,739,270 bytes for Normal and
3,645/1,976,216 bytes for Tumor; these are explicitly partial IPC observations
and exclude executor/framework overhead.

Scalar-versus-process artifacts were byte-identical:

- batch index:
  `beb8e4e1d376a573386d93a751f5e70179d0dfb32d0cf490b1bda8cf7e1b7164`;
- Normal candidates/review:
  `68510b5fff4f0b87d20d18e70168498f970d0af68ef9222d6a4e814ea11ecafc` /
  `b1bbdd591345ee8c80e8124091f631495deedaedbf4a82aff3d701448ff09bfd`;
- Tumor candidates/review:
  `58e016b5f5bab1b30f96e7e93b5c41d0a0bfe46f9380fc5fe423e5a1ec9743ce` /
  `723278e68109898707fddadaf82c2a8f9ebc180e1facc096cc5743e386773184`.

The original aggregate promotion result was `7.6224% < 20%`. Under the initial
hard-threshold wording, Transaction 2 was not authorized, the internal batch
maximum remained 1, and no paired 8RAW or 85RAW run was launched. The original
disposition was **`KILL_BATCHING_NO_VALUE`**. The acceptance owner subsequently
clarified that 20% is an advisory strong signal rather than a hard prohibition;
the current positive, bounded, reversible exact-safe candidate was authorized
for paired measurement. The superseding result follows.

### Maintainer soft-gate refreeze and Terminal B evidence

On 2026-08-11 the GitHub Issue #234 body and maintainer receipt were refrozen so
that a positive exact-window opportunity below 20% may proceed to a bounded
candidate. Paired 2RAW must beat observed noise with exact parity before paired
8RAW; Terminal B still requires the complete three-pair 8RAW oracle. The
production candidate changes only the internal Discovery MS1 batch maximum from
1 to 256 at commit `8beebe46`. Revision `bf5fd79e` additionally applies the
existing bounded Windows atomic-replace retry pattern to `TimingRecorder` after
two excluded runs independently reproduced `PermissionError: [WinError 5]`
while updating a live timing sidecar. That runtime fix changes neither the
candidate nor scalar acquisition path.

The scalar and candidate variants used the same clean `bf5fd79e` code,
environment, Thermo RawFileReader 4.0.19.0, DLL SHA-256
`ca452149b7fd4a7dd99b1b86daea4b23485ec87c43fda213e9f9ab97bbcf240d`,
default `scripts.run_discovery` CID-NL/peak settings, sequential worker budget,
full Discovery candidate/review CSV output, and no application XIC cache. A
task-local diagnostic wrapper changed only the private batch maximum to 1 for
scalar and 256 for candidate. One scalar and one candidate warmup were excluded
at each tier; the three recorded scalar/candidate observations were interleaved.
OS filesystem cache remained externally uncontrolled after warmup. Each run had
a 600-second foreground timeout and used the live/final `timing.json` sidecar.

Paired 2RAW reused `NormalBC2312_DNA.raw` and `TumorBC2312_DNA.raw` through the
same task-local hardlinks. Recorded wall seconds were scalar
`32.720, 31.695, 32.512` and candidate `31.471, 34.572, 30.729`. Scalar wall
median was `32.512 s`, candidate median was `31.471 s`, scalar wall noise was
`3.155%`, and integrated gain was `3.204%`. The `discover.ms1_backfill` median
was `11.093 -> 9.713 s`: `12.440%` gain against `5.165%` scalar stage noise.
Every run preserved 7,150 logical requests and 108,738 trace points; calls were
`7,150 -> 6,605`. The same five Discovery CSV hashes were byte-identical in all
six runs. The paired 2RAW gate therefore passed, narrowly on whole-run wall and
more clearly on the isolated MS1 stage.

Paired 8RAW used the documented validation directory's eight files:
`BenignfatBC1055_DNA.raw`, `BenignfatBC1151_DNA.raw`,
`Breast_Cancer_Tissue_pooled_QC3.raw`,
`Breast_Cancer_Tissue_pooled_QC5.raw`, `NormalBC2263_DNA.raw`,
`NormalBC2312_DNA.raw`, `TumorBC2263_DNA.raw`, and
`TumorBC2312_DNA.raw`, totaling 1,601,282,569 bytes.

| Pair | Scalar wall (s) | Candidate wall (s) | Scalar MS1 (s) | Candidate MS1 (s) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 158.494 | 146.622 | 61.147 | 49.327 |
| 2 | 166.620 | 149.663 | 66.300 | 51.634 |
| 3 | 160.238 | 146.829 | 62.603 | 49.556 |

Scalar wall median was `160.238 s`, candidate wall median was `146.829 s`,
scalar wall noise was `5.071%`, and integrated gain was `8.368%`. The
`discover.ms1_backfill` median was `62.603 -> 49.556 s`: `20.840%` gain against
`8.232%` scalar stage noise. Every candidate repetition was faster than its
paired scalar repetition. Logical requests remained 31,819 and trace points
remained 454,223. Mechanical batches changed `31,819 -> 128`; physical vendor
calls changed `31,819 -> 29,148`, a reduction of 2,671 calls (`8.394%`).

All 17 Discovery CSV artifacts were byte-identical across all six recorded
8RAW runs. The common batch-index SHA-256 was
`1a634ab3648bb26f1b96fe8e85078fae62ed9edaf3fe77741fa3adc7d489fbbf`.
The sequential runner emits no process IPC metric. Worker and process-tree RSS
remained explicitly unavailable with the unchanged truthful reason
`unsupported: Discovery runner has no owner-bound RSS sampler`; no measured RSS
claim is made. No super-window, cache, public tuning/configuration, Matrix,
writer, scientific-rule, Pt/composite, Alignment, or Backfill change occurred.

The exact-safe correctness oracle and the integrated noise rule both pass.
Superseding disposition: **`RETAIN_NATIVE_BATCHING`** (Terminal B). Validation
status remains `diagnostic_only`; this performance result grants no new Matrix,
writer, activation, publication, or scientific authority. No 85RAW run was
launched.

### Two-worker process confirmation

The highest-value residual evidence loop fixed both variants to the current
two-worker Windows process backend at clean revision `29d02d0a`. It reused the
same two RAW hardlinks, Python 3.12.13, DLL identity, default Discovery settings,
full CSV outputs, and foreground timing contract. A task-local ignored wrapper
applied the scalar `1` or candidate `256` batch maximum at module import so the
override executed inside each spawned RAW-owning worker, not only in the parent.
One warmup per variant was excluded. The formal order was
`S1 C1 / C2 S2 / S3 C3`; OS filesystem cache remained uncontrolled.

| Pair | Scalar wall (s) | Candidate wall (s) | Paired gain |
| --- | ---: | ---: | ---: |
| 1 | 22.149 | 20.654 | 6.753% |
| 2 | 21.797 | 21.203 | 2.726% |
| 3 | 21.429 | 20.796 | 2.956% |

Scalar wall median was `21.797 s`, candidate wall median was `20.796 s`, and
the median gain was `4.594%` against `3.303%` scalar range-over-median noise.
Every paired candidate was faster. Because samples execute concurrently, the
process-stage decision metric is the maximum per-sample
`discover.ms1_backfill` span per run rather than the sum: its median was
`6.360 -> 5.648 s`, an `11.195%` gain against `4.194%` scalar stage noise.

All six formal runs preserved 7,150 logical requests, 108,738 trace points, and
the five byte-identical Discovery CSV hashes already listed above. Mechanical
batches changed `7,150 -> 29`; physical vendor calls changed `7,150 -> 6,605`.
The worker-emitted batch-maximum observations summed to 2 for scalar and 512 for
candidate, confirming that the intended variant reached both spawned workers.
Partial pickled request/result payload observations changed
`3,722,849 -> 3,566,259 bytes`; they still exclude executor framing and confirmed
transport bytes. Worker and process-tree RSS remain unavailable with the owner
reason `unsupported: Discovery runner has no owner-bound RSS sampler`.

This confirmation closes the process-backend uncertainty for the bounded 2RAW
lane and strengthens, but does not broaden, Terminal B. It does not establish a
universal dataset gain, an RSS bound, or product/scientific authority. No
additional 8RAW or 85RAW run was launched for this loop.

### Corrective owner-bound RAW-open receipt confirmation

The review-corrective receipt seam was validated in one fresh two-worker 2RAW
process smoke at clean producing revision `633c4d80`. The final #234 delivery
keeps that acquisition/runtime/Discovery code unchanged; it only removes the
separable `TimingRecorder` retry into follow-up #236 and adds this durable
receipt. The smoke used Python 3.12.13, Thermo RawFileReader 4.0.19.0, and DLL
SHA-256
`ca452149b7fd4a7dd99b1b86daea4b23485ec87c43fda213e9f9ab97bbcf240d`.

Inputs were the same task-local hardlinks used by the earlier 2RAW lane:

- `NormalBC2312_DNA.raw`: 199,440,593 bytes, SHA-256
  `74bbf71d3ebc85d944b23cbd5b5487bdbc5d38a1b57f56bbf0ee43f20ff729bf`;
- `TumorBC2312_DNA.raw`: 202,128,099 bytes, SHA-256
  `cbf020359be2249e800825ad810a3ea38ea990599da08ba0f165ac60e13a7a1f`.

The task-local wrapper selected the retained private candidate batch maximum
`256`, default Discovery CID-NL/peak settings, full candidate/review CSV output,
and exactly two process workers. No application XIC cache was enabled; OS
filesystem cache remained uncontrolled. One repetition was run in the
foreground. Wall elapsed was 22.628193 s, which is recorded only as smoke
provenance and is not a new performance comparison.

Both RAW-owning workers returned a typed `RawAcquisitionSessionReceipt` that
`execution_runtime_owner.derive_runtime_raw_open_metrics` validated against the
parent-minted capability:

| Sample | `raw_open_count` | Availability | Reason |
| --- | ---: | --- | --- |
| NormalBC2312_DNA | 1 | measured | empty |
| TumorBC2312_DNA | 1 | measured | empty |

The five persisted Discovery CSV artifacts matched the accepted exact-safe
oracle:

- batch index:
  `beb8e4e1d376a573386d93a751f5e70179d0dfb32d0cf490b1bda8cf7e1b7164`;
- Normal candidates/review:
  `68510b5fff4f0b87d20d18e70168498f970d0af68ef9222d6a4e814ea11ecafc` /
  `b1bbdd591345ee8c80e8124091f631495deedaedbf4a82aff3d701448ff09bfd`;
- Tumor candidates/review:
  `58e016b5f5bab1b30f96e7e93b5c41d0a0bfe46f9380fc5fe423e5a1ec9743ce` /
  `723278e68109898707fddadaf82c2a8f9ebc180e1facc096cc5743e386773184`.

`run_ok` and the owner-receipt/parity gate both passed. This closes only the
review-found RAW-open observability gap: it does not repeat or broaden the
paired performance evidence, and the Terminal B disposition remains
**`RETAIN_NATIVE_BATCHING`** with `diagnostic_only` evidence status. No new
8RAW or 85RAW run was launched.
