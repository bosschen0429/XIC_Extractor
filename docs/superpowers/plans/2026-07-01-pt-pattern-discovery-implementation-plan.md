# Pt Pattern Discovery Implementation Plan

Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/product/discovery.md
Doc exit rule: Retire after the Pt pattern implementation ships, stable claims
  are absorbed into product docs, and branch closeout records validation status.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

This required sub-skill line is workflow guidance from the planning skill, not a
product contract, runtime gate, or CI requirement. If the named workflow skill is
unavailable in a future session, continue with this plan plus repo `AGENTS.md`
and record the bypass reason in the branch handoff.

**Goal:** Implement the first Pt pattern Discovery provider as
`delta_mass(pattern_id=pt)`, producing candidate/review artifacts from
sample-local detected MS1 features without giving Pt sidecars direct Matrix
write authority.

**Architecture:** Add a Delta Mass discovery lane beside the existing
neutral-loss lane. Pt pattern matching consumes typed detected features and, for
the composite mode, existing CID-NL candidate evidence on the base feature. It
does not recompute CID-NL, does not scan RAW as the product discovery primitive,
and does not mutate Alignment, Backfill, ProductWriter, workbook Matrix, or
counted-detection semantics. The first public execution surface is a focused Pt
pattern CLI that writes CSV/JSON review artifacts; existing `xic-discovery-cli`
keeps its neutral-loss behavior.

**Tech Stack:** Python dataclasses and stdlib `csv`/`json`, existing
`xic_extractor.tabular_io`, existing preset loader/application stack, pytest,
ruff, mypy, uv, and the repo-local RAW runner rules in
`docs/agent/parameter-settings.md` for any later RAW-backed validation.

## Global Constraints

- First version supports positive mode and z=1 only.
- Built-in pattern is exactly `pt` with positions:
  `base=0`, `pt_plus_1=1.0021`, `pt_plus_2=2.0023`,
  `pt_plus_4=4.0052`.
- `base` is the lowest m/z anchor. Only `base + delta` is valid.
- Nonzero position ppm uses expected m/z as denominator:

```text
expected_mz = base_mz + expected_delta
mz_error_ppm = (observed_mz - expected_mz) / expected_mz * 1e6
```

- Hard m/z gate is `abs(mz_error_ppm) <= 20` for every nonzero Pt position.
- RT hard ceiling is `<= 30 sec` across all positions. Tighter RT is scored as
  better, but the first hard fail threshold is 30 sec.
- Discovery seed requires a sample-local full Pt pattern. Cross-sample behavior
  can support review confidence only after at least one sample-local seed
  exists.
- Local signal support is required for a detected Pt seed. Accept either
  `trace_quality in {"GOOD", "OK", "PASS"}` or `shape_score >= 0.80` on the
  base plus at least two non-base positions.
- Intensity and area ratios are evidence fields, not hard gates.
- Ratio basis is `both`: write height ratios and area ratios when available.
- `pt_pattern + cid_nl(dR)` is an intersection strategy: the Pt base feature
  must match an existing CID-NL(dR) candidate in the same sample.
- No OR/union strategy, z=2, negative mode, neutral-mass inference, adduct
  inference, Matrix writer authority, workbook Matrix change, or Backfill as
  counted detection in this implementation.
- Validation status after implementation is at most `diagnostic_only` until the
  user manually reviews candidate output and promotes a manual oracle.

## Architecture Preflight

Goal:

- Build an implementation-ready first version of Pt pattern Discovery from the
  refined spec, not the historical Delta Mass shadow-only plan.

Existing owner/helper to reuse:

- Reuse `xic_extractor.discovery` for Discovery-domain models and pipelines.
- Reuse `xic_extractor.presets` for method/preset selection.
- Reuse existing CID-NL candidate CSV records for the composite base join.
- Reuse `xic_extractor.tabular_io` for shared delimited file mechanics.
- If formula-escape behavior is needed outside one writer, move a small
  `escape_excel_formula` helper to `xic_extractor.tabular_io` instead of
  importing from `xic_extractor.alignment`.

New code location:

- `xic_extractor/discovery/delta_mass_patterns.py`
- `xic_extractor/discovery/detected_features.py`
- `xic_extractor/discovery/delta_mass_csv.py`
- `xic_extractor/discovery/delta_mass_pipeline.py`
- `scripts/run_pt_pattern_discovery.py`
- Tests under `tests/test_delta_mass_*.py`,
  `tests/test_pt_pattern_*.py`, and preset/CLI tests.

Evidence provider role:

- Pt pattern is a Discovery evidence provider. It creates candidate families and
  review evidence. It does not directly authorize final quant values.
- Composite Pt-plus-CID-NL consumes existing neutral-loss evidence on the base
  only. It does not require `+1`, `+2`, or `+4` positions to show CID-NL.

Simplest product rule:

- Full sample-local Pt pattern plus local signal support creates a Pt candidate.
- Composite method additionally requires CID-NL(dR) evidence on base.
- Quantification identity is base-centered, but this PR does not write Matrix.

Call-cost model:

- Product matcher operates on detected feature rows grouped by sample and sorted
  by m/z.
- For each base candidate, lookup each expected position in an m/z-bounded
  window, then filter by RT. Do not do unbounded all-pairs matching.
- Composite `pt_pattern + cid_nl(dR)` is seed-first: load eligible CID-NL base
  rows, identify matching detected-feature base anchors, and run Pt matching
  only from those base feature IDs. Do not run global Pt matching first and
  intersect afterward.
- Backfill and Alignment are not modified in this first PR.

Public contracts at risk:

- New preset names and TOML tag shape.
- New Pt detected-feature CSV input schema.
- New Pt candidate/review CSV and sidecar JSON schema.
- New CLI entry point and output filenames.
- Existing neutral-loss discovery CSV, workbook, Matrix, and counted-detection
  contracts must remain unchanged.

Validation gate:

- No-RAW unit and contract tests first.
- CLI parse/write smoke with synthetic detected-feature CSV.
- Preset load/apply tests for `pt_pattern` and `pt_pattern_cid_nl_dr`.
- Existing neutral-loss discovery tests must still pass.
- Real sample candidate generation is diagnostic-only until manual review
  promotes a regression oracle.

Stop rule:

- Stop before coding or merge if the implementation requires reinterpreting
  Backfill as detection, lets a Pt sidecar write Matrix values, changes existing
  neutral-loss outputs, infers chemistry beyond the configured pattern, or needs
  OR/union behavior in the first version.

## Existing File Structure

Current Discovery and preset files that the implementer must inspect before
editing:

```text
xic_extractor/discovery/
  __init__.py
  csv_writer.py
  evidence_config.py
  evidence_score.py
  grouping.py
  models.py
  ms1_backfill.py
  ms2_seeds.py
  peak_anchor.py
  pipeline.py
  priority.py
  tag_profiles.py
xic_extractor/presets/
  __init__.py
  apply.py
  loader.py
  models.py
  data/dna_dr.toml
  data/dna_dr_product_ready.toml
scripts/run_discovery.py
tests/test_discovery_csv.py
tests/test_discovery_pipeline.py
tests/test_discovery_review_csv.py
tests/test_presets_apply.py
tests/test_presets_loader.py
tests/test_run_discovery.py
```

Important current behavior:

- `DiscoverySettings` currently requires at least one neutral-loss profile.
- `PresetStrategy` already names `delta_mass`, but
  `SUPPORTED_STRATEGIES = {"neutral_loss"}` rejects it.
- `apply_to_discovery()` currently maps every preset tag to
  `NeutralLossProfile`; Pt pattern must not be forced through that path.
- Existing `--feature-list` in `scripts/run_discovery.py` is an FH tag profile
  list, not a detected-feature table. Do not reuse that flag for Pt features.

## Target File Structure

Add these implementation files:

```text
xic_extractor/discovery/detected_features.py
xic_extractor/discovery/delta_mass_patterns.py
xic_extractor/discovery/delta_mass_csv.py
xic_extractor/discovery/delta_mass_pipeline.py
xic_extractor/presets/data/pt_pattern.toml
xic_extractor/presets/data/pt_pattern_cid_nl_dr.toml
scripts/run_pt_pattern_discovery.py
tests/test_delta_mass_patterns.py
tests/test_delta_mass_csv.py
tests/test_delta_mass_pipeline.py
tests/test_pt_pattern_presets.py
tests/test_run_pt_pattern_discovery.py
tests/test_pt_pattern_boundaries.py
```

Modify these existing files:

```text
xic_extractor/discovery/models.py
xic_extractor/discovery/csv_writer.py
xic_extractor/presets/__init__.py
xic_extractor/presets/apply.py
xic_extractor/presets/loader.py
xic_extractor/presets/models.py
pyproject.toml
```

Do not modify these in the first implementation unless a test proves a
read-only compatibility fix is needed:

```text
xic_extractor/alignment/
xic_extractor/output/
scripts/csv_to_excel.py
scripts/run_alignment.py
```

## Public Contract Details

### Detected Features CSV v1

New input file for Pt matching. Required header order:

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

- `sample_stem`, `feature_id`, `mz`, `rt_apex_min`, `height`,
  `trace_quality`, and `source` are required non-empty values.
- `source_feature_row_id`, `rt_start_min`, `rt_end_min`, `area`, and
  `shape_score` may be blank.
- `feature_id` is the detected-feature source identity. When the detected
  feature source can preserve the existing CID-NL `ms1_feature_row_id`, it must
  also write that value to `source_feature_row_id` so composite joins can use a
  same-feature identity match before falling back to m/z/RT.
- `mz`, `rt_apex_min`, `height`, and nonblank numeric optional fields must be
  finite.
- `height` must be `> 0`.
- Rows with invalid required fields fail the run. They are not silently skipped.
- This CSV is a detected-feature input, not an FH tag setting file.

### Pt Candidate CSV v1

Output path: `pt_pattern_candidates.csv`.

Required header order:

```text
schema_version
sample_stem
method_label
pattern_id
candidate_id
candidate_state
review_priority
evidence_tier
evidence_score
base_feature_id
base_mz
base_rt_apex_min
base_height
base_area
position_count
required_position_count
rt_span_sec
pattern_rt_status
local_signal_support_status
cross_sample_support_status
ratio_basis
cid_nl_base_status
cid_nl_base_candidate_id
cid_nl_base_join_mode
reason
pt_plus_1_feature_id
pt_plus_1_mz
pt_plus_1_expected_mz
pt_plus_1_mz_error_ppm
pt_plus_1_delta_error_da
pt_plus_1_rt_delta_sec
pt_plus_1_height_ratio_to_base
pt_plus_1_area_ratio_to_base
pt_plus_2_feature_id
pt_plus_2_mz
pt_plus_2_expected_mz
pt_plus_2_mz_error_ppm
pt_plus_2_delta_error_da
pt_plus_2_rt_delta_sec
pt_plus_2_height_ratio_to_base
pt_plus_2_area_ratio_to_base
pt_plus_4_feature_id
pt_plus_4_mz
pt_plus_4_expected_mz
pt_plus_4_mz_error_ppm
pt_plus_4_delta_error_da
pt_plus_4_rt_delta_sec
pt_plus_4_height_ratio_to_base
pt_plus_4_area_ratio_to_base
```

Field values:

- `schema_version = pt_pattern_candidates.v1`
- `method_label in {"pt_pattern", "pt_pattern+cid_nl_dr"}`
- `pattern_id = pt`
- `candidate_state in {"pt_pattern_candidate", "pt_pattern_cid_nl_supported"}`
- `pt_pattern_candidate` means the delta-pattern candidate row has passed the
  matcher contract. It is not the final Pt detection verdict when the source is
  scan-level; diagnostic detection-grade status lives in
  `pt_pattern_trace_summary.csv` as `combined_pt_pattern_status`.
- `required_position_count = 4`
- `position_count = 4` for every first-version output row
- `pattern_rt_status = within_30s`
- `local_signal_support_status = supported`
- `cross_sample_support_status = not_evaluated`
- `ratio_basis = both`
- `cid_nl_base_status`:
  - `not_required` for Pt-only
  - `matched` for composite output rows
- `cid_nl_base_join_mode`:
  - `not_required` for Pt-only
  - `same_feature_id` when `source_feature_row_id` or `feature_id` matches the
    CID-NL `ms1_feature_row_id`
  - `mz_rt_fallback` only when same-feature identity is unavailable and the
    fallback m/z/RT predicate passes
- `review_priority in {"HIGH", "MEDIUM", "LOW"}`

### Pt Review CSV v1

Output path: `pt_pattern_review.csv`.

Required header order:

```text
review_priority
evidence_tier
evidence_score
sample_stem
candidate_id
method_label
base_mz
base_rt_apex_min
rt_span_sec
position_count
cid_nl_base_status
cid_nl_base_join_mode
reason
review_note
```

The review CSV is a human queue. It must use the same candidate ordering as
`pt_pattern_candidates.csv`.

### Pt Discovery Evidence CSV v1

Output path: `pt_pattern_evidence.csv`.

Required semantics:

- `schema_version = pt_pattern_discovery_evidence.v1`
- one row per `pt_pattern_candidates.csv` row
- feature-level full-pattern candidates write
  `scan_pattern_seed_status = full_pattern_seeded`
- when no trace summary is provided, trace rule fields write
  `not_evaluated` rather than `detected` or `non_detected`
- when a trace summary row is provided for the same `candidate_id`, the shared
  Pt evidence rule writes `trace_rule_call in {"accept", "reject"}` and
  `trace_detection_status in {"detected", "non_detected"}`
- every row writes `matrix_write_authority = FALSE`,
  `counted_detection_authority = FALSE`, and
  `export_activation_authority = FALSE`

This CSV is an evidence handoff surface. It does not change Matrix, Backfill,
counted detection, workbook, selected area, or export behavior.

### Pt Sidecar JSON v2

Output path: `pt_pattern_sidecar.json`.

Required top-level shape:

```json
{
  "schema_version": "pt_pattern_sidecar.v2",
  "method_label": "<selected method label>",
  "pattern": {
    "pattern_id": "pt",
    "charge": 1,
    "polarity": "positive",
    "positions": [
      {"label": "base", "delta_da": 0.0},
      {"label": "pt_plus_1", "delta_da": 1.0021},
      {"label": "pt_plus_2", "delta_da": 2.0023},
      {"label": "pt_plus_4", "delta_da": 4.0052}
    ],
    "mz_tolerance_ppm": 20.0,
    "rt_tolerance_sec": 30.0
  },
  "inputs": {
    "detected_features_csv": "detected_features.csv",
    "cid_nl_candidates_csv": null,
    "trace_summary_csv": null
  },
  "summary": {
    "candidate_count": 0,
    "pt_only_candidate_count": 0,
    "cid_nl_supported_candidate_count": 0,
    "evidence_row_count": 0,
    "trace_rule_call_counts": {},
    "trace_evidence_status_counts": {},
    "trace_detection_status_counts": {},
    "trace_rule_reason_counts": {}
  },
  "candidates": []
}
```

For real runs, `inputs.*` should record only the path string passed to the CLI.
Do not normalize to machine-specific absolute paths in committed fixtures.
`method_label` must equal the selected preset method label. For
`pt_pattern_cid_nl_dr`, `inputs.cid_nl_candidates_csv` must be the CLI-provided
string and must not be `null`. Sidecar tests must cover both `pt_pattern` and
`pt_pattern_cid_nl_dr`.

## Implementation Tasks

### Task 1: Add Contract Tests First

Files:

- `tests/test_delta_mass_patterns.py`
- `tests/test_delta_mass_csv.py`
- `tests/test_delta_mass_pipeline.py`
- `tests/test_pt_pattern_presets.py`
- `tests/test_run_pt_pattern_discovery.py`
- `tests/test_pt_pattern_boundaries.py`

Interfaces:

- `PT_PATTERN`
- `DeltaMassPatternSettings`
- `DetectedFeature`
- `match_delta_mass_pattern()`
- `load_detected_features_csv()`
- `write_pt_pattern_outputs()`
- `run_pt_pattern_discovery()`
- `scripts.run_pt_pattern_discovery.main()`

Steps:

- [ ] Add a no-RAW ppm boundary test proving the `+1` window uses expected m/z,
      not delta m/z, through `match_delta_mass_pattern()`:

```python
def test_matcher_pt_plus_one_window_uses_expected_mz_denominator() -> None:
    base_mz = 500.0
    expected_mz = base_mz + 1.0021
    inside = expected_mz + expected_mz * 20e-6
    outside = expected_mz + expected_mz * 20.1e-6

    assert len(match_delta_mass_pattern(_features_with_pt_plus_1(inside))) == 1
    assert len(match_delta_mass_pattern(_features_with_pt_plus_1(outside))) == 0
```

- [ ] Keep a helper-level `is_within_ppm()` unit test only as supporting
      coverage. The matcher-level test above is the required oracle.
- [ ] Add a test where `base`, `+1`, `+2`, and `+4` all match within 20 ppm
      and RT span is under 30 sec, producing exactly one candidate.
- [ ] Add a test where `+4` is missing. The matcher must produce no detected
      candidate.
- [ ] Add a test where all m/z positions match but RT span is over 30 sec. The
      matcher must produce no detected candidate.
- [ ] Add a test where all positions match but local signal support is missing.
      The matcher must produce no detected candidate.
- [ ] Add a test where both height and area exist. Candidate CSV must write
      both `*_height_ratio_to_base` and `*_area_ratio_to_base`.
- [ ] Add a test where area is blank. Candidate CSV must write height ratio and
      blank area ratio without failing.
- [ ] Add tests for exact candidate/review CSV header order.
- [ ] Add tests for sidecar JSON shape and schema version.
- [ ] Add CLI tests for:
      `--preset pt_pattern`,
      `--preset pt_pattern_cid_nl_dr`,
      required `--cid-nl-candidates-csv` for composite,
      and no RAW/DLL requirement.
- [ ] Add an AST boundary test proving Pt pattern modules do not import
      `xic_extractor.alignment`, `xic_extractor.output`, `scripts.csv_to_excel`,
      or `scripts.run_alignment`.

### Task 2: Implement Domain Models and Pattern Matching

Files:

- `xic_extractor/discovery/detected_features.py`
- `xic_extractor/discovery/delta_mass_patterns.py`
- `xic_extractor/discovery/models.py`

Interfaces:

```python
@dataclass(frozen=True)
class DetectedFeature:
    sample_stem: str
    feature_id: str
    mz: float
    rt_apex_min: float
    rt_start_min: float | None
    rt_end_min: float | None
    height: float
    area: float | None
    trace_quality: str
    shape_score: float | None
    source: str

@dataclass(frozen=True)
class DeltaMassPatternPosition:
    label: str
    delta_da: float

@dataclass(frozen=True)
class DeltaMassPattern:
    pattern_id: str
    display_name: str
    charge: int
    polarity: str
    positions: tuple[DeltaMassPatternPosition, ...]

@dataclass(frozen=True)
class DeltaMassPatternSettings:
    pattern_id: str = "pt"
    method_label: str = "pt_pattern"
    mz_tolerance_ppm: float = 20.0
    rt_tolerance_sec: float = 30.0
    ratio_basis: str = "both"

def match_delta_mass_pattern(
    features: Iterable[DetectedFeature],
    *,
    pattern: DeltaMassPattern = PT_PATTERN,
    settings: DeltaMassPatternSettings = DeltaMassPatternSettings(),
) -> tuple[DeltaMassCandidate, ...]:
    ...
```

Steps:

- [ ] Add `DetectedFeature` parsing helpers with strict field validation.
- [ ] Add `PT_PATTERN` as the only built-in pattern.
- [ ] Add `is_within_ppm()` and use expected m/z as denominator.
- [ ] Group features by `sample_stem`; sort each group by m/z.
- [ ] For each possible base feature, calculate expected m/z for each nonzero
      Pt position.
- [ ] Use an m/z-bounded lookup window for each expected position:

```python
window_da = expected_mz * settings.mz_tolerance_ppm * 1e-6
lower_mz = expected_mz - window_da
upper_mz = expected_mz + window_da
```

- [ ] Filter position matches by absolute RT delta from base:

```python
rt_delta_sec = abs(feature.rt_apex_min - base.rt_apex_min) * 60.0
```

- [ ] If multiple features match a position, select the one with smallest
      absolute RT delta, then smallest absolute ppm error, then highest height.
- [ ] Require all three nonzero positions. Do not emit partial or base-only
      rows in this first Discovery matcher.
- [ ] Require local signal support:

```text
supported if base and at least two non-base positions have
trace_quality in {"GOOD", "OK", "PASS"} or shape_score >= 0.80
```

- [ ] Compute RT span in seconds across all four positions.
- [ ] Compute height ratios and area ratios relative to base.
- [ ] Emit deterministic `candidate_id`:

```text
{sample_stem}|pt|base:{base_feature_id}|mz:{base_mz:.6f}|rt:{base_rt_apex_min:.6f}
```

- [ ] Sort candidates by `sample_stem`, `base_rt_apex_min`, `base_mz`,
      `candidate_id`.

### Task 3: Implement CSV and JSON IO

Files:

- `xic_extractor/discovery/delta_mass_csv.py`
- `xic_extractor/discovery/csv_writer.py`
- `xic_extractor/tabular_io.py`
- `tests/test_delta_mass_csv.py`

Interfaces:

```python
DETECTED_FEATURE_COLUMNS = (...)
PT_PATTERN_CANDIDATE_COLUMNS = (...)
PT_PATTERN_REVIEW_COLUMNS = (...)

def load_detected_features_csv(path: Path) -> tuple[DetectedFeature, ...]:
    ...

def write_pt_pattern_candidates_csv(
    path: Path,
    candidates: Sequence[DeltaMassCandidate],
) -> Path:
    ...

def write_pt_pattern_review_csv(
    path: Path,
    candidates: Sequence[DeltaMassCandidate],
) -> Path:
    ...

def write_pt_pattern_sidecar_json(
    path: Path,
    *,
    result: DeltaMassDiscoveryResult,
) -> Path:
    ...
```

Steps:

- [ ] Add required input/output column constants exactly as listed in Public
      Contract Details.
- [ ] Create parent directories before writing.
- [ ] Write header-only CSVs when there are no candidates.
- [ ] Preserve candidate/review row order between full and review CSVs.
- [ ] Pt candidate/review writers must call the shared delimited writer with
      `extrasaction="raise"` or perform an equivalent schema assertion in
      production code. Extra row fields must fail the writer API, not only a
      test-local renderer.
- [ ] If Pt writer needs formula escaping, add a narrow helper to
      `xic_extractor.tabular_io`:

```python
def escape_excel_formula(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
```

- [ ] Update existing discovery CSV writer to use the shared helper only if its
      tests remain identical. Do not import `xic_extractor.alignment` from
      Discovery.
- [ ] Format floats with the same precision style as existing discovery CSVs:
      stable, compact, no unnecessary trailing zeros.
- [ ] Add writer tests proving unexpected fields fail loudly and formula-like
      text is escaped consistently.
- [ ] In sidecar JSON, record CLI input path strings exactly as provided, not
      resolved local absolute paths.

### Task 4: Extend Presets Without Breaking Neutral-Loss Presets

Files:

- `xic_extractor/presets/models.py`
- `xic_extractor/presets/loader.py`
- `xic_extractor/presets/apply.py`
- `xic_extractor/presets/__init__.py`
- `xic_extractor/presets/data/pt_pattern.toml`
- `xic_extractor/presets/data/pt_pattern_cid_nl_dr.toml`
- `tests/test_pt_pattern_presets.py`
- Existing preset tests

Interfaces:

```toml
# xic_extractor/presets/data/pt_pattern.toml
name = "Pt pattern"
description = "Delta-mass Pt pattern discovery."
combine_mode = "single"

[[tag]]
strategy = "delta_mass"
name = "Pt pattern"
pattern_id = "pt"

[discovery]
method_label = "pt_pattern"
delta_mass_mz_tolerance_ppm = 20.0
delta_mass_rt_tolerance_sec = 30.0
delta_mass_ratio_basis = "both"
```

```toml
# xic_extractor/presets/data/pt_pattern_cid_nl_dr.toml
name = "Pt pattern + CID-NL dR"
description = "Pt pattern intersected with CID neutral-loss dR evidence on base."
combine_mode = "intersection"

[[tag]]
strategy = "delta_mass"
name = "Pt pattern"
pattern_id = "pt"

[[tag]]
strategy = "neutral_loss"
name = "DNA_dR"
value = 116.0474

[discovery]
method_label = "pt_pattern+cid_nl_dr"
delta_mass_mz_tolerance_ppm = 20.0
delta_mass_rt_tolerance_sec = 30.0
delta_mass_ratio_basis = "both"
```

Steps:

- [ ] Add `delta_mass` to `SUPPORTED_STRATEGIES`.
- [ ] Extend `PresetTag` with `pattern_id: str | None = None` and
      `value: float | None = None`.
- [ ] For `strategy = "neutral_loss"`, require finite `value` and reject
      `pattern_id`.
- [ ] For `strategy = "delta_mass"`, require `pattern_id = "pt"` and reject
      `value`.
- [ ] Keep `product_ion` unsupported in loader until a real HCD-PI contract
      exists.
- [ ] Add `pt_pattern` and `pt_pattern_cid_nl_dr` to `list_presets()`.
- [ ] Update `apply_to_discovery()` so neutral-loss-only presets return the same
      `DiscoverySettings` as today.
- [ ] Keep `apply_to_discovery()` neutral-loss-only. If a preset contains any
      `delta_mass` tag, `apply_to_discovery()` must raise a clear `PresetError`
      telling callers to use `apply_to_delta_mass_discovery()`.
- [ ] Add:

```python
def apply_to_delta_mass_discovery(
    preset: Preset,
) -> DeltaMassPatternSettings:
    ...
```

- [ ] `apply_to_delta_mass_discovery()` accepts `pt_pattern` and
      `pt_pattern_cid_nl_dr`, returns `DeltaMassPatternSettings`, and never
      builds fake `NeutralLossProfile` values.
- [ ] Loader validation for delta-mass presets must be strategy-aware:
      `strategy = "neutral_loss"` requires finite `value` and rejects
      `pattern_id`; `strategy = "delta_mass"` requires `pattern_id = "pt"` and
      rejects `value`.
- [ ] `[discovery]` must allow and validate `method_label`,
      `delta_mass_mz_tolerance_ppm`, `delta_mass_rt_tolerance_sec`, and
      `delta_mass_ratio_basis` only for presets with a `delta_mass` tag. These
      keys must remain rejected for neutral-loss-only presets unless a successor
      plan explicitly changes that contract.
- [ ] For composite preset, require `combine_mode = "intersection"` and exactly
      one `delta_mass(pattern_id=pt)` tag plus one `neutral_loss(DNA_dR)` tag.
- [ ] Keep existing `dna_dr` and `dna_dr_product_ready` tests passing without
      changing expected values.

### Task 5: Add Pt Pattern Pipeline and CLI

Files:

- `xic_extractor/discovery/delta_mass_pipeline.py`
- `scripts/run_pt_pattern_discovery.py`
- `pyproject.toml`
- `tests/test_delta_mass_pipeline.py`
- `tests/test_run_pt_pattern_discovery.py`

Interfaces:

```python
@dataclass(frozen=True)
class DeltaMassDiscoveryOutputs:
    candidates_csv: Path
    review_csv: Path
    sidecar_json: Path

def run_pt_pattern_discovery(
    *,
    detected_features_csv: Path,
    output_dir: Path,
    preset_name: str = "pt_pattern",
    cid_nl_candidates_csv: Path | None = None,
    timing_recorder: TimingRecorder | None = None,
) -> DeltaMassDiscoveryOutputs:
    ...
```

CLI:

```powershell
python -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output/pt_pattern/example/detected_features.csv `
  --output-dir output/pt_pattern/example `
  --preset pt_pattern
```

Composite CLI:

```powershell
python -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output/pt_pattern/example/detected_features.csv `
  --cid-nl-candidates-csv output/discovery/example/discovery_candidates.csv `
  --output-dir output/pt_pattern/example `
  --preset pt_pattern_cid_nl_dr
```

Steps:

- [ ] Load preset and fail if the preset is not a Pt pattern preset.
- [ ] Load detected features.
- [ ] For `pt_pattern`, write candidates with `cid_nl_base_status =
      not_required`.
- [ ] For `pt_pattern_cid_nl_dr`, require `--cid-nl-candidates-csv`.
- [ ] Parse existing `discovery_candidates.csv` rows for CID-NL base evidence.
- [ ] Use a typed CID-NL candidate parser for composite joins. Required columns
      must be present; `precursor_mz`, `ms1_apex_rt`, and `evidence_score` must
      be finite when used; `neutral_loss_tag` and `discovery_candidate_state`
      must match allowed values exactly. Missing or malformed values must raise
      a clear error instead of being skipped.
- [ ] CID-NL base join predicate:

```text
1. same-feature identity join, preferred:
   same sample_stem
   neutral_loss_tag == DNA_dR
   discovery_candidate_state in {"ms1_feature_nl_supported", "ms1_feature_nl_rescued"}
   cid_row.ms1_feature_row_id equals pt_base.source_feature_row_id or pt_base.feature_id

2. m/z/RT fallback, only when Pt feature identity cannot be compared:
   same sample_stem
   neutral_loss_tag == DNA_dR
   discovery_candidate_state in {"ms1_feature_nl_supported", "ms1_feature_nl_rescued"}
   abs((cid_row.precursor_mz - pt_base_mz) / pt_base_mz * 1e6) <= 20
   abs(cid_row.ms1_apex_rt - pt_base_rt_apex_min) * 60 <= 30
```

- [ ] If a same-feature identity match exists, it wins over every fallback
      match. If multiple same-feature rows match one Pt base, select highest
      `evidence_score`, then smallest absolute RT delta.
- [ ] If fallback mode is used, write `cid_nl_base_join_mode = mz_rt_fallback`
      and include fallback wording in `reason`.
- [ ] If multiple fallback CID-NL rows match one Pt base, select smallest
      absolute RT delta, then smallest absolute m/z ppm, then highest
      `evidence_score`.
- [ ] If no CID-NL row matches, exclude the candidate from composite output.
- [ ] If required CID-NL columns are missing, fail closed with a clear error.
- [ ] For composite mode, build CID-NL base seeds before Pt matching and pass
      only those base feature IDs to `match_delta_mass_pattern()`. Fallback
      seeds may use m/z/RT only when same-feature identity cannot be compared.
      If a detected feature has `source_feature_row_id`, identity mismatch must
      not silently fall back to m/z/RT.
- [ ] Keep scan-proxy labels source-specific: `raw_scan_proxy_v1` rows ending
      in `|pt_plus_1`, `|pt_plus_2`, or `|pt_plus_4` must never become shifted
      base anchors.
- [ ] Write `pt_pattern_candidates.csv`, `pt_pattern_review.csv`,
      `pt_pattern_evidence.csv`, and `pt_pattern_sidecar.json`.
- [ ] Add `pyproject.toml` script entry:

```toml
xic-pt-pattern-discovery-cli = "scripts.run_pt_pattern_discovery:main"
```

- [ ] Keep `xic-discovery-cli` and existing neutral-loss pipeline outputs
      unchanged.

### Task 6: Guard Matrix, Alignment, Backfill, and Workbook Boundaries

Files:

- `tests/test_pt_pattern_boundaries.py`
- No production changes outside Discovery/presets/CLI

Steps:

- [ ] Add an AST/import boundary test:

```python
FORBIDDEN_IMPORTS = {
    "xic_extractor.alignment",
    "xic_extractor.output",
    "scripts.csv_to_excel",
    "scripts.run_alignment",
}
```

- [ ] Assert Pt pattern modules do not import forbidden modules.
- [ ] Assert no Pt pattern writer writes `.xlsx`.
- [ ] Assert no Pt pattern code imports or calls ProductWriter or quant-matrix
      builders.
- [ ] Assert no Pt pattern output row uses Backfill terminology as detection.
- [ ] Add a regression test that base-only input creates zero candidates.
- [ ] Add a regression test that partial pattern plus a synthetic
      "backfill-like" row still creates zero detected candidates.

### Task 7: Detected-Feature Source Gate For Real Samples

Files:

- `tools/diagnostics/pt_pattern_scan_proxy.py`
- `tests/test_pt_pattern_scan_proxy.py`
- `docs/superpowers/validation/pt_pattern_discovery_v1/README.md`

Purpose:

- Make the first Fullscan sample runnable through the same Pt candidate writer
  without pretending a scan-level proxy is product-quality peak detection.

Rules:

- If an accepted detected-feature CSV is available, use it directly and record
  `source` from that feature detector.
- If no accepted detected-feature CSV is available, add
  `tools/diagnostics/pt_pattern_scan_proxy.py` to produce a schema-valid
  `detected_features.csv` with `source = raw_scan_proxy_v1` and a proxy
  provenance JSON. That CSV can drive the Pt matcher for candidate exploration,
  but the validation README must label the result `diagnostic_only`.
- The proxy bridge answers the user's first "does this Fullscan sample produce
  plausible candidates?" question. It is not a product feature detector, not
  production readiness, and not Matrix authority.

Steps:

- [ ] Add a proxy smoke test that emits `DETECTED_FEATURE_COLUMNS` exactly.
- [ ] Add a validation note field:

```text
detected_feature_source = accepted_feature_csv | raw_scan_proxy_v1
validation_status = diagnostic_only
```

- [ ] Do not start Fullscan real-sample candidate generation until either an
      accepted feature CSV is named or the proxy bridge command emits a
      schema-valid `detected_features.csv`.
- [ ] Update the branch handoff with which source was used before asking the
      user to review candidate rows.

### Task 8: Diagnostic Real-Sample Candidate Generation

Files:

- `docs/superpowers/validation/pt_pattern_discovery_v1/README.md`
- Optional only if a detected-feature source is not yet available:
  `tools/diagnostics/pt_pattern_scan_proxy.py`
- Optional proxy tests:
  `tests/test_pt_pattern_scan_proxy.py`

Purpose:

- Generate a human-review candidate set from the user-provided Fullscan sample
  without claiming product validation or Matrix readiness.

Rules:

- Use the source selected by Task 7.
- A scan-level proxy result may be used only as `diagnostic_only` evidence. It
  cannot be used as product acceptance or Matrix authority.
- Keep local RAW paths in environment variables or ignored local notes. Do not
  commit machine-specific absolute RAW paths.

Command shape:

```powershell
Get-Content .env.xic-local |
  Where-Object { $_ -match '^\s*[^#=]+=' } |
  ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item "Env:$name" $value
  }

Test-Path "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw"
Test-Path "$env:THERMO_RAWFILE_READER_DLL_DIR"
```

If detected features are available:

```powershell
.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_discovery_v1\fullscan\detected_features.csv `
  --output-dir output\pt_pattern_discovery_v1\fullscan `
  --preset pt_pattern
```

If only a diagnostic proxy is available, generate `detected_features.csv` first:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_scan_proxy `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy `
  --lookup-ppm 20 `
  --min-scans-per-group 2
```

Then run the same Pt discovery CLI on the proxy-generated detected-feature CSV:

```powershell
.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_discovery_v1\fullscan_proxy\detected_features.csv `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy\pt_candidates `
  --preset pt_pattern
```

Steps:

- [ ] Create validation README with `Doc placement: repo_support_doc`,
      `Doc kind: validation_artifact`, `Doc lifecycle: archived`,
      `Repo owner: docs/product/discovery.md`, and a clear diagnostic-only
      status.
- [ ] Record command, input alias, candidate count, output paths, and manual
      review status.
- [ ] Do not copy large RAW outputs or local absolute paths into repo docs.
- [ ] Mark result `diagnostic_only` until the user manually accepts candidate
      examples as an oracle.

### Task 9: Composite DDMS2 Diagnostic Gate

Files:

- `docs/superpowers/validation/pt_pattern_cid_nl_dr_v1/README.md`
- Existing neutral-loss discovery outputs from `xic-discovery-cli`
- Pt pattern outputs from `scripts.run_pt_pattern_discovery`

Purpose:

- Test `pt_pattern + cid_nl(dR)` on the DDMS2 sample only after Task 5's base
  join predicate is implemented and covered by no-RAW tests.

Command outline:

```powershell
.venv\Scripts\python.exe -m scripts.run_discovery `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\cid_nl `
  --neutral-loss-tag DNA_dR `
  --neutral-loss-da 116.0474

.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery `
  --detected-features-csv output\pt_pattern_cid_nl_dr_v1\detected_features.csv `
  --cid-nl-candidates-csv output\pt_pattern_cid_nl_dr_v1\cid_nl\discovery_candidates.csv `
  --output-dir output\pt_pattern_cid_nl_dr_v1\pt_intersection `
  --preset pt_pattern_cid_nl_dr
```

Steps:

- [ ] Run this only after Pt-only synthetic tests pass.
- [ ] Confirm the CID-NL candidate CSV has the columns needed by the base join.
- [ ] Confirm every composite output row has `cid_nl_base_status = matched`.
- [ ] Confirm there are no composite rows when the CID-NL input is absent.
- [ ] Keep validation status `diagnostic_only` until manual review accepts an
      oracle.

### Task 10: Chromatography-Aware Trace Diagnosis

Files:

- `xic_extractor/discovery/pt_pattern_trace_evidence.py`
- `tools/diagnostics/pt_pattern_trace_diagnosis.py`
- `tests/test_pt_pattern_trace_diagnosis.py`
- `tools/diagnostics/INDEX.md`
- `docs/superpowers/validation/pt_pattern_discovery_v1/README.md`
- `docs/superpowers/validation/pt_pattern_cid_nl_dr_v1/README.md`
- `docs/product/discovery.md`

Purpose:

- Test the user hypothesis that Pt pattern detection should combine scan-level
  delta-pattern seed evidence with chromatographic co-elution confirmation.
- Extract vendor XIC traces for base / `+1.0021` / `+2.0023` / `+4.0052`
  around sampled candidates and report RT/apex/shape support.
- Keep the vocabulary strict: a scan-level full pattern is a seed/candidate;
  a diagnostic Pt pattern detection requires both scan seed and trace support.
- Keep this as `diagnostic_only`: no candidate creation, Matrix write,
  Backfill promotion, counted detection, or product gate.

Command outline:

```powershell
.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_discovery_v1\fullscan_proxy_fixed\pt_candidates_anchor_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\CT-DNA+100uMCP_Fullscan.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_discovery_v1\fullscan_proxy_fixed\trace_diagnosis_limit50 `
  --limit 50

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis `
  --candidates-csv output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\pt_pattern_candidates.csv `
  --raw "$env:PT_PATTERN_SAMPLE_DIR\DNA+100uMCP_DDMS2_new.raw" `
  --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" `
  --output-dir output\pt_pattern_cid_nl_dr_v1\pt_intersection_seed_first_fixed\trace_diagnosis_limit50 `
  --limit 50
```

Observed 50-row diagnostic slices:

- Pt-only scan-proxy candidates: 12 supported, 32 partial, 5 review, 1
  incomplete trace signal.
- Pt+CID-NL(dR) seed-first candidates: 34 supported, 14 partial, 2 incomplete
  trace signal.
- In both slices, `pt_plus_4` is the most common weak position.
- The trace summary writes `scan_pattern_seed_status`,
  `trace_support_status`, and `combined_pt_pattern_status`. The value
  `pt_pattern_detected_diagnostic` means "scan seed plus co-elution support" in
  this ignored diagnostic output only; it is not counted detection or Matrix
  authority.
- Trace diagnosis schema v2 uses Gaussian15-smoothed XIC intensity as the
  decision basis for apex, shape similarity, active overlap, and support
  status. Raw vendor trace points and raw audit metrics remain available for
  inspection, but raw trace similarity is not the default support gate.
- The reusable support semantics now live in
  `xic_extractor.discovery.pt_pattern_trace_evidence`; the diagnostic CLI only
  opens RAW files, batches XIC extraction, and writes diagnostic artifacts.
- The diagnostic JSON sidecar records `readiness_tier = diagnostic_only` and
  `evidence_owner = xic_extractor.discovery.pt_pattern_trace_evidence` so this
  probe cannot be mistaken for Matrix/counting authority.

Conclusion:

- The trace diagnosis has useful discrimination and supports the architecture
  pivot: keep scan-level pattern finding as the seed layer, then require
  chromatographic co-elution evidence before calling a candidate
  detection-grade. Do not productize scan-level matching by itself.

### Task 11: Manual Review Packet For Oracle Promotion

Files:

- `xic_extractor/discovery/pt_pattern_review_packet.py`
- `tools/diagnostics/pt_pattern_review_packet.py`
- `tests/test_pt_pattern_review_packet.py`
- `tools/diagnostics/INDEX.md`

Purpose:

- Convert existing Pt candidate + trace diagnosis outputs into a bounded,
  human-reviewable packet.
- Let the user mark sampled rows as `accepted`, `rejected`, or `uncertain`
  before any oracle or promotion packet exists.
- Keep the packet `diagnostic_only`: blank human-review columns and
  `oracle_status = pending_human_review` are not product authority.

Command outline:

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

Rules:

- The packet samples up to `--max-per-status` rows from each
  `combined_pt_pattern_status` bucket in deterministic RT/m/z order.
- The CSV must include `human_review_decision`, `human_review_note`, and
  `oracle_status` columns, but generated rows keep the decision blank and
  `oracle_status = pending_human_review`.
- The JSON sidecar must record `readiness_tier = diagnostic_only` and the
  package evidence owner. It must not claim an accepted oracle.
- A future promotion step may consume reviewed rows only after the user marks
  decisions and the packet is treated as a human-created oracle artifact.

### Task 12: Double-Blind MS1/XIC Overlay Review

Files:

- `xic_extractor/discovery/pt_pattern_blind_overlay.py`
- `tools/diagnostics/pt_pattern_blind_overlay.py`
- `tests/test_pt_pattern_blind_overlay.py`
- `tools/diagnostics/INDEX.md`

Purpose:

- Support a double-blind review of the bounded Pt review packets.
- Generate an HTML overlay gallery that shows normalized base / `+1.0021` /
  `+2.0023` / `+4.0052` traces for each packet row.
- Keep Codex's rule-based call in a separate sealed CSV so the HTML does not
  bias the user's visual judgment.

Command outline:

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

Rules:

- HTML must not include `combined_pt_pattern_status`, machine calls, or
  accept/reject/uncertain strings.
- HTML trace curves and sealed machine calls use the same Gaussian15-smoothed
  trace-decision metrics. Raw trace-point CSVs remain unchanged and no Matrix
  authority, counted detection, or quantitation contract is granted.
- Summary JSON must not expose machine-call distribution.
- `pt_pattern_machine_calls_sealed.csv` is Codex's blind call surface; do not
  open it until the user has recorded independent calls.
- Overlay review remains `diagnostic_only`; it does not promote an oracle,
  Matrix authority, or counted detection.

### Task 13: Bounded Manual Oracle Promotion Packet

Files:

- `xic_extractor/discovery/pt_pattern_oracle_promotion.py`
- `tools/diagnostics/pt_pattern_oracle_promotion.py`
- `tests/test_pt_pattern_oracle_promotion.py`
- `tools/diagnostics/INDEX.md`

Purpose:

- Convert reviewed and adjudicated Pt blind-review rows into a bounded manual
  oracle promotion packet.
- Fail closed unless every adjudicated row is `accept` or `reject`, has a
  nonblank adjudication basis, and matches exactly one original manual-review
  packet row by `packet_row_id` / `candidate_id`.
- Record input SHA-256 provenance and make the scope explicit:
  `bounded_manual_oracle_only`.

Command outline:

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
- `pt_pattern_oracle_promotion_packet.csv`: 28 rows, 10 `accept` /
  `detected`, 18 `reject` / `non_detected`.
- `pt_pattern_oracle_promotion_summary.json`: records `readiness_tier =
  production_candidate`, `authority_scope = bounded_manual_oracle_only`, input
  SHA-256 values, and explicit false flags for Matrix, counted-detection, and
  export changes.

Current v2 status:

- `pt_pattern_trace_diagnosis.v2` changes the trace decision basis to
  Gaussian15-smoothed XIC intensity and regenerates the bounded packet
  population.
- The regenerated bounded v2 packets have 24 rows total. They overlap 20 old
  adjudicated row ids, add 4 new row ids, and leave 8 old v1 oracle row ids
  outside the bounded packets.
- `pt_pattern_oracle_refresh.v1` writes a no-RAW refresh worklist under
  `output/pt_pattern_productization_v1/oracle_refresh/pt_detected_oracle_v2_refresh/`:
  20 labels are carried forward with current packet provenance, 4 rows were
  adjudicated by the user as `reject`, and 8 old oracle rows are marked stale.
- The initial 24-row v2 adjudicated oracle candidate is
  `output/pt_pattern_productization_v1/oracle_refresh/pt_detected_oracle_v2_refresh/pt_pattern_v2_adjudicated_oracle_candidate.csv`
  with 24 rows: 5 `accept`, 19 `reject`, 0 `uncertain`. This artifact is an
  intermediate refresh result, not the calibration baseline, because it omits
  the 8 old v1 oracle rows that fell outside the bounded packet selection.
- The repaired v2 oracle rechecks all 8 stale old rows against current
  Gaussian15 packet metrics and combines them with the 24-row refresh result:
  `output/pt_pattern_productization_v1/oracle_repair/pt_detected_oracle_v2_repaired_32/`.
  The repaired adjudicated oracle has 32 rows: 10 `accept`, 22 `reject`, 0
  `uncertain`.
- The current repaired v2 oracle promotion output is
  `output/pt_pattern_productization_v1/oracle_promotion/pt_detected_oracle_v2_repaired_32/`.
  Its summary records `readiness_tier = production_candidate`,
  `authority_scope = bounded_manual_oracle_only`, 10 `detected`, 22
  `non_detected`, and explicit false flags for Matrix, counted-detection, and
  export changes.
- The current repaired v2 threshold calibration output is
  `output/pt_pattern_productization_v1/oracle_thresholds/pt_detected_oracle_v2_repaired_32/`.
  It recommends full-pattern status plus `max_abs_apex_delta_sec <= 10`,
  `min_pearson_r_to_base >= 0.90`, and
  `min_active_overlap_fraction >= 0.50`. Against the repaired 32-row v2 oracle,
  that rule has 10 true positives, 22 true negatives, 0 false positives, and 0
  false negatives. The previous sealed machine-call rule has 3 false positives
  and 8 uncertain reject rows on the repaired oracle.
- The current repaired v2 diagnostic gate replay output is
  `output/pt_pattern_productization_v1/diagnostic_gate/pt_detected_oracle_v2_repaired_32/`.
  It applies the same calibrated rule through
  `xic_extractor.discovery.pt_pattern_diagnostic_gate` and schema
  `pt_pattern_diagnostic_gate.v1`. With `--require-zero-fpfn`, the replay exits
  `0` on the repaired 32-row v2 oracle and reports 10 true positives, 22 true
  negatives, 0 false positives, and 0 false negatives.
- The reusable row-level Pt full-pattern accept/reject rule now lives in
  `xic_extractor.discovery.pt_pattern_evidence_rule`. Threshold calibration and
  diagnostic gate replay call that shared rule; they no longer own separate
  copies of the decision logic.
- The current full trace-summary diagnostic gate replay output is
  `output/pt_pattern_productization_v1/diagnostic_gate/trace_summary_limit50_current/`.
  It uses `--trace-summary-input` on the current 50-row Pt-only Fullscan trace
  summary and current 50-row Pt+CID-NL DDMS2 trace summary. This no-oracle
  diagnostic replay reports 100 rows: 45 `accept`, 55 `reject`. Source-level
  calls are 15/35 accept/reject for Pt-only and 30/20 accept/reject for
  Pt+CID-NL.
- The v1 promotion packet is now historical calibration context only, not
  current v2 authority.

Rules:

- A promotion packet can be production-candidate only for the exact bounded
  manual oracle rows it validates. It does not make the Pt detector
  production-candidate, does not change Matrix authority, and does not redefine
  counted detection.
- A threshold calibration report is `diagnostic_only`. It can justify the next
  diagnostic gate, but it is not detector activation and must not become a
  counted-detection, Matrix, workbook, or export rule without a later explicit
  public-surface contract.
- A diagnostic gate replay is still `diagnostic_only`. It is useful because the
  calibrated rule is now machine-repeatable and fail-closed against a bounded
  oracle or full trace-diagnosed candidate slices, but it still does not
  activate the detector, write Matrix/Backfill, or define counted
  detection/export behavior.
- A refresh draft is not a promoted oracle. It can only feed promotion after
  every pending/conflicting row has an `accept` or `reject` adjudication and
  current packet provenance still matches.
- `uncertain` rows cannot be promoted. They must be adjudicated to `accept` or
  `reject` before this step.
- Natural-language notes stay preserved as rationale and are not normalized into
  a controlled vocabulary yet.

## Verification Commands

Run focused tests first:

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q `
  tests/test_delta_mass_patterns.py `
  tests/test_delta_mass_csv.py `
  tests/test_delta_mass_pipeline.py `
  tests/test_pt_pattern_presets.py `
  tests/test_run_pt_pattern_discovery.py `
  tests/test_pt_pattern_trace_diagnosis.py `
  tests/test_pt_pattern_review_packet.py `
  tests/test_pt_pattern_blind_overlay.py `
  tests/test_pt_pattern_oracle_promotion.py `
  tests/test_pt_pattern_oracle_refresh.py `
  tests/test_pt_pattern_oracle_thresholds.py `
  tests/test_pt_pattern_diagnostic_gate.py `
  tests/test_pt_pattern_boundaries.py `
  tests/test_presets_loader.py `
  tests/test_presets_apply.py `
  tests/test_run_discovery.py `
  tests/test_discovery_csv.py `
  tests/test_discovery_review_csv.py `
  tests/test_discovery_pipeline.py
```

Run CI-equivalent gates before PR:

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests
$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_diagnostics_index.py
$env:UV_CACHE_DIR='.uv-cache'; uv run mypy xic_extractor
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards --check
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards docs-config -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards gui -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards targeted-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards alignment-core -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-activation -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards product-gates-evidence -- -v --tb=short -x
$env:UV_CACHE_DIR='.uv-cache'; uv run python -m tools.testing.test_shards diagnostics-tools -- -v --tb=short -x
```

Run after documentation or plan edits:

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q `
  tests/test_docs_placement_guard.py `
  tests/test_docs_policy.py `
  tests/test_docs_management_audit.py

git diff --check
$scanPaths = @(
  "docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md",
  "docs/superpowers/plans/README.md"
)
Select-String -Path $scanPaths `
  -Pattern '(?i)(api[_-]?key\s*=|token\s*=|secret\s*=|password\s*=|[A-Z]:[\\/][^\s`"<>|]+)'
```

## Self-Review Checklist

- [ ] Pt ppm formula uses expected m/z denominator for nonzero positions.
- [ ] Full pattern is required for Discovery candidate output.
- [ ] Base-only and partial-pattern cases produce no detected candidate.
- [ ] RT gate is `<= 30 sec`.
- [ ] Local signal support is required for a sample-local seed.
- [ ] Height and area ratios are evidence only.
- [ ] Composite strategy is intersection on base, not union.
- [ ] Existing neutral-loss discovery behavior and tests are unchanged.
- [ ] Pt outputs are review/sidecar artifacts only.
- [ ] Trace diagnosis outputs are diagnostic artifacts only.
- [ ] No Matrix, workbook Matrix, ProductWriter, Backfill, or counted-detection
      authority changed.
- [ ] Real-sample outputs are labeled `diagnostic_only` until manual review
      promotes an oracle.
- [ ] No committed doc or fixture contains local absolute RAW paths or secrets.
- [ ] Ignored local handoff content is not copied into PR body or tracked docs
      when it contains machine-local RAW paths.

## Productization Control-Plane Impact

The first diagnostic implementation pass did not require a control-plane tier
change. The product goal has now changed: this branch is the active
`pt_pattern_discovery_productization_v1` lane in
`docs/superpowers/plans/2026-06-15-productization-control-plane.md`.

Current tier remains `diagnostic_only`. The near-term target is
`production_candidate`, not `production_ready`, and only after trace/co-elution
evidence is package-owned, schema/provenance tests are locked, and a manual
accepted/rejected oracle exists. `pt_pattern_trace_diagnosis.v2` is still a
diagnostic output; even
`combined_pt_pattern_status = pt_pattern_detected_diagnostic` is not counted
detection, ProductWriter authority, workbook, Matrix, or activation schema.
