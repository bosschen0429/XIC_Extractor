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
against the real-sample Fullscan RAW file. See "Results" below. The run is
`diagnostic_only`, never product validation or Matrix authority, and awaits
manual user review before any oracle promotion.

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
  own internal m/z lookup window is deliberately looser than the Pt pattern
  matcher's real 20 ppm / 30 sec Global Constraint gates, which the matcher
  re-applies to every candidate regardless of proxy output.
- `trace_quality` / `shape_score` are fixed, conservative placeholders
  (`"REVIEW"` / `0.0`) that intentionally do not pass the matcher's
  local-signal-support gate on their own, so a human must confirm signal
  quality before any candidate produced from this proxy is trusted.

If an accepted detected-feature CSV becomes available in a later task (a
real MS1 peak detector emitting this exact schema), that source should be
used directly instead and this section updated to name it, with
`detected_feature_source` changed accordingly.

## Results

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
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy

.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_discovery_v1\fullscan_proxy\detected_features.csv `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy\pt_candidates `
  --preset pt_pattern
```

Output directories (relative to repo root, git-ignored, not committed):

- `output/pt_pattern_discovery_v1/fullscan_proxy/` -- proxy
  `detected_features.csv` (78,976 pseudo-feature rows: 19,744 scan-level
  groups x 4 Pt positions each) + `proxy_provenance.json`
  (`ms1_scan_count: 2726`, `scan_hit_count: 387924`).
- `output/pt_pattern_discovery_v1/fullscan_proxy/pt_candidates/` --
  `pt_pattern_candidates.csv`, `pt_pattern_review.csv`,
  `pt_pattern_sidecar.json` (Pt pattern Discovery CLI output,
  `--preset pt_pattern`).

Candidate count: **0** (header-only `pt_pattern_candidates.csv` and
`pt_pattern_review.csv`; `pt_pattern_sidecar.json` summary reports
`candidate_count: 0`, `pt_only_candidate_count: 0`,
`cid_nl_supported_candidate_count: 0`, empty `candidates` array).

Zero candidates is a valid, expected diagnostic outcome here, not a failure:
the proxy's placeholder `trace_quality = "REVIEW"` / `shape_score = 0.0` on
every row are deliberately below the real Pt matcher's local-signal-support
gate, and the matcher re-applies its real 20 ppm / 30 sec Global Constraint
gates on top of the proxy's looser 50 ppm scan-level lookup window. No
tolerance, gate, or CLI default was loosened to try to manufacture a
nonzero result.

Runtime observed: proxy scan pass ~34 seconds over the full RAW file (2,726
MS1 scans); Pt pattern Discovery CLI pass over the resulting 78,976-row
detected-features CSV took approximately 19 minutes wall-clock -- notably
slower than its typical usage against much smaller, real feature-detector
outputs, since a scan-level proxy emits far more candidate rows per group
than a true chromatographic peak detector would. This is a diagnostic-lane
observation about a first real-sample run, not a change to any product
code or gate.

**Result status: `diagnostic_only`.** This candidate set (in this case, an
empty set) is a first-look diagnostic result only. It does not validate
peak-detection accuracy, does not promote any oracle, and does not change
counted detection, Matrix authority, or production readiness. It awaits
manual user review of the underlying proxy features and gate behavior
before any candidate example from this or a future run can be accepted as
an oracle.
