# Pt pattern Discovery validation v1

Doc placement: repo_support_doc
Doc kind: validation_artifact
Doc lifecycle: archived
Repo owner: docs/product/discovery.md
Doc exit rule: This file is archived as a point-in-time diagnostic snapshot
(Task 8 real-sample run recorded below: candidate count, output paths,
review status). Keep this file (archived) until Discovery product docs or a
newer validation artifact covers the same Pt pattern real-sample diagnostic
lane.

Status: Task 8 (Diagnostic Real-Sample Candidate Generation) of
`docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
has run the scan-level proxy and the Pt pattern Discovery CLI end-to-end
against the real-sample Fullscan RAW file. A follow-up bug/performance pass
fixed two proxy-path problems: scan-proxy rows now satisfy the diagnostic local
support gate, and scan-proxy labels cannot be reused as shifted base anchors.
See "Current Results" below. The run is `diagnostic_only`, never product
validation or Matrix authority, and awaits manual user review before any oracle
promotion.

## Purpose

Validate that the Pt pattern (`delta_mass(pattern_id=pt)`) Discovery lane
(candidate CSV / review CSV / sidecar JSON writers implemented in Tasks 2-6)
can run end-to-end against a real user-provided Fullscan RAW sample and
produce plausible candidate rows for human review -- without claiming
product feature-detection readiness or Matrix authority.

This validation lane answers one question only: "does this Fullscan sample
produce plausible Pt pattern candidates when run through the same candidate
writer the CLI already has?" It does not validate peak-detection accuracy,
does not promote any oracle, and does not change counted detection, Matrix
authority, or production readiness.

## Detected-feature source

No accepted detected-feature CSV source exists in this repo yet (confirmed:
no existing tool emits the exact
`xic_extractor.discovery.delta_mass_csv.DETECTED_FEATURES_COLUMNS` schema
besides the proxy built for this lane). Per the plan's Task 7 rules, this
validation lane therefore uses the fallback scan-level proxy:

```text
detected_feature_source = raw_scan_proxy_v1
validation_status = diagnostic_only
```

- Tool: `tools/diagnostics/pt_pattern_scan_proxy.py`
- Output: `detected_features.csv` (schema-valid, `source = "raw_scan_proxy_v1"`
  on every row) + `proxy_provenance.json` (provenance: which RAW file, which
  pattern/tolerances were used for the scan-level lookup, and an explicit
  `diagnostic_only` / "not a product feature detector" marker).
- This proxy is a SCAN-LEVEL bridge, not a real peak detector: no
  chromatographic peak-shape modeling, no smoothing, no deconvolution. Its
  default internal m/z lookup window now matches the Pt pattern matcher's real
  20 ppm m/z gate, and the matcher still re-applies the 20 ppm / 30 sec Global
  Constraint gates to every candidate regardless of proxy output.
- `trace_quality` is set to `"OK"` and `shape_score` is left blank so repeated
  scan-level full-pattern groups can pass the diagnostic local-support gate.
  This is recorded as `raw_scan_proxy_v1` / `diagnostic_only`; it is not a
  product chromatographic peak-quality claim.
- Proxy groups must appear in at least two MS1 scans within the same RAW file
  before pseudo-features are emitted. This is an LC-contiguity diagnostic
  filter, not a cross-sample or multi-RAW requirement.

If an accepted detected-feature CSV becomes available in a later task (a
real MS1 peak detector emitting this exact schema), that source should be
used directly instead and this section updated to name it, with
`detected_feature_source` changed accordingly.

## Current Results

Sample used: the Dec 2025 CSMU delta-mass Fullscan sample (a real
user-provided Fullscan RAW file; local absolute path kept only in the
git-ignored `.env.xic-local` local config, never in this doc).

Commands run (env vars loaded from `.env.xic-local` per the plan's command
shape):

```powershell
Get-Content .env.xic-local |
  Where-Object { $_ -match '^\s*[^#=]+=' } |
  ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item "Env:$name" $value
  }

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_scan_proxy `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy_fixed

.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\detected_features.csv `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy_fixed\pt_candidates_anchor_fixed `
  --preset pt_pattern

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\pt_candidates_anchor_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy_fixed\trace_diagnosis_limit50 `
  --limit 50
```

Output directories (relative to repo root, git-ignored, not committed):

- `output/pt_pattern_discovery_v1/fullscan_proxy_fixed/` -- proxy
  `detected_features.csv` (37,320 pseudo-feature rows: 9,330 scan-level
  groups x 4 Pt positions each) + `proxy_provenance.json`
  (`ms1_scan_count: 2726`, `scan_hit_count: 232780`,
  `raw_group_count: 12286`, `group_count: 9330`).
- `output/pt_pattern_discovery_v1/fullscan_proxy_fixed/pt_candidates_anchor_fixed/` --
  `pt_pattern_candidates.csv`, `pt_pattern_review.csv`,
  `pt_pattern_sidecar.json` (Pt pattern Discovery CLI output,
  `--preset pt_pattern`).

Candidate count: **8,722** Pt-only diagnostic candidates. All candidate base
features are scan-proxy `...|base` rows; scan-proxy `pt_plus_1` /
`pt_plus_2` / `pt_plus_4` rows are no longer allowed to become shifted base
anchors. All rows now use `candidate_state = pt_pattern_candidate`; this is a
delta-pattern candidate state, not the final Pt detection verdict.

Runtime observed: proxy scan pass ~31.3 seconds over the full RAW file
(2,726 MS1 scans); Pt pattern Discovery CLI pass over the resulting
37,320-row detected-features CSV took ~2.8 seconds after the matcher
anchor fix. This confirms the feature-level matcher is not the bottleneck;
the scan-level proxy remains the expensive diagnostic bridge.

## Trace Co-Elution Diagnostic

A follow-up chromatography-aware trace diagnostic was run against the first 50
Pt-only candidates using `tools/diagnostics/pt_pattern_trace_diagnosis.py`.
This tool extracts vendor XIC traces for base / `+1.0021` / `+2.0023` /
`+4.0052` around the base RT and writes diagnostic-only co-elution metrics. It
does not create candidates, write Matrix values, or change Discovery outputs.
The summary explicitly separates the scan-level seed (`scan_pattern_seed_status`)
from trace confirmation (`trace_support_status`) and writes
`combined_pt_pattern_status`; `pt_pattern_detected_diagnostic` means both
evidence layers support the candidate in this diagnostic output only.

Output directory:

- `output/pt_pattern_discovery_v1/fullscan_proxy_fixed/trace_diagnosis_limit50/`

Candidate-level trace support summary, 50-row sample after the Gaussian15
decision-basis v2 rerun:

- `supported`: 36
- `partial_support`: 12
- `review`: 1
- `incomplete_trace_signal`: 1

Combined diagnostic Pt status, 50-row sample:

- `pt_pattern_detected_diagnostic`: 36
- `pt_pattern_candidate_partial`: 12
- `pt_pattern_candidate_review`: 1
- `pt_pattern_candidate_incomplete`: 1

Position-level support summary, 50-row sample:

- `pt_plus_1`: 48 supported, 2 review
- `pt_plus_2`: 47 supported, 3 review
- `pt_plus_4`: 39 supported, 10 review, 1 missing signal

Interpretation: the scan-level proxy can find many same-scan Pt-like seeds, but
trace co-elution is the confirmation layer that separates diagnostic detected
patterns from partial/review candidates. The v2 rerun confirms that raw trace
similarity was too sensitive for the machine gate; Gaussian15 smoothing is the
current decision basis. The `+4` position remains the main weak point in this
sample slice. This supports the current architecture conclusion: scan-level
pattern finding should remain the seed layer, and product-grade Pt pattern
detection should require chromatographic co-elution evidence as well.

## Manual Review Packet

A bounded manual-review packet was generated from the candidate CSV and
trace-diagnosis outputs. This packet is the intended handoff for user review;
it is not an oracle until the user fills `human_review_decision` and the
reviewed rows are promoted by a separate oracle/promotion step.

Command:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_review_packet `
  --candidates-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\pt_candidates_anchor_fixed\pt_pattern_candidates.csv `
  --trace-summary-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\trace_diagnosis_limit50\pt_pattern_trace_summary.csv `
  --trace-metrics-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\trace_diagnosis_limit50\pt_pattern_trace_metrics.csv `
  --output-dir output\pt_pattern_productization_v1\manual_review_packet\pt_only_fullscan `
  --packet-label pt_pattern_productization_v1 `
  --source-label pt_only_fullscan `
  --max-per-status 5
```

Output directory, relative to repo root and git-ignored:

- `output/pt_pattern_productization_v1/manual_review_packet/pt_only_fullscan/`

Packet summary:

- `schema_version = pt_pattern_manual_review_packet.v1`
- `readiness_tier = diagnostic_only`
- `oracle_status = pending_human_review`
- Row count: 16
- Status buckets: 5 `pt_pattern_detected_diagnostic`, 5
  `pt_pattern_candidate_partial`, 5 `pt_pattern_candidate_review`, and 1
  `pt_pattern_candidate_incomplete`.

## Blind Overlay Review

The manual review packet was converted into a blind MS1/XIC overlay gallery.
The HTML shows normalized base / `+1.0021` / `+2.0023` / `+4.0052` traces for
each packet row. It intentionally hides diagnostic status buckets and Codex's
rule-based calls. Codex's calls are written only to the sealed CSV and should
not be opened until the user has recorded independent visual decisions.

Commands:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\pt_candidates_anchor_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_productization_v1\blind_overlay_trace_points\pt_only_fullscan `
  --limit 50 `
  --write-trace-points

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_blind_overlay `
  --review-packet-csv output\pt_pattern_productization_v1\manual_review_packet\pt_only_fullscan\pt_pattern_manual_review_packet.csv `
  --trace-points-csv output\pt_pattern_productization_v1\blind_overlay_trace_points\pt_only_fullscan\pt_pattern_trace_points.csv `
  --output-dir output\pt_pattern_productization_v1\blind_overlay\pt_only_fullscan `
  --title "Pt Pattern Blind Overlay - Pt Only Fullscan"
```

Output directories, relative to repo root and git-ignored:

- `output/pt_pattern_productization_v1/blind_overlay_trace_points/pt_only_fullscan/`
- `output/pt_pattern_productization_v1/blind_overlay/pt_only_fullscan/`

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

The initial Task 8 run used the older proxy defaults (`lookup_ppm = 50`,
`trace_quality = "REVIEW"`, `shape_score = 0.0`) and produced 78,976
pseudo-feature rows but 0 Pt candidates. That result is superseded by the
current run above because the proxy-to-matcher contract was internally
inconsistent: the proxy rows could not pass the local-support gate they were
intended to exercise.

**Result status: `diagnostic_only`.** This candidate set (in this case, an
8,722-row diagnostic review queue) is a first-look diagnostic result only. It
does not validate peak-detection accuracy, does not promote any oracle, and
does not change counted detection, Matrix authority, or production readiness.
It awaits manual user review of sampled candidate rows before any candidate
example from this or a future run can be accepted as an oracle.
