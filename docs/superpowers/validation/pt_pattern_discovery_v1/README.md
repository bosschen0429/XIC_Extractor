# Pt pattern Discovery validation v1

Doc placement: repo_support_doc
Doc kind: validation_artifact
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Set `Doc lifecycle: archived` once Task 8 records the
real-sample run results in this same file (candidate count, output paths,
review status). Keep this file (active or archived) until Discovery product
docs or a newer validation artifact covers the same Pt pattern real-sample
diagnostic lane.

Status: not yet run. This is a stub created by Task 7 (Detected-Feature
Source Gate For Real Samples) of
`docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`.
No real-sample run has happened yet -- the "Results" section below is a
placeholder only. When it is filled in (Task 8), the run will be
`diagnostic_only`, never product validation or Matrix authority.

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

Not yet run. Task 8 (Diagnostic Real-Sample Candidate Generation) will run
the proxy and/or the Pt pattern CLI against the user-provided Fullscan RAW
sample and append results here: candidate count, output paths, and review
status. Until that section is added, treat this validation lane as
`status: not yet run`.
