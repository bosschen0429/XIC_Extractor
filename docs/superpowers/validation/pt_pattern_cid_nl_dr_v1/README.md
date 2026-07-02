# Pt pattern + CID-NL(dR) composite Discovery validation v1

Doc placement: repo_support_doc
Doc kind: validation_artifact
Doc lifecycle: archived
Repo owner: docs/product/discovery.md
Doc exit rule: This file is archived as a point-in-time diagnostic snapshot
(Task 9 real-sample composite run recorded below: candidate counts, output
paths, review status, and follow-up fixes for the composite CID-NL loader /
seed-first search path). Keep this file (archived) until Discovery product docs
or a newer validation artifact covers the same Pt pattern + CID-NL(dR)
composite real-sample diagnostic lane.

Status: Task 9 (Composite DDMS2 Diagnostic Gate) of
`docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
has run the neutral-loss discovery CLI and the scan-level Pt pattern proxy
end-to-end against the real-sample DDMS2 RAW file, then ran the composite Pt
pattern + CID-NL(dR) Discovery CLI. A follow-up fix made the composite path
CID-NL seed-first instead of "global Pt match then intersect", and fixed the
real `review_only_orphan_nl` loader crash documented by the initial run.

This validation lane is `diagnostic_only`, never product validation or Matrix
authority.

## Purpose

Validate that the Pt pattern + CID-NL(dR) composite Discovery lane
(`pt_pattern_cid_nl_dr` preset) can run end-to-end against a real user-provided
DDMS2 RAW sample, using two detection outputs joined together:

1. Neutral-loss discovery candidates (`discovery_candidates.csv`), produced by
   the existing `scripts.run_discovery` CLI.
2. Pt pattern detected features (`detected_features.csv`), produced by the
   scan-level proxy tool (`tools.diagnostics.pt_pattern_scan_proxy`), because
   no accepted real feature-detector source exists yet.

This validation lane does not validate peak-detection accuracy, does not
promote any oracle, and does not change counted detection, Matrix authority, or
production readiness.

## Detected-Feature Source

Same as Task 8's `pt_pattern_discovery_v1` lane: no accepted detected-feature
CSV source exists in this repo yet, so this lane uses the fallback scan-level
proxy (`raw_scan_proxy_v1`, `validation_status = diagnostic_only`). See
`docs/superpowers/validation/pt_pattern_discovery_v1/README.md` for full detail
on the proxy's design and limitations.

Current proxy defaults use a 20 ppm scan-level lookup window, require at least
two MS1 scans per proxy group within one RAW file, emit `trace_quality = "OK"`
with blank `shape_score`, and remain explicitly `diagnostic_only`.

## Commands

Commands run with env vars loaded from `.env.xic-local`; local absolute RAW and
DLL paths are intentionally represented by env vars in this tracked doc.

```powershell
Get-Content .env.xic-local | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
  $parts = $_ -split '=', 2
  Set-Item "Env:$($parts[0].Trim())" $parts[1].Trim()
}

.venv\Scripts\python.exe -m scripts.run_discovery `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\cid_nl

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_scan_proxy `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\proxy_seed_first_fixed

.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_cid_nl_dr_v1\proxy_seed_first_fixed\detected_features.csv `
  --cid-nl-candidates-csv output\pt_pattern_cid_nl_dr_v1\cid_nl\discovery_candidates.csv `
  --output-dir output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed `
  --preset pt_pattern_cid_nl_dr

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\trace_diagnosis_limit50 `
  --limit 50
```

## Results

Output directories (relative to repo root, git-ignored, not committed):

- `output/pt_pattern_cid_nl_dr_v1/cid_nl/` -- neutral-loss discovery output
  (`discovery_candidates.csv`, `discovery_review.csv`).
- `output/pt_pattern_cid_nl_dr_v1/proxy_seed_first_fixed/` -- proxy
  `detected_features.csv` + `proxy_provenance.json`.
- `output/pt_pattern_cid_nl_dr_v1/pt_intersection_seed_first_fixed/` --
  composite Pt pattern Discovery CLI output.

Neutral-loss discovery candidate count: **367**.

`discovery_candidates.csv` has 367 data rows. Header confirmed to include the
columns needed by the base join predicate: `ms1_feature_row_id`,
`neutral_loss_tag`, and `discovery_candidate_state`. State breakdown:
`ms1_feature_nl_rescued` = 159, `review_only_orphan_nl` = 140,
`ms1_feature_nl_supported` = 68. All rows have `neutral_loss_tag = DNA_dR`.

Proxy detected-feature count: **103,992** rows.

`proxy_provenance.json` confirms `diagnostic_only: true`,
`not_a_product_feature_detector: true`,
`detected_feature_source: "raw_scan_proxy_v1"`, `lookup_ppm: 20`,
`min_scans_per_group: 2`, `ms1_scan_count: 13577`,
`scan_hit_count: 1225908`, `raw_group_count: 37638`, and
`group_count: 25998`.

Composite candidate count: **354** rows.

`pt_pattern_sidecar.json` reports:

```text
candidate_count = 354
pt_only_candidate_count = 0
cid_nl_supported_candidate_count = 354
```

All 354 rows have `cid_nl_base_status = matched` and
`cid_nl_base_join_mode = mz_rt_fallback`. This fallback join is expected for the
scan-proxy source because it has no stable same-feature identity linking proxy
rows to CID-NL `ms1_feature_row_id` values.

All 354 output bases are scan-proxy `...|base` rows. The matcher no longer
allows scan-proxy `pt_plus_1`, `pt_plus_2`, or `pt_plus_4` rows to become
shifted base anchors, and the composite pipeline no longer runs global Pt
matching before CID-NL intersection. It first seeds base feature IDs from
CID-NL(dR), then asks the Pt matcher to check only those bases for +1/+2/+4.
Composite rows use `candidate_state = pt_pattern_cid_nl_supported`; this is
still a candidate/review state, not Matrix authority or counted detection.

Runtime observed:

- Neutral-loss discovery: ~20-25 seconds.
- Proxy scan pass: ~117.4 seconds over 13,577 MS1 scans.
- Composite seed-first Discovery CLI: ~2.8 seconds over 103,992 proxy feature
  rows plus 367 CID-NL rows.

The proxy scan pass remains the expensive diagnostic bridge. The feature-level
matcher and seed-first composite path are not the dominant bottleneck in this
run.

## Trace Co-Elution Diagnostic

A follow-up chromatography-aware trace diagnostic was run against the first 50
composite candidates using `tools/diagnostics/pt_pattern_trace_diagnosis.py`.
This tool extracts vendor XIC traces for base / `+1.0021` / `+2.0023` /
`+4.0052` around the base RT and writes diagnostic-only co-elution metrics. It
does not create candidates, write Matrix values, or change Discovery outputs.
The summary explicitly separates the scan-level seed (`scan_pattern_seed_status`)
from trace confirmation (`trace_support_status`) and writes
`combined_pt_pattern_status`; `pt_pattern_detected_diagnostic` means both
evidence layers support the candidate in this diagnostic output only.

Output directory:

- `output/pt_pattern_cid_nl_dr_v1/pt_intersection_seed_first_fixed/trace_diagnosis_limit50/`

Candidate-level trace support summary, 50-row sample after the Gaussian15
decision-basis v2 rerun:

- `supported`: 41
- `partial_support`: 7
- `incomplete_trace_signal`: 2

Combined diagnostic Pt status, 50-row sample:

- `pt_pattern_detected_diagnostic`: 41
- `pt_pattern_candidate_partial`: 7
- `pt_pattern_candidate_incomplete`: 2

Position-level support summary, 50-row sample:

- `pt_plus_1`: 48 supported, 1 review, 1 missing signal
- `pt_plus_2`: 48 supported, 1 review, 1 missing signal
- `pt_plus_4`: 41 supported, 7 review, 2 missing signal

Interpretation: CID-NL seed-first intersection produces a much stronger
trace-confirmed slice than Pt-only scan-proxy matching in the sampled rows, but
the v2 rerun shows that raw trace similarity was too sensitive for the machine
gate. Gaussian15 smoothing is the current decision basis, and `+4` remains the
main weak position. This supports a two-layer design: scan-level pattern
finding proposes seeds, chromatographic co-elution confirms which seeds are
diagnostic Pt detections, and composite CID-NL search should remain seed-first.

## Manual Review Packet

A bounded manual-review packet was generated from the composite candidate CSV
and trace-diagnosis outputs. This packet is the intended handoff for user
review; it is not an oracle until the user fills `human_review_decision` and
the reviewed rows are promoted by a separate oracle/promotion step.

Command:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_review_packet `
  --candidates-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\pt_pattern_candidates.csv `
  --trace-summary-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\trace_diagnosis_limit50\pt_pattern_trace_summary.csv `
  --trace-metrics-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\trace_diagnosis_limit50\pt_pattern_trace_metrics.csv `
  --output-dir output\pt_pattern_productization_v1\manual_review_packet\pt_cid_nl_dr_ddms2 `
  --packet-label pt_pattern_productization_v1 `
  --source-label pt_cid_nl_dr_ddms2 `
  --max-per-status 5
```

Output directory, relative to repo root and git-ignored:

- `output/pt_pattern_productization_v1/manual_review_packet/pt_cid_nl_dr_ddms2/`

Packet summary:

- `schema_version = pt_pattern_manual_review_packet.v1`
- `readiness_tier = diagnostic_only`
- `oracle_status = pending_human_review`
- Row count: 12
- Status buckets: 5 `pt_pattern_detected_diagnostic`, 5
  `pt_pattern_candidate_partial`, and 2 `pt_pattern_candidate_incomplete`.

## Blind Overlay Review

The composite manual review packet was converted into a blind MS1/XIC overlay
gallery. The HTML shows normalized base / `+1.0021` / `+2.0023` / `+4.0052`
traces for each packet row. It intentionally hides diagnostic status buckets
and Codex's rule-based calls. Codex's calls are written only to the sealed CSV
and should not be opened until the user has recorded independent visual
decisions.

Commands:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_productization_v1\blind_overlay_trace_points\pt_cid_nl_dr_ddms2 `
  --limit 50 `
  --write-trace-points

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_blind_overlay `
  --review-packet-csv output\pt_pattern_productization_v1\manual_review_packet\pt_cid_nl_dr_ddms2\pt_pattern_manual_review_packet.csv `
  --trace-points-csv output\pt_pattern_productization_v1\blind_overlay_trace_points\pt_cid_nl_dr_ddms2\pt_pattern_trace_points.csv `
  --output-dir output\pt_pattern_productization_v1\blind_overlay\pt_cid_nl_dr_ddms2 `
  --title "Pt Pattern Blind Overlay - Pt + CID-NL DDMS2"
```

Output directories, relative to repo root and git-ignored:

- `output/pt_pattern_productization_v1/blind_overlay_trace_points/pt_cid_nl_dr_ddms2/`
- `output/pt_pattern_productization_v1/blind_overlay/pt_cid_nl_dr_ddms2/`

Blind-review outputs:

- `pt_pattern_blind_overlay_gallery.html` for human visual review.
- HTML trace curves and sealed calls use the same Gaussian15-smoothed
  trace-decision metrics. Raw trace points remain unchanged audit/reference
  evidence.
- `pt_pattern_machine_calls_sealed.csv` for Codex's sealed calls; do not open
  until user calls are recorded.
- `pt_pattern_blind_overlay_summary.json`, with no machine-call distribution.

## Manual Oracle Promotion

The bounded blind review across Pt-only and Pt+CID-NL packets was adjudicated
into:

- `output/pt_pattern_productization_v1/blind_overlay_review/pt_pattern_blind_review_adjudicated_oracle_candidate.csv`
- `output/pt_pattern_productization_v1/blind_overlay_review/pt_pattern_blind_review_adjudicated_oracle_summary.json`

Promotion command:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_oracle_promotion `
  --adjudicated-oracle-csv output\pt_pattern_productization_v1\blind_overlay_review\pt_pattern_blind_review_adjudicated_oracle_candidate.csv `
  --adjudication-summary-json output\pt_pattern_productization_v1\blind_overlay_review\pt_pattern_blind_review_adjudicated_oracle_summary.json `
  --review-packet-csv output\pt_pattern_productization_v1\manual_review_packet\pt_only_fullscan\pt_pattern_manual_review_packet.csv `
  --review-packet-csv output\pt_pattern_productization_v1\manual_review_packet\pt_cid_nl_dr_ddms2\pt_pattern_manual_review_packet.csv `
  --output-dir output\pt_pattern_productization_v1\oracle_promotion\pt_detected_oracle_v1 `
  --oracle-packet-id pt_detected_oracle_v1
```

Historical v1 output:

- `output/pt_pattern_productization_v1/oracle_promotion/pt_detected_oracle_v1/`
- `pt_pattern_oracle_promotion_packet.csv`: 28 provenance-matched rows; 10
  `accept` / `detected`, 18 `reject` / `non_detected`.
- `pt_pattern_oracle_promotion_summary.json`: `readiness_tier =
  production_candidate`, scoped to `bounded_manual_oracle_only`.

This v1 packet is historical calibration context because v2 Gaussian-basis
rebucketing changed the current review packet population. Its adjudications are
still reused where the same row is rechecked against current v2 metrics.

Intermediate 24-row v2 refresh output:

- `output/pt_pattern_productization_v1/oracle_refresh/pt_detected_oracle_v2_refresh/pt_pattern_v2_adjudicated_oracle_candidate.csv`
- `output/pt_pattern_productization_v1/oracle_promotion/pt_detected_oracle_v2/`
- `pt_pattern_oracle_promotion_packet.csv`: 24 provenance-matched rows; 5
  `accept` / `detected`, 19 `reject` / `non_detected`.
- `pt_pattern_oracle_promotion_summary.json`: `readiness_tier =
  production_candidate`, scoped to `bounded_manual_oracle_only`.

Current repaired v2 oracle output:

- `output/pt_pattern_productization_v1/oracle_repair/pt_detected_oracle_v2_repaired_32/`
- `output/pt_pattern_productization_v1/oracle_promotion/pt_detected_oracle_v2_repaired_32/`
- `output/pt_pattern_productization_v1/oracle_thresholds/pt_detected_oracle_v2_repaired_32/`
- `output/pt_pattern_productization_v1/diagnostic_gate/pt_detected_oracle_v2_repaired_32/`
- `output/pt_pattern_productization_v1/diagnostic_gate/trace_summary_limit50_current/`
- `pt_pattern_oracle_promotion_packet.csv`: 32 provenance-matched rows; 10
  `accept` / `detected`, 22 `reject` / `non_detected`.
- The threshold calibration recommends full-pattern status plus apex <= 10 s,
  Pearson >= 0.90, and overlap >= 0.50, giving 10 TP / 22 TN / 0 FP / 0 FN on
  the repaired oracle.
- The diagnostic gate replay applies the same rule through schema
  `pt_pattern_diagnostic_gate.v1`; with `--require-zero-fpfn`, it exits `0` and
  reports 10 TP / 22 TN / 0 FP / 0 FN on the repaired oracle.
- The full trace-summary diagnostic gate replay applies the same rule directly
  to the current 50-row Pt-only Fullscan trace summary and current 50-row
  Pt+CID-NL DDMS2 trace summary. It reports 100 rows total: 45 `accept`, 55
  `reject`; Pt-only contributes 15 accept / 35 reject, and Pt+CID-NL contributes
  30 accept / 20 reject.

The repaired v2 packet is current bounded manual-oracle evidence only. It still
does not promote scan-proxy, detector/runtime behavior, Matrix authority,
counted detection, Backfill, workbook, or export behavior.

## Superseded Initial Run

The initial Task 9 run crashed on `review_only_orphan_nl` rows because the
CID-NL loader required `ms1_feature_row_id` before filtering join-eligible
states. That loader bug is now covered by
`tests/test_delta_mass_pipeline.py::test_run_pt_pattern_discovery_ignores_review_only_orphan_nl_rows`.

The initial run also used the older proxy defaults and pre-seed-first composite
path. Treat that initial result as superseded diagnostic evidence, not current
behavior.

## Status

Result status: `diagnostic_only`.

This run is a first-look diagnostic result only. It does not validate
peak-detection accuracy, does not promote any oracle, and does not change
counted detection, Matrix authority, or production readiness. It awaits manual
user review of sampled composite candidate rows before any candidate example
from this or a future run can be accepted as an oracle.
