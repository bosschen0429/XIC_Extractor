from pathlib import Path

import numpy as np
import pytest

from tests.package2_no_raw_fixture import (
    build_package2_no_raw_fixture,
    package2_hypothesis,
    package2_selection,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
    align_selected_targeted_hypotheses,
)
from xic_extractor.alignment.targeted_hypothesis_backfill import (
    TargetedHypothesisBackfillError,
    build_targeted_hypothesis_cells,
)
from xic_extractor.config import ExtractionConfig


class FrozenNoRawBackfillSource:
    def __init__(self, *, center: float, height: float) -> None:
        self.rt = np.linspace(center - 0.1, center + 0.1, 11)
        scale = np.array([0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 0.75, 0.5, 0.25, 0.1, 0.0])
        self.intensity = scale * height
        self.calls: list[tuple[float, float, float, float]] = []

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        self.calls.append((mz, rt_min, rt_max, ppm_tol))
        return self.rt, self.intensity


def test_complete_cell_set_has_observed_backfill_missing_ambiguity_and_failure(
    tmp_path: Path,
) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )
    eligible_source = FrozenNoRawBackfillSource(center=8.01, height=600.0)
    missing_source = FrozenNoRawBackfillSource(center=8.01, height=0.0)

    cells = build_targeted_hypothesis_cells(
        inputs,
        groups,
        backfill_sources={
            "eligible-backfill": eligible_source,
            "true-missing": missing_source,
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )
    by_stem = {cell.sample_stem: cell for cell in cells}

    assert {stem: cell.status for stem, cell in by_stem.items()} == {
        "ambiguous": "ambiguous",
        "eligible-backfill": "backfilled",
        "failed": "failed",
        "observed-a": "observed",
        "observed-b": "observed",
        "true-missing": "true_missing",
    }
    assert len(groups) == 1
    assert by_stem["eligible-backfill"].group_hypothesis_id == (
        groups[0].group_hypothesis_id
    )
    assert by_stem["eligible-backfill"].alignment_owner_ids == groups[0].owner_ids
    assert by_stem["eligible-backfill"].source_hypothesis_id is None
    assert by_stem["eligible-backfill"].selected_integration is not None
    assert by_stem["true-missing"].selected_integration is None
    assert len(eligible_source.calls) == 1
    assert len(missing_source.calls) == 1


def test_backfill_source_cannot_bypass_ambiguity_or_failure(tmp_path: Path) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )
    source = FrozenNoRawBackfillSource(center=8.0, height=600.0)

    with pytest.raises(
        TargetedHypothesisBackfillError, match="only for a no-candidate"
    ):
        build_targeted_hypothesis_cells(
            inputs,
            groups,
            backfill_sources={"ambiguous": source},
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
        )
    assert source.calls == []


def _complete_inputs(tmp_path: Path):
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=(
            "observed-a",
            "observed-b",
            "eligible-backfill",
            "true-missing",
            "ambiguous",
            "failed",
        ),
    )
    observed_a = package2_hypothesis(
        fixture, "observed-a", apex_rt=8.00, scan_number=40
    )
    observed_b = package2_hypothesis(
        fixture, "observed-b", apex_rt=8.02, scan_number=50
    )
    ambiguous_a = package2_hypothesis(
        fixture, "ambiguous", apex_rt=8.00, scan_number=60
    )
    ambiguous_b = package2_hypothesis(
        fixture, "ambiguous", apex_rt=8.30, scan_number=70
    )
    inputs = (
        _input(fixture, "observed-a", (observed_a,)),
        _input(fixture, "observed-b", (observed_b,)),
        _input(fixture, "eligible-backfill", ()),
        _input(fixture, "true-missing", ()),
        _input(fixture, "ambiguous", (ambiguous_a, ambiguous_b)),
        _input(fixture, "failed", (), failure_reason="discovery_worker_failed"),
    )
    return fixture, inputs


def _input(fixture, sample_stem, hypotheses, *, failure_reason=None):
    return TargetedHypothesisAlignmentInput(
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        selection=package2_selection(
            fixture,
            sample_stem,
            hypotheses,
            failure_reason=failure_reason,
        ),
        candidate_hypotheses=hypotheses,
    )


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "xic_diagnostics.csv",
        smooth_window=3,
        smooth_polyorder=1,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )
