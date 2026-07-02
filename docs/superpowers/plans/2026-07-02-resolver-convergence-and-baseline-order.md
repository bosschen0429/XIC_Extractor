# Resolver Convergence And Baseline Order Evaluation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the raw `local_minimum` resolver from the targeted extraction path (ChromPeakSegment becomes sole detector), delete the fully-dead `legacy_savgol` resolver, and run an empirical comparison of AsLS baseline estimation order.

**Architecture:** Three independent workstreams. WS1 (delete legacy_savgol) is mechanical with zero behavior change. WS2 (ChromPeakSegment primary for targeted) changes the targeted peak detection dispatcher and requires parity evidence. WS3 (AsLS baseline order) is an independent experiment that informs a future design decision. WS1 must complete before WS2; WS3 is fully parallel.

**Tech Stack:** Python 3.11+, numpy, scipy, pytest

**Branch:** Execute on a dedicated branch (e.g. `feat/resolver-convergence`), separate from the `codex/pt-pattern-discovery` main work. Consider using a git worktree for parallel development.

## Global Constraints

- `local_minimum.py` is **NOT deleted** — discovery and alignment paths still depend on it.
- All `resolver_*` config fields used by ChromPeakSegment or discovery must be retained.
- `region_safe_merge.py`, `boundaries.py`, `boundary_scoring.py` are shared modules — do not modify their interfaces.
- Parity evidence required: 8RAW targeted run before and after, diff all boundary/area/confidence values.
- Project type: **Engineering** (structural change, no behavior change intended). Acceptance criterion: characterization-test parity.

---

## File Map

### WS1: Delete legacy_savgol

| Action | File | Responsibility |
| --- | --- | --- |
| Delete | `xic_extractor/peak_detection/legacy_savgol.py` | Dead Savitzky-Golay resolver |
| Modify | `xic_extractor/peak_detection/engine_candidates.py:42-43` | Remove legacy_savgol dispatch branch |
| Modify | `xic_extractor/peak_detection/facade.py:28-29,37-38` | Remove legacy_savgol re-export |
| Modify | `xic_extractor/settings_schema.py:14-16` | Remove `"legacy_savgol"` from `RESOLVER_MODES` |
| Modify | `xic_extractor/configuration/models.py:16-17` | Remove `smooth_window`, `smooth_polyorder` fields |
| Modify | `xic_extractor/configuration/settings.py:319-334,77-91` | Remove smooth_window/polyorder validation and migration |
| Modify | `tests/test_signal_processing.py:26,454-471` | Change `_config()` default; delete legacy_savgol-specific test |
| Modify | `tests/test_peak_detection_module_boundaries.py:107-127,278` | Delete legacy_savgol structural test; update engine_candidates assertion |
| Delete | `scripts/local_minimum_param_sweep.py` | Raw resolver tuning tool |
| Delete | `scripts/compare_resolvers.py` | Resolver comparison tool |
| Delete | `tests/test_local_minimum_param_sweep.py` | Test for deleted script |
| Delete | `tests/test_compare_resolvers.py` | Test for deleted script |

### WS2: ChromPeakSegment primary for targeted

| Action | File | Responsibility |
| --- | --- | --- |
| Modify | `xic_extractor/peak_detection/engine_candidates.py:26-53` | `region_first_safe_merge` dispatches to ChromPeakSegment, not local_minimum |
| Modify | `xic_extractor/peak_detection/engine_candidates.py:56-80` | Delete `augment_with_chrom_peak_segment_candidates` |
| Modify | `xic_extractor/peak_detection/chrom_peak_candidate_adapter.py:25-54` | Add `PeakCandidatesResult` wrapper for standalone use |
| Modify | `xic_extractor/peak_detection/chrom_peak_candidate_adapter.py:68-69` | Lower `min_apex_fraction_of_context` floor from 0.05 to 0.0 |
| Modify | `xic_extractor/peak_detection/facade.py:88-96` | Remove `legacy_savgol_finder` param from `find_peak_candidates` |
| Modify | `xic_extractor/peak_detection/engine.py:58-66` | Remove ChromPeakSegment augmentation block (now inline in dispatcher) |
| Modify | `tests/test_signal_processing.py:706-728` | Update `test_region_first_safe_merge_routes_to_local_minimum_candidate_finder` |
| Create | `tests/test_chrom_peak_segment_primary.py` | Parity tests for ChromPeakSegment as sole targeted detector |

### WS3: AsLS baseline order comparison

| Action | File | Responsibility |
| --- | --- | --- |
| Create | `tools/diagnostics/asls_baseline_order_comparison.py` | Empirical comparison script |
| Create | `tests/test_asls_baseline_order_comparison.py` | Unit test for comparison logic |

---

## Task 1: Delete legacy_savgol (WS1)

**Files:**
- Delete: `xic_extractor/peak_detection/legacy_savgol.py`
- Modify: `xic_extractor/settings_schema.py`
- Modify: `xic_extractor/peak_detection/engine_candidates.py`
- Modify: `xic_extractor/peak_detection/facade.py`
- Modify: `xic_extractor/configuration/models.py`
- Modify: `xic_extractor/configuration/settings.py`
- Modify: `tests/test_signal_processing.py`
- Modify: `tests/test_peak_detection_module_boundaries.py`
- Delete: `scripts/local_minimum_param_sweep.py`, `scripts/compare_resolvers.py`
- Delete: `tests/test_local_minimum_param_sweep.py`, `tests/test_compare_resolvers.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `RESOLVER_MODES` no longer includes `"legacy_savgol"`; `ExtractionConfig` no longer has `smooth_window`/`smooth_polyorder`

- [ ] **Step 1: Add retirement message constant**

In `xic_extractor/settings_schema.py`, add a retirement message following the existing `ARBITRATED_RESOLVER_RETIRED_MESSAGE` pattern, and remove `"legacy_savgol"` from `RESOLVER_MODES`:

```python
# Replace:
RESOLVER_MODES: tuple[str, ...] = (
    "legacy_savgol",
    "local_minimum",
    "region_first_safe_merge",
)

# With:
RESOLVER_MODES: tuple[str, ...] = (
    "local_minimum",
    "region_first_safe_merge",
)

LEGACY_SAVGOL_RETIRED_MESSAGE = (
    "legacy_savgol resolver mode is retired; use region_first_safe_merge"
)
```

Also remove `smooth_window` and `smooth_polyorder` from `CANONICAL_SETTINGS_DEFAULTS` and `CANONICAL_SETTINGS_DESCRIPTIONS`.

- [ ] **Step 2: Update engine_candidates.py dispatcher**

In `xic_extractor/peak_detection/engine_candidates.py`, replace the legacy_savgol dispatch with a retirement error:

```python
# In find_peak_candidates(), replace the legacy_savgol branch:

# Before:
from xic_extractor.peak_detection.legacy_savgol import (
    find_peak_candidates_legacy_savgol,
)
# ...
    if resolver_mode in {"local_minimum", "region_first_safe_merge"}:
        return local_minimum_finder(rt, intensity, config, ...)
    if resolver_mode == "legacy_savgol":
        return legacy_savgol_finder(rt, intensity, config, ...)

# After:
from xic_extractor.settings_schema import LEGACY_SAVGOL_RETIRED_MESSAGE
# Remove the legacy_savgol import entirely
# ...
    if resolver_mode in {"local_minimum", "region_first_safe_merge"}:
        return local_minimum_finder(rt, intensity, config, ...)
    if resolver_mode == "legacy_savgol":
        raise ValueError(LEGACY_SAVGOL_RETIRED_MESSAGE)
```

- [ ] **Step 3: Update facade.py re-exports**

Remove `find_peak_candidates_legacy_savgol` from `facade.py`:

```python
# Delete these lines:
find_peak_candidates_legacy_savgol = (
    candidate_flow.find_peak_candidates_legacy_savgol
)

# Remove from __all__:
# Remove "find_peak_candidates_legacy_savgol" entry

# Update find_peak_candidates to remove legacy_savgol_finder param:
def find_peak_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
) -> PeakCandidatesResult:
    return _find_peak_candidates(
        rt,
        intensity,
        config,
        peak_min_prominence_ratio=peak_min_prominence_ratio,
        local_minimum_finder=find_peak_candidates_local_minimum,
        # legacy_savgol_finder param removed
    )
```

- [ ] **Step 4: Remove smooth_window/smooth_polyorder from ExtractionConfig**

In `xic_extractor/configuration/models.py`, delete `smooth_window` and `smooth_polyorder` fields from `ExtractionConfig`. These are only consumed by `legacy_savgol.py`.

```python
# Delete these two lines from ExtractionConfig:
    smooth_window: int           # line 16 — delete
    smooth_polyorder: int        # line 17 — delete
```

Note: `peak_rel_height` is also only consumed by `legacy_savgol.py`'s `_build_candidate`, but it exists in the config schema and may have other future uses. Leave it for now.

- [ ] **Step 5: Update settings.py parsing and validation**

In `xic_extractor/configuration/settings.py`:

1. Remove `smooth_window`/`smooth_polyorder` from `_ParsedSettings` dataclass
2. Remove their parsing from `_parse_settings_values`
3. Remove validation in `_validate_settings_ranges` (lines 319-334: odd-window check, polyorder < window check)
4. Remove `migrate_settings_dict` legacy key handling (`smooth_points` → `smooth_window`, `smooth_sigma` drop)
5. Remove from `_build_config` field mapping

Add `"legacy_savgol"` retirement check in `_validate_settings_ranges`, following the `"arbitrated"` pattern:

```python
# After the existing arbitrated check (line 362-369), add:
if settings.resolver_mode == "legacy_savgol":
    from xic_extractor.settings_schema import LEGACY_SAVGOL_RETIRED_MESSAGE
    errors.append(
        _validation_error("resolver_mode", LEGACY_SAVGOL_RETIRED_MESSAGE)
    )
```

- [ ] **Step 6: Update test defaults and delete legacy_savgol tests**

In `tests/test_signal_processing.py`:

1. Change `_config()` default from `resolver_mode="legacy_savgol"` to `resolver_mode="local_minimum"`:

```python
# line 26, change:
def _config(
    ...
    resolver_mode: str = "local_minimum",  # was "legacy_savgol"
    ...
```

2. Delete `test_legacy_savgol_resolver_mode_preserves_existing_candidate_behavior` (lines 454-471).

3. Add retirement test following the `test_arbitrated_resolver_mode_is_retired` pattern (line 684):

```python
def test_legacy_savgol_resolver_mode_is_retired():
    config = _config(resolver_mode="legacy_savgol")
    rt, intensity = _clean_gaussian_trace()
    with pytest.raises(ValueError, match="retired.*region_first_safe_merge"):
        find_peak_and_area(rt, intensity, config)
```

4. Remove `smooth_window`/`smooth_polyorder` from `_config()` params (they no longer exist in `ExtractionConfig`). Replace with hardcoded defaults where needed.

- [ ] **Step 7: Update structural boundary tests**

In `tests/test_peak_detection_module_boundaries.py`:

1. Delete `test_peak_detection_legacy_savgol_owns_default_candidate_formation` (lines 107-127) entirely.

2. In `test_peak_detection_engine_candidates_owns_candidate_flow` (line 249), remove assertion at line 278:

```python
# Delete this assertion:
assert "find_peak_candidates_legacy_savgol" in candidates_source
```

- [ ] **Step 8: Delete dead scripts and their tests**

```
git rm scripts/local_minimum_param_sweep.py
git rm scripts/compare_resolvers.py
git rm tests/test_local_minimum_param_sweep.py
git rm tests/test_compare_resolvers.py
```

- [ ] **Step 9: Delete legacy_savgol.py**

```
git rm xic_extractor/peak_detection/legacy_savgol.py
```

- [ ] **Step 10: Run full test suite**

```
uv run pytest --tb=short -q
```

Expected: all tests pass. The only behavior change is that `resolver_mode="legacy_savgol"` now raises `ValueError` instead of running — no active code path uses this mode.

- [ ] **Step 11: Commit**

```
git add -A
git commit -m "refactor: retire legacy_savgol resolver mode

Delete legacy_savgol.py and all code paths that dispatch to it.
Add retirement error following the arbitrated-mode precedent.
Remove smooth_window/smooth_polyorder config fields (savgol-only).
Delete local_minimum_param_sweep and compare_resolvers scripts.

No behavior change: no active pipeline path uses legacy_savgol."
```

---

## Task 2: ChromPeakSegment as primary detector for targeted path (WS2)

**Files:**
- Modify: `xic_extractor/peak_detection/chrom_peak_candidate_adapter.py`
- Modify: `xic_extractor/peak_detection/engine_candidates.py`
- Modify: `xic_extractor/peak_detection/engine.py`
- Modify: `xic_extractor/peak_detection/facade.py`
- Modify: `tests/test_signal_processing.py`
- Create: `tests/test_chrom_peak_segment_primary.py`

**Interfaces:**
- Consumes: `chrom_peak_segment_candidates()` from adapter, `PeakCandidatesResult` from models
- Produces: `find_peak_candidates` returns ChromPeakSegment-sourced candidates when `resolver_mode == CANONICAL_RESOLVER_MODE`

**Prerequisites:** Task 1 complete. 8RAW targeted parity baseline captured.

- [ ] **Step 1: Add PeakCandidatesResult wrapper to adapter**

In `xic_extractor/peak_detection/chrom_peak_candidate_adapter.py`, add a function that wraps `chrom_peak_segment_candidates` into a `PeakCandidatesResult`:

```python
from xic_extractor.peak_detection.models import PeakCandidatesResult

def find_peak_candidates_chrom_peak_segment(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
) -> PeakCandidatesResult:
    """ChromPeakSegment-based candidate finder with PeakCandidatesResult interface."""
    _ = peak_min_prominence_ratio  # not used by ChromPeakSegment
    rt_values = np.asarray(rt, dtype=float)
    intensity_values = np.asarray(intensity, dtype=float)
    n_points = len(intensity_values)
    if n_points < 2:
        return PeakCandidatesResult(
            status="NO_SIGNAL", candidates=(), n_points=n_points,
            max_smoothed=None, n_prominent_peaks=0,
        )
    candidates = chrom_peak_segment_candidates(rt_values, intensity_values, config)
    max_smoothed = float(np.max(intensity_values)) if n_points > 0 else None
    if not candidates:
        return PeakCandidatesResult(
            status="PEAK_NOT_FOUND", candidates=(), n_points=n_points,
            max_smoothed=max_smoothed, n_prominent_peaks=0,
        )
    return PeakCandidatesResult(
        status="OK", candidates=candidates, n_points=n_points,
        max_smoothed=max_smoothed, n_prominent_peaks=len(candidates),
    )
```

- [ ] **Step 2: Lower min_apex_fraction_of_context floor**

In `xic_extractor/peak_detection/chrom_peak_candidate_adapter.py`, `_policy_from_config`:

```python
# Before (line 68-69):
        min_apex_fraction_of_context=max(
            float(getattr(config, "resolver_min_relative_height", 0.05)),
            0.0,
        ),

# After — remove the 0.05 fallback, use config value directly:
        min_apex_fraction_of_context=max(
            float(getattr(config, "resolver_min_relative_height", 0.02)),
            0.0,
        ),
```

This aligns ChromPeakSegment's relative-height threshold with the raw resolver's default (2%).

- [ ] **Step 3: Write parity test**

Create `tests/test_chrom_peak_segment_primary.py`:

```python
import numpy as np
import pytest

from tests.test_signal_processing import _config, _clean_gaussian_trace
from xic_extractor.peak_detection.chrom_peak_candidate_adapter import (
    find_peak_candidates_chrom_peak_segment,
)
from xic_extractor.peak_detection.engine import find_peak_and_area


def test_chrom_peak_segment_primary_finds_clean_gaussian():
    config = _config(resolver_mode="region_first_safe_merge")
    rt, intensity = _clean_gaussian_trace()
    result = find_peak_candidates_chrom_peak_segment(rt, intensity, config)
    assert result.status == "OK"
    assert len(result.candidates) >= 1
    apex_rt = result.candidates[0].selection_apex_rt
    assert abs(apex_rt - 5.0) < 0.2  # near center of trace


def test_chrom_peak_segment_primary_returns_peak_not_found_for_noise():
    config = _config(resolver_mode="region_first_safe_merge")
    rng = np.random.default_rng(42)
    rt = np.linspace(0, 10, 200)
    intensity = rng.normal(100, 5, size=200).clip(min=0)
    result = find_peak_candidates_chrom_peak_segment(rt, intensity, config)
    assert result.status == "PEAK_NOT_FOUND"


def test_chrom_peak_segment_primary_no_signal_for_empty():
    config = _config(resolver_mode="region_first_safe_merge")
    result = find_peak_candidates_chrom_peak_segment(
        np.array([]), np.array([]), config,
    )
    assert result.status == "NO_SIGNAL"


def test_region_first_safe_merge_uses_chrom_peak_segment_source():
    """After migration, canonical mode candidates come from ChromPeakSegment."""
    config = _config(resolver_mode="region_first_safe_merge")
    rt, intensity = _clean_gaussian_trace()
    result = find_peak_and_area(rt, intensity, config)
    assert result.status == "OK"
    assert "chrom_peak_segment" in result.hypothesis.candidate.proposal_sources
```

- [ ] **Step 4: Run test to verify it fails**

```
uv run pytest tests/test_chrom_peak_segment_primary.py -v
```

Expected: `test_region_first_safe_merge_uses_chrom_peak_segment_source` FAILS (currently routes to `local_minimum`).

- [ ] **Step 5: Change dispatcher for canonical mode**

In `xic_extractor/peak_detection/engine_candidates.py`:

```python
# Add import:
from xic_extractor.peak_detection.chrom_peak_candidate_adapter import (
    find_peak_candidates_chrom_peak_segment,
)

# In find_peak_candidates(), change the dispatch:

# Before:
    if resolver_mode in {"local_minimum", "region_first_safe_merge"}:
        return local_minimum_finder(rt, intensity, config, ...)

# After:
    if resolver_mode == CANONICAL_RESOLVER_MODE:
        return find_peak_candidates_chrom_peak_segment(
            rt, intensity, config,
            peak_min_prominence_ratio=peak_min_prominence_ratio,
        )
    if resolver_mode == "local_minimum":
        return local_minimum_finder(rt, intensity, config, ...)
```

This routes `"region_first_safe_merge"` to ChromPeakSegment and preserves `"local_minimum"` for discovery/alignment.

- [ ] **Step 6: Remove ChromPeakSegment augmentation from engine.py**

In `xic_extractor/peak_detection/engine.py`, the augmentation block (lines 58-66) is no longer needed for canonical mode because the dispatcher already returns ChromPeakSegment candidates. Remove:

```python
# Delete the augmentation block:
    if scoring_context_builder is not None:
        candidates_result = candidate_flow.augment_with_chrom_peak_segment_candidates(
            ...
        )
```

Note: `augment_with_chrom_peak_segment_candidates` in `engine_candidates.py` can be deleted or kept as dead code for now. Recommend deletion for clarity.

- [ ] **Step 7: Update facade.py**

Remove `legacy_savgol_finder` parameter from `find_peak_candidates` (already done in Task 1 if following order). Ensure `find_peak_candidates` still delegates to `engine_candidates.find_peak_candidates` correctly.

- [ ] **Step 8: Update test_region_first_safe_merge_routes_to_local_minimum**

In `tests/test_signal_processing.py`, `test_region_first_safe_merge_routes_to_local_minimum_candidate_finder` (line 706) currently asserts `proposal_sources == ("local_minimum",)`. Update:

```python
def test_region_first_safe_merge_routes_to_chrom_peak_segment():
    config = _config(resolver_mode="region_first_safe_merge")
    rt, intensity = _clean_gaussian_trace()
    result = find_peak_candidates(rt, intensity, config)
    assert result.status == "OK"
    assert any(
        "chrom_peak_segment" in c.proposal_sources
        for c in result.candidates
    )
```

- [ ] **Step 9: Run full test suite**

```
uv run pytest --tb=short -q
```

Expected: all pass. Targeted path now uses ChromPeakSegment; discovery/alignment still use local_minimum via `resolver_mode="local_minimum"`.

- [ ] **Step 10: Run 8RAW targeted parity diff**

Run the targeted extraction pipeline on 8RAW data with the new code. Compare boundary/area/confidence against the Task 0 baseline. Expected differences:

- Boundaries may shift slightly (ChromPeakSegment uses morphology trace, not raw)
- Areas should be similar (both ultimately compute `area_ms1_morphology`)
- Some weak peaks may appear/disappear at the detection threshold boundary

Record the diff in `docs/superpowers/validation/resolver_convergence_v1/`.

- [ ] **Step 11: Commit**

```
git add -A
git commit -m "refactor: ChromPeakSegment as primary detector for targeted path

region_first_safe_merge mode now dispatches to ChromPeakSegment
instead of local_minimum. local_minimum mode preserved for
discovery/alignment paths.

Removed ChromPeakSegment augmentation block from engine.py
(no longer supplementary — now primary).

Lowered min_apex_fraction_of_context default from 0.05 to 0.02
to match raw resolver coverage."
```

---

## Task 3: AsLS baseline order empirical comparison (WS3)

**Files:**
- Create: `tools/diagnostics/asls_baseline_order_comparison.py`
- Create: `tests/test_asls_baseline_order_comparison.py`

**Interfaces:**
- Consumes: `asls_baseline` from `baseline.py`, `gaussian15_morphology_trace` from `ms1_morphology.py`
- Produces: CSV comparison report

**Prerequisites:** None (independent of WS1/WS2)

- [ ] **Step 1: Write comparison core logic test**

Create `tests/test_asls_baseline_order_comparison.py`:

```python
import numpy as np
import pytest


def _noisy_peak_with_valley(
    rng: np.random.Generator,
    n: int = 200,
    noise_std: float = 20.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic trace: Gaussian peak + noisy valley with a low-intensity spike."""
    rt = np.linspace(0, 10, n)
    clean = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.3) ** 2)
    noise = rng.normal(0, noise_std, size=n).clip(min=-clean)
    intensity = (clean + noise).clip(min=0)
    # Add a low-intensity noise spike in the valley before the peak
    intensity[60] = max(intensity[60], noise_std * 3)
    return rt, intensity


def test_order_a_and_b_produce_different_baselines_on_noisy_input():
    from xic_extractor.peak_detection.baseline import asls_baseline
    from xic_extractor.peak_detection.ms1_morphology import (
        gaussian15_morphology_trace,
    )

    rng = np.random.default_rng(42)
    rt, intensity = _noisy_peak_with_valley(rng)

    # Order A (current): raw → AsLS → residual → smooth
    baseline_a = asls_baseline(intensity)
    residual_a = intensity - baseline_a
    smoothed_a = gaussian15_morphology_trace(residual_a)

    # Order B (alternative): raw → smooth → AsLS(smoothed) → residual
    from xic_extractor.peak_detection.ms1_shape_identity import (
        gaussian_smooth_values,
    )

    smoothed_input = gaussian_smooth_values(intensity)
    baseline_b = asls_baseline(smoothed_input)
    residual_b = smoothed_input - baseline_b

    # They should NOT be identical (AsLS is nonlinear)
    assert not np.allclose(smoothed_a, residual_b, atol=1e-6)

    # Order B baseline should be smoother in valleys
    # (less influenced by single-sample noise)
    valley_slice = slice(40, 70)  # valley region before peak
    baseline_a_valley_var = np.var(baseline_a[valley_slice])
    baseline_b_valley_var = np.var(baseline_b[valley_slice])
    assert baseline_b_valley_var <= baseline_a_valley_var
```

- [ ] **Step 2: Run test**

```
uv run pytest tests/test_asls_baseline_order_comparison.py -v
```

Expected: PASS (confirming the two orders produce different results on noisy input).

- [ ] **Step 3: Write comparison diagnostic script**

Create `tools/diagnostics/asls_baseline_order_comparison.py`:

```python
"""Compare AsLS baseline estimation order on real XIC traces.

Order A (current): raw intensity → AsLS baseline → residual → Gaussian15 smooth
Order B (alternative): raw intensity → Gaussian15 smooth → AsLS baseline → residual

Produces a CSV with per-feature area comparison and summary statistics.

Usage:
    uv run python tools/diagnostics/asls_baseline_order_comparison.py \
        --data-dir path/to/raw_files \
        --targets-csv path/to/targets.csv \
        --settings-csv path/to/settings.csv \
        --output-csv output/baseline_order_comparison.csv
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from xic_extractor.peak_detection.baseline import asls_baseline
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.ms1_morphology import (
    configured_morphology_window_points,
    gaussian15_morphology_trace,
)
from xic_extractor.peak_detection.ms1_shape_identity import gaussian_smooth_values


@dataclass(frozen=True)
class OrderComparisonRow:
    sample: str
    target: str
    left_index: int
    right_index: int
    area_order_a: float
    area_order_b: float
    area_diff: float
    area_ratio: float | None
    apex_intensity: float
    n_scans: int


def compare_orders_single_trace(
    rt: np.ndarray,
    intensity: np.ndarray,
    left: int,
    right: int,
    window_points: int = 15,
) -> tuple[float, float]:
    """Return (area_order_a, area_order_b) for a single trace segment."""
    # Order A: raw → AsLS → residual → smooth → positive clip
    baseline_a = asls_baseline(intensity)
    residual_a = intensity - baseline_a
    smoothed_a = gaussian15_morphology_trace(residual_a, window_points=window_points)
    positive_a = np.maximum(smoothed_a, 0.0)
    area_a = float(np.trapezoid(positive_a[left:right], rt[left:right])) * 60.0

    # Order B: raw → smooth → AsLS(smoothed) → residual → positive clip
    smoothed_input = gaussian_smooth_values(intensity, points=window_points)
    baseline_b = asls_baseline(smoothed_input)
    residual_b = smoothed_input - baseline_b
    positive_b = np.maximum(residual_b[left:right], 0.0)
    area_b = float(np.trapezoid(positive_b[left:right], rt[left:right])) * 60.0

    return area_a, area_b


def _safe_ratio(a: float, b: float) -> float | None:
    if b <= 0:
        return None
    return a / b


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--targets-csv", type=Path, required=True)
    parser.add_argument("--settings-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    # Integration with the pipeline's extraction loop is needed here.
    # This is a skeleton — the actual implementation will need to:
    # 1. Load settings and targets
    # 2. For each sample × target, extract the XIC trace
    # 3. Find boundaries (using current pipeline)
    # 4. Call compare_orders_single_trace
    # 5. Collect rows and write CSV
    print(
        f"Skeleton: would compare baseline orders on {args.data_dir} "
        f"→ {args.output_csv}"
    )
    print("Wire up the extraction loop to populate this script.")


if __name__ == "__main__":
    main()
```

Note: the `main()` function is a skeleton that needs to be wired into the pipeline's extraction loop. The core comparison logic (`compare_orders_single_trace`) is complete and testable independently. The wiring depends on how the 8RAW data is loaded, which varies by execution environment.

- [ ] **Step 4: Write unit test for comparison function**

Add to `tests/test_asls_baseline_order_comparison.py`:

```python
def test_compare_orders_returns_two_positive_areas_for_clean_peak():
    from tools.diagnostics.asls_baseline_order_comparison import (
        compare_orders_single_trace,
    )

    rt = np.linspace(0, 10, 200)
    intensity = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.3) ** 2)
    intensity += 50.0  # baseline offset
    left, right = 80, 120  # around the peak

    area_a, area_b = compare_orders_single_trace(rt, intensity, left, right)
    assert area_a > 0
    assert area_b > 0
    # For a clean peak, both orders should give similar areas
    ratio = area_a / area_b
    assert 0.9 < ratio < 1.1
```

- [ ] **Step 5: Run tests**

```
uv run pytest tests/test_asls_baseline_order_comparison.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```
git add tools/diagnostics/asls_baseline_order_comparison.py tests/test_asls_baseline_order_comparison.py
git commit -m "feat: add AsLS baseline order comparison diagnostic

Compare current order (raw→AsLS→smooth) vs alternative (raw→smooth→AsLS).
Core comparison function complete; extraction loop skeleton for 8RAW wiring.

Motivation: AsLS is nonlinear, so the two orders are not equivalent.
Pre-smoothing may reduce noise-driven baseline overestimation in valleys."
```

---

## Task 4: Documentation and cleanup

**Files:**
- Modify: `docs/product/signal-processing-rationale.md`
- Modify: `tools/diagnostics/INDEX.md`

**Prerequisites:** Tasks 1-2 complete.

- [ ] **Step 1: Update signal-processing-rationale.md**

In `docs/product/signal-processing-rationale.md`, update §Raw Resolver And The Smoothed-Everywhere Boundary:

- Targeted path section: change from "planned for removal" to "completed — ChromPeakSegment is the sole detector"
- Update the layered design rationale section to note it is now historical for targeted, still active for discovery/alignment

- [ ] **Step 2: Update diagnostics INDEX.md**

Add `asls_baseline_order_comparison.py` to `tools/diagnostics/INDEX.md`.

- [ ] **Step 3: Commit**

```
git add docs/ tools/diagnostics/INDEX.md
git commit -m "docs: update signal processing rationale after resolver convergence

Targeted path now uses ChromPeakSegment as sole detector (completed).
Discovery/alignment still use local_minimum (documented as future migration).
Added AsLS baseline order comparison to diagnostics index."
```

---

## Task 0: Parity baseline capture (run before Task 2)

This is not a code task — it's a data capture step.

- [ ] **Step 1: Run 8RAW targeted extraction with current code**

```
uv run python scripts/run_extraction.py \
    --data-dir path/to/8RAW \
    --targets-csv path/to/targets.csv \
    --settings-csv path/to/settings.csv \
    --output-dir output/parity_baseline_v0
```

- [ ] **Step 2: Archive the output**

```
cp -r output/parity_baseline_v0 docs/superpowers/validation/resolver_convergence_v1/baseline/
```

This becomes the characterization baseline for Task 2 Step 10.

---

## Execution notes

### Worktree strategy

These tasks should run on a separate branch from `codex/pt-pattern-discovery`:

```powershell
git worktree add ../XIC_Extractor-resolver-convergence -b feat/resolver-convergence
```

Main branch continues pt-pattern discovery work. Merge `feat/resolver-convergence` after parity evidence is accepted.

### Task execution order

```
Task 0 (parity baseline) → Task 1 (delete legacy_savgol) → Task 2 (ChromPeakSegment primary)
                                                              ↕ parallel
                                                           Task 3 (AsLS comparison)
                                                              ↓
                                                           Task 4 (docs)
```

### Future work (not in this plan)

- Discovery/alignment migration to ChromPeakSegment (separate plan after targeted parity evidence)
- AsLS baseline order change (pending Task 3 empirical results)
- `local_minimum.py` deletion (only after all paths migrated)
- Config field rename (`resolver_*` → names reflecting ChromPeakSegment semantics)
