from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.alignment.owner_backfill as owner_backfill_module
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
    processing_identity_candidate_set,
    processing_identity_hypothesis,
    processing_identity_selection,
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


def test_no_profile_targeted_backfill_preserves_activation_base_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )

    def reject_canonical_owner(*_args, **_kwargs):
        raise AssertionError(
            "Targeted no-profile compatibility entered canonical owner"
        )

    monkeypatch.setattr(
        owner_backfill_module,
        "prepare_physical_signal",
        reject_canonical_owner,
    )
    monkeypatch.setattr(
        owner_backfill_module,
        "evaluate_physical_peak",
        reject_canonical_owner,
    )

    cells = build_targeted_hypothesis_cells(
        inputs,
        groups,
        backfill_sources={
            "eligible-backfill": FrozenNoRawBackfillSource(
                center=8.01,
                height=600.0,
            ),
            "true-missing": FrozenNoRawBackfillSource(
                center=8.01,
                height=0.0,
            ),
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
    backfilled = by_stem["eligible-backfill"]
    eligible_input = next(
        item for item in inputs if item.sample.metadata.raw_stem == "eligible-backfill"
    )
    assert (
        backfilled.canonical_sample_identity_id,
        backfilled.target_definition_id,
        backfilled.selection_evidence_artifact_id,
        backfilled.group_hypothesis_id,
        backfilled.alignment_owner_ids,
        backfilled.source_hypothesis_id,
    ) == (
        eligible_input.sample.identity.sample_identity_id,
        eligible_input.target.definition.target_definition_id,
        eligible_input.selection.artifact.evidence_artifact_id,
        groups[0].group_hypothesis_id,
        groups[0].owner_ids,
        None,
    )
    integration = backfilled.selected_integration
    assert integration is not None
    assert (
        integration.rt_left_min,
        integration.rt_apex_min,
        integration.rt_right_min,
        integration.height_raw,
        integration.area_raw_counts_seconds,
        integration.area_ms1_morphology,
    ) == pytest.approx((7.93, 8.01, 8.09, 600.0, 2952.0, 2835.100474933266))
    assert integration.boundary_sources == ("owner_backfill_batch",)


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


def test_missing_source_remains_unchecked_instead_of_becoming_true_missing(
    tmp_path: Path,
) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )

    cells = build_targeted_hypothesis_cells(
        inputs,
        groups,
        backfill_sources={
            "eligible-backfill": FrozenNoRawBackfillSource(
                center=8.01,
                height=600.0,
            ),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    missing = next(cell for cell in cells if cell.sample_stem == "true-missing")
    assert missing.status == "unchecked"
    assert missing.backfill_cell is None
    assert missing.reason == (
        "no local evidence source was supplied for the missing cell"
    )


def test_non_unique_alignment_correspondence_remains_ambiguous(
    tmp_path: Path,
) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )
    assert len(groups) == 1

    cells = build_targeted_hypothesis_cells(
        inputs,
        (
            groups[0],
            replace(
                groups[0],
                group_hypothesis_id="FAM000002",
                owner_ids=(f"xic.peak-hypothesis.v1:{'0' * 64}",),
            ),
        ),
        backfill_sources={},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    gap = next(cell for cell in cells if cell.sample_stem == "eligible-backfill")
    assert gap.status == "ambiguous"
    assert gap.reason == "Alignment correspondence is not unique for Backfill"


def test_unassessable_owner_trace_remains_unassessable(tmp_path: Path) -> None:
    fixture, inputs = _complete_inputs(tmp_path)
    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )
    source = FrozenNoRawBackfillSource(center=8.01, height=600.0)
    source.rt[-1] = np.nan

    cells = build_targeted_hypothesis_cells(
        inputs,
        groups,
        backfill_sources={"eligible-backfill": source},
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    cell = next(item for item in cells if item.sample_stem == "eligible-backfill")
    assert cell.status == "unassessable"
    assert cell.backfill_cell is not None
    assert cell.backfill_cell.trace_quality == "owner_backfill_unassessable"


def _complete_inputs(tmp_path: Path):
    fixture = build_processing_identity_no_raw_fixture(
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
    observed_a = processing_identity_hypothesis(
        fixture, "observed-a", apex_rt=8.00, scan_number=40
    )
    observed_b = processing_identity_hypothesis(
        fixture, "observed-b", apex_rt=8.02, scan_number=50
    )
    ambiguous_a = processing_identity_hypothesis(
        fixture, "ambiguous", apex_rt=8.00, scan_number=60
    )
    ambiguous_b = processing_identity_hypothesis(
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
    candidate_set = processing_identity_candidate_set(
        fixture,
        sample_stem,
        hypotheses,
        failure_reason=failure_reason,
    )
    return TargetedHypothesisAlignmentInput(
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        selection=processing_identity_selection(
            fixture,
            sample_stem,
            hypotheses,
            candidate_set=candidate_set,
            failure_reason=failure_reason,
        ),
        candidate_set=candidate_set,
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
