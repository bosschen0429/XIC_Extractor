# Local MS1 Feature Table Generator Design

Doc placement: formal_repo_doc
Doc kind: spec
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Absorb durable decisions into docs/product/discovery.md and the
DetectedFeature/local feature generator implementation docs, then retire this
transient spec through the docs cleanup workflow after implementation.

Status: design_approved
Validation status: design_only

## Purpose

Build a sample-local MS1 feature table generator for Delta Mass / Pt pattern
Discovery.

The generator converts one Thermo RAW file into a canonical
`detected_features.csv` feature source. It is a chromatographic feature
pre-detector, not a quantifier, Matrix writer, Backfill engine, or counted
detection authority.

Primary data flow:

```text
Thermo RAW MS1 centroid scans
-> in-memory MS1 scan index
-> scan-to-scan m/z trace linking
-> smoothed chromatographic peak detection
-> role-aware feature quality labeling
-> detected_features.csv + summary/diagnostics
-> Pt feature-level pattern matcher
```

The design removes the scan-level Pt proxy from the main product path. The
proxy can remain diagnostic-only, but Pt-only Discovery should use detected MS1
features rather than brute-force centroid scan hits.

## Architecture Preflight

Goal:
Create a local MS1 feature source for Delta Mass / Pt Discovery while keeping
screening evidence separate from final quantification and Matrix authority.

Existing owner/helper to reuse:
`xic_extractor.discovery.detected_features` owns the `DetectedFeature` input
contract. Existing MS1 scan-index mechanics can be used as implementation
reference, but Discovery package code must not depend on Alignment internals. If
package code needs reusable MS1 scan indexing, extract or wrap it behind a
neutral package helper instead of duplicating RAW scan access in a diagnostic
script.

New code location:
Package behavior should live under `xic_extractor.discovery` or a small shared
package module. CLI orchestration should live under `scripts/`. Diagnostic
rendering and reports can live under `tools/diagnostics/`, but they must not own
the reusable feature-detection logic.

Evidence provider role:
The generator emits Discovery feature-source evidence. Pt pattern matching and
Pt trace/co-elution evidence remain separate evidence providers. The generator
does not directly grant ProductWriter, Backfill, workbook, export, counted
detection, or Matrix authority.

Simplest product rule:
Detect sample-local chromatographic MS1 features from smoothed scan-to-scan m/z
traces. Base-grade features must be `OK`; support-only features may be
`WEAK_SUPPORT` if they pass basic physical gates.

Call-cost model:
Open each RAW once, build an in-memory MS1 scan index once, link centroid peaks
scan-to-scan, and avoid vendor XIC calls during table generation. Vendor XIC
extraction remains for Pt trace evidence and overlay review, not for the
generator's main pass.

Public contracts at risk:
`detected_features.csv`, CLI flags, output paths, feature IDs, `height`/`area`
semantics, `trace_quality` meanings, and Pt matcher role rules.

Validation gate:
Synthetic and contract tests first, then focused single-RAW diagnostic smoke on
the Fullscan file and Pt+CID-NL(dR) smoke on the DDMS2 file. These single-RAW
runs can prove mechanics, metrics, and reviewability only. Manual overlay/oracle
review plus a later broader validation gate are required before any
production-candidate claim.

Stop rule:
Do not promote generator output to final quantification, Matrix, Backfill, or
counted detection without an explicit activation contract and quantitative
validation.

## Scope

V1 scope:

- Single RAW input.
- One sample-local `detected_features.csv` output.
- No cross-sample alignment.
- No Backfill.
- No Matrix or workbook output.
- No isotope/deisotoping grouping.
- No final quantitative area.
- Positive mode only for the current Pt validation path.

Future scope:

- Batch wrapper over multiple RAW files.
- Optional MS1 index caching.
- User-configurable delta-mass presets beyond Pt.
- Possible activation path for quantitative integration, only after a separate
  activation contract.

## Pt Pattern Context

`delta_mass` is a family of MS1 pattern-based Discovery strategies. `pt` /
`Pt pattern` is the first concrete preset.

Pt pattern was discovered from observed sample behavior and is supported by Pt
isotope distribution. It is not a pure theoretical isotope-envelope detector.
The expected envelope is evidence, not a hard gate.

Observed/expected Pt positions:

```text
base / 0 position: lowest m/z anchor
+1: base + 1.0021
+2: base + 2.0023
+4: base + 4.0052
```

Approximate normalized Pt isotope-envelope rationale, using `+1` as 100:

```text
base ~= 97
+1   = 100
+2   ~= 75
+4   ~= 22
```

V1 must not assume base is the highest-intensity position. `+4` is expected to
be weak and can legitimately require support-grade rather than base-grade
quality. Ratios are review/scoring evidence only.

## Feature Linking

The generator should read MS1 centroid scans into an in-memory scan index, then
link peaks scan-to-scan.

Defaults:

```text
trace_builder_mode = mzmine_seeded
mass_detection_noise_level = 25000
min_trace_seed_intensity = 100000
min_consecutive_trace_intensity = 50000
feature_link_ppm = 20
max_missing_scans = 1
min_observed_scans = 3
```

Post-review amendment, 2026-07-02: the initial adjacent scan-linking design was
rejected by real Fullscan RAW performance. The local generator must not trace
all centroid points. It uses a MZmine-inspired seeded chromatogram builder:

1. Build a mass-detected point list from MS1 scans using
   `mass_detection_noise_level`.
2. Sort mass-detected points by decreasing intensity only to define
   non-overlapping m/z seed ranges. A point can create a seed range only when
   its intensity is `>= min_trace_seed_intensity`.
3. Assign all mass-detected points to the existing seed ranges by m/z.
4. Inside each seed range, build RT-local traces in scan order. Lower
   mass-detected bridge points can connect high-intensity seed points only when
   the scan-order segment obeys `max_missing_scans`; intensity order must not
   decide trace topology.
5. A trace must have at least `min_observed_scans` consecutive points above
   `min_consecutive_trace_intensity` before peak-region quality is evaluated.

The older bounded scan-linker remains a low-level diagnostic/test helper only.
It must not be the product-facing default for this RAW-backed local feature
source.

## Smoothing And Peak Boundaries

All V1 feature decisions use Gaussian15-smoothed trace intensity:

- apex;
- boundaries;
- split decisions;
- screening height;
- screening area;
- role-aware quality labels.

Raw trace values remain diagnostics/audit evidence only.

The main `area` value is a local linear baseline-corrected smoothed area. It is
screening evidence only.

Conservative split rule:

- split two peaks only when the valley between apices is `<= 50%` of the smaller
  apex height;
- every child peak must independently pass the same hard gates.

## Hard Gates And Quality Labels

Hard physical gates for any main-table feature:

- smoothed height `> 0`;
- smoothed baseline-corrected area `> 0`;
- observed scan count `>= 3`;
- duration between `0.03` and `1.5` minutes;
- apex is not at the region edge.

Quality labels:

```text
OK
WEAK_SUPPORT
FAIL
```

`OK` means the local feature source found a base-grade chromatographic feature.
It can seed a Pt base candidate or support a non-base position, but it is still
feature-source quality, not trace/co-elution authority.

`WEAK_SUPPORT` means near-pass support only. It passed the hard physical gates
but failed one or more stricter quality checks such as prominence, boundary
quality, gap fraction, or m/z stability. It can support a Pt non-base position
but cannot seed a Pt candidate as base.

`FAIL` rows do not enter `detected_features.csv`. They go to diagnostics with
reject reasons.

For `source = local_ms1_feature`, the main CSV allowed `trace_quality` values
are only:

```text
OK
WEAK_SUPPORT
```

`FAIL`, unknown labels, and misspelled labels must fail closed for
`local_ms1_feature` rows. Other existing feature sources can keep their current
adapter semantics, but local generator rows must not introduce open-ended
quality strings.

## Matcher Authority Mapping

Pt matcher role rule for `source = local_ms1_feature`:

- base / 0 position requires `trace_quality = OK`;
- non-base positions (`+1`, `+2`, `+4`) may use `OK` or `WEAK_SUPPORT`;
- `WEAK_SUPPORT` must not be counted as base-grade local support;
- `shape_score` must not upgrade `WEAK_SUPPORT`, `FAIL`, diagnostics-only rows,
  or unknown local labels into base eligibility;
- `FAIL` and diagnostics-only traces must not become Pt evidence.

Local generator rows alone must remain source-level screening evidence. A full
four-position local feature-source pattern can emit a Pt candidate, but until
the Pt trace/co-elution evidence provider evaluates it, the matcher must label
it as `feature_source_detected` or an equivalent non-trace-supported status. It
must not become `supported`, tier A, score-90, `detected`, counted detection,
Backfill authority, Matrix authority, or final quantification evidence solely
because the four local feature rows are `OK`.

Only `xic_extractor.discovery.pt_pattern_trace_evidence` or another explicitly
validated trace/co-elution evidence provider can upgrade a candidate from
feature-source evidence to detection-grade Pt support.

## Output Artifacts

### detected_features.csv

The main CSV keeps the existing public contract:

```text
sample_stem
feature_id
source_feature_row_id
mz
rt_apex_min
rt_start_min
rt_end_min
height
area
trace_quality
shape_score
source
```

Rules:

- `source = local_ms1_feature`.
- `trace_quality` is `OK` or `WEAK_SUPPORT`.
- `height` is Gaussian15-smoothed screening height.
- `area` is Gaussian15-smoothed, local baseline-corrected screening area.
- `mz` is the observed centroid m/z at the smoothed apex scan.
- `feature_id = <sample_stem>|local_ms1_feature|000001`.
- `source_feature_row_id = feature_id`.
- Deterministic sort order: `sample_stem`, `rt_apex_min`, `mz`, stable index.
- Cross-rerun feature ID stability is not guaranteed when parameters change.

The schema does not rename `height`/`area` to `screen_height`/`screen_area` in
V1. Those names would be clearer, but changing the public `DetectedFeature`
contract would turn this into a broader schema migration.

### local_ms1_feature_summary.json

Run-level metadata should include:

- `schema_version = local_ms1_feature_summary.v1`;
- RAW path and sample stem;
- RAW identity fields sufficient to reopen the same data;
- `extraction_backend = local_ms1_scan_index`;
- MS1 scan selection rule;
- generator version;
- `trace_builder_mode`;
- parameter values;
- parameter hash;
- smoothing kernel name;
- smoothing kernel scans;
- median MS1 scan interval;
- effective smoothing seconds;
- MS1 scan count;
- centroid peak count;
- candidate edge count;
- conflict component count;
- degraded assignment component count;
- total traces;
- `OK`, `WEAK_SUPPORT`, and `FAIL` counts;
- emitted `detected_features.csv` row count;
- diagnostics row count;
- assignment degradation summary;
- area and height semantics;
- quantification authority fields.

Required authority metadata:

```json
{
  "schema_version": "local_ms1_feature_summary.v1",
  "height_semantics": "screening_smoothed_height",
  "area_semantics": "screening_smoothed_baseline_corrected_area",
  "area_contract": "screening_only",
  "quant_ready": false
}
```

Required count invariants:

```text
detected_features_rows == ok_feature_count + weak_support_feature_count
total_peak_regions == ok_feature_count + weak_support_feature_count + fail_count
```

If any invariant cannot be computed, the run is diagnostic-incomplete and must
not be used as a Pt matcher input without an explicit override.

Builder-mode metric semantics:

- In `mzmine_seeded` mode, `candidate_edge_count` is the count of
  mass-detected points above `mass_detection_noise_level`.
- In `mzmine_seeded` mode, `conflict_component_count` is the count of m/z seed
  ranges.
- In `mzmine_seeded` mode, `degraded_assignment_component_count` is `0` because
  bipartite assignment is not used; the degraded-assignment smoke budget is
  relevant only to `scan_link` mode.
- In `scan_link` mode, those same fields retain the bounded bipartite-linker
  meanings: candidate edges, conflict components, and degraded large/dense
  components.

### local_ms1_feature_diagnostics.csv

Diagnostics should carry rejected and degraded trace/peak evidence:

- internal trace or peak id;
- RT and m/z summaries;
- gap fraction;
- m/z ppm standard deviation or equivalent stability metric;
- prominence;
- boundary quality;
- baseline fields;
- assignment mode;
- component size;
- component edge density;
- assignment degradation flag;
- split peak flag;
- overlap suspicion flag;
- reject or degrade reason.

Diagnostics are for review and tuning. They must not be silently consumed as
Matrix or counted-detection authority.

## CLI

V1 should expose a separate feature-source CLI:

```text
scripts/build_local_ms1_feature_table.py
python -m scripts.build_local_ms1_feature_table
```

It should not be hidden inside `scripts/run_pt_pattern_discovery.py`.

Expected workflow:

```text
build_local_ms1_feature_table --raw <file.raw> --output-dir <dir>
run_pt_pattern_discovery --detected-features-csv <dir>/detected_features.csv
```

Pt+CID-NL(dR) composite strategy can seed from CID-NL base candidates and then
look up local MS1 feature support. It should not bypass MS1 feature quality
diagnostics.

## Validation Plan

Synthetic tests:

- scan-to-scan linking;
- bounded hybrid assignment;
- missing-scan tolerance;
- conservative splitting;
- baseline-corrected area;
- `OK` / `WEAK_SUPPORT` / `FAIL` routing;
- role-aware Pt matcher rules.

Contract tests:

- `detected_features.csv` remains compatible with `DetectedFeature`;
- `height`/`area` are documented as screening-only;
- `WEAK_SUPPORT` cannot seed a base position;
- diagnostics-only rows cannot affect Pt candidate status;
- summary JSON records smoothing and authority metadata.

Real RAW smoke tests:

```text
$env:XIC_20251210_DELTA_MASS_RAW_DIR\CT-DNA+100uMCP_Fullscan.raw
$env:XIC_20251210_DELTA_MASS_RAW_DIR\DNA+100uMCP_DDMS2_new.raw
```

Fullscan smoke goals:

- produce a canonical `detected_features.csv`;
- write wall time, MS1 scan count, centroid peak count, trace count, feature
  count, candidate edge count, conflict component count, degraded component
  count, peak/sec or scan/sec, and memory metric when available;
- meet the initial single-RAW diagnostic budget below, or mark the result
  `inconclusive_performance` and stop before threshold tuning;
- Pt-only Discovery no longer uses scan-level proxy as the main path;
- candidate output can be reduced into a bounded review packet without changing
  candidate identity;
- weak `+4` support is not prematurely discarded;
- sampled overlays show plausible co-elution.

DDMS2 composite smoke goals:

- CID-NL(dR) can seed base candidates;
- Pt support lookup uses local MS1 feature evidence;
- composite strategy remains intersection-only for V1.

Initial single-RAW diagnostic budget:

```text
feature_table_wall_time_sec <= 900
pt_candidate_rows <= 10000
bounded_review_packet_rows <= 64
degraded_assignment_component_fraction <= 0.05
```

These numbers are smoke budgets, not final performance requirements. Exceeding
one does not prove the method is scientifically wrong; it means the V1 design is
not reviewable enough to proceed to threshold tuning without redesign or an
explicit budget change.

## Known V1 Limits And Future Scope

- Seed ranges are non-overlapping. Close-in-m/z features inside one
  `feature_link_ppm` window can be clipped into the earlier seed range or
  dropped if no range width remains. This is acceptable for V1 Pt pattern
  Discovery because Pt positions are separated by about 1 Da, but it is a
  future concern for denser delta-mass presets.
- Within one seed range and one scan, only the highest-intensity centroid point
  is retained for the trace. This matches the seeded chromatogram simplification
  but can hide a weaker co-eluting feature in the same m/z window.
- `mass_detection_noise_level` is an absolute intensity threshold. The V1
  default is `25000` to match the lab's routine screening threshold. Broader
  deployment should add adaptive noise estimation or per-file calibration.
- `scan_link` mode remains a low-level diagnostic/test helper. Its optimal
  component assignment is bounded by caps and should be replaced with a proper
  assignment algorithm before being promoted beyond diagnostic use.

Readiness labels:

- Before synthetic, contract, and focused RAW smoke: `diagnostic_only`.
- Focused single-RAW Fullscan/DDMS2 smoke remains `diagnostic_only`, even when
  the command completes and review packets are produced.
- A manual overlay/oracle packet from the focused samples can support threshold
  calibration, but it does not by itself make the detector
  `production_candidate`.
- `production_candidate` requires a later activation plan with broader
  validation, including explicit 8RAW framing or another approved benchmark
  scope.
- `production_ready` requires a later activation plan and broader validation.

Gate table:

| Gate | Input | Required artifacts | Pass / fail rule | Allowed label |
| --- | --- | --- | --- | --- |
| Synthetic mechanics | Synthetic scan traces | Unit-test fixtures | Linking, split, area, quality labels, and role-aware matcher rules pass, including negative tests | `synthetic_only` |
| Contract/schema | Generated local feature CSV/JSON fixtures | `detected_features.csv`, `local_ms1_feature_summary.json`, diagnostics CSV | Local `trace_quality` fails closed; summary invariants hold; diagnostics-only rows cannot alter candidate status | `focused_tests` |
| Fullscan diagnostic smoke | `CT-DNA+100uMCP_Fullscan.raw` | feature table, summary, diagnostics, Pt candidate/review output | Run emits required metrics, uses no scan proxy fallback, and stays within the initial single-RAW diagnostic budget; zero candidates or budget overflow makes the science result `inconclusive`, not production evidence | `diagnostic_only` |
| DDMS2 composite diagnostic smoke | `DNA+100uMCP_DDMS2_new.raw` | CID-NL-seeded Pt support output plus summary/diagnostics | CID-NL(dR) base seeding is preserved; Pt support lookup uses local MS1 features; no union/OR behavior appears | `diagnostic_only` |
| `+4` / `WEAK_SUPPORT` review | Bounded review packet from focused samples | blind overlay packet and manual labels | Packet includes retained weak `+4` examples and rejected near-pass examples; labels calibrate thresholds but do not activate Matrix/Backfill | `manual_eic_ms2_review` for reviewed rows only |
| Product activation follow-up | Approved broader validation scope | activation plan, expected-diff contract, focused outputs | Broader gate passes and owner docs absorb durable rules | at most `production_candidate` until final activation |

## Non-Goals

- Do not write Matrix.
- Do not write Backfill.
- Do not create counted detections.
- Do not claim final quantitative area.
- Do not silently replace vendor XIC integration.
- Do not make Pt isotope ratios hard gates.
- Do not support z=2 in V1.
- Do not support union/OR composite strategy in V1.
- Do not make scan-level proxy a product detector.

## Open Implementation Decisions

These are deliberately left to the implementation plan:

- exact thresholds for prominence, gap fraction, boundary quality, and m/z
  stability;
- exact component size and edge-density thresholds for optimal assignment;
- final module names;
- whether the existing alignment MS1 index helper is moved or wrapped;
- any hard wall-clock target beyond the required diagnostic runtime metrics.

None of these should change the authority boundary: this generator is a
screening feature source only.
