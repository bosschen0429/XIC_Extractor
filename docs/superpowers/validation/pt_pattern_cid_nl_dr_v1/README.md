# Pt pattern + CID-NL(dR) composite Discovery validation v1

Doc placement: repo_support_doc
Doc kind: validation_artifact
Doc lifecycle: archived
Repo owner: docs/product/discovery.md
Doc exit rule: This file is archived as a point-in-time diagnostic snapshot
(Task 9 real-sample composite run recorded below: candidate counts, output
paths, review status, and a real bug found in the composite CID-NL loader).
Keep this file (archived) until Discovery product docs or a newer
validation artifact covers the same Pt pattern + CID-NL(dR) composite
real-sample diagnostic lane, or until the bug documented below is fixed and
re-validated.

Status: Task 9 (Composite DDMS2 Diagnostic Gate) of
`docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md`
has run the neutral-loss discovery CLI and the scan-level Pt pattern proxy
end-to-end against the real-sample DDMS2 RAW file, then attempted the
composite Pt pattern + CID-NL(dR) Discovery CLI. The composite step
**crashed with a real, confirmed bug** in already-approved Task 5 code (see
"Results" below) rather than producing candidate output. This is
`diagnostic_only`, never product validation or Matrix authority, and no
code was modified to work around the crash.

## Purpose

Validate that the Pt pattern + CID-NL(dR) composite Discovery lane
(`pt_pattern_cid_nl_dr` preset, implemented in Task 5) can run end-to-end
against a real user-provided DDMS2 RAW sample, using two real detection
outputs joined together:

1. Neutral-loss discovery candidates (`discovery_candidates.csv`), produced
   by the existing, already-mature `scripts.run_discovery` CLI (unrelated
   to Tasks 1-9, pre-existing production code).
2. Pt pattern detected features (`detected_features.csv`), produced by the
   Task 7 scan-level proxy tool (`tools.diagnostics.pt_pattern_scan_proxy`),
   the same fallback used in Task 8, since no accepted real feature-detector
   source exists yet.

This validation lane does not validate peak-detection accuracy, does not
promote any oracle, and does not change counted detection, Matrix
authority, or production readiness.

## Detected-feature source

Same as Task 8's `pt_pattern_discovery_v1` lane: no accepted detected-feature
CSV source exists in this repo yet, so this lane uses the fallback
scan-level proxy (`raw_scan_proxy_v1`, `validation_status = diagnostic_only`).
See `docs/superpowers/validation/pt_pattern_discovery_v1/README.md` for full
detail on the proxy's design and limitations (looser 50 ppm scan-level
lookup window than the matcher's real 20 ppm / 30 sec Global Constraint
gates; placeholder `trace_quality = "REVIEW"` / `shape_score = 0.0` on every
row).

## Results

Sample used: the Dec 2025 CSMU delta-mass DDMS2 sample (a real
user-provided DDMS2 RAW file; local absolute path kept only in the
git-ignored `.env.xic-local` local config, never in this doc).

Commands run (env vars loaded from `.env.xic-local` the same way as Task 8):

```powershell
Get-Content .env.xic-local | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
  $parts = $_ -split '=', 2
  Set-Item "Env:$($parts[0].Trim())" $parts[1].Trim()
}

# Step 1: neutral-loss discovery (existing production CLI)
.venv\Scripts\python.exe -m scripts.run_discovery `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\cid_nl

# Step 2: Pt pattern scan-level proxy (Task 7 tool), same DDMS2 RAW file
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_scan_proxy `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1

# Step 3: composite Pt pattern + CID-NL(dR) Discovery (Task 5 CLI)
.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_cid_nl_dr_v1\detected_features.csv `
  --cid-nl-candidates-csv output\pt_pattern_cid_nl_dr_v1\cid_nl\discovery_candidates.csv `
  --output-dir output\pt_pattern_cid_nl_dr_v1\pt_intersection `
  --preset pt_pattern_cid_nl_dr
```

Output directories (relative to repo root, git-ignored, not committed):

- `output/pt_pattern_cid_nl_dr_v1/cid_nl/` -- neutral-loss discovery
  output (`discovery_candidates.csv`, `discovery_review.csv`).
- `output/pt_pattern_cid_nl_dr_v1/` -- proxy `detected_features.csv` +
  `proxy_provenance.json`.
- `output/pt_pattern_cid_nl_dr_v1/pt_intersection/` -- intended composite
  Pt pattern Discovery CLI output; **empty**, because Step 3 crashed
  before writing any output file.

### Neutral-loss discovery candidate count: 367

`discovery_candidates.csv` has 367 data rows. Header confirmed to include
all three columns the base join predicate needs: `ms1_feature_row_id`,
`neutral_loss_tag`, `discovery_candidate_state` (plus many other provenance
columns). `discovery_candidate_state` breakdown: `ms1_feature_nl_rescued`
= 159, `review_only_orphan_nl` = 140, `ms1_feature_nl_supported` = 68. All
367 rows have `neutral_loss_tag = DNA_dR` (CLI default, not overridden).

### Proxy detected-feature count: 180,592 rows

Compare to Task 8's 78,976 rows for the smaller Fullscan sample --
consistent with the DDMS2 sample being larger (13,577 MS1 scans vs. 2,726).
`proxy_provenance.json` confirms `diagnostic_only: true`,
`not_a_product_feature_detector: true`,
`detected_feature_source: "raw_scan_proxy_v1"`.

### Pt composite candidate count: BLOCKED -- 0 output rows, run crashed

Step 3 (`scripts.run_pt_pattern_discovery --preset pt_pattern_cid_nl_dr`)
raised an unhandled `ValueError` partway through and exited with code 1.
**No `pt_pattern_candidates.csv`, `pt_pattern_review.csv`, or
`pt_pattern_sidecar.json` was produced.** This is not the same as the
valid "zero candidates" outcome Task 8 recorded for the Fullscan sample --
it is a hard failure with no output at all, so the required check "every
composite row has `cid_nl_base_status == matched`" **cannot be confirmed
or denied** for this run.

**Bug found (real, in already-approved Task 5 production code -- not
modified by this task):**

`_load_cid_nl_candidate_rows()` /
`_parse_cid_nl_row()` in `xic_extractor/discovery/delta_mass_pipeline.py`
unconditionally requires every row of the input `discovery_candidates.csv`
to have a non-empty `ms1_feature_row_id`, regardless of that row's own
`discovery_candidate_state`. But rows with
`discovery_candidate_state = "review_only_orphan_nl"` are, by design
(`xic_extractor.discovery.models.MS1_FEATURE_BACKED_STATES` deliberately
excludes this state), never backed by an MS1 feature and therefore
legitimately have an empty `ms1_feature_row_id`. The join predicate itself
(this plan's own composite join spec) only ever considers rows where
`discovery_candidate_state in {"ms1_feature_nl_supported",
"ms1_feature_nl_rescued"}` -- `review_only_orphan_nl` rows were never meant
to participate in the join at all. On this real sample, 140 of 367 rows
(about 38%) are `review_only_orphan_nl`, and the loader crashed on the
first one it encountered (row 227), before any state-based filtering could
exclude it.

No existing unit test exercises a CID-NL CSV containing a
`review_only_orphan_nl` row (Task 5's approved tests only ever construct
synthetic CID-NL CSVs where every row is `ms1_feature_nl_supported` with a
populated `ms1_feature_row_id`), so this gap was not caught before this
real-sample run -- this is the first time `scripts.run_discovery`'s actual
production output (which routinely contains `review_only_orphan_nl` rows)
was fed into the composite Pt pattern path.

Per this task's guardrails, this bug was reported, not patched: no
production code, test file, or CLI default was modified or loosened to
work around it, and no CSV pre-filtering workaround was applied either
(that would itself be papering over the bug rather than fixing it).

**Runtime observed:** neutral-loss discovery ~20-25 seconds; proxy scan
pass completed within the allotted window (13,577 MS1 scans, 180,592
pseudo-feature rows); the composite Pt pattern Discovery CLI ran for
~17.8 seconds before crashing during CID-NL CSV loading -- markedly faster
than Task 8's ~19-minute `pt_pattern`-only run over a comparable
detected-features row count, consistent with the just-landed bisect-bound
m/z window lookup perf fix. Because the crash occurred during CSV loading,
before the matcher's output ever reached the CID-NL join step
(`_apply_cid_nl_composite_join`), **no timing observation was possible for
that join step** in this run -- the brief's guardrail concern about that
step's linear-scan cost could not be evaluated here.

**Result status: `diagnostic_only`.** This run is a first-look diagnostic
result only, and it surfaced a real, actionable bug rather than a
candidate set. It does not validate peak-detection accuracy, does not
promote any oracle, and does not change counted detection, Matrix
authority, or production readiness. It awaits manual user review -- both
of the bug found and, after a fix, of any subsequent composite candidate
output -- before any candidate example from this or a future run can be
accepted as an oracle.
