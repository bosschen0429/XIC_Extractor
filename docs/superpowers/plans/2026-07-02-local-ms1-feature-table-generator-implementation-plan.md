# Local MS1 Feature Table Generator Implementation Plan

Doc placement: formal_repo_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/superpowers/plans/2026-07-02-local-ms1-feature-table-generator-implementation-plan.md
Doc exit rule: Mark implemented or superseded after the local MS1 feature table
generator leaves the active Pt pattern productization branch.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, single-RAW MS1 feature table generator that emits canonical `detected_features.csv` screening evidence for Pt pattern Discovery without granting trace-supported, Matrix, Backfill, counted-detection, or final-quant authority.

**Architecture:** Keep `DetectedFeature` as the public handoff contract, but add source-aware semantics so `source=local_ms1_feature` rows remain feature-source evidence. Extract reusable MS1 scan-index mechanics to a neutral package module, then build `xic_extractor.discovery.local_ms1_features` on top of that neutral helper. The CLI writes `detected_features.csv`, `local_ms1_feature_summary.json`, and `local_ms1_feature_diagnostics.csv`; Pt matching stays feature-level and trace/co-elution authority remains owned by Pt trace evidence modules.

**Post-review implementation amendment, 2026-07-02:** Real Fullscan RAW rejected
the original all-centroid adjacent scan-linking path: it timed out beyond 1000
seconds after trying to trace 86,452,767 centroid points. The implemented
product-facing path is now MZmine-inspired seed-first chromatogram building:
mass-detected MS1 points above `mass_detection_noise_level` are collected once,
high-intensity points above `min_trace_seed_intensity` define non-overlapping
m/z seed ranges, and all mass-detected points inside each range are segmented in
scan order. `max_missing_scans` is enforced while segmenting scan-ordered points;
intensity order must not decide RT trace topology. Each trace must satisfy
`min_consecutive_trace_intensity` across
`min_observed_scans` consecutive MS1 scans before Gaussian15 peak-region
classification. The old `_link_traces()` scan-linker is retained only as a
low-level diagnostic/test helper, not the default feature-source path.

**Post-review observability amendment, 2026-07-03:** Expose
`trace_builder_mode` as a top-level `local_ms1_feature_summary.json` field, not
only under `parameters`, because seeded mode and scan-link mode use different
diagnostic metric semantics. In `mzmine_seeded` mode, `candidate_edge_count`
means mass-detected point count, `conflict_component_count` means m/z seed range
count, and degraded-assignment counts are not applicable because bipartite
assignment is not used. Known V1 limits are seed-range clipping for close m/z
features, one retained centroid per scan/seed range, absolute noise threshold,
and diagnostic-only scan-link optimal assignment.

**Lab-default amendment, 2026-07-03:** Set the V1 default
`mass_detection_noise_level` to `25000`, matching the lab's routine screening
threshold. This changes the default CLI/config behavior for future local MS1
feature-table runs; older smoke counts produced with the previous 50,000
threshold are previous-threshold evidence until rerun.

**Tech Stack:** Python 3.11+, dataclasses, NumPy, existing Thermo `raw_reader`, existing `tabular_io.write_delimited_rows`, existing `peak_detection.ms1_morphology.gaussian15_morphology_trace`, pytest.

## Global Constraints

- Branch/worktree: work only in the current XIC worktree on branch `codex/pt-pattern-discovery`.
- Do not stage, revert, or rewrite unrelated dirty files already present in the worktree.
- V1 input is one Thermo RAW file; V1 output is one sample-local `detected_features.csv`.
- `source=local_ms1_feature` main CSV rows may have only `trace_quality=OK` or `trace_quality=WEAK_SUPPORT`.
- `FAIL`, unknown labels, and misspelled labels fail closed for `source=local_ms1_feature`; rejected regions go to diagnostics only.
- `height` is Gaussian15-smoothed screening height.
- `area` is Gaussian15-smoothed, local linear baseline-corrected screening area.
- `area_contract=screening_only` and `quant_ready=false`.
- Local generator rows alone must not become `supported`, tier A, score-90, `detected`, counted detection, Backfill authority, Matrix authority, workbook/export authority, or final quantification evidence.
- Base / 0 position requires `trace_quality=OK`.
- Non-base Pt positions (`+1`, `+2`, `+4`) may use `OK` or `WEAK_SUPPORT`.
- `shape_score` must not upgrade `WEAK_SUPPORT`, `FAIL`, diagnostics-only rows, or unknown local labels into local base eligibility.
- Full four-position local feature-source patterns must remain `feature_source_detected` until `xic_extractor.discovery.pt_pattern_trace_evidence` or another explicitly validated trace/co-elution provider upgrades them.
- Discovery package code must not import Alignment internals for MS1 scan indexing; shared MS1 scan-index mechanics belong in a neutral package helper.
- The product CLI must not silently fall back to `raw_scan_proxy_v1`; scan proxy remains diagnostic-only.
- Focused Fullscan/DDMS2 single-RAW runs remain `diagnostic_only`.
- Initial single-RAW diagnostic budget: `feature_table_wall_time_sec <= 900`, `pt_candidate_rows <= 10000`, `bounded_review_packet_rows <= 64`, `degraded_assignment_component_fraction <= 0.05`.
- No Matrix, Backfill, counted detection, workbook export, z=2 support, union/OR composite strategy, or final quantitative area in this implementation.
- Before RAW/DLL commands, use `.venv\Scripts\python.exe` and `docs/agent/parameter-settings.md` runner rules.
- Commits require explicit user instruction in this Codex environment; if executing this plan without commit permission, perform the code/test work and leave changes unstaged.

---

## File Structure

- Modify `xic_extractor/discovery/detected_features.py`: add local feature source constants, local `trace_quality` validation, and exported helper predicates.
- Modify `xic_extractor/discovery/delta_mass_patterns.py`: add source-aware local matcher predicates so local feature-source rows can seed candidates without trace-supported authority.
- Modify `tests/test_detected_feature_sources.py`: add local feature CSV validation tests.
- Modify `tests/test_delta_mass_patterns.py`: add local source role-aware Pt matcher tests.
- Create `xic_extractor/ms1_scan_index.py`: neutral MS1 scan-index model, RAW index builder, cache IO, and index-backed XIC extraction.
- Modify `xic_extractor/alignment/ms1_index_source.py`: import neutral MS1 scan-index primitives and keep alignment-specific wrapper/source classes local.
- Create `tests/test_ms1_scan_index.py`: characterize neutral helper behavior and protect alignment import compatibility.
- Create `xic_extractor/discovery/local_ms1_features.py`: core local feature generator, trace linking, smoothing, baseline area, quality classification, summary/diagnostics models, and writers.
- Create `tests/test_local_ms1_features.py`: synthetic trace-linking, bounded peak-region, assignment-mode diagnostics, peak-quality, writer, summary invariant, and fail-closed output tests.
- Create `scripts/build_local_ms1_feature_table.py`: CLI wrapper around the generator.
- Create `tests/test_build_local_ms1_feature_table_cli.py`: CLI tests with monkeypatched RAW opening/generator calls.
- Modify `docs/product/discovery.md`: absorb durable local MS1 feature source contract.
- Modify `docs/superpowers/validation/productization_status_index_v1.tsv` only if lane state changes; detector/runtime outputs remain `diagnostic_only`.
- Modify `tools/testing/test_shards.py`: add `tests/test_ms1_scan_index.py`, `tests/test_local_ms1_features.py`, and `tests/test_build_local_ms1_feature_table_cli.py` to the explicit shard map when required.

## Architecture Preflight

Goal:
Implement a local MS1 feature source for Delta Mass / Pt Discovery while preserving screening-only authority.

Existing owner/helper to reuse:
`xic_extractor.discovery.detected_features` owns the `DetectedFeature` public input contract; `xic_extractor.discovery.delta_mass_patterns` owns Pt feature-level matching; `xic_extractor.peak_detection.ms1_morphology` owns Gaussian15 smoothing; MS1 scan-index mechanics should move to neutral `xic_extractor.ms1_scan_index`.

New code location:
Core generator in `xic_extractor.discovery.local_ms1_features`; CLI in `scripts/build_local_ms1_feature_table.py`; neutral scan index in `xic_extractor/ms1_scan_index.py`.

Evidence provider role:
The generator emits source-level Discovery screening evidence only. Pt trace/co-elution evidence remains the upgrade path for detection-grade support.

Simplest product rule:
Detect sample-local chromatographic MS1 features from smoothed scan-to-scan m/z traces; base-grade rows are `OK`, support-only rows are `WEAK_SUPPORT`, rejected rows stay diagnostics-only.

Call-cost model:
Open the RAW once, build one in-memory MS1 scan index, link centroid peaks scan-to-scan, and avoid vendor XIC extraction in the generator pass.

Public contracts at risk:
`detected_features.csv`, `trace_quality` semantics, Pt candidate evidence tier/status, new CLI flags, summary JSON schema, diagnostics CSV schema, and productization status wording.

Validation gate:
Synthetic and contract tests first; focused RAW smoke remains diagnostic-only and uses the initial single-RAW diagnostic budget.

Stop rule:
If local feature rows alone can produce `supported`/tier A/score-90 or any Matrix/Backfill/counting authority, stop and fix the matcher contract before continuing.

---

### Task 1: Source-Aware DetectedFeature And Pt Matcher Contract

**Files:**
- Modify: `xic_extractor/discovery/detected_features.py`
- Modify: `xic_extractor/discovery/delta_mass_patterns.py`
- Test: `tests/test_detected_feature_sources.py`
- Test: `tests/test_delta_mass_patterns.py`

**Interfaces:**
- Produces constants in `detected_features.py`:
  - `LOCAL_MS1_FEATURE_SOURCE = "local_ms1_feature"`
  - `LOCAL_MS1_FEATURE_TRACE_QUALITY_OK = "OK"`
  - `LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT = "WEAK_SUPPORT"`
  - `LOCAL_MS1_FEATURE_TRACE_QUALITIES = frozenset({"OK", "WEAK_SUPPORT"})`
  - `is_local_ms1_feature_source(source: str) -> bool`
- Updates `load_detected_features_csv(path: Path) -> tuple[DetectedFeature, ...]` to reject invalid local `trace_quality` values.
- Updates `match_delta_mass_pattern(...) -> tuple[PtPatternCandidate, ...]` so local all-OK patterns emit `feature_source_detected`, tier B, score 70, not `supported`.

- [ ] **Step 1: Add failing local source validation tests**

Add these tests to `tests/test_detected_feature_sources.py`:

```python
def _write_detected_csv(path: Path, row: dict[str, object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETECTED_FEATURES_COLUMNS)
        writer.writeheader()
        writer.writerow({column: row.get(column, "") for column in DETECTED_FEATURES_COLUMNS})


def _local_detected_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_stem": "Sample_1",
        "feature_id": "Sample_1|local_ms1_feature|000001",
        "source_feature_row_id": "Sample_1|local_ms1_feature|000001",
        "mz": "500.0",
        "rt_apex_min": "10.0",
        "rt_start_min": "9.95",
        "rt_end_min": "10.05",
        "height": "1000.0",
        "area": "2500.0",
        "trace_quality": "OK",
        "shape_score": "",
        "source": "local_ms1_feature",
    }
    row.update(overrides)
    return row


def test_local_ms1_feature_detected_csv_accepts_ok_and_weak_support(
    tmp_path: Path,
) -> None:
    ok_csv = tmp_path / "ok.csv"
    weak_csv = tmp_path / "weak.csv"
    _write_detected_csv(ok_csv, _local_detected_row(trace_quality="OK"))
    _write_detected_csv(weak_csv, _local_detected_row(trace_quality="WEAK_SUPPORT"))

    assert load_detected_features_csv(ok_csv)[0].trace_quality == "OK"
    assert load_detected_features_csv(weak_csv)[0].trace_quality == "WEAK_SUPPORT"


def test_local_ms1_feature_detected_csv_rejects_fail_and_unknown_quality(
    tmp_path: Path,
) -> None:
    fail_csv = tmp_path / "fail.csv"
    typo_csv = tmp_path / "typo.csv"
    _write_detected_csv(fail_csv, _local_detected_row(trace_quality="FAIL"))
    _write_detected_csv(typo_csv, _local_detected_row(trace_quality="GOOD"))

    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(fail_csv)
    with pytest.raises(ValueError, match="local_ms1_feature trace_quality"):
        load_detected_features_csv(typo_csv)
```

Also add imports at the top:

```python
import pytest

from xic_extractor.discovery.detected_features import load_detected_features_csv
```

- [ ] **Step 2: Run local source validation tests and verify failure**

Run:

```powershell
python -m pytest tests/test_detected_feature_sources.py::test_local_ms1_feature_detected_csv_accepts_ok_and_weak_support tests/test_detected_feature_sources.py::test_local_ms1_feature_detected_csv_rejects_fail_and_unknown_quality -q
```

Expected: fail because `load_detected_features_csv()` currently accepts any non-empty `trace_quality`.

- [ ] **Step 3: Implement local source constants and validation**

In `xic_extractor/discovery/detected_features.py`, add constants after `_UNSPECIFIED_TRACE_QUALITY`:

```python
LOCAL_MS1_FEATURE_SOURCE = "local_ms1_feature"
LOCAL_MS1_FEATURE_TRACE_QUALITY_OK = "OK"
LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT = "WEAK_SUPPORT"
LOCAL_MS1_FEATURE_TRACE_QUALITIES = frozenset(
    {
        LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
        LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    }
)
```

Add this helper near `_external_source_label()`:

```python
def is_local_ms1_feature_source(source: str) -> bool:
    return source == LOCAL_MS1_FEATURE_SOURCE


def _validate_trace_quality_for_source(
    trace_quality: str,
    *,
    source: str,
    row_label: str,
) -> str:
    if is_local_ms1_feature_source(source) and trace_quality not in LOCAL_MS1_FEATURE_TRACE_QUALITIES:
        allowed = ", ".join(sorted(LOCAL_MS1_FEATURE_TRACE_QUALITIES))
        raise ValueError(
            f"{row_label} has invalid local_ms1_feature trace_quality: "
            f"{trace_quality!r}; expected one of {allowed}"
        )
    return trace_quality
```

Then in `_parse_detected_feature()`, replace the direct `trace_quality=trace_quality` assignment with:

```python
        trace_quality=_validate_trace_quality_for_source(
            trace_quality,
            source=source,
            row_label=row_label,
        ),
```

- [ ] **Step 4: Run validation tests and verify pass**

Run:

```powershell
python -m pytest tests/test_detected_feature_sources.py::test_local_ms1_feature_detected_csv_accepts_ok_and_weak_support tests/test_detected_feature_sources.py::test_local_ms1_feature_detected_csv_rejects_fail_and_unknown_quality -q
```

Expected: pass.

- [ ] **Step 5: Add failing Pt matcher role tests**

Add these tests to `tests/test_delta_mass_patterns.py`:

```python
def test_local_ms1_feature_all_ok_pattern_stays_feature_source_detected() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"
    assert candidates[0].evidence_score == 70


def test_local_ms1_feature_weak_support_can_match_non_base_only() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"


def test_local_ms1_feature_weak_base_is_rejected_even_with_shape_score() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    assert match_delta_mass_pattern(features) == ()


def test_local_ms1_feature_unknown_quality_bypassing_loader_is_fail_closed() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="GOOD",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
    ]

    assert match_delta_mass_pattern(features) == ()


def test_local_ms1_feature_shape_score_cannot_upgrade_tier() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"
    assert candidates[0].evidence_score == 70
```

- [ ] **Step 6: Run matcher tests and verify failure**

Run:

```powershell
python -m pytest tests/test_delta_mass_patterns.py::test_local_ms1_feature_all_ok_pattern_stays_feature_source_detected tests/test_delta_mass_patterns.py::test_local_ms1_feature_weak_support_can_match_non_base_only tests/test_delta_mass_patterns.py::test_local_ms1_feature_weak_base_is_rejected_even_with_shape_score tests/test_delta_mass_patterns.py::test_local_ms1_feature_unknown_quality_bypassing_loader_is_fail_closed tests/test_delta_mass_patterns.py::test_local_ms1_feature_shape_score_cannot_upgrade_tier -q
```

Expected: fail because current matcher treats local `OK` as explicit support and does not accept `WEAK_SUPPORT` as non-base support.

- [ ] **Step 7: Implement source-aware matcher predicates**

In `xic_extractor/discovery/delta_mass_patterns.py`, update the `TYPE_CHECKING` import block to include `is_local_ms1_feature_source`, then add runtime imports inside helpers to avoid cycles.

Add constants near the existing support constants:

```python
_LOCAL_MS1_FEATURE_SOURCE_LEVEL_STATUS = "feature_source_detected"
```

Replace `_local_signal_support_status()` with:

```python
def _local_signal_support_status(
    base: "DetectedFeature", matches: dict[str, _PositionMatch]
) -> str | None:
    if _is_local_ms1_feature(base):
        if not _is_local_ms1_base_supported(base):
            return None
        local_supported_non_base = sum(
            1
            for match in matches.values()
            if _is_local_ms1_non_base_supported(match.feature)
        )
        if local_supported_non_base < _REQUIRED_NON_BASE_SUPPORT_COUNT:
            return None
        return _LOCAL_MS1_FEATURE_SOURCE_LEVEL_STATUS

    if not _is_supported_feature(base):
        return None

    supported_non_base = sum(
        1 for match in matches.values() if _is_supported_feature(match.feature)
    )
    if supported_non_base < _REQUIRED_NON_BASE_SUPPORT_COUNT:
        return None

    explicit_non_base = sum(
        1
        for match in matches.values()
        if _has_explicit_local_signal_support(match.feature)
    )
    if (
        _has_explicit_local_signal_support(base)
        and explicit_non_base >= _REQUIRED_NON_BASE_SUPPORT_COUNT
    ):
        return "supported"
    return "feature_source_detected"
```

Add these helpers below `_is_supported_feature()`:

```python
def _is_local_ms1_feature(feature: "DetectedFeature") -> bool:
    from xic_extractor.discovery.detected_features import is_local_ms1_feature_source

    return is_local_ms1_feature_source(feature.source)


def _is_local_ms1_base_supported(feature: "DetectedFeature") -> bool:
    from xic_extractor.discovery.detected_features import (
        LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
    )

    return (
        _is_local_ms1_feature(feature)
        and feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_OK
    )


def _is_local_ms1_non_base_supported(feature: "DetectedFeature") -> bool:
    from xic_extractor.discovery.detected_features import (
        LOCAL_MS1_FEATURE_TRACE_QUALITIES,
    )

    return (
        _is_local_ms1_feature(feature)
        and feature.trace_quality in LOCAL_MS1_FEATURE_TRACE_QUALITIES
    )
```

Replace `_is_position_match_eligible()` so local base candidates cannot match non-local support rows:

```python
def _is_position_match_eligible(
    base: "DetectedFeature",
    feature: "DetectedFeature",
    expected_label: str,
) -> bool:
    if base.source == _SCAN_PROXY_SOURCE_LABEL:
        base_parsed = _scan_proxy_group_and_label(base.feature_id)
        feature_parsed = _scan_proxy_group_and_label(feature.feature_id)
        if base_parsed is None or feature_parsed is None:
            return False
        base_group, base_label = base_parsed
        feature_group, feature_label = feature_parsed
        return (
            base_label == _BASE_LABEL
            and feature_group == base_group
            and feature_label == expected_label
        )

    if _is_local_ms1_feature(base):
        return _is_local_ms1_non_base_supported(feature)

    return feature.source != _SCAN_PROXY_SOURCE_LABEL
```

- [ ] **Step 8: Run Task 1 tests**

Run:

```powershell
python -m pytest tests/test_detected_feature_sources.py tests/test_delta_mass_patterns.py -q
```

Expected: pass.

### Task 2: Neutral MS1 Scan Index Owner

**Files:**
- Create: `xic_extractor/ms1_scan_index.py`
- Modify: `xic_extractor/alignment/ms1_index_source.py`
- Test: `tests/test_ms1_scan_index.py`

**Interfaces:**
- Produces neutral module exports:
  - `MS1Scan`
  - `MS1_INDEX_CACHE_SCHEMA_VERSION`
  - `build_ms1_scan_index(source: Any) -> tuple[MS1Scan, ...]`
  - `write_ms1_scan_index_npz(path: Path, index: tuple[MS1Scan, ...]) -> Path`
  - `read_ms1_scan_index_npz(path: Path) -> tuple[MS1Scan, ...]`
  - `extract_index_xic(source: Any, index: tuple[MS1Scan, ...], request: XICRequest, *, intensity_mode: Literal["max", "sum"] = "max") -> XICTrace`
- `xic_extractor.alignment.ms1_index_source` continues to export the same names by importing the neutral primitives.

- [ ] **Step 1: Add neutral module tests**

Create `tests/test_ms1_scan_index.py`:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np

from xic_extractor.ms1_scan_index import (
    MS1Scan,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    write_ms1_scan_index_npz,
)
from xic_extractor.xic_models import XICRequest


class _Source:
    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 1, 3


def test_ms1_scan_index_npz_round_trip(tmp_path: Path) -> None:
    index = (
        MS1Scan(
            scan_number=1,
            rt=1.0,
            masses=np.asarray([100.0, 101.0], dtype=np.float64),
            intensities=np.asarray([10.0, 20.0], dtype=np.float64),
        ),
        MS1Scan(
            scan_number=2,
            rt=1.1,
            masses=np.asarray([100.0], dtype=np.float64),
            intensities=np.asarray([30.0], dtype=np.float64),
        ),
    )
    path = tmp_path / "index.npz"

    write_ms1_scan_index_npz(path, index)
    loaded = read_ms1_scan_index_npz(path)

    assert [scan.scan_number for scan in loaded] == [1, 2]
    assert [scan.rt for scan in loaded] == [1.0, 1.1]
    np.testing.assert_allclose(loaded[0].masses, [100.0, 101.0])
    np.testing.assert_allclose(loaded[1].intensities, [30.0])


def test_extract_index_xic_uses_ppm_window_and_max_intensity() -> None:
    index = (
        MS1Scan(
            scan_number=1,
            rt=1.0,
            masses=np.asarray([99.99, 100.0005, 100.0015], dtype=np.float64),
            intensities=np.asarray([5.0, 10.0, 20.0], dtype=np.float64),
        ),
        MS1Scan(
            scan_number=2,
            rt=1.1,
            masses=np.asarray([100.0002, 100.0030], dtype=np.float64),
            intensities=np.asarray([30.0, 7.0], dtype=np.float64),
        ),
    )
    request = XICRequest(mz=100.0, rt_min=0.9, rt_max=1.2, ppm_tol=20.0)

    trace = extract_index_xic(_Source(), index, request)

    np.testing.assert_allclose(trace.rt, [1.0, 1.1])
    np.testing.assert_allclose(trace.intensity, [20.0, 30.0])


def test_build_ms1_scan_index_prefers_segmented_scan_api() -> None:
    class _Header:
        FirstSpectrum = 1
        LastSpectrum = 1

    class _Filter:
        MSOrder = "Ms"

    class _Segmented:
        Positions = [100.0, 99.0]
        Intensities = [10.0, 5.0]

    class _Raw:
        RunHeaderEx = _Header()

        def GetFilterForScanNumber(self, _scan_number: int) -> _Filter:
            return _Filter()

        def GetSegmentedScanFromScanNumber(self, scan_number: int, _stats):
            assert scan_number == 1
            return _Segmented()

        def GetSimplifiedScan(self, _scan_number: int):  # pragma: no cover - must not be called
            raise AssertionError("segmented API should be preferred")

        def RetentionTimeFromScanNumber(self, _scan_number: int) -> float:
            return 2.5

    index = build_ms1_scan_index(_Raw())

    assert len(index) == 1
    assert index[0].scan_number == 1
    assert index[0].rt == 2.5
    np.testing.assert_allclose(index[0].masses, [99.0, 100.0])
    np.testing.assert_allclose(index[0].intensities, [5.0, 10.0])
```

- [ ] **Step 2: Run neutral module tests and verify failure**

Run:

```powershell
python -m pytest tests/test_ms1_scan_index.py -q
```

Expected: fail because `xic_extractor.ms1_scan_index` does not exist.

- [ ] **Step 3: Create neutral MS1 scan-index module**

Create `xic_extractor/ms1_scan_index.py` by moving the neutral pieces from `xic_extractor/alignment/ms1_index_source.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.xic_models import XICRequest, XICTrace

MS1IndexIntensityMode = Literal["max", "sum"]
MS1_INDEX_CACHE_SCHEMA_VERSION = "ms1_scan_index_npz_v1"


@dataclass(frozen=True)
class MS1Scan:
    scan_number: int
    rt: float
    masses: NDArray[np.float64]
    intensities: NDArray[np.float64]


def build_ms1_scan_index(source: Any) -> tuple[MS1Scan, ...]:
    raw_file = _raw_file(source)
    header = raw_file.RunHeaderEx
    scans: list[MS1Scan] = []
    for scan_number in range(int(header.FirstSpectrum), int(header.LastSpectrum) + 1):
        filter_obj = raw_file.GetFilterForScanNumber(scan_number)
        if str(getattr(filter_obj, "MSOrder", "")) != "Ms":
            continue
        masses, intensities = _scan_arrays(raw_file, scan_number)
        if (
            masses.ndim != 1
            or intensities.ndim != 1
            or masses.shape != intensities.shape
        ):
            continue
        scans.append(
            MS1Scan(
                scan_number=scan_number,
                rt=float(raw_file.RetentionTimeFromScanNumber(scan_number)),
                masses=masses,
                intensities=intensities,
            )
        )
    return tuple(scans)


def write_ms1_scan_index_npz(path: Path, index: tuple[MS1Scan, ...]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets: NDArray[np.int64] = np.zeros(len(index) + 1, dtype=np.int64)
    scan_numbers: NDArray[np.int64] = np.empty(len(index), dtype=np.int64)
    rts: NDArray[np.float64] = np.empty(len(index), dtype=np.float64)
    mass_arrays: list[NDArray[np.float64]] = []
    intensity_arrays: list[NDArray[np.float64]] = []
    position = 0
    for scan_index, scan in enumerate(index):
        scan_numbers[scan_index] = scan.scan_number
        rts[scan_index] = scan.rt
        masses = np.asarray(scan.masses, dtype=np.float64)
        intensities = np.asarray(scan.intensities, dtype=np.float64)
        if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
            raise ValueError("MS1 scan cache arrays must be matching 1D arrays")
        mass_arrays.append(masses)
        intensity_arrays.append(intensities)
        position += int(masses.size)
        offsets[scan_index + 1] = position
    masses_concat = np.concatenate(mass_arrays) if mass_arrays else np.asarray([], dtype=np.float64)
    intensities_concat = np.concatenate(intensity_arrays) if intensity_arrays else np.asarray([], dtype=np.float64)
    np.savez(
        path,
        schema_version=np.asarray([MS1_INDEX_CACHE_SCHEMA_VERSION]),
        scan_number=scan_numbers,
        rt=rts,
        offsets=offsets,
        masses=masses_concat,
        intensities=intensities_concat,
    )
    return path


def read_ms1_scan_index_npz(path: Path) -> tuple[MS1Scan, ...]:
    with np.load(path, allow_pickle=False) as payload:
        schema_version = str(payload["schema_version"][0])
        if schema_version != MS1_INDEX_CACHE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported MS1 index cache schema version: "
                f"{schema_version}"
            )
        scan_numbers = np.asarray(payload["scan_number"], dtype=np.int64)
        rts = np.asarray(payload["rt"], dtype=np.float64)
        offsets = np.asarray(payload["offsets"], dtype=np.int64)
        masses = np.asarray(payload["masses"], dtype=np.float64)
        intensities = np.asarray(payload["intensities"], dtype=np.float64)
    if scan_numbers.ndim != 1 or rts.ndim != 1 or scan_numbers.shape != rts.shape:
        raise ValueError("MS1 index cache scan metadata is malformed")
    if offsets.ndim != 1 or len(offsets) != len(scan_numbers) + 1:
        raise ValueError("MS1 index cache offsets are malformed")
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        raise ValueError("MS1 index cache intensity arrays are malformed")
    if len(offsets) and (offsets[0] != 0 or offsets[-1] != len(masses)):
        raise ValueError("MS1 index cache offsets do not match payload length")
    scans: list[MS1Scan] = []
    for index, scan_number in enumerate(scan_numbers):
        start = int(offsets[index])
        end = int(offsets[index + 1])
        if start > end:
            raise ValueError("MS1 index cache offsets are not monotonic")
        scans.append(
            MS1Scan(
                scan_number=int(scan_number),
                rt=float(rts[index]),
                masses=masses[start:end],
                intensities=intensities[start:end],
            )
        )
    return tuple(scans)


def extract_index_xic(
    source: Any,
    index: tuple[MS1Scan, ...],
    request: XICRequest,
    *,
    intensity_mode: MS1IndexIntensityMode = "max",
) -> XICTrace:
    start_scan, end_scan = _scan_window(source, request)
    tolerance = request.mz * request.ppm_tol / 1e6
    rt_values: list[float] = []
    intensity_values: list[float] = []
    for scan in index:
        if scan.scan_number < start_scan or scan.scan_number > end_scan:
            continue
        left = int(np.searchsorted(scan.masses, request.mz - tolerance, side="left"))
        right = int(np.searchsorted(scan.masses, request.mz + tolerance, side="right"))
        intensity = _window_intensity(
            scan.intensities[left:right],
            intensity_mode=intensity_mode,
        )
        rt_values.append(scan.rt)
        intensity_values.append(intensity)
    return XICTrace.from_arrays(rt_values, intensity_values)


def _raw_file(source: Any) -> Any:
    raw_file = getattr(source, "_raw_file", None)
    return raw_file if raw_file is not None else source


def _scan_arrays(raw_file: Any, scan_number: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if hasattr(raw_file, "GetSegmentedScanFromScanNumber"):
        scan = raw_file.GetSegmentedScanFromScanNumber(scan_number, None)
        masses = np.asarray(getattr(scan, "Positions", []), dtype=np.float64)
    else:
        scan = raw_file.GetSimplifiedScan(scan_number)
        masses = np.asarray(getattr(scan, "Masses", []), dtype=np.float64)
    intensities = np.asarray(getattr(scan, "Intensities", []), dtype=np.float64)
    order = np.argsort(masses)
    return masses[order], intensities[order]


def _scan_window(source: Any, request: XICRequest) -> tuple[int, int]:
    resolver = getattr(source, "scan_window_for_request", None)
    if callable(resolver):
        start_scan, end_scan = resolver(request)
        return int(start_scan), int(end_scan)
    raw_file = _raw_file(source)
    return (
        int(raw_file.ScanNumberFromRetentionTime(request.rt_min)),
        int(raw_file.ScanNumberFromRetentionTime(request.rt_max)),
    )


def _window_intensity(
    intensities: NDArray[np.float64],
    *,
    intensity_mode: MS1IndexIntensityMode,
) -> float:
    if len(intensities) == 0:
        return 0.0
    if intensity_mode == "max":
        return float(np.max(intensities))
    if intensity_mode == "sum":
        return float(np.sum(intensities))
    raise ValueError(f"unsupported MS1 index intensity mode: {intensity_mode}")
```

- [ ] **Step 4: Update alignment module to import neutral primitives**

In `xic_extractor/alignment/ms1_index_source.py`, replace local definitions of `MS1Scan`, `MS1IndexIntensityMode`, `MS1_INDEX_CACHE_SCHEMA_VERSION`, `build_ms1_scan_index`, `write_ms1_scan_index_npz`, `read_ms1_scan_index_npz`, `extract_index_xic`, `_raw_file`, `_scan_arrays`, `_scan_window`, and `_window_intensity` with imports:

```python
from xic_extractor.ms1_scan_index import (
    MS1_INDEX_CACHE_SCHEMA_VERSION,
    MS1IndexIntensityMode,
    MS1Scan,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    write_ms1_scan_index_npz,
)
```

Keep `MS1IndexedRawSource`, `RawSuperWindowSource`, source factories, and super-window grouping in the alignment module.

- [ ] **Step 5: Run neutral and alignment-related tests**

Run:

```powershell
python -m pytest tests/test_ms1_scan_index.py tests/test_alignment_ms1_index_source.py -q
```

If this repo has an additional process-backend integration test for `ms1_index`, also run:

```powershell
python -m pytest tests/test_alignment_process_backend.py -q
```

Expected: pass.

### Task 3: Local MS1 Feature Generator Core

**Files:**
- Create: `xic_extractor/discovery/local_ms1_features.py`
- Test: `tests/test_local_ms1_features.py`

**Interfaces:**
- Produces dataclasses:
  - `LocalMs1FeatureSettings`
  - `LocalMs1FeatureOutputs`
  - `LocalMs1FeatureSummary`
  - `LocalMs1FeatureDiagnosticRow`
- Produces core function:
  - `build_local_ms1_feature_table(index: tuple[MS1Scan, ...], *, sample_stem: str, raw_path: str, raw_file_size_bytes: int | None = None, raw_file_mtime_ns: int | None = None, settings: LocalMs1FeatureSettings | None = None) -> tuple[tuple[DetectedFeature, ...], LocalMs1FeatureSummary, tuple[LocalMs1FeatureDiagnosticRow, ...]]`
- Produces writer:
  - `write_local_ms1_feature_outputs(output_dir: Path, features: Sequence[DetectedFeature], summary: LocalMs1FeatureSummary, diagnostics: Sequence[LocalMs1FeatureDiagnosticRow]) -> LocalMs1FeatureOutputs`

- [ ] **Step 1: Add synthetic generator tests**

Create `tests/test_local_ms1_features.py`:

```python
from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
    _local_linear_baseline_area,
    _peak_regions_from_smoothed_trace,
    _validate_summary,
    write_local_ms1_feature_outputs,
)
from xic_extractor.ms1_scan_index import MS1Scan


def _scan(scan_number: int, rt: float, peaks: list[tuple[float, float]]) -> MS1Scan:
    return MS1Scan(
        scan_number=scan_number,
        rt=rt,
        masses=np.asarray([mz for mz, _intensity in peaks], dtype=np.float64),
        intensities=np.asarray([intensity for _mz, intensity in peaks], dtype=np.float64),
    )


def test_build_local_ms1_feature_table_emits_ok_and_weak_support_features() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0), (501.0000, 2.0)]),
        _scan(2, 1.05, [(500.0002, 40.0), (501.0002, 5.0)]),
        _scan(3, 1.10, [(500.0001, 100.0), (501.0001, 10.0)]),
        _scan(4, 1.15, [(500.0002, 45.0), (501.0002, 5.0)]),
        _scan(5, 1.20, [(500.0000, 10.0), (501.0000, 2.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        settings=LocalMs1FeatureSettings(
            min_ok_prominence=20.0,
            weak_support_prominence=3.0,
        ),
    )

    assert [feature.trace_quality for feature in features] == ["OK", "WEAK_SUPPORT"]
    assert features[0].feature_id == "Sample_1|local_ms1_feature|000001"
    assert features[0].source_feature_row_id == features[0].feature_id
    assert features[0].source == "local_ms1_feature"
    assert features[0].height > 0
    assert features[0].area is not None and features[0].area > 0
    assert summary.detected_features_rows == 2
    assert summary.ok_feature_count == 1
    assert summary.weak_support_feature_count == 1
    assert summary.fail_count == len(diagnostics)
    assert summary.detected_features_rows == (
        summary.ok_feature_count + summary.weak_support_feature_count
    )


def test_local_ms1_feature_table_rejects_edge_apex_to_diagnostics() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 100.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
    )

    assert features == ()
    assert summary.detected_features_rows == 0
    assert summary.fail_count == 1
    assert diagnostics[0].reason == "apex_at_region_edge"


def test_peak_regions_split_only_when_valley_is_low_enough() -> None:
    settings = LocalMs1FeatureSettings(min_observed_scans=3)
    scan_numbers = (1, 2, 3, 4, 5)
    rts = np.asarray([1.00, 1.05, 1.10, 1.15, 1.20], dtype=float)
    mzs = np.asarray([500.0, 500.0, 500.0, 500.0, 500.0], dtype=float)
    intensities = np.asarray([0.0, 10.0, 4.0, 9.0, 0.0], dtype=float)

    split_regions = _peak_regions_from_smoothed_trace(
        trace_id="Sample_1|trace|000001",
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=intensities,
        internal_missing_scan_count=0,
        candidate_edge_count=4,
        assignment_mode="optimal_component",
        component_size=3,
        component_edge_density=0.5,
        assignment_degraded_large_component=False,
        settings=settings,
    )
    no_split_regions = _peak_regions_from_smoothed_trace(
        trace_id="Sample_1|trace|000001",
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=np.asarray([0.0, 10.0, 6.0, 9.0, 0.0], dtype=float),
        internal_missing_scan_count=0,
        candidate_edge_count=4,
        assignment_mode="optimal_component",
        component_size=3,
        component_edge_density=0.5,
        assignment_degraded_large_component=False,
        settings=settings,
    )

    assert len(split_regions) == 2
    assert all(region.split_peak for region in split_regions)
    assert len(no_split_regions) == 1
    assert no_split_regions[0].split_peak is False


def test_assignment_components_record_degraded_dense_and_sparse_modes() -> None:
    dense_index = (
        _scan(1, 1.00, [(500.0000, 10.0), (500.0004, 9.0)]),
        _scan(2, 1.05, [(500.0002, 12.0), (500.0006, 11.0)]),
        _scan(3, 1.10, [(500.0003, 10.0), (500.0007, 9.0)]),
    )

    _features, dense_summary, dense_diagnostics = build_local_ms1_feature_table(
        dense_index,
        sample_stem="Dense",
        raw_path="Dense.raw",
        settings=LocalMs1FeatureSettings(
            optimal_component_max_size=2,
            optimal_component_max_edge_density=0.10,
            min_ok_prominence=999.0,
            weak_support_prominence=999.0,
            min_observed_scans=2,
        ),
    )

    assert dense_summary.degraded_assignment_component_count > 0
    assert any(row.assignment_degraded_large_component for row in dense_diagnostics)

    sparse_index = (
        _scan(1, 1.00, [(500.0000, 10.0), (500.0050, 9.0), (500.0100, 8.0)]),
        _scan(2, 1.05, [(500.0002, 12.0), (500.0052, 11.0), (500.0102, 10.0)]),
        _scan(3, 1.10, [(500.0003, 10.0), (500.0053, 9.0), (500.0103, 8.0)]),
    )
    _features, sparse_summary, _diagnostics = build_local_ms1_feature_table(
        sparse_index,
        sample_stem="Sparse",
        raw_path="Sparse.raw",
        settings=LocalMs1FeatureSettings(
            optimal_component_max_size=2,
            optimal_component_max_edge_density=0.80,
            min_observed_scans=2,
        ),
    )

    assert sparse_summary.conflict_component_count > 0
    assert sparse_summary.degraded_assignment_component_count == 0


def test_missing_scan_tolerance_counts_internal_gap_not_trailing_gap() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, []),
        _scan(3, 1.10, [(500.0002, 50.0)]),
        _scan(4, 1.15, []),
    )

    _features, _summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Gap",
        raw_path="Gap.raw",
        settings=LocalMs1FeatureSettings(
            max_missing_scans=1,
            min_observed_scans=2,
            min_ok_prominence=999.0,
            weak_support_prominence=999.0,
        ),
    )

    assert diagnostics[0].missing_scan_count == 1
    assert diagnostics[0].gap_fraction == pytest.approx(1 / 3)


def test_local_linear_baseline_area_has_numeric_contract() -> None:
    rts = np.asarray([1.0, 2.0, 3.0], dtype=float)
    smoothed = np.asarray([10.0, 30.0, 10.0], dtype=float)

    assert _local_linear_baseline_area(rts, smoothed, 0, 2) == pytest.approx(1200.0)


def test_summary_invariant_failure_raises() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )
    _features, summary, _diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        settings=LocalMs1FeatureSettings(min_ok_prominence=5.0),
    )

    with pytest.raises(ValueError, match="summary count invariant"):
        _validate_summary(replace(summary, detected_features_rows=summary.detected_features_rows + 1))


def test_write_local_ms1_feature_outputs_writes_contract_files(tmp_path: Path) -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )
    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        settings=LocalMs1FeatureSettings(min_ok_prominence=5.0),
    )

    outputs = write_local_ms1_feature_outputs(tmp_path, features, summary, diagnostics)

    assert outputs.detected_features_csv == tmp_path / "detected_features.csv"
    assert outputs.summary_json == tmp_path / "local_ms1_feature_summary.json"
    assert outputs.diagnostics_csv == tmp_path / "local_ms1_feature_diagnostics.csv"
    with outputs.detected_features_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["source"] == "local_ms1_feature"
    payload = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "local_ms1_feature_summary.v1"
    assert payload["area_contract"] == "screening_only"
    assert payload["quant_ready"] is False
```

- [ ] **Step 2: Run generator tests and verify failure**

Run:

```powershell
python -m pytest tests/test_local_ms1_features.py -q
```

Expected: fail because `xic_extractor.discovery.local_ms1_features` does not exist.

- [ ] **Step 3: Implement generator models and settings**

Create `xic_extractor/discovery/local_ms1_features.py` with these imports and dataclasses:

```python
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from xic_extractor.discovery.detected_features import (
    LOCAL_MS1_FEATURE_SOURCE,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    DetectedFeature,
    write_detected_features_csv,
)
from xic_extractor.ms1_scan_index import MS1Scan
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
    morphology_trace_effective_points,
)
from xic_extractor.tabular_io import write_delimited_rows

SUMMARY_SCHEMA_VERSION = "local_ms1_feature_summary.v1"
DIAGNOSTICS_COLUMNS = (
    "trace_id",
    "mz_apex",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "observed_scan_count",
    "missing_scan_count",
    "gap_fraction",
    "mz_ppm_std",
    "prominence",
    "boundary_quality",
    "assignment_mode",
    "component_size",
    "component_edge_density",
    "assignment_degraded_large_component",
    "split_peak",
    "overlap_suspected",
    "reason",
)


@dataclass(frozen=True)
class LocalMs1FeatureSettings:
    feature_link_ppm: float = 20.0
    max_missing_scans: int = 1
    min_observed_scans: int = 3
    min_duration_min: float = 0.03
    max_duration_min: float = 1.5
    gaussian_window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS
    min_ok_prominence: float = 20.0
    weak_support_prominence: float = 3.0
    max_ok_gap_fraction: float = 0.20
    max_weak_gap_fraction: float = 0.35
    max_ok_mz_ppm_std: float = 10.0
    max_weak_mz_ppm_std: float = 20.0
    optimal_component_max_size: int = 12
    optimal_component_max_edge_density: float = 0.35
    split_valley_fraction_of_smaller_apex: float = 0.50


@dataclass(frozen=True)
class LocalMs1FeatureSummary:
    schema_version: str
    raw_path: str
    raw_file_name: str
    raw_file_size_bytes: int | None
    raw_file_mtime_ns: int | None
    sample_stem: str
    extraction_backend: str
    ms1_scan_selection_rule: str
    generator_version: str
    trace_builder_mode: str
    parameters: Mapping[str, object]
    parameter_hash: str
    wall_time_sec: float
    smoothing_kernel_name: str
    smoothing_kernel_scans: int
    median_ms1_scan_interval_sec: float | None
    effective_smoothing_seconds: float | None
    ms1_scan_count: int
    centroid_peak_count: int
    candidate_edge_count: int
    conflict_component_count: int
    degraded_assignment_component_count: int
    degraded_assignment_component_fraction: float
    total_traces: int
    total_peak_regions: int
    ok_feature_count: int
    weak_support_feature_count: int
    fail_count: int
    detected_features_rows: int
    diagnostics_row_count: int
    height_semantics: str
    area_semantics: str
    area_contract: str
    quant_ready: bool


@dataclass(frozen=True)
class LocalMs1FeatureDiagnosticRow:
    trace_id: str
    mz_apex: float | None
    rt_apex_min: float | None
    rt_start_min: float | None
    rt_end_min: float | None
    observed_scan_count: int
    missing_scan_count: int
    gap_fraction: float
    mz_ppm_std: float | None
    prominence: float | None
    boundary_quality: float | None
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool
    split_peak: bool
    overlap_suspected: bool
    reason: str


@dataclass(frozen=True)
class LocalMs1FeatureOutputs:
    detected_features_csv: Path
    summary_json: Path
    diagnostics_csv: Path
```

- [ ] **Step 4: Implement trace linking and quality helpers**

Add these internal dataclasses and helpers:

```python
@dataclass
class _TraceBuilder:
    trace_id: int
    points: list[tuple[int, float, float, float]]
    trailing_missing_scans: int = 0
    internal_missing_scan_count: int = 0
    assignment_mode: str = "new_trace"
    component_size: int = 1
    component_edge_density: float = 0.0
    assignment_degraded_large_component: bool = False

    @property
    def last_mz(self) -> float:
        return self.points[-1][2]

    @property
    def last_scan_number(self) -> int:
        return self.points[-1][0]


@dataclass(frozen=True)
class _AssignmentEdge:
    ppm_error_abs: float
    trace_index: int
    peak_index: int
    intensity: float
    scan_gap: int
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool


@dataclass(frozen=True)
class _PeakRegion:
    trace_id: str
    scan_numbers: tuple[int, ...]
    rts: np.ndarray
    mzs: np.ndarray
    intensities: np.ndarray
    smoothed: np.ndarray
    apex_index: int
    left_index: int
    right_index: int
    missing_scan_count: int
    candidate_edge_count: int
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool
    split_peak: bool
    overlap_suspected: bool


def _ppm_error(observed_mz: float, expected_mz: float) -> float:
    return (observed_mz - expected_mz) / expected_mz * 1e6


def _parameter_hash(settings: LocalMs1FeatureSettings) -> str:
    payload = json.dumps(asdict(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _median_scan_interval_sec(index: tuple[MS1Scan, ...]) -> float | None:
    if len(index) < 2:
        return None
    diffs = np.diff(np.asarray([scan.rt for scan in index], dtype=float)) * 60.0
    if len(diffs) == 0:
        return None
    return float(np.median(diffs))


def _local_linear_baseline_area(rts: np.ndarray, smoothed: np.ndarray, left: int, right: int) -> float:
    if right <= left:
        return 0.0
    left_value = float(smoothed[left])
    right_value = float(smoothed[right])
    segment_rt = rts[left : right + 1]
    if len(segment_rt) < 2:
        return 0.0
    baseline = np.linspace(left_value, right_value, len(segment_rt))
    residual = np.maximum(smoothed[left : right + 1] - baseline, 0.0)
    return float(np.trapezoid(residual, segment_rt)) * 60.0


def _boundary_quality(smoothed: np.ndarray, apex_index: int, left: int, right: int) -> float:
    apex = float(smoothed[apex_index])
    if apex <= 0:
        return 0.0
    edge = max(float(smoothed[left]), float(smoothed[right]))
    return max(0.0, 1.0 - edge / apex)


def _prominence(smoothed: np.ndarray, apex_index: int, left: int, right: int) -> float:
    apex = float(smoothed[apex_index])
    edge = max(float(smoothed[left]), float(smoothed[right]))
    return max(0.0, apex - edge)


def _peak_regions_from_smoothed_trace(
    *,
    trace_id: str,
    scan_numbers: tuple[int, ...],
    rts: np.ndarray,
    mzs: np.ndarray,
    intensities: np.ndarray,
    smoothed: np.ndarray,
    internal_missing_scan_count: int,
    candidate_edge_count: int,
    assignment_mode: str,
    component_size: int,
    component_edge_density: float,
    assignment_degraded_large_component: bool,
    settings: LocalMs1FeatureSettings,
) -> tuple[_PeakRegion, ...]:
    if len(smoothed) < settings.min_observed_scans:
        return ()
    apices = _local_apex_indices(smoothed)
    if not apices:
        apices = (int(np.argmax(smoothed)),)
    boundaries = _split_boundaries(smoothed, apices, settings)
    regions: list[_PeakRegion] = []
    split_peak = len(boundaries) > 1
    for left, apex_index, right in boundaries:
        if right - left + 1 < settings.min_observed_scans:
            continue
        if apex_index in {left, right}:
            regions.append(
                _PeakRegion(
                    trace_id=trace_id,
                    scan_numbers=scan_numbers[left : right + 1],
                    rts=rts[left : right + 1],
                    mzs=mzs[left : right + 1],
                    intensities=intensities[left : right + 1],
                    smoothed=smoothed[left : right + 1],
                    apex_index=apex_index - left,
                    left_index=0,
                    right_index=right - left,
                    missing_scan_count=internal_missing_scan_count,
                    candidate_edge_count=candidate_edge_count,
                    assignment_mode=assignment_mode,
                    component_size=component_size,
                    component_edge_density=component_edge_density,
                    assignment_degraded_large_component=assignment_degraded_large_component,
                    split_peak=split_peak,
                    overlap_suspected=not split_peak and len(apices) > 1,
                )
            )
            continue
        regions.append(
            _PeakRegion(
                trace_id=trace_id,
                scan_numbers=scan_numbers[left : right + 1],
                rts=rts[left : right + 1],
                mzs=mzs[left : right + 1],
                intensities=intensities[left : right + 1],
                smoothed=smoothed[left : right + 1],
                apex_index=apex_index - left,
                left_index=0,
                right_index=right - left,
                missing_scan_count=internal_missing_scan_count,
                candidate_edge_count=candidate_edge_count,
                assignment_mode=assignment_mode,
                component_size=component_size,
                component_edge_density=component_edge_density,
                assignment_degraded_large_component=assignment_degraded_large_component,
                split_peak=split_peak,
                overlap_suspected=not split_peak and len(apices) > 1,
            )
        )
    return tuple(regions)


def _local_apex_indices(smoothed: np.ndarray) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(1, len(smoothed) - 1)
        if smoothed[index] >= smoothed[index - 1] and smoothed[index] > smoothed[index + 1]
    )


def _split_boundaries(
    smoothed: np.ndarray,
    apices: tuple[int, ...],
    settings: LocalMs1FeatureSettings,
) -> tuple[tuple[int, int, int], ...]:
    if len(apices) <= 1:
        apex_index = apices[0]
        return ((0, apex_index, len(smoothed) - 1),)
    boundaries: list[tuple[int, int, int]] = []
    left = 0
    current_apex = apices[0]
    for next_apex in apices[1:]:
        valley_index = current_apex + int(np.argmin(smoothed[current_apex : next_apex + 1]))
        valley = float(smoothed[valley_index])
        smaller_apex = min(float(smoothed[current_apex]), float(smoothed[next_apex]))
        should_split = valley <= smaller_apex * settings.split_valley_fraction_of_smaller_apex
        if should_split:
            boundaries.append((left, current_apex, valley_index))
            left = valley_index
            current_apex = next_apex
        elif smoothed[next_apex] > smoothed[current_apex]:
            current_apex = next_apex
    boundaries.append((left, current_apex, len(smoothed) - 1))
    return tuple(boundaries)
```

Then implement `_link_traces()` with bounded hybrid assignment while recording real component metadata on each trace:

```python
def _link_traces(
    index: tuple[MS1Scan, ...],
    settings: LocalMs1FeatureSettings,
) -> tuple[list[_TraceBuilder], int, int, int]:
    active: list[_TraceBuilder] = []
    complete: list[_TraceBuilder] = []
    next_trace_id = 1
    candidate_edge_count = 0
    conflict_component_count = 0
    degraded_component_count = 0

    for scan in index:
        peaks = [
            (float(mz), float(intensity))
            for mz, intensity in zip(scan.masses, scan.intensities, strict=True)
            if float(intensity) > 0
        ]
        assigned_traces: set[int] = set()
        assigned_peaks: set[int] = set()
        edges: list[_AssignmentEdge] = []
        for trace_index, trace in enumerate(active):
            tolerance = trace.last_mz * settings.feature_link_ppm * 1e-6
            for peak_index, (mz, intensity) in enumerate(peaks):
                if abs(mz - trace.last_mz) <= tolerance:
                    candidate_edge_count += 1
                    edges.append(
                        _AssignmentEdge(
                            ppm_error_abs=abs(_ppm_error(mz, trace.last_mz)),
                            trace_index=trace_index,
                            peak_index=peak_index,
                            intensity=intensity,
                            scan_gap=max(1, int(scan.scan_number) - trace.last_scan_number),
                            assignment_mode="unresolved",
                            component_size=0,
                            component_edge_density=0.0,
                            assignment_degraded_large_component=False,
                        )
                    )
        components = _edge_components(edges)
        conflict_component_count += len(components)
        for component in components:
            component_trace_count = len({edge.trace_index for edge in component})
            component_peak_count = len({edge.peak_index for edge in component})
            component_size = component_trace_count + component_peak_count
            component_edge_density = len(component) / max(
                1,
                component_trace_count * component_peak_count,
            )
            use_optimal = (
                component_size <= settings.optimal_component_max_size
                or component_edge_density <= settings.optimal_component_max_edge_density
            )
            if use_optimal:
                assignment_mode = "optimal_component"
                degraded = False
                chosen_edges = _optimal_component_assignment(component)
            else:
                assignment_mode = "greedy_large_dense_component"
                degraded = True
                degraded_component_count += 1
                chosen_edges = _greedy_component_assignment(component)
            for edge in chosen_edges:
                if edge.trace_index in assigned_traces or edge.peak_index in assigned_peaks:
                    continue
                trace = active[edge.trace_index]
                mz, intensity = peaks[edge.peak_index]
                trace.points.append((scan.scan_number, scan.rt, mz, intensity))
                if trace.trailing_missing_scans:
                    trace.internal_missing_scan_count += trace.trailing_missing_scans
                trace.trailing_missing_scans = 0
                trace.assignment_mode = assignment_mode
                trace.component_size = component_size
                trace.component_edge_density = component_edge_density
                trace.assignment_degraded_large_component = degraded
                assigned_traces.add(edge.trace_index)
                assigned_peaks.add(edge.peak_index)
        still_active: list[_TraceBuilder] = []
        for trace_index, trace in enumerate(active):
            if trace_index in assigned_traces:
                still_active.append(trace)
                continue
            trace.trailing_missing_scans += 1
            if trace.trailing_missing_scans <= settings.max_missing_scans:
                still_active.append(trace)
            else:
                complete.append(trace)
        for peak_index, (mz, intensity) in enumerate(peaks):
            if peak_index in assigned_peaks:
                continue
            still_active.append(
                _TraceBuilder(
                    trace_id=next_trace_id,
                    points=[(scan.scan_number, scan.rt, mz, intensity)],
                )
            )
            next_trace_id += 1
        active = still_active
    complete.extend(active)
    return complete, candidate_edge_count, conflict_component_count, degraded_component_count
```

Add component helpers after `_link_traces()`:

```python
def _edge_components(edges: Sequence[_AssignmentEdge]) -> list[list[_AssignmentEdge]]:
    remaining = list(edges)
    components: list[list[_AssignmentEdge]] = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        trace_ids = {seed.trace_index}
        peak_ids = {seed.peak_index}
        changed = True
        while changed:
            changed = False
            kept: list[_AssignmentEdge] = []
            for edge in remaining:
                if edge.trace_index in trace_ids or edge.peak_index in peak_ids:
                    component.append(edge)
                    trace_ids.add(edge.trace_index)
                    peak_ids.add(edge.peak_index)
                    changed = True
                else:
                    kept.append(edge)
            remaining = kept
        components.append(component)
    return components


def _edge_sort_key(edge: _AssignmentEdge) -> tuple[float, int, float]:
    return (edge.ppm_error_abs, edge.scan_gap, -edge.intensity)


def _greedy_component_assignment(
    component: Sequence[_AssignmentEdge],
) -> tuple[_AssignmentEdge, ...]:
    selected: list[_AssignmentEdge] = []
    used_traces: set[int] = set()
    used_peaks: set[int] = set()
    for edge in sorted(component, key=_edge_sort_key):
        if edge.trace_index in used_traces or edge.peak_index in used_peaks:
            continue
        selected.append(edge)
        used_traces.add(edge.trace_index)
        used_peaks.add(edge.peak_index)
    return tuple(selected)


def _optimal_component_assignment(
    component: Sequence[_AssignmentEdge],
) -> tuple[_AssignmentEdge, ...]:
    ordered = tuple(sorted(component, key=_edge_sort_key))
    best: tuple[tuple[float, float, float, float], tuple[_AssignmentEdge, ...]] | None = None

    def _search(
        index: int,
        selected: tuple[_AssignmentEdge, ...],
        used_traces: frozenset[int],
        used_peaks: frozenset[int],
    ) -> None:
        nonlocal best
        if index >= len(ordered):
            score = (
                -float(len(selected)),
                sum(edge.ppm_error_abs for edge in selected),
                float(sum(edge.scan_gap for edge in selected)),
                -sum(edge.intensity for edge in selected),
            )
            candidate = (
                score,
                selected,
            )
            if best is None or candidate[0] < best[0]:
                best = candidate
            return
        edge = ordered[index]
        _search(index + 1, selected, used_traces, used_peaks)
        if edge.trace_index in used_traces or edge.peak_index in used_peaks:
            return
        _search(
            index + 1,
            (*selected, edge),
            frozenset((*used_traces, edge.trace_index)),
            frozenset((*used_peaks, edge.peak_index)),
        )

    _search(0, (), frozenset(), frozenset())
    return best[1] if best is not None else ()
```

- [ ] **Step 5: Implement build and writer functions**

Add:

```python
def build_local_ms1_feature_table(
    index: tuple[MS1Scan, ...],
    *,
    sample_stem: str,
    raw_path: str,
    raw_file_size_bytes: int | None = None,
    raw_file_mtime_ns: int | None = None,
    settings: LocalMs1FeatureSettings | None = None,
) -> tuple[tuple[DetectedFeature, ...], LocalMs1FeatureSummary, tuple[LocalMs1FeatureDiagnosticRow, ...]]:
    resolved = settings or LocalMs1FeatureSettings()
    start = time.perf_counter()
    traces, edge_count, component_count, degraded_count = _link_traces(index, resolved)
    features: list[DetectedFeature] = []
    diagnostics: list[LocalMs1FeatureDiagnosticRow] = []
    peak_region_count = 0

    for trace in traces:
        trace_id = f"{sample_stem}|trace|{trace.trace_id:06d}"
        if len(trace.points) < resolved.min_observed_scans:
            diagnostics.append(_diagnostic_from_trace(trace_id, trace, "too_few_observed_scans"))
            peak_region_count += 1
            continue
        scan_numbers = tuple(point[0] for point in trace.points)
        rts = np.asarray([point[1] for point in trace.points], dtype=float)
        mzs = np.asarray([point[2] for point in trace.points], dtype=float)
        intensities = np.asarray([point[3] for point in trace.points], dtype=float)
        smoothed = gaussian15_morphology_trace(
            intensities,
            window_points=resolved.gaussian_window_points,
        )
        regions = _peak_regions_from_smoothed_trace(
            trace_id=trace_id,
            scan_numbers=scan_numbers,
            rts=rts,
            mzs=mzs,
            intensities=intensities,
            smoothed=smoothed,
            internal_missing_scan_count=trace.internal_missing_scan_count,
            candidate_edge_count=edge_count,
            assignment_mode=trace.assignment_mode,
            component_size=trace.component_size,
            component_edge_density=trace.component_edge_density,
            assignment_degraded_large_component=trace.assignment_degraded_large_component,
            settings=resolved,
        )
        if not regions:
            diagnostics.append(_diagnostic_from_trace(trace_id, trace, "no_bounded_peak_region"))
            peak_region_count += 1
            continue
        for region in regions:
            peak_region_count += 1
            quality, reason = _classify_region(region, resolved)
            if quality == "FAIL":
                diagnostics.append(_diagnostic_from_region(region, reason))
                continue
            feature_index = len(features) + 1
            feature_id = f"{sample_stem}|local_ms1_feature|{feature_index:06d}"
            features.append(
                DetectedFeature(
                    sample_stem=sample_stem,
                    feature_id=feature_id,
                    source_feature_row_id=feature_id,
                    mz=float(region.mzs[region.apex_index]),
                    rt_apex_min=float(region.rts[region.apex_index]),
                    rt_start_min=float(region.rts[region.left_index]),
                    rt_end_min=float(region.rts[region.right_index]),
                    height=float(region.smoothed[region.apex_index]),
                    area=_local_linear_baseline_area(
                        region.rts,
                        region.smoothed,
                        region.left_index,
                        region.right_index,
                    ),
                    trace_quality=quality,
                    shape_score=None,
                    source=LOCAL_MS1_FEATURE_SOURCE,
                )
            )

    features.sort(key=lambda item: (item.sample_stem, item.rt_apex_min, item.mz, item.feature_id))
    # Reassign deterministic IDs after sorting.
    sorted_features = []
    for index_id, feature in enumerate(features, start=1):
        feature_id = f"{sample_stem}|local_ms1_feature|{index_id:06d}"
        sorted_features.append(
            DetectedFeature(
                sample_stem=feature.sample_stem,
                feature_id=feature_id,
                source_feature_row_id=feature_id,
                mz=feature.mz,
                rt_apex_min=feature.rt_apex_min,
                rt_start_min=feature.rt_start_min,
                rt_end_min=feature.rt_end_min,
                height=feature.height,
                area=feature.area,
                trace_quality=feature.trace_quality,
                shape_score=feature.shape_score,
                source=feature.source,
            )
        )
    ok_count = sum(1 for feature in sorted_features if feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_OK)
    weak_count = sum(1 for feature in sorted_features if feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT)
    fail_count = len(diagnostics)
    interval = _median_scan_interval_sec(index)
    summary = LocalMs1FeatureSummary(
        schema_version=SUMMARY_SCHEMA_VERSION,
        raw_path=raw_path,
        raw_file_name=Path(raw_path).name,
        raw_file_size_bytes=raw_file_size_bytes,
        raw_file_mtime_ns=raw_file_mtime_ns,
        sample_stem=sample_stem,
        extraction_backend="local_ms1_scan_index",
        ms1_scan_selection_rule="MSOrder == Ms",
        generator_version="local_ms1_feature_generator.v1",
        trace_builder_mode=resolved.trace_builder_mode,
        parameters=asdict(resolved),
        parameter_hash=_parameter_hash(resolved),
        wall_time_sec=time.perf_counter() - start,
        smoothing_kernel_name="gaussian_15",
        smoothing_kernel_scans=resolved.gaussian_window_points,
        median_ms1_scan_interval_sec=interval,
        effective_smoothing_seconds=(
            interval * morphology_trace_effective_points(
                len(index),
                method="gaussian_15",
                window_points=resolved.gaussian_window_points,
            )
            if interval is not None
            else None
        ),
        ms1_scan_count=len(index),
        centroid_peak_count=sum(len(scan.masses) for scan in index),
        candidate_edge_count=edge_count,
        conflict_component_count=component_count,
        degraded_assignment_component_count=degraded_count,
        degraded_assignment_component_fraction=(
            degraded_count / component_count if component_count else 0.0
        ),
        total_traces=len(traces),
        total_peak_regions=peak_region_count,
        ok_feature_count=ok_count,
        weak_support_feature_count=weak_count,
        fail_count=fail_count,
        detected_features_rows=len(sorted_features),
        diagnostics_row_count=len(diagnostics),
        height_semantics="screening_smoothed_height",
        area_semantics="screening_smoothed_baseline_corrected_area",
        area_contract="screening_only",
        quant_ready=False,
    )
    _validate_summary(summary)
    _elapsed = time.perf_counter() - start
    return tuple(sorted_features), summary, tuple(diagnostics)
```

Add:

```python
def _classify_region(
    region: _PeakRegion,
    settings: LocalMs1FeatureSettings,
) -> tuple[str, str]:
    smoothed = region.smoothed
    apex = float(smoothed[region.apex_index])
    if apex <= 0:
        return "FAIL", "non_positive_height"
    if region.apex_index in {region.left_index, region.right_index}:
        return "FAIL", "apex_at_region_edge"
    area = _local_linear_baseline_area(region.rts, smoothed, region.left_index, region.right_index)
    if area <= 0:
        return "FAIL", "non_positive_area"
    duration = float(region.rts[region.right_index] - region.rts[region.left_index])
    if duration < settings.min_duration_min:
        return "FAIL", "duration_below_min"
    if duration > settings.max_duration_min:
        return "FAIL", "duration_above_max"
    gap_fraction = region.missing_scan_count / max(1, len(region.scan_numbers) + region.missing_scan_count)
    ppm_std = _mz_ppm_std(region.mzs)
    prominence = _prominence(smoothed, region.apex_index, region.left_index, region.right_index)
    boundary = _boundary_quality(smoothed, region.apex_index, region.left_index, region.right_index)
    if (
        prominence >= settings.min_ok_prominence
        and gap_fraction <= settings.max_ok_gap_fraction
        and ppm_std <= settings.max_ok_mz_ppm_std
        and boundary > 0
    ):
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_OK, "ok"
    if (
        prominence >= settings.weak_support_prominence
        and gap_fraction <= settings.max_weak_gap_fraction
        and ppm_std <= settings.max_weak_mz_ppm_std
    ):
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT, "weak_support"
    return "FAIL", "quality_below_weak_support"


def _mz_ppm_std(mzs: np.ndarray) -> float:
    mean_mz = float(np.mean(mzs))
    if mean_mz <= 0:
        return math.inf
    return float(np.std((mzs - mean_mz) / mean_mz * 1e6))
```

Add diagnostics and writer:

```python
def _diagnostic_from_trace(
    trace_id: str,
    trace: _TraceBuilder,
    reason: str,
) -> LocalMs1FeatureDiagnosticRow:
    mzs = np.asarray([point[2] for point in trace.points], dtype=float)
    rts = np.asarray([point[1] for point in trace.points], dtype=float)
    return LocalMs1FeatureDiagnosticRow(
        trace_id=trace_id,
        mz_apex=float(mzs[0]) if len(mzs) else None,
        rt_apex_min=float(rts[0]) if len(rts) else None,
        rt_start_min=float(rts[0]) if len(rts) else None,
        rt_end_min=float(rts[-1]) if len(rts) else None,
        observed_scan_count=len(trace.points),
        missing_scan_count=trace.internal_missing_scan_count,
        gap_fraction=trace.internal_missing_scan_count / max(1, len(trace.points) + trace.internal_missing_scan_count),
        mz_ppm_std=_mz_ppm_std(mzs) if len(mzs) else None,
        prominence=None,
        boundary_quality=None,
        assignment_mode=trace.assignment_mode,
        component_size=trace.component_size,
        component_edge_density=trace.component_edge_density,
        assignment_degraded_large_component=trace.assignment_degraded_large_component,
        split_peak=False,
        overlap_suspected=False,
        reason=reason,
    )


def _diagnostic_from_region(
    region: _PeakRegion,
    reason: str,
) -> LocalMs1FeatureDiagnosticRow:
    return LocalMs1FeatureDiagnosticRow(
        trace_id=region.trace_id,
        mz_apex=float(region.mzs[region.apex_index]),
        rt_apex_min=float(region.rts[region.apex_index]),
        rt_start_min=float(region.rts[region.left_index]),
        rt_end_min=float(region.rts[region.right_index]),
        observed_scan_count=len(region.scan_numbers),
        missing_scan_count=region.missing_scan_count,
        gap_fraction=region.missing_scan_count / max(1, len(region.scan_numbers) + region.missing_scan_count),
        mz_ppm_std=_mz_ppm_std(region.mzs),
        prominence=_prominence(region.smoothed, region.apex_index, region.left_index, region.right_index),
        boundary_quality=_boundary_quality(region.smoothed, region.apex_index, region.left_index, region.right_index),
        assignment_mode=region.assignment_mode,
        component_size=region.component_size,
        component_edge_density=region.component_edge_density,
        assignment_degraded_large_component=region.assignment_degraded_large_component,
        split_peak=region.split_peak,
        overlap_suspected=region.overlap_suspected,
        reason=reason,
    )


def _validate_summary(summary: LocalMs1FeatureSummary) -> None:
    if summary.detected_features_rows != summary.ok_feature_count + summary.weak_support_feature_count:
        raise ValueError("local MS1 feature summary count invariant failed")
    if summary.diagnostics_row_count != summary.fail_count:
        raise ValueError("local MS1 feature diagnostics count invariant failed")
    if summary.total_peak_regions != (
        summary.ok_feature_count + summary.weak_support_feature_count + summary.fail_count
    ):
        raise ValueError("local MS1 feature peak-region count invariant failed")
    expected_fraction = (
        summary.degraded_assignment_component_count / summary.conflict_component_count
        if summary.conflict_component_count
        else 0.0
    )
    if not math.isclose(summary.degraded_assignment_component_fraction, expected_fraction):
        raise ValueError("local MS1 feature degraded assignment fraction invariant failed")


def write_local_ms1_feature_outputs(
    output_dir: Path,
    features: Sequence[DetectedFeature],
    summary: LocalMs1FeatureSummary,
    diagnostics: Sequence[LocalMs1FeatureDiagnosticRow],
) -> LocalMs1FeatureOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    detected_features_csv = write_detected_features_csv(
        output_dir / "detected_features.csv",
        features,
    )
    summary_json = output_dir / "local_ms1_feature_summary.json"
    summary_json.write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    diagnostics_csv = output_dir / "local_ms1_feature_diagnostics.csv"
    write_delimited_rows(
        diagnostics_csv,
        (asdict(row) for row in diagnostics),
        DIAGNOSTICS_COLUMNS,
        extrasaction="raise",
    )
    return LocalMs1FeatureOutputs(
        detected_features_csv=detected_features_csv,
        summary_json=summary_json,
        diagnostics_csv=diagnostics_csv,
    )
```

- [ ] **Step 6: Run generator tests**

Run:

```powershell
python -m pytest tests/test_local_ms1_features.py -q
```

Expected: pass. If synthetic peak smoothing turns an expected `OK` into `WEAK_SUPPORT`, adjust only the test settings thresholds, not the production defaults.

### Task 4: CLI, Product Docs, Productization State, And Focused Contracts

**Files:**
- Create: `scripts/build_local_ms1_feature_table.py`
- Test: `tests/test_build_local_ms1_feature_table_cli.py`
- Modify: `docs/product/discovery.md`
- Modify: `docs/superpowers/validation/productization_status_index_v1.tsv` only if lane state changes.
- Modify: `tools/testing/test_shards.py` only if needed.

**Interfaces:**
- CLI:
  - `python -m scripts.build_local_ms1_feature_table --raw <file.raw> --dll-dir <dir> --output-dir <dir>`
  - optional `--sample-stem`
  - optional numeric settings mirroring `LocalMs1FeatureSettings`.
- CLI prints paths to `detected_features.csv`, `local_ms1_feature_summary.json`, and `local_ms1_feature_diagnostics.csv`.

- [ ] **Step 1: Add CLI tests**

Create `tests/test_build_local_ms1_feature_table_cli.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_local_ms1_feature_table
from xic_extractor.discovery.local_ms1_features import LocalMs1FeatureOutputs


def test_cli_builds_local_ms1_feature_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "Sample_1.raw"
    dll_dir = tmp_path / "dll"
    output_dir = tmp_path / "out"
    raw.write_text("", encoding="utf-8")
    dll_dir.mkdir()
    captured: dict[str, object] = {}

    class _Handle:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def _fake_open_raw(raw_path: Path, dll_path: Path):
        captured["raw_path"] = raw_path
        captured["dll_dir"] = dll_path
        return _Handle()

    def _fake_build_index(_handle):
        captured["build_index"] = True
        return ()

    def _fake_build_table(
        index,
        *,
        sample_stem,
        raw_path,
        raw_file_size_bytes,
        raw_file_mtime_ns,
        settings,
    ):
        captured["sample_stem"] = sample_stem
        captured["raw_path_text"] = raw_path
        captured["raw_file_size_bytes"] = raw_file_size_bytes
        captured["raw_file_mtime_ns"] = raw_file_mtime_ns
        captured["settings"] = settings
        return (), object(), ()

    def _fake_write_outputs(out_dir, features, summary, diagnostics):
        out_dir.mkdir(parents=True, exist_ok=True)
        detected = out_dir / "detected_features.csv"
        summary_json = out_dir / "local_ms1_feature_summary.json"
        diagnostics_csv = out_dir / "local_ms1_feature_diagnostics.csv"
        detected.write_text("sample_stem\n", encoding="utf-8")
        summary_json.write_text("{}\n", encoding="utf-8")
        diagnostics_csv.write_text("trace_id\n", encoding="utf-8")
        return LocalMs1FeatureOutputs(detected, summary_json, diagnostics_csv)

    monkeypatch.setattr(build_local_ms1_feature_table, "open_raw", _fake_open_raw)
    monkeypatch.setattr(build_local_ms1_feature_table, "build_ms1_scan_index", _fake_build_index)
    monkeypatch.setattr(build_local_ms1_feature_table, "build_local_ms1_feature_table", _fake_build_table)
    monkeypatch.setattr(build_local_ms1_feature_table, "write_local_ms1_feature_outputs", _fake_write_outputs)

    code = build_local_ms1_feature_table.main(
        [
            "--raw",
            str(raw),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    assert captured["sample_stem"] == "Sample_1"
    assert captured["raw_file_size_bytes"] == 0
    assert isinstance(captured["raw_file_mtime_ns"], int)
    stdout = capsys.readouterr().out
    assert "detected_features.csv" in stdout
    assert "local_ms1_feature_summary.json" in stdout
    assert "local_ms1_feature_diagnostics.csv" in stdout


def test_cli_rejects_missing_raw(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = build_local_ms1_feature_table.main(
        [
            "--raw",
            str(tmp_path / "missing.raw"),
            "--dll-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
    assert "RAW file does not exist" in capsys.readouterr().err
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```powershell
python -m pytest tests/test_build_local_ms1_feature_table_cli.py -q
```

Expected: fail because `scripts.build_local_ms1_feature_table` does not exist.

- [ ] **Step 3: Implement CLI**

Create `scripts/build_local_ms1_feature_table.py`:

```python
"""CLI for building local MS1 detected-feature tables from one RAW file."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
    write_local_ms1_feature_outputs,
)
from xic_extractor.ms1_scan_index import build_ms1_scan_index
from xic_extractor.raw_reader import RawReaderError, open_raw


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a local single-sample MS1 feature table for Delta Mass / "
            "Pt pattern Discovery. Outputs are screening evidence only."
        )
    )
    parser.add_argument("--raw", type=Path, required=True, help="Thermo RAW file.")
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Thermo RawFileReader DLL directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for detected_features.csv and local MS1 diagnostics.",
    )
    parser.add_argument(
        "--sample-stem",
        help="Sample stem for feature IDs. Defaults to the RAW filename stem.",
    )
    parser.add_argument("--feature-link-ppm", type=float, default=20.0)
    parser.add_argument("--max-missing-scans", type=int, default=1)
    parser.add_argument("--min-observed-scans", type=int, default=3)
    parser.add_argument("--min-ok-prominence", type=float, default=20.0)
    parser.add_argument("--weak-support-prominence", type=float, default=3.0)
    return parser


def _settings_from_args(args: argparse.Namespace) -> LocalMs1FeatureSettings:
    return LocalMs1FeatureSettings(
        feature_link_ppm=args.feature_link_ppm,
        max_missing_scans=args.max_missing_scans,
        min_observed_scans=args.min_observed_scans,
        min_ok_prominence=args.min_ok_prominence,
        weak_support_prominence=args.weak_support_prominence,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raw_path = args.raw.resolve()
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not raw_path.is_file():
        print(f"{raw_path}: RAW file does not exist", file=sys.stderr)
        return 2
    if not dll_dir.is_dir():
        print(f"{dll_dir}: DLL directory does not exist", file=sys.stderr)
        return 2
    sample_stem = args.sample_stem or raw_path.stem
    raw_stat = raw_path.stat()
    try:
        with open_raw(raw_path, dll_dir) as handle:
            index = build_ms1_scan_index(handle)
    except RawReaderError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem=sample_stem,
        raw_path=str(raw_path),
        raw_file_size_bytes=int(raw_stat.st_size),
        raw_file_mtime_ns=int(raw_stat.st_mtime_ns),
        settings=_settings_from_args(args),
    )
    outputs = write_local_ms1_feature_outputs(
        output_dir,
        features,
        summary,
        diagnostics,
    )
    print(f"Detected features CSV: {outputs.detected_features_csv}")
    print(f"Local MS1 feature summary JSON: {outputs.summary_json}")
    print(f"Local MS1 feature diagnostics CSV: {outputs.diagnostics_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
python -m pytest tests/test_build_local_ms1_feature_table_cli.py -q
```

Expected: pass.

- [ ] **Step 5: Update docs**

In `docs/product/discovery.md`, add a short local feature source section near the DetectedFeature/Pt matcher section:

```markdown
Local MS1 feature table generation is the product-facing replacement path for
the scan-level Pt proxy. `scripts/build_local_ms1_feature_table.py` builds a
single-sample `detected_features.csv` from one RAW file using local MS1
centroid scan linking and Gaussian15-smoothed screening features. Rows with
`source = local_ms1_feature` carry feature-source quality only:
`trace_quality = OK` can seed a base, `trace_quality = WEAK_SUPPORT` can support
non-base Pt positions, and neither value grants trace/co-elution authority.
The local generator also writes `local_ms1_feature_summary.json` and
`local_ms1_feature_diagnostics.csv`; its `height` and `area` values are
screening evidence with `area_contract = screening_only` and
`quant_ready = false`.
```

In `docs/superpowers/validation/productization_status_index_v1.tsv`, keep the Pt
lane readiness `diagnostic_only` for detector/runtime outputs. Preserve the
stable product boundary in `docs/product/discovery.md`:

```markdown
Local MS1 feature table generator outputs are `diagnostic_only` / feature-source
screening evidence. They do not change Matrix, Backfill, counted-detection,
workbook, export, or production readiness authority.
```

- [ ] **Step 6: Add shard mapping if needed**

Open `tools/testing/test_shards.py`. Add these tests to the existing `alignment-core` shard unless the current file names a more specific discovery/delta-mass shard:

```python
"tests/test_local_ms1_features.py",
"tests/test_build_local_ms1_feature_table_cli.py",
"tests/test_ms1_scan_index.py",
```

Do not leave the tests outside explicit shard coverage. If a more specific shard is chosen, add a short comment in the plan implementation report naming which shard owns them.

- [ ] **Step 7: Run focused plan test suite**

Run:

```powershell
python -m pytest tests/test_detected_feature_sources.py tests/test_delta_mass_patterns.py tests/test_ms1_scan_index.py tests/test_local_ms1_features.py tests/test_build_local_ms1_feature_table_cli.py -q
```

Expected: pass.

## Final Verification

- [ ] Run ruff on touched package/tests/scripts:

```powershell
$env:UV_CACHE_DIR='.uv-cache'; uv run ruff check xic_extractor tests scripts
```

Expected: pass, or report sandbox/dependency blocker and rerun with approval if the sandbox blocks dependency resolution or executable spawn.

- [ ] Run focused pytest:

```powershell
python -m pytest tests/test_detected_feature_sources.py tests/test_delta_mass_patterns.py tests/test_ms1_scan_index.py tests/test_local_ms1_features.py tests/test_build_local_ms1_feature_table_cli.py -q
```

Expected: pass.

- [ ] Run docs/file hygiene checks:

```powershell
git diff --check
rg -n "<repo credential scan pattern>" xic_extractor scripts tests docs/superpowers/specs/2026-07-02-local-ms1-feature-table-generator-design.md docs/superpowers/plans/2026-07-02-local-ms1-feature-table-generator-implementation-plan.md
```

Expected: `git diff --check` has no whitespace errors; credential scan has no credential hits.

- [ ] Optional diagnostic-only Fullscan RAW smoke, only after code/tests pass and RAW runner preflight is satisfied. First run the preflight:

```powershell
if (Test-Path .env.xic-local) {
  Get-Content .env.xic-local |
    Where-Object { $_ -match '^\s*[^#][^=]+=' } |
    ForEach-Object {
      $name, $value = $_ -split '=', 2
      Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim().Trim('"')
    }
}
Get-Command python | Select-Object -ExpandProperty Source
python --version
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -c "import importlib.util; print('pythonnet', importlib.util.find_spec('pythonnet') is not None); print('pytest', importlib.util.find_spec('pytest') is not None)"
Test-Path "$env:THERMO_RAWFILE_READER_DLL_DIR"
Test-Path "$env:XIC_20251210_DELTA_MASS_RAW_DIR\CT-DNA+100uMCP_Fullscan.raw"
```

Expected: Python/DLL paths are present and both `Test-Path` commands print `True`. Then run generator, Pt matcher, trace diagnosis, and bounded packet:

```powershell
.venv\Scripts\python.exe -m scripts.build_local_ms1_feature_table --raw "$env:XIC_20251210_DELTA_MASS_RAW_DIR\CT-DNA+100uMCP_Fullscan.raw" --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" --output-dir output\local_ms1_feature_table_v1\fullscan

.venv\Scripts\python.exe -m scripts.run_pt_pattern_discovery --detected-features-csv output\local_ms1_feature_table_v1\fullscan\detected_features.csv --preset pt_pattern --output-dir output\local_ms1_feature_table_v1\fullscan\pt_pattern

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_trace_diagnosis --candidates-csv output\local_ms1_feature_table_v1\fullscan\pt_pattern\pt_pattern_candidates.csv --raw "$env:XIC_20251210_DELTA_MASS_RAW_DIR\CT-DNA+100uMCP_Fullscan.raw" --dll-dir "$env:THERMO_RAWFILE_READER_DLL_DIR" --output-dir output\local_ms1_feature_table_v1\fullscan\trace_diagnosis_limit64 --limit 64 --mz-source expected

.venv\Scripts\python.exe -m tools.diagnostics.pt_pattern_review_packet --candidates-csv output\local_ms1_feature_table_v1\fullscan\pt_pattern\pt_pattern_candidates.csv --trace-summary-csv output\local_ms1_feature_table_v1\fullscan\trace_diagnosis_limit64\pt_pattern_trace_summary.csv --trace-metrics-csv output\local_ms1_feature_table_v1\fullscan\trace_diagnosis_limit64\pt_pattern_trace_metrics.csv --output-dir output\local_ms1_feature_table_v1\fullscan\manual_review_packet --packet-label local_ms1_fullscan_pt_only_v1 --source-label local_ms1_fullscan --max-per-status 8
```

Run the diagnostic budget check:

```powershell
@'
import csv
import json
from pathlib import Path

root = Path("output/local_ms1_feature_table_v1/fullscan")
summary_path = root / "local_ms1_feature_summary.json"
candidates_path = root / "pt_pattern" / "pt_pattern_candidates.csv"
review_path = root / "pt_pattern" / "pt_pattern_review.csv"
sidecar_path = root / "pt_pattern" / "pt_pattern_sidecar.json"
packet_sidecar_path = root / "manual_review_packet" / "pt_pattern_manual_review_packet.json"
required = [summary_path, candidates_path, review_path, sidecar_path, packet_sidecar_path]
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"inconclusive_performance: missing artifact(s): {missing}")

summary = json.loads(summary_path.read_text(encoding="utf-8"))
if float(summary["wall_time_sec"]) > 900:
    raise SystemExit("inconclusive_performance: feature_table_wall_time_sec > 900")
if float(summary["degraded_assignment_component_fraction"]) > 0.05:
    raise SystemExit("inconclusive_performance: degraded_assignment_component_fraction > 0.05")

with candidates_path.open(newline="", encoding="utf-8") as handle:
    candidates = list(csv.DictReader(handle))
if not candidates:
    raise SystemExit("inconclusive_performance: zero Pt candidates")
if len(candidates) > 10000:
    raise SystemExit("inconclusive_performance: pt_candidate_rows > 10000")
if any(row.get("local_signal_support_status") == "supported" for row in candidates):
    raise SystemExit("contract_failure: local MS1 rows produced supported authority")
if any(row.get("evidence_tier") == "A" for row in candidates):
    raise SystemExit("contract_failure: local MS1 rows produced tier A authority")
if any("scan_proxy" in repr(row).lower() for row in candidates):
    raise SystemExit("contract_failure: scan proxy fallback appeared in local feature smoke")

packet = json.loads(packet_sidecar_path.read_text(encoding="utf-8"))
if int(packet["packet_row_count"]) > 64:
    raise SystemExit("inconclusive_performance: bounded_review_packet_rows > 64")
print("diagnostic_only Fullscan smoke passed budget checks")
'@ | .venv\Scripts\python.exe -
```

Expected: artifacts are emitted and remain `diagnostic_only`. Any budget overflow, zero candidate result, or missing artifact stops the work as `inconclusive_performance`; do not tune thresholds or promote readiness inside this smoke. This smoke validates Pt-only Fullscan only; DDMS2 composite Pt+CID-NL remains unvalidated unless a separate CID-NL candidate CSV is supplied and reviewed in a later gate.
